# Project Technical Memory & State Log

## Project: Employee Attendance & Analytics API
**Current Milestone**: Workspace Cleaned & Verified | Ready for Phase 3 Execution  
**Last Updated**: 2026-10-10 22:18:00 IST

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
- Re-tested entire endpoint suite; verified 100% passing tests.

---

## 4. Next Milestone: Phase 3 (Core CRUD & Regularization)

- Target Endpoints:
  1. `POST /attendance/punch-out`:
     - Atomic `find_one_and_update` on latest open record (`punch_in <= punched_at` and `punch_out == None`).
     - Validate 24-hour limit and `punched_at > punch_in` (422).
     - Compute `work_hours`, `overtime_minutes`, `half_day`.
     - Return updated record matching schema.
  2. `PATCH /attendance/{emp_code}/{date}` (Regularization):
     - Address by natural key `(emp_code, date)`.
     - Status transition validation (clearing punch times on `ABSENT`/`LEAVE`, presence requires `punch_in`).
     - Enforce `punch_in` stays on record's attendance date.
     - Recompute derived fields.
     - Append immutable audit log to `history` array with `{at, by, reason, changes}`.
