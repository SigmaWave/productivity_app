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


def _priority_sql(prefix: str = "") -> str:
    # Higher score = do sooner. Deadline within 7 days adds up to 1.0.
    # `prefix` (e.g. "t.") qualifies the column names for queries that join
    # in another table sharing them, like recurring_tasks in open_tasks_full.
    return f"""
        COALESCE({prefix}urgent, 0.5) * 1.0
      + COALESCE({prefix}importance, 0.5) * 1.0
      + CASE
            WHEN {prefix}deadline IS NULL THEN 0
            WHEN {prefix}deadline <= now() THEN 1.5
            ELSE GREATEST(0, 1.0 - EXTRACT(EPOCH FROM ({prefix}deadline - now())) / 604800.0)
        END
    """


# tasks with no sort_order (nothing has ever been dragged, or it's new since
# the last drag) fall back to the computed priority score; once anything has
# a sort_order it's placed by that instead, ahead of the priority-sorted rest
_ORDER_BY = """
    ORDER BY ({pfx}pinned_at IS NOT NULL) DESC,
             ({pfx}sort_order IS NULL) ASC, {pfx}sort_order ASC,
             priority DESC, {pfx}created_at ASC
"""


def open_tasks(limit: int | None = None) -> list[dict[str, Any]]:
    """Unfinished tasks, most important first. A task pinned via
    :func:`set_active_task` always sorts first; behind that, any tasks
    manually reordered via :func:`reorder_tasks` come next in that order,
    ahead of the rest sorted by the computed priority score."""
    sql = f"""
        SELECT *, ({_priority_sql()}) AS priority
        FROM tasks
        WHERE done < 1
        {_ORDER_BY.format(pfx="")}
    """
    if limit is not None:
        sql += " LIMIT %(limit)s"
    return db.query(sql, {"limit": limit})


def open_tasks_full() -> list[dict[str, Any]]:
    """Every unfinished task, most important first (same ordering as
    :func:`open_tasks`), each also carrying its recurrence rule as
    ``recurrence`` (``None`` for a one-off task) — for the full "tasks" list
    window, which shows every open task rather than just the visible few."""
    sql = f"""
        SELECT t.*, ({_priority_sql("t.")}) AS priority, r.recurrence AS recurrence
        FROM tasks t
        LEFT JOIN recurring_tasks r ON r.id = t.recurring_id
        WHERE t.done < 1
        {_ORDER_BY.format(pfx="t.")}
    """
    return db.query(sql)


def set_active_task(task_id: int) -> dict[str, Any] | None:
    """Pin one open task as the active task, unpinning whichever task (if
    any) held that spot before it."""
    db.execute(
        "UPDATE tasks SET pinned_at = NULL WHERE pinned_at IS NOT NULL AND id != %s",
        (task_id,),
    )
    return db.execute(
        "UPDATE tasks SET pinned_at = now() WHERE id = %s RETURNING *",
        (task_id,),
    )


def reorder_tasks(ordered_ids: list[int]) -> None:
    """Persist a manual order for open tasks — drag-to-reorder in the tasks
    list window. Assigns each id a spaced-out sort_order (10, 20, 30, …) so
    it sorts in exactly this sequence ahead of the computed priority score
    (see :data:`_ORDER_BY`); a task not in ``ordered_ids`` keeps whatever
    sort_order it already had."""
    if not ordered_ids:
        return
    with db.cursor() as cur:
        cur.executemany(
            "UPDATE tasks SET sort_order = %s WHERE id = %s",
            [(i * 10, task_id) for i, task_id in enumerate(ordered_ids, start=1)],
        )


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


def clear_open_tasks() -> int:
    """Delete every open task — clears the day's schedule. Only deletes rows
    in ``tasks``, never ``recurring_tasks``, so a repeating task's template
    survives and simply spawns a fresh instance next time it's due. Returns
    the number of tasks removed."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE done < 1")
        return cur.rowcount
