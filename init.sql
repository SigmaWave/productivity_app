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
    created_at TIMESTAMPTZ DEFAULT now(),
    -- set when the user clicks a task's name to make it the active task;
    -- at most one row has this set at a time (see app/tasks.py:set_active_task)
    pinned_at TIMESTAMPTZ,
    -- manual drag-to-reorder position from the tasks list window, spaced
    -- 10 apart; NULL until the user has dragged anything (see reorder_tasks)
    sort_order INTEGER,
    -- Phase 5: LLM-assigned labels (app/llm_labels.py, via a local Ollama
    -- model). NULL until a successful classification — that's how the
    -- startup sweep finds tasks still needing one.
    category TEXT CHECK (category IN (
        'job', 'mail', 'message', 'call', 'linkedin', 'deep_computer', 'deep_offline',
        'admin_desk', 'admin_mobile', 'read_desk', 'read_mobile',
        'physical_home', 'physical_out', 'unknown'
    )),
    energy TEXT CHECK (energy IN ('low', 'medium', 'high'))
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

-- Job Search screen (app/jobsearch.py, not one of the numbered phases below —
-- "Phase 7" there is reserved for the scheduler). One row per click of the
-- "+" counter: started_at is when the manually-started stopwatch began
-- (NULL if it wasn't running), ts is when the counter was clicked, and
-- elapsed_seconds is the stopwatch's reading measured with time.monotonic()
-- — NOT derived as ts - started_at, since those come from different clocks
-- (host wall clock vs. this container's) that can drift apart.
-- timer_seconds / diff_seconds are reserved for a possible future
-- preset-timer feature and stay NULL for now.
CREATE TABLE job_applications (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    elapsed_seconds REAL,
    timer_seconds REAL,
    diff_seconds REAL
);

-- Mouse activity stream: movement samples (throttled, not every raw event)
-- plus every click, buffered in RAM and bulk-inserted every 60s (app/mouse.py),
-- mirroring the keystroke table above. x/y are screen coordinates from
-- NSEvent.mouseLocation() (bottom-left origin); button is NULL for 'move' rows.
CREATE TABLE mouse_event (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('move', 'click')),
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    button TEXT CHECK (button IN ('left', 'right', 'other'))
);
CREATE INDEX mouse_event_ts_idx ON mouse_event (ts);
