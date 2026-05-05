"""Core agent loop.

The pattern:
    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

Supports parallel tool execution, 3-layer context compression,
sub-agent isolation, dynamic system prompts, and Ctrl+C cancellation.
"""

import asyncio
import concurrent.futures
import threading
from typing import Callable

from core import logger
from .llm import LLM, LLMResponse
from .tools_bridge import get_all_tools, get_tool
from .context import ContextManager
from .prompt import build_system_prompt


class Agent:
    def __init__(
        self,
        llm: LLM,
        workspace: str,
        tools=None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        extra_instructions: str = "",
        persona: str = "",
        skills_prompt: str = "",
    ):
        self.llm = llm
        self.workspace = workspace
        self.tools = tools if tools is not None else get_all_tools(workspace)
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self.extra_instructions = extra_instructions
        self.persona = persona
        self.skills_prompt = skills_prompt
        self._cancel_event = threading.Event()
        self._was_cancelled = False  # True if previous chat() was interrupted

        # Wire up sub-agent capability
        from core.tools.agent_tool import AgentTool
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

    def _build_system(self) -> str:
        return build_system_prompt(
            self.tools,
            self.workspace,
            extra_instructions=self.extra_instructions,
            persona=self.persona,
            skills_prompt=self.skills_prompt,
        )

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._build_system()}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(
        self,
        user_input: str,
        on_token: Callable | None = None,
        on_tool: Callable | None = None,
        on_thinking: Callable | None = None,
        on_thinking_done: Callable | None = None,
        on_tool_start: Callable | None = None,
        on_tool_end: Callable | None = None,
    ) -> str:
        """Process one user message. May involve multiple LLM/tool rounds.

        Returns the final text response, or an interruption notice if Ctrl+C
        was pressed during processing.
        """
        self._cancel_event.clear()

        # If the previous run was interrupted, let the AI know before
        # processing the new message so it can pick up context.
        was_interrupted = self._was_cancelled
        self._was_cancelled = False

        if was_interrupted:
            self.messages.append({
                "role": "user",
                "content": (
                    "[System notice: Your previous task was interrupted by the user "
                    "via Ctrl+C. The task you were working on was stopped mid-execution. "
                    "Below is the user's new message — respond to it directly.]\n\n"
                    + user_input
                ),
            })
        else:
            self.messages.append({"role": "user", "content": user_input})

        self.context.maybe_compress(self.messages, self.llm, self._build_system())

        for _ in range(self.max_rounds):
            # Check for cancellation before each LLM call
            if self._cancel_event.is_set():
                self._was_cancelled = True
                return "[Interrupted by user]"

            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
                on_thinking=on_thinking,
                cancel_event=self._cancel_event,
            )

            # LLM may have been interrupted mid-stream
            if self._cancel_event.is_set():
                self._was_cancelled = True
                return resp.content or "[Interrupted by user]"

            if resp.reasoning_content and on_thinking_done:
                on_thinking_done(resp.reasoning_content)

            if not resp.tool_calls:
                self.messages.append(resp.message)
                return resp.content

            self.messages.append(resp.message)

            if len(resp.tool_calls) == 1:
                tc = resp.tool_calls[0]
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                if on_tool_start:
                    on_tool_start(tc.id, tc.name, tc.arguments)
                result = self._exec_tool(tc)
                if on_tool_end:
                    on_tool_end(tc.id, tc.name, tc.arguments, result)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
            else:
                results = self._exec_tools_parallel(
                    resp.tool_calls, on_tool, on_tool_start, on_tool_end,
                )
                for tc, result in zip(resp.tool_calls, results):
                    self.messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": result}
                    )

            # After tool execution, check for cancellation before next round
            if self._cancel_event.is_set():
                self._was_cancelled = True
                return "[Interrupted by user]"

            self.context.maybe_compress(self.messages, self.llm, self._build_system())

        return "(reached maximum tool-call rounds)"

    def cancel(self):
        """Signal the agent to stop at the next safe point.

        Also cancels any currently running tool (e.g. terminates a bash subprocess).
        Thread-safe — may be called from the main thread while chat() runs in an executor.
        """
        self._cancel_event.set()
        for tool in self.tools:
            try:
                tool.cancel()
            except Exception as e:
                logger.debug(f"Error cancelling tool {tool.name}: {e}")

    async def chat_async(
        self,
        user_input: str,
        on_token: Callable | None = None,
        on_tool: Callable | None = None,
        on_thinking: Callable | None = None,
        on_thinking_done: Callable | None = None,
        on_tool_start: Callable | None = None,
        on_tool_end: Callable | None = None,
    ) -> str:
        """Async wrapper: runs the synchronous chat in a thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.chat(
                user_input, on_token, on_tool,
                on_thinking, on_thinking_done, on_tool_start, on_tool_end,
            ),
        )

    def _exec_tool(self, tc) -> str:
        tool = get_tool(tc.name, self.tools)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        try:
            return tool.execute(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        except Exception as e:
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(
        self, tool_calls, on_tool=None, on_tool_start=None, on_tool_end=None,
    ) -> list[str]:
        """Run multiple tool calls concurrently using threads."""
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)
            if on_tool_start:
                on_tool_start(tc.id, tc.name, tc.arguments)

        def _run_and_notify(tc):
            result = self._exec_tool(tc)
            if on_tool_end:
                on_tool_end(tc.id, tc.name, tc.arguments, result)
            return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_run_and_notify, tc) for tc in tool_calls]
            return [f.result() for f in futures]

    def reset(self):
        self.messages.clear()
