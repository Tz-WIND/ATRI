"""Sub-agent spawning with isolated context.

Spawns independent agents with their own conversation history, context manager,
LLM instance, and tool access. Sub-agents' contexts are fully isolated from the
parent.

Two execution modes:
- blocking (default): parent waits for sub-agent to finish
- background: sub-agent runs in a background thread, parent gets a task_id
  immediately and can continue working. Use agent_result to inspect live status,
  visible output, tool calls, and final results.

Parallel execution: pass multiple tasks via 'tasks' to run sub-agents concurrently.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core import logger

from .base import Tool, ToolCapabilities

if TYPE_CHECKING:
    from core.agent.agent import Agent
    from core.runtime import TaskStore


# ---------------------------------------------------------------------------
# Shared state for background tasks (class-level on AgentTool)
# ---------------------------------------------------------------------------
_background_tasks: dict[str, SubAgentRun] = {}
_tasks_lock = threading.Lock()
_background_pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)

_MAX_TEXT_CHARS = 4000
_MAX_RESULT_CHARS = 5000
_MAX_EVENT_PREVIEW_CHARS = 800
_MAX_REPORT_CHARS = 12000
_TEXT_SNAPSHOT_INTERVAL_SECONDS = 1.0


@dataclass
class SubAgentRun:
    task_id: str
    task: str
    model: str = ""
    provider: str = ""
    status: str = "queued"
    events: list[dict] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    result: str = ""
    error: str = ""
    future: concurrent.futures.Future | None = None
    agent: Agent | None = None
    task_store: TaskStore | None = field(default=None, repr=False, compare=False)
    _last_text_snapshot_at: float = field(default=0.0, init=False, repr=False, compare=False)
    lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )

    def set_status(self, status: str):
        with self.lock:
            self.status = status
        if status == "running":
            self._persist_start()
        else:
            self._persist_update(status=status)

    def add_text(self, text: str):
        if not text:
            return
        delta = ""
        visible_text = ""
        with self.lock:
            current_len = sum(len(part) for part in self.text_parts)
            remaining = max(0, _MAX_TEXT_CHARS - current_len)
            if remaining:
                delta = text[:remaining]
                self.text_parts.append(delta)
            elif not self.text_parts or self.text_parts[-1] != "\n... (text truncated)":
                delta = "\n... (text truncated)"
                self.text_parts.append(delta)
            if delta and self._should_persist_text_snapshot_locked():
                visible_text = "".join(self.text_parts)
        if delta:
            self._persist_event("text_delta", {"text": delta})
            if visible_text:
                self._persist_update(metadata={"text": visible_text})

    def add_tool_start(self, tc_id: str, name: str, args: dict):
        self.add_event(
            {
                "type": "tool_start",
                "id": tc_id,
                "tool": name,
                "args": args,
            }
        )

    def add_tool_end(self, tc_id: str, name: str, args: dict, result: str):
        success = not (
            isinstance(result, str) and (result.startswith("Error") or "BLOCKED:" in result)
        )
        self.add_event(
            {
                "type": "tool_end",
                "id": tc_id,
                "tool": name,
                "args": args,
                "success": success,
                "result_preview": _truncate(result, _MAX_EVENT_PREVIEW_CHARS),
            }
        )

    def add_event(self, event: dict):
        with self.lock:
            self.events.append(event)
        self._persist_event(str(event.get("type") or "event"), event)

    def finish(self, result: str):
        with self.lock:
            self.status = "done"
            self.result = _truncate(result, _MAX_RESULT_CHARS)
            visible_text = "".join(self.text_parts)
        self._persist_finish("completed", result=self.result, metadata={"text": visible_text})

    def fail(self, error: str):
        with self.lock:
            self.status = "error"
            self.error = error
            visible_text = "".join(self.text_parts)
        self._persist_finish("failed", error=error, metadata={"text": visible_text})

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "task_id": self.task_id,
                "task": self.task,
                "model": self.model,
                "provider": self.provider,
                "status": self.status,
                "events": list(self.events),
                "text": "".join(self.text_parts),
                "result": self.result,
                "error": self.error,
            }

    def _should_persist_text_snapshot_locked(self) -> bool:
        now = time.monotonic()
        if now - self._last_text_snapshot_at < _TEXT_SNAPSHOT_INTERVAL_SECONDS:
            return False
        self._last_text_snapshot_at = now
        return True

    def _persist_start(self) -> None:
        if not self.task_store:
            return
        self._safe_persist("start", lambda: self.task_store.start_task(self.task_id))

    def _persist_update(self, **kwargs: Any) -> None:
        if not self.task_store:
            return
        self._safe_persist("update", lambda: self.task_store.update_task(self.task_id, **kwargs))

    def _persist_finish(
        self,
        status: str,
        *,
        result: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.task_store:
            return
        self._safe_persist(
            "finish",
            lambda: self.task_store.finish_task(
                self.task_id,
                status=status,
                result=result,
                error=error,
                metadata=metadata,
            ),
        )

    def _persist_event(self, event_type: str, payload: dict) -> None:
        if not self.task_store:
            return
        self._safe_persist(
            "event",
            lambda: self.task_store.append_event(self.task_id, event_type, payload),
        )

    def _safe_persist(self, operation: str, callback) -> None:
        try:
            callback()
        except (RuntimeError, OSError, sqlite3.Error, TypeError, ValueError) as e:
            logger.debug(
                "Sub-agent task persistence %s failed for %s: %s",
                operation,
                self.task_id,
                e,
            )


# ---------------------------------------------------------------------------
# AgentTool
# ---------------------------------------------------------------------------


class AgentTool(Tool):
    name = "agent"
    description = (
        "Spawn one or more sub-agents to handle complex sub-tasks independently. "
        "Each sub-agent has its own isolated context, LLM instance, and fresh "
        "tool instances.\n"
        "\n"
        "**Blocking mode (default):** parent waits for sub-agent(s) to finish.\n"
        "All provided sub-agent tasks run through the same parallel executor. "
        "Use 'tasks' (array) or 'task_configs' to run multiple sub-agents at "
        "the same time.\n"
        "\n"
        "**Status reporting:** sub-agent results include status, visible text "
        "output, tool calls, and tool result previews. Thinking/reasoning "
        "content is not exposed to the parent agent.\n"
        "\n"
        "**Model selection:** optionally pass 'model' and 'provider' to run all "
        "spawned sub-agents on a specific configured model. Use 'task_configs' "
        "to choose different models per sub-agent.\n"
        "\n"
        "**Background mode:** set 'background: true' to spawn sub-agents that "
        "run asynchronously. The tool returns a task_id immediately so you can "
        "continue working. Use the agent_result tool to inspect live status, "
        "visible output, tool calls, and final results while sub-agents run."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A single task for one sub-agent.",
            },
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple tasks to run in parallel across independent sub-agent instances.",  # noqa: E501
            },
            "task_configs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Task for this sub-agent.",
                        },
                        "model": {
                            "type": "string",
                            "description": "Optional model for this sub-agent.",
                        },
                        "provider": {
                            "type": "string",
                            "description": "Optional provider name for this sub-agent.",
                        },
                    },
                    "required": ["task"],
                },
                "description": "Multiple sub-agent task objects, each with optional model/provider overrides.",  # noqa: E501
            },
            "model": {
                "type": "string",
                "description": "Optional model to use for spawned sub-agent(s). Omit to inherit the current model.",  # noqa: E501
            },
            "provider": {
                "type": "string",
                "description": "Optional configured provider for the selected model. Use when the same model name exists on multiple providers.",  # noqa: E501
            },
            "background": {
                "type": "boolean",
                "description": "If true, run sub-agent(s) in the background and return immediately with task_id(s). Use agent_result to inspect live status, visible output, tool calls, and final results.",  # noqa: E501
                "default": False,
            },
        },
        "required": [],
    }
    capabilities = ToolCapabilities(
        capability="agent.spawn",
        writes_files=True,
        executes_shell=True,
        network=True,
    )

    _parent_agent: Agent | None = None

    def __init__(self, workspace: str = ".", task_store: TaskStore | None = None):
        super().__init__(workspace)
        self.task_store = task_store
        self._active_runs: dict[str, SubAgentRun] = {}
        self._active_runs_lock = threading.Lock()

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------

    def execute(
        self,
        task: str | None = None,
        tasks: list[str] | None = None,
        task_configs: list[dict] | None = None,
        background: bool = False,
        model: str | None = None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Collect task list
        all_tasks: list[dict] = []
        if tasks:
            all_tasks.extend(
                {"task": t, "model": model, "provider": provider}
                for t in tasks
                if isinstance(t, str) and t.strip()
            )
        if task:
            all_tasks.append({"task": task, "model": model, "provider": provider})
        if task_configs:
            for item in task_configs:
                if not isinstance(item, dict):
                    continue
                configured_task = item.get("task")
                if not isinstance(configured_task, str) or not configured_task.strip():
                    continue
                all_tasks.append(
                    {
                        "task": configured_task,
                        "model": item.get("model") or model,
                        "provider": item.get("provider") or provider,
                    }
                )

        if not all_tasks:
            return "Error: no task or tasks provided"

        if background:
            return self._run_background(all_tasks)

        return self._run_parallel_blocking(all_tasks)

    # ------------------------------------------------------------------
    # Blocking execution
    # ------------------------------------------------------------------

    def _new_run(
        self,
        task_spec: dict,
        task_id: str | None = None,
        *,
        persist: bool = False,
    ) -> SubAgentRun:
        return SubAgentRun(
            task_id=task_id or f"run_{uuid.uuid4().hex[:8]}",
            task=task_spec["task"],
            model=_clean(task_spec.get("model")),
            provider=_clean(task_spec.get("provider")),
            task_store=self.task_store if persist else None,
        )

    def _create_child_agent(self, task_spec: dict) -> Agent:
        if self._parent_agent is None:
            raise RuntimeError("agent tool not initialized (no parent agent)")

        from core.agent.agent import Agent
        from core.agent.tools_bridge import get_all_tools

        parent = self._parent_agent
        child_tools = [
            t
            for t in get_all_tools(
                parent.workspace,
                skill_manager=parent.skill_manager,
                tool_result_store=parent.context.tool_result_store,
                task_store=parent.task_store,
                mcp_servers=parent.mcp_servers,
                mode_controller=parent.mode_controller,
            )
            if t.name not in ("agent", "agent_result")
        ]
        return Agent(
            llm=parent.create_child_llm(
                model=task_spec.get("model"),
                provider=task_spec.get("provider"),
            ),
            workspace=parent.workspace,
            tools=child_tools,
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
            extra_instructions=parent.extra_instructions,
            persona=parent.persona,
            skills_prompt=parent.skills_prompt,
            skill_manager=parent.skill_manager,
            task_store=parent.task_store,
            mode_controller=parent.mode_controller,
            mcp_servers=parent.mcp_servers,
        )

    def _run_subagent_task(self, run: SubAgentRun, task_spec: dict) -> str:
        with self._active_runs_lock:
            self._active_runs[run.task_id] = run

        try:
            sub = self._create_child_agent(task_spec)
            run.agent = sub
        except (RuntimeError, ValueError, OSError) as e:
            logger.warning("Sub-agent creation failed: %s", e)
            run.fail(f"Sub-agent setup error: {e}")
            with self._active_runs_lock:
                self._active_runs.pop(run.task_id, None)
            return run.error

        try:
            run.set_status("running")
            result = sub.chat(
                task_spec["task"],
                on_token=run.add_text,
                on_tool_start=run.add_tool_start,
                on_tool_end=run.add_tool_end,
            )
            run.finish(result)
            return result
        except (RuntimeError, ValueError, OSError) as e:
            run.fail(f"Sub-agent error: {e}")
            return run.error
        finally:
            with self._active_runs_lock:
                self._active_runs.pop(run.task_id, None)

    def _run_parallel_blocking(self, tasks: list[dict]) -> str:
        max_workers = min(len(tasks), 5)
        runs = [self._new_run(task_spec) for task_spec in tasks]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._run_subagent_task, run, task_spec): i
                for i, (run, task_spec) in enumerate(zip(runs, tasks, strict=False))
            }
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    future.result()
                except (RuntimeError, ValueError, OSError, concurrent.futures.CancelledError) as e:
                    runs[idx].fail(f"Sub-agent error: {e}")

        parts = []
        for i, run in enumerate(runs):
            parts.append(_format_run_report(run, total=len(runs), index=i))

        return _truncate("\n\n".join(parts), _MAX_REPORT_CHARS)

    # ------------------------------------------------------------------
    # Background (non-blocking) execution
    # ------------------------------------------------------------------

    def _run_background(self, tasks: list[dict]) -> str:
        """Spawn sub-agent(s) in background threads; return task IDs immediately."""
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        runs = []
        for task_spec in tasks:
            tid = f"bg_{uuid.uuid4().hex[:8]}"
            persist = False
            if self.task_store:
                try:
                    tid = self.task_store.create_task(
                        task_id=tid,
                        kind="sub_agent",
                        title=_truncate(task_spec["task"], 160),
                        input_text=task_spec["task"],
                        workspace=self._workspace,
                        metadata={
                            "model": _clean(task_spec.get("model")),
                            "provider": _clean(task_spec.get("provider")),
                        },
                    )
                    persist = True
                except (RuntimeError, OSError, sqlite3.Error, TypeError, ValueError) as e:
                    logger.debug("Failed to persist background sub-agent task %s: %s", tid, e)
            run = self._new_run(task_spec, task_id=tid, persist=persist)
            future = _background_pool.submit(self._run_subagent_task, run, task_spec)
            run.future = future
            runs.append(run)
            with _tasks_lock:
                _background_tasks[tid] = run

        ids_fmt = "\n".join(
            f"  - `{run.task_id}`{_format_run_model_tag(run)}: {run.task[:80]}" for run in runs
        )
        return (
            f"Dispatched {len(tasks)} background sub-agent(s):\n{ids_fmt}\n\n"
            f"Use `agent_result(task_id='<id>')` to inspect live status, visible text, "
            f"tool calls, and final results. "
            f"Use `agent_result()` with no arguments to list all tasks."
        )

    def cancel(self):
        with self._active_runs_lock:
            runs = list(self._active_runs.values())
            seen_ids = {run.task_id for run in runs}
        with _tasks_lock:
            runs.extend(
                run
                for run in _background_tasks.values()
                if run.status in {"queued", "running"} and run.task_id not in seen_ids
            )
        for run in runs:
            agent = run.agent
            if agent is not None:
                try:
                    agent.cancel()
                except (RuntimeError, OSError) as e:
                    logger.debug("Error cancelling sub-agent %s: %s", run.task_id, e)


def _clean(value) -> str:
    return str(value).strip() if value is not None else ""


def _truncate(text: object, max_chars: int) -> str:
    s = "" if text is None else str(text)
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 31] + "\n... (sub-agent output truncated)"


def _format_run_model_tag(run: SubAgentRun) -> str:
    return _format_model_tag(run.model, run.provider)


def _format_task_model_tag(task: dict) -> str:
    metadata = task.get("metadata") or {}
    model = _clean(metadata.get("model"))
    provider = _clean(metadata.get("provider"))
    return _format_model_tag(model, provider)


def _format_model_tag(model: str, provider: str) -> str:
    if model and provider:
        return f" [{provider}/{model}]"
    if model:
        return f" [{model}]"
    if provider:
        return f" [{provider}]"
    return ""


def _format_args(args: dict) -> str:
    if not args:
        return "{}"
    return _truncate(repr(args), 500)


def _format_run_report(run: SubAgentRun, *, total: int = 1, index: int = 0) -> str:
    snap = run.snapshot()
    index_tag = f" {index + 1}/{total}" if total > 1 else ""
    title = f"### Sub-agent{index_tag}{_format_run_model_tag(run)}"
    lines = [
        title,
        f"Task: {snap['task'][:200]}",
        f"Status: {snap['status']}",
    ]
    if snap["error"]:
        lines.append(f"Error: {snap['error']}")

    if snap["text"]:
        lines.extend(["", "Visible text output:", snap["text"]])

    if snap["events"]:
        lines.extend(["", "Tool activity:"])
        for event in snap["events"][-30:]:
            tool = event.get("tool", "tool")
            args = _format_args(event.get("args") or {})
            if event.get("type") == "tool_start":
                lines.append(f"- started `{tool}` with args {args}")
            elif event.get("type") == "tool_end":
                state = "succeeded" if event.get("success") else "failed"
                preview = event.get("result_preview") or ""
                lines.append(f"- {state} `{tool}` with args {args}")
                if preview:
                    lines.append(f"  result preview: {_truncate(preview, 500)}")
        if len(snap["events"]) > 30:
            lines.append(f"- ... ({len(snap['events']) - 30} earlier event(s) omitted)")

    if snap["result"]:
        lines.extend(["", "Final result:", snap["result"]])

    return _truncate("\n".join(lines), _MAX_REPORT_CHARS)


def _format_task_report(task: dict, events: list) -> str:
    metadata = task.get("metadata") or {}
    lines = [
        f"### Background task `{task['id']}`{_format_task_model_tag(task)}",
        f"Kind: {task['kind']}",
        f"Task: {task['input'][:200]}",
        f"Status: {task['status']}",
    ]
    if task.get("error"):
        lines.append(f"Error: {task['error']}")

    text = metadata.get("text")
    if text:
        lines.extend(["", "Visible text output:", str(text)])

    tool_events = [
        event for event in events if getattr(event, "event_type", "") in {"tool_start", "tool_end"}
    ]
    if tool_events:
        lines.extend(["", "Tool activity:"])
        for event in tool_events[-30:]:
            payload = event.payload
            tool = payload.get("tool", "tool")
            args = _format_args(payload.get("args") or {})
            if event.event_type == "tool_start":
                lines.append(f"- started `{tool}` with args {args}")
            elif event.event_type == "tool_end":
                state = "succeeded" if payload.get("success") else "failed"
                preview = payload.get("result_preview") or ""
                lines.append(f"- {state} `{tool}` with args {args}")
                if preview:
                    lines.append(f"  result preview: {_truncate(preview, 500)}")
        if len(tool_events) > 30:
            lines.append(f"- ... ({len(tool_events) - 30} earlier event(s) omitted)")

    evidence = task.get("evidence") or {}
    if evidence:
        lines.extend(["", f"Evidence: {_truncate(repr(evidence), 1000)}"])

    artifacts = task.get("artifacts") or []
    if artifacts:
        lines.extend(["", f"Artifacts: {_truncate(repr(artifacts), 1000)}"])

    if task.get("result"):
        lines.extend(["", "Final result:", task["result"]])

    return _truncate("\n".join(lines), _MAX_REPORT_CHARS)


# ---------------------------------------------------------------------------
# AgentResultTool - poll / collect background sub-agent results
# ---------------------------------------------------------------------------


class AgentResultTool(Tool):
    name = "agent_result"
    description = (
        "Check status and collect results of background sub-agents spawned "
        "via the agent tool with background=true. "
        "Call with no arguments to list all background tasks and their statuses. "
        "Call with a specific task_id to inspect live visible text, tool calls, "
        "and final results. Thinking content is never included."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by a previous background agent call. If omitted, lists all tasks.",  # noqa: E501
            },
        },
        "required": [],
    }
    capabilities = ToolCapabilities(
        capability="agent.read",
        read_only=True,
        supports_parallel=True,
    )

    def __init__(self, workspace: str = ".", task_store: TaskStore | None = None):
        super().__init__(workspace)
        self.task_store = task_store

    def execute(self, task_id: str | None = None, **kwargs: Any) -> str:
        if task_id:
            return self._query_one(task_id)
        return self._list_all()

    def _query_one(self, task_id: str) -> str:
        with _tasks_lock:
            run = _background_tasks.get(task_id)

        if run is None:
            if self.task_store:
                task = self.task_store.get_task(task_id)
                if task is not None and task.get("kind") == "sub_agent":
                    return _format_task_report(task, self.task_store.events(task_id))
            return f"No background task found with id '{task_id}'"

        future = run.future
        if future and future.done() and run.status in {"queued", "running"}:
            try:
                future.result()
            except (RuntimeError, ValueError, OSError, concurrent.futures.CancelledError) as e:
                run.fail(f"Sub-agent error: {e}")

        snap = run.snapshot()
        # Clean up completed tasks after they've been collected
        if snap["status"] in {"done", "error"}:
            with _tasks_lock:
                _background_tasks.pop(task_id, None)

        return _format_run_report(run)

    def _list_all(self) -> str:
        lines = []
        active_ids = set()
        with _tasks_lock:
            stale_ids = []
            for tid, run in sorted(_background_tasks.items()):
                active_ids.add(tid)
                future = run.future
                if future and future.done() and run.status in {"queued", "running"}:
                    try:
                        future.result()
                    except (
                        RuntimeError,
                        ValueError,
                        OSError,
                        concurrent.futures.CancelledError,
                    ) as e:
                        run.fail(f"Sub-agent error: {e}")
                snap = run.snapshot()
                lines.append(
                    f"  - `{tid}`: {snap['status']}{_format_run_model_tag(run)} - "
                    f"{snap['task'][:80]}"
                )
                # Mark completed/errored tasks for cleanup
                if snap["status"] in {"done", "error"}:
                    stale_ids.append(tid)
            # Auto-cleanup completed background tasks
            for tid in stale_ids:
                _background_tasks.pop(tid, None)

        if self.task_store:
            for task in self.task_store.list_tasks(kind="sub_agent", limit=50):
                tid = task["id"]
                if tid in active_ids:
                    continue
                lines.append(
                    f"  - `{tid}`: {task['status']}{_format_task_model_tag(task)} - "
                    f"{task['input'][:80]}"
                )

        if not lines:
            return "No background sub-agent tasks."

        return "Background sub-agent tasks:\n" + "\n".join(lines)
