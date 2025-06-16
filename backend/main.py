from __future__ import annotations

import os
import secrets
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional, List

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
    delete_dashboard_user, verify_password, get_password_hash
)
from .models import DashboardUser, DashboardRoleEnum, User, Visit

import uvicorn

API_KEY = os.getenv("API_KEY", "your-secure-api-key-here")
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))

app = FastAPI(title="Browser Reporter Server")

# CORS (optional - you can restrict origins within LAN)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session middleware for login state
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

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
            # Don't fail startup if admin creation fails
            pass


# --------------------------- API Endpoints ------------------------------

@app.post("/api/reports/data")
async def ingest_report(report: ReportIn, request: Request, db: AsyncSession = Depends(get_db)):
    # Check API key
    api_key = request.headers.get("X-API-Key")
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

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
    result = await db.execute(select(DashboardUser).where(DashboardUser.username == username))
    user: Optional[DashboardUser] = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

    request.session["dashboard_user"] = username
    response = RedirectResponse(url="/", status_code=302)
    return response


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
async def reports_all(db: AsyncSession = Depends(get_db), request: Request = None):
    # Ensure logged in
    if request is not None:
        require_login(request)

    # Use SQLAlchemy query instead of raw SQL
    query = (
        select(
            User.username,
            func.coalesce(User.display_name, User.username).label("display_name"),
            User.email,
            User.homegroup.label("department"),
            func.count(Visit.id).label("total_visits"),
            func.count(func.distinct(Visit.url)).label("unique_urls"),
            func.max(Visit.visit_time).label("last_activity"),
            func.string_agg(func.distinct(Visit.computer_name), text("', '")).label("computers")
        )
        .select_from(User)
        .outerjoin(Visit, Visit.user_id == User.id)
        .group_by(User.id)
    )
    
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
    return data


@app.get("/api/reports/user/{username}")
async def reports_user(username: str, request: Request, days: int | None = None, db: AsyncSession = Depends(get_db)):
    require_login(request)
    # Get user id
    result = await db.execute(select(User.id).where(User.username == username))
    user_id_row = result.scalar_one_or_none()
    if user_id_row is None:
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user_id_row

    query = select(Visit).where(Visit.user_id == user_id)
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(Visit.visit_time >= cutoff)
    query = query.order_by(Visit.visit_time.desc())

    result = await db.execute(query)
    visits = result.scalars().all()

    return [
        {
            "timestamp": v.visit_time.isoformat(),
            "title": v.title,
            "url": v.url,
            "computerName": v.computer_name,
        }
        for v in visits
    ]


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
        
        # Process each row
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
                if len(password) < 6 or len(password) > 100:
                    errors.append(f"Row {row_num}: Password must be 6-100 characters")
                    continue
                
                # Validate role
                if role not in ['admin', 'user']:
                    errors.append(f"Row {row_num}: Role must be 'admin' or 'user'")
                    continue
                
                # Check if username already exists
                existing = await get_dashboard_user_by_username(db, username)
                if existing:
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


# Serve Bootstrap dashboard -------------------------------------------

@app.get("/dashboard.html", response_class=HTMLResponse)
async def dashboard_bootstrap(request: Request):
    require_login(request)
    return templates.TemplateResponse("dashboard.html", {"request": request})

# Redirect root to dashboard
@app.get("/")
async def root_redirect(request: Request):
    require_login(request)
    return RedirectResponse(url="/dashboard.html")


# -------------------------- Startup Events -----------------------------

@app.on_event("startup")
async def on_startup():
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # ensure initial admin exists
    await create_initial_admin()


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True) 