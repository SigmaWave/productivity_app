-- Productivity App schema (Phases 1-6)
-- This file runs once, on first container init, against productivity_db.

CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    urgent REAL CHECK (urgent >= 0 AND urgent <= 1),
    importance REAL CHECK (importance >= 0 AND importance <= 1),
    cognitive_load REAL CHECK (cognitive_load >= 0 AND cognitive_load <= 1),
    done REAL DEFAULT 0 CHECK (done >= 0 AND done <= 1),
    finished_date DATE,
    deadline TIMESTAMPTZ,
    estimated_duration REAL,
    manual SMALLINT CHECK (manual IN (0, 1)) DEFAULT 1,
    -- Phase 4: link back to the recurring definition that generated this task
    recurring_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE session (
    id SERIAL PRIMARY KEY,
    start TIMESTAMPTZ NOT NULL,
    duration REAL
);

CREATE TABLE typing (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    time REAL,
    accuracy REAL CHECK (accuracy >= 0 AND accuracy <= 1),
    ats REAL,
    cognitive_energy_approx REAL
);

CREATE TABLE schedule (
    id SERIAL PRIMARY KEY,
    task_description TEXT,
    time REAL,
    accuracy REAL CHECK (accuracy >= 0 AND accuracy <= 1),
    ats REAL,
    cognitive_energy_approx REAL
);

-- Phase 4: Recurring task definitions. A background job in the app
-- materialises a concrete `tasks` row from each active definition when it
-- becomes due (see app/recurring.py).
CREATE TABLE recurring_tasks (
    id SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    urgent REAL CHECK (urgent >= 0 AND urgent <= 1),
    importance REAL CHECK (importance >= 0 AND importance <= 1),
    cognitive_load REAL CHECK (cognitive_load >= 0 AND cognitive_load <= 1),
    estimated_duration REAL,
    -- 'daily' | 'weekly' | 'monthly' | 'weekdays'
    -- optionally scoped: 'weekly:0,3' (Mon & Thu, 0=Monday)
    recurrence TEXT NOT NULL,
    active SMALLINT CHECK (active IN (0, 1)) DEFAULT 1,
    last_generated DATE,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE tasks
    ADD CONSTRAINT tasks_recurring_id_fkey
    FOREIGN KEY (recurring_id) REFERENCES recurring_tasks(id) ON DELETE SET NULL;

CREATE INDEX tasks_open_idx ON tasks (done) WHERE done < 1;
CREATE INDEX tasks_recurring_idx ON tasks (recurring_id, created_at);

-- Phase 6: raw keystroke stream. The app buffers presses in RAM and bulk-inserts
-- here every 60s (see app/keys.py). 'char' = a printable character, 'delete' =
-- backspace / forward-delete, 'other' = anything else (enter, arrows, shortcuts…).
CREATE TABLE keystroke (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('char', 'delete', 'other'))
);
CREATE INDEX keystroke_ts_idx ON keystroke (ts);
