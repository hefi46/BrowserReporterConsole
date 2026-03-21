"""Shared dependencies and helpers used across routers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DashboardUser, DashboardRoleEnum

logger = logging.getLogger("browser_reporter")


def get_current_dashboard_user(request: Request) -> Optional[str]:
    """Return the logged-in dashboard username, or None."""
    return request.session.get("dashboard_user")


def require_login(request: Request) -> str:
    """Raise 302 redirect to /login if not authenticated."""
    username = get_current_dashboard_user(request)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
        )
    return username


async def require_admin(request: Request, db: AsyncSession) -> DashboardUser:
    """Require admin role and return the admin DashboardUser."""
    username = require_login(request)
    result = await db.execute(
        select(DashboardUser).where(DashboardUser.username == username)
    )
    user: Optional[DashboardUser] = result.scalar_one_or_none()
    if not user or user.role != DashboardRoleEnum.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def validate_pagination(page: int, page_size: int) -> tuple[int, int]:
    """Clamp pagination parameters to safe ranges."""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 1000:
        page_size = 50
    return page, page_size


def parse_days_cutoff(days: Optional[float]) -> Optional[datetime]:
    """Convert a days-ago value to a UTC cutoff datetime, or None."""
    if days is None:
        return None
    try:
        return datetime.now(timezone.utc) - timedelta(days=float(days))
    except (ValueError, TypeError):
        return None


def pagination_meta(page: int, page_size: int, total_count: int) -> dict:
    """Build a standard pagination envelope."""
    total_pages = (total_count + page_size - 1) // page_size
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }
