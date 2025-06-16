from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Optional, List

from sqlalchemy import insert, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from passlib.context import CryptContext

from .models import User, Visit, DashboardUser, DashboardRoleEnum
from .schemas import ReportIn, UserInfoIn, VisitIn

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

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
    return user_id


async def bulk_insert_visits(db: AsyncSession, user_id: int, visits: Sequence[VisitIn]):
    rows = []
    for v in visits:
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