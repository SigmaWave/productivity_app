"""Recurring tasks (Phase 4).

A recurring task is a template row in ``recurring_tasks``. Whenever the app
starts and once a day thereafter, :func:`generate_due_tasks` walks the active
templates and creates a concrete ``tasks`` row for any that are due today and
have not already been generated today.

``recurrence`` grammar
----------------------
    daily                every day
    weekdays             Monday-Friday
    weekly               every 7 days from creation / last generation
    weekly:0,3           on those ISO weekdays (0 = Monday .. 6 = Sunday)
    monthly              on the same day-of-month as it was created
    monthly:1            on the 1st of every month
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import db, tasks


def add_recurring(
    description: str,
    recurrence: str,
    *,
    urgent: float | None = None,
    importance: float | None = None,
    cognitive_load: float | None = None,
    estimated_duration: float | None = None,
) -> dict[str, Any]:
    if not description or not description.strip():
        raise ValueError("Description must not be empty")
    recurrence = recurrence.strip().lower()
    _validate_recurrence(recurrence)
    row = db.execute(
        """
        INSERT INTO recurring_tasks (description, recurrence, urgent, importance,
                                     cognitive_load, estimated_duration)
        VALUES (%(description)s, %(recurrence)s, %(urgent)s, %(importance)s,
                %(cognitive_load)s, %(estimated_duration)s)
        RETURNING *
        """,
        {
            "description": description.strip(),
            "recurrence": recurrence,
            "urgent": urgent,
            "importance": importance,
            "cognitive_load": cognitive_load,
            "estimated_duration": estimated_duration,
        },
    )
    assert row is not None
    return row


def list_recurring(active_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM recurring_tasks"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY created_at"
    return db.query(sql)


def set_active(recurring_id: int, active: bool) -> None:
    db.execute(
        "UPDATE recurring_tasks SET active = %s WHERE id = %s",
        (1 if active else 0, recurring_id),
    )


def delete_recurring(recurring_id: int) -> None:
    db.execute("DELETE FROM recurring_tasks WHERE id = %s", (recurring_id,))


# --- scheduling ------------------------------------------------------------

_VALID_KINDS = {"daily", "weekdays", "weekly", "monthly"}


def _validate_recurrence(recurrence: str) -> None:
    kind, _, arg = recurrence.partition(":")
    if kind not in _VALID_KINDS:
        raise ValueError(f"Unknown recurrence kind: {kind!r}")
    if arg:
        try:
            values = [int(x) for x in arg.split(",") if x != ""]
        except ValueError:
            raise ValueError(f"Bad recurrence argument: {arg!r}") from None
        if kind in ("weekly", "weekdays") and not all(0 <= v <= 6 for v in values):
            raise ValueError("Weekday values must be 0-6 (0 = Monday)")
        if kind == "monthly" and not all(1 <= v <= 31 for v in values):
            raise ValueError("Day-of-month values must be 1-31")


def is_due(template: dict[str, Any], on: date) -> bool:
    """Whether ``template`` should spawn a task on the date ``on``."""
    if template.get("active", 1) != 1:
        return False
    if template.get("last_generated") == on:
        return False

    recurrence: str = template["recurrence"]
    kind, _, arg = recurrence.partition(":")
    args = [int(x) for x in arg.split(",")] if arg else []
    anchor: date = (template.get("created_at") or on)
    if hasattr(anchor, "date"):
        anchor = anchor.date()

    if kind == "daily":
        return True
    if kind == "weekdays":
        return on.weekday() < 5
    if kind == "weekly":
        if args:
            return on.weekday() in args
        last = template.get("last_generated")
        return last is None or (on - last).days >= 7
    if kind == "monthly":
        target_days = args or [anchor.day]
        return on.day in target_days
    return False


def generate_due_tasks(on: date | None = None) -> list[dict[str, Any]]:
    """Create concrete tasks for every due template. Returns the new tasks."""
    on = on or date.today()
    created: list[dict[str, Any]] = []
    for template in list_recurring(active_only=True):
        if not is_due(template, on):
            continue
        task = tasks.add_task(
            template["description"],
            urgent=template["urgent"],
            importance=template["importance"],
            cognitive_load=template["cognitive_load"],
            estimated_duration=template["estimated_duration"],
            manual=0,
            recurring_id=template["id"],
        )
        db.execute(
            "UPDATE recurring_tasks SET last_generated = %s WHERE id = %s",
            (on, template["id"]),
        )
        created.append(task)
    return created
