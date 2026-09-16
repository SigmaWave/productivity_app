#!/usr/bin/env python3
"""Helpers for inspecting the productivity database.

Use as a library::

    from scripts.db_helpers import overview, show_tasks, task_stats
    overview()
    show_tasks("open")

...or from the shell::

    python -m scripts.db_helpers overview
    python -m scripts.db_helpers tasks --status open
    python -m scripts.db_helpers tasks --status all --limit 50
    python -m scripts.db_helpers recurring
    python -m scripts.db_helpers sessions --days 14
    python -m scripts.db_helpers typing
    python -m scripts.db_helpers deadlines
    python -m scripts.db_helpers mouse --minutes 60
    python -m scripts.db_helpers stats
    python -m scripts.db_helpers sql "SELECT * FROM tasks WHERE urgent > 0.7"

Every function prints a table and also returns the rows (list[dict]) so results
can be reused in a REPL or notebook.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

# Allow "python scripts/db_helpers.py ..." as well as "-m scripts.db_helpers".
if __package__ in (None, ""):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

Rows = list[dict[str, Any]]


# --------------------------------------------------------------------------
# pretty printing
# --------------------------------------------------------------------------

def print_table(rows: Sequence[dict[str, Any]], *, title: str | None = None,
                columns: Sequence[str] | None = None, max_width: int = 48) -> None:
    if title:
        print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return

    cols = list(columns) if columns else list(rows[0].keys())

    def fmt(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            text = f"{value:.3f}".rstrip("0").rstrip(".")
        else:
            text = str(value)
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    widths = {c: len(c) for c in cols}
    cells = []
    for row in rows:
        formatted = {c: fmt(row.get(c)) for c in cols}
        cells.append(formatted)
        for c in cols:
            widths[c] = max(widths[c], len(formatted[c]))

    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for formatted in cells:
        print("  ".join(formatted[c].ljust(widths[c]) for c in cols))
    print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


# --------------------------------------------------------------------------
# inspection helpers
# --------------------------------------------------------------------------

def overview() -> Rows:
    """Row counts for every table plus a couple of quick totals."""
    rows = db.query(
        """
        SELECT 'tasks'           AS table, COUNT(*) AS rows FROM tasks
        UNION ALL SELECT 'tasks (open)',   COUNT(*) FROM tasks WHERE done < 1
        UNION ALL SELECT 'tasks (done)',   COUNT(*) FROM tasks WHERE done >= 1
        UNION ALL SELECT 'recurring_tasks', COUNT(*) FROM recurring_tasks
        UNION ALL SELECT 'recurring (active)', COUNT(*) FROM recurring_tasks WHERE active = 1
        UNION ALL SELECT 'session',        COUNT(*) FROM session
        UNION ALL SELECT 'typing',         COUNT(*) FROM typing
        UNION ALL SELECT 'schedule',       COUNT(*) FROM schedule
        UNION ALL SELECT 'keystroke',      COUNT(*) FROM keystroke
        UNION ALL SELECT 'keystroke (1h)', COUNT(*) FROM keystroke WHERE ts >= now() - interval '1 hour'
        UNION ALL SELECT 'mouse_event',      COUNT(*) FROM mouse_event
        UNION ALL SELECT 'mouse_event (1h)', COUNT(*) FROM mouse_event WHERE ts >= now() - interval '1 hour'
        """
    )
    print_table(rows, title="Overview", columns=["table", "rows"])
    return rows


def show_tasks(status: str = "open", limit: int = 20) -> Rows:
    """List tasks. ``status`` is one of open | done | all."""
    where = {"open": "WHERE done < 1", "done": "WHERE done >= 1", "all": ""}[status]
    rows = db.query(
        f"""
        SELECT id, description, urgent, importance, cognitive_load, done,
               deadline, estimated_duration AS mins, manual, recurring_id,
               finished_date
        FROM tasks
        {where}
        ORDER BY done < 1 DESC, deadline NULLS LAST, id
        LIMIT %s
        """,
        (limit,),
    )
    print_table(rows, title=f"Tasks ({status})")
    return rows


def show_recurring() -> Rows:
    rows = db.query(
        """
        SELECT r.id, r.description, r.recurrence, r.active, r.last_generated,
               r.urgent, r.importance, r.cognitive_load,
               r.estimated_duration AS mins,
               COUNT(t.id) AS spawned
        FROM recurring_tasks r
        LEFT JOIN tasks t ON t.recurring_id = r.id
        GROUP BY r.id
        ORDER BY r.id
        """
    )
    print_table(rows, title="Recurring tasks")
    return rows


def show_sessions(days: int = 7) -> Rows:
    rows = db.query(
        """
        SELECT id, start, duration AS minutes
        FROM session
        WHERE start >= CURRENT_DATE - %s::int
        ORDER BY start DESC
        """,
        (days,),
    )
    print_table(rows, title=f"Pomodoro sessions (last {days}d)")
    return rows


def show_typing(days: int = 7) -> Rows:
    rows = db.query(
        """
        SELECT id, date, time, accuracy, ats, cognitive_energy_approx
        FROM typing
        WHERE date >= CURRENT_DATE - %s::int
        ORDER BY date DESC
        """,
        (days,),
    )
    print_table(rows, title=f"Typing samples (last {days}d)")
    return rows


def show_keystrokes(minutes: int = 60) -> Rows:
    """Per-minute keystroke counts by kind over the last `minutes` minutes."""
    rows = db.query(
        """
        SELECT date_trunc('minute', ts) AS minute,
               COUNT(*) FILTER (WHERE kind = 'char')   AS chars,
               COUNT(*) FILTER (WHERE kind = 'delete') AS deletes,
               COUNT(*) FILTER (WHERE kind = 'other')  AS other,
               COUNT(*)                                AS total
        FROM keystroke
        WHERE ts >= now() - make_interval(mins => %s)
        GROUP BY minute
        ORDER BY minute DESC
        """,
        (minutes,),
    )
    print_table(rows, title=f"Keystrokes per minute (last {minutes} min)")
    return rows


def show_mouse(minutes: int = 60) -> Rows:
    """Per-minute mouse move/click counts over the last `minutes` minutes."""
    rows = db.query(
        """
        SELECT date_trunc('minute', ts) AS minute,
               COUNT(*) FILTER (WHERE kind = 'move')  AS moves,
               COUNT(*) FILTER (WHERE kind = 'click') AS clicks,
               COUNT(*)                               AS total
        FROM mouse_event
        WHERE ts >= now() - make_interval(mins => %s)
        GROUP BY minute
        ORDER BY minute DESC
        """,
        (minutes,),
    )
    print_table(rows, title=f"Mouse activity per minute (last {minutes} min)")
    return rows


def typing_speed(minutes: int = 60) -> Rows:
    """cpm (chars/min, deletes excluded) and inter-key gap stats over a window."""
    rows = db.query(
        """
        WITH g AS (
            SELECT kind,
                   extract(epoch from (ts - lag(ts) over (order by ts))) * 1000 AS gap_ms
            FROM keystroke
            WHERE ts >= now() - make_interval(mins => %(m)s)
        )
        SELECT
            COUNT(*) FILTER (WHERE kind = 'char')::float / %(m)s AS cpm,
            COUNT(*) FILTER (WHERE kind = 'char')                AS chars,
            COUNT(*) FILTER (WHERE kind = 'delete')              AS deletes,
            COUNT(*) FILTER (WHERE kind = 'other')               AS other,
            ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_ms)::numeric, 0) AS median_gap_ms,
            ROUND(AVG(gap_ms)::numeric, 0)                       AS mean_gap_ms
        FROM g
        """,
        {"m": minutes},
    )
    print_table(rows, title=f"Typing speed (last {minutes} min)")
    return rows


def upcoming_deadlines(days: int = 14) -> Rows:
    rows = db.query(
        """
        SELECT id, description, deadline,
               deadline::date - CURRENT_DATE AS days_left,
               done
        FROM tasks
        WHERE done < 1 AND deadline IS NOT NULL
          AND deadline <= (CURRENT_DATE + %s::int)::timestamptz
        ORDER BY deadline
        """,
        (days,),
    )
    print_table(rows, title=f"Deadlines within {days}d")
    return rows


def recent_completions(days: int = 7) -> Rows:
    rows = db.query(
        """
        SELECT id, description, finished_date, estimated_duration AS mins, manual
        FROM tasks
        WHERE done >= 1 AND finished_date >= CURRENT_DATE - %s::int
        ORDER BY finished_date DESC
        """,
        (days,),
    )
    print_table(rows, title=f"Completed in last {days}d")
    return rows


def task_stats() -> Rows:
    rows = db.query(
        """
        SELECT
            COUNT(*)                                    AS total,
            COUNT(*) FILTER (WHERE done >= 1)           AS completed,
            COUNT(*) FILTER (WHERE done < 1)            AS open,
            ROUND(AVG(done)::numeric, 3)                AS avg_progress,
            ROUND(AVG(urgent)::numeric, 3)             AS avg_urgent,
            ROUND(AVG(importance)::numeric, 3)         AS avg_importance,
            ROUND(AVG(cognitive_load)::numeric, 3)     AS avg_load,
            ROUND(AVG(estimated_duration)::numeric, 1) AS avg_mins,
            COUNT(*) FILTER (WHERE manual = 1)          AS manual,
            COUNT(*) FILTER (WHERE manual = 0)          AS auto
        FROM tasks
        """
    )
    print_table(rows, title="Task stats")
    return rows


def session_stats() -> Rows:
    rows = db.query(
        """
        SELECT
            COUNT(*)                                          AS sessions,
            COUNT(*) FILTER (WHERE start::date = CURRENT_DATE) AS today,
            COUNT(*) FILTER (WHERE start >= CURRENT_DATE - 7)  AS last_7d,
            ROUND(COALESCE(SUM(duration), 0)::numeric, 1)      AS total_min,
            ROUND(COALESCE(SUM(duration) FILTER (WHERE start::date = CURRENT_DATE), 0)::numeric, 1) AS today_min,
            ROUND(AVG(duration)::numeric, 1)                   AS avg_min
        FROM session
        """
    )
    print_table(rows, title="Session stats")
    return rows


def stats() -> None:
    task_stats()
    session_stats()


def sql(statement: str) -> Rows:
    """Run an arbitrary read-only statement and print the result."""
    if not statement.lstrip().lower().startswith(("select", "with", "table", "explain")):
        raise ValueError("Only SELECT / WITH / TABLE / EXPLAIN statements are allowed")
    rows = db.query(statement)
    print_table(rows, title="Query")
    return rows


def schema(table: str | None = None) -> Rows:
    """Column definitions for one table, or all of them."""
    rows = db.query(
        """
        SELECT table_name, ordinal_position AS pos, column_name,
               data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (%(table)s::text IS NULL OR table_name = %(table)s::text)
        ORDER BY table_name, ordinal_position
        """,
        {"table": table},
    )
    print_table(rows, title="Schema" + (f" ({table})" if table else ""))
    return rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("overview", help="row counts for every table")
    sub.add_parser("stats", help="task + session statistics")
    sub.add_parser("deadlines", help="open tasks with a near deadline").add_argument(
        "--days", type=int, default=14
    )
    sub.add_parser("done", help="recently completed tasks").add_argument(
        "--days", type=int, default=7
    )

    t = sub.add_parser("tasks", help="list tasks")
    t.add_argument("--status", choices=["open", "done", "all"], default="open")
    t.add_argument("--limit", type=int, default=20)

    sub.add_parser("recurring", help="recurring task templates")

    s = sub.add_parser("sessions", help="pomodoro sessions")
    s.add_argument("--days", type=int, default=7)

    ty = sub.add_parser("typing", help="typing samples")
    ty.add_argument("--days", type=int, default=7)

    ks = sub.add_parser("keystrokes", help="per-minute keystroke counts by kind")
    ks.add_argument("--minutes", type=int, default=60)

    mo = sub.add_parser("mouse", help="per-minute mouse move/click counts")
    mo.add_argument("--minutes", type=int, default=60)

    tsp = sub.add_parser("speed", help="cpm + inter-key gap stats")
    tsp.add_argument("--minutes", type=int, default=60)

    sc = sub.add_parser("schema", help="column definitions")
    sc.add_argument("table", nargs="?")

    q = sub.add_parser("sql", help="run a read-only SELECT")
    q.add_argument("statement")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not db.ping():
        print("Database unreachable — is `docker compose up` running?", file=sys.stderr)
        return 1

    match args.command:
        case "overview":
            overview()
        case "stats":
            stats()
        case "deadlines":
            upcoming_deadlines(args.days)
        case "done":
            recent_completions(args.days)
        case "tasks":
            show_tasks(args.status, args.limit)
        case "recurring":
            show_recurring()
        case "sessions":
            show_sessions(args.days)
        case "typing":
            show_typing(args.days)
        case "keystrokes":
            show_keystrokes(args.minutes)
        case "mouse":
            show_mouse(args.minutes)
        case "speed":
            typing_speed(args.minutes)
        case "schema":
            schema(args.table)
        case "sql":
            sql(args.statement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
