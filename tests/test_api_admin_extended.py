"""Tests for admin endpoints not covered by test_api_admin.py:

- POST /api/admin/users/bulk-import
- POST /api/admin/secureconfig
- GET  /api/admin/secureconfig/current
- POST /create-admin-emergency
- GET  /client-config
- GET  /dashboard.html  (page route in main.py)
- GET  /secureconfig.json (public file route in main.py)

Endpoints skipped (PostgreSQL-only):
- GET  /api/admin/db-stats  — uses pg_stat_*, pg_total_relation_size, etc.
- POST /api/admin/enrich-students — uses pg_insert ON CONFLICT + SPLIT_PART
"""
import io
import json
import os
import pytest
from unittest.mock import patch

from tests.conftest import login_as, BCRYPT_WORKS

pytestmark = pytest.mark.skipif(
    not BCRYPT_WORKS,
    reason="passlib/bcrypt incompatibility on this system — run in Docker",
)


# ── POST /api/admin/users/bulk-import ─────────────────────────────────────


def _csv_file(content: str, filename: str = "users.csv"):
    """Build an upload-ready tuple for httpx multipart."""
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


@pytest.mark.asyncio
async def test_bulk_import_success(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    csv = "username,password,role\nbulk1,password123,user\nbulk2,password456,admin\n"
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(csv))
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["created_users"]) == 2
    assert data["created_users"][0]["username"] == "bulk1"
    assert data["created_users"][1]["role"] == "admin"
    assert len(data["errors"]) == 0


@pytest.mark.asyncio
async def test_bulk_import_requires_admin(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    csv = "username,password,role\nfoo,password123,user\n"
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(csv))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bulk_import_rejects_non_csv(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    files = {"file": ("users.txt", io.BytesIO(b"data"), "text/plain")}
    resp = await client.post("/api/admin/users/bulk-import", files=files)
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_import_missing_headers(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    csv = "name,pass\nfoo,bar\n"
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(csv))
    assert resp.status_code == 400
    assert "headers" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_import_validation_errors(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    csv = (
        "username,password,role\n"
        "ab,password123,user\n"          # username too short
        "valid,ab,user\n"                # password too short
        "valid2,password123,superadmin\n" # invalid role
        ",password123,user\n"            # empty username
    )
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(csv))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created_users"]) == 0
    assert len(data["errors"]) == 4


@pytest.mark.asyncio
async def test_bulk_import_duplicate_in_csv(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    csv = "username,password,role\ndupuser,password123,user\ndupuser,password456,admin\n"
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(csv))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created_users"]) == 1  # first one succeeds
    assert any("Duplicate" in e for e in data["errors"])


@pytest.mark.asyncio
async def test_bulk_import_existing_user_skipped(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Create a user first
    await client.post("/api/admin/users", json={
        "username": "preexist",
        "password": "password123",
        "role": "user",
    })
    # Try to import same username via CSV
    csv = "username,password,role\npreexist,password123,user\nnewone,password123,user\n"
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(csv))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created_users"]) == 1
    assert data["created_users"][0]["username"] == "newone"
    assert any("already exists" in e for e in data["errors"])


@pytest.mark.asyncio
async def test_bulk_import_size_limit(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Create a CSV larger than 5 MB
    header = "username,password,role\n"
    row = "user_xxxxx,password123456,user\n"
    huge_csv = header + row * (5 * 1024 * 1024 // len(row) + 1)
    resp = await client.post("/api/admin/users/bulk-import", files=_csv_file(huge_csv))
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"]


# ── POST /api/admin/secureconfig ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_secureconfig_generate_and_read(client, admin_user, tmp_path):
    """Round-trip: generate encrypted config, then read it back decrypted."""
    await login_as(client, admin_user["username"], admin_user["password"])

    fake_path = str(tmp_path / "secureconfig.json")
    plain = {"server_url": "http://test:8000", "exit_password": "secret"}

    with patch("backend.routers.admin.SECURECONFIG_PATH", fake_path):
        # Generate
        resp = await client.post("/api/admin/secureconfig", json=plain)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify file was written
        assert os.path.exists(fake_path)
        with open(fake_path) as f:
            encrypted = json.load(f)
        assert "encrypted_data" in encrypted
        assert "iv" in encrypted

        # Read back
        resp2 = await client.get("/api/admin/secureconfig/current")
        assert resp2.status_code == 200
        decrypted = resp2.json()
        assert decrypted["server_url"] == "http://test:8000"
        assert decrypted["exit_password"] == "secret"


@pytest.mark.asyncio
async def test_secureconfig_current_returns_defaults_when_no_file(client, admin_user, tmp_path):
    await login_as(client, admin_user["username"], admin_user["password"])
    fake_path = str(tmp_path / "nonexistent.json")

    with patch("backend.routers.admin.SECURECONFIG_PATH", fake_path):
        resp = await client.get("/api/admin/secureconfig/current")
        assert resp.status_code == 200
        data = resp.json()
        # Should return hardcoded defaults
        assert data["sync_interval_minutes"] == 5
        assert "chrome" in data["browsers"]
        assert data["exit_password"] == "BRAdmin2025"


@pytest.mark.asyncio
async def test_secureconfig_requires_admin(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    resp = await client.post("/api/admin/secureconfig", json={"key": "val"})
    assert resp.status_code == 403

    resp2 = await client.get("/api/admin/secureconfig/current")
    assert resp2.status_code == 403


# ── POST /create-admin-emergency ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_emergency_admin_creates_user(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.post("/create-admin-emergency")
    assert resp.status_code == 200
    # "admin" user doesn't exist yet (fixture user is "testadmin"), so it should create
    assert resp.json()["message"] == "Admin created successfully"


@pytest.mark.asyncio
async def test_emergency_admin_idempotent(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    # Create once
    await client.post("/create-admin-emergency")
    # Create again — should report already exists
    resp = await client.post("/create-admin-emergency")
    assert resp.status_code == 200
    assert resp.json()["message"] == "Admin already exists"


@pytest.mark.asyncio
async def test_emergency_admin_requires_admin(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    resp = await client.post("/create-admin-emergency")
    assert resp.status_code == 403


# ── GET /client-config ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_config_page_renders(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/client-config")
    assert resp.status_code == 200
    assert "Client Config" in resp.text or "Configuration" in resp.text


@pytest.mark.asyncio
async def test_client_config_requires_admin(client, regular_user):
    await login_as(client, regular_user["username"], regular_user["password"])
    resp = await client.get("/client-config")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_client_config_unauthenticated(client):
    resp = await client.get("/client-config")
    assert resp.status_code == 302


# ── GET /dashboard.html ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_page_renders(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/dashboard.html")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_unauthenticated(client):
    resp = await client.get("/dashboard.html")
    assert resp.status_code == 302


# ── GET /secureconfig.json ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_secureconfig_json_not_found(client, tmp_path):
    """Returns 404 when no secureconfig.json has been generated."""
    fake_path = str(tmp_path / "nonexistent.json")
    with patch("backend.main.SECURECONFIG_PATH", fake_path):
        resp = await client.get("/secureconfig.json")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_secureconfig_json_served(client, admin_user, tmp_path):
    """Serves the file when it exists (no auth required)."""
    await login_as(client, admin_user["username"], admin_user["password"])
    fake_path = str(tmp_path / "secureconfig.json")

    with patch("backend.routers.admin.SECURECONFIG_PATH", fake_path):
        # Generate a config first
        resp = await client.post("/api/admin/secureconfig", json={"test": True})
        assert resp.status_code == 200

    with patch("backend.main.SECURECONFIG_PATH", fake_path):
        # Fetch without auth — should work (no auth on purpose)
        resp2 = await client.get("/secureconfig.json")
        assert resp2.status_code == 200
        data = resp2.json()
        assert "encrypted_data" in data
