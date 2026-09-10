"""Deadline quick-picks + the free-text "Weekday 24" entry (Phase 3 refinement).

The named options in the new-task form's deadline drop-down are read from
``config/deadlines.json`` so they can be edited by hand without touching code.
Each option is ``{"label", "day", "time"}`` where

    day    today | tomorrow | monday..sunday | +N   (N days from today)
    time   HH:MM (24-hour)

The drop-down's final entry is ``custom…``; picking it reveals a text box that
takes ``Weekday`` or ``Weekday 24``:

* ``Monday``     -> the next Monday (today if today is a Monday)
* ``Monday 24``  -> the Monday that falls on the 24th, searched over the next
                    30 days
* no such day in the next 30 days -> :class:`DeadlineError`, message
  ``did you mean Monday 21?`` (the nearest Monday's date); the task is not saved

All functions return a naive local ``datetime`` — the caller applies the form's
optional time-of-day override and ``.astimezone()``.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEADLINES_PATH = _PROJECT_ROOT / "config" / "deadlines.json"

NONE_LABEL = "—"
CUSTOM_LABEL = "custom…"

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")

# how far ahead "Weekday N" looks for an exact match before giving up
ENTRY_SEARCH_DAYS = 30

_DEFAULT_OPTIONS = [
    {"label": "by end of day", "day": "today", "time": "23:59"},
    {"label": "by end of morning", "day": "today", "time": "12:00"},
    {"label": "by end of afternoon", "day": "today", "time": "17:00"},
    {"label": "tomorrow", "day": "tomorrow", "time": "23:59"},
    {"label": "end of week", "day": "sunday", "time": "23:59"},
]


class DeadlineError(ValueError):
    """A custom deadline entry that can't be resolved. ``message`` is shown
    verbatim in the form's error line and the task is not saved."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _load_options() -> list[dict]:
    """The options from ``config/deadlines.json``, or the built-in defaults if
    the file is missing / unreadable / empty."""
    try:
        data = json.loads(_DEADLINES_PATH.read_text())
        raw = data["options"] if isinstance(data, dict) else data
        cleaned = []
        for opt in raw:
            label = str(opt["label"]).strip()
            if label:
                cleaned.append({
                    "label": label,
                    "day": str(opt.get("day", "today")).strip().lower(),
                    "time": str(opt.get("time", "23:59")).strip(),
                })
        if cleaned:
            return cleaned
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [dict(o) for o in _DEFAULT_OPTIONS]


def option_labels() -> list[str]:
    """Ordered labels for the deadline drop-down (without the marker / custom
    entries the form adds around them)."""
    return [o["label"] for o in _load_options()]


def _parse_hhmm(text: str) -> dtime:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return dtime(hh, mm)
    return dtime(23, 59)


def _weekday_date(target_idx: int, today: date) -> date:
    """Next date on weekday ``target_idx`` (0 = Monday) — today if it matches."""
    return today + timedelta(days=(target_idx - today.weekday()) % 7)


def _option_date(day: str, today: date) -> date:
    day = (day or "today").strip().lower()
    if day in ("today", "", NONE_LABEL):
        return today
    if day == "tomorrow":
        return today + timedelta(days=1)
    if day.startswith("+"):
        try:
            return today + timedelta(days=int(day[1:]))
        except ValueError:
            return today
    if len(day) >= 3:
        for idx, name in enumerate(WEEKDAYS):
            if name.lower().startswith(day):
                return _weekday_date(idx, today)
    return today


def resolve(label: str, *, now: datetime | None = None) -> datetime | None:
    """A named drop-down option -> naive local datetime. ``None`` for the empty
    marker, the custom placeholder, or an unknown label."""
    if label in (None, "", NONE_LABEL, CUSTOM_LABEL):
        return None
    today = (now or datetime.now()).date()
    for opt in _load_options():
        if opt["label"] == label:
            return datetime.combine(_option_date(opt["day"], today),
                                    _parse_hhmm(opt["time"]))
    return None


def autocomplete(text: str) -> str | None:
    """If ``text`` begins with >=3 letters that uniquely prefix one weekday and
    aren't already its full name, return it expanded (keeping any trailing
    number). Otherwise ``None`` — leave the field alone."""
    m = re.match(r"^([A-Za-z]+)(.*)$", text or "")
    if not m:
        return None
    word, rest = m.group(1), m.group(2)
    if len(word) < 3:
        return None
    hits = [d for d in WEEKDAYS if d.lower().startswith(word.lower())]
    if len(hits) == 1 and hits[0].lower() != word.lower():
        return hits[0] + rest
    return None


def strip_completed_weekday(prev: str, current: str) -> str | None:
    """When a backspace eats into an already-completed weekday word, return the
    text with that whole leading word removed (so it can be retyped from
    scratch); otherwise ``None`` — leave the edit alone.

    ``prev`` is the field's value before the keystroke, ``current`` after it.
    """
    if len(current or "") >= len(prev or ""):
        return None
    pm = re.match(r"^([A-Za-z]+)", prev or "")
    if not pm or pm.group(1).capitalize() not in WEEKDAYS:
        return None
    word = pm.group(1)
    cur_word = re.match(r"^([A-Za-z]*)", current or "").group(1)
    if len(cur_word) < len(word) and word.lower().startswith(cur_word.lower()):
        return current[len(cur_word):].lstrip()
    return None


def _match_weekday(word: str) -> int:
    lowers = [d.lower() for d in WEEKDAYS]
    if word.lower() in lowers:
        return lowers.index(word.lower())
    hits = [i for i, d in enumerate(lowers) if d.startswith(word.lower())]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise DeadlineError(
            f'"{word}" is ambiguous — {" or ".join(WEEKDAYS[i] for i in hits)}?')
    raise DeadlineError(f'"{word}" isn\'t a day of the week')


_ENTRY_RE = re.compile(r"^\s*([A-Za-z]+)\s*(\d{1,2})?\s*$")


def parse_entry(text: str, *, now: datetime | None = None) -> datetime:
    """``Weekday`` / ``Weekday 24`` -> naive local datetime (end of day).

    Raises :class:`DeadlineError` for an unparseable string, an out-of-range day
    number, or (``did you mean Weekday D?``) a weekday/number pair with no match
    in the next 30 days.
    """
    today = (now or datetime.now()).date()
    eod = dtime(23, 59)

    m = _ENTRY_RE.match(text or "")
    if not m:
        raise DeadlineError('type a day like "Monday" or "Monday 24"')
    word, num = m.group(1), m.group(2)
    idx = _match_weekday(word)
    name = WEEKDAYS[idx]

    if num is None:
        return datetime.combine(_weekday_date(idx, today), eod)

    number = int(num)
    if not 1 <= number <= 31:
        raise DeadlineError("day of the month must be 1-31")

    for offset in range(ENTRY_SEARCH_DAYS + 1):
        d = today + timedelta(days=offset)
        if d.weekday() == idx and d.day == number:
            return datetime.combine(d, eod)

    # no such day soon: suggest the <weekday> nearest to the date they likely
    # meant — the next time the month hits `number`
    anchor = today
    for _ in range(370):
        if anchor.day == number:
            break
        anchor += timedelta(days=1)
    delta = (idx - anchor.weekday()) % 7
    forward = anchor + timedelta(days=delta)
    backward = anchor - timedelta(days=(7 - delta) % 7)
    nearest = min(forward, backward, key=lambda d: abs((d - anchor).days))
    raise DeadlineError(f"did you mean {name} {nearest.day}?")
