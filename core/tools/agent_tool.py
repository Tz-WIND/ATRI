"""Sub-agent spawning with isolated context.

Spawns independent agents with their own conversation history, context manager,
LLM instance, and tool access. Sub-agents' contexts are fully isolated from the
parent.

Two execution modes:
- blocking (default): parent waits for sub-agent to finish
- background: sub-agent runs in a background thread, parent gets a task_id
  immediately and can continue working. Use agent_result to check/poll later.

Parallel execution: pass multiple tasks via 'tasks' to run sub-agents concurrently.
"""

import concurrent.futures
import threading
import uuid

from core import logger
from .base import Tool


# ---------------------------------------------------------------------------
# Shared state for background tasks (class-level on AgentTool)
# ---------------------------------------------------------------------------
_background_tasks: dict[str, concurrent.futures.Future] = {}
_tasks_lock = threading.Lock()


# ---------------------------------------------------------------------------
# AgentTool
# ---------------------------------------------------------------------------

class AgentTool(Tool):
    name = "agent"
    description = (
        "Spawn one or more sub-agents to handle complex sub-tasks independently. "
        "Each sub-agent has its own isolated context, LLM instance, and tool access.\n"
        "\n"
        "**Blocking mode (default):** parent waits for sub-agent(s) to finish.\n"
        "Use 'tasks' (array) to run multiple sub-agents in parallel.\n"
        "\n"
        "**Model selection:** optionally pass 'model' and 'provider' to run all "
        "spawned sub-agents on a specific configured model. Use 'task_configs' "
        "to choose different models per sub-agent.\n"
        "\n"
        "**Background mode:** set 'background: true' to spawn sub-agents that "
        "run asynchronously. The tool returns a task_id immediately so you can "
        "continue working. Use the agent_result tool later to check status and "
        "collect results. This lets you dispatch work and do other things "
        "while sub-agents run."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A single task for one sub-agent.",
            },
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple tasks to run in parallel across independent sub-agents.",
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
                "description": "Multiple sub-agent task objects, each with optional model/provider overrides.",
            },
            "model": {
                "type": "string",
                "description": "Optional model to use for spawned sub-agent(s). Omit to inherit the current model.",
            },
            "provider": {
                "type": "string",
                "description": "Optional configured provider for the selected model. Use when the same model name exists on multiple providers.",
            },
            "background": {
                "type": "boolean",
                "description": "If true, run sub-agent(s) in the background and return immediately with task_id(s). Use agent_result tool to collect results.",
                "default": False,
            },
        },
        "required": [],
    }

    _parent_agent = None

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
        **kwargs,
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
                all_tasks.append({
                    "task": configured_task,
                    "model": item.get("model") or model,
                    "provider": item.get("provider") or provider,
                })

        if not all_tasks:
            return "Error: no task or tasks provided"

        if background:
            return self._run_background(all_tasks)

        if len(all_tasks) == 1:
            return self._run_single(all_tasks[0])

        return self._run_parallel_blocking(all_tasks)

    # ------------------------------------------------------------------
    # Blocking execution
    # ------------------------------------------------------------------

    def _run_single(self, task_spec: dict) -> str:
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        from core.agent.agent import Agent

        parent = self._parent_agent
        try:
            sub = Agent(
                llm=parent.create_child_llm(
                    model=task_spec.get("model"),
                    provider=task_spec.get("provider"),
                ),
                workspace=parent.workspace,
                tools=[t for t in parent.tools if t.name not in ("agent", "agent_result")],
                max_context_tokens=parent.context.max_tokens,
                max_rounds=20,
                extra_instructions=parent.extra_instructions,
                persona=parent.persona,
                skills_prompt=parent.skills_prompt,
            )
        except Exception as e:
            logger.warning("Sub-agent creation failed: %s", e)
            return f"Sub-agent setup error: {e}"

        try:
            result = sub.chat(task_spec["task"])
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return result
        except Exception as e:
            return f"Sub-agent error: {e}"

    def _run_parallel_blocking(self, tasks: list[dict]) -> str:
        max_workers = min(len(tasks), 5)
        results: dict[int, str] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._run_single, t): i for i, t in enumerate(tasks)}
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = f"Sub-agent error: {e}"

        parts = []
        for i, task_spec in enumerate(tasks):
            tag = f"Sub-agent {i+1}/{len(tasks)}"
            task = task_spec["task"]
            parts.append(
                f"### {tag}{_format_model_tag(task_spec)}: {task[:100]}\n{results[i]}"
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Background (non-blocking) execution
    # ------------------------------------------------------------------

    def _run_background(self, tasks: list[dict]) -> str:
        """Spawn sub-agent(s) in background threads; return task IDs immediately."""
        from core.agent.agent import Agent

        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        parent = self._parent_agent
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(tasks), 5)
        )

        task_ids = []
        for task_spec in tasks:
            tid = f"bg_{uuid.uuid4().hex[:8]}"
            task_ids.append(tid)

            try:
                sub = Agent(
                    llm=parent.create_child_llm(
                        model=task_spec.get("model"),
                        provider=task_spec.get("provider"),
                    ),
                    workspace=parent.workspace,
                    tools=[t for t in parent.tools if t.name not in ("agent", "agent_result")],
                    max_context_tokens=parent.context.max_tokens,
                    max_rounds=20,
                    extra_instructions=parent.extra_instructions,
                    persona=parent.persona,
                    skills_prompt=parent.skills_prompt,
                )
            except Exception as e:
                logger.warning("Background sub-agent creation failed: %s", e)
                with _tasks_lock:
                    _background_tasks[tid] = _completed_future(
                        f"Sub-agent setup error: {e}"
                    )
                continue

            future = pool.submit(self._run_subagent_thread, sub, task_spec["task"])
            with _tasks_lock:
                _background_tasks[tid] = future

        # Keep the pool alive; background futures own their running threads.
        ids_fmt = "\n".join(
            f"  - `{tid}`{_format_model_tag(t)}: {t['task'][:80]}"
            for tid, t in zip(task_ids, tasks)
        )
        return (
            f"Dispatched {len(tasks)} background sub-agent(s):\n{ids_fmt}\n\n"
            f"Use `agent_result(task_id='<id>')` to check status and collect results. "
            f"Use `agent_result()` with no arguments to list all tasks."
        )

    @staticmethod
    def _run_subagent_thread(sub_agent, task: str) -> str:
        """Entry point for a background sub-agent thread."""
        try:
            result = sub_agent.chat(task)
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return result
        except Exception as e:
            return f"Sub-agent error: {e}"


def _completed_future(result: str) -> concurrent.futures.Future:
    future: concurrent.futures.Future = concurrent.futures.Future()
    future.set_result(result)
    return future


def _format_model_tag(task_spec: dict) -> str:
    model = _clean(task_spec.get("model"))
    provider = _clean(task_spec.get("provider"))
    if model and provider:
        return f" [{provider}/{model}]"
    if model:
        return f" [{model}]"
    if provider:
        return f" [{provider}]"
    return ""


def _clean(value) -> str:
    return str(value).strip() if value is not None else ""


# ---------------------------------------------------------------------------
# AgentResultTool - poll / collect background sub-agent results
# ---------------------------------------------------------------------------

class AgentResultTool(Tool):
    name = "agent_result"
    description = (
        "Check status and collect results of background sub-agents spawned "
        "via the agent tool with background=true. "
        "Call with no arguments to list all background tasks and their statuses. "
        "Call with a specific task_id to get the result (blocks only if the task "
        "isn't done yet; it will wait)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by a previous background agent call. If omitted, lists all tasks.",
            },
        },
        "required": [],
    }

    def execute(self, task_id: str | None = None, **kwargs) -> str:
        if task_id:
            return self._query_one(task_id)
        return self._list_all()

    def _query_one(self, task_id: str) -> str:
        with _tasks_lock:
            future = _background_tasks.get(task_id)

        if future is None:
            return f"No background task found with id '{task_id}'"

        if not future.done():
            return (
                f"Task `{task_id}` is still running. "
                f"Check again later with `agent_result(task_id='{task_id}')`."
            )

        try:
            result = future.result()
            return f"Task `{task_id}` completed:\n{result}"
        except Exception as e:
            return f"Task `{task_id}` failed: {e}"

    def _list_all(self) -> str:
        with _tasks_lock:
            if not _background_tasks:
                return "No background sub-agent tasks."

            lines = []
            for tid, future in sorted(_background_tasks.items()):
                if future.done():
                    try:
                        future.result()
                        status = "done"
                    except Exception:
                        status = "error"
                else:
                    status = "running"
                lines.append(f"  - `{tid}`: {status}")

        return "Background sub-agent tasks:\n" + "\n".join(lines)
