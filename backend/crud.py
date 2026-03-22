from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Optional, List

from sqlalchemy import insert, select, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import User, Visit, DashboardUser, DashboardRoleEnum, StudentEnrichment
from .schemas import UserInfoIn, VisitIn
from .utils import get_password_hash

async def upsert_user(db: AsyncSession, info: UserInfoIn) -> int:
    """Upsert user and return id."""
    stmt = pg_insert(User).values(
        username=info.Username,
        display_name=info.DisplayName or info.Username,
        first_name=info.FirstName,
        last_name=info.LastName,
        homegroup=info.Department,
        email=info.Email,
        last_seen_at=datetime.now(timezone.utc),
    ).on_conflict_do_update(
        index_elements=[User.username],
        set_=dict(
            display_name=info.DisplayName or info.Username,
            first_name=info.FirstName,
            last_name=info.LastName,
            homegroup=info.Department,
            email=info.Email,
            last_seen_at=datetime.now(timezone.utc),
        ),
    ).returning(User.id)

    result = await db.execute(stmt)
    user_id = result.scalar_one()

    # Apply student enrichment if available (for Chrome extension users who only send email)
    email = info.Email or info.Username or ""
    if "@schools.vic.edu.au" in email:
        login = email.split("@")[0].upper()
        enrichment_result = await db.execute(
            select(StudentEnrichment).where(StudentEnrichment.login == login)
        )
        enrichment = enrichment_result.scalar_one_or_none()
        if enrichment:
            await db.execute(
                update(User).where(User.id == user_id).values(
                    first_name=enrichment.first_name,
                    last_name=enrichment.last_name,
                    display_name=enrichment.display_name,
                    homegroup=enrichment.homegroup,
                )
            )

    return user_id


_MIN_TIMESTAMP_MS = 0                    # 1970-01-01
_MAX_TIMESTAMP_MS = 4_102_444_800_000     # 2100-01-01


async def bulk_insert_visits(db: AsyncSession, user_id: int, visits: Sequence[VisitIn]):
    rows = []
    for v in visits:
        # Guard against out-of-range timestamps that would crash fromtimestamp()
        if not (_MIN_TIMESTAMP_MS <= v.VisitTime <= _MAX_TIMESTAMP_MS):
            continue
        rows.append(
            dict(
                user_id=user_id,
                computer_name=v.ComputerName,
                url=v.Url,
                title=v.Title,
                visit_time=datetime.fromtimestamp(v.VisitTime / 1000.0, tz=timezone.utc),
            )
        )
    if rows:
        await db.execute(insert(Visit), rows)


# Admin Management CRUD Operations

async def get_dashboard_users(db: AsyncSession) -> List[DashboardUser]:
    """Get all dashboard users."""
    result = await db.execute(select(DashboardUser).order_by(DashboardUser.created_at))
    return result.scalars().all()


async def get_dashboard_user_by_username(db: AsyncSession, username: str) -> Optional[DashboardUser]:
    """Get dashboard user by username."""
    result = await db.execute(select(DashboardUser).where(DashboardUser.username == username))
    return result.scalar_one_or_none()


async def create_dashboard_user(db: AsyncSession, username: str, password: str, role: DashboardRoleEnum) -> DashboardUser:
    """Create a new dashboard user."""
    dashboard_user = DashboardUser(
        username=username,
        password_hash=get_password_hash(password),
        role=role
    )
    db.add(dashboard_user)
    await db.flush()
    await db.refresh(dashboard_user)
    return dashboard_user


async def update_dashboard_user_password(db: AsyncSession, username: str, new_password: str) -> bool:
    """Update dashboard user password."""
    stmt = update(DashboardUser).where(DashboardUser.username == username).values(
        password_hash=get_password_hash(new_password)
    )
    result = await db.execute(stmt)
    return result.rowcount > 0


async def update_dashboard_user_role(db: AsyncSession, username: str, role: DashboardRoleEnum) -> bool:
    """Update dashboard user role."""
    stmt = update(DashboardUser).where(DashboardUser.username == username).values(role=role)
    result = await db.execute(stmt)
    return result.rowcount > 0


async def delete_dashboard_user(db: AsyncSession, username: str) -> bool:
    """Delete dashboard user."""
    stmt = delete(DashboardUser).where(DashboardUser.username == username)
    result = await db.execute(stmt)
    return result.rowcount > 0


# Student Enrichment Operations

async def upsert_student_enrichments(db: AsyncSession, rows: list) -> int:
    """Upsert student enrichment records. Returns count of rows imported."""
    if not rows:
        return 0
    stmt = pg_insert(StudentEnrichment).values(rows).on_conflict_do_update(
        index_elements=[StudentEnrichment.login],
        set_=dict(
            first_name=pg_insert(StudentEnrichment).excluded.first_name,
            last_name=pg_insert(StudentEnrichment).excluded.last_name,
            display_name=pg_insert(StudentEnrichment).excluded.display_name,
            homegroup=pg_insert(StudentEnrichment).excluded.homegroup,
            imported_at=pg_insert(StudentEnrichment).excluded.imported_at,
        ),
    )
    await db.execute(stmt)
    return len(rows)


async def apply_enrichment_to_existing_users(db: AsyncSession) -> int:
    """Bulk-update existing users with enrichment data. Returns count of updated rows."""
    result = await db.execute(text("""
        UPDATE users u
        SET
            first_name   = se.first_name,
            last_name    = se.last_name,
            display_name = se.display_name,
            homegroup    = se.homegroup
        FROM student_enrichments se
        WHERE UPPER(SPLIT_PART(u.email, '@', 1)) = se.login
           OR UPPER(SPLIT_PART(u.username, '@', 1)) = se.login
    """))
    return result.rowcount 