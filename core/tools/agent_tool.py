"""Sub-agent spawning with isolated context.

Spawns independent agents with their own conversation history, context manager,
and tool access. Sub-agents' contexts are fully isolated from the parent.

Two execution modes:
- blocking (default): parent waits for sub-agent to finish
- background: sub-agent runs in a background thread, parent gets a task_id
  immediately and can continue working. Use agent_result to check/poll later.

Parallel execution: pass multiple tasks via 'tasks' to run sub-agents concurrently.
"""

import concurrent.futures
import threading
import uuid

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
        "Each sub-agent has its own isolated context and tool access.\n"
        "\n"
        "**Blocking mode (default):** parent waits for sub-agent(s) to finish.\n"
        "Use 'tasks' (array) to run multiple sub-agents in parallel.\n"
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
        background: bool = False,
        **kwargs,
    ) -> str:
        # Collect task list
        all_tasks = []
        if tasks:
            all_tasks.extend(tasks)
        if task:
            all_tasks.append(task)

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

    def _run_single(self, task: str) -> str:
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        from core.agent.agent import Agent

        parent = self._parent_agent
        sub = Agent(
            llm=parent.llm,
            workspace=parent.workspace,
            tools=[t for t in parent.tools if t.name not in ("agent", "agent_result")],
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
            extra_instructions=parent.extra_instructions,
            persona=parent.persona,
            skills_prompt=parent.skills_prompt,
        )

        try:
            result = sub.chat(task)
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return result
        except Exception as e:
            return f"Sub-agent error: {e}"

    def _run_parallel_blocking(self, tasks: list[str]) -> str:
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
        for i, t in enumerate(tasks):
            tag = f"Sub-agent {i+1}/{len(tasks)}"
            parts.append(f"### {tag}: {t[:100]}\n{results[i]}")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Background (non-blocking) execution
    # ------------------------------------------------------------------

    def _run_background(self, tasks: list[str]) -> str:
        """Spawn sub-agent(s) in background threads; return task IDs immediately."""
        from core.agent.agent import Agent

        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        parent = self._parent_agent
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(tasks), 5)
        )

        task_ids = []
        for t in tasks:
            tid = f"bg_{uuid.uuid4().hex[:8]}"
            task_ids.append(tid)

            sub = Agent(
                llm=parent.llm,
                workspace=parent.workspace,
                tools=[t for t in parent.tools if t.name not in ("agent", "agent_result")],
                max_context_tokens=parent.context.max_tokens,
                max_rounds=20,
                extra_instructions=parent.extra_instructions,
                persona=parent.persona,
                skills_prompt=parent.skills_prompt,
            )

            future = pool.submit(self._run_subagent_thread, sub, t)
            with _tasks_lock:
                _background_tasks[tid] = future

        # Don't shut down the pool — threads are detached
        ids_fmt = "\n".join(f"  - `{tid}`: {t[:80]}" for tid, t in zip(task_ids, tasks))
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


# ---------------------------------------------------------------------------
# AgentResultTool — poll / collect background sub-agent results
# ---------------------------------------------------------------------------

class AgentResultTool(Tool):
    name = "agent_result"
    description = (
        "Check status and collect results of background sub-agents spawned "
        "via the agent tool with background=true. "
        "Call with no arguments to list all background tasks and their statuses. "
        "Call with a specific task_id to get the result (blocks only if the task "
        "isn't done yet — it will wait)."
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
