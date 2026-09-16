"""Phase 5: LLM-assigned task labels.

Every task gets two labels, filled in once each:

* ``category`` — *how* the task is actually performed (device + mental
  posture, not what it's about), from a local Ollama model — see
  ``utils/label.txt``.
* ``energy`` — the cognitive load it demands, ``low`` / ``medium`` / ``high``.
  No model call: it's a static lookup from ``category`` via
  ``utils/energy_map.json`` (:func:`energy_for_category`) — the same category
  always carries the same load, so there's nothing here for a model to judge
  per task.

The label prompt is written to make the model answer with exactly one bare
word; anything else is treated as a failed classification. A task counts as
"processed" once neither column is NULL, which is also how work still to do
is found — :func:`unlabeled_tasks` / :func:`label_unlabeled_tasks` sweep
every task missing either one, meant to run once at app launch. A failure
(Ollama unreachable, a bad/slow reply, a timeout) just leaves the column(s)
NULL rather than raising, so it's picked up again by the next sweep or the
next edit that touches the task — nothing here can block or fail task
creation over a downed Ollama server.

This module does no threading of its own and touches the database directly;
callers that don't want a several-second Ollama round trip on the UI thread
(menubar.py) are responsible for running it off the main thread.
"""

from __future__ import annotations

import json
import os
from typing import Any

import ollama

from . import config, db  # noqa: F401 - importing config loads .env as a side effect

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL") or "qwen2.5:14b"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST")  # None -> ollama's own default (localhost:11434)

_UTILS_DIR = config.PROJECT_ROOT / "utils"
_LABEL_PROMPT = (_UTILS_DIR / "label.txt").read_text()
_ENERGY_MAP: dict[str, str] = {
    k: v for k, v in json.loads((_UTILS_DIR / "energy_map.json").read_text()).items()
    if not k.startswith("_")
}

# kept in sync with the LABELS list in utils/label.txt
CATEGORIES = {
    "job", "mail", "message", "call", "linkedin", "deep_computer", "deep_offline",
    "admin_desk", "admin_mobile", "read_desk", "read_mobile",
    "physical_home", "physical_out", "unknown",
}
# kept in sync with the values in utils/energy_map.json
ENERGY_LEVELS = {"low", "medium", "high"}

_client = ollama.Client(host=OLLAMA_HOST) if OLLAMA_HOST else ollama.Client()


def energy_for_category(category: str | None) -> str | None:
    """The static ``low``/``medium``/``high`` energy level for a category
    (``utils/energy_map.json``), or ``None`` if ``category`` isn't a
    recognised one — same "leave it NULL, retry later" convention as a
    failed Ollama call."""
    return _ENERGY_MAP.get(category)


def _tag(task_id: int | None, description: str) -> str:
    """Common prefix identifying which task a log line is about."""
    ident = f"#{task_id} " if task_id is not None else ""
    return f'{ident}"{description}"'


def _ask(system_prompt: str, description: str, *, kind: str,
         task_id: int | None = None) -> str | None:
    """One chat completion; the model's stripped reply, or None on any
    failure (network, timeout, malformed response). Never raises. Echoes the
    raw (unstripped) model output to the terminal on every call — flushed
    immediately, since this mostly runs on a background thread whose stdout
    would otherwise sit in Python's block buffer until it happened to fill —
    so a bad reply is visible even when it gets rejected below."""
    tag = _tag(task_id, description)
    try:
        reply = _client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": description},
            ],
            options={"temperature": 0},
        )
        raw = reply["message"]["content"]
        print(f"[llm_labels] {tag} -> {kind}: {raw!r}", flush=True)
        return raw.strip()
    except Exception as exc:  # noqa: BLE001 - Ollama down/slow shouldn't propagate
        print(f"[llm_labels] {tag} -> {kind} call failed: {exc}", flush=True)
        return None


def classify(description: str, *, task_id: int | None = None) -> tuple[str | None, str | None]:
    """``(category, energy)`` for one task description. ``category`` is None
    if the Ollama call failed or the reply wasn't one of the valid labels;
    ``energy`` then follows straight from it via :func:`energy_for_category`
    (None too, in that case — there's nothing to look up). ``task_id`` is
    only used to label the terminal output."""
    category = _ask(_LABEL_PROMPT, description, kind="category", task_id=task_id)
    if category not in CATEGORIES:
        if category is not None:
            print(f"[llm_labels] {_tag(task_id, description)} "
                  f"unrecognised category: {category!r}", flush=True)
        category = None
    return category, energy_for_category(category)


def label_task(task_id: int, description: str) -> dict[str, Any] | None:
    """Classify one task and persist category + its derived energy. Returns
    the updated row, or None if the Ollama call didn't produce a valid
    category (both columns stay NULL, to be retried by the next sweep)."""
    category, energy = classify(description, task_id=task_id)
    if category is None:
        return None
    return db.execute(
        """
        UPDATE tasks
        SET category = COALESCE(%(category)s, category),
            energy = COALESCE(%(energy)s, energy)
        WHERE id = %(id)s
        RETURNING *
        """,
        {"category": category, "energy": energy, "id": task_id},
    )


def unlabeled_tasks() -> list[dict[str, Any]]:
    """Every task (open or done) still missing a category or an energy
    label."""
    return db.query(
        "SELECT id, description FROM tasks WHERE category IS NULL OR energy IS NULL"
    )


def label_unlabeled_tasks() -> list[dict[str, Any]]:
    """Sweep every task not yet fully labelled — meant to run once at app
    launch. Best-effort: one bad task doesn't stop the rest."""
    updated = []
    for row in unlabeled_tasks():
        try:
            result = label_task(row["id"], row["description"])
        except Exception as exc:  # noqa: BLE001
            print(f"[llm_labels] task {row['id']}: {exc}", flush=True)
            continue
        if result is not None:
            updated.append(result)
    return updated
