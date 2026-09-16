"""Calendar screen queries (Phase 7).

For now the calendar just surfaces the app's own task deadlines, grouped by
day. A later phase will merge in iCal / Google Calendar events for the same
day/week ranges.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import db


def day_tasks(day: date) -> list[dict[str, Any]]:
    """Open tasks whose deadline falls on ``day``, earliest first."""
    return db.query(
        """
        SELECT * FROM tasks
        WHERE done < 1 AND deadline IS NOT NULL AND deadline::date = %s
        ORDER BY deadline
        """,
        (day,),
    )


def undated_tasks() -> list[dict[str, Any]]:
    """Open tasks with no deadline at all. Nothing here says which day they
    belong on, so the day view (app/menubar.py _build_calendar_day) only
    ever places them on today, at a random time after the current moment."""
    return db.query("SELECT * FROM tasks WHERE done < 1 AND deadline IS NULL")


def week_tasks(week_start_day: date) -> dict[date, list[dict[str, Any]]]:
    """Open tasks with a deadline in the 7 days starting ``week_start_day``,
    grouped by day (every day of the week is present, even with an empty
    list)."""
    week_end = week_start_day + timedelta(days=7)
    rows = db.query(
        """
        SELECT * FROM tasks
        WHERE done < 1 AND deadline IS NOT NULL
          AND deadline >= %s AND deadline < %s
        ORDER BY deadline
        """,
        (week_start_day, week_end),
    )
    by_day: dict[date, list[dict[str, Any]]] = {
        week_start_day + timedelta(days=i): [] for i in range(7)
    }
    for row in rows:
        by_day.setdefault(row["deadline"].date(), []).append(row)
    return by_day


def week_start(day: date) -> date:
    """The Monday on or before ``day``."""
    return day - timedelta(days=day.weekday())
