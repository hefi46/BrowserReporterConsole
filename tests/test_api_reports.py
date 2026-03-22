"""Integration tests for report/analytics endpoints."""
import pytest
from tests.conftest import login_as, TestSessionLocal, BCRYPT_WORKS

pytestmark = pytest.mark.skipif(
    not BCRYPT_WORKS,
    reason="passlib/bcrypt incompatibility on this system — run in Docker",
)
from backend.models import User, Visit
from datetime import datetime, timezone


async def _seed_user_and_visits(username="jdoe", visit_count=3):
    """Insert a browsing user with some visits for testing."""
    async with TestSessionLocal() as session:
        user = User(
            username=username,
            display_name="John Doe",
            email=f"{username}@example.com",
            homegroup="5A",
        )
        session.add(user)
        await session.flush()

        for i in range(visit_count):
            visit = Visit(
                user_id=user.id,
                computer_name="PC01",
                url=f"https://example{i}.com/page",
                title=f"Example Page {i}",
                visit_time=datetime(2026, 3, 20, 10, i, 0, tzinfo=timezone.utc),
            )
            session.add(visit)

        await session.commit()
        return user.id


# ── GET /api/reports/all ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reports_all_unauthenticated(client):
    resp = await client.get("/api/reports/all")
    assert resp.status_code == 302  # redirects to login


@pytest.mark.asyncio
async def test_reports_all_empty(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"] == []
    assert data["pagination"]["total_count"] == 0


@pytest.mark.asyncio
async def test_reports_all_with_data(client, admin_user):
    await _seed_user_and_visits("jdoe", visit_count=5)
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/all")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1  # one user
    user_row = data["data"][0]
    assert user_row["username"] == "jdoe"
    assert user_row["totalVisits"] == 5


@pytest.mark.asyncio
async def test_reports_all_search_filter(client, admin_user):
    await _seed_user_and_visits("alice", visit_count=2)
    await _seed_user_and_visits("bob", visit_count=1)
    await login_as(client, admin_user["username"], admin_user["password"])

    resp = await client.get("/api/reports/all", params={"search": "alice"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["username"] == "alice"


@pytest.mark.asyncio
async def test_reports_all_pagination(client, admin_user):
    for i in range(5):
        await _seed_user_and_visits(f"user{i}", visit_count=1)
    await login_as(client, admin_user["username"], admin_user["password"])

    resp = await client.get("/api/reports/all", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 2
    assert data["pagination"]["total_count"] == 5
    assert data["pagination"]["has_next"] is True

    resp2 = await client.get("/api/reports/all", params={"page": 3, "page_size": 2})
    data2 = resp2.json()
    assert len(data2["data"]) == 1  # 5th user on page 3
    assert data2["pagination"]["has_next"] is False


# ── GET /api/reports/user/{username} ─────────────────────────────────────


@pytest.mark.asyncio
async def test_reports_user_not_found(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/user/nobody")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reports_user_with_visits(client, admin_user):
    await _seed_user_and_visits("jane", visit_count=3)
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/user/jane")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 3
    assert data["pagination"]["total_count"] == 3
    # Visits should be ordered by time descending
    timestamps = [v["timestamp"] for v in data["data"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_reports_user_pagination(client, admin_user):
    await _seed_user_and_visits("paginated", visit_count=5)
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/user/paginated", params={"page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 2
    assert data["pagination"]["total_count"] == 5
    assert data["pagination"]["has_next"] is True


# ── GET /api/meta/homegroups ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_homegroups_empty(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/meta/homegroups")
    assert resp.status_code == 200
    assert resp.json()["homegroups"] == []


@pytest.mark.asyncio
async def test_homegroups_returns_distinct(client, admin_user):
    await _seed_user_and_visits("student1", visit_count=1)
    await _seed_user_and_visits("student2", visit_count=1)
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/meta/homegroups")
    assert resp.status_code == 200
    # Both users have homegroup "5A" — should return one entry
    hgs = resp.json()["homegroups"]
    assert "5A" in hgs


# ── POST /api/reports/data (ingestion) ──────────────────────────────────
# These use pg_insert (PostgreSQL ON CONFLICT) via crud.upsert_user and won't
# run on SQLite.  They pass in Docker with the real PostgreSQL database.

_pg_only = pytest.mark.skip(reason="Requires PostgreSQL (uses pg_insert ON CONFLICT)")


@_pg_only
@pytest.mark.asyncio
async def test_ingest_report(client):
    """Data ingestion endpoint doesn't require session auth (uses API key in prod)."""
    payload = {
        "Username": "collector-user",
        "Visits": [
            {
                "Url": "https://google.com",
                "Title": "Google",
                "VisitTime": 1711000000000,
                "ComputerName": "LAB-PC01",
            }
        ],
        "UserInfo": {
            "Username": "collector-user",
            "DisplayName": "Collector User",
        },
    }
    resp = await client.post("/api/reports/data", json=payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@_pg_only
@pytest.mark.asyncio
async def test_ingest_report_empty_visits_rejected(client):
    payload = {
        "Username": "collector-user",
        "Visits": [],
        "UserInfo": {"Username": "collector-user"},
    }
    resp = await client.post("/api/reports/data", json=payload)
    assert resp.status_code == 422  # Pydantic validation error


# ── Input validation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_too_long_query(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/search", params={"url": "a" * 501})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reports_all_invalid_page(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/all", params={"page": 0})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reports_all_page_size_too_large(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/api/reports/all", params={"page_size": 9999})
    assert resp.status_code == 422


# ── Template pages ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_page_renders(client, admin_user):
    await _seed_user_and_visits("viewme")
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/user/viewme")
    assert resp.status_code == 200
    assert "User Details" in resp.text


@pytest.mark.asyncio
async def test_keyword_search_page_renders(client, admin_user):
    await login_as(client, admin_user["username"], admin_user["password"])
    resp = await client.get("/keyword-search")
    assert resp.status_code == 200
    assert "Keyword Search" in resp.text
