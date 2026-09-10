"""macOS menu bar interface (Phases 2.1, 2.3, 3, 4, 6).

Run with ``python run.py``. A 🍅 lives in the status bar; Ctrl+Shift+Space
(system-wide, Carbon ``RegisterEventHotKey``) or a click toggles a pop-up panel
(``NSPopover``) in a green monospace terminal font; Esc closes it.

Screens (``self._screen``):

* **tracking** — plain black. Header (``⚙`` at top-right opens settings), then
  the active task, the next two, and a "N more in queue" line fading down a
  luminosity gradient. Buttons: ``+ add task``, start/stop Pomodoro, ``stats``,
  quit.
* **transition** — the base loader: ~1 s of full-screen "Matrix" rain before any
  sub-screen (form, stats, timer, settings) appears.
* **timer** — shown after starting a Pomodoro: a big centred ``M:SS`` countdown
  (``FOCUS`` / ``BREAK``) with the current task under it. ``■ stop`` ends and
  logs it; ``‹ tasks`` returns to tracking with the timer still running. The
  1 s tick updates the digits in place.
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
  30-min ticks ≤2h, 1-h ticks above) and ``diff`` (a histogram of the ms gap
  between consecutive keystrokes, everything counted — x-axis is gap duration,
  not clock time, fixed 50 ms bins up to a cap the top-right ``›`` widens by
  250 ms a click). A
  bottom-right selector (``1h``/``2h``/``6h``/``12h``) sets the look-back
  window for both; it starts at ``keys.STATS_WINDOW_MINUTES``.

Phase 6: every keystroke on the machine is timestamped in RAM and flushed to the
``keystroke`` table every 60 s (``app/keys.py``).
"""

from __future__ import annotations

import random
import time
from datetime import date, datetime, time as dtime, timedelta

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
    NSStatusBar,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextField,
    NSTimer,
    NSTrackingArea,
    NSVariableStatusItemLength,
    NSView,
    NSViewController,
)
from Foundation import NSMakeRange, NSObject
from PyObjCTools import AppHelper

from . import config, db, deadlines, keys, pomodoro, recurring, tasks

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

# stats look-back windows the user can click, shortest first (label, minutes)
_WINDOWS = (("1h", 60), ("2h", 120), ("6h", 360), ("12h", 720))
_WINDOW_LABELS = {m: lab for lab, m in _WINDOWS}

# cpm rolling-average windows (minutes) the user can click
_CPM_MA_CHOICES = (5, 10, 20)

# seconds of digital rain shown when loading a sub-screen (the base transition)
TRANSITION_SECONDS = 1.0

# how often the RAM keystroke buffer is written to Postgres
KEYSTROKE_FLUSH_SECONDS = 60

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
    mark = "~" if task.get("done") else " "
    if brief:
        return f'[{mark}] {task["description"]}'
    meta = []
    deadline = task.get("deadline")
    if deadline:
        fmt = "%m/%d" if (deadline.hour, deadline.minute) in ((23, 59), (0, 0)) else "%m/%d %H:%M"
        meta.append(f"{deadline:{fmt}}")
    if task.get("estimated_duration"):
        meta.append(f'{task["estimated_duration"]:.0f}m')
    tail = f'   {" · ".join(meta)}' if meta else ""
    return f'[{mark}] {task["description"]}{tail}'


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
        self.setBordered_(False)
        self.setFocusRingType_(NSFocusRingTypeNone)
        self.setFont_(_ui_font(12.5))
        self.setAlignment_(NSTextAlignmentLeft)
        self.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
        return self

    @objc.python_method
    def configure(self, text, base, hover):
        self._text, self._base, self._hover = text, base, hover
        self._paint(self._base)

    @objc.python_method
    def _paint(self, color):
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(NSTextAlignmentLeft)
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
        self._screen = "tracking"
        self._flash_msg = ""
        self._flash_until = 0.0
        self._form_err = ""
        self._settings_err = ""
        self._monitor = None
        self._header = None
        self._transition_timer = None
        self._transition_target = "form"
        self._stats_metric = "cpm"          # "cpm" | "diff"
        self._stats_window = keys.STATS_WINDOW_MINUTES   # minutes; 1h/2h/6h/12h
        self._diff_cap = keys.DIFF_HIST_CAP_MS           # diff chart x upper bound (ms)
        self._cpm_ma = 20                                # cpm rolling-average window (min)
        self._chart = None
        self._f_task = self._f_repeat = self._f_day = self._f_time = None
        self._f_entry = None
        self._f_entry_prev = ""          # last value seen, for the delete guard
        self._s_work = self._s_break = None
        self._timer_label = None
        self.tracker = keys.KeystrokeTracker()

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

        self._startup()
        return self

    def flushKeystrokes_(self, timer):
        self.tracker.flush()

    @objc.python_method
    def _hotkey_fired(self):
        self.togglePopover_(None)

    @objc.python_method
    def _handle_key_event(self, event):
        flags = event.modifierFlags()
        code = event.keyCode()
        if code == KEY_ESC and self.popover.isShown():
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
        except Exception as exc:  # noqa: BLE001
            print(f"[recurring] {exc}")

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

    # -- popover open / close ------------------------------------------

    def togglePopover_(self, sender):
        if self.popover.isShown():
            self.popover.close()
            return
        self._db_ok = db.ping()
        if self._db_ok:
            self._generate_recurring()
        self._cancel_transition()
        self._screen = "timer" if self.pomo.running else "tracking"
        self.rebuild()
        button = self.status_item.button()
        self.popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, NSMinYEdge
        )
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def popoverDidShow_(self, notification):
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown,
            lambda event: self.popover.performClose_(None),
        )

    def popoverDidClose_(self, notification):
        self._cancel_transition()
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        self._screen = "tracking"

    # -- tracking-screen actions -----------------------------------

    def taskClicked_(self, sender):
        try:
            tasks.complete_task(int(sender.tag()))
            self._flash("task done ✓")
        except Exception as exc:  # noqa: BLE001
            self._flash(f"err: {exc}")
        self.rebuild()

    def togglePomodoro_(self, sender):
        if self.pomo.running:
            self.stopPomodoro_(sender)
            return
        pomodoro.start_work(self.pomo)
        self._flash("focus on.")
        self._update_status_title()
        self._start_transition("timer")

    def stopPomodoro_(self, sender):
        minutes = pomodoro.stop(self.pomo)
        self._flash(f"logged {minutes:.1f} min" if minutes else "stopped")
        self._update_status_title()
        self._cancel_transition()
        self._screen = "tracking"
        self.rebuild()

    def timerBack_(self, sender):
        # leave the Pomodoro running; just return to the task list
        self._screen = "tracking"
        self.rebuild()

    def openStats_(self, sender):
        self._start_transition("stats")

    def openSettings_(self, sender):
        self._settings_err = ""
        self._start_transition("settings")

    def quit_(self, sender):
        self.tracker.flush()
        self.tracker.stop()
        NSApplication.sharedApplication().terminate_(self)

    @objc.python_method
    def _pomodoro_finished(self):
        was_break = self.pomo.on_break
        pomodoro.stop(self.pomo)
        if was_break:
            self._flash("break over — back to it")
            if self._screen == "timer":
                self._screen = "tracking"
        else:
            self._flash("pomodoro complete — break started")
            pomodoro.start_break(self.pomo)
        self._update_status_title()
        if self.popover.isShown() and self._screen in ("tracking", "timer"):
            self.rebuild()

    # -- new-task form -------------------------------------------

    def openForm_(self, sender):
        self._form_err = ""
        self._reset_form()
        self._start_transition("form")

    def cancelForm_(self, sender):
        self._cancel_transition()
        self._screen = "tracking"
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
        self._stats_metric = "cpm" if int(sender.tag()) == 0 else "diff"
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
                hover_rgb=_HEAD, tag=None):
        btn = TermButton.alloc().initWithFrame_(frame)
        btn.configure(text, _green(rgb, alpha), _green(hover_rgb, min(1.0, alpha + 0.45)))
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
            state = f'{"break" if self.pomo.on_break else "focus"} {secs // 60}:{secs % 60:02d}'
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
        y = PAD
        if self._screen == "form":
            y = self._build_form(y, separators)
        elif self._screen == "stats":
            y = self._build_stats(y, separators)
        elif self._screen == "settings":
            y = self._build_settings(y, separators)
        elif self._screen == "timer":
            y = self._build_timer(y, separators)
        elif self._screen == "transition":
            y = self._build_transition(y)
        else:
            y = self._build_tracking(y, separators)

        self.view.separators = separators
        size = NSMakeSize(PANEL_W, y)
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
        minutes from now. 30-min spacing for windows up to 2h, 1-h spacing above.
        Returns [(fraction_along_x, "HH:MM"), …] aligned to round clock times."""
        step = 30 if window <= 120 else 60
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
            ticks.append((frac, tick.strftime("%H:%M")))
            tick += timedelta(minutes=step)
        return ticks

    @objc.python_method
    def _build_stats(self, y, separators):
        self._header = None
        self.tracker.flush()

        self.view.addSubview_(self._label(
            NSMakeRect(PAD, y, PANEL_W - 2 * PAD, 36),
            "STATS\n  keystroke activity", size=13, rgb=_HEAD, alpha=1.0, lines=2))
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
            values = keys.cpm_series(window)
            vmax = max(values) if values else 1
            top = ""
            overlay = keys.rolling_average(values, self._cpm_ma)
            y_step = 50
            x_ticks = self._clock_xticks(window)
            caption = ""
            empty = f"no characters typed in the last {wlabel}"
        else:
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

        chart_h = 150
        self._ensure_chart()
        self._chart.setFrame_(NSMakeRect(PAD, y, PANEL_W - 2 * PAD, chart_h))
        self._chart.set_data(values, vmax, top, caption, empty, x_ticks, overlay,
                             y_step, overflow)
        self.view.addSubview_(self._chart)

        # legend over the graph's top-left, styled like the task gradient
        ly = y + 4
        for i, name in enumerate(("cpm", "diff")):
            selected = (metric == name)
            self.view.addSubview_(self._button(
                NSMakeRect(PAD + 8, ly, 84, 18), name, "statsMetric:",
                rgb=_HEAD if selected else _BODY,
                alpha=1.0 if selected else 0.30, tag=i))
            ly += 19

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
        for label, minutes in _WINDOWS:
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
                rows = tasks.open_tasks(limit=VISIBLE_TASKS)
                total = tasks.open_task_count()
            except Exception:  # noqa: BLE001
                self._db_ok = False

        if not self._db_ok:
            self.view.addSubview_(self._label(
                NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H),
                "// no connection — reconnecting…", rgb=_BODY, alpha=0.7))
            y += ROW_H
        elif rows:
            for i, task in enumerate(rows):
                active = i == 0
                prefix = "▸ " if active else "  "
                self.view.addSubview_(self._button(
                    NSMakeRect(PAD, y, PANEL_W - 2 * PAD, ROW_H),
                    prefix + _task_line(task, brief=not active),
                    "taskClicked:", tag=int(task["id"]),
                    rgb=_HEAD if active else _BODY, alpha=GRADIENT[i],
                    hover_rgb=_HEAD,
                ))
                y += ROW_H + 1
            # final line: how many tasks are still queued behind the visible ones
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
        pomo = "■ stop pomodoro" if self.pomo.running else "▶ start pomodoro"
        grid = [
            ("+ add task", "openForm:"),
            (pomo, "togglePomodoro:"),
            ("▚ stats", "openStats:"),
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
        self.view.addSubview_(self._button(
            NSMakeRect(PAD, y, half, ROW_H), "■ stop", "stopPomodoro:",
            rgb=_HEAD, alpha=1.0))
        self.view.addSubview_(self._button(
            NSMakeRect(PAD + half + 12, y, half, ROW_H), "‹ tasks", "timerBack:",
            rgb=_BODY, alpha=0.8))
        return SHEET_H

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
