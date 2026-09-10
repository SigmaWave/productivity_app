"""Task operations (Phase 2.2 + Phase 3).

A "task" is one row of the ``tasks`` table. Open tasks are ordered by a simple
priority score derived from urgency, importance and deadline proximity so the
menu bar can surface the most relevant few.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from . import db


def add_task(
    description: str,
    *,
    urgent: float | None = None,
    importance: float | None = None,
    cognitive_load: float | None = None,
    estimated_duration: float | None = None,
    deadline: date | datetime | None = None,
    manual: int = 1,
    recurring_id: int | None = None,
) -> dict[str, Any]:
    """Insert a task and return the created row."""
    if not description or not description.strip():
        raise ValueError("Task description must not be empty")
    row = db.execute(
        """
        INSERT INTO tasks (description, urgent, importance, cognitive_load,
                           estimated_duration, deadline, manual, recurring_id)
        VALUES (%(description)s, %(urgent)s, %(importance)s, %(cognitive_load)s,
                %(estimated_duration)s, %(deadline)s, %(manual)s, %(recurring_id)s)
        RETURNING *
        """,
        {
            "description": description.strip(),
            "urgent": urgent,
            "importance": importance,
            "cognitive_load": cognitive_load,
            "estimated_duration": estimated_duration,
            "deadline": deadline,
            "manual": manual,
            "recurring_id": recurring_id,
        },
    )
    assert row is not None
    return row


def _priority_sql() -> str:
    # Higher score = do sooner. Deadline within 7 days adds up to 1.0.
    return """
        COALESCE(urgent, 0.5) * 1.0
      + COALESCE(importance, 0.5) * 1.0
      + CASE
            WHEN deadline IS NULL THEN 0
            WHEN deadline <= now() THEN 1.5
            ELSE GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (deadline - now())) / 604800.0)
        END
    """


def open_tasks(limit: int | None = None) -> list[dict[str, Any]]:
    """Unfinished tasks, most important first."""
    sql = f"""
        SELECT *, ({_priority_sql()}) AS priority
        FROM tasks
        WHERE done < 1
        ORDER BY priority DESC, created_at ASC
    """
    if limit is not None:
        sql += " LIMIT %(limit)s"
    return db.query(sql, {"limit": limit})


def open_task_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM tasks WHERE done < 1")
    return int(row["n"]) if row else 0


def get_task(task_id: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM tasks WHERE id = %s", (task_id,))


def set_progress(task_id: int, done: float) -> dict[str, Any] | None:
    """Update completion. Sets finished_date when it reaches 1.0."""
    done = max(0.0, min(1.0, float(done)))
    return db.execute(
        """
        UPDATE tasks
        SET done = %(done)s,
            finished_date = CASE WHEN %(done)s >= 1 THEN CURRENT_DATE ELSE NULL END
        WHERE id = %(id)s
        RETURNING *
        """,
        {"done": done, "id": task_id},
    )


def complete_task(task_id: int) -> dict[str, Any] | None:
    return set_progress(task_id, 1.0)


def delete_task(task_id: int) -> None:
    db.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
