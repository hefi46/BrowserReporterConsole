from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import insert, select, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import User, Visit, DashboardUser, DashboardRoleEnum, StudentEnrichment, PurgeSchedule
from .schemas import UserInfoIn, VisitIn
from .utils import get_password_hash

async def upsert_user(db: AsyncSession, info: UserInfoIn) -> int:
    """Upsert user and return id."""
    values = dict(
        username=info.Username.upper() if info.Username else info.Username,
        display_name=info.DisplayName or info.Username,
        first_name=info.FirstName,
        last_name=info.LastName,
        homegroup=info.Department,
        email=info.Email,
        last_seen_at=datetime.now(timezone.utc),
    )
    stmt = pg_insert(User).values(**values).on_conflict_do_update(
        index_elements=[User.username],
        set_={k: v for k, v in values.items() if k != "username"},
    ).returning(User.id)

    result = await db.execute(stmt)
    user_id = result.scalar_one()

    # Apply student enrichment if available (for Chrome extension users who only send email)
    enriched = False
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
            enriched = True

    # Fallback: if no enrichment data and no details from the agent, try AD lookup
    if not enriched and not info.FirstName and not info.LastName:
        try:
            from .ldap_auth import get_effective_ldap_config, lookup_user_details
            ldap_config = await get_effective_ldap_config(db)
            if ldap_config.get("enabled") or ldap_config.get("enrichment_enabled"):
                # Check if we already attempted enrichment for this user
                check_result = await db.execute(
                    select(User.ad_enriched_at).where(User.id == user_id)
                )
                last_attempt = check_result.scalar_one_or_none()

                if last_attempt is None:  # only attempt once per user
                    lookup_name = email.split("@")[0].upper() if "@" in email else (info.Username or "").upper()
                    if lookup_name:
                        ad_info = lookup_user_details(lookup_name, ldap_config)
                        update_vals = {"ad_enriched_at": datetime.now(timezone.utc)}
                        if ad_info:
                            if ad_info.get("first_name"):
                                update_vals["first_name"] = ad_info["first_name"]
                            if ad_info.get("last_name"):
                                update_vals["last_name"] = ad_info["last_name"]
                            if ad_info.get("department"):
                                update_vals["homegroup"] = ad_info["department"]
                            if ad_info.get("display_name"):
                                update_vals["display_name"] = ad_info["display_name"]
                            if ad_info.get("email"):
                                update_vals["email"] = ad_info["email"]
                        # Always set ad_enriched_at (even on miss) to prevent repeated lookups
                        await db.execute(
                            update(User).where(User.id == user_id).values(**update_vals)
                        )
        except Exception:
            import logging
            logging.getLogger("browser_reporter").debug(
                "AD enrichment skipped for '%s'", info.Username, exc_info=True
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
                source=v.Source,
                browser_profile=v.BrowserProfile,
            )
        )
    if rows:
        await db.execute(insert(Visit), rows)


# Admin Management CRUD Operations

async def get_dashboard_users(db: AsyncSession) -> list[DashboardUser]:
    """Get all dashboard users."""
    result = await db.execute(select(DashboardUser).order_by(DashboardUser.created_at))
    return result.scalars().all()


async def get_dashboard_user_by_username(db: AsyncSession, username: str) -> DashboardUser | None:
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
    stmt = pg_insert(StudentEnrichment).values(rows)
    update_cols = ("first_name", "last_name", "display_name", "homegroup", "imported_at")
    stmt = stmt.on_conflict_do_update(
        index_elements=[StudentEnrichment.login],
        set_={col: stmt.excluded[col] for col in update_cols},
    )
    await db.execute(stmt)
    return len(rows)


# Database Purge Operations

async def purge_visits(db: AsyncSession, retain_days: int = 0) -> int:
    """Delete visit records. If retain_days > 0, keep visits from the last N days.

    Also removes User rows that have no remaining visits after the purge.
    """
    if retain_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
        result = await db.execute(delete(Visit).where(Visit.visit_time < cutoff))
    else:
        result = await db.execute(delete(Visit))
    deleted_visits = result.rowcount

    # Remove users that now have no visits
    await db.execute(
        delete(User).where(
            ~User.id.in_(select(Visit.user_id).distinct())
        )
    )

    return deleted_visits


async def purge_all_data(db: AsyncSession) -> dict:
    """Delete all visits and users (full data reset). Dashboard users are preserved."""
    visits_deleted = (await db.execute(delete(Visit))).rowcount
    users_deleted = (await db.execute(delete(User))).rowcount
    enrichments_deleted = (await db.execute(delete(StudentEnrichment))).rowcount
    return {
        "visits_deleted": visits_deleted,
        "users_deleted": users_deleted,
        "enrichments_deleted": enrichments_deleted,
    }


async def get_purge_schedule(db: AsyncSession) -> PurgeSchedule | None:
    """Get the current purge schedule (single row)."""
    result = await db.execute(select(PurgeSchedule).limit(1))
    return result.scalar_one_or_none()


async def upsert_purge_schedule(
    db: AsyncSession,
    schedule_type: str,
    retain_days: int,
    next_purge_at: datetime | None,
    updated_by: str,
) -> PurgeSchedule:
    """Create or update the purge schedule."""
    schedule = await get_purge_schedule(db)
    if schedule:
        schedule.schedule_type = schedule_type
        schedule.retain_days = retain_days
        schedule.next_purge_at = next_purge_at
        schedule.updated_at = datetime.now(timezone.utc)
        schedule.updated_by = updated_by
    else:
        schedule = PurgeSchedule(
            schedule_type=schedule_type,
            retain_days=retain_days,
            next_purge_at=next_purge_at,
            updated_by=updated_by,
        )
        db.add(schedule)
    await db.flush()
    return schedule


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