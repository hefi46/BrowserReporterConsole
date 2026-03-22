"""Report / analytics endpoints: user lists, search, per-user visits."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import expression
from sqlalchemy.ext.compiler import compiles

from ..database import get_db
from ..models import User, Visit
from ..schemas import ReportIn
from ..crud import upsert_user, bulk_insert_visits
from .deps import (
    require_login,
    validate_pagination,
    parse_days_cutoff,
    pagination_meta,
)

logger = logging.getLogger("browser_reporter")


# ── Cross-database DISTINCT string aggregation ────────────────────────────
# PostgreSQL uses string_agg(DISTINCT col, sep), SQLite uses group_concat(DISTINCT col, sep).

class _string_agg_distinct(expression.FunctionElement):
    """Cross-database aggregate: concatenate distinct values with a separator."""
    type = func.group_concat("x").type  # text return type
    inherit_cache = True

    def __init__(self, column, separator):
        super().__init__(column, separator)
        self.column = column
        self.separator = separator


@compiles(_string_agg_distinct)
def _compile_string_agg_pg(element, compiler, **kw):
    col = compiler.process(element.clauses.clauses[0], **kw)
    sep = compiler.process(element.clauses.clauses[1], **kw)
    return f"string_agg(DISTINCT {col}, {sep})"


@compiles(_string_agg_distinct, "sqlite")
def _compile_string_agg_sqlite(element, compiler, **kw):
    col = compiler.process(element.clauses.clauses[0], **kw)
    # SQLite's group_concat(DISTINCT col) doesn't accept a separator arg,
    # but defaults to comma which is close enough for tests.
    return f"group_concat(DISTINCT {col})"
router = APIRouter()

templates: Jinja2Templates = None  # type: ignore[assignment]


def _apply_homegroup_cutoff(q, homegroup: Optional[str], cutoff):
    """Apply homegroup and time-cutoff WHERE clauses to a query."""
    if homegroup:
        q = q.where(User.homegroup == homegroup)
    if cutoff:
        q = q.where(Visit.visit_time >= cutoff)
    return q


def _apply_user_search(q, search: Optional[str]):
    """Apply username/display-name/email/homegroup text search to a query."""
    if search:
        pattern = f"%{search.lower()}%"
        q = q.where(
            func.lower(User.username).like(pattern)
            | func.lower(func.coalesce(User.display_name, "")).like(pattern)
            | func.lower(func.coalesce(User.email, "")).like(pattern)
            | func.lower(func.coalesce(User.homegroup, "")).like(pattern)
        )
    return q


def configure(tpl: Jinja2Templates) -> None:
    global templates
    templates = tpl


# ── Data ingestion ───────────────────────────────────────────────────────

@router.post("/api/reports/data")
async def ingest_report(report: ReportIn, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await upsert_user(db, report.UserInfo)
    await bulk_insert_visits(db, user_id, report.Visits)
    await db.commit()
    return {"success": True}


# ── User list with aggregation ───────────────────────────────────────────

@router.get("/api/reports/all")
async def reports_all(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    homegroup: Optional[str] = Query(None, max_length=100),
    days: Optional[float] = None,
    search: Optional[str] = Query(None, max_length=500),
    db: AsyncSession = Depends(get_db),
):
    require_login(request)
    page, page_size = validate_pagination(page, page_size)
    cutoff = parse_days_cutoff(days)

    # Base columns
    # Use column.distinct() rather than func.distinct(column) so that
    # both PostgreSQL and SQLite render "COUNT(DISTINCT col)" correctly.
    selectable = (
        select(
            User.username,
            func.coalesce(User.display_name, User.username).label("display_name"),
            User.email,
            User.homegroup.label("department"),
            func.count(Visit.id).label("total_visits"),
            func.count(Visit.url.distinct()).label("unique_urls"),
            func.max(Visit.visit_time).label("last_activity"),
            _string_agg_distinct(Visit.computer_name, text("', '")).label("computers"),
        )
        .select_from(User)
        .outerjoin(Visit, Visit.user_id == User.id)
    )

    # Filters
    selectable = _apply_homegroup_cutoff(selectable, homegroup, cutoff)
    selectable = _apply_user_search(selectable, search)

    selectable = selectable.group_by(User.id).order_by(User.username)

    # Count
    count_q = (
        select(func.count(User.id.distinct()))
        .select_from(User)
        .outerjoin(Visit, Visit.user_id == User.id)
    )
    count_q = _apply_homegroup_cutoff(count_q, homegroup, cutoff)
    count_q = _apply_user_search(count_q, search)
    total_count = (await db.execute(count_q)).scalar() or 0

    # Paginate
    rows = (await db.execute(selectable.limit(page_size).offset((page - 1) * page_size))).fetchall()

    return {
        "data": [
            {
                "username": r.username,
                "displayName": r.display_name,
                "email": r.email,
                "department": r.department,
                "totalVisits": r.total_visits,
                "uniqueUrls": r.unique_urls,
                "lastActivity": r.last_activity.isoformat() if r.last_activity else None,
                "computers": r.computers,
            }
            for r in rows
        ],
        "pagination": pagination_meta(page, page_size, total_count),
    }


# ── Homegroups ───────────────────────────────────────────────────────────

@router.get("/api/meta/homegroups")
async def get_homegroups(request: Request, db: AsyncSession = Depends(get_db)):
    """Return distinct homegroups for populating the dropdown."""
    require_login(request)
    result = await db.execute(
        select(func.distinct(User.homegroup))
        .where(User.homegroup.is_not(None))
        .order_by(User.homegroup)
    )
    rows = result.scalars().all()
    return {"homegroups": [hg for hg in rows if (hg or "").strip()]}


# ── Keyword / URL search ────────────────────────────────────────────────

@router.get("/api/reports/search")
async def search_website(
    request: Request,
    url: str = Query(..., min_length=1, max_length=500, description="Search term for URL/title matching"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    days: float | None = None,
    homegroup: Optional[str] = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """Search for users who visited a specific URL or website."""
    require_login(request)
    page, page_size = validate_pagination(page, page_size)
    cutoff = parse_days_cutoff(days)
    search_term = url.strip()

    # Hybrid search: full-text + ILIKE fallback
    search_filter = (
        (
            (Visit.search_vector.isnot(None))
            & (Visit.search_vector.op("@@")(func.websearch_to_tsquery("english", search_term)))
        )
        | (Visit.url.ilike(f"%{search_term}%"))
        | (Visit.title.ilike(f"%{search_term}%"))
    )

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
                func.ts_rank(Visit.search_vector, func.websearch_to_tsquery("english", search_term)),
                0,
            ).label("rank"),
        )
        .select_from(Visit)
        .join(User, Visit.user_id == User.id)
        .where(search_filter)
        .order_by(text("rank DESC"), Visit.visit_time.desc())
    )
    query = _apply_homegroup_cutoff(query, homegroup, cutoff)

    # Count
    count_q = (
        select(func.count(Visit.id))
        .select_from(Visit)
        .join(User, Visit.user_id == User.id)
        .where(search_filter)
    )
    count_q = _apply_homegroup_cutoff(count_q, homegroup, cutoff)
    total_count = (await db.execute(count_q)).scalar() or 0

    # Unique users
    unique_q = (
        select(func.count(User.id.distinct()))
        .select_from(Visit)
        .join(User, Visit.user_id == User.id)
        .where(Visit.url.ilike(f"%{url}%"))
    )
    unique_q = _apply_homegroup_cutoff(unique_q, homegroup, cutoff)
    unique_users_count = (await db.execute(unique_q)).scalar() or 0

    # Paginate
    visits = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).fetchall()

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
                "homegroup": v.homegroup,
            }
            for v in visits
        ],
        "pagination": pagination_meta(page, page_size, total_count),
        "summary": {
            "search_term": url,
            "total_visits": total_count,
            "unique_users": unique_users_count,
            "days_filter": days,
        },
    }


# ── Single-user visit history ───────────────────────────────────────────

@router.get("/api/reports/user/{username}")
async def reports_user(
    request: Request,
    username: str = Path(..., min_length=1, max_length=255),
    days: float | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    require_login(request)
    page, page_size = validate_pagination(page, page_size)
    cutoff = parse_days_cutoff(days)

    result = await db.execute(select(User.id).where(User.username == username))
    user_id = result.scalar_one_or_none()
    if user_id is None:
        raise HTTPException(status_code=404, detail="User not found")

    base_filter = Visit.user_id == user_id
    query = select(Visit).where(base_filter)
    count_q = select(func.count(Visit.id)).where(base_filter)

    if cutoff:
        query = query.where(Visit.visit_time >= cutoff)
        count_q = count_q.where(Visit.visit_time >= cutoff)

    total_count = (await db.execute(count_q)).scalar() or 0

    visits = (
        await db.execute(
            query.order_by(Visit.visit_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

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
        "pagination": pagination_meta(page, page_size, total_count),
    }


# ── Template pages ──────────────────────────────────────────────────────

@router.get("/user/{username}", response_class=HTMLResponse)
async def user_page(request: Request, username: str = Path(..., min_length=1, max_length=255)):
    require_login(request)
    return templates.TemplateResponse("user.html", {"request": request, "username": username})
