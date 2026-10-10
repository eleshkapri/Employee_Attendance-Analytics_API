# Product Requirements Document (PRD)

## Project: Employee Attendance & Analytics API
**Client / Context**: HROne Software Engineer Hiring Assignment (Contract v2.0)  
**Target Architecture**: FastAPI (Python 3.11+) + MongoDB 6.0+ (Atlas Cloud Cluster)

---

## 1. Product Summary & Vision
The system provides a robust backend API for enterprise attendance tracking and workforce analytics. It handles high-volume employee punch actions, administrative regularizations with immutable audit histories, and complex analytical aggregations (department performance, attendance trends, late leaderboards).

The service is engineered to run against large-scale datasets (100,000+ attendance records) with sub-second response times, zero race conditions under concurrent requests, and strict adherence to contract v2.0.

---

## 2. Personas & Stakeholders

1. **Enterprise Employees**:
   - Punch in and out daily via mobile or web clients.
   - May work standard shifts (e.g. 09:30–18:30) or overnight shifts (e.g. 22:00–06:00).
   - Can work on site (`PRESENT`), from home (`WFH`), or on field assignment (`ON_DUTY`).
2. **HR Operations & Managers**:
   - Create new employee profiles.
   - Correct (regularize) past records with mandatory audit justification.
   - Review department health metrics, attendance rates, and absenteeism.
3. **Executive Management**:
   - Monitor 7-day moving average attendance trends per department.
   - Inspect late-arrival leaderboards with competition ranking.
4. **Automated Evaluator / Grader**:
   - Executes hundreds of automated contract and stress tests.
   - Verifies HTTP status codes, validation responses (422), concurrency locks (409), and database explain plans (`IXSCAN` without `COLLSCAN`).

---

## 3. Functional Requirements (FR)

### Module 1: System Health & Probes
- **FR-1.1 Health Probe (`GET /health`)**:
  - Must ping the underlying MongoDB instance (`admin.command('ping')`).
  - Returns HTTP 200 `{"status": "ok"}` when healthy.
  - Returns HTTP 503 `{"detail": ...}` when database ping fails or times out.

### Module 2: Employee Management
- **FR-2.1 Create Employee (`POST /employees`)**:
  - Accepts `emp_code`, `name`, `email`, `department`, `shift_start`, `shift_end`, `joined_on`.
  - Enforces uniqueness of `emp_code` at database level; concurrent duplicates return 409.
  - Validates `shift_start != shift_end` (rejects zero-length shifts with 422).
  - Server assigns `created_at` in epoch milliseconds (stored as BSON UTC datetime).
  - Returns HTTP 201 with created employee document (excluding `_id`).
- **FR-2.2 List Employees (`GET /employees`)**:
  - Supports optional exact `department` filter.
  - Supports 1-indexed pagination (`page`, `page_size`, max 100).
  - Results sorted by `emp_code` ascending.
  - `total` count must accurately reflect the filtered subset, not the entire collection.

### Module 3: Daily Attendance Tracking
- **FR-3.1 Punch-in (`POST /attendance/punch-in`)**:
  - Accepts `emp_code`, optional `punched_at` (epoch ms, defaults to now), and `status` (`PRESENT`, `WFH`, `ON_DUTY`).
  - Converts timestamp to IST calendar date per Rule R1 (supporting overnight shift assignment).
  - Rejects unknown employee with 404.
  - Rejects duplicate punch-in for the same date with 409 (enforced race-safely via unique compound index).
  - Computes `late_minutes` per Rule R2.
  - Initializes `punch_out=null`, `work_hours=null`, `overtime_minutes=0`, `half_day=false`, `history=[]`.
- **FR-3.2 Punch-out (`POST /attendance/punch-out`)**:
  - Accepts `emp_code`, optional `punched_at` (epoch ms, defaults to now).
  - Identifies latest open attendance record where `punch_in <= punched_at`.
  - Rejects unknown employee or no open record with 404.
  - Rejects record if already punched out with 409 (must be race-safe: concurrent attempts produce exactly one 200, others 409).
  - Rejects invalid intervals: `punched_at <= punch_in` or `punched_at > punch_in + 24 hours` with 422.
  - Computes `work_hours` (R4), `overtime_minutes` (R3), and `half_day` (R5).
- **FR-3.3 List Attendance Records (`GET /attendance`)**:
  - Filterable by `emp_code`, `date_from`, `date_to`, and `status`.
  - Validates `date_from <= date_to`; returns 422 if inverted.
  - Sorted by `date` descending, then `emp_code` ascending.
  - Paginated at database level (never loading full collections into Python memory).
- **FR-3.4 Regularization (`PATCH /attendance/{emp_code}/{date}`)**:
  - Targets record by natural key `(emp_code, date)`.
  - Allows corrections to `status`, `punch_in`, `punch_out`, with required `reason` and `regularized_by`.
  - Rules: setting `ABSENT` or `LEAVE` clears both punch times; supplying punches with them is 422.
  - Setting presence status requires `punch_in`.
  - `punch_in` must remain on the record's IST attendance date.
  - `punch_out` must be within 24 hours after `punch_in`.
  - Recomputes all derived fields (`late_minutes`, `work_hours`, `overtime_minutes`, `half_day`).
  - Appends an audit record to `history` capturing `{at, by, reason, changes}` where `changes` lists only fields that actually changed (`from` / `to`).
  - No changes requested returns 422. Missing employee or record returns 404.

### Module 4: Workforce Analytics (Pure Database Aggregations)
- **FR-4.1 Employee Monthly Summary (`GET /analytics/employees/{emp_code}/monthly`)**:
  - Aggregates single employee records for month `YYYY-MM`.
  - Calculates `working_days` (Mon–Fri from `joined_on` to month end).
  - Calculates `present_days` (Mon–Fri only, half-day = 0.5), `leave_days`, `late_count`, `total_late_minutes`, `total_overtime_minutes`.
  - Computes `attendance_pct` (rounded 2 decimals half-up).
- **FR-4.2 Department Monthly Summary (`GET /analytics/departments/summary`)**:
  - Cross-collection aggregation across `employees` and `attendance_logs`.
  - Determines active `headcount` (all department employees joined on or before month end, **including those with zero logs**).
  - Calculates aggregated `present_days`, `avg_work_hours` (mean over present records with work hours), `late_count`, `total_late_minutes`, `leave_count`, `on_duty_count`.
  - Omits departments with 0 active headcount. Sorted by `department` ascending.
- **FR-4.3 Late-Comers Leaderboard (`GET /analytics/leaderboard/late`)**:
  - Filters employees with `total_late_minutes > 0` for the month.
  - Applies standard competition ranking (1, 2, 2, 4) using MongoDB window functions.
  - Applies `limit` **after** ranking (all employees with `rank <= limit` are included, even if exceeding limit count).
  - Ordered by `total_late_minutes` descending, then `emp_code` ascending.
- **FR-4.4 Department Daily Trend (`GET /analytics/departments/{department}/trend`)**:
  - Date range `from` to `to` (max 92 days; 422 if inverted or out of range).
  - Returns exactly one row per calendar day via database gap-filling (`$densify`).
  - Emits `is_working_day`, `headcount`, `present_count`, `late_count`, `attendance_rate` (4 decimals half-up), and 7-day moving average (`moving_avg_7d`, 4 decimals half-up).

### Module 5: Admin & Performance Verification
- **FR-5.1 Explain Query Plan (`GET /admin/explain/{endpoint}`)**:
  - Executes MongoDB `explain("executionStats")` on the exact pipeline/query of target endpoint (`attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`).
  - Returns `{endpoint, collection, explain}`.

---

## 4. Non-Functional Requirements (NFR)

- **NFR-1 (Scale & Latency)**: Endpoints must sustain sub-200ms latency on 100,000+ attendance documents.
- **NFR-2 (Memory Efficiency)**: Database streaming and pagination via `.skip()` and `.limit()`; zero full collection loading into RAM.
- **NFR-3 (Execution Plan)**: All explained queries must use Index Scans (`IXSCAN`) and strictly eliminate Collection Scans (`COLLSCAN`).
- **NFR-4 (Isolation & Network)**: Zero outbound network traffic other than MongoDB Atlas cluster.
- **NFR-5 (Resilience & Concurrency)**: Zero race conditions or unhandled 500 exceptions under high-concurrency workloads.
