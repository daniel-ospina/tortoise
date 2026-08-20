"""Tests for deployment — health check."""
from __future__ import annotations  # noqa: I001

import pytest  # noqa: F401
from tortoise.deployment import health


def test_health_no_db_returns_ok():
    """health() without a DB returns ok uptime."""
    result = health()
    assert result["status"] == "ok"
    assert "uptime" in result
    assert result["uptime"] > 0


def test_health_with_error():
    """health() with a broken DB returns error."""
    class BrokenDB:
        def select_graph(self, name):
            raise RuntimeError("connection refused")

    result = health(db=BrokenDB())
    assert result["status"] == "error"
    assert "connection refused" in result["error"]
