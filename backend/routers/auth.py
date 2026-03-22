"""Authentication routes: login, logout, current-user."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import DashboardUser
from ..utils import verify_password
from .deps import get_current_dashboard_user

logger = logging.getLogger("browser_reporter")
router = APIRouter()

# Templates are set from main.py via configure()
templates: Jinja2Templates = None  # type: ignore[assignment]


def configure(tpl: Jinja2Templates) -> None:
    global templates
    templates = tpl


# ── HTML pages ───────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(DashboardUser).where(DashboardUser.username == username)
        )
        user: Optional[DashboardUser] = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "Invalid credentials"}
            )

        request.session["dashboard_user"] = username
        request.session["dashboard_role"] = user.role.value
        return RedirectResponse(url="/", status_code=302)
    except Exception:
        logger.exception("Login error")
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid credentials"}
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
