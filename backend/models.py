from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, BigInteger, ForeignKey
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
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    visits = relationship("Visit", back_populates="user", cascade="all, delete-orphan")


class Visit(Base):
    __tablename__ = "visits"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    computer_name = Column(String)
    url = Column(Text)
    title = Column(Text)
    visit_time = Column(DateTime(timezone=True), nullable=False)  # actual visit time
    inserted_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    user = relationship("User", back_populates="visits")


class DashboardRoleEnum(str, PyEnum):
    admin = "admin"
    user = "user"


class DashboardUser(Base):
    __tablename__ = "dashboard_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(DashboardRoleEnum), default=DashboardRoleEnum.user, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow) 