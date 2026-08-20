CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    urgent REAL CHECK (urgent >= 0 AND urgent <= 1),
    importance REAL CHECK (importance >= 0 AND importance <= 1),
    cognitive_load REAL CHECK (cognitive_load >= 0 AND cognitive_load <= 1),
    done REAL DEFAULT 0 CHECK (done >= 0 AND done <= 1),
    finished_date DATE,
    deadline DATE,
    estimated_duration REAL,
    manual SMALLINT CHECK (manual IN (0, 1)) DEFAULT 1
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