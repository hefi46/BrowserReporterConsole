"""BrowserReporterConsole — FastAPI application entry-point.

All route handlers live in backend/routers/.  This file is responsible only
for creating the app, wiring middleware, and running startup tasks.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from .database import engine, Base, AsyncSessionLocal
from .models import DashboardUser, DashboardRoleEnum
from .crud import get_purge_schedule, purge_visits, purge_all_data
from .utils import get_password_hash
from .migrations.runner import get_db_config, ensure_schema_columns, apply_migrations
from .routers import auth, reports, admin
from .routers.deps import require_login

# ── Logging ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("browser_reporter")

# ── Startup ──────────────────────────────────────────────────────────────

async def _create_initial_admin() -> None:
    """Ensure the default admin account exists."""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(DashboardUser).where(DashboardUser.username == "admin")
            )
            if not result.scalar_one_or_none():
                session.add(DashboardUser(
                    username="admin",
                    password_hash=get_password_hash("admin"),
                    role=DashboardRoleEnum.admin,
                ))
                await session.commit()
                logger.warning("Created default admin: admin / admin — change the password immediately")
            else:
                logger.info("Admin user already exists")
        except Exception:
            logger.exception("Admin setup warning")
            try:
                await session.rollback()
            except Exception:
                pass


async def _purge_scheduler():
    """Background task that checks hourly if a scheduled purge is due."""
    while True:
        await asyncio.sleep(3600)  # Check every hour
        try:
            async with AsyncSessionLocal() as db:
                schedule = await get_purge_schedule(db)
                if not schedule or schedule.schedule_type == "disabled" or not schedule.next_purge_at:
                    continue
                if datetime.now(timezone.utc) < schedule.next_purge_at:
                    continue

                # Purge is due
                deleted = await purge_visits(db, retain_days=schedule.retain_days)
                logger.info("Scheduled clear executed: %d visits deleted (retain=%d days, type=%s)",
                            deleted, schedule.retain_days, schedule.schedule_type)

                # Calculate next purge date
                from .routers.admin import _calculate_next_purge
                schedule.last_purge_at = datetime.now(timezone.utc)
                schedule.next_purge_at = _calculate_next_purge(schedule.schedule_type)
                await db.commit()
        except Exception:
            logger.exception("Clear data scheduler error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Create any brand-new tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Schema fixups + migrations via a raw asyncpg connection
    db_config = get_db_config()
    conn = None
    try:
        conn = await asyncpg.connect(**db_config)
        await ensure_schema_columns(conn)

        migrations_dir = Path(__file__).parent / "migrations"
        if migrations_dir.exists():
            applied, skipped = await apply_migrations(conn, migrations_dir)
            if applied:
                logger.info("Applied %d new migration(s)", applied)
            if skipped:
                logger.info("Skipped %d already-applied migration(s)", skipped)
    except asyncpg.exceptions.InvalidCatalogNameError:
        logger.warning("Database does not exist yet — will be created by SQLAlchemy")
    except asyncpg.exceptions.PostgresConnectionError:
        logger.warning("Could not connect to database for migrations — skipping")
    except Exception:
        logger.exception("Migration error — app will continue but performance may be degraded")
    finally:
        if conn:
            await conn.close()

    # 3. Ensure default admin exists
    await _create_initial_admin()

    # 4. Start background purge scheduler
    purge_task = asyncio.create_task(_purge_scheduler())

    yield

    purge_task.cancel()
    try:
        await purge_task
    except asyncio.CancelledError:
        pass


# ── App creation ─────────────────────────────────────────────────────────

SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))

app = FastAPI(title="Browser Reporter Server", lifespan=lifespan)


# ── Security headers middleware ────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # CSP: allow self + CDN resources used by Bootstrap / FontAwesome.
        # 'unsafe-inline' is needed for script-src because templates use
        # inline <script> blocks.  A future refactor could extract those to
        # external .js files and remove 'unsafe-inline'.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS – restrict to same-origin by default; override via CORS_ORIGINS env var
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("HTTPS_ONLY", "false").lower() == "true",
    max_age=3600,
)

# Static files & templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

SECURECONFIG_PATH = os.path.join(BASE_DIR, "secureconfig.json")


# ── Jinja2 template helpers ──────────────────────────────────────────────

def _is_admin_user(request: Request) -> bool:
    """Check if the current session user has admin role.

    Designed to be called from Jinja2 templates as ``is_admin(request)``.
    Uses a synchronous DB query via the existing session data — only checks
    the session value, not the DB, so it's lightweight.
    """
    return request.session.get("dashboard_role") == "admin"


templates.env.globals["is_admin"] = _is_admin_user


# ── Wire routers ─────────────────────────────────────────────────────────

auth.configure(templates)
reports.configure(templates)
admin.configure(templates, SECURECONFIG_PATH)

app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(admin.router)


# ── Global exception handler ────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return a consistent JSON 500 response
    instead of leaking stack traces to the client.

    FastAPI's built-in handlers for HTTPException and RequestValidationError
    fire first, so this only catches truly unexpected errors.
    """
    # Let FastAPI handle its own exceptions natively
    if isinstance(exc, (HTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Thin page routes that don't fit neatly in a single router ────────────

@app.get("/dashboard.html", response_class=HTMLResponse)
async def dashboard_bootstrap(request: Request):
    require_login(request)
    return templates.TemplateResponse("dashboard2.html", {"request": request})


@app.get("/dashboard2", response_class=HTMLResponse)
async def dashboard2(request: Request):
    require_login(request)
    return RedirectResponse(url="/dashboard.html")


@app.get("/keyword-search", response_class=HTMLResponse)
async def keyword_search_page(request: Request):
    require_login(request)
    return templates.TemplateResponse("keyword_search.html", {"request": request})


@app.get("/")
async def root_redirect(request: Request):
    require_login(request)
    return RedirectResponse(url="/dashboard.html")


@app.get("/secureconfig.json")
async def download_secureconfig():
    """Serve the generated secureconfig.json.

    No auth on purpose — the Windows collector expects to fetch it
    anonymously.  Wrap with auth/IP restrictions if desired.
    """
    if not os.path.exists(SECURECONFIG_PATH):
        raise HTTPException(status_code=404, detail="secureconfig.json not found. Generate it first via the admin panel.")
    return FileResponse(SECURECONFIG_PATH, media_type="application/json")


# ── CLI entry-point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
