"""Epic 903-C11 (#1249) — MCP dream mode + dream_health_check tool + error
mapping (DE2E-8 MCP surfaces; #888-consolidated surface, additive).

Pins: (1) the dream tool forwards mode/budget (SDK parity); (2) the
dream_health_check tool is registered; (3) error mapping —
BudgetExceededError → ERR_QUOTA, unknown mode ValueError → ERR_INVALID;
(4) registry additive (no collision).
"""
from __future__ import annotations

import pytest

from tortoise.tool_registry import TOOL_REGISTRY


def _tool(name: str):
    for t in TOOL_REGISTRY:
        if t.name == name:
            return t
    return None


class TestToolRegistration:
    def test_dream_health_tool_registered(self):
        t = _tool("tortoise_dream_health")
        assert t is not None, "tortoise_dream_health must be registered"
        assert t.sdk_method == "dream_health_check"
        assert t.http_policy is False  # #329-style: not tenant HTTP

    def test_dream_tool_registered_with_mode_and_budget(self):
        t = _tool("tortoise_dream")
        assert t is not None
        assert t.sdk_method == "dream"
        # The description advertises the epic-903 params (mode/budget).
        assert "stale-first" in t.description
        assert "budget" in t.description

    def test_no_registry_collision(self):
        """Additive surface: exactly one definition per name (no duplicate
        registration of the #888-consolidated tools)."""
        names = [t.name for t in TOOL_REGISTRY]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"duplicate tool registrations: {dupes}"


class TestErrorMapping:
    def test_budget_exceeded_maps_to_err_quota(self):
        """BudgetExceededError is quota-class → ERR_QUOTA (mcp _safe)."""
        import tortoise.mcp_server as ms
        from tortoise.exceptions import BudgetExceededError

        def boom():
            raise BudgetExceededError("full budget 1 unsatisfiable")

        from unittest.mock import patch
        with patch.object(ms, "_transport_mode",
                          type("TM", (), {"get": staticmethod(lambda: "stdio")})):
            with patch.object(ms, "_is_dev_mode", return_value=True):
                result = ms._safe(boom)
        assert result["code"] == ms.ERR_QUOTA
        assert "budget" in result["error"].lower()

    def test_unknown_mode_maps_to_err_invalid(self):
        """Unknown dream mode → ERR_INVALID (the tool's ValueError catch)."""
        import tortoise.mcp_server as ms
        from unittest.mock import patch

        class FakeSdk:
            def dream(self, **kw):
                raise ValueError("unknown dream mode 'quantum' — expected "
                                 "one of 'local', 'stale-first', 'full'")

        with patch.object(ms, "_get_team_sdk", return_value=FakeSdk()):
            with patch.object(ms, "_transport_mode", type("TM", (), {"get": staticmethod(lambda: "stdio")})):
                with patch.object(ms, "_is_dev_mode", return_value=True):
                    r = ms.tortoise_dream(mode="quantum")
        assert r["code"] == ms.ERR_INVALID
        assert "quantum" in r["error"]
