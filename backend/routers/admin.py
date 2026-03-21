"""Admin management endpoints: users, bulk-import, enrichment, config, db-stats."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
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


@router.get("/api/admin/users", response_model=List[DashboardUserResponse])
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
    username: str,
    user_data: DashboardUserUpdate,
    request: Request,
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
async def admin_delete_user(username: str, request: Request, db: AsyncSession = Depends(get_db)):
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

    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    try:
        content = await file.read()
        csv_content = content.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(csv_content))

        required_headers = {"username", "password", "role"}
        if not required_headers.issubset(set(csv_reader.fieldnames or [])):
            raise HTTPException(status_code=400, detail=f"CSV must contain headers: {', '.join(required_headers)}")

        created_users: list[dict] = []
        errors: list[str] = []
        usernames_to_check: list[str] = []
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
            if username in usernames_to_check:
                errors.append(f"Row {row_num}: Duplicate username '{username}' in CSV")
                continue

            usernames_to_check.append(username)
            valid_rows.append((row_num, username, password, role))

        existing_usernames: set[str] = set()
        if usernames_to_check:
            result = await db.execute(
                select(DashboardUser.username).where(DashboardUser.username.in_(usernames_to_check))
            )
            existing_usernames = set(result.scalars().all())

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

    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
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

    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    try:
        content = await file.read()
        csv_content = content.decode("utf-8-sig")
        csv_reader = csv.DictReader(io.StringIO(csv_content))

        required_cols = {"login", "firstName", "lastName", "displayName", "studClass"}
        if not required_cols.issubset(set(csv_reader.fieldnames or [])):
            raise HTTPException(status_code=400, detail=f"CSV must contain columns: {', '.join(sorted(required_cols))}")

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

    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
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

@router.get("/api/admin/db-stats")
async def admin_get_database_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Get comprehensive database statistics and health metrics. Admin only."""
    await require_admin(request, db)

    try:
        # Table sizes
        table_stats = [
            dict(row._mapping)
            for row in await db.execute(text("""
                SELECT schemaname, tablename,
                       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size,
                       pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) as table_size,
                       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)
                                     - pg_relation_size(schemaname||'.'||tablename)) as index_size,
                       pg_total_relation_size(schemaname||'.'||tablename) as total_bytes
                FROM pg_tables WHERE schemaname = 'public'
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
            """))
        ]

        visits_total = await db.scalar(select(func.count()).select_from(Visit))
        users_total = await db.scalar(select(func.count()).select_from(User))

        archive_exists = await db.scalar(text(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'visits_archive');"
        ))
        visits_archive_total = 0
        if archive_exists:
            visits_archive_total = await db.scalar(text("SELECT COUNT(*) FROM visits_archive"))

        oldest_visit = await db.scalar(select(func.min(Visit.visit_time)))
        newest_visit = await db.scalar(select(func.max(Visit.visit_time)))

        last_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        last_7d = datetime.now(timezone.utc) - timedelta(days=7)
        visits_24h = await db.scalar(select(func.count()).select_from(Visit).where(Visit.visit_time >= last_24h))
        visits_7d = await db.scalar(select(func.count()).select_from(Visit).where(Visit.visit_time >= last_7d))
        active_users_24h = await db.scalar(
            select(func.count(func.distinct(Visit.user_id))).select_from(Visit).where(Visit.visit_time >= last_24h)
        )

        db_size_row = (await db.execute(text(
            "SELECT pg_size_pretty(pg_database_size(current_database())) as db_size, pg_database_size(current_database()) as db_bytes;"
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

        return {
            "database": {"total_size": db_size["db_size"], "total_bytes": db_size["db_bytes"], "connection_info": connection_stats},
            "tables": table_stats,
            "records": {"users": users_total, "visits": visits_total, "visits_archive": visits_archive_total, "total_visits": visits_total + visits_archive_total},
            "activity": {
                "oldest_visit": oldest_visit.isoformat() if oldest_visit else None,
                "newest_visit": newest_visit.isoformat() if newest_visit else None,
                "visits_last_24h": visits_24h,
                "visits_last_7d": visits_7d,
                "active_users_24h": active_users_24h,
            },
            "indexes": index_stats,
            "slow_queries": slow_queries,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve database statistics: {e}")


# ── Template pages ───────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/client-config", response_class=HTMLResponse)
async def client_config_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("client_config.html", {"request": request})
