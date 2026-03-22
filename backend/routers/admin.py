"""Admin management endpoints: users, bulk-import, enrichment, config, db-stats."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, File, HTTPException, Path, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import DashboardUser, DashboardRoleEnum, User, Visit
from ..schemas import DashboardUserCreate, DashboardUserUpdate, DashboardUserResponse
from ..crud import (
    get_dashboard_users,
    get_dashboard_user_by_username,
    create_dashboard_user,
    update_dashboard_user_password,
    update_dashboard_user_role,
    delete_dashboard_user,
    upsert_student_enrichments,
    apply_enrichment_to_existing_users,
    purge_visits,
    purge_all_data,
    get_purge_schedule,
    upsert_purge_schedule,
)
from ..utils import encrypt_secure_config, decrypt_secure_config, get_password_hash
from .deps import require_login, require_admin

logger = logging.getLogger("browser_reporter")
router = APIRouter()

templates: Jinja2Templates = None  # type: ignore[assignment]

SECURECONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "secureconfig.json",
)


def configure(tpl: Jinja2Templates, secureconfig_path: str) -> None:
    global templates, SECURECONFIG_PATH
    templates = tpl
    SECURECONFIG_PATH = secureconfig_path


async def _read_csv_upload(
    file: UploadFile,
    max_bytes: int,
    required_headers: set[str],
    encoding: str = "utf-8",
) -> csv.DictReader:
    """Read, size-check, decode, and validate headers of an uploaded CSV file."""
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    content = await file.read()
    if len(content) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"CSV file too large (max {max_mb} MB)")
    try:
        csv_content = content.decode(encoding)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
    reader = csv.DictReader(io.StringIO(csv_content))
    if not required_headers.issubset(set(reader.fieldnames or [])):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must contain headers: {', '.join(sorted(required_headers))}",
        )
    return reader


def _validate_bulk_import_rows(csv_reader: csv.DictReader) -> tuple[list[tuple], list[str]]:
    """Validate rows from a bulk-user-import CSV; return (valid_rows, errors)."""
    errors: list[str] = []
    usernames_seen: set[str] = set()
    valid_rows: list[tuple] = []
    for row_num, row in enumerate(csv_reader, start=2):
        username = row.get("username", "").strip()
        password = row.get("password", "").strip()
        role = row.get("role", "").strip().lower()
        if not username or not password or not role:
            errors.append(f"Row {row_num}: Missing required fields")
            continue
        if len(username) < 3 or len(username) > 50:
            errors.append(f"Row {row_num}: Username must be 3-50 characters")
            continue
        if len(password) < 6 or len(password) > 72:
            errors.append(f"Row {row_num}: Password must be 6-72 characters")
            continue
        if role not in ("admin", "user"):
            errors.append(f"Row {row_num}: Role must be 'admin' or 'user'")
            continue
        if username in usernames_seen:
            errors.append(f"Row {row_num}: Duplicate username '{username}' in CSV")
            continue
        usernames_seen.add(username)
        valid_rows.append((row_num, username, password, role))
    return valid_rows, errors


# ── Dashboard user CRUD ──────────────────────────────────────────────────

@router.post("/create-admin-emergency")
async def create_admin_emergency(request: Request, db: AsyncSession = Depends(get_db)):
    """Emergency endpoint to create admin user (admin-only)."""
    await require_admin(request, db)
    result = await db.execute(select(DashboardUser).where(DashboardUser.username == "admin"))
    if result.scalar_one_or_none():
        return {"message": "Admin already exists"}
    admin_user = DashboardUser(
        username="admin",
        password_hash=get_password_hash("admin"),
        role=DashboardRoleEnum.admin,
    )
    db.add(admin_user)
    await db.commit()
    return {"message": "Admin created successfully"}


@router.get("/api/admin/users", response_model=list[DashboardUserResponse])
async def admin_get_users(request: Request, db: AsyncSession = Depends(get_db)):
    """Get all dashboard users (admin only)."""
    await require_admin(request, db)
    return await get_dashboard_users(db)


@router.post("/api/admin/users", response_model=DashboardUserResponse)
async def admin_create_user(
    user_data: DashboardUserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new dashboard user (admin only)."""
    await require_admin(request, db)
    if await get_dashboard_user_by_username(db, user_data.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    new_user = await create_dashboard_user(db, user_data.username, user_data.password, DashboardRoleEnum(user_data.role))
    await db.commit()
    return new_user


@router.put("/api/admin/users/{username}")
async def admin_update_user(
    user_data: DashboardUserUpdate,
    request: Request,
    username: str = Path(..., min_length=1, max_length=50),
    db: AsyncSession = Depends(get_db),
):
    """Update a dashboard user (admin only)."""
    admin_user = await require_admin(request, db)
    user = await get_dashboard_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if admin_user.username == username and user_data.role and user_data.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    if user_data.password:
        await update_dashboard_user_password(db, username, user_data.password)
    if user_data.role:
        await update_dashboard_user_role(db, username, DashboardRoleEnum(user_data.role))
    await db.commit()
    return {"success": True, "message": "User updated successfully"}


@router.delete("/api/admin/users/{username}")
async def admin_delete_user(request: Request, username: str = Path(..., min_length=1, max_length=50), db: AsyncSession = Depends(get_db)):
    """Delete a dashboard user (admin only)."""
    admin_user = await require_admin(request, db)
    if admin_user.username == username:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not await delete_dashboard_user(db, username):
        raise HTTPException(status_code=404, detail="User not found")
    await db.commit()
    return {"success": True, "message": "User deleted successfully"}


# ── Bulk import ──────────────────────────────────────────────────────────

@router.post("/api/admin/users/bulk-import")
async def admin_bulk_import_users(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Bulk import dashboard users from CSV file (admin only)."""
    await require_admin(request, db)

    try:
        csv_reader = await _read_csv_upload(file, 5 * 1024 * 1024, {"username", "password", "role"})
        valid_rows, errors = _validate_bulk_import_rows(csv_reader)

        usernames_to_check = [row[1] for row in valid_rows]
        existing_usernames: set[str] = set()
        if usernames_to_check:
            result = await db.execute(
                select(DashboardUser.username).where(DashboardUser.username.in_(usernames_to_check))
            )
            existing_usernames = set(result.scalars().all())

        created_users: list[dict] = []
        for row_num, username, password, role in valid_rows:
            if username in existing_usernames:
                errors.append(f"Row {row_num}: Username '{username}' already exists")
                continue
            try:
                new_user = await create_dashboard_user(db, username, password, DashboardRoleEnum(role))
                created_users.append({
                    "username": new_user.username,
                    "role": new_user.role.value,
                    "created_at": new_user.created_at.isoformat(),
                })
            except Exception as e:
                errors.append(f"Row {row_num}: {e}")

        if created_users:
            await db.commit()

        return {
            "success": True,
            "message": f"Import completed: {len(created_users)} users created, {len(errors)} errors",
            "created_users": created_users,
            "errors": errors,
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


@router.get("/api/admin/users/example-csv")
async def admin_get_example_csv(request: Request, db: AsyncSession = Depends(get_db)):
    """Download example CSV file for bulk import (admin only)."""
    await require_admin(request, db)
    csv_content = "username,password,role\njohn.admin,SecurePass123,admin\njane.user,UserPass456,user\nbob.manager,ManagerPass789,admin\nalice.analyst,AnalystPass321,user\ncharlie.dev,DevPass654,user"
    return Response(content=csv_content, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=users_import_example.csv"})


# ── Student enrichment ───────────────────────────────────────────────────

@router.post("/api/admin/enrich-students")
async def admin_enrich_students(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import school CSV export to enrich student user records (admin only)."""
    await require_admin(request, db)

    try:
        csv_reader = await _read_csv_upload(
            file,
            10 * 1024 * 1024,
            {"login", "firstName", "lastName", "displayName", "studClass"},
            encoding="utf-8-sig",
        )

        rows = []
        skipped = 0
        now = datetime.now(timezone.utc)

        for row in csv_reader:
            login = (row.get("login") or "").strip().upper()
            if not login:
                skipped += 1
                continue
            rows.append(dict(
                login=login,
                first_name=(row.get("firstName") or "").strip() or None,
                last_name=(row.get("lastName") or "").strip() or None,
                display_name=(row.get("displayName") or "").strip() or None,
                homegroup=(row.get("studClass") or "").strip() or None,
                imported_at=now,
            ))

        imported = await upsert_student_enrichments(db, rows)
        users_updated = await apply_enrichment_to_existing_users(db)
        await db.commit()

        return {"success": True, "imported": imported, "users_updated": users_updated, "skipped": skipped}

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


# ── Secure config ────────────────────────────────────────────────────────

@router.post("/api/admin/secureconfig")
async def admin_generate_secureconfig(
    request: Request,
    plain_config: dict,
    db: AsyncSession = Depends(get_db),
):
    """Generate an encrypted secureconfig.json file. Admins only."""
    await require_admin(request, db)
    encrypted = encrypt_secure_config(plain_config)
    try:
        with open(SECURECONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(encrypted, f, indent=2)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write secureconfig.json: {exc}")
    return {"success": True}


@router.get("/api/admin/secureconfig/current")
async def admin_get_current_config(request: Request, db: AsyncSession = Depends(get_db)):
    """Get the current decrypted configuration for editing. Admins only."""
    await require_admin(request, db)

    if not os.path.exists(SECURECONFIG_PATH):
        return {
            "server_url": "http://localhost:8000",
            "sync_interval_minutes": 5,
            "max_history_age_hours": 24,
            "monitored_users_group": "",
            "monitored_users": [],
            "monitored_hours": {"start": "00:00", "end": "23:59"},
            "browsers": ["chrome", "edge"],
            "log_max_mb": 5,
            "log_roll_count": 3,
            "exit_password": "BRAdmin2025",
        }

    try:
        with open(SECURECONFIG_PATH, "r", encoding="utf-8") as f:
            encrypted_data = json.load(f)
        return decrypt_secure_config(encrypted_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read current config: {exc}")


# ── Database stats ───────────────────────────────────────────────────────

async def _fetch_table_stats(db: AsyncSession) -> list[dict]:
    """Fetch per-table size statistics from PostgreSQL."""
    rows = await db.execute(text("""
        SELECT schemaname, tablename,
               pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size,
               pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) as table_size,
               pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)
                             - pg_relation_size(schemaname||'.'||tablename)) as index_size,
               pg_total_relation_size(schemaname||'.'||tablename) as total_bytes
        FROM pg_tables WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
    """))
    return [dict(row._mapping) for row in rows]


async def _fetch_record_counts(db: AsyncSession) -> dict:
    """Fetch record counts for users, visits, and archive."""
    visits_total = await db.scalar(select(func.count()).select_from(Visit))
    users_total = await db.scalar(select(func.count()).select_from(User))

    archive_exists = await db.scalar(text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'visits_archive');"
    ))
    visits_archive_total = 0
    if archive_exists:
        visits_archive_total = await db.scalar(text("SELECT COUNT(*) FROM visits_archive"))

    return {
        "users": users_total,
        "visits": visits_total,
        "visits_archive": visits_archive_total,
        "total_visits": visits_total + visits_archive_total,
    }


async def _fetch_activity_stats(db: AsyncSession) -> dict:
    """Fetch visit activity metrics (time range, recent counts)."""
    oldest_visit = await db.scalar(select(func.min(Visit.visit_time)))
    newest_visit = await db.scalar(select(func.max(Visit.visit_time)))

    last_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    last_7d = datetime.now(timezone.utc) - timedelta(days=7)
    visits_24h = await db.scalar(select(func.count()).select_from(Visit).where(Visit.visit_time >= last_24h))
    visits_7d = await db.scalar(select(func.count()).select_from(Visit).where(Visit.visit_time >= last_7d))
    active_users_24h = await db.scalar(
        select(func.count(func.distinct(Visit.user_id))).select_from(Visit).where(Visit.visit_time >= last_24h)
    )

    return {
        "oldest_visit": oldest_visit.isoformat() if oldest_visit else None,
        "newest_visit": newest_visit.isoformat() if newest_visit else None,
        "visits_last_24h": visits_24h,
        "visits_last_7d": visits_7d,
        "active_users_24h": active_users_24h,
    }


async def _fetch_db_meta(db: AsyncSession) -> tuple[dict, list[dict], dict, list[dict]]:
    """Fetch database size, index stats, connection info, and slow queries."""
    db_size_row = (await db.execute(text(
        "SELECT pg_size_pretty(pg_database_size(current_database())) as db_size, "
        "pg_database_size(current_database()) as db_bytes;"
    ))).fetchone()
    db_size = dict(db_size_row._mapping) if db_size_row else {"db_size": "Unknown", "db_bytes": 0}

    index_stats = [
        dict(row._mapping)
        for row in await db.execute(text("""
            SELECT schemaname, relname as tablename, indexrelname as indexname,
                   idx_scan as times_used, pg_size_pretty(pg_relation_size(indexrelid)) as index_size
            FROM pg_stat_user_indexes WHERE schemaname = 'public' AND indexrelname LIKE 'idx_%'
            ORDER BY idx_scan DESC LIMIT 20;
        """))
    ]

    conn_row = (await db.execute(text("""
        SELECT count(*) as total_connections,
               count(*) FILTER (WHERE state = 'active') as active_connections,
               count(*) FILTER (WHERE state = 'idle') as idle_connections
        FROM pg_stat_activity WHERE datname = current_database();
    """))).fetchone()
    connection_stats = dict(conn_row._mapping) if conn_row else {}

    try:
        slow_queries = [
            dict(row._mapping)
            for row in await db.execute(text("""
                SELECT substring(query, 1, 100) as query_preview, calls, mean_exec_time, total_exec_time
                FROM pg_stat_statements WHERE mean_exec_time > 100
                ORDER BY mean_exec_time DESC LIMIT 10;
            """))
        ]
    except Exception:
        slow_queries = []

    return db_size, index_stats, connection_stats, slow_queries


@router.get("/api/admin/db-stats")
async def admin_get_database_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Get comprehensive database statistics and health metrics. Admin only."""
    await require_admin(request, db)

    try:
        table_stats = await _fetch_table_stats(db)
        records = await _fetch_record_counts(db)
        activity = await _fetch_activity_stats(db)
        db_size, index_stats, connection_stats, slow_queries = await _fetch_db_meta(db)

        return {
            "database": {"total_size": db_size["db_size"], "total_bytes": db_size["db_bytes"], "connection_info": connection_stats},
            "tables": table_stats,
            "records": records,
            "activity": activity,
            "indexes": index_stats,
            "slow_queries": slow_queries,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve database statistics: {e}")


# ── Database purge ────────────────────────────────────────────────────────

def _calculate_next_purge(schedule_type: str, from_date: datetime | None = None) -> datetime | None:
    """Calculate the next purge date based on schedule type."""
    now = from_date or datetime.now(timezone.utc)

    if schedule_type == "weekly":
        # Next Monday at 02:00 UTC
        days_ahead = (7 - now.weekday()) % 7
        if days_ahead == 0 and now.hour >= 2:
            days_ahead = 7
        return now.replace(hour=2, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)

    elif schedule_type == "monthly":
        # 1st of next month at 02:00 UTC
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1, hour=2, minute=0, second=0, microsecond=0)
        return now.replace(month=now.month + 1, day=1, hour=2, minute=0, second=0, microsecond=0)

    elif schedule_type == "term":
        # Victorian school term start dates (approximate)
        term_starts = [
            (1, 28),   # Term 1: ~Jan 28
            (4, 14),   # Term 2: ~Apr 14
            (7, 14),   # Term 3: ~Jul 14
            (10, 7),   # Term 4: ~Oct 7
        ]
        for month, day in term_starts:
            candidate = now.replace(month=month, day=day, hour=2, minute=0, second=0, microsecond=0)
            if candidate > now:
                return candidate
        # All terms passed this year, use Term 1 next year
        return now.replace(year=now.year + 1, month=1, day=28, hour=2, minute=0, second=0, microsecond=0)

    elif schedule_type == "yearly":
        # January 15 at 02:00 UTC (after school year starts)
        candidate = now.replace(month=1, day=15, hour=2, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate.replace(year=now.year + 1)
        return candidate

    return None


@router.post("/api/admin/purge")
async def admin_purge_data(
    request: Request,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Manually purge database data. Admin only."""
    await require_admin(request, db)

    purge_type = body.get("type", "visits")  # "visits" or "all"
    retain_days = body.get("retain_days", 0)

    try:
        if purge_type == "all":
            result = await purge_all_data(db)
            await db.commit()
            logger.info("Admin clear data: full reset — %s", result)
            return {"success": True, "message": "Full data reset completed", **result}
        else:
            deleted = await purge_visits(db, retain_days=retain_days)
            await db.commit()
            label = f"older than {retain_days} days" if retain_days > 0 else "all"
            logger.info("Admin clear data: %d visits deleted (%s)", deleted, label)
            return {"success": True, "message": f"{deleted:,} visits cleared", "visits_deleted": deleted}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Clear data failed: {e}")


@router.get("/api/admin/purge-schedule")
async def admin_get_purge_schedule(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current purge schedule. Admin only."""
    await require_admin(request, db)
    schedule = await get_purge_schedule(db)
    if not schedule:
        return {"schedule_type": "disabled", "retain_days": 0, "next_purge_at": None, "last_purge_at": None}
    return {
        "schedule_type": schedule.schedule_type,
        "retain_days": schedule.retain_days,
        "next_purge_at": schedule.next_purge_at.isoformat() if schedule.next_purge_at else None,
        "last_purge_at": schedule.last_purge_at.isoformat() if schedule.last_purge_at else None,
        "updated_by": schedule.updated_by,
        "updated_at": schedule.updated_at.isoformat() if schedule.updated_at else None,
    }


@router.post("/api/admin/purge-schedule")
async def admin_set_purge_schedule(
    request: Request,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update purge schedule. Admin only."""
    admin_user = await require_admin(request, db)

    schedule_type = body.get("schedule_type", "disabled")
    if schedule_type not in ("disabled", "weekly", "monthly", "term", "yearly"):
        raise HTTPException(status_code=400, detail="Invalid schedule type")

    retain_days = int(body.get("retain_days", 0))
    if retain_days < 0:
        raise HTTPException(status_code=400, detail="retain_days must be >= 0")

    next_purge = _calculate_next_purge(schedule_type) if schedule_type != "disabled" else None

    await upsert_purge_schedule(db, schedule_type, retain_days, next_purge, admin_user.username)
    await db.commit()

    logger.info("Clear schedule updated: type=%s, retain=%d days, next=%s, by=%s",
                schedule_type, retain_days, next_purge, admin_user.username)

    return {
        "success": True,
        "schedule_type": schedule_type,
        "retain_days": retain_days,
        "next_purge_at": next_purge.isoformat() if next_purge else None,
    }


# ── Template pages ───────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/client-config", response_class=HTMLResponse)
async def client_config_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("client_config.html", {"request": request})
