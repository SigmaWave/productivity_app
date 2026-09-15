"""Phase 5: LLM-assigned task labels via a local Ollama model.

Every task gets two labels, filled in once each:

* ``category`` — *how* the task is actually performed (device + mental
  posture, not what it's about) — see ``prompts/label.txt``.
* ``energy`` — the cognitive load it demands, ``low`` / ``medium`` / ``high``
  — see ``prompts/energy.txt``.

Both prompts are written to make the model answer with exactly one bare
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

import os
from typing import Any

import ollama

from . import config, db  # noqa: F401 - importing config loads .env as a side effect

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL") or "qwen2.5:14b"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST")  # None -> ollama's own default (localhost:11434)

_PROMPTS_DIR = config.PROJECT_ROOT / "prompts"
_LABEL_PROMPT = (_PROMPTS_DIR / "label.txt").read_text()
_ENERGY_PROMPT = (_PROMPTS_DIR / "energy.txt").read_text()

# kept in sync with the LABELS list in prompts/label.txt
CATEGORIES = {
    "job", "mail", "message", "call", "linkedin", "deep_computer", "deep_offline",
    "admin_desk", "admin_mobile", "read_desk", "read_mobile",
    "physical_home", "physical_out", "unknown",
}
# kept in sync with prompts/energy.txt
ENERGY_LEVELS = {"low", "medium", "high"}

_client = ollama.Client(host=OLLAMA_HOST) if OLLAMA_HOST else ollama.Client()


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
    """``(category, energy)`` for one task description. Each is None if the
    call failed or the reply wasn't one of the valid words for that prompt.
    ``task_id`` is only used to label the terminal output."""
    category = _ask(_LABEL_PROMPT, description, kind="category", task_id=task_id)
    if category not in CATEGORIES:
        if category is not None:
            print(f"[llm_labels] {_tag(task_id, description)} "
                  f"unrecognised category: {category!r}", flush=True)
        category = None
    energy = _ask(_ENERGY_PROMPT, description, kind="energy", task_id=task_id)
    if energy not in ENERGY_LEVELS:
        if energy is not None:
            print(f"[llm_labels] {_tag(task_id, description)} "
                  f"unrecognised energy: {energy!r}", flush=True)
        energy = None
    return category, energy


def label_task(task_id: int, description: str) -> dict[str, Any] | None:
    """Classify one task and persist whichever of category/energy came back
    valid (the other stays NULL, to be retried by the next sweep). Returns
    the updated row, or None if neither label was obtained."""
    category, energy = classify(description, task_id=task_id)
    if category is None and energy is None:
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
