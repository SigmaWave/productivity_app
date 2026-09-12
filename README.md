# Productivity App
Productivity app that tracks and automatically assigns tasks based on LLM judgment. Work in Pomodoro session, tracking of tasks remaining and optimization of energy level throughout the day to adapt the tasks being done.


## Phases - Current: 5

### Phase 1: Set up Postgres Database ✅
`compose.yaml` + `init.sql` bring up Postgres 18 as `productivity_db`. The schema
also carries `recurring_tasks` + a `tasks.recurring_id` link (Phase 4), and
`tasks.deadline` is a `TIMESTAMPTZ` (day + time). Existing databases: apply the
files in `migrations/` in order (`002_phase4.sql`, `003_deadline_timestamp.sql`).

### Phase 2.1: Set up Interface ✅
`app/menubar.py` — a PyObjC app. Clicking the status-bar 🍅 (or **Ctrl+Shift+Space**
anywhere — a Carbon global hot key, `app/hotkey.py`, no permission needed) toggles
a **pop-up panel** (`NSPopover`, not a menu) in a green monospace terminal font.
**Esc** closes it. The tracking screen is plain black: open-task count, pomodoros
today, Pomodoro state, then the **active task and the next three fading out down a
luminosity gradient**. Starting a Pomodoro opens a full **timer screen** (big
`M:SS` + current task); a `⚙` on the tracking screen sets the work / break
lengths.

### Phase 2.2: Link interface with database ✅
`app/db.py` (shared psycopg connection) + `app/tasks.py` / `app/pomodoro.py`.
The panel reads tasks live. Each row splits into a `[ ]` checkbox and the task
name: clicking the checkbox completes the task (writes `done` / `finished_date`)
and, after standing checked for 0.5 s, fades out over 2 s before the rest of the
list moves up; clicking the name instead pins that task as the active one
(`tasks.pinned_at`, `migrations/005_task_pin.sql`), replacing whichever task was
pinned before it. Finishing a Pomodoro logs a `session` row. The "N more in
queue" line opens a **tasks list** window — every open task as a
`# | Task | Deadline | Repeat` table, paged 6 at a time with `▴`/`▾`. Press and
drag a row to reorder it; the new order is written to `tasks.sort_order`
(`migrations/006_task_sort_order.sql`) and, once anything has been dragged,
takes precedence over the computed priority score everywhere tasks are shown.

### Phase 2.3: Set up icon on task bar ✅
🍅 tomato icon in the menu bar; switches to a live `MM:SS` countdown (☕ on
breaks) while a Pomodoro runs.

### Phase 3: Set up manual task input ✅
`+ add task` plays a ~1 s full-screen "Matrix" digital rain, then dissolves to
a form on plain black: a **task** field (focused), an optional **repeat every**
weekday drop-down, and an optional **deadline** — a quick-pick drop-down whose
options come from `deadlines.json` (`by end of day`, `by end of morning`,
`by end of afternoon`, `tomorrow`, `end of week`, editable by hand) plus a
`custom…` entry (type `Monday` for the next Monday, or `Monday 24` for a
specific date; `Mon`→`Monday` auto-completes as you type) and an optional
time-of-day drop-down that overrides the option's own time. Enter or `done`
saves and returns to the tracking screen; `cancel` / Esc backs out.

### Phase 4: Add Recurring Tasks ✅
`app/recurring.py` + the `recurring_tasks` table. Choosing anything but `once` in
the form's **repeat every** drop-down stores a recurrence rule (`daily`,
`weekdays`, or `weekly:N`). On launch and once per day the app materialises a
concrete task for every template that is due and hasn't fired yet today.
Multi-day rules like `weekly:0,3` and `monthly` are still supported by
`recurring.add_recurring()` directly.

### Phase 5: Implement LLM call with Ollama

### Phase 6: Speed typing tracking ✅
`app/keys.py` — a global `NSEvent` monitor timestamps **every key press anywhere
on the machine** and classifies it `char` / `delete` / `other`. Presses are held
in RAM and bulk-inserted into the `keystroke` table every 60 s (prints
`database updated (N keystrokes)`). Needs macOS **Accessibility** permission
(System Settings ▸ Privacy & Security ▸ Accessibility ▸ enable your terminal,
then restart) — until granted the monitor sees nothing and the app says so.
Existing DBs: apply `migrations/004_keystroke.sql`.

---

## Running it

```bash
docker compose up -d               # Postgres 18 on localhost:5432

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# existing database (created before Phase 4)? apply the migrations once:
for f in migrations/*.sql; do docker compose exec -T db psql -U postgres -d productivity_db < "$f"; done

.venv/bin/python run.py            # 🍅 appears in the menu bar
```

Connection settings live in `.env` (`POSTGRES_PASSWORD`, `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`). Optional demo data:
`.venv/bin/python -m scripts.seed_demo --wipe`

### The pop-up panel
Toggle it with the 🍅 or **Ctrl+Shift+Space** (works from any app — Carbon
`RegisterEventHotKey`, no permission prompt). **Esc** or a click elsewhere closes
it. Green Menlo/Osaka-Mono terminal type on black.

**Tracking screen**
- header — `N open · M pomo today` and the live Pomodoro line; `⚙` at the
  top-right opens the settings screen
- the active task (bright, `▸`), the next two (each dimmer), then a
  `N more in queue` line at the faintest step of the gradient; click any task row
  to mark it done
- `+ add task` / `▚ stats` — each plays a ~1 s digital-rain (the base
  transition for loading any sub-screen), then shows the screen on plain black
- `▶ start pomodoro` — plays the digital rain, then opens the **timer screen**;
  `■ stop pomodoro` ends it. A finished work block is logged to `session` and a
  break starts automatically
- `⏻ quit` — quit the app

(The DB is re-checked and due recurring tasks regenerated whenever the panel
opens and every ~30 s while it's open, so there's no manual refresh.)

**Timer screen** (after `▶ start pomodoro`, or on reopening the panel while a
Pomodoro runs)
- a big centred `M:SS` countdown — `FOCUS` or `BREAK` — updated every second,
  with the current task shown beneath it
- `■ stop` ends and logs the block; `‹ tasks` goes back to the tracking screen
  while the timer keeps running (🍅 stays a countdown in the menu bar)

**Settings screen** (`⚙`)
- `pomodoro (min)` and `break (min)` — type the durations; `save` writes them to
  `config/pomodoro_settings.json` (git-ignored) and they apply to the next
  Pomodoro. `cancel` / Esc backs out. Falls back to the `POMODORO_*` `.env`
  values (25 / 5) when the file is absent

**New-task form** (`+ add task`)
- **task** — text, cursor lands here
- **repeat every** — `once` / `every day` / `weekdays` / `Mondays`…`Sundays`
- **deadline** — a quick-pick drop-down (options + times from `config/deadlines.json`:
  `by end of day` / `by end of morning` / `by end of afternoon` / `tomorrow` /
  `end of week`) whose last item, `custom…`, reveals a text box:
    - `Monday` — the next Monday (today if today is a Monday), end of day
    - `Monday 24` — the Monday that lands on the 24th, searched 30 days out; no
      such day → `did you mean Monday 21?` and the task isn't saved
    - typing auto-completes a unique 3+ letter prefix (`Mon` → `Monday`);
      backspacing into a completed day clears the whole word so you can retype
  plus an hourly time drop-down that, if set, overrides the chosen option's time
- Enter or `done` saves and returns to the tracking screen; `cancel` / Esc backs out

`config/deadlines.json` is plain JSON — edit the labels, `day`
(`today` / `tomorrow` / `monday`…`sunday` / `+N`) and `time` (`HH:MM`), then
restart the app. (All hand-editable config JSON lives in `config/`.)

**Stats page** (`▚ stats`)
- a bar chart with a **legend at the top-left** (one plain, one faded, like the
  task gradient) toggling two metrics, plus a **look-back selector at the
  bottom-right** — `1h` / `2h` / `6h` / `12h`, same click-to-select styling
  (selected plain, the rest faded); both metrics redraw over the chosen window:
  - **cpm** — per-minute count of characters typed over the window (deletes
    excluded), with a **trailing rolling-average line** over the bars (window
    set by the `5` / `10` / `20`-min buttons at the chart's top-right, same
    selected/faded styling as the metric legend; 20 by default), horizontal
    **y-axis gridlines every 50**, and an **x-axis of absolute clock times**
    (`HH:MM`) counting back over the window — 30-min ticks for the 1h/2h
    windows, 1-h ticks for 6h/12h
  - **diff** — distribution of the milliseconds between consecutive keystrokes
    (every key counted) over the same window: a histogram whose **x-axis is gap
    duration**, not clock time — fixed 50 ms bins from 0 to the x upper bound
    (750 ms by default). Gaps beyond the bound are shown as one **dashed,
    outline-only bar** past the last bin, labelled `<bound>+` on top (e.g.
    `750+`) — never folded into a real bin. The **`›` button at the chart's
    top-right** widens the bound by 250 ms per click and redraws live, wrapping
    back to 750 ms after 3 s
- `‹ back` returns to the tracking screen

## Inspecting the database

`scripts/db_helpers.py` — importable functions and a CLI. Every command prints a
table and also returns the rows (`list[dict]`).

```bash
.venv/bin/python -m scripts.db_helpers overview            # row counts per table
.venv/bin/python -m scripts.db_helpers tasks --status all  # open | done | all
.venv/bin/python -m scripts.db_helpers recurring           # templates + how many spawned
.venv/bin/python -m scripts.db_helpers sessions --days 14
.venv/bin/python -m scripts.db_helpers keystrokes --minutes 60  # per-min counts by kind
.venv/bin/python -m scripts.db_helpers speed --minutes 60       # cpm + inter-key gaps
.venv/bin/python -m scripts.db_helpers deadlines --days 7
.venv/bin/python -m scripts.db_helpers done --days 7       # recent completions
.venv/bin/python -m scripts.db_helpers stats               # task + session aggregates
.venv/bin/python -m scripts.db_helpers schema [table]
.venv/bin/python -m scripts.db_helpers sql "SELECT * FROM tasks WHERE urgent > 0.7"
```

```python
from scripts.db_helpers import overview, show_tasks, task_stats
rows = show_tasks("open")   # prints a table, also returns list[dict]
```

### Phase 6: Speed typing tracking

### Phase 7: Optimizer to build schedule

### Phase 8: Productionization

### Phase 9: Multimodal extension: Voice input

### Phase 10: Multimodal extension: Picture input

### Phase 11: Sync calendar

### Phase 12: Sync phone

## Database Schema

### Tasks

| Field | Description |
| :--- | :--- |
| `id` | Unique identifier |
| `description` | `str` Task description |
| `urgent` | `float` between 0 and 1 |
| `importance` | `float` between 0 and 1|
| `cognitive_load` | `float` between 0 and 1|
| `done` | `float` Completion progress between 0 and 1 |
| `finished_date` | `date` Date completed |
| `deadline` | `timestamptz` Task deadline, day + optional time (optional)|
| `recurring_id` | FK to `recurring_tasks.id` when this task was auto-generated |
| `estimated_duration` | `float` Estimated duration (minutes)|
| `manual`| `0` (automatically added) / `1` (manually added) |

---

### Session

| Field | Description |
| :--- | :--- |
| `id` | Unique identifier |
| `start` | `date` Start time/timestamp |
| `duration` | `float` Total duration |

---

### Typing

| Field | Description |
| :--- | :--- |
| `id` | Unique identifier |
| `date` | `date` Entry date |
| `time` | `float` Time taken to type |
| `accuracy` | `float` Typing accuracy between 0 and 1 |
| `ATS` | `float` Average typing speed |
| `cognitive_energy_approx` | Approximate cognitive energy |

### Keystroke  (Phase 6)

| Field | Description |
| :--- | :--- |
| `id` | `bigserial` |
| `ts` | `timestamptz` when the key was pressed |
| `kind` | `char` \| `delete` \| `other` |

### Schedule

| Field | Description |
| :--- | :--- |
| `id` | Unique identifier |
| `date` | `Task` Task description |
| `time` | `float` Time taken to type |
| `accuracy` | `float` Typing accuracy between 0 and 1 |
| `ATS` | `float` Average typing speed |
| `cognitive_energy_approx` | Approximate cognitive energy |


## Ressources
https://www.nature.com/articles/s41598-026-36500-7