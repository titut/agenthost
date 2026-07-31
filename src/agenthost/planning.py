"""Task planning — structured multi-step plans with DAG-based dependencies.

Plans are created by the agent via ``plan(action="create")`` and stepped through
one call at a time via ``plan(action="next")``.  Each ``next`` is a focused
``agent.chat()`` invocation; results are persisted in the task state database
for injection into later step prompts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agenthost.logger import setup_logging

logger = setup_logging("agenthost.planning")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

VALID_STEP_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "skipped", "blocked"}
)


@dataclass
class TaskStep:
    """A single unit of work within a task plan."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    plan_id: str = ""
    step_number: int = 0
    description: str = ""
    assigned_toolbox: str | None = None
    status: str = "pending"
    result_summary: str | None = None
    result_artifact_id: str | None = None
    result_is_truncated: bool = False
    error_message: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: str | None = None
    completed_at: str | None = None

    # Not persisted directly — loaded via the junction table.
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
        depends_on: list[str] | None = None,
    ) -> "TaskStep":
        """Build a TaskStep from a database row dict."""
        return cls(
            id=row["id"],
            plan_id=row["plan_id"],
            step_number=row["step_number"],
            description=row["description"],
            assigned_toolbox=row.get("assigned_toolbox"),
            status=row.get("status", "pending"),
            result_summary=row.get("result_summary"),
            result_artifact_id=row.get("result_artifact_id"),
            result_is_truncated=bool(row.get("result_is_truncated", False)),
            error_message=row.get("error_message"),
            created_at=row.get("created_at", ""),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            depends_on=depends_on or [],
        )


@dataclass
class TaskPlan:
    """A directed acyclic graph of steps that decomposes a user's goal."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    thread_id: str = ""
    goal: str = ""
    status: str = "draft"
    steps: list[TaskStep] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None

    @classmethod
    def from_row(
        cls, row: dict[str, Any], steps: list[TaskStep] | None = None
    ) -> "TaskPlan":
        return cls(
            id=row["id"],
            thread_id=row["thread_id"],
            goal=row["goal"],
            status=row.get("status", "draft"),
            steps=steps or [],
            created_at=row.get("created_at", ""),
            completed_at=row.get("completed_at"),
        )

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def get_step(self, step_id: str) -> TaskStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def ready_steps(self) -> list[TaskStep]:
        """Return steps that are ready to execute (all deps completed)."""
        completed_ids = {s.id for s in self.steps if s.status == "completed"}
        ready: list[TaskStep] = []
        for s in self.steps:
            if s.status not in ("pending", "ready"):
                continue
            if not s.depends_on or all(d in completed_ids for d in s.depends_on):
                ready.append(s)
        return ready

    def blocked_steps(self) -> list[TaskStep]:
        """Return steps that are blocked (a dependency failed or was skipped)."""
        failed_or_skipped = {
            s.id for s in self.steps if s.status in ("failed", "skipped")
        }
        blocked: list[TaskStep] = []
        for s in self.steps:
            if s.status in ("completed", "running", "failed", "skipped", "blocked"):
                continue
            if s.depends_on and any(d in failed_or_skipped for d in s.depends_on):
                blocked.append(s)
        return blocked


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------


def _validate_dag(steps: list[dict[str, Any]]) -> str | None:
    """Return an error string if the step graph is invalid, or None if valid.

    Checks: no cycles, all dependency step numbers exist, no self-references.
    """
    step_numbers = {s["step_number"] for s in steps}

    for s in steps:
        deps = s.get("depends_on") or []
        for dep_num in deps:
            if dep_num not in step_numbers:
                return (
                    f"Step {s['step_number']} depends on step {dep_num}, "
                    f"but that step does not exist in the plan."
                )
            if dep_num == s["step_number"]:
                return f"Step {s['step_number']} cannot depend on itself."

    # Cycle detection via DFS
    adj: dict[int, list[int]] = {}
    for s in steps:
        num = s["step_number"]
        adj[num] = s.get("depends_on") or []

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {num: WHITE for num in step_numbers}

    def _has_cycle(node: int) -> bool:
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if color.get(neighbor) == GRAY:
                return True
            if color.get(neighbor) == WHITE and _has_cycle(neighbor):
                return True
        color[node] = BLACK
        return False

    for num in step_numbers:
        if color[num] == WHITE and _has_cycle(num):
            return "The plan contains a circular dependency."

    return None


# ---------------------------------------------------------------------------
# Task state store (SQLite CRUD)
# ---------------------------------------------------------------------------


class TaskStateStore:
    """CRUD operations for task plans and steps in the agent's SQLite database.

    Uses the same ``memory.db`` database and ``_connect()`` context-manager
    pattern as ``AgentMemory``.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Plan CRUD
    # ------------------------------------------------------------------

    def create_plan(self, plan: TaskPlan) -> TaskPlan:
        """Insert a new plan and its steps into the database."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO task_plans (id, thread_id, goal, status, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (plan.id, plan.thread_id, plan.goal, plan.status, plan.created_at),
            )
            for step in plan.steps:
                conn.execute(
                    """INSERT INTO task_steps
                       (id, plan_id, step_number, description, assigned_toolbox,
                        status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step.id,
                        plan.id,
                        step.step_number,
                        step.description,
                        step.assigned_toolbox,
                        step.status,
                        step.created_at,
                    ),
                )
                for dep_id in step.depends_on:
                    conn.execute(
                        """INSERT INTO task_step_dependencies (step_id, depends_on_step_id)
                           VALUES (?, ?)""",
                        (step.id, dep_id),
                    )
            conn.commit()
        logger.info("Created plan %s with %d steps", plan.id, len(plan.steps))
        return plan

    def get_plan(self, plan_id: str) -> TaskPlan | None:
        """Load a plan and all its steps from the database."""
        with self._connect() as conn:
            plan_row = conn.execute(
                "SELECT * FROM task_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if not plan_row:
                return None

            step_rows = conn.execute(
                "SELECT * FROM task_steps WHERE plan_id = ? ORDER BY step_number",
                (plan_id,),
            ).fetchall()

            steps: list[TaskStep] = []
            for srow in step_rows:
                dep_rows = conn.execute(
                    "SELECT depends_on_step_id FROM task_step_dependencies WHERE step_id = ?",
                    (srow["id"],),
                ).fetchall()
                depends_on = [r["depends_on_step_id"] for r in dep_rows]
                steps.append(TaskStep.from_row(dict(srow), depends_on=depends_on))

        return TaskPlan.from_row(dict(plan_row), steps=steps)

    def get_active_plan_for_thread(self, thread_id: str) -> TaskPlan | None:
        """Return the currently active plan for a thread, if any."""
        with self._connect() as conn:
            plan_row = conn.execute(
                """SELECT * FROM task_plans
                   WHERE thread_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (thread_id,),
            ).fetchone()
            if not plan_row:
                return None
        return self.get_plan(plan_row["id"])

    # ------------------------------------------------------------------
    # Step CRUD
    # ------------------------------------------------------------------

    def update_step_status(
        self,
        step_id: str,
        new_status: str,
        *,
        result_summary: str | None = None,
        result_artifact_id: str | None = None,
        result_is_truncated: bool = False,
        error_message: str | None = None,
    ) -> TaskStep | None:
        """Update a step's status and optionally its result/error fields."""
        if new_status not in VALID_STEP_STATUSES:
            raise ValueError(f"Invalid step status: {new_status!r}")

        with self._connect() as conn:
            updates = ["status = ?"]
            params: list[Any] = [new_status]

            if result_summary is not None:
                updates.append("result_summary = ?")
                params.append(result_summary)
            if result_artifact_id is not None:
                updates.append("result_artifact_id = ?")
                params.append(result_artifact_id)
            if new_status == "completed":
                updates.append("result_is_truncated = ?")
                params.append(int(result_is_truncated))
            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)
            if new_status == "running":
                updates.append("started_at = ?")
                params.append(datetime.now(timezone.utc).isoformat())
            if new_status in ("completed", "failed", "skipped"):
                updates.append("completed_at = ?")
                params.append(datetime.now(timezone.utc).isoformat())

            params.append(step_id)
            conn.execute(
                f"UPDATE task_steps SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            conn.commit()

        plan = self._get_plan_for_step(step_id)
        if plan is not None:
            for s in plan.steps:
                if s.id == step_id:
                    return s
        return None

    def _get_plan_for_step(self, step_id: str) -> TaskPlan | None:
        """Return the plan that contains *step_id*."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT plan_id FROM task_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if not row:
                return None
            return self.get_plan(row["plan_id"])

    def delete_plan(self, plan_id: str) -> bool:
        """Delete a plan and all its steps (cascading via FK)."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM task_plans WHERE id = ?", (plan_id,))
            conn.commit()
            return cursor.rowcount > 0
