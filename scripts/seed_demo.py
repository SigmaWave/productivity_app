#!/usr/bin/env python3
"""Insert a handful of demo tasks / recurring templates for local testing.

    python -m scripts.seed_demo          # add demo rows
    python -m scripts.seed_demo --wipe   # TRUNCATE first, then add
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, recurring, tasks  # noqa: E402


def wipe() -> None:
    db.execute("TRUNCATE tasks, recurring_tasks, session RESTART IDENTITY CASCADE")


def seed() -> None:
    tasks.add_task("Finish quarterly report", urgent=0.9, importance=0.9,
                   cognitive_load=0.8, estimated_duration=120,
                   deadline=date.today() + timedelta(days=2))
    tasks.add_task("Reply to recruiter email", urgent=0.6, importance=0.3,
                   cognitive_load=0.2, estimated_duration=10)
    tasks.add_task("Refactor auth module", urgent=0.3, importance=0.7,
                   cognitive_load=0.9, estimated_duration=90)
    tasks.add_task("Water the plants", urgent=0.4, importance=0.1,
                   cognitive_load=0.1, estimated_duration=5)

    recurring.add_recurring("Morning standup", "weekdays",
                            importance=0.4, cognitive_load=0.2, estimated_duration=15)
    recurring.add_recurring("Weekly review", "weekly:6",
                            importance=0.7, cognitive_load=0.5, estimated_duration=45)
    recurring.add_recurring("Pay rent", "monthly:1",
                            urgent=0.8, importance=0.8, estimated_duration=10)

    created = recurring.generate_due_tasks()
    print(f"Seeded demo data (+{len(created)} recurring task(s) due today).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wipe", action="store_true", help="truncate tables first")
    args = parser.parse_args()
    if not db.ping():
        sys.exit("Database unreachable — run `docker compose up -d` first.")
    if args.wipe:
        wipe()
    seed()
