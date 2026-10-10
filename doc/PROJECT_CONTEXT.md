# PROJECT_CONTEXT.md: Master Context & Source of Truth

> **System Notice**: This file is the authoritative project context for the **HROne Employee Attendance & Analytics API** engineering assignment. All reasoning, implementations, tests, and documentation must strictly adhere to the constraints, contracts, and business rules documented here.
> **Zero Hallucination Directive**: Do not assume, invent, or modify field names, schemas, status codes, query parameters, or business calculations. Every requirement is grounded directly in `candidate_kit/PROBLEM_STATEMENT.docx`, `candidate_kit/openapi.yaml`, and `candidate_kit/DATA_MODEL.md`.

---

## 1. Project Overview & Meta Information

- **Company / Client**: HROne
- **Role / Track**: Software Engineer Hiring | Contract v2.0
- **Assignment**: Employee Attendance & Analytics API (FastAPI + MongoDB)
- **Evaluation Weighting**:
  - **Part A: Starter Review (10%)** — Cataloging defects in `REVIEW.md` and resolving them.
  - **Part B: CRUD, Rules R1–R5, Concurrency (30%)** — Accurate endpoints, race-safe operations, boundary handling.
  - **Part B: Analytics Aggregations (30%)** — 4 high-performance MongoDB aggregation pipelines (no Python looping).
  - **Part C: MongoDB Indexes & Explain (15%)** — Startup index creation, zero `COLLSCAN` on 100k documents.
  - **Part C: Decisions & Code Quality (15%)** — Answering 5 key architectural questions in `DECISIONS.md`.
- **Primary Deliverable**: Single application file `app/main.py` + supporting submission files (`requirements.txt`, `REVIEW.md`, `DECISIONS.md`, `README.md`).

---

## 2. Infrastructure & Environment State

- **Database Engine**: MongoDB Atlas (Cloud Replica Set)
  - **Cluster**: `attendance-cluster.kpb9stv.mongodb.net`
  - **Target Database**: `attendance_dev`
  - **Collections**: `employees`, `attendance_logs`
  - **Connectivity Verification**: Ping command verified (`{'ok': 1}`).
  - **Sample Data**: 6 employees and 9 attendance logs seeded via `sample_seed.py`.
- **Application Runtime**:
  - Python 3.14 on Windows
  - Key libraries: `fastapi (0.143.0)`, `uvicorn (0.54.0)`, `pymongo (4.18.3)`, `pydantic (2.14.0)`, `python-dotenv (1.2.4)`.
- **Startup Command**:
  ```powershell
  uvicorn app.main:app --port 8000 --reload
  ```
- **Configuration Precedence**:
  Real environment variables override local `.env`. Never commit secrets or `.env` files to git.

---

## 3. Strict Architectural & Implementation Invariants

1. **Single-File Constraint**:
   All FastAPI routes, Pydantic models, helper functions, and MongoDB initialization must reside in `app/main.py`.
2. **No External Network Calls**:
   The service communicates exclusively with MongoDB. No external third-party APIs.
3. **Identity & Addressing**:
   - Employees are identified by human-readable, unique `emp_code` (e.g. `EMP0001`), NEVER MongoDB's `_id`.
   - Attendance records have no ID field; they are addressed by the natural compound key `(emp_code, date)`.
   - `_id` and internal `id` must never appear in API responses or requests.
4. **Instant vs Calendar Types**:
   - **Instants**: Epoch milliseconds (integer $\ge 100000000000$). Stored in MongoDB as BSON datetime (UTC). Reject strings, floats, seconds with HTTP 422.
   - **Calendar Dates**: String `"YYYY-MM-DD"` (IST).
   - **Shift Times**: String `"HH:MM"` (24-hour IST).
   - **Truncation**: Instants truncated to whole integer seconds before calculations, storage, or return (R1).
5. **Precision & Rounding**:
   - Python built-in `round()` is Banker's rounding (round-half-to-even) and is **strictly prohibited**.
   - All rounding uses `decimal.Decimal` with `ROUND_HALF_UP`:
     - Counts, work hours, percentages: 2 decimal places.
     - Rates (`attendance_rate`, `moving_avg_7d`): 4 decimal places.
6. **Concurrency Safety**:
   - Check-then-insert is forbidden.
   - Concurrent punch-ins for the same date must yield exactly one 201 and others 409 (enforced by unique compound index).
   - Concurrent punch-outs must yield exactly one 200 (enforced by atomic `find_one_and_update({"punch_out": None}, ...)`).
   - Concurrent regularization PATCH requests must not overwrite audit history.
7. **Pure Aggregations for Analytics**:
   - Analytics calculations (`/analytics/*`) must be executed directly on the MongoDB database engine using aggregation pipelines.
   - Fetching documents into Python and looping in memory is strictly forbidden.

---

## 4. Master Document Index

The project architecture and documentation are partitioned into 6 focused specifications:
- [`PRD.md`](file:///d:/HRone%20assigment/PRD.md): Product Requirements, user personas, functional & non-functional requirements.
- [`architecture.md`](file:///d:/HRone%20assigment/architecture.md): Topology, data models, indexing plan, and aggregation pipeline structures.
- [`rules.md`](file:///d:/HRone%20assigment/rules.md): Exhaustive business rules bible (R1 to R10) with exact numerical edge cases.
- [`phases.md`](file:///d:/HRone%20assigment/phases.md): Phased implementation roadmap with milestones and verification criteria.
- [`design.md`](file:///d:/HRone%20assigment/design.md): Detailed API contract design, Pydantic schemas, and query specifications.
- [`memory.md`](file:///d:/HRone%20assigment/memory.md): Running technical memory, decisions log, Atlas setup record, and pitfalls.

---

## 5. Ongoing Status & Checkpoint Log

- **2026-10-10**: Verified Atlas cluster connection; seeded sample database; completed audit of starter `app/main.py`; established 7-document architecture and context baseline.
