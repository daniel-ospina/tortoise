"""Epic 903-C11 (#1249) — MCP dream mode + dream_health_check tool + error
mapping (DE2E-8 MCP surfaces; #888-consolidated surface, additive).

Pins: (1) the dream tool forwards mode/budget (SDK parity); (2) the
dream_health_check tool is registered; (3) error mapping —
BudgetExceededError → ERR_QUOTA, unknown mode ValueError → ERR_INVALID;
(4) registry additive (no collision).
"""
from __future__ import annotations

import pytest  # noqa: F401

from tortoise.tool_registry import TOOL_REGISTRY


def _tool(name: str):
    for t in TOOL_REGISTRY:
        if t.name == name:
            return t
    return None


class TestToolRegistration:
    def test_dream_health_tool_registered(self):
        # #4282: the door retired into `graph_overview(section="dream_health")`. The
        # #329-style exclusion is NOT on the registry any more — it lives in the
        # handler (`tortoise_dream_health` returns `_http_excluded_error()` when
        # `_transport_mode == "http"`), and the container's arm calls that handler, so
        # the exclusion still holds on the advertised path. Asserted both ways.
        from tortoise.tool_registry import RETIRED_USE_INSTEAD
        t = _tool("tortoise_graph_overview")
        assert t is not None, "graph_overview must be registered"
        assert 'section="dream_health"' in RETIRED_USE_INSTEAD["tortoise_dream_health"]
        import tortoise.mcp_server as ms
        prev = ms._transport_mode.get()
        try:
            ms._transport_mode.set("http")
            refused = ms.tortoise_graph_overview(section="dream_health")
            assert isinstance(refused, dict) and "error" in refused, (
                "dream_health must stay excluded from tenant HTTP through the container")
        finally:
            ms._transport_mode.set(prev)

    def test_dream_tool_registered_with_mode_and_budget(self):
        """Epic 903's params survive the #4282 Phase 2 consolidation.

        `tortoise_dream` retired into the `tortoise_refresh_confidence`
        container (scope='dirty'|'full'), so the LIVE row is the container and
        the epic-903 params (mode/budget) travel on the RETIRED row — which is
        the row the #3883 shim still serves to a legacy caller. Both are pinned,
        on the artifact a caller actually reaches.
        """
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY

        t = _tool("tortoise_refresh_confidence")
        assert t is not None, "the dream container must be registered"
        # A per-SCOPE dispatcher declares no single binding (#4337/#3904).
        assert t.sdk_method == ""
        for scope in ("dirty", "full"):
            assert scope in t.description, (
                f"the container's description does not advertise scope={scope!r}")

        retired = next(x for x in RETIRED_TOOL_REGISTRY
                       if x.name == "tortoise_dream")
        assert retired.retired_use_instead.startswith(
            "tortoise_refresh_confidence"), retired.retired_use_instead
        # The description advertises the epic-903 params (mode/budget).
        assert "stale-first" in retired.description
        assert "budget" in retired.description

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
        with patch.object(ms, "_transport_mode",  # noqa: SIM117
                          type("TM", (), {"get": staticmethod(lambda: "stdio")})):
            with patch.object(ms, "_is_dev_mode", return_value=True):
                result = ms._safe(boom)
        assert result["code"] == ms.ERR_QUOTA
        assert "budget" in result["error"].lower()

    def test_unknown_mode_maps_to_err_invalid(self):
        """Unknown dream mode → ERR_INVALID (the tool's ValueError catch)."""
        import tortoise.mcp_server as ms  # noqa: I001
        from unittest.mock import patch

        class FakeSdk:
            def dream(self, **kw):
                raise ValueError("unknown dream mode 'quantum' — expected "
                                 "one of 'local', 'stale-first', 'full'")

        with patch.object(ms, "_get_org_sdk", return_value=FakeSdk()):  # noqa: SIM117
            with patch.object(ms, "_transport_mode", type("TM", (), {"get": staticmethod(lambda: "stdio")})):
                with patch.object(ms, "_is_dev_mode", return_value=True):
                    r = ms.tortoise_dream(mode="quantum")
        assert r["code"] == ms.ERR_INVALID
        assert "quantum" in r["error"]
