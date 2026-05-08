"""Process stage - the core: integrates the Agent loop with dynamic system prompts.

This is where corecoder's Agent.chat() is invoked. The Agent uses workspace-bound
tools, 3-layer context compression, parallel tool execution, and dynamic prompts.
Each user/group session gets its own Agent instance with isolated context.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from core import logger
from core.agent.agent import Agent
from core.agent.context import TOOL_OUTPUT_COMPRESSED_MARKER
from core.agent.llm import LLM
from core.agent.session import SessionStore
from core.pipeline.stage import Stage, register_stage
from core.platform.message import MessageEvent, normalize_session_id
from core.runtime import RuntimeTimelineStore, summarize_text
from core.skills import SkillManager, build_skills_prompt
from core.tools.bash import CONFIRM_MARKER
from core.utils import clean_optional_str

if TYPE_CHECKING:
    pass


@register_stage
class ProcessStage(Stage):
    async def initialize(self, ctx: dict) -> None:
        self.workspace: str = ctx.get("workspace", ".")
        self.model: str = ctx.get("model", "gpt-4o")
        self.api_key: str = ctx.get("api_key", "")
        self.base_url: str | None = ctx.get("base_url")
        self.api_format: str = ctx.get("api_format", "openai")
        self.active_models: list[dict] = list(ctx.get("active_models", []))
        self.providers: dict = dict(ctx.get("providers", {}))
        self.max_tokens: int = ctx.get("max_tokens", 4096)
        self.temperature: float = ctx.get("temperature", 0.0)
        self.max_context_tokens: int = ctx.get("max_context_tokens", 128_000)
        self.max_rounds: int = ctx.get("max_rounds", 50)
        self.extra_instructions: str = ctx.get("extra_instructions", "")
        self.persona: str = ctx.get("persona", "")
        self.skills_root: str = ctx.get("skills_root", "skills")
        self.skill_search_roots: list[str] = ctx.get("skill_search_roots", [])
        self.skills_config: dict = ctx.get("skills_config", {})
        self.tavily_api_key: str = ctx.get("tavily_api_key", "")
        self.mcp_servers: dict = dict(ctx.get("mcp_servers", {}))

        from core.tools.web_search import set_tavily_key

        set_tavily_key(self.tavily_api_key or None)

        self.skill_manager = SkillManager(
            self.skills_root,
            self.skills_config,
            workspace=self.workspace,
            search_roots=self.skill_search_roots,
        )
        self._skills_prompt = self._build_skills_prompt()

        self.session_store = SessionStore(ctx.get("sessions_dir"))
        self.runtime_store = RuntimeTimelineStore(ctx.get("runtime_dir"))

        self._agents: dict[str, Agent] = {}
        self._agents_lock = threading.Lock()
        self._agents_last_active: dict[str, float] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._llm_template = {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_format": self.api_format,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        self.broadcast_fn: Callable[[dict], Coroutine[Any, Any, None]] | None = None
        self._active_session_ids: set[str] = set()
        self._active_lock = threading.Lock()

        # Store event loop reference for thread-safe broadcasting from agent executor
        self._loop = asyncio.get_running_loop()

    def _build_skills_prompt(self) -> str:
        active_skills = self.skill_manager.list_skills(active_only=True)
        if not active_skills:
            return ""
        return build_skills_prompt(active_skills)

    # Seconds of inactivity before an idle agent is evicted from memory
    AGENT_TTL_SECONDS = 1800  # 30 minutes

    def _get_or_create_agent(self, session_id: str) -> Agent:
        """Get existing agent for session or create a new one.

        Each session (user/group) gets its own Agent with its own LLM instance,
        ensuring isolated context and independent token tracking.

        Idle agents are evicted after AGENT_TTL_SECONDS to prevent memory leaks.
        Thread-safe: uses _agents_lock for dict access from executor threads.

        NOTE: The lock is held during LLM construction and session-store disk I/O
        when creating a new agent. Agent creation is infrequent (only on first
        message per session or after TTL eviction), so this is acceptable in
        practice. If agent creation ever moves to a hot path, switch to a
        double-checked locking pattern to reduce lock hold time.
        """
        now = time.time()

        with self._agents_lock:
            # Evict stale agents first.
            # FUTURE: If Agent ever acquires resources that need explicit
            # cleanup (thread pools, file handles, subprocesses), add a
            # cleanup call here before popping; currently GC is sufficient.
            stale = [
                sid
                for sid, last in self._agents_last_active.items()
                if now - last > self.AGENT_TTL_SECONDS
            ]
            for sid in stale:
                logger.info(f"Evicting idle agent for session {sid}")
                self._agents.pop(sid, None)
                self._agents_last_active.pop(sid, None)
                self._session_locks.pop(sid, None)

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
                    skill_manager=self.skill_manager,
                    mcp_servers=self.mcp_servers,
                    llm_factory=self._create_llm_for_model,
                    model_catalog=self._model_catalog,
                )
                # Try to restore session from disk
                loaded = self.session_store.load(session_id)
                if loaded:
                    agent.messages = loaded[0]
                    logger.info(f"Restored session {session_id} with {len(loaded[0])} messages")
                self._agents[session_id] = agent
                self._agents_last_active[session_id] = now

            self._agents_last_active[session_id] = now
            return self._agents[session_id]

    def _create_llm_for_model(
        self,
        model: str | None = None,
        provider: str | None = None,
    ) -> LLM:
        """Create a fresh LLM for a main or sub-agent model choice."""
        cfg = self._resolve_llm_config(model=model, provider=provider)
        return LLM(**cfg)

    def _resolve_llm_config(
        self,
        model: str | None = None,
        provider: str | None = None,
    ) -> dict:
        requested_model = clean_optional_str(model)
        requested_provider = clean_optional_str(provider)
        cfg = dict(self._llm_template)

        if not requested_model and not requested_provider:
            return cfg
        if requested_provider and not requested_model:
            raise ValueError("model is required when provider is specified")

        if requested_provider:
            provider_cfg = self.providers.get(requested_provider)
            if not isinstance(provider_cfg, dict):
                raise ValueError(f"unknown provider '{requested_provider}'")
            cfg.update(_llm_config_from_provider(provider_cfg))
            cfg["model"] = requested_model
            return cfg

        matches = [
            item
            for item in self.active_models
            if isinstance(item, dict) and item.get("model") == requested_model
        ]
        providers = sorted(
            {str(item.get("provider") or "") for item in matches if isinstance(item, dict)}
        )
        if len(providers) > 1:
            raise ValueError(
                f"model '{requested_model}' is available from multiple providers; specify provider"
            )
        if len(providers) == 1 and providers[0]:
            provider_cfg = self.providers.get(providers[0])
            if not isinstance(provider_cfg, dict):
                raise ValueError(f"unknown provider '{providers[0]}'")
            cfg.update(_llm_config_from_provider(provider_cfg))

        cfg["model"] = requested_model
        return cfg

    def _model_catalog(self) -> list[dict]:
        choices = []
        seen = set()
        for item in self.active_models:
            if not isinstance(item, dict):
                continue
            model = clean_optional_str(item.get("model"))
            if not model:
                continue
            provider = clean_optional_str(item.get("provider")) or ""
            key = (model, provider)
            if key in seen:
                continue
            seen.add(key)
            choices.append({"model": model, "provider": provider})
        if not choices:
            choices.append({"model": self.model, "provider": ""})
        return choices

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        with self._agents_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _fire(self, data: dict):
        """Thread-safe broadcast: schedule the async broadcast on the event loop."""
        if not self.broadcast_fn:
            return
        try:
            if self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast_fn(data), self._loop)
            else:
                self._loop.run_until_complete(self.broadcast_fn(data))
        except RuntimeError:
            pass

    async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
        if not event.message_str.strip():
            yield
            return

        session_id = event.unified_msg_origin
        session_lock = self._get_session_lock(session_id)
        async with session_lock:
            async for item in self._process_locked(event, session_id):
                yield item

    async def _process_locked(
        self,
        event: MessageEvent,
        session_id: str,
    ) -> AsyncGenerator[None, None]:
        agent = self._get_or_create_agent(session_id)
        thread_id = session_id
        runtime_persist_failed = False
        try:
            turn_id = self.runtime_store.start_turn(
                thread_id,
                input_text=event.message_str,
                model=agent.llm.model,
                workspace=self.workspace,
                metadata={
                    "platform": event.platform_name,
                    "message_type": event.message_type.value,
                    "sender": {
                        "user_id": event.sender.user_id,
                        "nickname": event.sender.nickname,
                    },
                },
            )
        except Exception:
            logger.exception(f"Runtime timeline turn creation failed for {session_id}")
            runtime_persist_failed = True
            turn_id = f"turn_ephemeral_{int(time.time() * 1000)}"

        tool_events: list[dict] = []
        pending_futures: list[concurrent.futures.Future[Any]] = []

        def _record_runtime_event(
            event_type: str,
            payload: dict,
            *,
            item_id: str | None = None,
        ) -> dict:
            nonlocal runtime_persist_failed
            if runtime_persist_failed:
                fallback = dict(payload)
                fallback.setdefault("type", event_type)
                fallback["thread_id"] = thread_id
                fallback["turn_id"] = turn_id
                if item_id:
                    fallback["item_id"] = item_id
                return fallback
            try:
                record = self.runtime_store.append_event(
                    thread_id,
                    event_type=event_type,
                    payload=payload,
                    turn_id=turn_id,
                    item_id=item_id,
                )
                return record.to_wire_payload()
            except Exception:
                if not runtime_persist_failed:
                    logger.exception(f"Runtime timeline persistence failed for {session_id}")
                    runtime_persist_failed = True
                fallback = dict(payload)
                fallback.setdefault("type", event_type)
                fallback["thread_id"] = thread_id
                fallback["turn_id"] = turn_id
                if item_id:
                    fallback["item_id"] = item_id
                return fallback

        def _create_runtime_item(
            *,
            kind: str,
            summary: str,
            status: str = "in_progress",
            detail: str | None = None,
            metadata: dict | None = None,
        ) -> str | None:
            nonlocal runtime_persist_failed
            if runtime_persist_failed:
                return None
            try:
                return self.runtime_store.create_item(
                    thread_id,
                    turn_id,
                    kind=kind,
                    summary=summary,
                    status=status,
                    detail=detail,
                    metadata=metadata,
                )
            except Exception:
                if not runtime_persist_failed:
                    logger.exception(f"Runtime timeline item persistence failed for {session_id}")
                    runtime_persist_failed = True
                return None

        def _finish_runtime_item(
            item_id: str | None,
            *,
            status: str = "completed",
            detail: str | None = None,
            metadata: dict | None = None,
        ) -> None:
            nonlocal runtime_persist_failed
            if not item_id:
                return
            if runtime_persist_failed:
                return
            try:
                self.runtime_store.finish_item(
                    item_id,
                    status=status,
                    detail=detail,
                    metadata=metadata,
                )
            except Exception:
                if not runtime_persist_failed:
                    logger.exception(f"Runtime timeline item update failed for {session_id}")
                    runtime_persist_failed = True

        def _finish_runtime_turn(*, status: str = "completed", error: str | None = None) -> None:
            nonlocal runtime_persist_failed
            if runtime_persist_failed:
                return
            try:
                self.runtime_store.finish_turn(turn_id, status=status, error=error)
            except Exception:
                if not runtime_persist_failed:
                    logger.exception(f"Runtime timeline turn update failed for {session_id}")
                    runtime_persist_failed = True

        def _broadcast_sync(data: dict):
            if not self.broadcast_fn:
                return
            try:
                fut = asyncio.run_coroutine_threadsafe(self.broadcast_fn(data), self._loop)
                pending_futures.append(fut)
            except RuntimeError:
                pass

        def on_tool(name, kwargs):
            tool_events.append({"tool": name, "args": kwargs})
            logger.info(f"[{session_id}] Tool: {name}({_brief(kwargs)})")

        thinking_done_sent = False
        thinking_content_parts: list[str] = []
        thinking_item_id: str | None = None

        _record_runtime_event(
            "turn_started",
            {
                "type": "turn_started",
                "session_id": session_id,
                "input_summary": summarize_text(event.message_str),
                "model": agent.llm.model,
            },
        )
        user_item_id = _create_runtime_item(
            kind="user_message",
            summary=summarize_text(event.message_str),
            status="completed",
            detail=event.message_str,
        )
        _record_runtime_event(
            "user_message",
            {
                "type": "user_message",
                "session_id": session_id,
                "content": event.message_str,
            },
            item_id=user_item_id,
        )

        def on_thinking(content: str):
            nonlocal thinking_item_id
            if content:
                thinking_content_parts.append(content)
            if thinking_item_id is None:
                thinking_item_id = _create_runtime_item(
                    kind="agent_reasoning",
                    summary="Thinking",
                    metadata={"channel": "reasoning"},
                )
            _broadcast_sync(
                _record_runtime_event(
                    "thinking_delta",
                    {
                        "type": "thinking_delta",
                        "session_id": session_id,
                        "content": content,
                    },
                    item_id=thinking_item_id,
                )
            )

        def mark_thinking_done():
            nonlocal thinking_done_sent
            if thinking_done_sent:
                return
            thinking_done_sent = True
            if thinking_item_id is not None:
                _finish_runtime_item(
                    thinking_item_id,
                    detail="".join(thinking_content_parts),
                )
            _broadcast_sync(
                _record_runtime_event(
                    "thinking_done",
                    {
                        "type": "thinking_done",
                        "session_id": session_id,
                    },
                    item_id=thinking_item_id,
                )
            )

        def on_thinking_done(full_content: str):
            nonlocal thinking_item_id
            if full_content and not thinking_content_parts:
                if thinking_item_id is None:
                    thinking_item_id = _create_runtime_item(
                        kind="agent_reasoning",
                        summary="Thinking",
                        metadata={"channel": "reasoning"},
                    )
                thinking_content_parts.append(full_content)
                _broadcast_sync(
                    _record_runtime_event(
                        "thinking_delta",
                        {
                            "type": "thinking_delta",
                            "session_id": session_id,
                            "content": full_content,
                        },
                        item_id=thinking_item_id,
                    )
                )
            mark_thinking_done()

        response_started = False
        response_item_id: str | None = None
        response_content_parts: list[str] = []
        tool_item_ids: dict[str, str] = {}

        def on_token(content: str):
            nonlocal response_item_id, response_started
            mark_thinking_done()
            if not response_started:
                response_started = True
                response_item_id = _create_runtime_item(
                    kind="agent_message",
                    summary="Assistant response",
                    metadata={"channel": "text"},
                )
                _broadcast_sync(
                    _record_runtime_event(
                        "response_start",
                        {
                            "type": "response_start",
                            "session_id": session_id,
                        },
                        item_id=response_item_id,
                    )
                )
            if content:
                response_content_parts.append(content)
            _broadcast_sync(
                _record_runtime_event(
                    "response_delta",
                    {
                        "type": "response_delta",
                        "session_id": session_id,
                        "content": content,
                    },
                    item_id=response_item_id,
                )
            )

        def on_tool_start(tc_id: str, name: str, args: dict):
            mark_thinking_done()
            item_id = _create_runtime_item(
                kind="command_execution" if name == "bash" else "tool_call",
                summary=name,
                metadata={"tool_call_id": tc_id, "tool": name, "args": args},
            )
            if item_id:
                tool_item_ids[tc_id] = item_id
            _broadcast_sync(
                _record_runtime_event(
                    "tool_start",
                    {
                        "type": "tool_start",
                        "session_id": session_id,
                        "data": {"id": tc_id, "tool": name, "args": args},
                    },
                    item_id=item_id,
                )
            )

        def on_tool_end(tc_id: str, name: str, args: dict, result: str):
            is_error = result.startswith("Error")
            is_blocked = "BLOCKED:" in result
            needs_confirm = CONFIRM_MARKER in result
            is_compressed = result.startswith(TOOL_OUTPUT_COMPRESSED_MARKER)
            result_id = _extract_tool_result_id(result) if is_compressed else ""
            preview_len = 8000 if name in {"edit_file", "write_file"} or is_compressed else 200
            preview = result[:preview_len] if len(result) > preview_len else result
            success = not is_error and not is_blocked and not needs_confirm
            item_id = tool_item_ids.get(tc_id)
            _finish_runtime_item(
                item_id,
                status="completed" if success else "failed",
                detail=preview,
                metadata={
                    "success": success,
                    "result_compressed": is_compressed,
                    "result_id": result_id,
                },
            )
            _broadcast_sync(
                _record_runtime_event(
                    "tool_end",
                    {
                        "type": "tool_end",
                        "session_id": session_id,
                        "data": {
                            "id": tc_id,
                            "tool": name,
                            "args": args,
                            "success": success,
                            "result_preview": preview,
                            "result_compressed": is_compressed,
                            "result_id": result_id,
                        },
                    },
                    item_id=item_id,
                )
            )
            if needs_confirm and name == "bash":
                _broadcast_sync(
                    _record_runtime_event(
                        "confirm_command",
                        {
                            "type": "confirm_command",
                            "session_id": session_id,
                            "command": args.get("command", ""),
                            "reason": result.split(f"{CONFIRM_MARKER}: ")[-1].split("\n")[0],
                        },
                        item_id=item_id,
                    )
                )

        try:
            logger.info(f"[{session_id}] Processing: {event.message_str[:80]}")
            with self._active_lock:
                self._active_session_ids.add(session_id)
            response = await agent.chat_async(
                event.message_str,
                on_token=on_token,
                on_tool=on_tool,
                on_thinking=on_thinking,
                on_thinking_done=on_thinking_done,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
            )
            response_text = response or ""
            mark_thinking_done()

            # Drain all pending cross-thread callbacks before responding
            if pending_futures:
                await asyncio.gather(
                    *[asyncio.wrap_future(f) for f in pending_futures],
                    return_exceptions=True,
                )
                pending_futures.clear()

            event.set_result(response_text)
            event._extras["tool_events"] = tool_events
            if response_item_id is None and response_text:
                response_item_id = _create_runtime_item(
                    kind="agent_message",
                    summary="Assistant response",
                    status="completed",
                    detail=response_text,
                    metadata={"channel": "text"},
                )
            else:
                _finish_runtime_item(
                    response_item_id,
                    detail=response_text or "".join(response_content_parts),
                )

            # Send response_done directly (not via thread-safe scheduling)
            # so it is guaranteed to reach the frontend before the HTTP response.
            response_done_event = _record_runtime_event(
                "response_done",
                {
                    "type": "response_done",
                    "session_id": session_id,
                    "content": response_text,
                },
                item_id=response_item_id,
            )
            if self.broadcast_fn:
                await self.broadcast_fn(response_done_event)

            turn_status = "canceled" if response_text.startswith("[Interrupted") else "completed"
            _finish_runtime_turn(status=turn_status)
            _record_runtime_event(
                "turn_completed",
                {
                    "type": "turn_completed",
                    "session_id": session_id,
                    "status": turn_status,
                },
            )

            self.session_store.save(
                agent.messages,
                agent.llm.model,
                session_id,
            )

        except Exception as e:
            logger.exception(f"Agent error for {session_id}: {e}")
            event.set_result(f"Error: {e}")
            _finish_runtime_turn(status="failed", error=str(e))
            _record_runtime_event(
                "error",
                {
                    "type": "error",
                    "session_id": session_id,
                    "message": str(e),
                },
            )
        finally:
            with self._active_lock:
                self._active_session_ids.discard(session_id)

        yield

    def cancel_current(self) -> bool:
        """Cancel the currently running agent operation (thread-safe).

        Called from the main thread on Ctrl+C to interrupt LLM streaming
        and tool execution without shutting down the whole process.
        """
        with self._active_lock:
            active = list(self._active_session_ids)
        if not active:
            return False

        cancelled = False
        with self._agents_lock:
            for sid in active:
                agent = self._agents.get(sid)
                if agent:
                    agent.cancel()
                    logger.info(f"Cancelled agent for session {sid}")
                    cancelled = True
        return cancelled

    def cancel_session(self, session_id: str) -> bool:
        """Cancel a specific session's agent (thread-safe).

        Called from the dashboard HTTP handler when the frontend user
        presses the stop/interrupt button.
        """
        session_id = normalize_session_id(session_id)
        with self._agents_lock:
            if session_id in self._agents:
                self._agents[session_id].cancel()
                logger.info(f"Cancelled agent for session {session_id}")
                return True
        return self.cancel_current()

    def update_config(self, **kwargs):
        """Hot-reload configuration (called from WebUI/dashboard)."""
        llm_updates = {}
        if "model" in kwargs:
            self.model = kwargs["model"]
            self._llm_template["model"] = kwargs["model"]
            llm_updates["model"] = kwargs["model"]
        if "api_key" in kwargs and kwargs["api_key"] != "***":
            self.api_key = kwargs["api_key"]
            self._llm_template["api_key"] = kwargs["api_key"]
            llm_updates["api_key"] = kwargs["api_key"]
        if "base_url" in kwargs:
            self.base_url = kwargs["base_url"]
            self._llm_template["base_url"] = kwargs["base_url"]
            llm_updates["base_url"] = kwargs["base_url"]
        if "api_format" in kwargs:
            self.api_format = kwargs["api_format"]
            self._llm_template["api_format"] = kwargs["api_format"]
            llm_updates["api_format"] = kwargs["api_format"]
        if "active_models" in kwargs:
            self.active_models = list(kwargs["active_models"] or [])
        if "providers" in kwargs:
            self.providers = dict(kwargs["providers"] or {})
        if "max_tokens" in kwargs:
            self.max_tokens = int(kwargs["max_tokens"])
            self._llm_template["max_tokens"] = self.max_tokens
            llm_updates["max_tokens"] = self.max_tokens
        if "temperature" in kwargs:
            self.temperature = float(kwargs["temperature"])
            self._llm_template["temperature"] = self.temperature
            llm_updates["temperature"] = self.temperature
        if llm_updates:
            with self._agents_lock:
                for agent in self._agents.values():
                    agent.llm.reconfigure(**llm_updates)
        agent_limit_updates = {}
        if "max_context_tokens" in kwargs:
            self.max_context_tokens = int(kwargs["max_context_tokens"])
            agent_limit_updates["max_context_tokens"] = self.max_context_tokens
        if "max_rounds" in kwargs:
            self.max_rounds = int(kwargs["max_rounds"])
            agent_limit_updates["max_rounds"] = self.max_rounds
        if agent_limit_updates:
            with self._agents_lock:
                for agent in self._agents.values():
                    if "max_context_tokens" in agent_limit_updates:
                        agent.context.set_max_tokens(self.max_context_tokens)
                    if "max_rounds" in agent_limit_updates:
                        agent.max_rounds = self.max_rounds
        if "extra_instructions" in kwargs:
            self.extra_instructions = kwargs["extra_instructions"]
            self._llm_template.pop("extra_instructions", None)
            with self._agents_lock:
                for agent in self._agents.values():
                    agent.extra_instructions = kwargs["extra_instructions"]
        if "persona" in kwargs:
            self.persona = kwargs["persona"]
            self._llm_template.pop("persona", None)
            with self._agents_lock:
                for agent in self._agents.values():
                    agent.persona = kwargs["persona"]
        skills_changed = False
        if "skills_root" in kwargs:
            self.skills_root = kwargs["skills_root"]
            self.skill_manager.skills_root = self.skills_root
            self.skill_manager._ensure_dir()
            self.skill_manager.invalidate_cache()
            skills_changed = True
        if "skill_search_roots" in kwargs:
            self.skill_search_roots = list(kwargs["skill_search_roots"])
            self.skill_manager.search_roots = self.skill_search_roots
            self.skill_manager.invalidate_cache()
            skills_changed = True
        if "skills" in kwargs:
            self.skill_manager.skills_config = kwargs["skills"]
            self.skill_manager.invalidate_cache()
            skills_changed = True
        if skills_changed:
            self._skills_prompt = self._build_skills_prompt()
            with self._agents_lock:
                for agent in self._agents.values():
                    agent.skills_prompt = self._skills_prompt
        if "tavily_api_key" in kwargs:
            self.tavily_api_key = kwargs["tavily_api_key"]
            from core.tools.web_search import set_tavily_key

            set_tavily_key(self.tavily_api_key or None)
        if "mcp_servers" in kwargs:
            self.mcp_servers = dict(kwargs["mcp_servers"] or {})
            with self._agents_lock:
                for agent in self._agents.values():
                    agent.reload_tools(mcp_servers=self.mcp_servers)

    def get_agent(self, session_id: str) -> Agent | None:
        """Thread-safe lookup of a session's agent. Returns None if not found."""
        with self._agents_lock:
            return self._agents.get(session_id)

    @property
    def agent_count(self) -> int:
        """Thread-safe count of active agents."""
        with self._agents_lock:
            return len(self._agents)

    def reload_skills(self):
        """Rebuild skills prompt and push to all live agents (thread-safe)."""
        self._skills_prompt = self._build_skills_prompt()
        with self._agents_lock:
            for agent in self._agents.values():
                agent.skills_prompt = self._skills_prompt

    def reset_session(self, session_id: str) -> bool:
        """Clear a specific session's history."""
        deleted = self.session_store.delete(session_id)
        try:
            runtime_deleted = self.runtime_store.delete_thread(session_id)
        except Exception:
            logger.exception(f"Failed to delete runtime thread for session {session_id}")
            runtime_deleted = False
        with self._agents_lock:
            agent = self._agents.pop(session_id, None)
            if agent:
                agent.reset()
            self._agents_last_active.pop(session_id, None)
            self._session_locks.pop(session_id, None)
        return deleted or runtime_deleted


def _brief(kwargs: dict, maxlen: int = 60) -> str:
    s = ", ".join(f"{k}={repr(v)[:30]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")


def _extract_tool_result_id(result: str) -> str:
    for line in result.splitlines()[:8]:
        if line.startswith("tool_result_id:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("Tool result id:"):
            return line.split(":", 1)[1].strip()
    return ""


def _llm_config_from_provider(provider_cfg: dict) -> dict:
    return {
        "api_key": provider_cfg.get("api_key", ""),
        "base_url": provider_cfg.get("base_url") or None,
        "api_format": provider_cfg.get("api_format", "openai"),
    }
