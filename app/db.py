"""Thin database access layer.

A single lazily-created psycopg connection is reused for the life of the
process. Every public helper opens its own cursor and commits, so callers just
get plain Python values back and never have to think about transactions.

A psycopg connection isn't safe for concurrent use from multiple threads, so
``cursor()`` serializes access through ``_lock`` — this app is single-threaded
except for the background LLM-labeling calls in app/llm_labels.py (kicked off
from app/menubar.py), and the lock is what lets those touch the database
without racing the main thread's own queries.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .config import db_connection_kwargs

_conn: psycopg.Connection | None = None
_lock = threading.Lock()


def get_connection() -> psycopg.Connection:
    """Return the shared connection, (re)connecting if needed."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(**db_connection_kwargs(), autocommit=False)
    return _conn


def close_connection() -> None:
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
    _conn = None


@contextmanager
def cursor(*, commit: bool = True) -> Iterator[psycopg.Cursor]:
    """Dict-row cursor context manager with commit/rollback handling.
    Holds ``_lock`` for its duration — see the module docstring."""
    with _lock:
        conn = get_connection()
        cur = conn.cursor(row_factory=dict_row)
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def query(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return all rows as dicts."""
    with cursor(commit=False) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    with cursor(commit=False) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    """Run an INSERT/UPDATE/DELETE. Returns the first RETURNING row if any."""
    with cursor(commit=True) as cur:
        cur.execute(sql, params)
        if cur.description is not None:
            return cur.fetchone()
        return None


def ping() -> bool:
    """True if the database is reachable."""
    try:
        with cursor(commit=False) as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        close_connection()
        return False
