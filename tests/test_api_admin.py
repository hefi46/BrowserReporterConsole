"""Integration tests for admin management endpoints."""
import pytest
from tests.conftest import login_as, BCRYPT_WORKS

pytestmark = pytest.mark.skipif(
    not BCRYPT_WORKS,
    reason="passlib/bcrypt incompatibility on this system — run in Docker",
)


# ── GET /api/admin/users ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_users_requires_admin(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_users_unauthenticated(client):
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 302  # redirects to login


@pytest.mark.asyncio
async def test_admin_list_users(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(u["username"] == "testadmin" for u in data)


# ── POST /api/admin/users ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_create_user(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.post("/api/admin/users", json={
        "username": "newuser",
        "password": "password123",
        "role": "user",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "newuser"
    assert data["role"] == "user"


@pytest.mark.asyncio
async def test_admin_create_duplicate_user(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Create first
    await client.post("/api/admin/users", json={
        "username": "dupeuser",
        "password": "password123",
        "role": "user",
    })
    # Try to create duplicate
    resp = await client.post("/api/admin/users", json={
        "username": "dupeuser",
        "password": "password123",
        "role": "user",
    })
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_admin_create_user_validation(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Short username
    resp = await client.post("/api/admin/users", json={
        "username": "ab",
        "password": "password123",
        "role": "user",
    })
    assert resp.status_code == 422  # validation error

    # Short password
    resp = await client.post("/api/admin/users", json={
        "username": "validuser",
        "password": "ab",
        "role": "user",
    })
    assert resp.status_code == 422

    # Invalid role
    resp = await client.post("/api/admin/users", json={
        "username": "validuser",
        "password": "password123",
        "role": "superadmin",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_regular_user_cannot_create_user(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    resp = await client.post("/api/admin/users", json={
        "username": "newuser",
        "password": "password123",
        "role": "user",
    })
    assert resp.status_code == 403


# ── PUT /api/admin/users/{username} ─────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_update_user_role(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Create a user to update
    await client.post("/api/admin/users", json={
        "username": "updateme",
        "password": "password123",
        "role": "user",
    })
    # Update role
    resp = await client.put("/api/admin/users/updateme", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_admin_cannot_demote_self(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.put(f"/api/admin/users/{admin_user['username']}", json={"role": "user"})
    assert resp.status_code == 400
    assert "own role" in resp.json()["detail"]


# ── DELETE /api/admin/users/{username} ──────────────────────────────────


@pytest.mark.asyncio
async def test_admin_delete_user(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Create a user to delete
    await client.post("/api/admin/users", json={
        "username": "deleteme",
        "password": "password123",
        "role": "user",
    })
    resp = await client.delete("/api/admin/users/deleteme")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_cannot_delete_self(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.delete(f"/api/admin/users/{admin_user['username']}")
    assert resp.status_code == 400
    assert "own account" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_admin_delete_nonexistent_user(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.delete("/api/admin/users/ghostuser")
    assert resp.status_code == 404


# ── GET /api/admin/users/example-csv ────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_example_csv(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/admin/users/example-csv")
    assert resp.status_code == 200
    assert "username,password,role" in resp.text


# ── Admin page ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_page_requires_admin(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    resp = await client.get("/admin")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_page_renders(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "Admin Panel" in resp.text
