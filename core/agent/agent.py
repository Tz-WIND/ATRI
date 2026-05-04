"""Core agent loop.

The pattern:
    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

Supports parallel tool execution, 3-layer context compression,
sub-agent isolation, and dynamic system prompts.
"""

import asyncio
import concurrent.futures
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
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
                on_thinking=on_thinking,
            )

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

            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

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
        loop = asyncio.get_event_loop()
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
