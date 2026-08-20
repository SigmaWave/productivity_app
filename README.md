# Productivity App
Productivity app that tracks and automatically assigns tasks based on LLM judgment. Work in Pomodoro session, tracking of tasks remaining and optimization of energy level throughout the day to adapt the tasks being done.


## Phases - Current: 1

### Phase 1: Set up Postgres Database

### Phase 2.1: Set up Interface

### Phase 2.2: Link interface with database

### Phase 2.3: Set up icon on task bar

### Phase 3: Set up manual task input

### Phase 4: Add Recurring Tasks

### Phase 5: Implement LLM call with Ollama

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
| `deadline` | `date` Task deadline (optional)|
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