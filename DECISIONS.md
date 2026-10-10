# DECISIONS.md

1. **Indexes.**
On `employees`: `{emp_code: 1}` (unique) prevents duplicates and backs `$lookup` joins; `{department: 1, joined_on: 1}` and `{joined_on: 1}` support headcount filtering (`joined_on <= date`) without `COLLSCAN`.
On `attendance_logs`: `{emp_code: 1, date: 1}` (unique) enforces single daily punch-in and speeds point updates; `{date: -1, emp_code: 1}` backs default sorting and date range queries.
I rejected indexing `late_minutes` standalone: sparse, unselective values cause index bloat without date compound prefixes.

2. **Punch-in race.**
Both concurrent requests pass validation and attempt `insert_one`. MongoDB's unique index on `(emp_code, date)` serializes them at the storage engine layer: the winner inserts successfully (`201 Created`), while the loser triggers a `DuplicateKeyError`, caught and converted into `409 Conflict`. Zero duplicate rows are created.

3. **Ties.**
The pipeline computes standard competition ranks via `$setWindowFields` (`$rank: {}`), skipping ranks after ties (e.g., 1, 2, 2, 4). The `$match: {rank: {$lte: limit}}` returns all qualifying employees—including tied candidates at cutoff—ordered by `total_late_minutes` descending, then `emp_code` ascending.

4. **Headcount.**
The pipeline starts from `employees` (`joined_on <= period_end`) and performs a `$lookup` into `attendance_logs` with `$unwind: {preserveNullAndEmptyArrays: true}`. Employees with zero logs survive the join, ensuring their department headcount is accurately counted.

5. **One thing you would change for 100x data.**
Pre-compute daily attendance rollups into a read-optimized summary collection using MongoDB change streams, avoiding expensive real-time multi-stage `$lookup` and `$densify` aggregations across 10M+ raw logs.
