from __future__ import annotations

import os
import secrets
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from pathlib import Path
import asyncpg

from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text

from .database import get_db, engine, Base, AsyncSessionLocal
from .schemas import ReportIn, DashboardUserCreate, DashboardUserUpdate, DashboardUserResponse
from .crud import (
    upsert_user, bulk_insert_visits, get_dashboard_users, get_dashboard_user_by_username,
    create_dashboard_user, update_dashboard_user_password, update_dashboard_user_role,
    delete_dashboard_user, upsert_student_enrichments, apply_enrichment_to_existing_users
)
from .models import DashboardUser, DashboardRoleEnum, User, Visit
from .utils import encrypt_secure_config, decrypt_secure_config, verify_password, get_password_hash

import uvicorn

SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))

app = FastAPI(title="Browser Reporter Server")

# CORS – restrict to same-origin by default; override via CORS_ORIGINS env var
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session middleware for login state with security settings
app.add_middleware(
    SessionMiddleware, 
    secret_key=SESSION_SECRET, 
    same_site="lax",
    https_only=False,  # Set to True in production with HTTPS
    max_age=3600  # 1 hour session timeout
)

# Static files & templates
base_dir = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(base_dir, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))


# --------------------------- Utility functions ---------------------------

def get_current_dashboard_user(request: Request) -> Optional[str]:
    return request.session.get("dashboard_user")


def require_login(request: Request):
    username = get_current_dashboard_user(request)
    if not username:
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})
    return username


async def require_admin(request: Request, db: AsyncSession) -> DashboardUser:
    """Require admin role and return the admin user."""
    username = require_login(request)
    result = await db.execute(select(DashboardUser).where(DashboardUser.username == username))
    user: Optional[DashboardUser] = result.scalar_one_or_none()
    if not user or user.role != DashboardRoleEnum.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def create_initial_admin():
    async with AsyncSessionLocal() as session:
        try:
            # Check if admin user specifically exists
            result = await session.execute(select(DashboardUser).where(DashboardUser.username == "admin"))
            existing_admin = result.scalar_one_or_none()
            if not existing_admin:
                admin_user = DashboardUser(
                    username="admin",
                    password_hash=get_password_hash("admin"),
                    role=DashboardRoleEnum.admin,
                )
                session.add(admin_user)
                await session.commit()
                print("⚠️  Created default admin: admin / admin (please change password)")
            else:
                print("✅ Admin user already exists")
        except Exception as e:
            print(f"⚠️  Admin setup warning: {e}")
            # Ensure session is rolled back on error
            try:
                await session.rollback()
            except Exception:
                pass
            # Don't fail startup if admin creation fails
            pass


# --------------------------- API Endpoints ------------------------------

@app.post("/create-admin-emergency")
async def create_admin_emergency(request: Request, db: AsyncSession = Depends(get_db)):
    """Emergency endpoint to create admin user (admin-only)."""
    await require_admin(request, db)
    try:
        result = await db.execute(select(DashboardUser).where(DashboardUser.username == "admin"))
        existing = result.scalar_one_or_none()
        if existing:
            return {"message": "Admin already exists"}
        admin_user = DashboardUser(
            username="admin",
            password_hash=get_password_hash("admin"),
            role=DashboardRoleEnum.admin,
        )
        db.add(admin_user)
        await db.commit()
        return {"message": "Admin created successfully"}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/reports/data")
async def ingest_report(report: ReportIn, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await upsert_user(db, report.UserInfo)
    await bulk_insert_visits(db, user_id, report.Visits)
    await db.commit()
    return {"success": True}


# -------------------------- Auth & Dashboard ----------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(DashboardUser).where(DashboardUser.username == username))
        user: Optional[DashboardUser] = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

        request.session["dashboard_user"] = username
        response = RedirectResponse(url="/", status_code=302)
        return response
    except Exception as e:
        # Log the error but don't expose details to user
        print(f"Login error: {e}")
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# New JSON endpoints ---------------------------------------------------

@app.get("/api/auth/user")
async def get_auth_user(request: Request, db: AsyncSession = Depends(get_db)):
    username = get_current_dashboard_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = await db.execute(select(DashboardUser).where(DashboardUser.username == username))
    user: DashboardUser | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401)
    
    response = {
        "username": user.username,
        "displayName": user.username,  # no separate display yet
        "role": user.role.value,
    }
    
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    request.session.clear()
    return {"success": True}


@app.get("/api/reports/all")
async def reports_all(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    homegroup: Optional[str] = None,
    days: Optional[float] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    # Ensure logged in
    require_login(request)
    
    # Validate pagination parameters
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 1000:
        page_size = 50

    # Base selectable columns
    selectable = select(
        User.username,
        func.coalesce(User.display_name, User.username).label("display_name"),
        User.email,
        User.homegroup.label("department"),
        func.count(Visit.id).label("total_visits"),
        func.count(func.distinct(Visit.url)).label("unique_urls"),
        func.max(Visit.visit_time).label("last_activity"),
        func.string_agg(func.distinct(Visit.computer_name), text("', '")).label("computers")
    ).select_from(User).outerjoin(Visit, Visit.user_id == User.id)

    # Apply optional filters (before grouping)
    if homegroup:
        selectable = selectable.where(User.homegroup == homegroup)

    if days is not None:
            # Only consider visits within cutoff for activity-related aggregations
        try:
            days_float = float(days)
        except Exception:
            days_float = None
        if days_float is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
            selectable = selectable.where(Visit.visit_time >= cutoff)

    if search:
        search_lower = f"%{search.lower()}%"
        selectable = selectable.where(
            func.lower(User.username).like(search_lower) |
            func.lower(func.coalesce(User.display_name, '')).like(search_lower) |
            func.lower(func.coalesce(User.email, '')).like(search_lower) |
            func.lower(func.coalesce(User.homegroup, '')).like(search_lower)
        )

    # Grouping and ordering
    selectable = selectable.group_by(User.id).order_by(User.username)

    # Total count with same filters (count distinct users)
    count_query = select(func.count(func.distinct(User.id))).select_from(User).outerjoin(Visit, Visit.user_id == User.id)
    if homegroup:
        count_query = count_query.where(User.homegroup == homegroup)
    if days is not None:
        try:
            days_float = float(days)
        except Exception:
            days_float = None
        if days_float is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
            count_query = count_query.where(Visit.visit_time >= cutoff)
    if search:
        search_lower = f"%{search.lower()}%"
        count_query = count_query.where(
            func.lower(User.username).like(search_lower) |
            func.lower(func.coalesce(User.display_name, '')).like(search_lower) |
            func.lower(func.coalesce(User.email, '')).like(search_lower) |
            func.lower(func.coalesce(User.homegroup, '')).like(search_lower)
        )

    count_result = await db.execute(count_query)
    total_count = count_result.scalar() or 0

    # Apply pagination to main selectable
    query = selectable.limit(page_size).offset((page - 1) * page_size)
    
    result = await db.execute(query)
    rows = result.fetchall()
    data = []
    for r in rows:
        data.append({
            "username": r.username,
            "displayName": r.display_name,
            "email": r.email,
            "department": r.department,
            "totalVisits": r.total_visits,
            "uniqueUrls": r.unique_urls,
            "lastActivity": r.last_activity.isoformat() if r.last_activity else None,
            "computers": r.computers,
        })
    
    # Calculate pagination info
    total_pages = (total_count + page_size - 1) // page_size
    
    return {
        "data": data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1
        }
    }


@app.get("/api/meta/homegroups")
async def get_homegroups(request: Request, db: AsyncSession = Depends(get_db)):
    """Return distinct homegroups for populating the dropdown."""
    require_login(request)
    result = await db.execute(
        select(func.distinct(User.homegroup)).where(User.homegroup.is_not(None)).order_by(User.homegroup)
    )
    rows = result.scalars().all()
    # Filter out empty strings if present
    homegroups = [hg for hg in rows if (hg or '').strip()]
    return {"homegroups": homegroups}


@app.get("/api/reports/search")
async def search_website(
    request: Request,
    url: str,
    page: int = 1,
    page_size: int = 50,
    days: float | None = None,
    homegroup: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Search for users who visited a specific URL or website"""
    require_login(request)
    
    # Validate pagination parameters
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 1000:
        page_size = 50
    
    # Build base query - hybrid search for best performance and flexibility
    # Use both full-text search AND ILIKE for comprehensive results
    search_term = url.strip()
    
    query = (
        select(
            Visit.url,
            Visit.title,
            Visit.visit_time,
            Visit.computer_name,
            User.username,
            User.display_name,
            User.email,
            User.homegroup,
            func.coalesce(
                func.ts_rank(Visit.search_vector, func.websearch_to_tsquery('english', search_term)), 0
            ).label('rank')
        )
        .select_from(Visit)
        .join(User, Visit.user_id == User.id)
        .where(
            # Use OR condition: full-text search (when vector exists) OR ILIKE fallback
            (
                (Visit.search_vector.isnot(None)) &
                (Visit.search_vector.op('@@')(func.websearch_to_tsquery('english', search_term)))
            ) |
            (Visit.url.ilike(f"%{search_term}%")) |
            (Visit.title.ilike(f"%{search_term}%"))
        )
        .order_by(text('rank DESC'), Visit.visit_time.desc())
    )
    if homegroup:
        query = query.where(User.homegroup == homegroup)
    
    # Apply date filter if specified
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        query = query.where(Visit.visit_time >= cutoff)
    
    # Get total count for pagination using hybrid search
    count_query = (
        select(func.count(Visit.id))
        .select_from(Visit)
        .join(User, Visit.user_id == User.id)
        .where(
            # Same OR condition as main query
            (
                (Visit.search_vector.isnot(None)) &
                (Visit.search_vector.op('@@')(func.websearch_to_tsquery('english', search_term)))
            ) |
            (Visit.url.ilike(f"%{search_term}%")) |
            (Visit.title.ilike(f"%{search_term}%"))
        )
    )
    if homegroup:
        count_query = count_query.where(User.homegroup == homegroup)
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        count_query = count_query.where(Visit.visit_time >= cutoff)
    
    total_result = await db.execute(count_query)
    total_count = total_result.scalar()
    
    # Apply pagination
    query = query.order_by(Visit.visit_time.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    
    result = await db.execute(query)
    visits = result.fetchall()
    
    # Calculate pagination metadata
    total_pages = (total_count + page_size - 1) // page_size
    has_next = page < total_pages
    has_prev = page > 1
    
    # Get unique users count for summary
    unique_users_query = (
        select(func.count(func.distinct(User.id)))
        .select_from(Visit)
        .join(User, Visit.user_id == User.id)
        .where(Visit.url.ilike(f"%{url}%"))
    )
    if homegroup:
        unique_users_query = unique_users_query.where(User.homegroup == homegroup)
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        unique_users_query = unique_users_query.where(Visit.visit_time >= cutoff)
    
    unique_users_result = await db.execute(unique_users_query)
    unique_users_count = unique_users_result.scalar()
    
    return {
        "data": [
            {
                "url": v.url,
                "title": v.title,
                "timestamp": v.visit_time.isoformat(),
                "computerName": v.computer_name,
                "username": v.username,
                "displayName": v.display_name,
                "email": v.email,
                "homegroup": v.homegroup
            }
            for v in visits
        ],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev
        },
        "summary": {
            "search_term": url,
            "total_visits": total_count,
            "unique_users": unique_users_count,
            "days_filter": days
        }
    }


@app.get("/api/reports/user/{username}")
async def reports_user(username: str, request: Request, days: float | None = None, page: int = 1, page_size: int = 50, db: AsyncSession = Depends(get_db)):
    require_login(request)
    
    # Validate pagination parameters
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 1000:  # Limit max page size to prevent abuse
        page_size = 50
    
    # Get user id
    result = await db.execute(select(User.id).where(User.username == username))
    user_id_row = result.scalar_one_or_none()
    if user_id_row is None:
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user_id_row

    # Build base query
    query = select(Visit).where(Visit.user_id == user_id)
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        query = query.where(Visit.visit_time >= cutoff)
    
    # Get total count for pagination
    count_query = select(func.count(Visit.id)).where(Visit.user_id == user_id)
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        count_query = count_query.where(Visit.visit_time >= cutoff)
    
    total_result = await db.execute(count_query)
    total_count = total_result.scalar()
    
    # Apply pagination
    query = query.order_by(Visit.visit_time.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    visits = result.scalars().all()

    # Calculate pagination metadata
    total_pages = (total_count + page_size - 1) // page_size  # Ceiling division
    has_next = page < total_pages
    has_prev = page > 1

    return {
        "data": [
            {
                "timestamp": v.visit_time.isoformat() if v.visit_time else None,
                "title": v.title,
                "url": v.url,
                "computerName": v.computer_name,
            }
            for v in visits
        ],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev
        }
    }


# Admin Management API Endpoints -------------------------------------

@app.get("/api/admin/users", response_model=List[DashboardUserResponse])
async def admin_get_users(request: Request, db: AsyncSession = Depends(get_db)):
    """Get all dashboard users (admin only)."""
    await require_admin(request, db)
    users = await get_dashboard_users(db)
    return users


@app.post("/api/admin/users", response_model=DashboardUserResponse)
async def admin_create_user(user_data: DashboardUserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new dashboard user (admin only)."""
    await require_admin(request, db)
    
    # Check if username already exists
    existing = await get_dashboard_user_by_username(db, user_data.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    # Create the user
    new_user = await create_dashboard_user(
        db, 
        user_data.username, 
        user_data.password, 
        DashboardRoleEnum(user_data.role)
    )
    await db.commit()
    return new_user


@app.put("/api/admin/users/{username}")
async def admin_update_user(username: str, user_data: DashboardUserUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    """Update a dashboard user (admin only)."""
    admin_user = await require_admin(request, db)
    
    # Check if user exists
    user = await get_dashboard_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from demoting themselves
    if admin_user.username == username and user_data.role and user_data.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    
    # Update password if provided
    if user_data.password:
        await update_dashboard_user_password(db, username, user_data.password)
    
    # Update role if provided
    if user_data.role:
        await update_dashboard_user_role(db, username, DashboardRoleEnum(user_data.role))
    
    await db.commit()
    return {"success": True, "message": "User updated successfully"}


@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a dashboard user (admin only)."""
    admin_user = await require_admin(request, db)
    
    # Prevent admin from deleting themselves
    if admin_user.username == username:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    
    # Check if user exists and delete
    success = await delete_dashboard_user(db, username)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.commit()
    return {"success": True, "message": "User deleted successfully"}


@app.post("/api/admin/users/bulk-import")
async def admin_bulk_import_users(
    request: Request, 
    file: UploadFile = File(...), 
    db: AsyncSession = Depends(get_db)
):
    """Bulk import dashboard users from CSV file (admin only)."""
    await require_admin(request, db)
    
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    
    try:
        # Read CSV content
        content = await file.read()
        csv_content = content.decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(csv_content))
        
        created_users = []
        errors = []
        
        # Expected CSV headers
        required_headers = {'username', 'password', 'role'}
        
        # Check if CSV has required headers
        if not required_headers.issubset(set(csv_reader.fieldnames or [])):
            raise HTTPException(
                status_code=400, 
                detail=f"CSV must contain headers: {', '.join(required_headers)}"
            )
        
        # Collect all usernames to check for duplicates in batch
        usernames_to_check = []
        valid_rows = []
        
        # First pass: validate data and collect usernames
        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (accounting for header)
            try:
                # Validate required fields
                username = row.get('username', '').strip()
                password = row.get('password', '').strip()
                role = row.get('role', '').strip().lower()
                
                if not username or not password or not role:
                    errors.append(f"Row {row_num}: Missing required fields")
                    continue
                
                # Validate username length
                if len(username) < 3 or len(username) > 50:
                    errors.append(f"Row {row_num}: Username must be 3-50 characters")
                    continue
                
                # Validate password length
                if len(password) < 6 or len(password) > 72:
                    errors.append(f"Row {row_num}: Password must be 6-72 characters")
                    continue
                
                # Validate role
                if role not in ['admin', 'user']:
                    errors.append(f"Row {row_num}: Role must be 'admin' or 'user'")
                    continue
                
                # Check for duplicate usernames within the CSV
                if username in usernames_to_check:
                    errors.append(f"Row {row_num}: Duplicate username '{username}' in CSV")
                    continue
                
                usernames_to_check.append(username)
                valid_rows.append((row_num, username, password, role))
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                continue
        
        # Batch check for existing usernames in database
        if usernames_to_check:
            result = await db.execute(
                select(DashboardUser.username).where(DashboardUser.username.in_(usernames_to_check))
            )
            existing_usernames = set(result.scalars().all())
        else:
            existing_usernames = set()
        
        # Second pass: create users that don't exist
        for row_num, username, password, role in valid_rows:
            try:
                if username in existing_usernames:
                    errors.append(f"Row {row_num}: Username '{username}' already exists")
                    continue
                
                # Create the user
                new_user = await create_dashboard_user(
                    db, 
                    username, 
                    password, 
                    DashboardRoleEnum(role)
                )
                created_users.append({
                    "username": new_user.username,
                    "role": new_user.role.value,
                    "created_at": new_user.created_at.isoformat()
                })
                
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
        
        # Commit all changes
        if created_users:
            await db.commit()
        
        return {
            "success": True,
            "message": f"Import completed: {len(created_users)} users created, {len(errors)} errors",
            "created_users": created_users,
            "errors": errors
        }
        
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@app.get("/api/admin/users/example-csv")
async def admin_get_example_csv(request: Request, db: AsyncSession = Depends(get_db)):
    """Download example CSV file for bulk import (admin only)."""
    await require_admin(request, db)
    
    # Create example CSV content
    csv_content = """username,password,role
john.admin,SecurePass123,admin
jane.user,UserPass456,user
bob.manager,ManagerPass789,admin
alice.analyst,AnalystPass321,user
charlie.dev,DevPass654,user"""
    
    # Return CSV file as downloadable content
    from fastapi.responses import Response
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=users_import_example.csv"
        }
    )


@app.post("/api/admin/enrich-students")
async def admin_enrich_students(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import school CSV export to enrich student user records (admin only).
    Only stores: login, firstName, lastName, displayName, studClass.
    All other columns are ignored.
    """
    await require_admin(request, db)

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    try:
        content = await file.read()
        csv_content = content.decode("utf-8-sig")  # handle BOM if present
        csv_reader = csv.DictReader(io.StringIO(csv_content))

        required_cols = {"login", "firstName", "lastName", "displayName", "studClass"}
        if not required_cols.issubset(set(csv_reader.fieldnames or [])):
            raise HTTPException(
                status_code=400,
                detail=f"CSV must contain columns: {', '.join(sorted(required_cols))}",
            )

        rows = []
        skipped = 0
        now = datetime.now(timezone.utc)

        for row in csv_reader:
            login = (row.get("login") or "").strip().upper()
            if not login:
                skipped += 1
                continue
            rows.append(
                dict(
                    login=login,
                    first_name=(row.get("firstName") or "").strip() or None,
                    last_name=(row.get("lastName") or "").strip() or None,
                    display_name=(row.get("displayName") or "").strip() or None,
                    homegroup=(row.get("studClass") or "").strip() or None,
                    imported_at=now,
                )
            )

        imported = await upsert_student_enrichments(db, rows)
        users_updated = await apply_enrichment_to_existing_users(db)
        await db.commit()

        return {
            "success": True,
            "imported": imported,
            "users_updated": users_updated,
            "skipped": skipped,
        }

    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


# Serve Bootstrap dashboard -------------------------------------------

@app.get("/dashboard.html", response_class=HTMLResponse)
async def dashboard_bootstrap(request: Request):
    require_login(request)
    # Serve the new dashboard under the legacy path
    return templates.TemplateResponse("dashboard2.html", {"request": request})

@app.get("/dashboard2", response_class=HTMLResponse)
async def dashboard2(request: Request):
    require_login(request)
    # Redirect to canonical dashboard path
    return RedirectResponse(url="/dashboard.html")


@app.get("/keyword-search", response_class=HTMLResponse)
async def keyword_search_page(request: Request, db: AsyncSession = Depends(get_db)):
    require_login(request)
    return templates.TemplateResponse("keyword_search.html", {"request": request})

# Redirect root to dashboard
@app.get("/")
async def root_redirect(request: Request):
    require_login(request)
    return RedirectResponse(url="/dashboard.html")

# New standalone pages for Admin and Client Config ---------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/client-config", response_class=HTMLResponse)
async def client_config_page(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    return templates.TemplateResponse("client_config.html", {"request": request})


@app.get("/user/{username}", response_class=HTMLResponse)
async def user_page(username: str, request: Request):
    require_login(request)
    return templates.TemplateResponse("user.html", {"request": request, "username": username})


# -------------------------- Auto-Migration Functions --------------------

def _split_sql_statements(sql_content: str) -> list[str]:
    """Split SQL content into individual statements, respecting DO $$ blocks."""
    statements: list[str] = []
    current: list[str] = []
    in_dollar_block = False

    for line in sql_content.splitlines():
        stripped = line.strip()
        if not stripped or (stripped.startswith("--") and not current):
            continue
        current.append(line)
        if stripped.upper().startswith("DO $$") or stripped.upper().startswith("DO $"):
            in_dollar_block = True
        if in_dollar_block and stripped.endswith("$$;"):
            in_dollar_block = False
            statements.append("\n".join(current))
            current = []
            continue
        if not in_dollar_block and stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []

    if current:
        stmt = "\n".join(current).strip()
        if stmt:
            statements.append(stmt)

    return [s for s in statements if s.strip() and not all(
        ln.strip().startswith("--") or not ln.strip() for ln in s.splitlines()
    )]


async def auto_apply_migrations():
    """
    Automatically apply database migrations on startup.
    This runs before the application starts serving requests.
    """
    # Database connection settings
    # Use 'db' as host when running inside Docker, otherwise 'localhost'
    db_host = os.getenv("DB_HOST", "db")  # Default to 'db' for Docker
    db_config = {
        "host": db_host,
        "port": int(os.getenv("DB_PORT", "5432")),
        "user": os.getenv("DB_USER", "browser_reporter"),
        "password": os.getenv("DB_PASSWORD", "browser_reporter"),
        "database": os.getenv("DB_NAME", "browser_reporter"),
    }

    conn = None
    try:
        # Connect to database
        print("🔄 Checking for pending database migrations...")
        conn = await asyncpg.connect(**db_config)

        # Create migration tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                migration_name VARCHAR(255) UNIQUE NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                execution_time_seconds DECIMAL(10, 3)
            )
        """)

        # Find migration files
        migrations_dir = Path(__file__).parent / "migrations"
        if not migrations_dir.exists():
            print("⚠️  No migrations directory found, skipping migrations")
            return

        migration_files = sorted(migrations_dir.glob("*.sql"))

        if not migration_files:
            print("✓ No SQL migration files found")
            return

        # Apply each migration
        applied_count = 0
        skipped_count = 0

        for migration_file in migration_files:
            migration_name = migration_file.name

            # Check if already applied
            is_applied = await conn.fetchval(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = $1",
                migration_name
            )

            if is_applied:
                skipped_count += 1
                continue

            # Apply migration — execute each statement individually so that
            # CREATE INDEX CONCURRENTLY works (cannot run inside a transaction).
            print(f"📋 Applying migration: {migration_name}")
            sql_content = migration_file.read_text()

            start_time = datetime.now()

            try:
                for stmt in _split_sql_statements(sql_content):
                    try:
                        await conn.execute(stmt)
                    except Exception as stmt_err:
                        err_msg = str(stmt_err).lower()
                        if "already exists" in err_msg or "duplicate" in err_msg:
                            continue
                        raise

                execution_time = (datetime.now() - start_time).total_seconds()

                # Record migration
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (migration_name, execution_time_seconds)
                    VALUES ($1, $2)
                    """,
                    migration_name,
                    execution_time
                )

                print(f"   ✅ Applied in {execution_time:.2f}s")
                applied_count += 1

            except Exception as e:
                print(f"   ❌ Migration failed: {e}")
                # Continue with other migrations
                continue

        # Summary
        if applied_count > 0:
            print(f"✅ Applied {applied_count} new migration(s)")
        if skipped_count > 0:
            print(f"⏭️  Skipped {skipped_count} already-applied migration(s)")
        if applied_count == 0 and skipped_count == 0:
            print("✓ No migrations needed")

    except asyncpg.exceptions.InvalidCatalogNameError:
        print("⚠️  Database does not exist yet, will be created by SQLAlchemy")
    except asyncpg.exceptions.PostgresConnectionError as e:
        print(f"⚠️  Could not connect to database for migrations: {e}")
        print("   Migrations will be skipped. Database may not be ready yet.")
    except Exception as e:
        print(f"⚠️  Migration error: {e}")
        print("   Application will continue startup, but performance may be degraded")
    finally:
        if conn:
            await conn.close()


# -------------------------- Startup Events -----------------------------

@app.on_event("startup")
async def on_startup():
    # Create tables (only creates new tables, doesn't alter existing ones)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ensure schema matches models — add any missing columns that
    # create_all won't handle on existing tables.
    await _ensure_schema_columns()

    # Auto-apply database migrations (indexes, etc.)
    await auto_apply_migrations()
    # ensure initial admin exists
    await create_initial_admin()


async def _ensure_schema_columns():
    """Add missing columns to existing tables so queries don't crash.
    create_all only creates new tables — it won't ALTER existing ones."""
    import asyncpg as _apg

    db_host = os.getenv("DB_HOST", "db")
    conn = None
    try:
        conn = await _apg.connect(
            host=db_host,
            port=int(os.getenv("DB_PORT", "5432")),
            user=os.getenv("DB_USER", "browser_reporter"),
            password=os.getenv("DB_PASSWORD", "browser_reporter"),
            database=os.getenv("DB_NAME", "browser_reporter"),
        )

        # Map of (table, column) -> SQL type to ensure exist
        required_columns = [
            ("visits", "search_vector", "TSVECTOR"),
        ]

        for table, column, col_type in required_columns:
            exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = $1 AND column_name = $2",
                table, column,
            )
            if not exists:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                )
                print(f"   ✅ Added missing column {table}.{column}")

    except Exception as e:
        print(f"⚠️  Schema check skipped: {e}")
    finally:
        if conn:
            await conn.close()


# -------------------------- Secure Config -------------------------------

# Location of the generated secureconfig.json (project root by default)
SECURECONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secureconfig.json")


@app.post("/api/admin/secureconfig")
async def admin_generate_secureconfig(
    request: Request,
    plain_config: dict,
    db: AsyncSession = Depends(get_db),
):
    """Generate an encrypted *secureconfig.json* file and persist it.
    Admins only.
    """
    await require_admin(request, db)

    encrypted = encrypt_secure_config(plain_config)

    # Persist to disk so that the Windows collector can fetch it via GET /secureconfig.json
    try:
        with open(SECURECONFIG_PATH, "w", encoding="utf-8") as f:
            import json

            json.dump(encrypted, f, indent=2)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write secureconfig.json: {exc}")

    return {"success": True}


@app.get("/api/admin/secureconfig/current")
async def admin_get_current_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the current decrypted configuration for editing.
    Admins only.
    """
    await require_admin(request, db)

    if not os.path.exists(SECURECONFIG_PATH):
        # Return default config if no config exists yet
        return {
            "server_url": "http://localhost:8000",
            "sync_interval_minutes": 5,
            "max_history_age_hours": 24,
            "monitored_users_group": "",
            "monitored_users": [],
            "monitored_hours": {
                "start": "00:00",
                "end": "23:59"
            },
            "browsers": ["chrome", "edge"],
            "log_max_mb": 5,
            "log_roll_count": 3,
            "exit_password": "BRAdmin2025"
        }

    try:
        # Read and decrypt the current config
        import json
        with open(SECURECONFIG_PATH, "r", encoding="utf-8") as f:
            encrypted_data = json.load(f)
        
        # Decrypt the config to get the actual values
        decrypted_config = decrypt_secure_config(encrypted_data)
        return decrypted_config
        
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read current config: {exc}")


@app.get("/api/admin/db-stats")
async def admin_get_database_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Get comprehensive database statistics and health metrics.
    Admin only endpoint for monitoring database performance and size.
    """
    await require_admin(request, db)

    try:
        # Table sizes and record counts
        table_stats_query = text("""
            SELECT
                schemaname,
                tablename,
                pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size,
                pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) as table_size,
                pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename) - pg_relation_size(schemaname||'.'||tablename)) as index_size,
                pg_total_relation_size(schemaname||'.'||tablename) as total_bytes
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
        """)

        table_stats_result = await db.execute(table_stats_query)
        table_stats = [dict(row._mapping) for row in table_stats_result]

        # Record counts for main tables
        visits_total = await db.scalar(select(func.count()).select_from(Visit))
        users_total = await db.scalar(select(func.count()).select_from(User))

        # Check if archive table exists and get its count
        archive_exists = await db.scalar(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'visits_archive'
            );
        """))

        visits_archive_total = 0
        if archive_exists:
            visits_archive_total = await db.scalar(text("SELECT COUNT(*) FROM visits_archive"))

        # Date range for visits
        oldest_visit = await db.scalar(select(func.min(Visit.visit_time)))
        newest_visit = await db.scalar(select(func.max(Visit.visit_time)))

        # Visits in last 24 hours
        last_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        visits_24h = await db.scalar(
            select(func.count()).select_from(Visit).where(Visit.visit_time >= last_24h)
        )

        # Visits in last 7 days
        last_7d = datetime.now(timezone.utc) - timedelta(days=7)
        visits_7d = await db.scalar(
            select(func.count()).select_from(Visit).where(Visit.visit_time >= last_7d)
        )

        # Active users (visited in last 24h)
        active_users_24h = await db.scalar(
            select(func.count(func.distinct(Visit.user_id)))
            .select_from(Visit)
            .where(Visit.visit_time >= last_24h)
        )

        # Database size
        db_size_query = text("""
            SELECT pg_size_pretty(pg_database_size(current_database())) as db_size,
                   pg_database_size(current_database()) as db_bytes;
        """)
        db_size_result = await db.execute(db_size_query)
        db_size_row = db_size_result.fetchone()
        db_size = dict(db_size_row._mapping) if db_size_row else {"db_size": "Unknown", "db_bytes": 0}

        # Index usage statistics
        index_stats_query = text("""
            SELECT
                schemaname,
                relname as tablename,
                indexrelname as indexname,
                idx_scan as times_used,
                pg_size_pretty(pg_relation_size(indexrelid)) as index_size
            FROM pg_stat_user_indexes
            WHERE schemaname = 'public'
              AND indexrelname LIKE 'idx_%'
            ORDER BY idx_scan DESC
            LIMIT 20;
        """)

        index_stats_result = await db.execute(index_stats_query)
        index_stats = [dict(row._mapping) for row in index_stats_result]

        # Connection stats
        connection_stats_query = text("""
            SELECT
                count(*) as total_connections,
                count(*) FILTER (WHERE state = 'active') as active_connections,
                count(*) FILTER (WHERE state = 'idle') as idle_connections
            FROM pg_stat_activity
            WHERE datname = current_database();
        """)

        connection_stats_result = await db.execute(connection_stats_query)
        connection_stats_row = connection_stats_result.fetchone()
        connection_stats = dict(connection_stats_row._mapping) if connection_stats_row else {}

        # Slow query detection (if pg_stat_statements is enabled)
        try:
            slow_queries_query = text("""
                SELECT
                    substring(query, 1, 100) as query_preview,
                    calls,
                    mean_exec_time,
                    total_exec_time
                FROM pg_stat_statements
                WHERE mean_exec_time > 100
                ORDER BY mean_exec_time DESC
                LIMIT 10;
            """)
            slow_queries_result = await db.execute(slow_queries_query)
            slow_queries = [dict(row._mapping) for row in slow_queries_result]
        except Exception:
            slow_queries = []  # pg_stat_statements not enabled

        return {
            "database": {
                "total_size": db_size["db_size"],
                "total_bytes": db_size["db_bytes"],
                "connection_info": connection_stats,
            },
            "tables": table_stats,
            "records": {
                "users": users_total,
                "visits": visits_total,
                "visits_archive": visits_archive_total,
                "total_visits": visits_total + visits_archive_total,
            },
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
        raise HTTPException(status_code=500, detail=f"Failed to retrieve database statistics: {str(e)}")


@app.get("/secureconfig.json")
async def download_secureconfig():
    """Serve the generated *secureconfig.json*.

    No auth on purpose – the Windows collector expects to fetch it anonymously.
    You may wrap this with auth/IP restrictions if desired.
    """
    if not os.path.exists(SECURECONFIG_PATH):
        raise HTTPException(status_code=404, detail="secureconfig.json not found. Generate it first via the admin panel.")

    return FileResponse(SECURECONFIG_PATH, media_type="application/json")


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True) 