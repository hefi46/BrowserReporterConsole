"""Authentication routes: login, logout, current-user."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..ldap_auth import authenticate_ldap, ldap_enabled, get_effective_ldap_config
from ..models import DashboardUser, DashboardRoleEnum
from ..utils import verify_password, get_password_hash
from .deps import get_current_dashboard_user

logger = logging.getLogger("browser_reporter")
router = APIRouter()

# Templates are set from main.py via configure()
templates: Jinja2Templates = None  # type: ignore[assignment]


def configure(tpl: Jinja2Templates) -> None:
    global templates
    templates = tpl


# ── Helpers ──────────────────────────────────────────────────────────────

async def _provision_ldap_user(
    db: AsyncSession, ldap_info: dict
) -> DashboardUser:
    """Create or update a DashboardUser from LDAP attributes."""
    result = await db.execute(
        select(DashboardUser).where(DashboardUser.username == ldap_info["username"])
    )
    user = result.scalar_one_or_none()

    role = DashboardRoleEnum(ldap_info.get("role", "user"))

    if user is None:
        # First login — auto-provision
        user = DashboardUser(
            username=ldap_info["username"],
            password_hash=get_password_hash("!ldap-managed-no-local-login!"),
            role=role,
            auth_source="ldap",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        logger.info("Auto-provisioned LDAP user '%s' (role=%s)", user.username, role.value)
    else:
        # Update auth_source if switching to LDAP and sync role from AD group
        if user.auth_source != "ldap":
            user.auth_source = "ldap"
        if ldap_info.get("role"):
            user.role = role
        await db.flush()

    return user


# ── HTML pages ───────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    ldap_cfg = await get_effective_ldap_config(db)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "ldap_enabled": ldap_cfg.get("enabled", False),
    })


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        user: DashboardUser | None = None

        # Load the effective LDAP config (DB first, then env vars)
        ldap_cfg = await get_effective_ldap_config(db)
        ldap_is_enabled = ldap_cfg.get("enabled", False)

        # ── Try LDAP first (when enabled) ────────────────────────────
        if ldap_is_enabled:
            ldap_info = authenticate_ldap(username, password, config=ldap_cfg)
            if ldap_info:
                user = await _provision_ldap_user(db, ldap_info)
                await db.commit()

        # ── Fall back to local auth ──────────────────────────────────
        if user is None:
            result = await db.execute(
                select(DashboardUser).where(DashboardUser.username == username)
            )
            local_user = result.scalar_one_or_none()
            # Block local password login for LDAP-managed accounts
            if local_user and local_user.auth_source == "ldap":
                return templates.TemplateResponse(
                    "login.html", {
                        "request": request,
                        "error": "This account uses LDAP authentication. Please use your domain password.",
                        "ldap_enabled": ldap_is_enabled,
                    }
                )
            if local_user and verify_password(password, local_user.password_hash):
                user = local_user

        if not user:
            return templates.TemplateResponse(
                "login.html", {
                    "request": request,
                    "error": "Invalid credentials",
                    "ldap_enabled": ldap_is_enabled,
                }
            )

        request.session["dashboard_user"] = user.username
        request.session["dashboard_role"] = user.role.value
        return RedirectResponse(url="/", status_code=302)
    except Exception:
        logger.exception("Login error")
        return templates.TemplateResponse(
            "login.html", {
                "request": request,
                "error": "Invalid credentials",
                "ldap_enabled": False,
            }
        )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ── JSON API ─────────────────────────────────────────────────────────────

@router.get("/api/auth/user")
async def get_auth_user(request: Request, db: AsyncSession = Depends(get_db)):
    username = get_current_dashboard_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = await db.execute(
        select(DashboardUser).where(DashboardUser.username == username)
    )
    user: DashboardUser | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401)
    return {
        "username": user.username,
        "displayName": user.username,
        "role": user.role.value,
    }


@router.post("/api/auth/logout")
async def api_logout(request: Request):
    request.session.clear()
    return {"success": True}
