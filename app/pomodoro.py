"""Pomodoro session tracking.

Timing lives entirely in memory (driven by the menu bar's 1s timer); only
completed work sessions are persisted, to the ``session`` table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import config, db


@dataclass
class PomodoroState:
    running: bool = False
    on_break: bool = False
    started_monotonic: float = 0.0
    ends_monotonic: float = 0.0
    started_at: datetime | None = None

    def remaining_seconds(self) -> int:
        if not self.running:
            return 0
        return max(0, int(round(self.ends_monotonic - time.monotonic())))

    def is_finished(self) -> bool:
        return self.running and time.monotonic() >= self.ends_monotonic


def start_work(state: PomodoroState) -> PomodoroState:
    now = time.monotonic()
    work_min, _ = config.pomodoro_durations()
    state.running = True
    state.on_break = False
    state.started_monotonic = now
    state.ends_monotonic = now + work_min * 60
    state.started_at = datetime.now(timezone.utc)
    return state


def start_break(state: PomodoroState) -> PomodoroState:
    now = time.monotonic()
    _, break_min = config.pomodoro_durations()
    state.running = True
    state.on_break = True
    state.started_monotonic = now
    state.ends_monotonic = now + break_min * 60
    state.started_at = None
    return state


def stop(state: PomodoroState, *, log: bool = True) -> float:
    """Stop the current interval. If it was a work interval, log it.

    Returns the elapsed minutes of the interval that was stopped.
    """
    elapsed_min = 0.0
    if state.running:
        elapsed_min = (time.monotonic() - state.started_monotonic) / 60
        if log and not state.on_break and state.started_at is not None:
            _log_session(state.started_at, elapsed_min)
    state.running = False
    state.on_break = False
    state.started_at = None
    return elapsed_min


def _log_session(started_at: datetime, duration_min: float) -> None:
    db.execute(
        "INSERT INTO session (start, duration) VALUES (%s, %s)",
        (started_at, duration_min),
    )


def sessions_today() -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM session WHERE start::date = CURRENT_DATE"
    )
    return int(row["n"]) if row else 0
