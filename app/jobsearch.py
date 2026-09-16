"""Job-search application tracking (not one of the numbered phases in
README.md — an ad-hoc feature; "Phase 7" there is reserved for the scheduler).

Each click of the "+" counter on the Job Search screen logs one row: when the
manually-started stopwatch began (``started_at``, NULL if it was never
started), when the counter was clicked (``ts``), and how long the stopwatch
had been running (``elapsed_seconds``). The duration is measured by menubar.py
with ``time.monotonic()`` and passed in directly rather than recomputed here
as ``ts - started_at`` — ``started_at`` comes from the host's wall clock and
``ts`` from Postgres's, and those two clocks drifting apart (e.g. the Docker
Desktop VM's clock lagging after the host sleeps) previously showed up as a
negative "time taken". ``timer_seconds`` / ``diff_seconds`` are reserved for a
possible future preset-timer feature and stay NULL for now.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import db


def log_application(started_at: datetime | None, elapsed_seconds: float | None) -> dict[str, Any]:
    """Record one job application and return the row. ``started_at`` is when
    the stopwatch for this attempt began (None if it was never started);
    ``elapsed_seconds`` is how long it had been running, measured with
    ``time.monotonic()`` so wall-clock skew can't throw it off."""
    row = db.execute(
        "INSERT INTO job_applications (started_at, ts, elapsed_seconds) "
        "VALUES (%s, now(), %s) RETURNING *",
        (started_at, elapsed_seconds),
    )
    assert row is not None
    return row


def count() -> int:
    """All-time total, regardless of when each application was logged."""
    row = db.query_one("SELECT COUNT(*) AS n FROM job_applications")
    return int(row["n"]) if row else 0


def count_today() -> int:
    """The "jobs applied today" counter on the Job Search screen."""
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM job_applications WHERE ts::date = CURRENT_DATE"
    )
    return int(row["n"]) if row else 0


def remove_last_application() -> bool:
    """Undo the most recent "+" click *from today* by deleting that logged
    application — matches the "jobs applied today" counter the "-" sits next
    to, so it never reaches back into a previous day. Returns False (no-op)
    if there were none today to remove."""
    row = db.query_one(
        "SELECT id FROM job_applications WHERE ts::date = CURRENT_DATE "
        "ORDER BY ts DESC LIMIT 1"
    )
    if row is None:
        return False
    db.execute("DELETE FROM job_applications WHERE id = %s", (row["id"],))
    return True


def list_applications() -> list[dict[str, Any]]:
    """Every logged application, most recent first, each with its elapsed
    minutes (``minutes`` is NULL when the stopwatch wasn't running for that
    one)."""
    return db.query(
        """
        SELECT id, started_at, ts AS finished_at, elapsed_seconds / 60.0 AS minutes
        FROM job_applications
        ORDER BY ts DESC
        """
    )


# -- LinkedIn outreach counter -----------------------------------------
#
# A second, simpler "+ / - " counter next to "jobs applied" on the Job
# Search screen. Unlike job applications it isn't tied to the stopwatch —
# just a per-day tally of outreach messages sent (connection requests,
# InMails, comments, etc.), so each row is just a timestamp.

def log_outreach() -> dict[str, Any]:
    row = db.execute(
        "INSERT INTO linkedin_outreach (ts) VALUES (now()) RETURNING *"
    )
    assert row is not None
    return row


def outreach_count_today() -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM linkedin_outreach WHERE ts::date = CURRENT_DATE"
    )
    return int(row["n"]) if row else 0


def remove_last_outreach() -> bool:
    """Undo the most recent outreach "+" click from today. Returns False
    (no-op) if there were none today to remove."""
    row = db.query_one(
        "SELECT id FROM linkedin_outreach WHERE ts::date = CURRENT_DATE "
        "ORDER BY ts DESC LIMIT 1"
    )
    if row is None:
        return False
    db.execute("DELETE FROM linkedin_outreach WHERE id = %s", (row["id"],))
    return True
