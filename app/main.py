"""
Employee Attendance & Analytics API

Run:
    uvicorn app.main:app --port 8000
"""
import asyncio
import json
import math
import os
import re
import urllib.request
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional

from bson import json_util
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
        db.employees.create_index([("joined_on", 1)])

        # attendance_logs collection indexes
        db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
        db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
        db.attendance_logs.create_index([("emp_code", 1), ("date", 1), ("status", 1)])
        db.attendance_logs.create_index([("status", 1), ("date", 1), ("late_minutes", 1)])
    except PyMongoError as e:
        print(f"Warning: Index creation encounter: {e}")


async def self_ping_worker():
    """Keep-alive worker: runs only on Render cloud to prevent free tier spin-down."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    await asyncio.sleep(60)
    while True:
        try:
            req = urllib.request.Request(f"{url}/health", headers={"User-Agent": "RenderKeepAlive/1.0"})
            with urllib.request.urlopen(req, timeout=15) as _:
                pass
        except Exception:
            pass
        await asyncio.sleep(600)  # Ping every 10 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create indexes idempotently
    create_indexes()
    # Start keep-alive worker in background (runs only when RENDER_EXTERNAL_URL is set on Render)
    asyncio.create_task(self_ping_worker())
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
@app.api_route("/health", methods=["GET", "HEAD"], summary="Readiness probe")
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


def build_attendance_query(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status_filter: Optional[str] = None
) -> Dict[str, Any]:
    """Build query filter for attendance records list and explain."""
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
    return q


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

    q = build_attendance_query(emp_code, date_from, date_to, status_filter)

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)
    items = [format_attendance_doc(doc) for doc in cursor]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Endpoints: Attendance Punch-Out & Regularization (Part B)
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-out", status_code=status.HTTP_200_OK, summary="Record a punch-out")
def punch_out(body: PunchOutRequest):
    """
    Closes the employee's most recent record whose punch_in is at or before punched_at
    (works across midnight for overnight shifts).
    Computes work_hours (R4), overtime_minutes (R3) and half_day (R5).
    Errors:
      404 unknown employee, or no punch-in found;
      409 that record is already punched out (two simultaneous punch-outs must yield exactly one 200);
      422 if punched_at <= punch_in or more than 24 hours after it.
    """
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if body.punched_at is not None:
        punch_out_ms = truncate_ms(body.punched_at)
    else:
        punch_out_ms = truncate_ms(int(datetime.now(timezone.utc).timestamp() * 1000))

    punch_out_dt = ms_to_datetime(punch_out_ms)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_dt, "$ne": None}
        },
        sort=[("punch_in", pymongo.DESCENDING)]
    )

    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="that record is already punched out")

    punch_in_ms = datetime_to_ms(record["punch_in"])
    if punch_in_ms is None or punch_out_ms <= punch_in_ms or (punch_out_ms - punch_in_ms) > 86400000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="punched_at must be strictly after punch_in and within 24 hours of it"
        )

    shift_start = emp.get("shift_start", "09:30")
    shift_end = emp.get("shift_end", "18:30")
    att_date = record["date"]

    work_hours = compute_work_hours(punch_in_ms, punch_out_ms)
    overtime_min = compute_overtime(punch_out_ms, shift_start, shift_end, att_date)
    half_day = compute_half_day(work_hours)

    updated_doc = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": punch_out_dt,
                "work_hours": work_hours,
                "overtime_minutes": overtime_min,
                "half_day": half_day
            }
        },
        return_document=pymongo.ReturnDocument.AFTER
    )

    if not updated_doc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="that record is already punched out")

    return format_attendance_doc(updated_doc)


@app.patch("/attendance/{emp_code}/{date}", status_code=status.HTTP_200_OK, summary="Regularize attendance record")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    """
    Regularize (manually correct) one day's record.
    The record is addressed by its natural key: emp_code and date (YYYY-MM-DD).
    Omitted fields are unchanged.
    Recomputes late_minutes, work_hours, overtime_minutes and half_day from final times (R2-R5).
    Appends one entry to history.
    """
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid date format, expected YYYY-MM-DD")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attendance record not found for this date")

    cur_status = record["status"]
    cur_punch_in = datetime_to_ms(record.get("punch_in"))
    cur_punch_out = datetime_to_ms(record.get("punch_out"))
    cur_work_hours = record.get("work_hours")
    cur_late_minutes = record.get("late_minutes", 0)
    cur_overtime_minutes = record.get("overtime_minutes", 0)
    cur_half_day = record.get("half_day", False)

    if body.status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Supplying punch_in or punch_out when setting status ABSENT or LEAVE is not allowed"
            )
        new_status = body.status
        new_punch_in = None
        new_punch_out = None
    elif body.status is not None:
        new_status = body.status
        new_punch_in = truncate_ms(body.punch_in) if body.punch_in is not None else cur_punch_in
        new_punch_out = truncate_ms(body.punch_out) if body.punch_out is not None else cur_punch_out
    else:
        new_status = cur_status
        new_punch_in = truncate_ms(body.punch_in) if body.punch_in is not None else cur_punch_in
        new_punch_out = truncate_ms(body.punch_out) if body.punch_out is not None else cur_punch_out

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Punch times cannot be set for ABSENT or LEAVE status"
            )
        new_punch_in = None
        new_punch_out = None
    else:
        if new_punch_in is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A presence status requires a punch_in"
            )

    shift_start = emp.get("shift_start", "09:30")
    shift_end = emp.get("shift_end", "18:30")

    if new_punch_in is not None:
        resolved_date = resolve_attendance_date(new_punch_in, shift_start, shift_end)
        if resolved_date != date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"punch_in must stay on the record's attendance date ({date}), got {resolved_date}"
            )

    if new_punch_out is not None:
        if new_punch_in is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="punch_out requires punch_in")
        if new_punch_out <= new_punch_in:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="punch_out must be strictly after punch_in")
        if (new_punch_out - new_punch_in) > 86400000:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="punch_out must be within 24 hours of punch_in")

    if new_status in ("ABSENT", "LEAVE"):
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        new_late_minutes = compute_late_minutes(new_punch_in, shift_start, shift_end, date)
        if new_punch_out is not None:
            new_work_hours = compute_work_hours(new_punch_in, new_punch_out)
            new_overtime_minutes = compute_overtime(new_punch_out, shift_start, shift_end, date)
            new_half_day = compute_half_day(new_work_hours)
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False

    changes: Dict[str, Any] = {}
    if cur_status != new_status:
        changes["status"] = {"from": cur_status, "to": new_status}
    if cur_punch_in != new_punch_in:
        changes["punch_in"] = {"from": cur_punch_in, "to": new_punch_in}
    if cur_punch_out != new_punch_out:
        changes["punch_out"] = {"from": cur_punch_out, "to": new_punch_out}
    if cur_work_hours != new_work_hours:
        changes["work_hours"] = {"from": cur_work_hours, "to": new_work_hours}
    if cur_late_minutes != new_late_minutes:
        changes["late_minutes"] = {"from": cur_late_minutes, "to": new_late_minutes}
    if cur_overtime_minutes != new_overtime_minutes:
        changes["overtime_minutes"] = {"from": cur_overtime_minutes, "to": new_overtime_minutes}
    if cur_half_day != new_half_day:
        changes["half_day"] = {"from": cur_half_day, "to": new_half_day}

    if not changes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Request changes nothing")

    mongo_changes: Dict[str, Any] = {}
    for k, v in changes.items():
        if k in ("punch_in", "punch_out"):
            mongo_changes[k] = {
                "from": ms_to_datetime(v["from"]) if v["from"] is not None else None,
                "to": ms_to_datetime(v["to"]) if v["to"] is not None else None,
            }
        else:
            mongo_changes[k] = v

    now_ms = truncate_ms(int(datetime.now(timezone.utc).timestamp() * 1000))
    history_entry = {
        "at": ms_to_datetime(now_ms),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": mongo_changes
    }

    cur_history_len = len(record.get("history", []))
    updated_doc = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            f"history.{cur_history_len}": {"$exists": False}
        },
        {
            "$set": {
                "status": new_status,
                "punch_in": ms_to_datetime(new_punch_in) if new_punch_in is not None else None,
                "punch_out": ms_to_datetime(new_punch_out) if new_punch_out is not None else None,
                "work_hours": new_work_hours,
                "late_minutes": new_late_minutes,
                "overtime_minutes": new_overtime_minutes,
                "half_day": new_half_day,
            },
            "$push": {
                "history": history_entry
            }
        },
        return_document=pymongo.ReturnDocument.AFTER
    )

    if not updated_doc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Concurrent regularization conflict")

    return format_attendance_doc(updated_doc)


def round_half_up(val: Optional[float], decimals: int = 2) -> Optional[float]:
    """Round a number half-up to specified decimals per Rule R8."""
    if val is None:
        return None
    d = Decimal(str(val)).quantize(Decimal("1e-" + str(decimals)), rounding=ROUND_HALF_UP)
    return float(d)


# --------------------------------------------------------------------------- #
# Endpoints: Workforce Analytics Aggregations (Part B: Rules R6 - R9)
# --------------------------------------------------------------------------- #
def count_working_days(joined_on_str: str, month_str: str) -> int:
    """
    R7: Count Monday-Friday days in month YYYY-MM.
    For employees who joined mid-month, count from joined_on to end of month.
    """
    y, m = map(int, month_str.split("-"))
    if m == 12:
        next_month = date(y + 1, 1, 1)
    else:
        next_month = date(y, m + 1, 1)
    last_day = next_month - timedelta(days=1)
    first_day = date(y, m, 1)
    joined_date = date.fromisoformat(joined_on_str)

    start_date = max(first_day, joined_date)
    if start_date > last_day:
        return 0

    count = 0
    cur = start_date
    while cur <= last_day:
        if cur.weekday() < 5:  # Monday to Friday
            count += 1
        cur += timedelta(days=1)
    return count


def build_employee_monthly_pipeline(emp_code: str, month: str) -> List[Dict[str, Any]]:
    """Build aggregation pipeline for single-employee monthly summary."""
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$regex": f"^{month}"}
            }
        },
        {
            "$addFields": {
                "dt": {"$dateFromString": {"dateString": "$date"}}
            }
        },
        {
            "$addFields": {
                "day_of_week": {"$dayOfWeek": "$dt"}
            }
        },
        {
            "$addFields": {
                "is_weekday": {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]},
                "is_present_status": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$is_weekday", "$is_present_status"]},
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0.0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }
        }
    ]


@app.get("/analytics/employees/{emp_code}/monthly", summary="Monthly attendance summary for one employee")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    """
    Monthly attendance summary for one employee via MongoDB aggregation.
    working_days per R7. present_days per R5-R7 (half day = 0.5, Mon-Fri only).
    leave_days = LEAVE records. late_count = late_minutes > 0 records.
    attendance_pct = present_days / working_days * 100 (R8 rounding), or null when working_days is 0.
    404 for unknown employee.
    """
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    working_days = count_working_days(emp["joined_on"], month)
    pipeline = build_employee_monthly_pipeline(emp_code, month)

    res = list(db.attendance_logs.aggregate(pipeline))
    if res:
        agg = res[0]
        raw_present_days = agg.get("present_days", 0.0)
        leave_days = int(agg.get("leave_days", 0))
        late_count = int(agg.get("late_count", 0))
        total_late = int(agg.get("total_late_minutes", 0))
        total_ot = int(agg.get("total_overtime_minutes", 0))
    else:
        raw_present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late = 0
        total_ot = 0

    present_days = round_half_up(raw_present_days, 2)
    if working_days > 0:
        attendance_pct = round_half_up((present_days / working_days) * 100, 2)
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late,
        "total_overtime_minutes": total_ot,
        "attendance_pct": attendance_pct
    }


def build_department_summary_pipeline(month: str, department: Optional[str] = None) -> List[Dict[str, Any]]:
    """Build aggregation pipeline for department summary across both collections."""
    y, m = map(int, month.split("-"))
    if m == 12:
        next_month = date(y + 1, 1, 1)
    else:
        next_month = date(y, m + 1, 1)
    last_day_str = (next_month - timedelta(days=1)).isoformat()

    match_stage: Dict[str, Any] = {"joined_on": {"$lte": last_day_str}}
    if department:
        match_stage["department"] = department

    return [
        {"$match": match_stage},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$regexMatch": {"input": "$date", "regex": f"^{month}"}}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$addFields": {
                "log_dt": {
                    "$cond": [
                        {"$ne": ["$logs", None]},
                        {"$dateFromString": {"dateString": "$logs.date"}},
                        None
                    ]
                }
            }
        },
        {
            "$addFields": {
                "day_of_week": {
                    "$cond": [
                        {"$ne": ["$log_dt", None]},
                        {"$dayOfWeek": "$log_dt"},
                        None
                    ]
                }
            }
        },
        {
            "$addFields": {
                "is_weekday": {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]},
                "is_present_status": {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}
            }
        },
        {
            "$group": {
                "_id": "$department",
                "emp_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$is_weekday", "$is_present_status"]},
                            {"$cond": ["$logs.half_day", 0.5, 1.0]},
                            0.0
                        ]
                    }
                },
                "avg_work_hours": {
                    "$avg": {
                        "$cond": [
                            {"$and": ["$is_present_status", {"$ne": ["$logs.work_hours", None]}]},
                            "$logs.work_hours",
                            "$$REMOVE"
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$emp_codes"},
                "present_days": "$present_days",
                "avg_work_hours": "$avg_work_hours",
                "late_count": "$late_count",
                "total_late_minutes": "$total_late_minutes",
                "leave_count": "$leave_count",
                "on_duty_count": "$on_duty_count"
            }
        },
        {"$match": {"headcount": {"$gt": 0}}},
        {"$sort": {"department": 1}}
    ]


@app.get("/analytics/departments/summary", summary="Department summary for a month")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None
):
    """
    Department summary for a month via MongoDB aggregation across both collections.
    Headcount per R9 - people with zero logs still count, people who join later do not.
    avg_work_hours is mean over records that are present status and have work_hours value.
    Omit departments with headcount 0.
    Sorted by department ascending.
    """
    pipeline = build_department_summary_pipeline(month, department)
    results = list(db.employees.aggregate(pipeline))
    items = []
    for r in results:
        items.append({
            "department": r["department"],
            "headcount": int(r["headcount"]),
            "present_days": round_half_up(r["present_days"], 2),
            "avg_work_hours": round_half_up(r["avg_work_hours"], 2),
            "late_count": int(r["late_count"]),
            "total_late_minutes": int(r["total_late_minutes"]),
            "leave_count": int(r["leave_count"]),
            "on_duty_count": int(r["on_duty_count"]),
        })

    return {"month": month, "items": items}


def build_late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None) -> List[Dict[str, Any]]:
    """Build aggregation pipeline for late leaderboard with standard competition ranking."""
    pipeline = [
        {
            "$match": {
                "date": {"$regex": f"^{month}"},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
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
        {"$unwind": "$emp"}
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {
            "$match": {"rank": {"$lte": limit}}
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp.emp_code": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": "$rank",
                "emp_code": "$emp.emp_code",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": "$total_late_minutes",
                "late_count": "$late_count"
            }
        }
    ])
    return pipeline


@app.get("/analytics/leaderboard/late", summary="Late-comers leaderboard for a month")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    """
    Late-comers ranking with standard competition ranking (1, 2, 2, 4).
    limit applied after ranking. Tied rows ordered by emp_code ascending.
    """
    pipeline = build_late_leaderboard_pipeline(month, limit, department)
    results = list(db.attendance_logs.aggregate(pipeline))
    items = []
    for r in results:
        items.append({
            "rank": int(r["rank"]),
            "emp_code": r["emp_code"],
            "name": r["name"],
            "department": r["department"],
            "total_late_minutes": int(r["total_late_minutes"]),
            "late_count": int(r["late_count"]),
        })

    return {"month": month, "items": items}


def build_department_trend_pipeline(department: str, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """Build aggregation pipeline for daily attendance trend with calendar densification."""
    d_to = date.fromisoformat(to_date)
    from_dt = datetime.fromisoformat(f"{from_date}T00:00:00Z")
    to_plus_one = (d_to + timedelta(days=1)).isoformat()
    to_bound = datetime.fromisoformat(f"{to_plus_one}T00:00:00Z")

    return [
        {
            "$match": {
                "date": {"$gte": from_date, "$lte": to_date}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {"$unwind": "$emp"},
        {
            "$match": {
                "emp.department": department
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0.0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateFromString": {"dateString": "$_id"}},
                "present_count": 1,
                "late_count": 1
            }
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [from_dt, to_bound]
                }
            }
        },
        {
            "$project": {
                "date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
                "present_count": {"$ifNull": ["$present_count", 0.0]},
                "late_count": {"$ifNull": ["$late_count", 0]}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"d_str": "$date_str"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$d_str"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "active_emps"
            }
        },
        {
            "$addFields": {
                "date": "$date_str",
                "headcount": {"$size": "$active_emps"},
                "day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date_str"}}}
            }
        },
        {
            "$addFields": {
                "is_working_day": {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]}
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        },
        {"$sort": {"date": 1}},
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        }
    ]


@app.get("/analytics/departments/{department}/trend", summary="Daily attendance trend for a department")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    """
    Daily attendance trend for a department.
    Returns exactly one row per calendar day from from to to inclusive via $densify.
    is_working_day = Mon-Fri.
    headcount = department employees with joined_on <= that day (R9).
    attendance_rate = present_count / headcount (4 decimals half-up), only on working days with headcount > 0.
    moving_avg_7d = mean of non-null attendance_rate among current row and up to 6 preceding rows in requested range.
    404 unknown department; 422 if to < from or range exceeds 92 days.
    """
    if to_date < from_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="to date must not be before from date")

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)
    day_count = (d_to - d_from).days + 1
    if day_count > 92:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date range must not exceed 92 days")

    # Check if department exists
    dept_emp = db.employees.find_one({"department": department})
    if not dept_emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")

    pipeline = build_department_trend_pipeline(department, from_date, to_date)
    results = list(db.attendance_logs.aggregate(pipeline))
    items = []
    for r in results:
        items.append({
            "date": r["date"],
            "is_working_day": bool(r["is_working_day"]),
            "headcount": int(r["headcount"]),
            "present_count": round_half_up(r["present_count"], 2),
            "late_count": int(r["late_count"]),
            "attendance_rate": round_half_up(r["attendance_rate"], 4),
            "moving_avg_7d": round_half_up(r["moving_avg_7d"], 4),
        })

    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Endpoints: Admin & Diagnostic Execution Plans (Part C: Index Verification)
# --------------------------------------------------------------------------- #
class ExplainEndpointEnum(str, Enum):
    attendance_list = "attendance_list"
    employee_monthly = "employee_monthly"
    department_summary = "department_summary"
    late_leaderboard = "late_leaderboard"
    department_trend = "department_trend"


@app.get("/admin/explain/{endpoint}", summary="MongoDB explain for an endpoint's main query")
def explain_endpoint(
    endpoint: ExplainEndpointEnum,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: Optional[int] = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern=r"^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$"),
    from_date: Optional[str] = Query(default=None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: Optional[str] = Query(default=None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: Optional[int] = Query(default=1, ge=1),
    page_size: Optional[int] = Query(default=20, ge=1, le=100),
):
    """
    Runs explain with executionStats verbosity for the main query or aggregation of the named endpoint,
    built exactly as that endpoint builds it (same filters, sort, pagination), and returns raw explain output.
    Accepts the same query parameters as the endpoint it explains. Missing required parameters -> 422.
    """
    ep = endpoint.value

    if ep == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_from must not be after date_to")
        q = build_attendance_query(emp_code, date_from, date_to, status_filter)
        p = page if page is not None else 1
        ps = page_size if page_size is not None else 20
        skip = (p - 1) * ps
        cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(ps)
        raw_explain = cursor.explain()
        explain_json = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": explain_json
        }

    elif ep == "employee_monthly":
        if not emp_code:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="emp_code is required for employee_monthly")
        if not month:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="month is required for employee_monthly")
        pipeline = build_employee_monthly_pipeline(emp_code, month)
        raw_explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        explain_json = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": explain_json
        }

    elif ep == "department_summary":
        if not month:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="month is required for department_summary")
        pipeline = build_department_summary_pipeline(month, department)
        raw_explain = db.command(
            "explain",
            {"aggregate": "employees", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        explain_json = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "employees",
            "explain": explain_json
        }

    elif ep == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="month is required for late_leaderboard")
        lim = limit if limit is not None else 10
        pipeline = build_late_leaderboard_pipeline(month, lim, department)
        raw_explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        explain_json = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": explain_json
        }

    elif ep == "department_trend":
        if not department:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="department is required for department_trend")
        if not from_date:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="from is required for department_trend")
        if not to_date:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="to is required for department_trend")
        if to_date < from_date:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="to date must not be before from date")
        d_from = date.fromisoformat(from_date)
        d_to = date.fromisoformat(to_date)
        if (d_to - d_from).days + 1 > 92:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date range must not exceed 92 days")
        pipeline = build_department_trend_pipeline(department, from_date, to_date)
        raw_explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        explain_json = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": explain_json
        }
