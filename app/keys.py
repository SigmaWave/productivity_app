"""Phase 6 — global keystroke tracking.

Every key-down anywhere on the machine is timestamped and classified
(``char`` = a printable character, ``delete`` = backspace / forward-delete,
``other`` = enter / arrows / tab / function keys / shortcuts …), buffered in
RAM, and bulk-inserted into the ``keystroke`` table every 60 seconds (the
controller owns that timer and calls :meth:`KeystrokeTracker.flush`).

Global key monitoring needs macOS Accessibility permission. Until it is granted
the monitor simply never fires; :func:`accessibility_trusted` reports the state
so the UI can show a hint.
"""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone

from AppKit import NSEvent, NSEventMaskKeyDown

from . import db

try:
    from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagControl
except ImportError:  # pragma: no cover
    NSEventModifierFlagCommand = 1 << 20
    NSEventModifierFlagControl = 1 << 18

_DELETE_KEYCODES = {51, 117}          # backspace, forward-delete
_BUFFER_CAP = 200_000

# Window (minutes) the stats screen looks back over. Both metrics — the cpm
# series and the inter-key diff distribution — read exactly this span, so
# changing it here moves both together. "Last hour" today; a knob later.
STATS_WINDOW_MINUTES = 60

try:
    _AS = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    _AS.AXIsProcessTrusted.restype = ctypes.c_bool
except Exception:  # pragma: no cover
    _AS = None


def accessibility_trusted() -> bool:
    if _AS is None:
        return False
    try:
        return bool(_AS.AXIsProcessTrusted())
    except Exception:  # pragma: no cover
        return False


def _classify(event) -> str:
    """Classify a key-down NSEvent as char / delete / other."""
    if event.keyCode() in _DELETE_KEYCODES:
        return "delete"
    try:
        chars = event.characters() or ""
    except Exception:  # pragma: no cover - non-character events
        return "other"
    mods = event.modifierFlags()
    shortcut = mods & (NSEventModifierFlagCommand | NSEventModifierFlagControl)
    if len(chars) == 1 and chars.isprintable() and not shortcut:
        return "char"
    return "other"


class KeystrokeTracker:
    def __init__(self) -> None:
        self._buffer: list[tuple[datetime, str]] = []
        self._monitor = None
        self.permitted = False
        self.total_flushed = 0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.permitted = accessibility_trusted()
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self._on_event
        )
        if not self.permitted:
            print("[keys] keystroke tracking needs permission — System Settings ▸ "
                  "Privacy & Security ▸ Accessibility ▸ enable your terminal, then "
                  "restart the app")

    def stop(self) -> None:
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    # -- capture -------------------------------------------------------

    def _on_event(self, event) -> None:
        self._buffer.append((datetime.now(timezone.utc), _classify(event)))
        if len(self._buffer) > _BUFFER_CAP:
            del self._buffer[: len(self._buffer) - _BUFFER_CAP]

    # -- flush --------------------------------------------------------

    def flush(self) -> int:
        """Write the RAM buffer to Postgres. Returns the row count written."""
        pending, self._buffer = self._buffer, []
        n = len(pending)
        if n:
            try:
                with db.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO keystroke (ts, kind) VALUES (%s, %s)", pending
                    )
                self.total_flushed += n
            except Exception as exc:  # noqa: BLE001 - keep the buffer, retry next tick
                self._buffer = pending + self._buffer
                print(f"[keys] flush failed, {len(self._buffer)} buffered: {exc}")
                return 0
        print(f"database updated ({n} keystrokes)")
        return n

    def pending_count(self) -> int:
        return len(self._buffer)


# --------------------------------------------------------------------------
# stats queries (read straight from Postgres — flush() first for freshness)
# --------------------------------------------------------------------------

def cpm_series(minutes: int = STATS_WINDOW_MINUTES, bucket_minutes: int = 1) -> list[int]:
    """Chars/min rate per bucket, oldest first, zero-filled. ``bucket_minutes``
    groups multiple minutes into one bar (each still a chars/min rate, not a
    raw count) so wide windows — a day or more — stay readable as a chart."""
    buckets = max(1, minutes // bucket_minutes)
    rows = db.query(
        """
        SELECT floor(extract(epoch from (now() - ts)) / 60 / %s)::int AS ago,
               COUNT(*) AS n
        FROM keystroke
        WHERE kind = 'char' AND ts >= now() - make_interval(mins => %s)
        GROUP BY ago
        """,
        (bucket_minutes, minutes),
    )
    by_ago = {int(r["ago"]): int(r["n"]) for r in rows}
    return [round(by_ago.get(buckets - 1 - i, 0) / bucket_minutes)
            for i in range(buckets)]


def rolling_average(series: list[float], window: int = 20) -> list[float]:
    """Trailing mean of ``series`` — element ``i`` is the mean of the up-to
    ``window`` values ending at ``i`` (fewer near the start). Same length as
    the input, so it overlays the cpm bars one-for-one."""
    out: list[float] = []
    total = 0.0
    for i, v in enumerate(series):
        total += v
        if i >= window:
            total -= series[i - window]
        out.append(total / min(i + 1, window))
    return out


def cpm_average(minutes: int = STATS_WINDOW_MINUTES) -> float:
    row = db.query_one(
        """
        SELECT COUNT(*)::float AS n
        FROM keystroke
        WHERE kind = 'char' AND ts >= now() - make_interval(mins => %s)
        """,
        (minutes,),
    )
    return (row["n"] / minutes) if row else 0.0


def interkey_diffs(within_minutes: int = STATS_WINDOW_MINUTES) -> list[float]:
    """Milliseconds between every pair of consecutive keystrokes (any kind) in
    the window, chronological. Same span as :func:`cpm_series` — both read
    ``STATS_WINDOW_MINUTES`` unless told otherwise."""
    rows = db.query(
        """
        WITH g AS (
            SELECT ts,
                   extract(epoch from (ts - lag(ts) over (order by ts))) * 1000 AS gap_ms
            FROM keystroke
            WHERE ts >= now() - make_interval(mins => %s)
        )
        SELECT gap_ms FROM g WHERE gap_ms IS NOT NULL ORDER BY ts
        """,
        (within_minutes,),
    )
    return [float(r["gap_ms"]) for r in rows]


DIFF_HIST_CAP_MS = 750.0         # default x-axis upper bound of the diff chart
DIFF_HIST_BIN_MS = 50.0          # fixed bar width, so bars keep meaning as the cap grows


def interkey_diff_histogram(
    within_minutes: int = STATS_WINDOW_MINUTES,
    cap_ms: float = DIFF_HIST_CAP_MS, bin_ms: float = DIFF_HIST_BIN_MS,
) -> tuple[list[int], list[float], int, int]:
    """Distribution of the inter-key gaps over the window, truncated at
    ``cap_ms``.

    Returns ``(counts, edges, plotted, overflow)`` where ``counts[i]`` is how
    many gaps fell in ``[edges[i], edges[i + 1])`` ``ms``. Bars are a fixed
    ``bin_ms`` wide, so ``edges`` runs ``0 → cap_ms`` in
    ``round(cap_ms / bin_ms)`` steps. Every bin is an exact count.
    ``plotted`` is how many gaps landed in the bins; ``overflow`` is how many
    were at or beyond ``cap_ms`` (the chart draws those as one dashed
    outline-only bar past the last bin rather than folding them in).

    The x-axis of the resulting bar plot is gap duration, not clock time.
    """
    gaps = interkey_diffs(within_minutes)
    bins = max(1, round(cap_ms / bin_ms))
    width = cap_ms / bins
    edges = [i * width for i in range(bins + 1)]

    counts = [0] * bins
    plotted = 0
    overflow = 0
    for g in gaps:
        if g < 0:
            continue
        if g >= cap_ms:
            overflow += 1
            continue
        counts[int(g / width)] += 1
        plotted += 1
    return counts, edges, plotted, overflow


def counts(within_minutes: int = 60) -> dict[str, int]:
    rows = db.query(
        """
        SELECT kind, COUNT(*) AS n
        FROM keystroke
        WHERE ts >= now() - make_interval(mins => %s)
        GROUP BY kind
        """,
        (within_minutes,),
    )
    out = {"char": 0, "delete": 0, "other": 0}
    for r in rows:
        out[r["kind"]] = int(r["n"])
    return out
