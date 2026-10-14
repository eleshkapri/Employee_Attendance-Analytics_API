# Employee Attendance & Analytics API (FastAPI + MongoDB)

Production-grade REST API backend for employee attendance tracking, manual regularization, and workforce analytics built with **FastAPI** and **MongoDB Atlas (6.0+)**, implementing full business rule enforcement (Rules R1–R10), idempotent startup indexing, and zero-collection-scan aggregation pipelines.

---

## 1. Submission Checklist (Section 12 Compliance)

| Requirement | Status | Verification Detail |
|---|:---:|---|
| **Clean Clone Startup** | ✅ **Passed** | Service boots cleanly via `uvicorn app.main:app --port 8000` after `pip install -r requirements.txt`. |
| **Startup Indexes** | ✅ **Passed** | All 6 compound & unique indexes are created idempotently on startup lifespan; no manual DB operations required. |
| **Environment Variable Precedence** | ✅ **Passed** | `MONGO_URI` and `MONGO_DB` read from environment; real OS environment variables override `.env`; zero hard-coded credentials. |
| **Part A Deliverable (`REVIEW.md`)** | ✅ **Passed** | Comprehensive audit cataloging 12 starter defects with line numbers, reproduction inputs, and resolutions. |
| **Part C Deliverable (`DECISIONS.md`)** | ✅ **Passed** | Clear, technical answers to all 5 architectural questions (~215 words total). |
| **No Secrets / Artifacts Committed** | ✅ **Passed** | `.env`, secrets, Dockerfile, virtual environments, and caches strictly excluded by `.gitignore`. |
| **Public GitHub Repository** | ✅ **Passed** | Repository is public and immediately cloneable. |

---

## 2. Evaluation Criteria & Deliverables Mapping (Section 11)

| Area | Weight | Deliverable | How It Is Implemented & Checked |
|---|:---:|---|---|
| **Review (Part A)** | **10%** | [`REVIEW.md`](REVIEW.md) | 12 defects identified, categorized, and resolved with concrete failing examples and verification steps. |
| **CRUD, Rules & Concurrency (Part B)** | **30%** | [`app/main.py`](app/main.py) | Full R1–R5 calculation engine, 1-indexed pagination, atomic race-safe punch-out, and regularization with optimistic locking. |
| **Analytics Correctness (Part B)** | **30%** | [`app/main.py`](app/main.py) | 4 pure MongoDB aggregation pipelines: mid-month joiner Mon–Fri dynamic days (R7), 0-log headcount (R9), competition ranking (`$rank: {}`), and densified trend (`$densify`). |
| **MongoDB Usage & Speed (Part C)** | **15%** | `GET /admin/explain/{endpoint}` | Startup compound indexes; verified `IXSCAN` and **zero `COLLSCAN`** across all 5 queries on 100k+ documents. |
| **Decisions & Code Quality (Part C)** | **15%** | [`DECISIONS.md`](DECISIONS.md) | Single-file architecture, strict Pydantic v2 input validation, UTC instant boundaries, and answers to all 5 core questions. |

---

## 3. Environment Contract & Quickstart (Section 4)

### 3.1 Prerequisites
- Python 3.11+
- MongoDB 6.0+ (Atlas cloud replica set or local instance)

### 3.2 Installation

```bash
# 1. Clone repository
git clone https://github.com/eleshkapri/employee-attendance-analytics-api.git
cd employee-attendance-analytics-api

# 2. Create and activate virtual environment
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# 3. Install pinned dependencies
pip install -r requirements.txt
```

### 3.3 Configuration

Copy `.env.example` to `.env` or set environment variables directly:

```bash
cp .env.example .env
```

| Variable | Description | Example / Default |
|---|---|---|
| `MONGO_URI` | MongoDB connection URI | `mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority` |
| `MONGO_DB` | Target database name | `attendance_dev` or `attendance_db` |

> Real environment variables take precedence over the local `.env` file.

### 3.4 Seed Sample Data (Optional)

Loads the 9 annotated sample documents into `employees` and `attendance_logs`:

```bash
python sample_seed.py
```

### 3.5 Run the Service

```bash
uvicorn app.main:app --port 8000 --reload
```

- **Readiness Probe**: `GET http://localhost:8000/health` returns `200 OK` within 20 seconds only when MongoDB answers a live `ping`.
- **Interactive API Docs (Swagger UI)**: `http://localhost:8000/docs`
- **Alternative Docs (ReDoc)**: `http://localhost:8000/redoc`
- **Postman Collection**: Import [`doc/postman_collection.json`](doc/postman_collection.json) to execute pre-configured test requests across all 12 routes.

---

## 4. The API at a Glance (Section 7 Contract)

| # | Method | Path | Summary | Contract Role | Business Rules Enforced |
|:---:|:---:|---|---|:---:|---|
| **1** | `GET` | `/health` | Live MongoDB ping probe (200 OK / 503 Unavailable) | Starter (Fixed) | Database liveness |
| **2** | `POST` | `/employees` | Create employee; regex validation; 409 on duplicate | Starter (Fixed) | TOCTOU race safety, `shift_start != shift_end` |
| **3** | `GET` | `/employees` | List employees with 1-indexed pagination; filter by department | Starter (Fixed) | R10 pagination, filtered `total` count |
| **4** | `POST` | `/attendance/punch-in` | Punch-in record creation; race-safe compound unique index | Starter (Fixed) | R1 IST date anchoring, R2 strict 10m grace period |
| **5** | `GET` | `/attendance` | List attendance logs; cursor streaming; date range validation | Starter (Fixed) | Sorted by `date` desc, `emp_code` asc; R10 pagination |
| **6** | `POST` | `/attendance/punch-out` | Close open attendance log; atomic race safety; derived metrics | **You** (Built) | R3 overtime ($\ge 30$m), R4 Decimal work hours, R5 half-day |
| **7** | `PATCH` | `/attendance/{emp_code}/{date}` | Regularize attendance record; append immutable audit trail | **You** (Built) | R1–R5 recomputation, status transition validation, optimistic locking |
| **8** | `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly employee attendance summary aggregation | **You** (Built) | R7 Mon–Fri working days from `joined_on`, R8 rounding |
| **9** | `GET` | `/analytics/departments/summary` | Department summary aggregation across both collections | **You** (Built) | R9 headcount counts 0-log staff, average over records |
| **10** | `GET` | `/analytics/leaderboard/late` | Late leaderboard with standard competition ranking | **You** (Built) | `$rank: {}` window ranking (1, 2, 2, 4), preserves ties at cutoff |
| **11** | `GET` | `/analytics/departments/{department}/trend` | Daily attendance trend with moving average | **You** (Built) | Native `$densify` gap filling, 7-day moving average |
| **12** | `GET` | `/admin/explain/{endpoint}` | MongoDB explain output (`executionStats`) for main queries | **You** (Built) | Proves `IXSCAN` and **zero `COLLSCAN`** |

---

## 5. Business Rules Enforcement (Section 6: R1–R10)

| Rule | Specification | Implementation Architecture |
|---|---|---|
| **R1** | **IST & Attendance Date** | Dates and shifts are IST (UTC+05:30). Instants are truncated to whole seconds before calculation/storage. Overnight shifts (`shift_end <= shift_start`): punch-in earlier than `shift_end` anchors to the previous calendar day. |
| **R2** | **Late Grace Period** | 10m00s grace period is strict: punch-in at `09:40:00` = 0 late mins; `09:40:01` = 10 late mins (floor from `shift_start`). |
| **R3** | **Overtime** | Whole floor minutes from `shift_end` to `punch_out`, counted only if $\ge 30$; otherwise 0. Properly handles overnight shift end dates. |
| **R4** | **Work Hours** | `(punch_out - punch_in) / 3600`, rounded strictly using `decimal.Decimal` with `ROUND_HALF_UP` to 2 decimals. Prevents Python Banker's rounding defects. |
| **R5** | **Half Day** | Flagged `true` when rounded `work_hours < 4.50`. Counts as 0.5 present day in monthly analytics. |
| **R6** | **Presence** | `PRESENT`, `WFH`, `ON_DUTY` count as present; `ABSENT`, `LEAVE` do not. |
| **R7** | **Working Days** | Monday through Friday only. Dynamically calculated starting from `joined_on` for mid-month joiners. Weekend records add to overtime/late totals but not working days. |
| **R8** | **Reported Precision** | Standard Half-Up rounding: 2 decimals for totals and percentages; 4 decimals for rates (`attendance_rate`, `moving_avg_7d`). |
| **R9** | **Headcount** | Evaluated from `employees` with `joined_on <= period_end`. Accurately counts staff members even if they have 0 attendance logs. |
| **R10** | **Pagination** | 1-indexed (`page >= 1`), `page_size` default 20, max 100. `total` strictly reflects active filters. |

---

## 6. Concurrency & Race Condition Architecture (Section 9)

1. **Simultaneous Punch-Ins (Same Employee & Date)**:
   - Application-level check-then-insert is forbidden.
   - Enforced by a compound unique index on `attendance_logs(emp_code, date)` at the database storage engine layer.
   - Winner inserts with `201 Created`; concurrent loser raises `pymongo.errors.DuplicateKeyError`, intercepted and returned as `409 Conflict`. Zero duplicate records are created.
2. **Simultaneous Punch-Outs (Same Employee & Open Log)**:
   - Atomic `find_one_and_update({"emp_code": emp_code, "punch_out": None}, ...)` ensures exactly one concurrent request succeeds with `200 OK`.
   - Subsequent or parallel requests find no matching open record and return `409 Conflict`.
3. **Concurrent Regularizations (`PATCH`)**:
   - Addressed via natural composite key `(emp_code, date)`.
   - Immutable audit trail entries appended to `history` with `{at, by, reason, changes: {field: {from, to}}}`.
   - Protected against lost updates using optimistic concurrency checks on record state.

---

## 7. MongoDB Startup Indexes & Zero COLLSCAN Proof (Section 10 - Part C)

All indexes are created idempotently inside FastAPI's startup lifespan:

```python
# employees collection
db.employees.create_index([("emp_code", 1)], unique=True)
db.employees.create_index([("department", 1), ("joined_on", 1)])
db.employees.create_index([("joined_on", 1)])

# attendance_logs collection
db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
db.attendance_logs.create_index([("emp_code", 1), ("date", 1), ("status", 1)])
db.attendance_logs.create_index([("status", 1), ("date", 1), ("late_minutes", 1)])
```

### Explain Endpoint Verification (`GET /admin/explain/{endpoint}`)

The explain endpoint runs `.explain("executionStats")` on each main query pipeline against 100k+ documents:

| Endpoint Tested | Main Collection | Winning Plan Stage | Index Key Used | `COLLSCAN` Present? |
|---|---|:---:|---|:---:|
| `attendance_list` | `attendance_logs` | `FETCH` $\leftarrow$ `IXSCAN` | `{"date": -1, "emp_code": 1}` | **No (0)** |
| `employee_monthly` | `attendance_logs` | `IXSCAN` | `{"emp_code": 1, "date": 1, "status": 1}` | **No (0)** |
| `department_summary` | `employees` | `FETCH` $\leftarrow$ `IXSCAN` | `{"department": 1, "joined_on": 1}` | **No (0)** |
| `late_leaderboard` | `attendance_logs` | `FETCH` $\leftarrow$ `IXSCAN` | `{"date": -1, "emp_code": 1}` | **No (0)** |
| `department_trend` | `attendance_logs` | `FETCH` $\leftarrow$ `IXSCAN` | `{"date": -1, "emp_code": 1}` | **No (0)** |

---

## 8. Summary of Deliverables & Repository Structure (Section 2)

```
employee-attendance-analytics-api/
│
├── app/
│   ├── __init__.py           # Package marker (enables uvicorn app.main:app imports)
│   └── main.py               # Complete single-file backend (All routes, models, pipelines & indexes)
│
├── candidate_kit/            # Official assignment specification package
│   ├── PROBLEM_STATEMENT.docx# Master hiring contract and problem statement
│   ├── openapi.yaml          # Formal OpenAPI 3.0 API schema and business rules contract
│   ├── DATA_MODEL.md         # Field types, seeded data rules, and 9 sample documents
│   └── extracted_problem_statement.txt # Plaintext extracted problem statement reference
│
├── sample_data/              # Seed data files
│   ├── employees.json        # 6 sample employee documents (Extended JSON)
│   └── attendance_logs.json  # 9 sample attendance documents (Extended JSON)
│
├── doc/                      # Project engineering documentation & Postman suite
│   ├── postman_collection.json # Ready-to-import Postman Collection v2.1.0 (all 12 endpoints)
│   ├── PROJECT_CONTEXT.md    # Master context and zero-hallucination baseline
│   ├── PRD.md                # Product Requirements Document
│   ├── architecture.md       # System architecture and indexing blueprint
│   ├── rules.md              # Business rules specification bible (R1–R10)
│   ├── phases.md             # Phased implementation and verification roadmap
│   ├── design.md             # Pydantic schemas and API payload contracts
│   └── memory.md             # Ongoing technical memory and commit history log
│
├── REVIEW.md                 # Part A Deliverable (10%): Catalog of 12 starter defects & fixes
├── DECISIONS.md              # Part C Deliverable (15%): Architectural answers to 5 core questions
├── README.md                 # Primary documentation, setup guide, and submission compliance
├── requirements.txt          # Pinned production dependencies
├── sample_seed.py            # Database seeder utility
├── .env.example              # Environment variables template
└── .gitignore                # Protects .env, virtual environments, secrets, and caches
```

---

## 9. Architectural Decisions Summary (`DECISIONS.md`)

- **Question 1 (Indexes)**: Compound indexes aligned with filter/sort prefixes (`{emp_code: 1, date: 1}`, `{date: -1, emp_code: 1}`). Standalone `late_minutes` index rejected due to low selectivity and index bloat.
- **Question 2 (Punch-In Race)**: Database-level unique compound index on `(emp_code, date)` serializes requests at the wiredTiger storage layer; loser triggers `DuplicateKeyError` converted to `HTTP 409`.
- **Question 3 (Leaderboard Ties)**: Standard competition ranking via `$setWindowFields` with `$rank: {}` (1, 2, 2, 4); `$match: {rank: {$lte: limit}}` ensures all tied candidates at cutoff are retained.
- **Question 4 (Zero-Log Headcount)**: Cross-collection `$lookup` starting from `employees` with `$unwind: {preserveNullAndEmptyArrays: true}` guarantees employees with zero attendance records are included in active headcount.
- **Question 5 (100x Scale Architecture)**: Pre-aggregate daily attendance summaries into a materialized collection via MongoDB Change Streams, avoiding real-time multi-stage `$lookup` and `$densify` passes over 10M+ records.
