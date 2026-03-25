from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import relationship
from enum import Enum as PyEnum

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    display_name = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    homegroup = Column(String, index=True)  # mapped from Department
    email = Column(String)
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    visits = relationship("Visit", back_populates="user", cascade="all, delete-orphan")


class Visit(Base):
    __tablename__ = "visits"

    id = Column(BigInteger, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    computer_name = Column(String)
    url = Column(Text)
    title = Column(Text)
    visit_time = Column(DateTime(timezone=True), nullable=False)  # actual visit time
    inserted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source = Column(String, nullable=True)  # "chrome_extension", "windows_agent", etc.
    browser_profile = Column(String, nullable=True)  # "Default", "Profile 1", etc.
    search_vector = Column(TSVECTOR)

    user = relationship("User", back_populates="visits")


class StudentEnrichment(Base):
    __tablename__ = "student_enrichments"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, nullable=False, index=True)  # e.g. "TJAMA2"
    first_name = Column(String)
    last_name = Column(String)
    display_name = Column(String)
    homegroup = Column(String)
    imported_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DashboardRoleEnum(str, PyEnum):
    admin = "admin"
    user = "user"


class PurgeSchedule(Base):
    __tablename__ = "purge_schedule"

    id = Column(Integer, primary_key=True, index=True)
    schedule_type = Column(String, default="disabled")  # disabled, weekly, monthly, term, yearly
    retain_days = Column(Integer, default=0)  # 0 = purge all visits, >0 = keep recent N days
    last_purge_at = Column(DateTime(timezone=True))
    next_purge_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_by = Column(String)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DashboardUser(Base):
    __tablename__ = "dashboard_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(DashboardRoleEnum), default=DashboardRoleEnum.user, nullable=False)
    auth_source = Column(String, default="local", nullable=False, server_default="local")  # "local" or "ldap"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)) 