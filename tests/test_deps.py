"""Tests for backend.routers.deps — shared helper functions."""
from datetime import datetime, timezone

from backend.routers.deps import (
    validate_pagination,
    parse_days_cutoff,
    pagination_meta,
)


# ── validate_pagination ─────────────────────────────────────────────────


def test_pagination_defaults():
    page, size = validate_pagination(1, 50)
    assert page == 1
    assert size == 50


def test_pagination_clamps_negative_page():
    page, size = validate_pagination(-1, 50)
    assert page == 1


def test_pagination_clamps_zero_page():
    page, size = validate_pagination(0, 50)
    assert page == 1


def test_pagination_clamps_oversized_page_size():
    page, size = validate_pagination(1, 5000)
    assert size == 50  # falls back to default


def test_pagination_clamps_zero_page_size():
    page, size = validate_pagination(1, 0)
    assert size == 50


def test_pagination_clamps_negative_page_size():
    page, size = validate_pagination(1, -10)
    assert size == 50


def test_pagination_accepts_max_size():
    page, size = validate_pagination(1, 1000)
    assert size == 1000


# ── parse_days_cutoff ───────────────────────────────────────────────────


def test_days_cutoff_none():
    assert parse_days_cutoff(None) is None


def test_days_cutoff_7_days():
    cutoff = parse_days_cutoff(7.0)
    assert cutoff is not None
    now = datetime.now(timezone.utc)
    delta = now - cutoff
    # Should be approximately 7 days (within a few seconds)
    assert 6.99 < delta.total_seconds() / 86400 < 7.01


def test_days_cutoff_fractional():
    cutoff = parse_days_cutoff(0.5)
    assert cutoff is not None
    now = datetime.now(timezone.utc)
    delta = now - cutoff
    # 0.5 days = 12 hours
    assert 11.9 < delta.total_seconds() / 3600 < 12.1


# ── pagination_meta ─────────────────────────────────────────────────────


def test_pagination_meta_first_page():
    meta = pagination_meta(1, 25, 100)
    assert meta["page"] == 1
    assert meta["page_size"] == 25
    assert meta["total_count"] == 100
    assert meta["total_pages"] == 4
    assert meta["has_next"] is True
    assert meta["has_prev"] is False


def test_pagination_meta_last_page():
    meta = pagination_meta(4, 25, 100)
    assert meta["has_next"] is False
    assert meta["has_prev"] is True


def test_pagination_meta_single_page():
    meta = pagination_meta(1, 50, 10)
    assert meta["total_pages"] == 1
    assert meta["has_next"] is False
    assert meta["has_prev"] is False


def test_pagination_meta_empty():
    meta = pagination_meta(1, 50, 0)
    assert meta["total_pages"] == 0
    assert meta["has_next"] is False
    assert meta["has_prev"] is False


def test_pagination_meta_partial_last_page():
    meta = pagination_meta(1, 25, 26)
    assert meta["total_pages"] == 2
    assert meta["has_next"] is True
