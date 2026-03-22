from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class VisitIn(BaseModel):
    Url: str
    Title: str
    VisitTime: int  # epoch milliseconds
    ComputerName: str


class UserInfoIn(BaseModel):
    Username: str
    DisplayName: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Department: Optional[str] = None
    Email: Optional[str] = None


class ReportIn(BaseModel):
    Username: str
    Visits: list[VisitIn]
    UserInfo: UserInfoIn

    @field_validator("Visits")
    @classmethod
    def validate_visits_not_empty(cls, v: list[VisitIn]) -> list[VisitIn]:
        if len(v) == 0:
            raise ValueError("Visits list cannot be empty")
        return v


# Admin Management Schemas

class DashboardUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=72)
    role: str = Field(..., pattern="^(admin|user)$")


class DashboardUserUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=6, max_length=72)
    role: Optional[str] = Field(None, pattern="^(admin|user)$")


class DashboardUserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}