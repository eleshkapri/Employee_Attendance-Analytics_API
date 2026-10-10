"""
Employee Attendance & Analytics API - Production Implementation
Contract Version: 2.0.0
Evaluation: HROne Software Engineer Hiring Assignment

Run: uvicorn app.main:app --port 8000
Env: MONGO_URI, MONGO_DB (real environment variables take priority over local .env)
"""
import math
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
import pymongo
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

# Real environment variables take priority
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client: MongoClient = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))


def create_indexes():
    """Create all required indexes idempotently at startup."""
    try:
        # employees collection indexes
        db.employees.create_index([("emp_code", 1)], unique=True)
        db.employees.create_index([("department", 1), ("joined_on", 1)])

        # attendance_logs collection indexes
        db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
        db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
        db.attendance_logs.create_index([("emp_code", 1), ("date", 1), ("status", 1)])
        db.attendance_logs.create_index([("status", 1), ("date", 1), ("late_minutes", 1)])
    except PyMongoError as e:
        print(f"Warning: Index creation encounter: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create indexes idempotently
    create_indexes()
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan
)


# --------------------------------------------------------------------------- #
# Helpers & Rules (R1 - R5)
# --------------------------------------------------------------------------- #
def truncate_ms(epoch_ms: int) -> int:
    """Truncate milliseconds to whole seconds per Rule R1."""
    return (epoch_ms // 1000) * 1000


def ms_to_datetime(epoch_ms: int) -> datetime:
    """Convert epoch milliseconds to UTC BSON datetime for MongoDB."""
    trunc_ms = truncate_ms(epoch_ms)
    return datetime.fromtimestamp(trunc_ms / 1000.0, tz=timezone.utc)


def datetime_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert MongoDB BSON datetime to epoch milliseconds."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def get_ist_datetime(epoch_ms: int) -> datetime:
    """Get IST datetime for an epoch millisecond instant."""
    trunc_ms = truncate_ms(epoch_ms)
    return datetime.fromtimestamp(trunc_ms / 1000.0, tz=IST)


def resolve_attendance_date(punch_in_ms: int, shift_start: str, shift_end: str) -> str:
    """
    R1: Attendance date is IST calendar date of punch_in.
    Overnight shifts (shift_end <= shift_start): punch-in earlier than shift_end
    belongs to the shift that started the previous calendar day.
    """
    dt_ist = get_ist_datetime(punch_in_ms)
    is_overnight = shift_end <= shift_start
    if is_overnight:
        end_h, end_m = map(int, shift_end.split(":"))
        if (dt_ist.hour, dt_ist.minute, dt_ist.second) < (end_h, end_m, 0):
            prev_day = dt_ist.date() - timedelta(days=1)
            return prev_day.isoformat()
    return dt_ist.date().isoformat()


def compute_late_minutes(punch_in_ms: int, shift_start: str, shift_end: str, att_date: str) -> int:
    """
    R2: Grace period 10 minutes.
    Late only if punch-in is strictly more than 10:00 after shift_start.
    When late, late_minutes = whole minutes (floor) elapsed since shift_start,
    not since grace period ended.
    """
    shift_start_dt = datetime.fromisoformat(f"{att_date}T{shift_start}:00").replace(tzinfo=IST)
    punch_in_ist = get_ist_datetime(punch_in_ms)
    diff_sec = (punch_in_ist - shift_start_dt).total_seconds()
    if diff_sec > 600:
        return int(math.floor(diff_sec / 60))
    return 0


def compute_work_hours(punch_in_ms: int, punch_out_ms: int) -> float:
    """
    R4: (punch_out - punch_in) in seconds / 3600, rounded to 2 decimals half-up.
    """
    in_sec = truncate_ms(punch_in_ms) // 1000
    out_sec = truncate_ms(punch_out_ms) // 1000
    diff_sec = out_sec - in_sec
    hours = Decimal(diff_sec) / Decimal(3600)
    rounded = hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(rounded)


def compute_overtime(punch_out_ms: int, shift_start: str, shift_end: str, att_date: str) -> int:
    """
    R3: Whole minutes (floor) from shift_end to punch_out, counted only if >= 30; otherwise 0.
    For overnight shifts, shift_end falls on the next calendar day.
    """
    is_overnight = shift_end <= shift_start
    if is_overnight:
        end_date = (date.fromisoformat(att_date) + timedelta(days=1)).isoformat()
    else:
        end_date = att_date
    shift_end_dt = datetime.fromisoformat(f"{end_date}T{shift_end}:00").replace(tzinfo=IST)
    punch_out_ist = get_ist_datetime(punch_out_ms)
    diff_sec = (punch_out_ist - shift_end_dt).total_seconds()
    elapsed_minutes = int(math.floor(diff_sec / 60))
    if elapsed_minutes >= 30:
        return elapsed_minutes
    return 0


def compute_half_day(work_hours: Optional[float]) -> bool:
    """R5: True when rounded work_hours < 4.50."""
    if work_hours is None:
        return False
    return work_hours < 4.50


def format_employee_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format employee document for API responses, converting created_at to epoch ms."""
    if not doc:
        return doc
    doc.pop("_id", None)
    created_at = doc.get("created_at")
    if isinstance(created_at, datetime):
        doc["created_at"] = datetime_to_ms(created_at)
    return doc


def format_attendance_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format attendance document for API response matching AttendanceRecord schema."""
    if not doc:
        return doc
    doc.pop("_id", None)
    doc.pop("id", None)
    doc["punch_in"] = datetime_to_ms(doc.get("punch_in"))
    doc["punch_out"] = datetime_to_ms(doc.get("punch_out"))
    doc["work_hours"] = doc.get("work_hours")
    doc["late_minutes"] = doc.get("late_minutes", 0)
    doc["overtime_minutes"] = doc.get("overtime_minutes", 0)
    doc["half_day"] = doc.get("half_day", False)

    formatted_history = []
    for h in doc.get("history", []):
        h_copy = dict(h)
        if isinstance(h_copy.get("at"), datetime):
            h_copy["at"] = datetime_to_ms(h_copy["at"])
        raw_changes = h_copy.get("changes", {})
        formatted_changes = {}
        for k, v in raw_changes.items():
            if isinstance(v, dict):
                v_copy = dict(v)
                if k in ("punch_in", "punch_out"):
                    if isinstance(v_copy.get("from"), datetime):
                        v_copy["from"] = datetime_to_ms(v_copy["from"])
                    if isinstance(v_copy.get("to"), datetime):
                        v_copy["to"] = datetime_to_ms(v_copy["to"])
                formatted_changes[k] = v_copy
            else:
                formatted_changes[k] = v
        h_copy["changes"] = formatted_changes
        formatted_history.append(h_copy)
    doc["history"] = formatted_history
    return doc


# --------------------------------------------------------------------------- #
# Request & Response Models
# --------------------------------------------------------------------------- #
class EmployeeCreate(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$", description="Customer employee code EMP + 4-6 digits")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("shift_end")
    @classmethod
    def validate_shift_times(cls, v: str, info) -> str:
        if "shift_start" in info.data and v == info.data["shift_start"]:
            raise ValueError("shift_end must differ from shift_start")
        return v


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    status: str = Field(default="PRESENT", pattern=r"^(PRESENT|WFH|ON_DUTY)$")


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)


class RegularizeRequest(BaseModel):
    status: Optional[str] = Field(default=None, pattern=r"^(PRESENT|WFH|ON_DUTY|ABSENT|LEAVE)$")
    punch_in: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(default=None, ge=100000000000, le=4102444800000)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints: System & Employees
# --------------------------------------------------------------------------- #
@app.get("/health", summary="Readiness probe")
def health():
    """Returns 200 only if MongoDB answers a ping, otherwise 503."""
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Database unavailable: {str(e)}")


@app.post("/employees", status_code=status.HTTP_201_CREATED, summary="Create an employee")
def create_employee(body: EmployeeCreate):
    """
    Creates an employee. Enforces uniqueness of emp_code via database unique index.
    created_at is set by the server as BSON datetime (UTC).
    """
    now_ms = truncate_ms(int(datetime.now(timezone.utc).timestamp() * 1000))
    doc = body.model_dump()
    doc["created_at"] = ms_to_datetime(now_ms)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="emp_code already exists")

    return format_employee_doc(doc)


@app.get("/employees", summary="List employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List employees sorted by emp_code ascending. Total counts filtered set."""
    q: Dict[str, Any] = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size)
    items = [format_employee_doc(doc) for doc in cursor]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Endpoints: Attendance
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-in", status_code=status.HTTP_201_CREATED, summary="Record a punch-in")
def punch_in(body: PunchInRequest):
    """
    Creates the day's record per R1 and R2.
    Race-safe via compound unique index on (emp_code, date).
    """
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if body.punched_at is not None:
        punch_ms = truncate_ms(body.punched_at)
    else:
        punch_ms = truncate_ms(int(datetime.now(timezone.utc).timestamp() * 1000))

    shift_start = emp.get("shift_start", "09:30")
    shift_end = emp.get("shift_end", "18:30")

    att_date = resolve_attendance_date(punch_ms, shift_start, shift_end)
    late_min = compute_late_minutes(punch_ms, shift_start, shift_end, att_date)

    doc = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status,
        "punch_in": ms_to_datetime(punch_ms),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_min,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already punched in for this date")

    return format_attendance_doc(doc)


@app.get("/attendance", summary="List attendance records")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern=r"^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    List attendance records sorted by date descending, then emp_code ascending.
    Streams via database cursor to maintain high performance at 100k docs.
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_from must not be after date_to")

    q: Dict[str, Any] = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status_filter:
        q["status"] = status_filter

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)
    items = [format_attendance_doc(doc) for doc in cursor]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Remaining Endpoints Placeholders (Part B - To be implemented in Phase 3 & 4)
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    raise HTTPException(status_code=501, detail="To be completed in Phase 3")


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    raise HTTPException(status_code=501, detail="To be completed in Phase 3")


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    raise HTTPException(status_code=501, detail="To be completed in Phase 4")


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None):
    raise HTTPException(status_code=501, detail="To be completed in Phase 4")


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, max=50),
    department: Optional[str] = None,
):
    raise HTTPException(status_code=501, detail="To be completed in Phase 4")


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    raise HTTPException(status_code=501, detail="To be completed in Phase 4")


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    raise HTTPException(status_code=501, detail="To be completed in Phase 5")
