"""macOS menu bar interface (Phases 2.1, 2.3, 3, 4, 6).

Run with ``python run.py``. A 🍅 lives in the status bar; Ctrl+Shift+Space
(system-wide, Carbon ``RegisterEventHotKey``) or a click toggles a pop-up panel
(``NSPopover``) in a green monospace terminal font; Esc closes it.

Screens (``self._screen``):

* **tracking** — plain black. Header (``⚙`` at top-right opens settings), then
  the active task, the next two, and a "N more in queue" line (plain text, not
  clickable) fading down a luminosity gradient. Buttons: ``Tasks`` (opens the
  full task-list window), start/stop Pomodoro, ``stats``, quit.
* **transition** — the base loader: ~1 s of full-screen "Matrix" rain before any
  sub-screen (form, stats, timer, settings) appears.
* **timer** — shown after starting a Pomodoro: a big centred ``M:SS`` countdown
  (``FOCUS`` / ``BREAK``) with the current task under it, then a 2×2 button
  grid. ``■ stop`` freezes the countdown in place (still running, not ticking)
  and becomes ``▶ continue``; ``↺ reset`` restarts the current phase from its
  full duration, discarding progress, without logging anything; ``Tasks``
  opens the full task-list window (remembering to come back here rather than
  to tracking); ``⌂ main page`` returns to tracking, leaving the timer
  running/paused untouched. Only a full countdown reaching zero logs and ends
  the interval (``_pomodoro_finished``). The 1 s tick updates the digits in
  place.
* **settings** — the gear screen: ``pomodoro (min)`` and ``break (min)`` fields
  (``config.set_pomodoro_durations`` → ``config/pomodoro_settings.json``); ``save`` /
  ``cancel``.
* **new task** — plain black form: task field (focused), optional "repeat every"
  weekday drop-down, and a deadline: a quick-pick drop-down (labels from
  ``config/deadlines.json``) whose last item, ``custom…``, reveals a text box taking
  ``Monday`` / ``Monday 24``, plus an optional time-of-day override.
* **stats** — a bar chart with a top-left legend toggling ``cpm`` (chars/min,
  deletes excluded, with a trailing rolling-average line — window set by the
  top-right ``5``/``10``/``20`` buttons — and y-gridlines every 50; x-axis is
  absolute clock time counting back over the window —
  30-min ticks ≤2h, 1-h ticks above), ``diff`` (a histogram of the ms gap
  between consecutive keystrokes, everything counted — x-axis is gap duration,
  not clock time, fixed 50 ms bins up to a cap the top-right ``›`` widens by
  250 ms a click), and ``mouse`` (a spatial scatter of recorded positions —
  dim dots for movement samples, bright dots for clicks — plotted against the
  main screen's resolution rather than a bar chart, since position is the
  point). A
  bottom-right selector (``1h``/``2h``/``6h``/``12h``) sets the look-back
  window for all three; it starts at ``keys.STATS_WINDOW_MINUTES``.
* **calendar** — opened from the ``Calendar`` button: an hour-by-hour day
  timeline by default, a fixed ``09:00``-``24:00`` grid every day, always
  drawn even with nothing on it (``DayTimelineView`` — gridlines down the
  left, tasks as colored blocks, overlapping ones split side by side rather
  than stacked on top of each other, a red current-time line when today is
  shown). A task with a real scheduled time (its deadline's time-of-day isn't
  the ``23:59``/``00:00`` day-boundary marker) spans
  ``[deadline - estimated_duration, deadline]``; everything else — a
  day-boundary-only deadline, or (today only) no deadline at all — gets a
  stable, per-task random slot instead (labelled ``~HH:MM`` rather than a
  real time), on its deadline's day if it has one or today after the current
  moment if it doesn't, since nothing says where it actually belongs. A
  ``day``/``week`` toggle switches to the same fixed hour grid laid out as 7
  day columns side by side (``WeekTimelineView`` — Monday through Sunday,
  each day's blocks confined to its own column, current-time line only
  across today's), narrower and untitled since there isn't room for the day
  view's per-block time label; ``‹``/``›`` slide the shown day or week
  by one unit and ``today`` jumps back to the current one. iCal / Google
  Calendar sync is a later phase — for now it only surfaces this app's own
  task deadlines.

Phase 6: every keystroke on the machine is timestamped in RAM and flushed to the
``keystroke`` table every 60 s (``app/keys.py``). Mouse movement (throttled) and
every click are tracked the same way into ``mouse_event`` (``app/mouse.py``).
"""

from __future__ import annotations

import random
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAppearance,
    NSAttributedString,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSLineBreakByTruncatingTail,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSPopover,
    NSPopUpButton,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSScreen,
    NSStatusBar,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextField,
    NSTimer,
    NSTrackingArea,
    NSVariableStatusItemLength,
    NSView,
    NSViewController,
    NSWindowAbove,
)
from Foundation import NSMakeRange, NSObject
from PyObjCTools import AppHelper

from . import (
    calendar_view,
    config,
    db,
    deadlines,
    jobsearch,
    keys,
    llm_labels,
    mouse,
    pomodoro,
    recurring,
    tasks,
)

try:
    from .hotkey import CONTROL as _HK_CONTROL, SHIFT as _HK_SHIFT, GlobalHotKey
except Exception as _exc:  # noqa: BLE001 - Carbon missing shouldn't kill the app
    GlobalHotKey = None
    _HK_CONTROL = _HK_SHIFT = 0
    print(f"[hotkey] global shortcut unavailable: {_exc}")

try:  # constant name differs across pyobjc versions
    from AppKit import NSMinYEdge
except ImportError:  # pragma: no cover
    NSMinYEdge = 1

try:
    from AppKit import NSEventMaskLeftMouseDown, NSEventMaskRightMouseDown
except ImportError:  # pragma: no cover
    NSEventMaskLeftMouseDown = 1 << 1
    NSEventMaskRightMouseDown = 1 << 3

try:
    from AppKit import (
        NSEventMaskKeyDown,
        NSEventModifierFlagControl,
        NSEventModifierFlagShift,
    )
except ImportError:  # pragma: no cover
    NSEventMaskKeyDown = 1 << 10
    NSEventModifierFlagControl = 1 << 18
    NSEventModifierFlagShift = 1 << 17

KEY_SPACE = 49
KEY_ESC = 53

NSTrackingMouseEnteredAndExited = 0x01
NSTrackingActiveAlways = 0x80
NSTrackingInVisibleRect = 0x200
NSFocusRingTypeNone = 1

IDLE_ICON = "🍅"
BREAK_ICON = "☕"

PANEL_W = 372
PAD = 16
ROW_H = 25
SHEET_H = 304                # fixed height for the transition / form / stats screens

# stats look-back windows the user can click, shortest first
# (label, minutes, cpm bucket size in minutes — >1 for the day+ windows so the
# chart stays a few hundred bars wide instead of one per minute)
_WINDOWS = (
    ("1h", 60, 1), ("2h", 120, 1), ("6h", 360, 1), ("12h", 720, 1),
    ("1d", 1440, 10), ("2d", 2880, 20), ("1w", 10080, 60),
)
_WINDOW_LABELS = {m: lab for lab, m, _ in _WINDOWS}
_WINDOW_BUCKETS = {m: b for _, m, b in _WINDOWS}

# cpm rolling-average windows (minutes) the user can click
_CPM_MA_CHOICES = (5, 10, 20)

# seconds of digital rain shown when loading a sub-screen (the base transition)
TRANSITION_SECONDS = 1.0

# how often the RAM keystroke / mouse-event buffers are written to Postgres
KEYSTROKE_FLUSH_SECONDS = 60
MOUSE_FLUSH_SECONDS = 60

# checking a task off: how long it stands still showing [X] before fading,
# and how long the fade itself takes, before the row is actually removed
TASK_CHECK_STAND_S = 0.5
TASK_CHECK_FADE_S = 2.0

# the tasks-list window is wider than every other screen (PANEL_W) to fit the
# Phase 5 category/energy columns alongside the existing ones
TASKLIST_PANEL_W = PANEL_W + 190

# jobs table (stats screen): applications shown per page
JOBTABLE_PAGE_SIZE = 6

# calendar day view: an hour-by-hour timeline (app/menubar.py DayTimelineView)
# rather than a plain list — tasks become blocks spanning
# [deadline - estimated_duration, deadline], since a task only carries a due
# time, not a real start/end (that arrives with the later iCal/Google sync).
# Fixed for every day, never resized to fit whatever's scheduled.
CAL_DAY_START_HOUR = 9
CAL_DAY_END_HOUR = 24      # clamped to [0, 24]
CAL_DEFAULT_BLOCK_MIN = 30  # block length for a task with no estimated_duration
CAL_HOUR_PX = 26            # pixels per hour row (day view)
CAL_GUTTER = 44             # left column width reserved for hour labels (day view)

# calendar week view: the same fixed hour grid as the day view, but as 7 day
# columns side by side (WeekTimelineView) rather than one line of truncated
# names per day. Shorter per-hour pixel height and gutter than the day view —
# with 7 columns sharing the width there's no room for the day view's detail,
# so it's read as a week-at-a-glance rather than a precise schedule.
CAL_WEEK_HOUR_PX = 15
CAL_WEEK_GUTTER = 26
CAL_WEEK_HEADER_H = 14      # weekday-name row above the hour grid
# the week grid needs more horizontal room for 7 columns than the other
# screens (PANEL_W) — same idea as TASKLIST_PANEL_W widening for its columns
CALENDAR_WEEK_PANEL_W = PANEL_W + 140

# calendar event-block colors, keyed by the task's LLM category label
# (app/llm_labels.CATEGORIES) so same-category tasks read as the same
# "calendar" — a stand-in for the per-source colors a real iCal/Google sync
# will eventually provide
_CAL_PALETTE = (
    (0.20, 0.60, 0.65),   # teal
    (0.25, 0.45, 0.90),   # blue
    (0.85, 0.30, 0.45),   # red / pink
    (0.55, 0.35, 0.85),   # purple
    (0.85, 0.60, 0.20),   # amber
    (0.30, 0.70, 0.40),   # green
)
_CAL_CATEGORY_COLOR = {
    "job": _CAL_PALETTE[1], "linkedin": _CAL_PALETTE[1],
    "mail": _CAL_PALETTE[0], "message": _CAL_PALETTE[0],
    "read_desk": _CAL_PALETTE[0], "read_mobile": _CAL_PALETTE[0],
    "call": _CAL_PALETTE[4],
    "deep_computer": _CAL_PALETTE[3], "deep_offline": _CAL_PALETTE[3],
    "admin_desk": _CAL_PALETTE[5], "admin_mobile": _CAL_PALETTE[5],
    "physical_home": _CAL_PALETTE[2], "physical_out": _CAL_PALETTE[2],
}

# active task + next two visible, then the "in queue" line, all fading down a
# luminosity gradient (GRADIENT[3] is the queue line, kept from when a 4th task
# used to sit there)
VISIBLE_TASKS = 3
GRADIENT = (1.00, 0.52, 0.30, 0.15)

_HEAD = (0.80, 1.00, 0.80)
_BODY = (0.00, 0.92, 0.38)
_KATAKANA = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
_ASCII = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ:.=+*<>[]{}#$%&/|?!"

_REPEAT_ITEMS = ("once", "every day", "weekdays",
                 "Mondays", "Tuesdays", "Wednesdays", "Thursdays",
                 "Fridays", "Saturdays", "Sundays")
_REPEAT_RULES = {
    "once": None, "every day": "daily", "weekdays": "weekdays",
    "Mondays": "weekly:0", "Tuesdays": "weekly:1", "Wednesdays": "weekly:2",
    "Thursdays": "weekly:3", "Fridays": "weekly:4", "Saturdays": "weekly:5",
    "Sundays": "weekly:6",
}
_TIME_ITEMS = ("—", *(f"{h:02d}:00" for h in range(7, 23)))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _task_line(task: dict, *, brief: bool = False) -> str:
    """The task row's text past the checkbox — just the description for the
    dimmer queued rows, description + deadline/duration for the active one."""
    if brief:
        return task["description"]
    meta = []
    deadline = task.get("deadline")
    if deadline:
        fmt = "%m/%d" if (deadline.hour, deadline.minute) in ((23, 59), (0, 0)) else "%m/%d %H:%M"
        meta.append(f"{deadline:{fmt}}")
    if task.get("estimated_duration"):
        meta.append(f'{task["estimated_duration"]:.0f}m')
    tail = f'   {" · ".join(meta)}' if meta else ""
    return f'{task["description"]}{tail}'


def _checkbox_glyph(checked: bool) -> str:
    return "[X]" if checked else "[ ]"


def _elide(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_clock(dt) -> str:
    """``9:05`` / ``11`` — no AM/PM, used inside a time range that carries
    its own suffix, and for the current-time indicator."""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}" if dt.minute else str(h)


def _time_range_label(start_dt, end_dt) -> str:
    period = "AM" if end_dt.hour < 12 else "PM"
    return f"{_fmt_clock(start_dt)}–{_fmt_clock(end_dt)}{period}"


def _hour_label(hour: int) -> str:
    hour %= 24
    period = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {period}"


def _naive(dt):
    """Strip tzinfo from a fetched ``timestamptz`` value — Postgres already
    converts it to the session's local wall-clock time on the way out, so the
    fields are already local; this just makes it safe to mix with a plain
    ``datetime.now()`` in arithmetic without a naive/aware TypeError."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _layout_columns(spans):
    """Assign each ``(start, end, task)`` span a side-by-side column so
    overlapping tasks in the day timeline sit next to each other instead of
    stacking on top of one another. Returns parallel ``(col, cols)`` lists —
    ``col`` is the span's 0-based column index, ``cols`` the total number of
    columns in its overlap cluster (spans that don't overlap anything get
    ``col=0, cols=1``, i.e. full width).

    Standard calendar-view sweep: walk spans in start order, track each open
    column's current end time, and drop a span into the first column that's
    already free (its previous occupant ended by this span's start) or open a
    new one. A run of spans connected end-to-end by overlap forms one
    cluster, sized by the most columns any single moment in it needed."""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    col_of = [0] * len(spans)
    cluster_of = [0] * len(spans)
    cluster_cols = []

    col_ends: list = []          # end time currently occupying each column
    cluster_end = None
    cluster_id = -1
    for i in order:
        start, end, _ = spans[i]
        if cluster_end is None or start >= cluster_end:
            cluster_id += 1
            cluster_cols.append(1)
            col_ends = []
            cluster_end = end
        else:
            cluster_end = max(cluster_end, end)
        placed = False
        for ci, col_end in enumerate(col_ends):
            if col_end <= start:
                col_ends[ci] = end
                col_of[i] = ci
                placed = True
                break
        if not placed:
            col_ends.append(end)
            col_of[i] = len(col_ends) - 1
        cluster_of[i] = cluster_id
        cluster_cols[cluster_id] = max(cluster_cols[cluster_id], len(col_ends))

    cols_of = [cluster_cols[cluster_of[i]] for i in range(len(spans))]
    return col_of, cols_of


def _calendar_day_blocks(day, rows, undated, range_start, range_end):
    """Timeline blocks for one day of the calendar (day view or one column of
    the week grid) — shared so both read the same placement rules.

    ``rows`` are tasks whose deadline falls on ``day`` (real day, real time);
    ``undated`` are open tasks with no deadline at all, only ever passed for
    today. A task's deadline time of ``23:59``/``00:00`` is treated as a
    day-boundary marker rather than a real scheduled time (that's what "by
    end of day" resolves to) — like an undated task, it gets a stable
    per-task random slot instead of pinning to the boundary, labelled
    ``~HH:MM`` rather than a real time. Returns ``blocks`` ready for
    DayTimelineView/WeekTimelineView, with side-by-side "col"/"cols" already
    assigned via ``_layout_columns``.
    """
    span_seconds = (range_end - range_start).total_seconds()
    now = datetime.now()

    def frac(dt):
        return max(0.0, min(1.0, (dt - range_start).total_seconds() / span_seconds))

    spans = []
    for task in rows:
        deadline = _naive(task["deadline"])
        duration = float(task.get("estimated_duration") or CAL_DEFAULT_BLOCK_MIN)
        if (deadline.hour, deadline.minute) in ((23, 59), (0, 0)):
            rng = random.Random(int(task["id"]) * 1_000_003 + day.toordinal() * 7 + 1)
            start_dt = range_start + timedelta(
                seconds=rng.random() * max(0.0, span_seconds - duration * 60))
            spans.append((start_dt, start_dt + timedelta(minutes=duration), task, True))
        else:
            spans.append((deadline - timedelta(minutes=duration), deadline, task, False))
    for task in undated:
        duration = float(task.get("estimated_duration") or CAL_DEFAULT_BLOCK_MIN)
        rng = random.Random(int(task["id"]) * 1_000_003 + day.toordinal() * 7 + 2)
        window_start = max(range_start, now)
        window = max(0.0, (range_end - window_start).total_seconds() - duration * 60)
        start_dt = window_start + timedelta(seconds=rng.random() * window)
        spans.append((start_dt, start_dt + timedelta(minutes=duration), task, True))

    col_of, cols_of = _layout_columns([(s, e, t) for s, e, t, _ in spans])

    blocks = []
    for i, (start_dt, end_dt, task, approx) in enumerate(spans):
        sf = frac(start_dt)
        label = _time_range_label(start_dt, end_dt)
        blocks.append({
            "title": task["description"],
            "label": f"~{label}" if approx else label,
            "start_frac": sf,
            "end_frac": max(sf + 0.02, frac(end_dt)),
            "col": col_of[i],
            "cols": cols_of[i],
            "rgb": _CAL_CATEGORY_COLOR.get(task.get("category"), _CAL_PALETTE[0]),
        })
    return blocks


# column widths (characters, monospace) for the full task-list table
_COL_ORDER_W = 3
_COL_DESC_W = 20
_COL_DEADLINE_W = 8
# Phase 5: LLM labels — widths fit every value in llm_labels.CATEGORIES /
# ENERGY_LEVELS without eliding ("deep_computer"/"physical_home" are the
# longest categories at 13; "medium" the longest energy level at 6)
_COL_CATEGORY_W = 13
_COL_ENERGY_W = 6
_TABLE_ROW_W = (_COL_ORDER_W + _COL_DESC_W + _COL_DEADLINE_W
                + _COL_CATEGORY_W + _COL_ENERGY_W + 4 * len(" | "))


def _table_row(order: str, desc: str, deadline: str,
                category: str, energy: str) -> str:
    return (f"{str(order).rjust(_COL_ORDER_W)} | "
            f"{_elide(desc, _COL_DESC_W).ljust(_COL_DESC_W)} | "
            f"{deadline.ljust(_COL_DEADLINE_W)} | "
            f"{_elide(category, _COL_CATEGORY_W).ljust(_COL_CATEGORY_W)} | "
            f"{energy.ljust(_COL_ENERGY_W)}")


# column widths (characters, monospace) for the jobs table (stats screen)
_JOB_COL_ID_W = 3
_JOB_COL_TS_W = 11        # "%m/%d %H:%M"
_JOB_COL_TAKEN_W = 7      # e.g. "12.3m"
_JOB_TABLE_ROW_W = (_JOB_COL_ID_W + 2 * _JOB_COL_TS_W + _JOB_COL_TAKEN_W
                     + 3 * len(" | "))


def _job_row(job_id: str, started: str, finished: str, taken: str) -> str:
    return (f"{str(job_id).rjust(_JOB_COL_ID_W)} | "
            f"{started.ljust(_JOB_COL_TS_W)} | "
            f"{finished.ljust(_JOB_COL_TS_W)} | "
            f"{taken.ljust(_JOB_COL_TAKEN_W)}")


def _ui_font(size: float) -> NSFont:
    for name in ("Menlo", "Monaco", "Andale Mono", "Courier New"):
        font = NSFont.fontWithName_size_(name, size)
        if font is not None:
            return font
    return NSFont.userFixedPitchFontOfSize_(size)


def _rain_font(size: float) -> tuple[NSFont, bool]:
    for name, cjk in (("Osaka-Mono", True), ("Hiragino Sans", True),
                      ("PingFang SC", True), ("Menlo", False)):
        font = NSFont.fontWithName_size_(name, size)
        if font is not None:
            return font, cjk
    return NSFont.userFixedPitchFontOfSize_(size), False


def _green(rgb: tuple[float, float, float], alpha: float) -> NSColor:
    return NSColor.colorWithDeviceRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


_DARK = None


def _dark_appearance():
    global _DARK
    if _DARK is None:
        try:
            _DARK = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        except Exception:  # pragma: no cover
            _DARK = False
    return _DARK or None


# --------------------------------------------------------------------------
# hover-aware terminal button
# --------------------------------------------------------------------------

class TermButton(NSButton):
    def initWithFrame_(self, frame):
        self = objc.super(TermButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self._text = ""
        self._base = _green(_BODY, 0.95)
        self._hover = _green(_HEAD, 1.0)
        self._align = NSTextAlignmentLeft
        self.setBordered_(False)
        self.setFocusRingType_(NSFocusRingTypeNone)
        self.setFont_(_ui_font(12.5))
        self.setAlignment_(NSTextAlignmentLeft)
        self.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
        return self

    @objc.python_method
    def configure(self, text, base, hover, *, align=None):
        self._text, self._base, self._hover = text, base, hover
        if align is not None:
            self._align = align
        self._paint(self._base)

    @objc.python_method
    def set_text(self, text):
        """Update just the label, keeping the current colors/alignment —
        for a button whose text ticks on a timer (e.g. the job-search
        banner) without needing to re-supply its colors every time."""
        self._text = text
        self._paint(self._base)

    @objc.python_method
    def _paint(self, color):
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(self._align)
        para.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self.setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_(
                self._text,
                {NSFontAttributeName: self.font(),
                 NSForegroundColorAttributeName: color,
                 NSParagraphStyleAttributeName: para},
            )
        )

    def updateTrackingAreas(self):
        objc.super(TermButton, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        opts = (NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways
                | NSTrackingInVisibleRect)
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None
            )
        )

    def mouseEntered_(self, event):
        self._paint(self._hover)

    def mouseExited_(self, event):
        self._paint(self._base)


# --------------------------------------------------------------------------
# draggable row for the full task-list window (click and drag to reorder)
# --------------------------------------------------------------------------

class TaskRow(NSView):
    """One row of the tasks-list table. Plain text, but pressing and dragging
    it vertically reorders it among its page-mates — see MenuController's
    _task_row_drag_* methods, which own the actual reorder bookkeeping; this
    view just reports raw mouse events in its superview's coordinate space."""

    def initWithFrame_(self, frame):
        self = objc.super(TaskRow, self).initWithFrame_(frame)
        if self is None:
            return None
        self.controller = None
        self.task_id = None
        # drawn directly (no child NSTextField): a subview covering the row
        # would be what actually gets hit-tested, so this view's own
        # mouseDown_/mouseDragged_ would never fire
        self._text = ""
        self._color = _green(_BODY, 0.9)
        self._desc = self._deadline_s = ""
        self._category_s = self._energy_s = ""
        self._drag_origin_y = 0.0
        self._frame_origin_y = 0.0
        return self

    def drawRect_(self, rect):
        NSAttributedString.alloc().initWithString_attributes_(
            self._text,
            {NSFontAttributeName: _ui_font(11),
             NSForegroundColorAttributeName: self._color},
        ).drawAtPoint_(NSMakePoint(0, 1))

    @objc.python_method
    def configure(self, controller, task_id, desc, deadline_s,
                  category_s, energy_s, order, rgb, alpha):
        self.controller = controller
        self.task_id = task_id
        # kept so set_order() can redraw the row with just a new "#" as it
        # moves during a drag, without the controller re-fetching from Postgres
        self._desc, self._deadline_s = desc, deadline_s
        self._category_s, self._energy_s = category_s, energy_s
        self._color = _green(rgb, alpha)
        self.set_order(order)

    @objc.python_method
    def set_order(self, order):
        self._text = _table_row(order, self._desc, self._deadline_s,
                                 self._category_s, self._energy_s)
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        loc = self.superview().convertPoint_fromView_(event.locationInWindow(), None)
        self._drag_origin_y = loc.y
        self._frame_origin_y = self.frame().origin.y
        if self.controller is not None:
            self.controller.task_row_drag_began(self)

    def mouseDragged_(self, event):
        loc = self.superview().convertPoint_fromView_(event.locationInWindow(), None)
        dy = loc.y - self._drag_origin_y
        if self.controller is not None:
            self.controller.task_row_dragged(self, dy)

    def mouseUp_(self, event):
        if self.controller is not None:
            self.controller.task_row_drag_ended(self)


# --------------------------------------------------------------------------
# Matrix digital-rain view (hosts every control as a subview)
# --------------------------------------------------------------------------

class MatrixView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(MatrixView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._cell = 15.0
        self._font, cjk = _rain_font(14.0)
        self._charset = _KATAKANA if cjk else _ASCII
        self._columns = []
        self._last = time.monotonic()
        self._rain = False               # rain only runs during the transition
        self.separators = []
        return self

    def isFlipped(self):
        return True

    @objc.python_method
    def set_rain(self, on):
        self._rain = bool(on)
        if on:
            self._last = time.monotonic()
            self._seed_columns()
        self.setNeedsDisplay_(True)

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def _new_column(self, index, height, *, seeded):
        length = random.randint(6, 18)
        rows = height / self._cell
        return {
            "x": index * self._cell + 3,
            "head": random.uniform(0.0, rows + length) if seeded
            else random.uniform(-rows * 0.4, 0.0),
            "speed": random.uniform(7.0, 24.0),
            "length": length,
            "glyphs": [random.choice(self._charset) for _ in range(length)],
        }

    @objc.python_method
    def _seed_columns(self):
        width = self.bounds().size.width or PANEL_W
        height = self.bounds().size.height or 420
        count = int(width // self._cell) + 1
        self._columns = [self._new_column(i, height, seeded=True) for i in range(count)]

    def setFrameSize_(self, size):
        objc.super(MatrixView, self).setFrameSize_(size)
        if self._rain:
            self._seed_columns()

    def stepAnimation_(self, timer):
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        width, height = bounds.size.width, bounds.size.height

        NSColor.blackColor().set()
        NSBezierPath.fillRect_(bounds)

        if self._rain:
            now = time.monotonic()
            dt = min(0.1, now - self._last)
            self._last = now
            for column in self._columns:
                column["head"] += column["speed"] * dt
                if random.random() < 0.07:
                    column["glyphs"][random.randrange(column["length"])] = \
                        random.choice(self._charset)
                if (column["head"] - column["length"]) * self._cell > height:
                    idx = int((column["x"] - 3) // self._cell)
                    column.update(self._new_column(idx, height, seeded=False))
                for i in range(column["length"]):
                    row = column["head"] - i
                    if row < 0:
                        continue
                    y = row * self._cell
                    if y > height:
                        continue
                    if i == 0:
                        color = _green(_HEAD, 1.0)
                    elif i <= 2:
                        color = _green(_BODY, 0.9)
                    else:
                        color = _green(_BODY, max(0.05, 0.85 * (1.0 - i / column["length"])))
                    NSAttributedString.alloc().initWithString_attributes_(
                        column["glyphs"][i],
                        {NSFontAttributeName: self._font,
                         NSForegroundColorAttributeName: color},
                    ).drawAtPoint_(NSMakePoint(column["x"], y))

        _green(_BODY, 0.30).set()
        for y in self.separators:
            NSBezierPath.fillRect_(NSMakeRect(PAD, y, width - 2 * PAD, 1))


# --------------------------------------------------------------------------
# Stats bar chart
# --------------------------------------------------------------------------

class ChartView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(ChartView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._values = []
        self._vmax = 1.0
        self._top_label = ""
        self._caption = ""
        self._empty = "no data yet"
        self._x_ticks = []          # list of (fraction_along_x, label)
        self._overlay = []          # line drawn over the bars, same y-scale
        self._y_step = 0.0          # horizontal gridline spacing in value units
        self._overflow = None       # (value, label) drawn as a dashed extra bar
        return self

    @objc.python_method
    def set_data(self, values, vmax, top_label, caption, empty, x_ticks=None,
                 overlay=None, y_step=0, overflow=None):
        self._values = [float(v) for v in values]
        self._vmax = float(vmax) or 1.0
        self._top_label = top_label
        self._caption = caption
        self._empty = empty
        self._x_ticks = list(x_ticks or [])
        self._overlay = [float(v) for v in (overlay or [])]
        self._y_step = float(y_step or 0)
        self._overflow = (
            (float(overflow[0]), str(overflow[1])) if overflow else None)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _text(self, string, x, y, *, alpha=0.7, size=10.0):
        NSAttributedString.alloc().initWithString_attributes_(
            string,
            {NSFontAttributeName: _ui_font(size),
             NSForegroundColorAttributeName: _green(_BODY, alpha)},
        ).drawAtPoint_(NSMakePoint(x, y))

    def drawRect_(self, rect):
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height
        NSColor.blackColor().set()
        NSBezierPath.fillRect_(bounds)

        m_left, m_top, m_right = 30, 40, 8
        m_bottom = 22 if self._x_ticks else 16
        pw = w - m_left - m_right
        ph = h - m_bottom - m_top

        _green(_BODY, 0.35).set()
        axis = NSBezierPath.bezierPath()
        axis.moveToPoint_(NSMakePoint(m_left, m_bottom))
        axis.lineToPoint_(NSMakePoint(m_left + pw, m_bottom))
        axis.moveToPoint_(NSMakePoint(m_left, m_bottom))
        axis.lineToPoint_(NSMakePoint(m_left, m_bottom + ph))
        axis.setLineWidth_(1.0)
        axis.stroke()

        if not self._values:
            self._text(self._empty, m_left + 10, m_bottom + ph / 2, alpha=0.5, size=11)
            return

        vmax = self._vmax
        n = len(self._values)
        bw = pw / (n + 1 if self._overflow else n)

        if self._y_step > 0:
            v = self._y_step
            while v <= vmax:
                gy = m_bottom + ph * (v / vmax)
                _green(_BODY, 0.18).set()
                grid = NSBezierPath.bezierPath()
                grid.moveToPoint_(NSMakePoint(m_left, gy))
                grid.lineToPoint_(NSMakePoint(m_left + pw, gy))
                grid.setLineWidth_(0.5)
                grid.stroke()
                self._text(f"{v:.0f}", m_left - 20, gy - 4, alpha=0.5, size=9)
                v += self._y_step

        for i, value in enumerate(self._values):
            frac = min(1.0, value / vmax)
            bh = ph * frac
            x = m_left + i * bw
            _green(_BODY, 0.5 + 0.45 * frac).set()
            NSBezierPath.fillRect_(
                NSMakeRect(x + 0.5, m_bottom + 1, max(1.0, bw - 1.0), bh)
            )

        # overflow: everything past the current x upper bound, as one dashed
        # outline-only bar in the slot after the last bin, capped-value label on top
        if self._overflow:
            ov_val, ov_label = self._overflow
            obh = ph * min(1.0, ov_val / vmax)
            ox = m_left + n * bw
            dashed = NSBezierPath.bezierPathWithRect_(
                NSMakeRect(ox + 0.5, m_bottom + 1, max(1.0, bw - 1.0), max(1.0, obh))
            )
            dashed.setLineWidth_(1.0)
            dashed.setLineDash_count_phase_([2.0, 2.0], 2, 0.0)
            _green(_BODY, 0.55).set()
            dashed.stroke()
            self._text(
                ov_label, ox + bw / 2 - 3.0 * len(ov_label),
                min(m_bottom + obh + 3, m_bottom + ph - 9), alpha=0.6, size=9)

        if len(self._overlay) >= 2:
            line = NSBezierPath.bezierPath()
            for i, value in enumerate(self._overlay):
                py = m_bottom + 1 + ph * min(1.0, value / vmax)
                px = m_left + i * bw + bw / 2
                if i == 0:
                    line.moveToPoint_(NSMakePoint(px, py))
                else:
                    line.lineToPoint_(NSMakePoint(px, py))
            _green(_HEAD, 0.9).set()
            line.setLineWidth_(1.5)
            line.stroke()

        if self._top_label:
            label_w = 7.0 * len(self._top_label)
            self._text(self._top_label, m_left + pw - label_w, m_bottom + ph - 2,
                       alpha=0.55)
        self._text("0", m_left - 12, m_bottom - 3, alpha=0.5)

        if self._x_ticks:
            last_label_x = -1e9
            for frac, label in self._x_ticks:
                tx = m_left + pw * frac
                _green(_BODY, 0.3).set()
                mark = NSBezierPath.bezierPath()
                mark.moveToPoint_(NSMakePoint(tx, m_bottom))
                mark.lineToPoint_(NSMakePoint(tx, m_bottom - 3))
                mark.setLineWidth_(1.0)
                mark.stroke()
                lx = tx - 3.0 * len(label)
                if lx - last_label_x >= 34:      # skip labels that would collide
                    self._text(label, lx, m_bottom - 13, alpha=0.5, size=9)
                    last_label_x = lx

        # only the permission hint (or another alert) still uses the caption slot
        if self._caption:
            self._text(self._caption, m_left, 1, alpha=0.6)


# --------------------------------------------------------------------------
# mouse activity map (spatial scatter of recorded positions)
# --------------------------------------------------------------------------

class MouseMapView(NSView):
    """Positions from ``mouse_event`` plotted where they happened on screen —
    dim dots for movement samples, brighter dots for clicks — scaled down
    into the chart area against the main screen's resolution."""

    def initWithFrame_(self, frame):
        self = objc.super(MouseMapView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._points = []       # [{"x", "y", "kind"}]
        self._screen_w = 1920.0
        self._screen_h = 1080.0
        self._caption = ""
        self._empty = "no mouse activity yet"
        return self

    @objc.python_method
    def set_data(self, points, screen_w, screen_h, caption, empty):
        self._points = list(points)
        self._screen_w = float(screen_w) or 1920.0
        self._screen_h = float(screen_h) or 1080.0
        self._caption = caption
        self._empty = empty
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _text(self, string, x, y, *, alpha=0.7, size=10.0):
        NSAttributedString.alloc().initWithString_attributes_(
            string,
            {NSFontAttributeName: _ui_font(size),
             NSForegroundColorAttributeName: _green(_BODY, alpha)},
        ).drawAtPoint_(NSMakePoint(x, y))

    def drawRect_(self, rect):
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height
        NSColor.blackColor().set()
        NSBezierPath.fillRect_(bounds)

        m = 6
        pw, ph = w - 2 * m, h - 2 * m
        _green(_BODY, 0.25).set()
        NSBezierPath.strokeRect_(NSMakeRect(m, m, pw, ph))

        if not self._points:
            self._text(self._empty, m + 10, m + ph / 2, alpha=0.5, size=11)
            return

        # NSEvent.mouseLocation() is bottom-left origin, same as this (flipped
        # or not) view's default coordinate system, so plot x/y directly.
        for p in self._points:
            fx = min(1.0, max(0.0, p["x"] / self._screen_w))
            fy = min(1.0, max(0.0, p["y"] / self._screen_h))
            px = m + fx * pw
            py = m + fy * ph
            if p["kind"] == "click":
                _green(_HEAD, 0.85).set()
                r = 2.2
            else:
                _green(_BODY, 0.35).set()
                r = 1.0
            NSBezierPath.fillRect_(NSMakeRect(px - r, py - r, 2 * r, 2 * r))

        if self._caption:
            self._text(self._caption, m + 4, m + 3, alpha=0.6)


# --------------------------------------------------------------------------
# calendar day timeline (hour grid + proportional event blocks)
# --------------------------------------------------------------------------

class DayTimelineView(NSView):
    """Hour-by-hour agenda for one day: gridlines + labels down the left
    gutter, tasks as colored blocks positioned/sized by their time span
    (overlapping ones split side by side via ``_layout_columns`` rather than
    stacking on top of each other), and a red current-time line when the
    shown day is today."""

    def initWithFrame_(self, frame):
        self = objc.super(DayTimelineView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._start_hour = CAL_DAY_START_HOUR
        self._end_hour = CAL_DAY_END_HOUR
        self._blocks = []      # [{"title", "label", "start_frac", "end_frac", "rgb"}]
        self._now_frac = None
        self._now_label = ""
        return self

    def isFlipped(self):
        return True

    @objc.python_method
    def set_data(self, start_hour, end_hour, blocks, now_frac, now_label):
        self._start_hour = start_hour
        self._end_hour = end_hour
        self._blocks = blocks
        self._now_frac = now_frac
        self._now_label = now_label
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _text(self, string, x, y, *, rgb, alpha, size):
        NSAttributedString.alloc().initWithString_attributes_(
            string,
            {NSFontAttributeName: _ui_font(size),
             NSForegroundColorAttributeName: _green(rgb, alpha)},
        ).drawAtPoint_(NSMakePoint(x, y))

    def drawRect_(self, rect):
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height
        NSColor.blackColor().set()
        NSBezierPath.fillRect_(bounds)

        gutter = CAL_GUTTER
        hours = max(1, self._end_hour - self._start_hour)
        px_per_hour = h / hours

        for i in range(hours + 1):
            y = i * px_per_hour
            _green(_BODY, 0.15).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(gutter, y))
            line.lineToPoint_(NSMakePoint(w, y))
            line.setLineWidth_(0.5)
            line.stroke()
            if i < hours:
                self._text(_hour_label(self._start_hour + i), 2, y + 3,
                           rgb=_BODY, alpha=0.55, size=9.5)

        for block in self._blocks:
            y0 = block["start_frac"] * h
            y1 = block["end_frac"] * h
            bh = max(16.0, y1 - y0)
            r, g, b = block["rgb"]

            # tasks overlapping in time sit side by side rather than one on
            # top of another: "col"/"cols" (from _layout_columns) split the
            # available width into cols equal slices with a thin gap between
            avail_w = w - gutter - 6
            cols = max(1, block.get("cols", 1))
            col = block.get("col", 0)
            gap = 3.0 if cols > 1 else 0.0
            col_w = (avail_w - gap * (cols - 1)) / cols
            bx = gutter + 3 + col * (col_w + gap)

            body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bx, y0, col_w, bh), 4, 4)
            NSColor.colorWithDeviceRed_green_blue_alpha_(r, g, b, 0.30).set()
            body.fill()
            NSColor.colorWithDeviceRed_green_blue_alpha_(r, g, b, 0.95).set()
            NSBezierPath.fillRect_(NSMakeRect(bx, y0, 3, bh))
            max_chars = max(4, int((col_w - 10) / 6.2))
            self._text(_elide(block["title"], max_chars), bx + 7, y0 + 3,
                       rgb=(1.0, 1.0, 1.0), alpha=0.92, size=10.5)
            if bh >= 20 and col_w >= 40:
                self._text(block["label"], bx + 7, y0 + 17,
                           rgb=(1.0, 1.0, 1.0), alpha=0.6, size=9)

        if self._now_frac is not None:
            y = self._now_frac * h
            NSColor.colorWithDeviceRed_green_blue_alpha_(0.95, 0.25, 0.25, 0.95).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(gutter, y))
            line.lineToPoint_(NSMakePoint(w, y))
            line.setLineWidth_(1.2)
            line.stroke()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(w - 6, y - 3, 6, 6)).fill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(2, y - 7, gutter - 8, 14), 3, 3).fill()
            self._text(self._now_label, 5, y - 5,
                       rgb=(1.0, 1.0, 1.0), alpha=1.0, size=9.5)


class WeekTimelineView(NSView):
    """7-day grid version of DayTimelineView: weekday-name columns across the
    top over the same fixed hour grid, each day's blocks confined to its own
    column (side by side, not stacked) so the week reads at a glance instead
    of one line of truncated task names per day. Columns are too narrow for
    the day view's detail, so blocks show a short title only — no time label
    — and the current-time line is drawn across just today's column."""

    def initWithFrame_(self, frame):
        self = objc.super(WeekTimelineView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._start_hour = CAL_DAY_START_HOUR
        self._end_hour = CAL_DAY_END_HOUR
        self._day_labels = []      # 7 strings, e.g. "Mon"
        self._today_index = None   # 0-6, or None if today isn't in view
        self._columns = []         # 7 lists of blocks (see DayTimelineView)
        self._now_frac = None
        return self

    def isFlipped(self):
        return True

    @objc.python_method
    def set_data(self, start_hour, end_hour, day_labels, today_index, columns, now_frac):
        self._start_hour = start_hour
        self._end_hour = end_hour
        self._day_labels = day_labels
        self._today_index = today_index
        self._columns = columns
        self._now_frac = now_frac
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _text(self, string, x, y, *, rgb, alpha, size):
        NSAttributedString.alloc().initWithString_attributes_(
            string,
            {NSFontAttributeName: _ui_font(size),
             NSForegroundColorAttributeName: _green(rgb, alpha)},
        ).drawAtPoint_(NSMakePoint(x, y))

    def drawRect_(self, rect):
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height
        NSColor.blackColor().set()
        NSBezierPath.fillRect_(bounds)

        gutter = CAL_WEEK_GUTTER
        header_h = CAL_WEEK_HEADER_H
        hours = max(1, self._end_hour - self._start_hour)
        grid_h = h - header_h
        n_days = len(self._day_labels) or 7
        col_w = (w - gutter) / n_days

        if self._today_index is not None:
            _green(_HEAD, 0.07).set()
            NSBezierPath.fillRect_(NSMakeRect(
                gutter + self._today_index * col_w, header_h, col_w, grid_h))

        for i, label in enumerate(self._day_labels):
            is_today = i == self._today_index
            self._text(label, gutter + i * col_w + 3, 2,
                       rgb=_HEAD if is_today else _BODY,
                       alpha=1.0 if is_today else 0.6, size=8.5)

        for i in range(hours + 1):
            gy = header_h + i * (grid_h / hours)
            _green(_BODY, 0.15).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(gutter, gy))
            line.lineToPoint_(NSMakePoint(w, gy))
            line.setLineWidth_(0.5)
            line.stroke()
            if i < hours:
                self._text(_hour_label(self._start_hour + i), 1, gy + 2,
                           rgb=_BODY, alpha=0.5, size=7.5)

        for i in range(n_days + 1):
            gx = gutter + i * col_w
            _green(_BODY, 0.18).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(gx, header_h))
            line.lineToPoint_(NSMakePoint(gx, h))
            line.setLineWidth_(0.5)
            line.stroke()

        for day_i, blocks in enumerate(self._columns):
            cx = gutter + day_i * col_w
            for block in blocks:
                y0 = header_h + block["start_frac"] * grid_h
                y1 = header_h + block["end_frac"] * grid_h
                bh = max(7.0, y1 - y0)
                r, g, b = block["rgb"]
                cols = max(1, block.get("cols", 1))
                col = block.get("col", 0)
                sub_w = (col_w - 2) / cols
                bx = cx + 1 + col * sub_w

                NSColor.colorWithDeviceRed_green_blue_alpha_(r, g, b, 0.35).set()
                NSBezierPath.fillRect_(NSMakeRect(bx, y0, max(1.0, sub_w - 1), bh))
                NSColor.colorWithDeviceRed_green_blue_alpha_(r, g, b, 0.95).set()
                NSBezierPath.fillRect_(NSMakeRect(bx, y0, 2, bh))
                if bh >= 8 and sub_w >= 12:
                    max_chars = max(2, int((sub_w - 4) / 4.6))
                    self._text(_elide(block["title"], max_chars), bx + 3, y0 + 1,
                               rgb=(1.0, 1.0, 1.0), alpha=0.92, size=6.5)

        if self._now_frac is not None and self._today_index is not None:
            gy = header_h + self._now_frac * grid_h
            x0 = gutter + self._today_index * col_w
            NSColor.colorWithDeviceRed_green_blue_alpha_(0.95, 0.25, 0.25, 0.95).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(x0, gy))
            line.lineToPoint_(NSMakePoint(x0 + col_w, gy))
            line.setLineWidth_(1.2)
            line.stroke()


# --------------------------------------------------------------------------
# controller: status item + popover + all actions
# --------------------------------------------------------------------------

class MenuController(NSObject):
    def init(self):
        self = objc.super(MenuController, self).init()
        if self is None:
            return None
        self.pomo = pomodoro.PomodoroState()
        self._db_ok = False
        self._last_generated = None
        self._ticks = 0
        # "tracking" | "transition" | "form" | "stats" | "timer" | "settings"
        # | "tasklist" | "jobsearch" | "jobstable" | "calendar"
        self._screen = "tracking"
        # which screen to return to on the tasks-list "‹ back" button — set
        # by openTaskList_ to whichever screen ("tracking" or "timer") it was
        # opened from
        self._tasklist_return_screen = "tracking"
        # drag-to-reorder state for the tasks-list window: every open task's
        # id in on-screen order (mutated live while dragging), their TaskRow
        # views, and the geometry of the row band — reorder_tasks() gets this
        # order directly, the whole list being shown at once now
        self._tasklist_row_order = []
        self._tasklist_row_views = {}
        self._tasklist_row_top = 0.0
        self._tasklist_row_step = 16.0
        self._drag_task_id = None
        self._drag_moved = False
        # job-search stopwatch: manually started, pausable, resets to 0 when
        # the "+" counter is clicked (see jobApplied_/jobStopwatchStart_).
        # elapsed_accum banks the time from segments already paused through;
        # while running, the current segment (time.monotonic() since
        # started_monotonic) is added on top of that. started_at is the wall
        # -clock time of the very first start of this attempt (unchanged by
        # pausing/resuming) — logged to Postgres as when the attempt began.
        self._stopwatch_running = False
        self._stopwatch_started_monotonic = 0.0
        self._stopwatch_elapsed_accum = 0.0
        self._stopwatch_started_at = None
        self._stopwatch_label = None
        # shrinks the job-search popover to a notification-banner-sized strip
        # when you click elsewhere (the same outside-click monitor that
        # normally closes the popover — see popoverDidShow_); clicking the
        # banner itself (jobSearchExpand_) expands it back
        self._jobsearch_compact = False
        self._content_w = PANEL_W       # popover width; screens may override it
        self._jobtable_page = 0
        # calendar screen: "day" shows self._cal_anchor itself, "week" shows
        # the 7 days starting at self._cal_anchor (always a Monday in that mode)
        self._cal_mode = "day"
        self._cal_anchor = date.today()
        self._daytimeline = None
        self._weektimeline = None
        self._flash_msg = ""
        self._flash_until = 0.0
        self._form_err = ""
        self._settings_err = ""
        self._monitor = None
        self._header = None
        self._transition_timer = None
        self._transition_target = "form"
        self._stats_metric = "cpm"          # "cpm" | "diff" | "mouse"
        self._stats_window = keys.STATS_WINDOW_MINUTES   # minutes; 1h/2h/6h/12h
        self._diff_cap = keys.DIFF_HIST_CAP_MS           # diff chart x upper bound (ms)
        self._cpm_ma = 20                                # cpm rolling-average window (min)
        self._chart = None
        self._mouse_map = None
        self._f_task = self._f_repeat = self._f_day = self._f_time = None
        self._f_entry = None
        self._f_entry_prev = ""          # last value seen, for the delete guard
        self._s_work = self._s_break = None
        self._timer_label = None
        # task-complete animation: task_id -> {"start": monotonic()} for a row
        # that's been checked off and is standing still / fading before its
        # slot is reclaimed; _tracking_rows/_tracking_total are the task list
        # as of the last real fetch, reused while any row is animating so the
        # rest of the list doesn't jump until the completed row is gone
        self._completing = {}
        self._task_row_widgets = {}
        self._task_anim_timer = None
        self._tracking_rows = []
        self._tracking_total = 0
        self.tracker = keys.KeystrokeTracker()
        self.mouse_tracker = mouse.MouseTracker()

        self.view = MatrixView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, 420))
        self.vc = NSViewController.alloc().init()
        self.vc.setView_(self.view)

        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(self.vc)
        self.popover.setContentSize_(NSMakeSize(PANEL_W, 420))
        self.popover.setAnimates_(True)
        self.popover.setBehavior_(0)        # application-defined; we close it ourselves
        self.popover.setDelegate_(self)

        bar = NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self.status_item.button()
        button.setTitle_(IDLE_ICON)
        button.setTarget_(self)
        button.setAction_("togglePopover:")

        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "tick:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self._tick_timer = timer
        self._anim_timer = None

        # System-wide Ctrl+Shift+Space via Carbon (needs no permission).
        self._hotkey = None
        if GlobalHotKey is not None:
            try:
                hk = GlobalHotKey(KEY_SPACE, _HK_CONTROL | _HK_SHIFT, self._hotkey_fired)
                self._hotkey = hk
                if not hk.ok:
                    print("[hotkey] RegisterEventHotKey failed — falling back to "
                          "in-app shortcut only")
            except Exception as exc:  # noqa: BLE001
                print(f"[hotkey] {exc}")

        # Local monitor: Esc to close, plus Ctrl+Shift+Space when the global
        # hot key could not be registered.
        self._key_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self._handle_key_event
        )

        # Phase 6: global keystroke tracking, RAM buffer -> Postgres every 60s.
        self.tracker.start()
        flush = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            KEYSTROKE_FLUSH_SECONDS, self, "flushKeystrokes:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(flush, NSRunLoopCommonModes)
        self._flush_timer = flush

        # Mouse tracking: global move (throttled) + click monitoring, RAM
        # buffer -> Postgres every 60s (app/mouse.py), same shape as above.
        self.mouse_tracker.start()
        mouse_flush = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            MOUSE_FLUSH_SECONDS, self, "flushMouse:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(mouse_flush, NSRunLoopCommonModes)
        self._mouse_flush_timer = mouse_flush

        self._startup()
        return self

    def flushKeystrokes_(self, timer):
        self.tracker.flush()

    def flushMouse_(self, timer):
        self.mouse_tracker.flush()

    @objc.python_method
    def _hotkey_fired(self):
        self.togglePopover_(None)

    @objc.python_method
    def _handle_key_event(self, event):
        flags = event.modifierFlags()
        code = event.keyCode()
        if code == KEY_ESC and self.popover.isShown() and self._screen != "jobsearch":
            self.popover.close()
            return None
        hotkey_live = self._hotkey is not None and self._hotkey.ok
        if (not hotkey_live and code == KEY_SPACE
                and flags & NSEventModifierFlagControl
                and flags & NSEventModifierFlagShift):
            self.togglePopover_(None)
            return None
        return event

    # -- lifecycle -------------------------------------------------------

    @objc.python_method
    def _startup(self):
        self._db_ok = db.ping()
        if self._db_ok:
            self._generate_recurring(force=True)
            self._label_backlog_async()
        self._update_status_title()

    @objc.python_method
    def _generate_recurring(self, *, force=False):
        today = date.today()
        if not force and self._last_generated == today:
            return
        try:
            created = recurring.generate_due_tasks(today)
            self._last_generated = today
            if created:
                self._flash(f'+{len(created)} recurring task(s)')
                for task in created:
                    self._label_task_async(task["id"], task["description"])
        except Exception as exc:  # noqa: BLE001
            print(f"[recurring] {exc}")

    # -- Phase 5: LLM labels (app/llm_labels.py) -------------------------
    #
    # Both calls below run the actual Ollama round trip on a background
    # thread — it can take several seconds (longer on the very first call
    # after Ollama starts, while the model loads) and must never freeze the
    # popover. db.py serializes access to the shared connection, so it's
    # safe for that thread to read/write the database directly; the only
    # thing marshalled back to the main thread is the UI refresh.

    @objc.python_method
    def _label_task_async(self, task_id, description):
        """Classify one just-created task in the background and refresh the
        tasks-list window if it's the one currently on screen."""
        def worker():
            try:
                llm_labels.label_task(task_id, description)
            except Exception as exc:  # noqa: BLE001
                print(f"[llm_labels] task {task_id}: {exc}")
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "labelingDone:", None, False)
        threading.Thread(target=worker, daemon=True).start()

    @objc.python_method
    def _label_backlog_async(self):
        """Startup sweep: classify every task that doesn't have both labels
        yet (app/llm_labels.unlabeled_tasks) — created before Phase 5 existed,
        or left NULL by a previous failed call."""
        def worker():
            try:
                updated = llm_labels.label_unlabeled_tasks()
            except Exception as exc:  # noqa: BLE001
                print(f"[llm_labels] backlog sweep: {exc}")
                updated = []
            if updated:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "labelingDone:", None, False)
        threading.Thread(target=worker, daemon=True).start()

    def labelingDone_(self, arg):
        # runs on the main thread (see performSelectorOnMainThread_ above);
        # only worth a rebuild if the labels would actually be visible
        if self.popover.isShown() and self._screen == "tasklist":
            self.rebuild()

    # -- timer ----------------------------------------------------------

    def tick_(self, timer):
        self._ticks += 1
        if self.pomo.running and self.pomo.is_finished():
            self._pomodoro_finished()
        self._update_status_title()
        if self.popover.isShown() and self._screen == "tracking":
            self._render_header()
            period = 3 if not self._db_ok else 30
            if self._ticks % period == 0:
                self._db_ok = db.ping()
                if self._db_ok:
                    self._generate_recurring()
                self.rebuild()
        elif self.popover.isShown() and self._screen == "timer":
            self._render_timer()
        elif self.popover.isShown() and self._screen == "jobsearch":
            self._render_stopwatch()

    # -- popover open / close ------------------------------------------

    def togglePopover_(self, sender):
        if self.popover.isShown():
            if self._screen != "jobsearch":
                self.popover.close()
            return
        self._db_ok = db.ping()
        if self._db_ok:
            self._generate_recurring()
        self._cancel_transition()
        self._screen = "timer" if self.pomo.running else "tracking"
        self.rebuild()
        self._show_popover()

    @objc.python_method
    def _show_popover(self):
        """Bring the popover on screen (a no-op if it's already showing) and
        activate the app — used both for the manual toggle and to pop the
        break page up on its own when a pomodoro finishes."""
        if not self.popover.isShown():
            button = self.status_item.button()
            self.popover.showRelativeToRect_ofView_preferredEdge_(
                button.bounds(), button, NSMinYEdge
            )
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def popoverDidShow_(self, notification):
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown,
            self._handle_outside_click,
        )

    @objc.python_method
    def _handle_outside_click(self, event):
        # normally an outside click just dismisses the popover; on the job
        # search screen it instead shrinks it to a banner (still open — only
        # jobSearchBack_/jobSearchExpand_ leave or restore it from here)
        if self._screen == "jobsearch":
            if not self._jobsearch_compact:
                self._jobsearch_compact = True
                self.rebuild()
            return
        self.popover.performClose_(None)

    def popoverDidClose_(self, notification):
        self._cancel_transition()
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        self._screen = "tracking"

    # -- tracking-screen actions -----------------------------------

    def taskCheck_(self, sender):
        # the "[ ]" checkbox: complete the task, then hold the row on screen
        # (checked) for TASK_CHECK_STAND_S before fading it out over
        # TASK_CHECK_FADE_S — see taskAnimTick_. The rest of the list is
        # frozen (_build_tracking reuses _tracking_rows) until that finishes,
        # so nothing else jumps up until the row has actually disappeared.
        task_id = int(sender.tag())
        if task_id in self._completing:
            return
        try:
            tasks.complete_task(task_id)
            self._flash("task done ✓")
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
            self.rebuild()
            return
        self._completing[task_id] = {"start": time.monotonic()}
        self._start_task_anim()
        self.rebuild()

    def taskActivate_(self, sender):
        # clicking a task's name (not its checkbox) pins it as the active
        # task, replacing whichever task was pinned before it.
        task_id = int(sender.tag())
        if task_id in self._completing:
            return
        try:
            tasks.set_active_task(task_id)
            self._refresh_tracking_rows()
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
        self.rebuild()

    @objc.python_method
    def _refresh_tracking_rows(self):
        self._tracking_rows = tasks.open_tasks(limit=VISIBLE_TASKS)
        self._tracking_total = tasks.open_task_count()

    @objc.python_method
    def _fade_alpha(self, elapsed):
        if elapsed < TASK_CHECK_STAND_S:
            return 1.0
        t = elapsed - TASK_CHECK_STAND_S
        if t >= TASK_CHECK_FADE_S:
            return 0.0
        return 1.0 - t / TASK_CHECK_FADE_S

    @objc.python_method
    def _start_task_anim(self):
        if self._task_anim_timer is None:
            anim = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / 30, self, "taskAnimTick:", None, True
            )
            NSRunLoop.currentRunLoop().addTimer_forMode_(anim, NSRunLoopCommonModes)
            self._task_anim_timer = anim

    def taskAnimTick_(self, timer):
        finished = []
        for task_id, info in self._completing.items():
            elapsed = time.monotonic() - info["start"]
            fade = self._fade_alpha(elapsed)
            for btn, base_alpha, rgb in self._task_row_widgets.get(task_id, ()):
                color = _green(rgb, base_alpha * fade)
                btn.configure(btn._text, color, color)
            if elapsed >= TASK_CHECK_STAND_S + TASK_CHECK_FADE_S:
                finished.append(task_id)
        for task_id in finished:
            del self._completing[task_id]
        if finished:
            # the completed row(s) are gone from Postgres now — a real fetch
            # reflows the rest of the list up into their place
            self.rebuild()
        if not self._completing and self._task_anim_timer is not None:
            self._task_anim_timer.invalidate()
            self._task_anim_timer = None

    def openPomodoro_(self, sender):
        # the tracking screen's "Pomodoro" button: jump to whichever of the
        # focus/break timer is running (paused or not), starting a fresh one
        # if neither is — it navigates, it never stops or resets one (those
        # live on the timer screen's own buttons, so a running/paused break
        # can't be killed by accident).
        self._cancel_transition()
        if not self.pomo.running:
            pomodoro.start_work(self.pomo)
            self._flash("focus on.")
            self._update_status_title()
        self._screen = "timer"
        self.rebuild()

    def pauseResumePomodoro_(self, sender):
        # the timer screen's "stop"/"continue" toggle: freezes the countdown
        # in place (still running, just not ticking) rather than ending the
        # interval — the popover stays on the timer screen either way.
        if not self.pomo.running:
            return
        if self.pomo.paused:
            pomodoro.resume(self.pomo)
            self._flash("resumed")
        else:
            pomodoro.pause(self.pomo)
            self._flash("paused")
        self._update_status_title()
        self.rebuild()

    def resetPomodoro_(self, sender):
        # restart the current phase (focus or break) from its full duration,
        # discarding progress so far — nothing is logged.
        if not self.pomo.running:
            return
        pomodoro.reset(self.pomo)
        self._flash("reset")
        self._update_status_title()
        self.rebuild()

    def timerMainPage_(self, sender):
        # leave the Pomodoro running (or paused) and return to the main
        # tracking screen
        self._screen = "tracking"
        self.rebuild()

    def openStats_(self, sender):
        self._start_transition("stats")

    def openSettings_(self, sender):
        self._settings_err = ""
        self._start_transition("settings")

    # -- job search -------------------------------------------------
    # Deliberately "sticky": while this screen is up, the popover ignores
    # both the outside-click-to-dismiss monitor (popoverDidShow_) and Esc
    # (_handle_key_event) — only jobSearchBack_ can leave it. An outside click
    # shrinks it to a banner instead of closing it (_handle_outside_click);
    # clicking the banner (jobSearchExpand_) expands it back.

    def openJobSearch_(self, sender):
        self._cancel_transition()
        self._screen = "jobsearch"
        self.rebuild()

    def jobStopwatchStart_(self, sender):
        # this button doubles as pause: running -> bank the segment and stop;
        # idle -> start a fresh attempt (elapsed 0) or resume a paused one
        # (elapsed_accum already holds whatever was banked before the pause)
        if self._stopwatch_running:
            self._stopwatch_elapsed_accum += (
                time.monotonic() - self._stopwatch_started_monotonic)
            self._stopwatch_running = False
        else:
            if self._stopwatch_started_at is None:
                self._stopwatch_started_at = datetime.now(timezone.utc)
            self._stopwatch_started_monotonic = time.monotonic()
            self._stopwatch_running = True
        self.rebuild()

    def jobStopwatchReset_(self, sender):
        # back to 0 with nothing logged — unlike jobApplied_, no db write
        self._stopwatch_running = False
        self._stopwatch_started_monotonic = 0.0
        self._stopwatch_elapsed_accum = 0.0
        self._stopwatch_started_at = None
        self.rebuild()

    def jobApplied_(self, sender):
        current_segment = (time.monotonic() - self._stopwatch_started_monotonic
                            if self._stopwatch_running else 0.0)
        elapsed = self._stopwatch_elapsed_accum + current_segment
        started_at = self._stopwatch_started_at
        try:
            jobsearch.log_application(started_at, elapsed if started_at is not None else None)
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
            self.rebuild()
            return
        print(f"Job applied to in {elapsed / 60:.1f} min")
        # counter logged — the stopwatch resets and waits to be started again
        self._stopwatch_running = False
        self._stopwatch_started_monotonic = 0.0
        self._stopwatch_elapsed_accum = 0.0
        self._stopwatch_started_at = None
        self.rebuild()

    def jobApplicationRemove_(self, sender):
        # "-": undo the last "+" — doesn't touch the stopwatch, just the count
        try:
            removed = jobsearch.remove_last_application()
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
            self.rebuild()
            return
        if removed:
            self._flash("removed last application")
        self.rebuild()

    def outreachAdd_(self, sender):
        # "+" on the linkedin outreach counter — a plain per-day tally,
        # unlike jobApplied_ it doesn't touch the stopwatch
        try:
            jobsearch.log_outreach()
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
        self.rebuild()

    def outreachRemove_(self, sender):
        try:
            removed = jobsearch.remove_last_outreach()
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
            self.rebuild()
            return
        if removed:
            self._flash("removed last outreach")
        self.rebuild()

    def jobSearchBack_(self, sender):
        self._jobsearch_compact = False
        self._screen = "tracking"
        self.rebuild()

    def jobSearchExpand_(self, sender):
        # clicking the banner (only visible while compact) restores it
        self._jobsearch_compact = False
        self.rebuild()

    @objc.python_method
    def _render_stopwatch(self):
        if self._stopwatch_label is None:
            return
        if self._jobsearch_compact:
            self._stopwatch_label.set_text(self._jobsearch_banner_text())
        else:
            self._stopwatch_label.setStringValue_(self._stopwatch_text())

    # -- jobs table (from the stats screen) --------------------------

    def openJobsTable_(self, sender):
        self._jobtable_page = 0
        self._screen = "jobstable"
        self.rebuild()

    def jobsTableBack_(self, sender):
        self._screen = "stats"
        self.rebuild()

    def jobTableUp_(self, sender):
        self._jobtable_page = max(0, self._jobtable_page - 1)
        self.rebuild()

    def jobTableDown_(self, sender):
        self._jobtable_page += 1
        self.rebuild()

    # -- calendar -----------------------------------------------------

    def openCalendar_(self, sender):
        self._cancel_transition()
        today = date.today()
        self._cal_anchor = today if self._cal_mode == "day" else calendar_view.week_start(today)
        self._screen = "calendar"
        self.rebuild()

    def calendarBack_(self, sender):
        self._screen = "tracking"
        self.rebuild()

    def calendarMode_(self, sender):
        # tag 0 = day, 1 = week; switching to week re-anchors on that day's
        # Monday, switching back to day just shows whichever day is anchored
        mode = "day" if int(sender.tag()) == 0 else "week"
        if mode != self._cal_mode:
            if mode == "week":
                self._cal_anchor = calendar_view.week_start(self._cal_anchor)
            self._cal_mode = mode
            self.rebuild()

    def calendarPrev_(self, sender):
        step = timedelta(days=1 if self._cal_mode == "day" else 7)
        self._cal_anchor -= step
        self.rebuild()

    def calendarNext_(self, sender):
        step = timedelta(days=1 if self._cal_mode == "day" else 7)
        self._cal_anchor += step
        self.rebuild()

    def calendarToday_(self, sender):
        today = date.today()
        self._cal_anchor = today if self._cal_mode == "day" else calendar_view.week_start(today)
        self.rebuild()

    def quit_(self, sender):
        self.tracker.flush()
        self.tracker.stop()
        self.mouse_tracker.flush()
        self.mouse_tracker.stop()
        NSApplication.sharedApplication().terminate_(self)

    @objc.python_method
    def _pomodoro_finished(self):
        was_break = self.pomo.on_break
        pomodoro.stop(self.pomo)
        if was_break:
            self._flash("break over — back to it")
            if self._screen == "timer":
                self._screen = "tracking"
            self._update_status_title()
            if self.popover.isShown():
                self.rebuild()
        else:
            self._flash("pomodoro complete — break started")
            pomodoro.start_break(self.pomo)
            self._update_status_title()
            self._cancel_transition()
            self._screen = "timer"
            self.rebuild()
            # the break page comes up on its own, even if the popover was
            # closed or parked on another screen
            self._show_popover()

    # -- new-task form -------------------------------------------

    def openForm_(self, sender):
        self._form_err = ""
        self._reset_form()
        self._cancel_transition()
        self._screen = "form"
        self.rebuild()

    def cancelForm_(self, sender):
        self._cancel_transition()
        self._screen = "tracking"
        self.rebuild()

    # -- full task list -------------------------------------------

    def openTaskList_(self, sender):
        self._cancel_transition()
        # remember where we came from ("tracking" or "timer") so the "‹ back"
        # button below returns there instead of always going to tracking
        if self._screen in ("tracking", "timer"):
            self._tasklist_return_screen = self._screen
        self._screen = "tasklist"
        self.rebuild()

    def taskListBack_(self, sender):
        self._screen = self._tasklist_return_screen
        self.rebuild()

    def taskListComplete_(self, sender):
        # the "X" at the end of a row: permanently deletes the task (not a
        # completion — there's no undo, unlike the tracking screen's checkbox)
        task_id = int(sender.tag())
        try:
            tasks.delete_task(task_id)
            self._flash("task deleted")
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
        self.rebuild()

    def taskListClear_(self, sender):
        # "clear": wipes every open task. Recurring templates
        # (recurring_tasks) aren't touched, so a repeating task simply comes
        # back next time recurring.generate_due_tasks() is due to make it —
        # only today's already-generated instance is removed.
        try:
            removed = tasks.clear_open_tasks()
            self._flash(f"cleared {removed} task(s)")
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
        self.rebuild()

    # -- tasks-list drag-to-reorder ---------------------------------
    # Called directly by TaskRow's mouse handlers (plain python calls, not
    # objc action selectors — TaskRow just reports raw drag deltas; all the
    # reorder bookkeeping lives here).

    @objc.python_method
    def task_row_drag_began(self, row):
        self._drag_task_id = row.task_id
        self._drag_moved = False
        # bring the dragged row above its page-mates for the duration
        row.superview().addSubview_positioned_relativeTo_(row, NSWindowAbove, None)

    @objc.python_method
    def task_row_dragged(self, row, dy):
        if self._drag_task_id != row.task_id:
            return
        step = self._tasklist_row_step
        top = self._tasklist_row_top
        n = len(self._tasklist_row_order)
        new_y = max(top, min(top + (n - 1) * step, row._frame_origin_y + dy))
        frame = row.frame()
        row.setFrameOrigin_(NSMakePoint(frame.origin.x, new_y))

        slot = max(0, min(n - 1, int(round((new_y - top) / step))))
        cur_index = self._tasklist_row_order.index(row.task_id)
        if slot != cur_index:
            self._tasklist_row_order.pop(cur_index)
            self._tasklist_row_order.insert(slot, row.task_id)
            self._relayout_tasklist_rows(dragging_id=row.task_id)
            self._drag_moved = True
        self.view.setNeedsDisplay_(True)

    @objc.python_method
    def _relayout_tasklist_rows(self, dragging_id=None):
        # snap every row but the one under the cursor to its current slot,
        # and refresh each row's leading "#" now that ranks have shifted
        top = self._tasklist_row_top
        step = self._tasklist_row_step
        for i, task_id in enumerate(self._tasklist_row_order):
            view = self._tasklist_row_views.get(task_id)
            if view is None:
                continue
            view.set_order(i + 1)
            if task_id == dragging_id:
                continue
            frame = view.frame()
            view.setFrameOrigin_(NSMakePoint(frame.origin.x, top + i * step))

    @objc.python_method
    def task_row_drag_ended(self, row):
        if self._drag_task_id != row.task_id:
            return
        self._drag_task_id = None
        step = self._tasklist_row_step
        top = self._tasklist_row_top
        slot = self._tasklist_row_order.index(row.task_id)
        frame = row.frame()
        row.setFrameOrigin_(NSMakePoint(frame.origin.x, top + slot * step))

        if not self._drag_moved:
            return  # a plain click, not a drag — leave the stored order alone
        try:
            tasks.reorder_tasks(self._tasklist_row_order)
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
        self.rebuild()

    # -- settings (gear) screen ---------------------------------

    def saveSettings_(self, sender):
        try:
            work = float(self._s_work.stringValue().strip())
            brk = float(self._s_break.stringValue().strip())
        except ValueError:
            self._settings_err = "numbers only"
            self.rebuild()
            return
        if work < 1 or brk < 1:
            self._settings_err = "minimum 1 minute"
            self.rebuild()
            return
        config.set_pomodoro_durations(work, brk)
        self._settings_err = ""
        self._flash(f"pomodoro {work:g}m · break {brk:g}m")
        self._screen = "tracking"
        self.rebuild()

    def cancelSettings_(self, sender):
        self._cancel_transition()
        self._settings_err = ""
        self._screen = "tracking"
        self.rebuild()

    # -- screen transition (the digital-rain loader) -----------

    @objc.python_method
    def _start_transition(self, target):
        self._transition_target = target
        self._screen = "transition"
        self.view.set_rain(True)
        if self._anim_timer is None:
            anim = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / 16, self.view, "stepAnimation:", None, True
            )
            NSRunLoop.currentRunLoop().addTimer_forMode_(anim, NSRunLoopCommonModes)
            self._anim_timer = anim
        self.rebuild()
        self._transition_timer = (
            NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                TRANSITION_SECONDS, self, "endTransition:", None, False
            )
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._transition_timer, NSRunLoopCommonModes
        )

    def endTransition_(self, timer):
        self._transition_timer = None
        self._stop_rain()
        self._screen = self._transition_target
        self.rebuild()

    @objc.python_method
    def _stop_rain(self):
        if self._anim_timer is not None:
            self._anim_timer.invalidate()
            self._anim_timer = None
        self.view.set_rain(False)

    @objc.python_method
    def _cancel_transition(self):
        if self._transition_timer is not None:
            self._transition_timer.invalidate()
            self._transition_timer = None
        self._stop_rain()

    def submitForm_(self, sender):
        description = self._f_task.stringValue().strip()
        if not description:
            self._form_err = "type a task first"
            self.rebuild()
            self._focus(self._f_task)
            return
        try:
            rule = _REPEAT_RULES.get(self._f_repeat.titleOfSelectedItem())
            if rule:
                row = recurring.add_recurring(description, rule)
                self._generate_recurring(force=True)
                self._flash(f'repeating: {row["description"]}')
            else:
                deadline = self._resolve_deadline()
                row = tasks.add_task(description, deadline=deadline, manual=1)
                self._flash(f'added: {row["description"]}')
                self._label_task_async(row["id"], row["description"])
        except deadlines.DeadlineError as exc:
            self._form_err = exc.message
            self.rebuild()
            self._focus(self._f_entry)
            return
        except Exception as exc:  # noqa: BLE001
            self._form_err = str(exc)
            self.rebuild()
            return
        self._screen = "tracking"
        self.rebuild()

    @objc.python_method
    def _resolve_deadline(self):
        """The form's deadline as a datetime, or None. The named drop-down and
        the ``custom…`` text box are mutually exclusive; the time drop-down, if
        set, overrides the time part of either. Raises ``DeadlineError`` for a
        bad custom entry (caught by ``submitForm_`` and shown in the error line).
        """
        label = self._f_day.titleOfSelectedItem()
        time_choice = self._f_time.titleOfSelectedItem()
        has_time = time_choice not in (None, "", "—")

        if label == deadlines.CUSTOM_LABEL:
            text = self._f_entry.stringValue().strip()
            dt = deadlines.parse_entry(text) if text else None
        elif label not in (None, "", deadlines.NONE_LABEL):
            dt = deadlines.resolve(label)
        else:
            dt = None

        if dt is None:
            if not has_time:
                return None
            hh, mm = (int(x) for x in time_choice.split(":"))
            return datetime.combine(date.today(), dtime(hh, mm)).astimezone()

        if has_time:
            hh, mm = (int(x) for x in time_choice.split(":"))
            dt = dt.replace(hour=hh, minute=mm)
        return dt.astimezone()

    def deadlineChoice_(self, sender):
        # the "custom…" row shows / hides the free-text box
        self.rebuild()
        if sender.titleOfSelectedItem() == deadlines.CUSTOM_LABEL:
            self._focus(self._f_entry)

    def controlTextDidChange_(self, notification):
        # the custom-deadline box: auto-complete a unique weekday prefix
        # ("Mon" -> "Monday"), and when a backspace chews back into a completed
        # weekday word, drop the whole word so it can be retyped
        if notification.object() is not self._f_entry:
            return
        current = self._f_entry.stringValue()
        prev, self._f_entry_prev = self._f_entry_prev, current
        if current == prev:
            return
        new = deadlines.strip_completed_weekday(prev, current)
        if new is None:
            new = deadlines.autocomplete(current)
        if new is not None and new != current:
            self._set_entry_text(new)

    @objc.python_method
    def _set_entry_text(self, text):
        self._f_entry_prev = text
        editor = self._f_entry.currentEditor()
        if editor is not None:
            editor.setString_(text)
            editor.setSelectedRange_(NSMakeRange(len(text), 0))
        else:
            self._f_entry.setStringValue_(text)

    def control_textView_doCommandBySelector_(self, control, textView, selector):
        if selector == "cancelOperation:":
            if self._screen == "settings":
                self.cancelSettings_(None)
            else:
                self.cancelForm_(None)
            return True
        return False

    # -- stats screen ------------------------------------------

    def statsMetric_(self, sender):
        self._stats_metric = ("cpm", "diff", "mouse")[int(sender.tag())]
        self.rebuild()

    def statsWindow_(self, sender):
        # tag is the window length in minutes
        self._stats_window = int(sender.tag())
        self.rebuild()

    def cpmMA_(self, sender):
        # tag is the rolling-average window in minutes
        self._cpm_ma = int(sender.tag())
        self.rebuild()

    def diffCapMore_(self, sender):
        # widen the diff chart's x-axis by 250 ms; wrap back to the base once
        # it reaches 3 s so the single button stays a full round-trip
        self._diff_cap = (keys.DIFF_HIST_CAP_MS if self._diff_cap >= 3000
                          else self._diff_cap + 250)
        self.rebuild()

    def statsBack_(self, sender):
        self._screen = "tracking"
        self.rebuild()

    @objc.python_method
    def _ensure_chart(self):
        if self._chart is None:
            self._chart = ChartView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))

    @objc.python_method
    def _ensure_mouse_map(self):
        if self._mouse_map is None:
            self._mouse_map = MouseMapView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))

    @objc.python_method
    def _ensure_daytimeline(self):
        if self._daytimeline is None:
            self._daytimeline = DayTimelineView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))

    @objc.python_method
    def _ensure_weektimeline(self):
        if self._weektimeline is None:
            self._weektimeline = WeekTimelineView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))

    @objc.python_method
    def _ensure_form_controls(self):
        if self._f_task is not None:
            return
        self._f_task = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, ROW_H))
        self._f_task.setBezeled_(True)
        self._f_task.setDrawsBackground_(True)
        self._f_task.setBackgroundColor_(NSColor.colorWithDeviceWhite_alpha_(0.10, 0.92))
        self._f_task.setTextColor_(_green(_HEAD, 1.0))
        dark = _dark_appearance()
        if dark is not None:
            self._f_task.setAppearance_(dark)
        self._f_task.setFont_(_ui_font(12.5))
        self._f_task.setFocusRingType_(NSFocusRingTypeNone)
        self._f_task.setPlaceholderString_("what needs doing")
        self._f_task.setTarget_(self)
        self._f_task.setAction_("submitForm:")
        self._f_task.setDelegate_(self)

        self._f_repeat = self._popup(_REPEAT_ITEMS)
        self._f_day = self._popup(
            (deadlines.NONE_LABEL, *deadlines.option_labels(), deadlines.CUSTOM_LABEL)
        )
        self._f_day.setTarget_(self)
        self._f_day.setAction_("deadlineChoice:")
        self._f_time = self._popup(_TIME_ITEMS)

        self._f_entry = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, ROW_H))
        self._f_entry.setBezeled_(True)
        self._f_entry.setDrawsBackground_(True)
        self._f_entry.setBackgroundColor_(NSColor.colorWithDeviceWhite_alpha_(0.10, 0.92))
        self._f_entry.setTextColor_(_green(_HEAD, 1.0))
        if dark is not None:
            self._f_entry.setAppearance_(dark)
        self._f_entry.setFont_(_ui_font(12.5))
        self._f_entry.setFocusRingType_(NSFocusRingTypeNone)
        self._f_entry.setPlaceholderString_("e.g. Monday  ·  Monday 24")
        self._f_entry.setTarget_(self)
        self._f_entry.setAction_("submitForm:")
        self._f_entry.setDelegate_(self)

    @objc.python_method
    def _ensure_settings_controls(self):
        if self._s_work is not None:
            return
        self._s_work = self._num_field()
        self._s_break = self._num_field()

    @objc.python_method
    def _num_field(self):
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, ROW_H))
        field.setBezeled_(True)
        field.setDrawsBackground_(True)
        field.setBackgroundColor_(NSColor.colorWithDeviceWhite_alpha_(0.10, 0.92))
        field.setTextColor_(_green(_HEAD, 1.0))
        dark = _dark_appearance()
        if dark is not None:
            field.setAppearance_(dark)
        field.setFont_(_ui_font(12.5))
        field.setAlignment_(NSTextAlignmentCenter)
        field.setFocusRingType_(NSFocusRingTypeNone)
        field.setTarget_(self)
        field.setAction_("saveSettings:")
        field.setDelegate_(self)
        return field

    @objc.python_method
    def _popup(self, items):
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 10, ROW_H), False
        )
        popup.setFont_(_ui_font(11.5))
        attrs = {NSFontAttributeName: _ui_font(11.5),
                 NSForegroundColorAttributeName: _green(_BODY, 1.0)}
        for title in items:
            popup.addItemWithTitle_(title)
            popup.lastItem().setAttributedTitle_(
                NSAttributedString.alloc().initWithString_attributes_(title, attrs)
            )
        popup.selectItemAtIndex_(0)
        dark = _dark_appearance()
        if dark is not None:
            popup.setAppearance_(dark)
        return popup

    @objc.python_method
    def _reset_form(self):
        self._ensure_form_controls()
        self._f_task.setStringValue_("")
        self._f_entry.setStringValue_("")
        self._f_entry_prev = ""
        for popup in (self._f_repeat, self._f_day, self._f_time):
            popup.selectItemAtIndex_(0)

    @objc.python_method
    def _focus(self, responder):
        window = self.view.window()
        if window is not None:
            window.makeFirstResponder_(responder)

    # -- flash line ------------------------------------------------

    @objc.python_method
    def _flash(self, message):
        self._flash_msg = message
        self._flash_until = time.monotonic() + 4.0
        if self.popover.isShown() and self._screen == "tracking":
            self._render_header()

    @objc.python_method
    def _flash_text(self):
        if self._flash_msg and time.monotonic() < self._flash_until:
            return self._flash_msg
        return ""

    # -- status bar title ----------------------------------------

    @objc.python_method
    def _update_status_title(self):
        button = self.status_item.button()
        if self.pomo.running:
            secs = self.pomo.remaining_seconds()
            if self.pomo.paused:
                icon = "⏸"
            else:
                icon = BREAK_ICON if self.pomo.on_break else IDLE_ICON
            button.setTitle_(f"{icon} {secs // 60:d}:{secs % 60:02d}")
        else:
            button.setTitle_(IDLE_ICON)

    # -- view construction -------------------------------------

    @objc.python_method
    def _label(self, frame, text, *, size=12.5, rgb=_BODY, alpha=0.95, lines=1,
               align=None):
        field = NSTextField.alloc().initWithFrame_(frame)
        field.setBezeled_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setDrawsBackground_(False)
        field.setFont_(_ui_font(size))
        field.setTextColor_(_green(rgb, alpha))
        field.setStringValue_(text)
        if lines > 1:
            field.setUsesSingleLineMode_(False)
            field.cell().setWraps_(True)
        if align is not None:
            field.setAlignment_(align)
        return field

    @objc.python_method
    def _button(self, frame, text, action, *, rgb=_BODY, alpha=0.95,
                hover_rgb=_HEAD, tag=None, align=None):
        btn = TermButton.alloc().initWithFrame_(frame)
        btn.configure(text, _green(rgb, alpha), _green(hover_rgb, min(1.0, alpha + 0.45)),
                      align=align)
        btn.setTarget_(self)
        btn.setAction_(action)
        if tag is not None:
            btn.setTag_(tag)
        return btn

    @objc.python_method
    def _header_text(self):
        if not self._db_ok:
            return "PRODUCTIVITY\n  ▒ DATABASE OFFLINE\n  reconnecting…"
        try:
            total = tasks.open_task_count()
            sessions = pomodoro.sessions_today()
        except Exception as exc:  # noqa: BLE001
            self._db_ok = False
            return f"PRODUCTIVITY\n  db error: {exc}"
        if self.pomo.running:
            secs = self.pomo.remaining_seconds()
            phase = "break" if self.pomo.on_break else "focus"
            paused = "paused " if self.pomo.paused else ""
            state = f'{paused}{phase} {secs // 60}:{secs % 60:02d}'
        else:
            state = "idle"
        line3 = self._flash_text() or f"● {state}"
        return f"PRODUCTIVITY\n  {total} open · {sessions} pomo today\n  {line3}"

    @objc.python_method
    def _render_header(self):
        if self._header is not None:
            self._header.setStringValue_(self._header_text())

    @objc.python_method
    def _render_timer(self):
        if self._timer_label is not None:
            secs = self.pomo.remaining_seconds()
            self._timer_label.setStringValue_(f"{secs // 60:d}:{secs % 60:02d}")

    @objc.python_method
    def rebuild(self):
        for sub in list(self.view.subviews()):
            sub.removeFromSuperview()
        separators: list[float] = []
        self._content_w = PANEL_W    # _build_tasklist widens this; nothing else overrides it
        y = PAD
        if self._screen == "form":
            y = self._build_form(y, separators)
        elif self._screen == "stats":
            y = self._build_stats(y, separators)
        elif self._screen == "settings":
            y = self._build_settings(y, separators)
        elif self._screen == "timer":
            y = self._build_timer(y, separators)
        elif self._screen == "tasklist":
            y = self._build_tasklist(y, separators)
        elif self._screen == "jobsearch":
            y = self._build_jobsearch(y, separators)
        elif self._screen == "jobstable":
            y = self._build_jobstable(y, separators)
        elif self._screen == "calendar":
            y = self._build_calendar(y, separators)
        elif self._screen == "transition":
            y = self._build_transition(y)
        else:
            y = self._build_tracking(y, separators)

        self.view.separators = separators
        size = NSMakeSize(self._content_w, y)
        self.popover.setContentSize_(size)
        self.view.setFrameSize_(size)
        self.view.setNeedsDisplay_(True)
        if self._screen == "form":
            self._focus(self._f_task)
        elif self._screen == "settings":
            self._focus(self._s_work)

    @objc.python_method
    def _build_transition(self, y):
        # Nothing but rain; a faint status line at the bottom.
        self._header = None
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, SHEET_H - PAD - 16, PANEL_W - 2 * PAD, 16),
            f"> loading {self._transition_target} …", size=11, rgb=_BODY, alpha=0.45))
        return SHEET_H

    @objc.python_method
    def _clock_xticks(self, window):
        """Absolute time-of-day ticks for the cpm x-axis, looking back ``window``
        minutes from now, spaced so a handful of ticks fit regardless of span:
        30 min up to 2h, 1h up to 12h, 3h across a day, 6h across 2 days, and
        1 day (labelled by weekday) across a week.
        Returns [(fraction_along_x, label), …] aligned to round clock times."""
        if window <= 120:
            step, fmt = 30, "%H:%M"
        elif window <= 720:
            step, fmt = 60, "%H:%M"
        elif window <= 1440:
            step, fmt = 180, "%H:%M"
        elif window <= 2880:
            step, fmt = 360, "%a %H:%M"
        else:
            step, fmt = 1440, "%a"
        now = datetime.now()
        start = now - timedelta(minutes=window)
        start_min = start.hour * 60 + start.minute
        first_min = ((start_min // step) + 1) * step
        tick = start.replace(second=0, microsecond=0) + timedelta(
            minutes=first_min - start_min)
        span = window * 60
        ticks = []
        while tick <= now:
            frac = (tick - start).total_seconds() / span
            ticks.append((frac, tick.strftime(fmt)))
            tick += timedelta(minutes=step)
        return ticks

    @objc.python_method
    def _build_stats(self, y, separators):
        # No flush here: the stats screen just reads whatever's already in
        # Postgres. Flushing on every rebuild used to write to the db on every
        # click (metric/window/MA toggles all rebuild); now the buffer only
        # ever drains on the once-a-minute flushKeystrokes_ timer, so a value
        # typed in the last few seconds may not show up until that tick.
        self._header = None

        subtitle = "mouse activity" if self._stats_metric == "mouse" else "keystroke activity"
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 36),
            f"STATS\n  {subtitle}", size=13, rgb=_HEAD, alpha=1.0, lines=2))
        y += 42
        separators.append(y)
        y += 14

        metric = self._stats_metric
        window = self._stats_window
        wlabel = _WINDOW_LABELS.get(window, f"{window} min")
        x_ticks = None
        overlay = None
        y_step = 0
        overflow = None
        if metric == "cpm":
            bucket = _WINDOW_BUCKETS.get(window, 1)
            values = keys.cpm_series(window, bucket)
            vmax = max(values) if values else 1
            top = ""
            # _cpm_ma is a duration in minutes; scale it to bars since each
            # bar covers `bucket` minutes once the window is bucketed
            overlay = keys.rolling_average(values, max(1, self._cpm_ma // bucket))
            y_step = 50
            x_ticks = self._clock_xticks(window)
            caption = ""
            empty = f"no characters typed in the last {wlabel}"
            if not self.tracker.permitted:
                caption = "grant Accessibility permission + restart — see console"
        elif metric == "diff":
            # distribution of the gap between consecutive keystrokes over the
            # same window as cpm — x-axis is gap duration, not clock time
            counts, edges, total, over = keys.interkey_diff_histogram(
                window, cap_ms=self._diff_cap)
            values = counts
            vmax = max(counts) if counts else 1
            top = ""
            caption = ""
            empty = "no keystrokes recorded yet"
            overflow = (over, f"{self._diff_cap:.0f}+")
            # x fractions run against n+1 slots — the last slot is the overflow
            # bar, labelled on top, so the axis ticks stop at 3/4 of the bins
            n = len(counts)
            x_ticks = [(i / (n + 1), f"{edges[i]:.0f}")
                       for i in (n // 4, n // 2, 3 * n // 4)]
            if not self.tracker.permitted:
                caption = "grant Accessibility permission + restart — see console"
        else:  # "mouse" — spatial scatter of positions, not a bar chart
            mouse_points = mouse.points(window)
            screen = NSScreen.mainScreen()
            sw, sh = (screen.frame().size.width, screen.frame().size.height) \
                if screen is not None else (1920.0, 1080.0)
            caption = f"{len(mouse_points)} points · {wlabel}"
            empty = f"no mouse activity in the last {wlabel}"
            if not self.mouse_tracker.permitted:
                caption = "grant Accessibility permission + restart — see console"

        chart_h = 150
        if metric == "mouse":
            self._ensure_mouse_map()
            self._mouse_map.setFrame_(NSMakeRect(PAD, y, PANEL_W - 2 * PAD, chart_h))
            self._mouse_map.set_data(mouse_points, sw, sh, caption, empty)
            self.view.addSubview_(self._mouse_map)
        else:
            self._ensure_chart()
            self._chart.setFrame_(NSMakeRect(PAD, y, PANEL_W - 2 * PAD, chart_h))
            self._chart.set_data(values, vmax, top, caption, empty, x_ticks, overlay,
                                 y_step, overflow)
            self.view.addSubview_(self._chart)

        # legend over the graph's top-left, styled like the task gradient
        ly = y + 4
        for i, name in enumerate(("cpm", "diff", "mouse")):
            selected = (metric == name)
            self.view.addSubview_(self._button(
                NSMakeRect(PAD + 8, ly, 84, 18), name, "statsMetric:",
                rgb=_HEAD if selected else _BODY,
                alpha=1.0 if selected else 0.30, tag=i))
            ly += 19
        # not a metric of this chart — navigates to the jobs log table instead
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + 8, ly, 84, 18), "jobs", "openJobsTable:",
            rgb=_BODY, alpha=0.30))

        # diff only: a ">" at the chart's top-right widens the x-axis by 250 ms
        if metric == "diff":
            self.view.addSubview_(self._button(
                NSMakeRect(PANEL_W - PAD - 26, y + 4, 22, 18), "›",
                "diffCapMore:", rgb=_BODY, alpha=0.8))

        # cpm only: rolling-average window buttons at the chart's top-right,
        # styled like the metric legend
        if metric == "cpm":
            mw = 22
            mx = PANEL_W - PAD - len(_CPM_MA_CHOICES) * mw
            for choice in _CPM_MA_CHOICES:
                selected = (self._cpm_ma == choice)
                self.view.addSubview_(self._button(
                    NSMakeRect(mx, y + 4, mw, 18), str(choice), "cpmMA:",
                    rgb=_HEAD if selected else _BODY,
                    alpha=1.0 if selected else 0.30, tag=choice))
                mx += mw
        y += chart_h + 12

        separators.append(y)
        y += 14
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, 80, ROW_H), "‹ back", "statsBack:",
            rgb=_BODY, alpha=0.8))

        # look-back window selector, bottom-right, styled like the metric legend
        bw = 30
        bx = PANEL_W - PAD - len(_WINDOWS) * bw
        for label, minutes, _bucket in _WINDOWS:
            selected = (self._stats_window == minutes)
            self.view.addSubview_(self._button(
                NSMakeRect(bx, y, bw, ROW_H), label, "statsWindow:",
                rgb=_HEAD if selected else _BODY,
                alpha=1.0 if selected else 0.30, tag=minutes))
            bx += bw
        return SHEET_H

    @objc.python_method
    def _build_tracking(self, y, separators):
        self._header = self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD - 26, 54),
            self._header_text(), size=13, rgb=_HEAD, alpha=1.0, lines=3,
        )
        self.view.addSubview_(self._header)
        # gear: open the Pomodoro-timing settings screen
        self.view.addSubview_(self._button(
            NSMakeRect(PANEL_W - PAD - 22, y + 2, 22, 20), "⚙", "openSettings:",
            rgb=_BODY, alpha=0.55))
        y += 60
        separators.append(y)
        y += 12

        rows: list[dict] = []
        total = 0
        if self._db_ok:
            try:
                # while a row is checked-off and animating, keep showing the
                # list as it was when that started — a fresh fetch would drop
                # the completed task (done >= 1) and reflow everyone early
                if not self._completing:
                    self._refresh_tracking_rows()
                rows, total = self._tracking_rows, self._tracking_total
            except Exception:  # noqa: BLE001
                self._db_ok = False

        self._task_row_widgets = {}
        if not self._db_ok:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H),
                "// no connection — reconnecting…", rgb=_BODY, alpha=0.7))
            y += ROW_H
        elif rows:
            cb_w = 34
            for i, task in enumerate(rows):
                task_id = int(task["id"])
                active = i == 0
                checked = task_id in self._completing
                prefix = "▸ " if active else "  "
                rgb = _HEAD if active else _BODY
                base_alpha = GRADIENT[i]
                alpha = base_alpha
                if checked:
                    elapsed = time.monotonic() - self._completing[task_id]["start"]
                    alpha = base_alpha * self._fade_alpha(elapsed)

                # "[ ]" checkbox: click completes the task (taskCheck_)
                cb = self._button(
                    NSMakeRect(PAD, y, cb_w, ROW_H), _checkbox_glyph(checked),
                    "taskCheck:", rgb=rgb, alpha=alpha, hover_rgb=_HEAD, tag=task_id,
                )
                self.view.addSubview_(cb)
                # task name: click pins it as the active task (taskActivate_)
                name_btn = self._button(
                    NSMakeRect(PAD + cb_w, y, PANEL_W - 2 * PAD - cb_w, ROW_H),
                    prefix + _task_line(task, brief=not active),
                    "taskActivate:", rgb=rgb, alpha=alpha, hover_rgb=_HEAD, tag=task_id,
                )
                self.view.addSubview_(name_btn)

                # not setEnabled_(False): AppKit's default disabled-dimming
                # would fight the custom fade colors taskAnimTick_ paints.
                # taskCheck_/taskActivate_ already no-op while checked, via
                # the `task_id in self._completing` guard.
                self._task_row_widgets[task_id] = (
                    (cb, base_alpha, rgb), (name_btn, base_alpha, rgb))
                y += ROW_H + 1
            # final line: how many tasks are still queued behind the visible
            # ones — plain text now; the "Tasks" button below opens the full
            # list window instead.
            queued = max(0, total - len(rows))
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H),
                f"   {queued} more in queue", rgb=_BODY, alpha=GRADIENT[3]))
            y += ROW_H
        else:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H),
                "// no open tasks", rgb=_BODY, alpha=0.7))
            y += ROW_H

        y += 8
        separators.append(y)
        y += 14

        half = (PANEL_W - 2 * PAD - 12) / 2
        grid = [
            ("Tasks", "openTaskList:"),
            ("📅 Calendar", "openCalendar:"),
            ("▶ Pomodoro", "openPomodoro:"),
            ("▚ stats", "openStats:"),
            ("⌕ job search", "openJobSearch:"),
            ("⏻ quit", "quit:"),
        ]
        for i, (text, action) in enumerate(grid):
            col = i % 2
            x = PAD + col * (half + 12)
            self.view.addSubview_(self._button(
                NSMakeRect(x, y, half, ROW_H), text, action))
            if col == 1 or i == len(grid) - 1:
                y += ROW_H + 6
        y += 2
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 14),
            "ctrl+shift+space toggle · esc close", size=10, rgb=_BODY, alpha=0.30))
        y += 14
        return y + PAD - 6

    @objc.python_method
    def _build_form(self, y, separators):
        self._header = None
        self._ensure_form_controls()

        head = "NEW TASK" + (f"   ⚠ {self._form_err}" if self._form_err else "")
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 36), head + "\n  enter / done saves · esc cancels",
            size=13, rgb=_HEAD, alpha=1.0, lines=2))
        y += 44
        separators.append(y)
        y += 14

        def field_label(text):
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 16), text,
                size=11, rgb=_BODY, alpha=0.65))

        field_label("task")
        y += 18
        self._f_task.setFrame_(NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H))
        self.view.addSubview_(self._f_task)
        y += ROW_H + 12

        field_label("repeat every  (optional)")
        y += 18
        self._f_repeat.setFrame_(NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H))
        self.view.addSubview_(self._f_repeat)
        y += ROW_H + 12

        field_label("deadline  (optional — pick / type a day, time overrides)")
        y += 18
        half = (PANEL_W - 2 * PAD - 12) / 2
        self._f_day.setFrame_(NSMakeRect(PAD, y, half, ROW_H))
        self._f_time.setFrame_(NSMakeRect(PAD + half + 12, y, half, ROW_H))
        self.view.addSubview_(self._f_day)
        self.view.addSubview_(self._f_time)
        y += ROW_H + 12

        if self._f_day.titleOfSelectedItem() == deadlines.CUSTOM_LABEL:
            field_label('day — "Monday" (next one) or "Monday 24"')
            y += 18
            self._f_entry.setFrame_(NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H))
            self.view.addSubview_(self._f_entry)
            y += ROW_H + 12
        y += 6

        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, half, ROW_H), "done", "submitForm:",
            rgb=_HEAD, alpha=1.0))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + half + 12, y, half, ROW_H), "cancel", "cancelForm:",
            rgb=_BODY, alpha=0.7))
        y += ROW_H
        return max(SHEET_H, y + PAD)

    @objc.python_method
    def _build_tasklist(self, y, separators):
        # every open task as a compact table, all shown at once (no paging) —
        # the popover grows to fit them all. Wider than the other screens
        # (TASKLIST_PANEL_W) to fit the category/energy columns.
        self._header = None
        self._content_w = TASKLIST_PANEL_W
        table_w = self._content_w - 2 * PAD
        rule = "-" * _TABLE_ROW_W

        rows: list[dict] = []
        if self._db_ok:
            try:
                rows = tasks.open_tasks_full()
            except Exception:  # noqa: BLE001
                self._db_ok = False

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 20),
            f"TASKS ({len(rows)})", size=13, rgb=_HEAD, alpha=1.0))
        y += 26
        separators.append(y)
        y += 12

        total = len(rows)

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 12), rule, size=10, rgb=_BODY, alpha=0.4))
        y += 13
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 16),
            _table_row("#", "Task", "Deadline", "Category", "Energy"),
            size=11, rgb=_HEAD, alpha=0.85))
        y += 16
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 12), rule, size=10, rgb=_BODY, alpha=0.4))
        y += 16

        self._tasklist_row_order = []
        self._tasklist_row_views = {}
        if not rows:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, table_w, ROW_H),
                "// no open tasks" if self._db_ok else "// no connection — reconnecting…",
                rgb=_BODY, alpha=0.7))
            y += ROW_H
        else:
            # click-and-drag a row to reorder it among the others; the new
            # order is written back once the drag ends (task_row_drag_ended).
            # the row leaves room for an "X" at the end to delete it on the spot.
            x_w, x_gap = 20, 4
            row_w = table_w - x_w - x_gap
            self._tasklist_row_top = y
            self._tasklist_row_step = 16.0
            for i, task in enumerate(rows):
                task_id = int(task["id"])
                deadline = task.get("deadline")
                deadline_s = f"{deadline:%m/%d}" if deadline else "—"
                # Phase 5: LLM labels — "—" until the background classify
                # call (app/llm_labels.py) fills them in
                category_s = task.get("category") or "—"
                energy_s = task.get("energy") or "—"
                row = TaskRow.alloc().initWithFrame_(NSMakeRect(PAD, y, row_w, 16))
                row.configure(self, task_id, task["description"], deadline_s,
                              category_s, energy_s, i + 1,
                              _BODY, 0.9)
                self.view.addSubview_(row)
                self._tasklist_row_order.append(task_id)
                self._tasklist_row_views[task_id] = row
                self.view.addSubview_(self._button(
                    NSMakeRect(PAD + row_w + x_gap, y, x_w, 16), "X",
                    "taskListComplete:", rgb=_BODY, alpha=0.5, hover_rgb=_HEAD,
                    tag=task_id))
                y += 16

        y += 10
        separators.append(y)
        y += 14
        third = (table_w - 2 * 10) / 3
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, third, ROW_H), "‹ back", "taskListBack:",
            rgb=_BODY, alpha=0.8))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + third + 10, y, third, ROW_H), "clear", "taskListClear:",
            rgb=_BODY, alpha=0.55 if total else 0.25))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + 2 * (third + 10), y, third, ROW_H), "+ add", "openForm:",
            rgb=_HEAD, alpha=0.95))
        y += ROW_H
        return max(SHEET_H, y + PAD)

    @objc.python_method
    def _stopwatch_text(self):
        current_segment = (time.monotonic() - self._stopwatch_started_monotonic
                            if self._stopwatch_running else 0.0)
        secs = int(self._stopwatch_elapsed_accum + current_segment)
        return f"{secs // 60:d}:{secs % 60:02d}"

    @objc.python_method
    def _jobsearch_banner_text(self):
        return f"⌕ job search   {self._stopwatch_text()}"

    @objc.python_method
    def _build_jobsearch(self, y, separators):
        self._header = None
        if self._jobsearch_compact:
            return self._build_jobsearch_compact()

        applied = outreach = 0
        if self._db_ok:
            try:
                applied = jobsearch.count_today()
                outreach = jobsearch.outreach_count_today()
            except Exception:  # noqa: BLE001
                self._db_ok = False

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 36),
            "JOB SEARCH\n  applications & outreach", size=13, rgb=_HEAD,
            alpha=1.0, lines=2))
        y += 42
        separators.append(y)
        y += 14

        # two side-by-side counters, each its own "-" / count / "+" — jobs
        # applied (left, tied to the stopwatch below via jobApplied_) and
        # linkedin outreach (right, a plain per-day tally)
        col_gap = 12
        col_w = (PANEL_W - 2 * PAD - col_gap) / 2
        col1_x, col2_x = PAD, PAD + col_w + col_gap

        self.view.addSubview_(self._label(
            NSMakeRect(col1_x, y, col_w, 28), "jobs\napplied", size=11.5,
            rgb=_HEAD, alpha=0.6, lines=2, align=NSTextAlignmentCenter))
        self.view.addSubview_(self._label(
            NSMakeRect(col2_x, y, col_w, 28), "linkedin\noutreach", size=11.5,
            rgb=_HEAD, alpha=0.6, lines=2, align=NSTextAlignmentCenter))
        y += 32

        count_h, side_w = 38, 28
        side_y = y + (count_h - side_w) / 2
        self.view.addSubview_(self._button(
            NSMakeRect(col1_x, side_y, side_w, side_w), "-",
            "jobApplicationRemove:", rgb=_BODY,
            alpha=0.85 if applied > 0 else 0.3, align=NSTextAlignmentCenter))
        self.view.addSubview_(self._label(
            NSMakeRect(col1_x, y, col_w, count_h), str(applied), size=28,
            rgb=_HEAD, alpha=1.0, align=NSTextAlignmentCenter))
        self.view.addSubview_(self._button(
            NSMakeRect(col1_x + col_w - side_w, side_y, side_w, side_w), "+",
            "jobApplied:", rgb=_HEAD, alpha=1.0, align=NSTextAlignmentCenter))

        self.view.addSubview_(self._button(
            NSMakeRect(col2_x, side_y, side_w, side_w), "-",
            "outreachRemove:", rgb=_BODY,
            alpha=0.85 if outreach > 0 else 0.3, align=NSTextAlignmentCenter))
        self.view.addSubview_(self._label(
            NSMakeRect(col2_x, y, col_w, count_h), str(outreach), size=28,
            rgb=_HEAD, alpha=1.0, align=NSTextAlignmentCenter))
        self.view.addSubview_(self._button(
            NSMakeRect(col2_x + col_w - side_w, side_y, side_w, side_w), "+",
            "outreachAdd:", rgb=_HEAD, alpha=1.0, align=NSTextAlignmentCenter))
        y += count_h + 4

        if self._stopwatch_running:
            status_text, status_rgb, status_alpha = "● running", _HEAD, 0.8
        elif self._stopwatch_started_at is not None:
            status_text, status_rgb, status_alpha = "‖ paused", _HEAD, 0.6
        else:
            status_text, status_rgb, status_alpha = "stopwatch", _BODY, 0.5
        self.view.addSubview_(self._label(
            NSMakeRect(0, y, PANEL_W, 14), status_text, size=10,
            rgb=status_rgb, alpha=status_alpha, align=NSTextAlignmentCenter))
        y += 16

        self._stopwatch_label = self._label(
            NSMakeRect(0, y, PANEL_W, 40), self._stopwatch_text(), size=30,
            rgb=_HEAD, alpha=1.0, align=NSTextAlignmentCenter)
        self.view.addSubview_(self._stopwatch_label)
        y += 44

        separators.append(y)
        y += 14
        half = (PANEL_W - 2 * PAD - 12) / 2
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, half, ROW_H),
            "⏸ pause" if self._stopwatch_running else "▶ start",
            "jobStopwatchStart:", rgb=_BODY, alpha=0.85))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + half + 12, y, half, ROW_H), "↺ reset",
            "jobStopwatchReset:", rgb=_BODY, alpha=0.85))
        y += ROW_H + 8

        separators.append(y)
        y += 14
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, 80, ROW_H), "‹ back", "jobSearchBack:",
            rgb=_BODY, alpha=0.8))
        y += ROW_H
        return SHEET_H

    @objc.python_method
    def _build_jobsearch_compact(self):
        # a notification-banner-sized strip (full width, short) shown after
        # an outside click (_handle_outside_click); click it to expand back —
        # there's no back button here on purpose, that's the only action.
        h = 64
        banner = self._button(
            NSMakeRect(PAD, (h - ROW_H) / 2, PANEL_W - 2 * PAD, ROW_H),
            self._jobsearch_banner_text(), "jobSearchExpand:",
            rgb=_HEAD, alpha=0.95, align=NSTextAlignmentCenter)
        self.view.addSubview_(banner)
        self._stopwatch_label = banner
        return h

    @objc.python_method
    def _build_jobstable(self, y, separators):
        # every logged application as a compact table, paged like _build_tasklist
        self._header = None
        table_w = PANEL_W - 2 * PAD
        rule = "-" * _JOB_TABLE_ROW_W

        rows: list[dict] = []
        if self._db_ok:
            try:
                rows = jobsearch.list_applications()
            except Exception:  # noqa: BLE001
                self._db_ok = False

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 20),
            f"JOBS ({len(rows)})", size=13, rgb=_HEAD, alpha=1.0))
        y += 26
        separators.append(y)
        y += 12

        total = len(rows)
        page_count = max(1, (total + JOBTABLE_PAGE_SIZE - 1) // JOBTABLE_PAGE_SIZE)
        self._jobtable_page = max(0, min(self._jobtable_page, page_count - 1))
        start = self._jobtable_page * JOBTABLE_PAGE_SIZE
        page_rows = rows[start:start + JOBTABLE_PAGE_SIZE]

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 12), rule, size=10, rgb=_BODY, alpha=0.4))
        y += 13
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 16),
            _job_row("id", "started", "finished", "taken"),
            size=11, rgb=_HEAD, alpha=0.85))
        y += 16
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 12), rule, size=10, rgb=_BODY, alpha=0.4))
        y += 16

        if not page_rows:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, table_w, ROW_H),
                "// no applications logged yet" if self._db_ok
                else "// no connection — reconnecting…",
                rgb=_BODY, alpha=0.7))
            y += ROW_H
        else:
            for job in page_rows:
                started = job.get("started_at")
                finished = job.get("finished_at")
                minutes = job.get("minutes")
                started_s = f"{started:%m/%d %H:%M}" if started else "—"
                finished_s = f"{finished:%m/%d %H:%M}" if finished else "—"
                taken_s = f"{float(minutes):.1f}m" if minutes is not None else "—"
                self.view.addSubview_(self._label(
                    NSMakeRect(PAD, y, table_w, 16),
                    _job_row(job["id"], started_s, finished_s, taken_s),
                    size=11, rgb=_BODY, alpha=0.9))
                y += 16

        y += 10
        separators.append(y)
        y += 12

        half = (PANEL_W - 2 * PAD - 12) / 2
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, half, 20), "▴ up", "jobTableUp:",
            rgb=_BODY, alpha=0.85 if self._jobtable_page > 0 else 0.25))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + half + 12, y, half, 20), "▾ down", "jobTableDown:",
            rgb=_BODY, alpha=0.85 if self._jobtable_page < page_count - 1 else 0.25))
        y += 24
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 12),
            f"page {self._jobtable_page + 1}/{page_count}", size=9,
            rgb=_BODY, alpha=0.4, align=NSTextAlignmentCenter))
        y += 16

        separators.append(y)
        y += 14
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, 80, ROW_H), "‹ back", "jobsTableBack:",
            rgb=_BODY, alpha=0.8))
        y += ROW_H
        return SHEET_H

    @objc.python_method
    def _calendar_range_label(self):
        today = date.today()
        if self._cal_mode == "day":
            d = self._cal_anchor
            suffix = "  ·  today" if d == today else ""
            return f"{d:%a, %b %d}{suffix}"
        start = self._cal_anchor
        end = start + timedelta(days=6)
        if (start.year, start.month) == (end.year, end.month):
            span = f"{start:%b %d} – {end:%d}"
        elif start.year == end.year:
            span = f"{start:%b %d} – {end:%b %d}"
        else:
            span = f"{start:%b %d, %Y} – {end:%b %d, %Y}"
        this_week = calendar_view.week_start(today) == start
        return f"{span}{'  ·  this week' if this_week else ''}"

    @objc.python_method
    def _build_calendar(self, y, separators):
        self._header = None
        # the week grid needs 7 columns' worth of room; the day view fits
        # the normal panel width
        self._content_w = CALENDAR_WEEK_PANEL_W if self._cal_mode == "week" else PANEL_W
        content_w = self._content_w
        table_w = content_w - 2 * PAD

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, table_w, 20),
            "CALENDAR", size=13, rgb=_HEAD, alpha=1.0))
        y += 26
        separators.append(y)
        y += 12

        # day/week toggle, styled like the stats screen's metric legend, plus
        # a "today" shortcut at the row's right edge
        for i, mode in enumerate(("day", "week")):
            selected = self._cal_mode == mode
            self.view.addSubview_(self._button(
                NSMakeRect(PAD + i * 54, y, 50, 18), mode, "calendarMode:",
                rgb=_HEAD if selected else _BODY,
                alpha=1.0 if selected else 0.30, tag=i))
        self.view.addSubview_(self._button(
            NSMakeRect(content_w - PAD - 50, y, 50, 18), "today", "calendarToday:",
            rgb=_BODY, alpha=0.55))
        y += 26

        # ‹ prev / range label / next › — slides by one day or one week
        # depending on the mode above
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, 28, 22), "‹", "calendarPrev:", rgb=_BODY, alpha=0.85))
        self.view.addSubview_(self._label(
            NSMakeRect(PAD + 30, y + 3, table_w - 60, 16),
            self._calendar_range_label(), size=11.5, rgb=_HEAD, alpha=0.95,
            align=NSTextAlignmentCenter))
        self.view.addSubview_(self._button(
            NSMakeRect(content_w - PAD - 28, y, 28, 22), "›", "calendarNext:",
            rgb=_BODY, alpha=0.85))
        y += 30

        separators.append(y)
        y += 12

        if self._cal_mode == "day":
            y = self._build_calendar_day(y, table_w)
        else:
            y = self._build_calendar_week(y, table_w)

        y += 8
        separators.append(y)
        y += 14
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, 80, ROW_H), "‹ back", "calendarBack:",
            rgb=_BODY, alpha=0.8))
        y += ROW_H
        return y + PAD - 6

    @objc.python_method
    def _build_calendar_day(self, y, table_w):
        rows: list[dict] = []
        undated: list[dict] = []
        is_today = self._cal_anchor == date.today()
        if self._db_ok:
            try:
                rows = calendar_view.day_tasks(self._cal_anchor)
                # nothing says which day an undated task belongs on, so it
                # only ever shows up on today's timeline (see undated_tasks)
                if is_today:
                    undated = calendar_view.undated_tasks()
            except Exception:  # noqa: BLE001
                self._db_ok = False

        if not self._db_ok:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, table_w, ROW_H),
                "// no connection — reconnecting…", rgb=_BODY, alpha=0.7))
            return y + ROW_H

        # a fixed 9am-12am grid every day, always drawn even with no tasks,
        # rather than resizing to fit whatever deadlines happen to fall today
        start_hour, end_hour = CAL_DAY_START_HOUR, CAL_DAY_END_HOUR
        range_start = datetime.combine(self._cal_anchor, dtime(hour=start_hour))
        range_end = (datetime.combine(self._cal_anchor, dtime(hour=end_hour)) if end_hour < 24
                     else datetime.combine(self._cal_anchor + timedelta(days=1), dtime()))
        blocks = _calendar_day_blocks(self._cal_anchor, rows, undated, range_start, range_end)

        now = datetime.now()
        now_frac = now_label = None
        if is_today and range_start <= now <= range_end:
            now_frac = (now - range_start).total_seconds() / (range_end - range_start).total_seconds()
            now_label = _fmt_clock(now)

        grid_h = (end_hour - start_hour) * CAL_HOUR_PX
        self._ensure_daytimeline()
        self._daytimeline.setFrame_(NSMakeRect(PAD, y, table_w, grid_h))
        self._daytimeline.set_data(start_hour, end_hour, blocks, now_frac, now_label or "")
        self.view.addSubview_(self._daytimeline)
        return y + grid_h

    @objc.python_method
    def _build_calendar_week(self, y, table_w):
        by_day: dict = {}
        undated: list[dict] = []
        today = date.today()
        # nothing says which day an undated task belongs on, so it only ever
        # shows up under today's column — only fetch it if today's in view
        today_in_view = self._cal_anchor <= today < self._cal_anchor + timedelta(days=7)
        if self._db_ok:
            try:
                by_day = calendar_view.week_tasks(self._cal_anchor)
                if today_in_view:
                    undated = calendar_view.undated_tasks()
            except Exception:  # noqa: BLE001
                self._db_ok = False

        if not self._db_ok:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, table_w, ROW_H),
                "// no connection — reconnecting…", rgb=_BODY, alpha=0.7))
            return y + ROW_H

        start_hour, end_hour = CAL_DAY_START_HOUR, CAL_DAY_END_HOUR
        day_labels = []
        today_index = None
        columns = []
        now = datetime.now()
        now_frac = None
        for i in range(7):
            day = self._cal_anchor + timedelta(days=i)
            is_today = day == today
            if is_today:
                today_index = i
            day_labels.append(f"{day:%a}")

            range_start = datetime.combine(day, dtime(hour=start_hour))
            range_end = (datetime.combine(day, dtime(hour=end_hour)) if end_hour < 24
                         else datetime.combine(day + timedelta(days=1), dtime()))
            blocks = _calendar_day_blocks(
                day, by_day.get(day, []), undated if is_today else [],
                range_start, range_end)
            columns.append(blocks)
            if is_today and range_start <= now <= range_end:
                now_frac = (now - range_start).total_seconds() / (range_end - range_start).total_seconds()

        grid_h = (end_hour - start_hour) * CAL_WEEK_HOUR_PX + CAL_WEEK_HEADER_H
        self._ensure_weektimeline()
        self._weektimeline.setFrame_(NSMakeRect(PAD, y, table_w, grid_h))
        self._weektimeline.set_data(start_hour, end_hour, day_labels, today_index,
                                    columns, now_frac)
        self.view.addSubview_(self._weektimeline)
        return y + grid_h

    @objc.python_method
    def _build_timer(self, y, separators):
        self._header = None
        on_break = self.pomo.on_break
        secs = self.pomo.remaining_seconds()

        self.view.addSubview_(self._label(
            NSMakeRect(0, y, PANEL_W, 18),
            "BREAK" if on_break else "FOCUS", size=12,
            rgb=_HEAD, alpha=0.6, align=NSTextAlignmentCenter))
        y += 30

        self._timer_label = self._label(
            NSMakeRect(0, y, PANEL_W, 100), f"{secs // 60:d}:{secs % 60:02d}",
            size=76, rgb=_HEAD, alpha=1.0, align=NSTextAlignmentCenter)
        self.view.addSubview_(self._timer_label)
        y += 112

        task_text = "—"
        if self._db_ok:
            try:
                rows = tasks.open_tasks(limit=1)
                if rows:
                    task_text = rows[0]["description"]
            except Exception:  # noqa: BLE001
                self._db_ok = False
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 40), f"▸ {task_text}",
            size=13, rgb=_HEAD, alpha=0.9, lines=2, align=NSTextAlignmentCenter))
        y += 50

        separators.append(y)
        y += 14
        half = (PANEL_W - 2 * PAD - 12) / 2
        if self.pomo.paused:
            toggle_label = "▶ continue break" if on_break else "▶ continue"
        else:
            toggle_label = "■ stop break" if on_break else "■ stop"
        # row 1: pause/continue the countdown in place, or restart this phase
        # from its full duration; row 2: navigate away, timer left as-is
        grid = [
            (toggle_label, "pauseResumePomodoro:", _HEAD, 1.0),
            ("↺ reset", "resetPomodoro:", _BODY, 0.8),
            ("Tasks", "openTaskList:", _BODY, 0.8),
            ("⌂ main page", "timerMainPage:", _BODY, 0.8),
        ]
        for i, (text, action, rgb, alpha) in enumerate(grid):
            col = i % 2
            x = PAD + col * (half + 12)
            self.view.addSubview_(self._button(
                NSMakeRect(x, y, half, ROW_H), text, action, rgb=rgb, alpha=alpha))
            if col == 1:
                y += ROW_H + 6
        return max(SHEET_H, y + PAD - 6)

    @objc.python_method
    def _build_settings(self, y, separators):
        self._header = None
        self._ensure_settings_controls()
        if not self._settings_err:
            work, brk = config.pomodoro_durations()
            self._s_work.setStringValue_(f"{work:g}")
            self._s_break.setStringValue_(f"{brk:g}")

        head = "SETTINGS" + (f"   ⚠ {self._settings_err}" if self._settings_err else "")
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 36),
            head + "\n  pomodoro timing · enter / save · esc cancels",
            size=13, rgb=_HEAD, alpha=1.0, lines=2))
        y += 44
        separators.append(y)
        y += 14

        half = (PANEL_W - 2 * PAD - 12) / 2
        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, half, 16), "pomodoro (min)",
            size=11, rgb=_BODY, alpha=0.65))
        self.view.addSubview_(self._label(
            NSMakeRect(PAD + half + 12, y, half, 16), "break (min)",
            size=11, rgb=_BODY, alpha=0.65))
        y += 18
        self._s_work.setFrame_(NSMakeRect(PAD, y, half, ROW_H))
        self._s_break.setFrame_(NSMakeRect(PAD + half + 12, y, half, ROW_H))
        self.view.addSubview_(self._s_work)
        self.view.addSubview_(self._s_break)
        y += ROW_H + 18

        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, half, ROW_H), "save", "saveSettings:",
            rgb=_HEAD, alpha=1.0))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + half + 12, y, half, ROW_H), "cancel", "cancelSettings:",
            rgb=_BODY, alpha=0.7))
        return SHEET_H


_controller = None  # keep a strong reference for the app's lifetime


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    global _controller
    _controller = MenuController.alloc().init()
    print("productivity: 🍅 in the menu bar · Ctrl+Shift+Space toggles the panel, "
          "Esc closes it")
    AppHelper.runEventLoop()
