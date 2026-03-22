"""Tests for backend.schemas — Pydantic model validation."""
import pytest
from pydantic import ValidationError

from backend.schemas import (
    VisitIn,
    UserInfoIn,
    ReportIn,
    DashboardUserCreate,
    DashboardUserUpdate,
    DashboardUserResponse,
)


# ── VisitIn ──────────────────────────────────────────────────────────────


def test_visit_in_valid():
    v = VisitIn(Url="https://example.com", Title="Example", VisitTime=1700000000000, ComputerName="PC01")
    assert v.Url == "https://example.com"
    assert v.VisitTime == 1700000000000


def test_visit_in_missing_field():
    with pytest.raises(ValidationError):
        VisitIn(Url="https://example.com", Title="Example", VisitTime=1700000000000)


# ── UserInfoIn ───────────────────────────────────────────────────────────


def test_user_info_minimal():
    u = UserInfoIn(Username="jdoe")
    assert u.Username == "jdoe"
    assert u.DisplayName is None
    assert u.Email is None


def test_user_info_full():
    u = UserInfoIn(
        Username="jdoe",
        DisplayName="John Doe",
        FirstName="John",
        LastName="Doe",
        Department="5A",
        Email="jdoe@example.com",
    )
    assert u.Department == "5A"


# ── ReportIn ─────────────────────────────────────────────────────────────


def test_report_in_valid():
    r = ReportIn(
        Username="jdoe",
        Visits=[
            VisitIn(Url="https://a.com", Title="A", VisitTime=1700000000000, ComputerName="PC01"),
        ],
        UserInfo=UserInfoIn(Username="jdoe"),
    )
    assert len(r.Visits) == 1


def test_report_in_empty_visits_rejected():
    with pytest.raises(ValidationError, match="Visits list cannot be empty"):
        ReportIn(
            Username="jdoe",
            Visits=[],
            UserInfo=UserInfoIn(Username="jdoe"),
        )


# ── DashboardUserCreate ─────────────────────────────────────────────────


def test_dashboard_user_create_valid():
    u = DashboardUserCreate(username="admin", password="secret123", role="admin")
    assert u.role == "admin"


def test_dashboard_user_create_short_username():
    with pytest.raises(ValidationError):
        DashboardUserCreate(username="ab", password="secret123", role="admin")


def test_dashboard_user_create_short_password():
    with pytest.raises(ValidationError):
        DashboardUserCreate(username="admin", password="short", role="admin")


def test_dashboard_user_create_invalid_role():
    with pytest.raises(ValidationError):
        DashboardUserCreate(username="admin", password="secret123", role="superadmin")


def test_dashboard_user_create_long_username():
    with pytest.raises(ValidationError):
        DashboardUserCreate(username="a" * 51, password="secret123", role="admin")


# ── DashboardUserUpdate ─────────────────────────────────────────────────


def test_dashboard_user_update_password_only():
    u = DashboardUserUpdate(password="newpass123")
    assert u.password == "newpass123"
    assert u.role is None


def test_dashboard_user_update_role_only():
    u = DashboardUserUpdate(role="user")
    assert u.role == "user"
    assert u.password is None


def test_dashboard_user_update_empty_is_valid():
    u = DashboardUserUpdate()
    assert u.password is None
    assert u.role is None


def test_dashboard_user_update_short_password():
    with pytest.raises(ValidationError):
        DashboardUserUpdate(password="ab")
