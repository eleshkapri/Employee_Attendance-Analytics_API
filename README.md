# Employee Attendance & Analytics API

FastAPI and MongoDB service for employee attendance tracking, manual regularization, and workforce analytics with full business rule enforcement (R1–R10) and indexed aggregation pipelines.

- **Live Service**: [https://employee-attendance-analytics-api-hap6.onrender.com](https://employee-attendance-analytics-api-hap6.onrender.com)
- **Interactive Swagger Docs**: [https://employee-attendance-analytics-api-hap6.onrender.com/docs](https://employee-attendance-analytics-api-hap6.onrender.com/docs)

---

## How to Run

### 1. Installation

```bash
# Create and activate virtual environment
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

Set the environment variables or create a `.env` file from the template:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|---|---|---|
| `MONGO_URI` | MongoDB connection URI | `mongodb://localhost:27017` |
| `MONGO_DB` | Target database name | `attendance_db` |

*Note: Real environment variables take precedence over `.env`.*

### 3. Seed Sample Data (Optional)

Loads the 9 sample documents into `employees` and `attendance_logs`:

```bash
python sample_seed.py
```

### 4. Start the Service

```bash
uvicorn app.main:app --port 8000 --reload
```

- **Health Probe**: `GET http://localhost:8000/health` (returns `200 OK` when MongoDB is connected, `503` if unreachable)
- **Interactive API Docs (Swagger UI)**: `http://localhost:8000/docs`
- **ReDoc Documentation**: `http://localhost:8000/redoc`

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Database readiness probe |
| `POST` | `/employees` | Create an employee profile with shift configuration |
| `GET` | `/employees` | List employees (paginated, department filter) |
| `POST` | `/attendance/punch-in` | Record a punch-in with IST date resolution and grace period calculation |
| `GET` | `/attendance` | List attendance records (paginated, employee/date/status filters) |
| `POST` | `/attendance/punch-out` | Record punch-out, computing work hours, overtime, and half-day flag |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize record, recomputing derived fields and appending audit history |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly employee summary with dynamic Mon–Fri working days |
| `GET` | `/analytics/departments/summary` | Department monthly summary including 0-log active headcount |
| `GET` | `/analytics/leaderboard/late` | Late leaderboard using competition ranking (`$rank: {}`) |
| `GET` | `/analytics/departments/{department}/trend` | Daily attendance trend with calendar gap filling (`$densify`) and 7-day moving average |
| `GET` | `/admin/explain/{endpoint}` | MongoDB query executionStats proving index utilization (`IXSCAN`) |

---

## Key Design & Implementation Notes

- **Single-File Architecture**: All API models, route handlers, database initialization, and aggregation pipelines reside in [`app/main.py`](app/main.py).
- **Startup Indexes**: Created idempotently during application startup to support sorting, foreign key lookups, and aggregation pipelines:
  - `employees`: `emp_code` (unique), `(department, joined_on)`, `joined_on`.
  - `attendance_logs`: `(emp_code, date)` (unique), `(date, emp_code)`, `(emp_code, date, status)`, `(status, date, late_minutes)`.
- **Concurrency & Race Condition Defenses**:
  - Punch-in uniqueness is enforced at the database layer via compound unique index `(emp_code, date)`, intercepting `DuplicateKeyError` and returning HTTP 409 Conflict.
  - Punch-out utilizes atomic `find_one_and_update({"punch_out": None}, ...)` to prevent duplicate checkouts.
  - Regularization uses optimistic checks to prevent lost updates to the audit trail.
- **Precision Rounding**: All decimal calculations (work hours, percentages, moving averages) utilize `decimal.Decimal` with explicit `ROUND_HALF_UP` policy to prevent Python Banker's rounding discrepancies.
- **Query Optimization**: Every query and aggregation pipeline is backed by indexes. Verified through `GET /admin/explain/{endpoint}` showing index scans (`IXSCAN`) and zero collection scans (`COLLSCAN`).
- **Health Probing & Cloud Keep-Alive**: The `/health` endpoint accepts both `GET` and `HEAD` requests to ensure compatibility with uptime monitors (e.g. UptimeRobot). Additionally, a background task automatically pings the service every 10 minutes when deployed on cloud hosts (such as Render) using `RENDER_EXTERNAL_URL` to prevent idle container spin-down.

---

## Deliverables & Documentation

- [`app/main.py`](app/main.py) — Complete application code.
- [`REVIEW.md`](REVIEW.md) — Defect catalog identifying 12 starter bugs with reproduction cases and resolutions (Part A).
- [`DECISIONS.md`](DECISIONS.md) — Architectural rationale answering the 5 technical design questions (Part C).
- [`doc/architecture.md`](doc/architecture.md) — System topology and schema documentation.
- [`doc/postman_collection.json`](doc/postman_collection.json) — Importable Postman collection for manual and automated API verification.
