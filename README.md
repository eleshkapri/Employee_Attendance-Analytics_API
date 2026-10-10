# Employee Attendance & Analytics API

FastAPI and MongoDB (Atlas / 6.0+) service implementing attendance tracking, regularization, and workforce analytics with full business rule enforcement (R1–R10).

## 1. Installation

```bash
# Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## 2. Configuration

Copy `.env.example` to `.env` or provide environment variables directly:

```bash
cp .env.example .env
```

Environment variables:
- `MONGO_URI`: MongoDB connection string (e.g. `mongodb+srv://...` or `mongodb://localhost:27017`)
- `MONGO_DB`: Target database name (default: `attendance_db` or `attendance_dev`)

## 3. Seed Sample Data (Optional)

```bash
python sample_seed.py
```

## 4. Run the Service

```bash
uvicorn app.main:app --port 8000
```

Interactive API documentation is available at:
`http://localhost:8000/docs`

## 5. Endpoints Overview

- `GET /health`: Health check and live database ping.
- `POST /employees`: Create employee (validates email, shift times, joined_on).
- `GET /employees`: List employees with 1-based pagination.
- `POST /attendance/punch-in`: Record punch-in with R1-R2 business rule evaluation and conflict protection.
- `GET /attendance`: List attendance logs sorted by date descending, emp_code ascending.
- `POST /attendance/punch-out`: Close attendance record with atomic race condition protection, work hours, overtime, and half-day calculation.
- `PATCH /attendance/{emp_code}/{date}`: Regularize attendance with full audit history logging and optimistic concurrency locking.
- `GET /analytics/employees/{emp_code}/monthly`: Employee monthly summary with dynamically computed Mon–Fri working days.
- `GET /analytics/departments/summary`: Department monthly summary across both collections, counting employees with zero logs.
- `GET /analytics/leaderboard/late`: Late leaderboard with standard competition ranking (`$setWindowFields` / `$rank`).
- `GET /analytics/departments/{department}/trend`: Daily attendance trend with calendar densification (`$densify`) and 7-day moving average.
- `GET /admin/explain/{endpoint}`: MongoDB explain (`executionStats`) proving queries use index scans (`IXSCAN`) and zero collection scans (`COLLSCAN`).

## 6. Project Deliverables

- `app/main.py`: Complete production implementation.
- `REVIEW.md`: Starter code audit cataloging 12 defects and their resolutions.
- `DECISIONS.md`: Architectural decisions and answers to the five design questions.
- `requirements.txt`: Minimal pinned production dependencies.
