# Project Technical Memory & State Log

> **PERMANENT OPERATIONAL DIRECTIVE**: This `doc/memory.md` file must be continuously updated at regular intervals across every phase and conversation turn to preserve long-term context, record architectural decisions, log git commits, and prevent AI drift or hallucination.

## Project: Employee Attendance & Analytics API
**Current Milestone**: Phase 12 Completed — Submission Order & Selection Alignment Verified & Pushed  
**Last Updated**: 2026-10-14 11:22:00 IST

---

## 1. Database Connection & Environment State

- **MongoDB Deployment**: MongoDB Atlas Cloud Replica Set
  - Host: `attendance-cluster.kpb9stv.mongodb.net`
  - Target Database: `attendance_dev`
  - Collections: `employees`, `attendance_logs`
  - Connectivity: Verified active via `client.admin.command('ping')` $\rightarrow$ `{'ok': 1}`.
  - Initial Seeding: Loaded 6 employees and 9 attendance documents from `sample_data/` via `sample_seed.py`.
- **Startup Indexes Created & Verified**:
  - `employees`:
    - `{"emp_code": 1}` (`unique=True`)
    - `{"department": 1, "joined_on": 1}`
  - `attendance_logs`:
    - `{"emp_code": 1, "date": 1}` (`unique=True`)
    - `{"date": -1, "emp_code": 1}`
    - `{"emp_code": 1, "date": 1, "status": 1}`
    - `{"status": 1, "date": 1, "late_minutes": 1}`

---

## 2. Technical Decisions & Invariant Log

1. **Precision & Rounding Architecture**:
   - Python's standard `round(x, 2)` produces Banker's rounding (round half to even), failing on values like `2.505` $\rightarrow$ `2.50` instead of `2.51`.
   - Decision: All rounding operations utilize `decimal.Decimal` with explicit `ROUND_HALF_UP` policy (`quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`).
2. **Race Condition Prevention Strategy**:
   - Application-level check-then-insert (`if find_one(): insert()`) is strictly prohibited.
   - Decision: Enforce database-level uniqueness via MongoDB unique index on `employees.emp_code` and compound unique index on `attendance_logs.(emp_code, date)`. Intercept `DuplicateKeyError` and map to `HTTP 409 Conflict`.
   - Decision: Punch-out will utilize atomic `find_one_and_update` with filter `{"punch_out": None}` to ensure only one concurrent request succeeds with `HTTP 200`, while others return `HTTP 409 Conflict`.
3. **Leaderboard Ranking Scheme**:
   - Standard competition ranking is mandatory (1, 2, 2, 4) — ties share a rank, and subsequent ranks are skipped.
   - Decision: Utilizes MongoDB aggregation window function `$setWindowFields` with `output: { rank: { $rank: {} } }`.
   - Decision: Query limit is applied **after** ranking with `{"rank": {"$lte": limit}}`, ensuring all tied participants at cutoff remain present in the result.
4. **Department Summary Zero-Log Headcount**:
   - Rule R9 requires headcount to count all employees joined on or before month end, even if they have 0 attendance records.
   - Decision: Base aggregation starts on the `employees` collection (filtering `joined_on <= end_of_month`), performing a `$lookup` into `attendance_logs` with `$unwind: { preserveNullAndEmptyArrays: true }`.
5. **Department Trend Gap-Filling**:
   - Date range must include every calendar day, including weekends and days with 0 attendance logs.
   - Decision: Uses MongoDB `$densify` stage to densify dates, followed by `$setWindowFields` to compute the 7-day moving average.
6. **Date & Time Boundaries**:
   - Naive BSON datetimes from PyMongo are normalized to UTC `datetime` objects before computing epoch milliseconds.
   - API inputs are integer epoch milliseconds; reject float or seconds inputs ($< 10^{11}$) with HTTP 422.

---

## 3. Completed Milestones & Phase Progress

### Phase 1: Environment Alignment & Documentation (Done)
- Created all 6 planning files (`PRD.md`, `architecture.md`, `rules.md`, `phases.md`, `design.md`, `memory.md`) and master context `PROJECT_CONTEXT.md` in the dedicated [`doc/`](file:///d:/HRone%20assigment/doc) folder.
- Root workspace synchronized with clean candidate kit structure and `.gitignore`.

### Phase 2: Part A — Starter Audit & REVIEW.md (Done)
- Refactored `app/main.py`:
  - Lifespan index creation implemented.
  - Helper functions for R1 (seconds truncation, IST date, overnight shift), R2 (10m00s strict grace period), R3 (overtime $\ge 30$ mins), R4 (Decimal half-up work hours), R5 (half-day $< 4.50$).
  - `GET /health` with real ping and 503 fallback.
  - `POST /employees` with unique index race protection and validation.
  - `GET /employees` with 1-indexed pagination skip, sorting, and filtered count.
  - `POST /attendance/punch-in` with 404 employee check, overnight date resolution, and compound unique index.
  - `GET /attendance` with cursor streaming and `date_from > date_to` 422 check.
- Automated Test Suite executed (`scratch/test_phase2.py`):
  - Passed all 9 unit & integration tests against live MongoDB Atlas cluster.
- Documented all 12 defects in [`REVIEW.md`](file:///d:/HRone%20assigment/REVIEW.md).

### Pre-Phase 3 Workspace Sanitization (Done)
- Removed duplicate backup folder `docs/` to eliminate split-brain edits.
- Removed duplicate files inside `candidate_kit/` (`app/`, `sample_data/`, `.env`, `REVIEW.md`, `DECISIONS.md`, `README.md`, `requirements.txt`, `sample_seed.py`).
- Preserved authoritative specification and contract files in `candidate_kit/`:
  - `PROBLEM_STATEMENT.docx`
  - `openapi.yaml`
  - `DATA_MODEL.md`
  - `extracted_problem_statement.txt`
- Removed compiled `__pycache__` directories.
- Moved planning documents and context into dedicated `doc/` directory.
- Re-tested entire endpoint suite; verified 100% passing tests.

### Phase 3: Core CRUD & Regularization (Done)
- Implemented `POST /attendance/punch-out`:
  - Locates latest open attendance record (`punch_in <= punched_at`).
  - Race-safe atomic update via `find_one_and_update({"punch_out": None})` ensuring concurrent attempts produce exactly one 200 and subsequent 409.
  - Enforces 24-hour boundary and `punched_at > punch_in` (422).
  - Dynamically computes `work_hours` (Decimal half-up R4), `overtime_minutes` (R3), and `half_day` (R5).
- Implemented `PATCH /attendance/{emp_code}/{date}` (Regularization):
  - Natural key addressing `(emp_code, date)`.
  - Comprehensive status validation: clearing punches on `ABSENT`/`LEAVE`, requiring `punch_in` for presence statuses.
  - Enforces `punch_in` remains anchored to the attendance date (R1).
  - Enforces positive intervals and $\le 24$h window.
  - Recomputes all derived fields (`late_minutes`, `work_hours`, `overtime_minutes`, `half_day`).
  - Appends immutable audit entry to `history` array with `{at, by, reason, changes: {field: {from, to}}}`.
  - Optimistic locking protects against concurrent history clobbering.
- Automated Test Suite executed (`scratch/test_phase3.py`):
  - Passed all punch-out validation, calculation, and concurrency tests.
  - Passed all regularization boundary, audit formatting, and derived field tests.

### Git Version Control Milestones
- **Commit 1 (`e591419`)**: `chore(phase-1): initialize repository structure, candidate kit, and project specifications`
- **Commit 2 (`9fb6e70`)**: `feat(phase-2): resolve starter defects, implement rules R1-R5 engine, and complete review catalog (Part A)`
- **Commit 3 (`503e340`)**: `feat(phase-3): implement punch-out and regularization CRUD with audit logging`

### Phase 4: Workforce Analytics Aggregation Pipelines (Done)
- Implemented `GET /analytics/employees/{emp_code}/monthly`:
  - Working days dynamically computed starting from `joined_on` for mid-month joiners (R7).
  - Pure aggregation on `attendance_logs` calculating `present_days` (Mon–Fri only, half-day = 0.5), `leave_days`, `late_count`, totals, and `attendance_pct` (R8 rounding).
- Implemented `GET /analytics/departments/summary`:
  - Cross-collection aggregation (`employees` $\bowtie$ `attendance_logs`).
  - Active headcount accurately includes employees with 0 attendance logs (R9).
  - `avg_work_hours` computed strictly over records (not mean of employee averages).
- Implemented `GET /analytics/leaderboard/late`:
  - Standard competition ranking (1, 2, 2, 4) using MongoDB window function `$setWindowFields` with `$rank`.
  - Limit applied after ranking, preserving all ties at boundary cutoff.
- Implemented `GET /analytics/departments/{department}/trend`:
  - Gap-filling calendar days using native MongoDB `$densify` with bounds across full date range.
  - Per-day headcount via `$lookup` on `joined_on <= date`.
  - Attendance rate on working days with headcount > 0 (4 decimals half-up); null on weekends.
  - 7-day moving average over non-null rates using `$setWindowFields`.
- Automated Test Suite executed (`scratch/test_phase4.py`):
  - Passed all 4 analytics aggregations, validations, and edge cases against MongoDB Atlas.

### Git Version Control Milestones
- **Commit 1 (`e591419`)**: `chore(phase-1): initialize repository structure, candidate kit, and project specifications`
- **Commit 2 (`9fb6e70`)**: `feat(phase-2): resolve starter defects, implement rules R1-R5 engine, and complete review catalog (Part A)`
- **Commit 3 (`503e340`)**: `feat(phase-3): implement punch-out and regularization CRUD with audit logging`
- **Commit 4 (`21c11da`)**: `feat(phase-4): implement workforce analytics aggregation pipelines`
- **Commit 5 (`20d8ab8`)**: `feat(phase-5): implement startup indexes and admin explain endpoint with zero COLLSCAN (Part C)`
- **Commit 6 (`e9540f4`)**: `docs(phase-6): complete architectural decisions and polish setup documentation (Part C)`

---

### Phase 5: Startup Indexes & Explain Endpoint (Part C, Done)
- Startup Idempotent Indexes in `app/main.py`:
  - `employees`:
    - `{"emp_code": 1}` (unique): Guarantees uniqueness; accelerates `$lookup` target foreign key scans.
    - `{"department": 1, "joined_on": 1}`: Backs department filtering and headcount evaluation.
    - `{"joined_on": 1}`: Eliminates `COLLSCAN` during multi-department monthly summaries.
  - `attendance_logs`:
    - `{"emp_code": 1, "date": 1}` (unique): Guarantees single punch-in per day; race safety; point lookups for regularization; `$lookup` foreign join.
    - `{"date": -1, "emp_code": 1}`: Backs default pagination sort, date range scans, and leaderboard pipeline.
    - `{"emp_code": 1, "date": 1, "status": 1}`: Compound index for monthly employee aggregations.
    - `{"status": 1, "date": 1, "late_minutes": 1}`: Compound index for late minutes aggregation and department metrics.
- Implemented `GET /admin/explain/{endpoint}`:
  - Supported endpoints: `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`.
  - Built matching pipelines and queries through shared helper functions: `build_attendance_query`, `build_employee_monthly_pipeline`, `build_department_summary_pipeline`, `build_late_leaderboard_pipeline`, `build_department_trend_pipeline`.
  - Executes `.explain("executionStats")` and serializes BSON types via `json_util`.
  - Returns required schema `{endpoint, collection, explain}`.
  - Strict validation: rejects missing required parameters with HTTP 422.
- Verified Execution Plans:
  - All 5 endpoints show `IXSCAN` / index seeks.
  - **Zero `COLLSCAN`s detected across all query variants and aggregations.**

---

### Phase 6: Documentation & Final Polish (Part C, Done)
- Filled [DECISIONS.md](file:///d:/HRone%20assigment/DECISIONS.md) with concise, technical answers to the 5 architectural questions (~215 words total):
  1. Indexes created and why; rejected standalone `late_minutes` index rationale.
  2. Punch-in race condition mechanics (atomic serialization at storage layer via unique compound index, `DuplicateKeyError` to `409 Conflict`).
  3. Leaderboard ties at limit cutoff (standard competition ranking `$rank: {}` via `$setWindowFields`, all tied candidates returned, deterministic secondary sort on `emp_code`).
  4. Department summary headcount counting employees with zero logs (`$lookup` from `employees` with `$unwind: {preserveNullAndEmptyArrays: true}`).
  5. 100x scale adaptation (pre-aggregating daily rollups via MongoDB change streams to eliminate real-time `$lookup`/`$densify` overhead).
- Polished [README.md](file:///d:/HRone%20assigment/README.md) with concise setup, environment variable configuration, seed command, run command, and endpoint directory.
- End-to-End Regression Suite (`scratch/test_complete_suite.py`):
  - Passed 100% across all 12 system, employee, attendance, analytics, and admin explain endpoints.

---

### Final Project Status
- **Part A (10%)**: Starter Audit & `REVIEW.md` (12 defects cataloged and resolved).
- **Part B (30%)**: Rules R1–R5, Punch-Out, Regularization CRUD with optimistic concurrency & audit trail.
- **Part B (30%)**: 4 Workforce Analytics MongoDB Aggregations (`employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`).
- **Part C (15%)**: Startup Indexes & `GET /admin/explain/{endpoint}` with zero `COLLSCAN`.
- **Part C (15%)**: `DECISIONS.md` architectural rationale & code quality.
- **Deliverables**: Single application file `app/main.py`, `REVIEW.md`, `DECISIONS.md`, `README.md`, `requirements.txt`, clean `.gitignore`, `doc/postman_collection.json`.

---

### Phase 7: Postman API Testing Suite & Codebase Commentary (Done)
- Generated ready-to-import Postman Collection v2.1.0 at [`doc/postman_collection.json`](file:///d:/HRone%20assigment/doc/postman_collection.json) covering all 12 API routes.
- Enhanced inline docstrings, architectural commentary, and clear section dividers throughout `app/main.py`.
- Re-verified complete test suite with 100% pass rate.
- Deliverables: Single application file `app/main.py`, `REVIEW.md`, `DECISIONS.md`, `README.md`, `requirements.txt`, clean `.gitignore`, `doc/postman_collection.json`.

---

### Phase 8: Final Submission Readiness & Grader Simulation Verification (Done)
- Verification against `PROBLEM_STATEMENT.docx` & `PROJECT_CONTEXT.md`:
  - Mathematical correctness test suite (`scratch/test_grader_simulation.py`):
    - R1 Millisecond truncation and overnight shift day anchoring.
    - R2 Grace period boundary tests: exactly 10m00s (0 late min) vs 10m01s (10 late min floor).
    - R3 Overtime boundary tests: exactly 29m59s (0 min) vs 30m00s (30 min).
    - R4 Decimal half-up work hours rounding (strictly avoiding Banker's rounding).
    - R5 Half-day flag (< 4.50 hrs) and 0.5-day contribution to presence.
    - R7 Dynamic Mon–Fri working days starting from `joined_on` for mid-month joiners.
    - R8 Standard half-up precision rounding for rates and percentages.
  - Comprehensive Input Validation Test Suite (`scratch/test_input_validation.py`):
    - Tested all negative validation cases (bad employee code, bad email, equal shift times, bad timestamps, invalid dates, invalid enum statuses, date range inversions, range > 92 days).
    - Verified all invalid inputs return HTTP 422 Unprocessable Entity (zero 500 errors).
  - Cleanliness Check:
    - Zero `.env` files or secrets committed (`.gitignore` protects secrets, `.postman/`, and `scratch/`).
    - Repository tree clean and ready for submission.

---

### Phase 9: MongoDB Atlas Reconnection & Workspace Sanitization (Done)
- **Atlas Live Connectivity**:
  - User configured Network Access in MongoDB Atlas.
  - Successfully verified Atlas cluster ping (`{'ok': 1}`) and FastAPI readiness probe (`GET /health` -> `200 OK {'status': 'ok'}`).
  - Full 12-endpoint regression suite re-executed against live Atlas replica set with 100% pass rate.
- **Workspace Sanitization**:
  - Removed all temporary folders and files: `.postman/`, `scratch/`, and `__pycache__`.
  - Maintained master source of truth and planning files in [`doc/`](file:///d:/HRone%20assigment/doc).
  - Clean working tree confirmed via `git status`.

---

### Phase 10: Repository File Directory & Documentation Polish (Done)
- Expanded [`README.md`](file:///d:/HRone%20assigment/README.md) with Section 6: comprehensive repository file map explaining the purpose of every file and directory.
- Documented the exact purpose of `app/__init__.py`: marks `app` as an importable Python package for `uvicorn app.main:app` and explains why standard Python conventions keep it intentionally empty.
- Clean working tree maintained.

---

### Phase 11: GitHub Repository Creation & Publication (Done)
- **Repository Established**: `https://github.com/eleshkapri/employee-attendance-analytics-api`
- **Visibility**: Public (per submission requirements).
- **Metadata Configured**:
  - Description: `Employee Attendance & Analytics API (FastAPI + MongoDB) — Production-grade REST API implementing attendance tracking, regularization, and workforce analytics with full business rule enforcement (R1–R10) and indexed aggregation pipelines.`
  - Topics/Tags: `aggregation-pipeline`, `attendance-tracking`, `fastapi`, `hrone`, `mongodb`, `mongodb-atlas`, `pymongo`, `python`, `rest-api`, `workforce-analytics`.
- **Git Remote & Sync**:
  - Remote `origin` linked to `https://github.com/eleshkapri/employee-attendance-analytics-api.git`.
  - Default branch `main` pushed and tracking `origin/main`.
- **Security Check**:
  - Verified no `.env` or sensitive credentials pushed; strictly `.gitignore` protected.

---

### Phase 12: GitHub Submission Order & Selection Alignment (Done)
- Aligned `README.md` to follow the exact order and criteria of `candidate_kit/PROBLEM_STATEMENT.docx`:
  1. Section 12 Submission Checklist with verified checkboxes.
  2. Section 11 Evaluation Criteria & Deliverables Mapping table (10% Part A, 30% CRUD, 30% Analytics, 15% MongoDB Speed, 15% Decisions).
  3. Section 4 Environment Contract & Quickstart (`uvicorn app.main:app --port 8000`, Atlas config, seed).
  4. Section 7 Complete 12-endpoint directory table in exact sequence.
  5. Section 6 Business Rules (R1–R10) enforcement architecture matrix.
  6. Section 9 Concurrency & race condition defenses (atomic updates, compound unique indexes, optimistic locking).
  7. Section 10 Startup indexes & explain proof table (`IXSCAN`, 0 `COLLSCAN`).
  8. Section 2 Deliverables and complete repository directory tree.
  9. Architectural decisions summary (`DECISIONS.md`).
- Committed and pushed to GitHub `origin/main`.


