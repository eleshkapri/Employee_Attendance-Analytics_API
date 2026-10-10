# Business Rules Bible (R1 – R10)

## Project: Employee Attendance & Analytics API
**Contract Version**: 2.0.0  
**Context**: HROne Engineering Hiring Assignment

> **Mandate**: These rules are not explicitly validated by JSON schema validators. Most evaluation failures on hidden test datasets stem from subtle rule misinterpretations. This document details every calculation, mathematical formula, and boundary condition.

---

## R1: Time Zone & Attendance Date

- **Time Zone**: Indian Standard Time (IST, UTC+05:30).
- **Attendance Date Assignment**:
  - The record's `date` (`YYYY-MM-DD`) is defined as the **IST calendar date** of its `punch_in` instant.
- **Overnight Shifts (`shift_end <= shift_start`)**:
  - Example: `shift_start = "22:00"`, `shift_end = "06:00"`.
  - If an employee punches in earlier than `shift_end` in IST (e.g. at `04:15` IST on July 7), the punch belongs to the shift that started on the **previous calendar day** (July 6). The record's `date` is `2026-07-06`.
  - The shift end of an overnight shift falls on the **next calendar day** relative to the attendance date.
- **Timestamp Truncation**:
  - All timestamps (`punched_at`, `punch_in`, `punch_out`) are truncated to **whole seconds** before any calculation, storage, or comparison.
  - Example: `09:40:00.950` is truncated to `09:40:00.000` (epoch ms integer `floor(ms / 1000) * 1000`).
  - Truncated value is what is stored and returned in all responses.

---

## R2: Late Arrival Calculation

- **Grace Period**: Exactly 10 minutes from `shift_start`.
- **Condition for Lateness**:
  - A punch-in is late **only if** it is strictly greater than 10 minutes past `shift_start`:
    $$\text{is\_late} = (\text{punch\_in\_seconds} - \text{shift\_start\_seconds}) > 600$$
- **Formula for `late_minutes`**:
  - When late, minutes are calculated from `shift_start` (NOT from the end of the grace period):
    $$\text{late\_minutes} = \left\lfloor \frac{\text{punch\_in\_seconds} - \text{shift\_start\_seconds}}{60} \right\rfloor$$
  - When not late: `late_minutes = 0`.
- **Boundary Test Cases (Shift Start = 09:30:00)**:

| Punch-In Time | Elapsed from Shift Start | Is Late? | `late_minutes` |
|---|---|---|---|
| `09:28:15` | -1m 45s | No | `0` |
| `09:30:00` | 0m 00s | No | `0` |
| `09:39:59` | 9m 59s | No | `0` |
| `09:40:00` | 10m 00s (exact grace) | **No** | `0` |
| `09:40:01` | 10m 01s (strictly $>10$m) | **Yes** | `10` |
| `09:40:59` | 10m 59s | **Yes** | `10` |
| `09:41:00` | 11m 00s | **Yes** | `11` |
| `10:15:59` | 45m 59s | **Yes** | `45` |

---

## R3: Overtime Calculation

- **Eligibility Threshold**: Overtime must be at least 30 minutes past `shift_end`.
- **Formula for `overtime_minutes`**:
  $$\text{elapsed\_minutes} = \left\lfloor \frac{\text{punch\_out\_seconds} - \text{shift\_end\_seconds}}{60} \right\rfloor$$
  $$\text{overtime\_minutes} = \begin{cases} \text{elapsed\_minutes} & \text{if } \text{elapsed\_minutes} \ge 30 \\ 0 & \text{otherwise} \end{cases}$$
- **Boundary Test Cases (Shift End = 18:30:00)**:

| Punch-Out Time | Elapsed from Shift End | Eligible? ($\ge 30$) | `overtime_minutes` |
|---|---|---|---|
| `18:25:00` | -5m 00s | No | `0` |
| `18:30:00` | 0m 00s | No | `0` |
| `18:59:59` | 29m 59s | No | `0` |
| `19:00:00` | 30m 00s | **Yes** | `30` |
| `19:10:45` | 40m 45s | **Yes** | `40` |

---

## R4: Work Hours Calculation

- **Formula**:
  $$\text{work\_hours} = \frac{\text{punch\_out\_seconds} - \text{punch\_in\_seconds}}{3600}$$
- **Rounding Requirement**:
  - Must be rounded to **2 decimal places HALF-UP** (`decimal.Decimal` with `ROUND_HALF_UP`).
  - Standard Python `round()` uses Banker's rounding (round-half-to-even) and is **strictly prohibited**.
  - Implementation:
    ```python
    from decimal import Decimal, ROUND_HALF_UP
    hours = Decimal(str(seconds_diff)) / Decimal(3600)
    rounded_hours = float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    ```
- **State Before Punch-out**: `work_hours = null`.

---

## R5: Half-Day Classification

- **Threshold**:
  - Evaluated **after** punch-out using the **already rounded** `work_hours`:
    $$\text{half\_day} = (\text{rounded\_work\_hours} < 4.50)$$
- **Analytics Contribution**:
  - A half-day record contributes **0.5** to `present_days` (instead of 1.0).
- **Default State**: `half_day = false` while record is open or for absent/leave records.

---

## R6: Presence Classification

- **Statuses Counting as Present**:
  - `PRESENT` (Worked on-site)
  - `WFH` (Worked from home)
  - `ON_DUTY` (Official duty away from office)
- **Statuses NOT Counting as Present**:
  - `ABSENT` (Unexcused absence)
  - `LEAVE` (Approved leave)
- **Punch Time Constraints**:
  - Present statuses require a valid `punch_in`.
  - `ABSENT` and `LEAVE` must have both `punch_in = null` and `punch_out = null`.

---

## R7: Working Days & Calendar Calculations

- **Definition of Working Day**:
  - Monday through Friday exclusively (`weekday in [0, 1, 2, 3, 4]`).
  - No company or national holiday calendar is applied.
- **Mid-Month Joiners**:
  - Working days are counted starting from the employee's `joined_on` date through the end of the month.
  - If an employee joined on `2026-07-15`, days prior to July 15 are excluded from their `working_days`.
- **Weekend Attendance Impact**:
  - `present_days` counts records that fall on **Monday through Friday only**.
  - Weekend records (Saturday / Sunday) do **not** increase `present_days`.
  - However, weekend records **do contribute** to `total_late_minutes` and `total_overtime_minutes`.

---

## R8: Rounding & Reporting Precision

- **Standard Reported Numbers (Counts, Averages, Percentages)**:
  - Rounded to **2 decimal places HALF-UP**.
  - Example: `present_days = 19.50`, `attendance_pct = 92.86`.
- **Rates (`attendance_rate`, `moving_avg_7d`)**:
  - Rounded to **4 decimal places HALF-UP**.
  - Example: `attendance_rate = 0.8571`, `moving_avg_7d = 0.9143`.
  - Working day with 0 present employees and headcount $>0$: rate is `0.0000`.

---

## R9: Active Headcount Calculation

- **Eligibility**:
  - An employee is included in headcount for a given period if and only if:
    $$\text{joined\_on} \le \text{period\_end\_date}$$
- **Zero-Log Employees**:
  - Employees meeting the `joined_on` criteria **must be included in headcount even if they have 0 attendance records**.
  - Future joiners (`joined_on > period_end_date`) are strictly excluded.

---

## R10: Pagination Specifications

- **Page Index**: 1-indexed (`page >= 1`).
- **Page Size**: Default `20`, maximum `100` (`1 <= page_size <= 100`).
- **Total Count**:
  - `total` in response must reflect the **exact count of documents matching the active query filters**, never the total count of the entire collection.
