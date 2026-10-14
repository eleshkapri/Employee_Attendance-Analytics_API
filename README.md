# Employee Attendance & Analytics API

FastAPI and MongoDB (Atlas / 6.0+) service implementing attendance tracking, regularization, and workforce analytics with full business rule enforcement (R1–R10).

---

## 1. Installation

```bash
# Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Configuration

Copy `.env.example` to `.env` or provide environment variables directly:

```bash
cp .env.example .env
```

Environment variables:
- `MONGO_URI`: MongoDB connection string (e.g. `mongodb+srv://...` or `mongodb://localhost:27017`)
- `MONGO_DB`: Target database name (default: `attendance_db` or `attendance_dev`)

---

## 3. Seed Sample Data (Optional)

```bash
python sample_seed.py
```

---

## 4. Run the Service

```bash
uvicorn app.main:app --port 8000 --reload
```

Interactive API documentation is available at:
`http://localhost:8000/docs`

---

## 5. Endpoints Overview

- `GET /health`: Health check and live database ping (returns 200 or 503).
- `POST /employees`: Create employee (validates email, shift times, joined_on; 409 on duplicate).
- `GET /employees`: List employees with 1-based pagination.
- `POST /attendance/punch-in`: Record punch-in with R1-R2 business rule evaluation and conflict protection.
- `GET /attendance`: List attendance logs sorted by date descending, emp_code ascending.
- `POST /attendance/punch-out`: Close attendance record with atomic race condition protection, work hours (R4), overtime (R3), and half-day (R5).
- `PATCH /attendance/{emp_code}/{date}`: Regularize attendance with full audit history logging and optimistic concurrency locking.
- `GET /analytics/employees/{emp_code}/monthly`: Employee monthly summary with dynamically computed Mon–Fri working days (R7).
- `GET /analytics/departments/summary`: Department monthly summary across both collections, counting employees with zero logs (R9).
- `GET /analytics/leaderboard/late`: Late leaderboard with standard competition ranking (`$setWindowFields` / `$rank`).
- `GET /analytics/departments/{department}/trend`: Daily attendance trend with calendar densification (`$densify`) and 7-day moving average.
- `GET /admin/explain/{endpoint}`: MongoDB explain (`executionStats`) proving queries use index scans (`IXSCAN`) and zero collection scans (`COLLSCAN`).

---

## 6. Repository File Directory & Purpose of Each File

| File / Folder | Role & Purpose |
|---|---|
| **`app/__init__.py`** | Marks the `app/` folder as a Python package. It is **intentionally empty** because Python only requires this file to exist to enable module imports (e.g. `uvicorn app.main:app` or `from app.main import app`) without executing package-level side effects. |
| **`app/main.py`** | The complete single-file production backend containing all FastAPI routes, Pydantic request/response models, database connection, business rule calculation engines (R1–R10), and startup index creation. |
| **`REVIEW.md`** | **Part A Deliverable (10%)**: Comprehensive audit cataloging 12 starter defects across helper functions and endpoints, detailing their exact lines, reproduction inputs, and fixes. |
| **`DECISIONS.md`** | **Part C Deliverable (15%)**: Concise architectural rationale answering the 5 required questions (indexing strategy, punch-in race condition handling, leaderboard ties, 0-log headcount counting, and 100x data scaling). |
| **`README.md`** | Primary setup guide, configuration instructions, endpoint directory, and codebase file reference. |
| **`requirements.txt`** | Pinned minimal production Python dependencies (`fastapi`, `uvicorn`, `pymongo`, `pydantic`, `python-dotenv`). |
| **`sample_seed.py`** | Utility script to load the 9 sample documents into MongoDB collections (`employees`, `attendance_logs`). |
| **`.env.example`** | Safe template showing required environment variable keys without exposing real credentials. |
| **`.env`** | Local environment file with actual MongoDB Atlas credentials. **Never committed to Git** (ignored by `.gitignore`). |
| **`.gitignore`** | Enforces security by preventing `.env`, secrets, virtual environments, compiled caches (`__pycache__`), and temporary test files from being committed. |
| **`candidate_kit/`** | Contains original assignment specification files: `PROBLEM_STATEMENT.docx`, `openapi.yaml`, `DATA_MODEL.md`, and `extracted_problem_statement.txt`. |
| **`sample_data/`** | Contains the sample Extended JSON files (`employees.json`, `attendance_logs.json`) used by `sample_seed.py`. |
| **`doc/`** | In-depth engineering specifications and context: |
| ├── `PROJECT_CONTEXT.md` | Master project context, source of truth, and strict invariant guidelines. |
| ├── `PRD.md` | Product Requirements Document outlining personas, functional & non-functional requirements. |
| ├── `architecture.md` | System topology, schema definitions, indexing blueprint, and pipeline stages. |
| ├── `rules.md` | Complete business rules specification bible (Rules R1 to R10). |
| ├── `phases.md` | Phased implementation roadmap and verification criteria. |
| ├── `design.md` | Pydantic validation schemas, regex specifications, and API payload contracts. |
| ├── `memory.md` | Continuous technical memory log recording milestone completions and commit history. |
| └── `postman_collection.json` | Ready-to-import Postman Collection v2.1.0 with pre-filled test requests for all 12 endpoints. |
