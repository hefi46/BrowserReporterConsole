"""Integration tests for authentication endpoints."""
import pytest
from tests.conftest import login_as, BCRYPT_WORKS

pytestmark = pytest.mark.skipif(
    not BCRYPT_WORKS,
    reason="passlib/bcrypt incompatibility on this system — run in Docker",
)


# ── GET /login ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_page_renders(client):
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Browser Reporter" in resp.text


# ── POST /login ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_success(client, admin_user):
    resp = await client.post(
        "/login",
        data={"username": admin_user["username"], "password": admin_user["password"]},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/"


@pytest.mark.asyncio
async def test_login_wrong_password(client, admin_user):
    resp = await client.post(
        "/login",
        data={"username": admin_user["username"], "password": "wrongpassword"},
        follow_redirects=False,
    )
    # Should re-render the login page (200) with an error, not redirect
    assert resp.status_code == 200
    assert "Invalid credentials" in resp.text


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    resp = await client.post(
        "/login",
        data={"username": "nobody", "password": "nopass"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Invalid credentials" in resp.text


# ── GET /api/auth/user ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_user_unauthenticated(client):
    resp = await client.get("/api/auth/user")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_user_authenticated(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/auth/user")
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "testadmin"
    assert data["role"] == "admin"


# ── GET /logout ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logout_clears_session(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Verify we're logged in
    resp = await client.get("/api/auth/user")
    assert resp.status_code == 200

    # Logout
    resp = await client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302

    # Should now be unauthenticated
    resp = await client.get("/api/auth/user")
    assert resp.status_code == 401


# ── Protected page redirects ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_redirects_to_login_when_unauthenticated(client):
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_root_redirects_to_dashboard_when_authenticated(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/dashboard" in resp.headers.get("location", "")
