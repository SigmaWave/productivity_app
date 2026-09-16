"""Mouse activity tracking — global movement + click monitoring.

Mirrors app/keys.py's shape: global NSEvent monitors buffer samples in RAM and
the controller's timer calls :meth:`MouseTracker.flush` to bulk-insert into
the ``mouse_event`` table every 60s. Mouse-moved events fire far more often
than keystrokes, so movement is sampled at most once every ``MOVE_SAMPLE_MS``
instead of recording every raw event — otherwise a minute of wiggling the
mouse would write tens of thousands of rows.

Global mouse monitoring needs the same macOS Accessibility permission as
keystroke tracking; :func:`app.keys.accessibility_trusted` is reused as-is.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from AppKit import (
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskMouseMoved,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
)

from . import db
from .keys import accessibility_trusted

_BUFFER_CAP = 200_000
MOVE_SAMPLE_MS = 100          # minimum gap between recorded "move" rows

# Window (minutes) the stats screen looks back over by default.
STATS_WINDOW_MINUTES = 60

# Positions plotted on the mouse map, most recent first, capped so the chart
# stays responsive after long tracking sessions.
MAP_POINTS_CAP = 4000

_CLICK_MASK = (
    NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown | NSEventMaskOtherMouseDown
)
_BUTTON_NAMES = {0: "left", 1: "right"}


class MouseTracker:
    def __init__(self) -> None:
        self._buffer: list[tuple[datetime, str, int, int, str | None]] = []
        self._move_monitor = None
        self._click_monitor = None
        self.permitted = False
        self.total_flushed = 0
        self._last_move_ts = 0.0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.permitted = accessibility_trusted()
        self._move_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskMouseMoved, self._on_move
        )
        self._click_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _CLICK_MASK, self._on_click
        )
        if not self.permitted:
            print("[mouse] mouse tracking needs permission — System Settings ▸ "
                  "Privacy & Security ▸ Accessibility ▸ enable your terminal, then "
                  "restart the app")

    def stop(self) -> None:
        if self._move_monitor is not None:
            NSEvent.removeMonitor_(self._move_monitor)
            self._move_monitor = None
        if self._click_monitor is not None:
            NSEvent.removeMonitor_(self._click_monitor)
            self._click_monitor = None

    # -- capture -------------------------------------------------------

    def _append(self, kind: str, button: str | None) -> None:
        loc = NSEvent.mouseLocation()
        self._buffer.append(
            (datetime.now(timezone.utc), kind, int(loc.x), int(loc.y), button)
        )
        if len(self._buffer) > _BUFFER_CAP:
            del self._buffer[: len(self._buffer) - _BUFFER_CAP]

    def _on_move(self, event) -> None:
        now = time.monotonic()
        if (now - self._last_move_ts) * 1000 < MOVE_SAMPLE_MS:
            return
        self._last_move_ts = now
        self._append("move", None)

    def _on_click(self, event) -> None:
        self._append("click", _BUTTON_NAMES.get(event.buttonNumber(), "other"))

    # -- flush --------------------------------------------------------

    def flush(self) -> int:
        """Write the RAM buffer to Postgres. Returns the row count written."""
        pending, self._buffer = self._buffer, []
        n = len(pending)
        if n:
            try:
                with db.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO mouse_event (ts, kind, x, y, button) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        pending,
                    )
                self.total_flushed += n
            except Exception as exc:  # noqa: BLE001 - keep the buffer, retry next tick
                self._buffer = pending + self._buffer
                print(f"[mouse] flush failed, {len(self._buffer)} buffered: {exc}")
                return 0
        print(f"database updated ({n} mouse events)")
        return n

    def pending_count(self) -> int:
        return len(self._buffer)


# --------------------------------------------------------------------------
# stats queries (read straight from Postgres — flush() first for freshness)
# --------------------------------------------------------------------------

def activity_series(
    minutes: int = STATS_WINDOW_MINUTES, bucket_minutes: int = 1
) -> tuple[list[int], list[int]]:
    """Per-bucket (move samples, clicks) counts, oldest first, zero-filled —
    same bucketing shape as ``keys.cpm_series``."""
    buckets = max(1, minutes // bucket_minutes)
    rows = db.query(
        """
        SELECT floor(extract(epoch from (now() - ts)) / 60 / %s)::int AS ago,
               kind, COUNT(*) AS n
        FROM mouse_event
        WHERE ts >= now() - make_interval(mins => %s)
        GROUP BY ago, kind
        """,
        (bucket_minutes, minutes),
    )
    moves: dict[int, int] = {}
    clicks: dict[int, int] = {}
    for r in rows:
        (moves if r["kind"] == "move" else clicks)[int(r["ago"])] = int(r["n"])
    move_series = [moves.get(buckets - 1 - i, 0) for i in range(buckets)]
    click_series = [clicks.get(buckets - 1 - i, 0) for i in range(buckets)]
    return move_series, click_series


def click_count(within_minutes: int = STATS_WINDOW_MINUTES) -> int:
    row = db.query_one(
        """
        SELECT COUNT(*)::int AS n FROM mouse_event
        WHERE kind = 'click' AND ts >= now() - make_interval(mins => %s)
        """,
        (within_minutes,),
    )
    return row["n"] if row else 0


def points(
    within_minutes: int = STATS_WINDOW_MINUTES, limit: int = MAP_POINTS_CAP
) -> list[dict]:
    """Recent (x, y, kind) samples in the window, for the spatial mouse map —
    capped at ``limit`` (most recent first) so a long tracking session doesn't
    make the plot sluggish."""
    return db.query(
        """
        SELECT x, y, kind FROM mouse_event
        WHERE ts >= now() - make_interval(mins => %s)
        ORDER BY ts DESC
        LIMIT %s
        """,
        (within_minutes, limit),
    )


def counts(within_minutes: int = 60) -> dict[str, int]:
    rows = db.query(
        """
        SELECT kind, COUNT(*) AS n
        FROM mouse_event
        WHERE ts >= now() - make_interval(mins => %s)
        GROUP BY kind
        """,
        (within_minutes,),
    )
    out = {"move": 0, "click": 0}
    for r in rows:
        out[r["kind"]] = int(r["n"])
    return out
