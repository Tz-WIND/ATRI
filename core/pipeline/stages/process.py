"""Process stage - the core: integrates the Agent loop with dynamic system prompts.

This is where corecoder's Agent.chat() is invoked. The Agent uses workspace-bound
tools, 3-layer context compression, parallel tool execution, and dynamic prompts.
Each user/group session gets its own Agent instance with isolated context.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from core import logger
from core.agent.agent import Agent
from core.agent.llm import LLM
from core.agent.session import SessionStore
from core.platform.message import MessageEvent
from core.pipeline.stage import Stage, register_stage
from core.skills import SkillManager, build_skills_prompt

if TYPE_CHECKING:
    pass


@register_stage
class ProcessStage(Stage):
    async def initialize(self, ctx: dict) -> None:
        self.workspace: str = ctx.get("workspace", ".")
        self.model: str = ctx.get("model", "gpt-4o")
        self.api_key: str = ctx.get("api_key", "")
        self.base_url: str | None = ctx.get("base_url")
        self.max_tokens: int = ctx.get("max_tokens", 4096)
        self.temperature: float = ctx.get("temperature", 0.0)
        self.max_context_tokens: int = ctx.get("max_context_tokens", 128_000)
        self.max_rounds: int = ctx.get("max_rounds", 50)
        self.extra_instructions: str = ctx.get("extra_instructions", "")
        self.persona: str = ctx.get("persona", "")
        self.skills_root: str = ctx.get("skills_root", "skills")
        self.skills_config: dict = ctx.get("skills_config", {})
        self.tavily_api_key: str = ctx.get("tavily_api_key", "")

        from core.tools.web_search import set_tavily_key
        set_tavily_key(self.tavily_api_key or None)

        self.skill_manager = SkillManager(self.skills_root, self.skills_config)
        self._skills_prompt = self._build_skills_prompt()

        self.session_store = SessionStore(ctx.get("sessions_dir"))

        self._agents: dict[str, Agent] = {}
        self._llm_template = {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        self.broadcast_fn: Callable[[dict], Coroutine[Any, Any, None]] | None = None

    def _build_skills_prompt(self) -> str:
        active_skills = self.skill_manager.list_skills(active_only=True)
        if not active_skills:
            return ""
        return build_skills_prompt(active_skills)

    def _get_or_create_agent(self, session_id: str) -> Agent:
        """Get existing agent for session or create a new one.

        Each session (user/group) gets its own Agent with its own LLM instance,
        ensuring isolated context and independent token tracking.
        """
        if session_id not in self._agents:
            llm = LLM(**self._llm_template)
            agent = Agent(
                llm=llm,
                workspace=self.workspace,
                max_context_tokens=self.max_context_tokens,
                max_rounds=self.max_rounds,
                extra_instructions=self.extra_instructions,
                persona=self.persona,
                skills_prompt=self._skills_prompt,
            )
            # Try to restore session from disk
            loaded = self.session_store.load(session_id)
            if loaded:
                agent.messages = loaded[0]
                logger.info(f"Restored session {session_id} with {len(loaded[0])} messages")
            self._agents[session_id] = agent

        return self._agents[session_id]

    def _fire(self, data: dict):
        """Thread-safe broadcast: schedule the async broadcast on the event loop."""
        if not self.broadcast_fn:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast_fn(data), loop)
            else:
                loop.run_until_complete(self.broadcast_fn(data))
        except RuntimeError:
            pass

    async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
        if not event.message_str.strip():
            yield
            return

        session_id = event.unified_msg_origin
        agent = self._get_or_create_agent(session_id)
        loop = asyncio.get_event_loop()

        tool_events: list[dict] = []

        def _broadcast_sync(data: dict):
            if not self.broadcast_fn:
                return
            try:
                asyncio.run_coroutine_threadsafe(self.broadcast_fn(data), loop)
            except RuntimeError:
                pass

        def on_tool(name, kwargs):
            tool_events.append({"tool": name, "args": kwargs})
            logger.info(f"[{session_id}] Tool: {name}({_brief(kwargs)})")

        def on_thinking(content: str):
            _broadcast_sync({
                "type": "thinking_delta",
                "session_id": session_id,
                "content": content,
            })

        thinking_done_sent = False

        def mark_thinking_done():
            nonlocal thinking_done_sent
            if thinking_done_sent:
                return
            thinking_done_sent = True
            _broadcast_sync({
                "type": "thinking_done",
                "session_id": session_id,
            })

        def on_thinking_done(full_content: str):
            mark_thinking_done()

        response_started = False

        def on_token(content: str):
            nonlocal response_started
            mark_thinking_done()
            if not response_started:
                response_started = True
                _broadcast_sync({
                    "type": "response_start",
                    "session_id": session_id,
                })
            _broadcast_sync({
                "type": "response_delta",
                "session_id": session_id,
                "content": content,
            })

        def on_tool_start(tc_id: str, name: str, args: dict):
            mark_thinking_done()
            _broadcast_sync({
                "type": "tool_start",
                "session_id": session_id,
                "data": {"id": tc_id, "tool": name, "args": args},
            })

        def on_tool_end(tc_id: str, name: str, args: dict, result: str):
            is_error = result.startswith("Error")
            preview_len = 8000 if name in {"edit_file", "write_file"} else 200
            preview = result[:preview_len] if len(result) > preview_len else result
            _broadcast_sync({
                "type": "tool_end",
                "session_id": session_id,
                "data": {
                    "id": tc_id,
                    "tool": name,
                    "args": args,
                    "success": not is_error,
                    "result_preview": preview,
                },
            })

        try:
            logger.info(f"[{session_id}] Processing: {event.message_str[:80]}")
            response = await agent.chat_async(
                event.message_str,
                on_token=on_token,
                on_tool=on_tool,
                on_thinking=on_thinking,
                on_thinking_done=on_thinking_done,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
            )
            mark_thinking_done()

            # Drain any pending cross-thread callbacks before responding
            await asyncio.sleep(0)

            event.set_result(response)
            event._extras["tool_events"] = tool_events

            # Send response_done directly (not via thread-safe scheduling)
            # so it is guaranteed to reach the frontend before the HTTP response.
            if self.broadcast_fn:
                await self.broadcast_fn({
                    "type": "response_done",
                    "session_id": session_id,
                    "content": response,
                })

            self.session_store.save(
                agent.messages,
                agent.llm.model,
                session_id,
            )

        except Exception as e:
            logger.exception(f"Agent error for {session_id}: {e}")
            event.set_result(f"Error: {e}")

        yield

    def update_config(self, **kwargs):
        """Hot-reload configuration (called from WebUI/dashboard)."""
        if "model" in kwargs:
            self._llm_template["model"] = kwargs["model"]
            for agent in self._agents.values():
                agent.llm.model = kwargs["model"]
        if "api_key" in kwargs and kwargs["api_key"] != "***":
            self._llm_template["api_key"] = kwargs["api_key"]
            for agent in self._agents.values():
                agent.llm.client.api_key = kwargs["api_key"]
        if "base_url" in kwargs:
            self._llm_template["base_url"] = kwargs["base_url"]
            for agent in self._agents.values():
                agent.llm.client.base_url = kwargs["base_url"]
        if "extra_instructions" in kwargs:
            self.extra_instructions = kwargs["extra_instructions"]
            self._llm_template["extra_instructions"] = kwargs["extra_instructions"]
            for agent in self._agents.values():
                agent.extra_instructions = kwargs["extra_instructions"]
        if "persona" in kwargs:
            self.persona = kwargs["persona"]
            self._llm_template["persona"] = kwargs["persona"]
            for agent in self._agents.values():
                agent.persona = kwargs["persona"]
        if "skills" in kwargs:
            self.skill_manager.skills_config = kwargs["skills"]
            self._skills_prompt = self._build_skills_prompt()
            for agent in self._agents.values():
                agent.skills_prompt = self._skills_prompt
        if "tavily_api_key" in kwargs:
            self.tavily_api_key = kwargs["tavily_api_key"]
            from core.tools.web_search import set_tavily_key
            set_tavily_key(self.tavily_api_key or None)

    def reset_session(self, session_id: str):
        """Clear a specific session's history."""
        if session_id in self._agents:
            self._agents[session_id].reset()
            self.session_store.delete(session_id)


def _brief(kwargs: dict, maxlen: int = 60) -> str:
    s = ", ".join(f"{k}={repr(v)[:30]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
