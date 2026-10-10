# Detailed Technical Design Document

## Project: Employee Attendance & Analytics API
**Contract Version**: 2.0.0  
**Target Codebase**: `app/main.py` (Single file constraint)

---

## 1. Pydantic Models & Validation Layer

### 1.1 Custom Types & Constraints
- `EpochMillis`: Integer representing milliseconds since Unix epoch ($100000000000 \le \text{val} \le 4102444800000$). Rejects floats, strings, seconds with 422.
- `TimeStr`: Pattern `^([01]\d|2[0-3]):[0-5]\d$` (24-hour HH:MM, IST).
- `DateStr`: ISO date format `YYYY-MM-DD`.
- `MonthStr`: Pattern `^\d{4}-(0[1-9]|1[0-2])$`.

### 1.2 Model Definitions

```python
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("shift_end")
    @classmethod
    def validate_shift_not_equal(cls, v, values):
        if "shift_start" in values.data and v == values.data["shift_start"]:
            raise ValueError("shift_end must differ from shift_start")
        return v

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    status: str = Field(default="PRESENT", pattern=r"^(PRESENT|WFH|ON_DUTY)$")

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)

class RegularizeIn(BaseModel):
    status: Optional[str] = Field(default=None, pattern=r"^(PRESENT|WFH|ON_DUTY|ABSENT|LEAVE)$")
    punch_in: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
```

---

## 2. Boundary Converters & Helper Functions

### 2.1 Instant Truncation & Epoch Conversion
```python
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def truncate_ms_to_seconds(epoch_ms: int) -> int:
    """Truncates milliseconds to whole seconds per Rule R1."""
    return (epoch_ms // 1000) * 1000

def ms_to_utc_dt(epoch_ms: int) -> datetime:
    """Converts epoch milliseconds to naive/UTC BSON datetime for MongoDB."""
    sec = epoch_ms / 1000.0
    return datetime.fromtimestamp(sec, tz=timezone.utc)

def dt_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    """Converts MongoDB BSON datetime to epoch milliseconds."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
```

### 2.2 Attendance Date Resolution (Overnight Shifts R1)
```python
def resolve_attendance_date(punch_in_ms: int, shift_start: str, shift_end: str) -> str:
    """
    Computes IST calendar date of punch_in.
    Handles overnight shift: if shift_end <= shift_start and punch_in IST time < shift_end,
    date belongs to the previous calendar day.
    """
    dt_ist = datetime.fromtimestamp(punch_in_ms / 1000.0, tz=IST)
    is_overnight = shift_end <= shift_start
    if is_overnight:
        end_h, end_m = map(int, shift_end.split(":"))
        punch_time = (dt_ist.hour, dt_ist.minute, dt_ist.second)
        if punch_time < (end_h, end_m, 0):
            # Belongs to previous day's shift
            prev_day = dt_ist - timedelta(days=1)
            return prev_day.strftime("%Y-%m-%d")
    return dt_ist.strftime("%Y-%m-%d")
```

---

## 3. Endpoint Specifications & Query Designs

### 3.1 `GET /attendance` (High Volume Pagination)
```python
# Query MongoDB directly with index-backed sort and skip/limit
cursor = db.attendance_logs.find(query, {"_id": 0}) \
    .sort([("date", -1), ("emp_code", 1)]) \
    .skip((page - 1) * page_size) \
    .limit(page_size)
```

### 3.2 `POST /attendance/punch-out` (Atomic Update)
```python
result = db.attendance_logs.find_one_and_update(
    {
        "emp_code": emp_code,
        "date": record_date,
        "punch_out": None
    },
    {
        "$set": {
            "punch_out": punch_out_dt,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": half_day
        }
    },
    return_document=ReturnDocument.AFTER
)
# If result is None: check if already punched out (409) or missing (404)
```

### 3.3 `PATCH /attendance/{emp_code}/{date}` (Audit Regularization)
```python
history_entry = {
    "at": ms_to_utc_dt(now_ms),
    "by": body.regularized_by,
    "reason": body.reason,
    "changes": changes_dict
}

db.attendance_logs.update_one(
    {"emp_code": emp_code, "date": date},
    {
        "$set": updated_fields,
        "$push": {"history": history_entry}
    }
)
```

---

## 4. Aggregation Pipeline Blueprints

### 4.1 Late Leaderboard Pipeline
```json
[
  { "$match": { "date": { "$regex": "^2026-07" }, "late_minutes": { "$gt": 0 } } },
  {
    "$group": {
      "_id": "$emp_code",
      "total_late_minutes": { "$sum": "$late_minutes" },
      "late_count": { "$sum": 1 }
    }
  },
  {
    "$lookup": {
      "from": "employees",
      "localField": "_id",
      "foreignField": "emp_code",
      "as": "emp"
    }
  },
  { "$unwind": "$emp" },
  {
    "$setWindowFields": {
      "sortBy": { "total_late_minutes": -1 },
      "output": {
        "rank": { "$rank": {} }
      }
    }
  },
  { "$match": { "rank": { "$lte": 10 } } },
  { "$sort": { "total_late_minutes": -1, "emp.emp_code": 1 } }
]
```

### 4.2 Department Trend Pipeline
1. Uses `$densify` on `date` field between `from` and `to` step 1 day.
2. Filters by target department.
3. Uses `$setWindowFields` with `partitionBy: null, sortBy: {"date": 1}`:
   ```json
   {
     "moving_avg_7d": {
       "$avg": "$attendance_rate",
       "window": { "documents": [-6, 0] }
     }
   }
   ```

---

## 5. HTTP Error Status Code Matrix

| Scenario | HTTP Status Code | Response Body |
|---|---|---|
| MongoDB cluster unreachable / ping failure | `503 Service Unavailable` | `{"detail": "Database unavailable"}` |
| Unknown employee (`emp_code` not in `employees`) | `404 Not Found` | `{"detail": "Employee not found"}` |
| Attendance record missing for target date | `404 Not Found` | `{"detail": "Attendance record not found"}` |
| Duplicate employee creation (`emp_code` collision) | `409 Conflict` | `{"detail": "emp_code already exists"}` |
| Duplicate punch-in for same attendance date | `409 Conflict` | `{"detail": "already punched in for this date"}` |
| Punch-out on already closed record | `409 Conflict` | `{"detail": "Record is already punched out"}` |
| Invalid timestamp (seconds, string, float, out of range) | `422 Unprocessable Entity` | FastAPI default validation error format |
| Punch-out $\le$ punch-in or $> 24$ hours after punch-in | `422 Unprocessable Entity` | `{"detail": "punch_out must be after punch_in within 24 hours"}` |
| `date_from > date_to` or `from > to` | `422 Unprocessable Entity` | `{"detail": "date_from must not be after date_to"}` |
| Regularization changes nothing | `422 Unprocessable Entity` | `{"detail": "No effective changes requested"}` |
