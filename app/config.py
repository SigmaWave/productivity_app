"""Configuration loading.

Reads connection settings from the project ``.env`` file (falling back to real
environment variables). Keeping this in one place means both the menu bar app
and the ``scripts/`` helpers connect the same way.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env once at import time. Real environment variables win over the file.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _get(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _local_timezone_name() -> str:
    """The host's IANA timezone name (e.g. "America/New_York"), read from the
    ``/etc/localtime`` symlink macOS/Linux keep pointed at it.

    The Postgres container defaults its session TimeZone to UTC, which is
    almost never this host's zone. Left alone, that mismatch silently shifts
    every "today" boundary — ``CURRENT_DATE``, ``deadline::date``, a
    ``TIMESTAMPTZ`` handed back to Python — by the UTC offset: a task deadline
    of "end of day" (23:59 local) stored as 03:59 UTC the next day, checked
    against a UTC ``CURRENT_DATE`` that's already rolled over. That's what
    made the calendar's day view report "no deadlines today" for a task due
    today. Falls back to "UTC" if the symlink can't be resolved.
    """
    try:
        real = os.path.realpath("/etc/localtime")
        return real.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def db_connection_kwargs() -> dict[str, str | int]:
    """psycopg.connect(**kwargs) arguments for the productivity database."""
    return {
        "host": _get("PGHOST", "localhost"),
        "port": int(_get("PGPORT", "5432")),
        "dbname": _get("PGDATABASE", "productivity_db"),
        "user": _get("PGUSER", "postgres"),
        "password": _get("POSTGRES_PASSWORD", "postgres"),
        # pin the session's TimeZone to the host's own, not Postgres's UTC
        # default — see _local_timezone_name(). PGTZ overrides if ever needed.
        "options": f"-c TimeZone={_get('PGTZ', _local_timezone_name())}",
    }


def db_dsn() -> str:
    """Same settings as a libpq URI (handy for psql / logging)."""
    kw = db_connection_kwargs()
    return (
        f"postgresql://{kw['user']}:{kw['password']}"
        f"@{kw['host']}:{kw['port']}/{kw['dbname']}"
    )


# Pomodoro defaults (minutes). These are the fallback; the menu-bar gear screen
# writes user overrides to ``config/pomodoro_settings.json``.
POMODORO_WORK_MIN = float(_get("POMODORO_WORK_MIN", "25"))
POMODORO_BREAK_MIN = float(_get("POMODORO_BREAK_MIN", "5"))

CONFIG_DIR = PROJECT_ROOT / "config"
_POMODORO_SETTINGS_PATH = CONFIG_DIR / "pomodoro_settings.json"


def pomodoro_durations() -> tuple[float, float]:
    """``(work_min, break_min)`` — the user's saved overrides if present,
    otherwise the ``POMODORO_*`` defaults. Read fresh on every call so a change
    in the gear screen takes effect on the next Pomodoro."""
    work, brk = POMODORO_WORK_MIN, POMODORO_BREAK_MIN
    try:
        data = json.loads(_POMODORO_SETTINGS_PATH.read_text())
        work = float(data.get("work_min", work))
        brk = float(data.get("break_min", brk))
    except (OSError, ValueError, TypeError):
        pass
    return work, brk


def set_pomodoro_durations(work_min: float, break_min: float) -> None:
    """Persist the Pomodoro work / break lengths (minutes)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _POMODORO_SETTINGS_PATH.write_text(json.dumps(
        {"work_min": round(float(work_min), 2),
         "break_min": round(float(break_min), 2)}, indent=2) + "\n")
