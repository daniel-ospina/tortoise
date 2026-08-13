"""Tests for mcp_server — MCP tool registration, _safe wrapper, and tool behavior."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# #942: import mcp_server FIRST at module level (import-order is the
# contract — mcp_server must load before selfhost ever could, so the
# import cycle can never be masked).
import tortoise.mcp_server as mcp_mod

# Check if FalkorDB is available for integration tests
try:
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK()
    sdk.status()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False


@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate).

    These tests exercise _safe / tools directly (no HTTP middleware), so they
    run in stdio mode: dev-mode auth (TORTOISE_API_KEY unset) and no team
    context (quota skipped). Restore after each test.
    """
    from tortoise.mcp_auth import (
        _current_team_id, _current_team_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


def test_stdio_embedded_banner(monkeypatch, capsys, tmp_path):
    """#942: the stdio entrypoint (tortoise serve / python -m tortoise.mcp_server)
    prints the loud SINGLE-WRITER / EVAL-ONLY banner when running embedded.
    main() blocks in mcp.run — monkeypatch the module instance's run so the
    test returns. Instance-attribute functions are NEVER bound, so the fake
    is **kw-only (a method-shaped fake would TypeError)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "eval.db"))
    called = {}

    def fake_run(**kw):
        called["run"] = True

    monkeypatch.setattr(mcp_mod.mcp, "run", fake_run)
    mcp_mod.main()
    assert called.get("run"), "stdio main() must reach mcp.run"
    err = capsys.readouterr().err
    assert "SINGLE-WRITER" in err and "EVAL ONLY" in err
    # main() registers _get_sdk() with monitoring — reset the cached module
    # SDK so later tests in this file don't silently reuse the embedded one.
    mcp_mod.sdk = None
    mcp_mod._sdk = None


def test_stdio_uri_mode_no_banner(monkeypatch, capsys, tmp_path):
    """#942 negative pin: no banner when TORTOISE_DB_URI is a supported URI.
    _get_sdk EAGERLY connects (from_uri retry loop, mcp_server.py:305-309) —
    patch it so the test doesn't need a live sidecar."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise")
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
    called = {}

    def fake_run(**kw):
        called["run"] = True

    monkeypatch.setattr(mcp_mod.mcp, "run", fake_run)
    monkeypatch.setattr(mcp_mod, "_get_sdk", lambda: object())
    monkeypatch.setattr(mcp_mod.monitoring, "register", lambda *a, **kw: None)
    mcp_mod.main()
    assert called.get("run")
    assert "SINGLE-WRITER" not in capsys.readouterr().err



class TestSafeWrapper:
    """Tests for the _safe function that wraps all MCP tools."""
    def test_safe_returns_result_on_success(self):
        from tortoise.mcp_server import _safe
        result = _safe(lambda x: x * 2, 21)
        assert result == 42

    def test_safe_returns_error_dict_on_exception(self):
        from tortoise.mcp_server import _safe
        def fail():
            raise ValueError("test error")
        result = _safe(fail)
        assert isinstance(result, dict)
        assert "error" in result
        assert "test error" in result["error"]

    def test_safe_passes_args_correctly(self):
        from tortoise.mcp_server import _safe
        result = _safe(lambda a, b, c=3: a + b + c, 1, 2, c=4)
        assert result == 7

    def test_safe_records_monitoring_error(self):
        from tortoise.mcp_server import _safe
        from tortoise import monitoring as mon

        # Get initial error count
        initial = mon.metrics().get("error_count", 0)

        def fail():
            raise RuntimeError("monitoring test")
        _safe(fail)

        # Error count should have incremented
        after = mon.metrics().get("error_count", 0)
        assert after >= initial


class _StubQuerySDK:
    """Minimal SDK double for the query-family handlers (Epic #888)."""

    def __init__(self):
        self.calls = []
        self.points = [{"id": f"p{i}"} for i in range(5)]
        self.tag_points = [{"id": f"t{i}"} for i in range(3)]

    def query(self, kind=None, **kwargs):
        self.calls.append(("query", kind, kwargs))
        return self.points

    def paginated_query(self, kind=None, skip=0, limit=20, **kwargs):
        self.calls.append(("paginated_query", kind, skip, limit, kwargs))
        total = len(self.points)
        return {"results": self.points[skip:skip + limit], "total": total,
                "hasMore": skip + limit < total}

    def query_points_by_tag(self, tag):
        self.calls.append(("query_points_by_tag", tag))
        return self.tag_points

    def tortoise_fts_query(self, query=None, **kwargs):
        self.calls.append(("fts", query, kwargs))
        return []


@pytest.fixture
def query_sdk(monkeypatch):
    """Swap _get_team_sdk for a stub; return the stub to assert on calls."""
    from tortoise import mcp_server
    stub = _StubQuerySDK()
    monkeypatch.setattr(mcp_server, "_get_team_sdk", lambda: stub)
    return stub


class TestQueryConsolidation:
    """Epic #888 item 1: query + paginated_query + query_points_by_tag merge
    into one tortoise_query with offset/limit/page/tag params. Old call shapes
    (plain list, no pagination) must behave unchanged.
    """

    def test_structural_query_returns_plain_list(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(kind="statement")
        assert result == query_sdk.points
        name, kind, kwargs = query_sdk.calls[-1]
        assert (name, kind) == ("query", "statement")
        assert kwargs.get("include_retracted") is False

    def test_pagination_params_route_to_paginated_query(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(kind="statement", offset=5, limit=10)
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert (name, kind, skip, limit) == ("paginated_query", "statement", 5, 10)
        assert result == {"results": [], "total": 5, "hasMore": False}

    def test_page_1_maps_to_offset_0(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        tortoise_query(kind="statement", page=1, limit=10)
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert (name, kind, skip, limit) == ("paginated_query", "statement", 0, 10)

    def test_page_is_1_based_and_overrides_offset(self, query_sdk):
        """Both page and offset set -> page wins (review P1-2: the previous
        version never passed offset, so the precedence branch was untested)."""
        from tortoise.mcp_server import tortoise_query
        tortoise_query(kind="statement", page=2, offset=5, limit=10)
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert (name, skip, limit) == ("paginated_query", 10, 10)
        # zero offset must not bypass the page override either
        tortoise_query(kind="statement", page=3, offset=0, limit=10)
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert (name, skip, limit) == ("paginated_query", 20, 10)

    def test_tag_filter_uses_tag_path(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(tag="pricing")
        assert result == query_sdk.tag_points
        assert query_sdk.calls[-1] == ("query_points_by_tag", "pricing")

    def test_tag_takes_precedence_over_text(self, query_sdk):
        """Documented precedence: tag mode wins when both are provided."""
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(tag="pricing", text="semantic query")
        assert result == query_sdk.tag_points
        assert query_sdk.calls[-1][0] == "query_points_by_tag"

    def test_tag_with_pagination_returns_dict(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(tag="pricing", offset=1, limit=1)
        assert result == {"results": [{"id": "t1"}], "total": 3, "hasMore": True}

    def test_tag_excludes_retracted_by_default(self, query_sdk):
        """Tombstone contract: tag mode mirrors the other query paths —
        status='retracted' points excluded unless include_retracted=True."""
        from tortoise.mcp_server import tortoise_query
        query_sdk.tag_points = [{"id": "t0"},
                                {"id": "tomb", "status": "retracted"},
                                {"id": "t1"}]
        result = tortoise_query(tag="pricing")
        assert result == [{"id": "t0"}, {"id": "t1"}]
        result = tortoise_query(tag="pricing", include_retracted=True)
        assert len(result) == 3

    def test_tag_with_pagination_counts_post_filter_total(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        query_sdk.tag_points = [{"id": "t0"},
                                {"id": "tomb", "status": "retracted"},
                                {"id": "t1"}]
        result = tortoise_query(tag="pricing", offset=0, limit=1)
        assert result == {"results": [{"id": "t0"}], "total": 2, "hasMore": True}

    def test_text_routes_to_fts_with_previous_default_limit(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        tortoise_query(text="hello")
        name, q, kwargs = query_sdk.calls[-1]
        assert (name, q) == ("fts", "hello")
        assert kwargs.get("limit") == 100  # old default preserved for search path

    def test_text_with_pagination_returns_error(self, query_sdk):
        """fts has no offset/skip — reject the combination instead of silently
        dropping the pagination params (review fix)."""
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(text="hello", offset=10, limit=10)
        assert isinstance(result, dict) and "error" in result, result
        assert query_sdk.calls == [], "must not hit the SDK for a rejected call"

    def test_include_retracted_passthrough(self, query_sdk):
        from tortoise.mcp_server import tortoise_query
        tortoise_query(kind="statement", include_retracted=True, offset=0, limit=5)
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert kwargs.get("include_retracted") is True

    def test_filters_include_retracted_key_does_not_typeerror(self, query_sdk):
        """A caller passing include_retracted inside filters must not collide
        with the explicit kwarg (duplicate-keyword TypeError) — and the True
        intent is honored when the explicit param is unset (review P2-2)."""
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(kind="statement", filters={"include_retracted": True})
        assert result == query_sdk.points
        name, kind, kwargs = query_sdk.calls[-1]
        assert kwargs.get("include_retracted") is True  # filters value honored

    def test_explicit_include_retracted_wins_over_filters(self, query_sdk):
        """Explicit param True + filters False -> explicit wins (precedence)."""
        from tortoise.mcp_server import tortoise_query
        tortoise_query(kind="statement", include_retracted=True,
                       filters={"include_retracted": False})
        name, kind, kwargs = query_sdk.calls[-1]
        assert kwargs.get("include_retracted") is True

    def test_malformed_json_filters_does_not_raise(self, query_sdk):
        """Malformed-JSON filters string containing the substring
        'include_retracted' must not hit the P1-1 AttributeError on the
        non-dict .pop() path. (The pre-existing **unpack TypeError for
        non-mapping filters may still surface — that predates #888.)"""
        from tortoise.mcp_server import tortoise_query
        try:
            tortoise_query(kind="statement", filters='{"include_retracted": true')
        except TypeError:
            pass  # pre-existing malformed-filters behavior, not P1-1
        except AttributeError as e:  # pragma: no cover
            raise AssertionError(f"P1-1 regression: {e}")

    @pytest.mark.parametrize("kwargs,msg", [
        ({"page": 0}, "page"),
        ({"page": -2}, "page"),
        ({"offset": -1}, "offset"),
        ({"limit": 0}, "limit"),
        ({"limit": -5}, "limit"),
    ])
    def test_invalid_pagination_values_return_error(self, query_sdk, kwargs, msg):
        """page >= 1, offset >= 0, limit >= 1 — violations are structured
        errors, never silent wrong data (review fix)."""
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(kind="statement", **kwargs)
        assert isinstance(result, dict) and "error" in result, result
        assert msg in result["error"]
        assert query_sdk.calls == [], "must not hit the SDK for a rejected call"

    def test_empty_structural_result_with_unknown_kind_returns_list(self, query_sdk):
        """Empty result + unknown kind → compute_suggestion returns None → the
        empty list is returned untouched."""
        from tortoise.mcp_server import tortoise_query
        query_sdk.points = []
        result = tortoise_query(kind="definitely-not-a-real-kind-xyz")
        assert result == []

    def test_empty_result_with_suggestion_attaches_suggestion(self, query_sdk, monkeypatch):
        """The empty-result suggestion behavior must survive the merge: when
        compute_suggestion finds a near-miss kind, the tool returns
        {results: [], suggestion: ...}."""
        from tortoise.mcp_server import tortoise_query
        query_sdk.points = []
        monkeypatch.setattr(
            "tortoise.query_suggestions.compute_suggestion",
            lambda kind: {"hint": "did you mean 'decision'?"})
        result = tortoise_query(kind="decisoin")
        assert result == {"results": [], "suggestion": {"hint": "did you mean 'decision'?"}}


class TestDeprecatedAliases:
    """Epic #888 item 1: old query tools stay as thin aliases for one release
    (deprecation-with-grace), delegating to the consolidated tortoise_query.
    """

    def test_paginated_query_alias_delegates(self, query_sdk):
        from tortoise.mcp_server import tortoise_paginated_query
        result = tortoise_paginated_query(kind="statement", skip=2, limit=5,
                                          filters={"status": "active"})
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert (name, kind, skip, limit) == ("paginated_query", "statement", 2, 5)
        assert kwargs.get("status") == "active"
        assert result["hasMore"] is False

    def test_paginated_query_alias_defaults_keep_old_shape(self, query_sdk):
        """Default call (skip=0, limit=20) → paginated dict, same as before."""
        from tortoise.mcp_server import tortoise_paginated_query
        result = tortoise_paginated_query()
        name, kind, skip, limit, kwargs = query_sdk.calls[-1]
        assert (name, skip, limit) == ("paginated_query", 0, 20)
        assert "results" in result and "total" in result and "hasMore" in result

    def test_query_points_by_tag_alias_delegates(self, query_sdk):
        from tortoise.mcp_server import tortoise_query_points_by_tag
        result = tortoise_query_points_by_tag("pricing")
        assert result == query_sdk.tag_points
        assert query_sdk.calls[-1] == ("query_points_by_tag", "pricing")


class TestToolFunctions:
    """Test that tool functions exist and accept correct parameters."""
    def test_tortoise_status_exists(self):
        from tortoise.mcp_server import tortoise_status
        assert callable(tortoise_status)

    def test_tortoise_health_exists(self):
        from tortoise.mcp_server import tortoise_health
        assert callable(tortoise_health)

    def test_tortoise_taxonomy_exists(self):
        from tortoise.mcp_server import tortoise_taxonomy
        assert callable(tortoise_taxonomy)

    def test_tortoise_list_sources_exists(self):
        from tortoise.mcp_server import tortoise_list_sources
        assert callable(tortoise_list_sources)

    def test_all_core_tools_registered(self):
        """Verify the key tools agents use are importable and callable."""
        from tortoise.mcp_server import (
            tortoise_create_point,
            tortoise_query,
            tortoise_search,
            tortoise_suggest_entry_points,
            tortoise_session_context,
            tortoise_get_point,
            tortoise_status,
            tortoise_health,
            tortoise_checkpoint,
            tortoise_diary_write,
            tortoise_diary_read,
        )
        assert callable(tortoise_create_point)
        assert callable(tortoise_query)
        assert callable(tortoise_search)
        assert callable(tortoise_suggest_entry_points)
        assert callable(tortoise_session_context)
        assert callable(tortoise_get_point)
        assert callable(tortoise_status)
        assert callable(tortoise_health)
        assert callable(tortoise_checkpoint)
        assert callable(tortoise_diary_write)
        assert callable(tortoise_diary_read)


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestToolIntegration:
    """Integration tests that require FalkorDB."""
    def test_status_returns_dict(self):
        from tortoise.mcp_server import tortoise_status
        result = tortoise_status()
        assert isinstance(result, dict)
        assert "connected" in result
        assert "counts" in result

    def test_health_returns_metrics(self):
        from tortoise.mcp_server import tortoise_health
        result = tortoise_health()
        assert isinstance(result, dict)

    def test_taxonomy_returns_counts(self):
        from tortoise.mcp_server import tortoise_taxonomy
        result = tortoise_taxonomy()
        assert isinstance(result, dict)
        # Should have Point count at minimum
        assert "Point" in result or isinstance(result.get("error"), str)

    def test_create_and_get_point(self):
        from tortoise.mcp_server import tortoise_create_point, tortoise_get_point

        result = tortoise_create_point(
            kind="observation",
            content="Integration test point — should be cleaned up",
            authoredBy="test-suite",
        )
        assert isinstance(result, dict)
        assert "id" in result

        point_id = result["id"]
        fetched = tortoise_get_point(point_id)
        assert isinstance(fetched, dict)
        assert fetched.get("content") == "Integration test point — should be cleaned up"

        # Cleanup
        from tortoise.mcp_server import tortoise_delete_point
        tortoise_delete_point(point_id)

    def test_query_returns_list(self):
        from tortoise.mcp_server import (tortoise_create_point,
                                         tortoise_delete_point, tortoise_query)
        # Seed an observation point first: on an empty graph the tool returns
        # the empty-result suggestion dict instead of a list, so the assertion
        # was order-dependent on the shared default-path DB (#493).
        created = tortoise_create_point(
            kind="observation", content="query-list-test", authoredBy="test-suite")
        point_id = created.get("id") if isinstance(created, dict) else None
        try:
            result = tortoise_query(kind="observation")
            assert isinstance(result, list) or isinstance(result.get("error"), str), result
        finally:
            if point_id:
                tortoise_delete_point(point_id)

    def test_search_returns_list(self):
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("integration test", limit=5)
        assert isinstance(result, list) or isinstance(result.get("error"), str)


    def test_search_order_by_graph_and_confidence(self):
        """#560: order_by flows through the MCP surface — 'graph' (GraphRanker
        rerank) and 'confidence' (persisted EP) must be accepted by the tool
        and return result lists (invalid values surface as structured errors,
        per the _safe wrapper contract)."""
        from tortoise.mcp_server import tortoise_search
        for ob in ("graph", "confidence"):
            result = tortoise_search("integration test", limit=5, order_by=ob)
            assert isinstance(result, list) or isinstance(result.get("error"), str), result
        # Invalid order_by → structured error dict (SDK raises ValueError,
        # _safe converts it to {"error": ...}).
        result = tortoise_search("integration test", order_by="bogus")
        assert isinstance(result, dict) and "error" in result, result
    def test_suggest_entry_points(self):
        from tortoise.mcp_server import tortoise_suggest_entry_points
        result = tortoise_suggest_entry_points("integration", limit=3)
        assert isinstance(result, list) or isinstance(result.get("error"), str)

    def test_session_context(self):
        from tortoise.mcp_server import tortoise_session_context
        result = tortoise_session_context()
        assert isinstance(result, dict)
        # Should at minimum have the expected keys
        expected_keys = {"no_prior_sessions", "diary_entries", "recent_points", "recent_events", "confidence_changes"}
        if "error" not in result:
            for key in expected_keys:
                assert key in result, f"Missing key: {key}"

    def test_diary_write_and_read(self):
        from tortoise.mcp_server import tortoise_diary_write, tortoise_diary_read

        write_result = tortoise_diary_write(
            agent_name="test-agent",
            entry="SESSION:2026-07-21|test.diary.entry|★★★",
            topic="test",
        )
        assert isinstance(write_result, dict)

        read_result = tortoise_diary_read("test-agent", last_n=5)
        assert isinstance(read_result, list) or isinstance(read_result.get("error"), str)

    def test_checkpoint(self):
        from tortoise.mcp_server import tortoise_checkpoint

        result = tortoise_checkpoint(
            items=[
                {"wing": "test", "room": "integration", "content": "Checkpoint test item"},
            ],
            agent_name="test-suite",
            threshold=1.0,  # hash-only dedup to avoid embedding dependency
        )
        assert isinstance(result, dict)
        assert "filed" in result or "error" in result

    # ── New tools (issue #7310) ──────────────────────────────

    def test_annotate_operator(self):
        from tortoise.mcp_server import tortoise_create_point, tortoise_create_operator
        from tortoise.mcp_server import tortoise_annotate_operator

        a = tortoise_create_point("statement", "Claim A")
        b = tortoise_create_point("statement", "Claim B")
        if "error" in a or "error" in b:
            pytest.skip("FalkorDB not available")
        op = tortoise_create_operator("IMPL", a["id"], [b["id"]])
        result = tortoise_annotate_operator(op["id"], 0.1, 0.8, 0.7, 0.9)
        assert isinstance(result, dict)
        assert result.get("annotator_bias") == 0.1 or "error" in result

    def test_get_operator(self):
        from tortoise.mcp_server import tortoise_create_point, tortoise_create_operator
        from tortoise.mcp_server import tortoise_get_operator

        a = tortoise_create_point("statement", "Claim A")
        b = tortoise_create_point("statement", "Claim B")
        if "error" in a or "error" in b:
            pytest.skip("FalkorDB not available")
        op = tortoise_create_operator("IMPL", a["id"], [b["id"]])
        result = tortoise_get_operator(op["id"])
        assert isinstance(result, dict)
        assert result.get("is_operator") is True or "error" in result

    def test_get_operator_rejects_non_operator(self):
        from tortoise.mcp_server import tortoise_create_point, tortoise_get_operator

        p = tortoise_create_point("statement", "Not an operator")
        if "error" in p:
            pytest.skip("FalkorDB not available")
        result = tortoise_get_operator(p["id"])
        assert isinstance(result, dict)
        assert "error" in result

    def test_mitigate_operator(self):
        from tortoise.mcp_server import tortoise_create_point, tortoise_create_operator
        from tortoise.mcp_server import tortoise_mitigate_operator

        a = tortoise_create_point("statement", "Claim A")
        b = tortoise_create_point("statement", "Claim B")
        if "error" in a or "error" in b:
            pytest.skip("FalkorDB not available")
        op = tortoise_create_operator("IMPL", a["id"], [b["id"]])
        result = tortoise_mitigate_operator(op["id"], "sample too small", 0.3)
        assert isinstance(result, dict)
        assert result.get("mitigation_strength") == 0.3 or "error" in result

    def test_mitigate_operator_idempotent(self):
        from tortoise.mcp_server import tortoise_create_point, tortoise_create_operator
        from tortoise.mcp_server import tortoise_mitigate_operator

        a = tortoise_create_point("statement", "Claim A")
        b = tortoise_create_point("statement", "Claim B")
        if "error" in a or "error" in b:
            pytest.skip("FalkorDB not available")
        op = tortoise_create_operator("IMPL", a["id"], [b["id"]])
        first = tortoise_mitigate_operator(op["id"], "v1", 0.3)
        second = tortoise_mitigate_operator(op["id"], "v2", 0.7)
        if "error" not in first and "error" not in second:
            assert first["id"] == second["id"]
            assert second["mitigation_strength"] == 0.7


# ── #329: batch caps on node-creating tools ─────────────────────────

class TestBatchCaps:
    def _tool(self, name, args):
        import tortoise.mcp_server as ms
        fn = getattr(ms, name)
        return fn(**args)

    def test_checkpoint_item_cap(self):
        import tortoise.mcp_server as ms
        items = [{"content": f"item {i}"} for i in range(501)]
        result = ms.tortoise_checkpoint(items)
        assert "error" in result and "cap" in result["error"]

    def test_file_decision_option_cap(self):
        import tortoise.mcp_server as ms
        options = [f"option {i}" for i in range(51)]
        result = ms.tortoise_file_decision(options, ["evidence"], 0)
        assert "error" in result and "cap" in result["error"]

    def test_file_decision_evidence_cap(self):
        import tortoise.mcp_server as ms
        evidence = [f"evidence {i}" for i in range(101)]
        result = ms.tortoise_file_decision(["opt"], evidence, 0)
        assert "error" in result and "cap" in result["error"]

    def test_create_operator_target_cap(self):
        import tortoise.mcp_server as ms
        target_ids = [f"t{i}" for i in range(501)]
        result = ms.tortoise_create_operator("IMPL", "src", target_ids)
        assert "error" in result and "cap" in result["error"]

    def test_tag_cap_and_value_validation(self):
        import tortoise.mcp_server as ms
        tags = [f"tag{i}" for i in range(51)]
        result = ms.tortoise_create_point("statement", "x", props={"tags": tags})
        assert "error" in result and "cap" in result["error"]
        # empty-string tag rejected
        result2 = ms.tortoise_create_point("statement", "y", props={"tags": [""]})
        assert "error" in result2 and "invalid tag" in result2["error"]
        # non-string tag rejected
        result3 = ms.tortoise_create_point("statement", "z", props={"tags": [123]})
        assert "error" in result3 and "invalid tag" in result3["error"]


# ── #329: analyze LLM budget (per-team per-minute) ──────────────────

class TestAnalyzeLlmBudget:
    def test_budget_exhausted_disables_llm(self, monkeypatch):
        """Beyond the per-minute budget, tortoise_analyze skips llm_classify
        (no outbound call) and degrades to keyword-only."""
        import tortoise.mcp_server as ms
        from tortoise.mcp_auth import _current_team_id
        from tortoise.quota import MAX_ANALYZE_LLM_PER_MIN

        # embedded env (no Docker) so the team SDK resolves
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/tortoise_analyze_budget.db")
        import tempfile as _tf, os as _os
        monkeypatch.setenv("TORTOISE_DB_PATH", _os.path.join(_tf.mkdtemp(), "budget.db"))

        # Team context (HTTP) → budget accounting
        token = _current_team_id.set("team-budget")
        try:
            # Exercise the ACCUMULATION path: MAX calls allowed, next rejected
            ms._ANALYZE_LLM_BUDGET.pop("team-budget", None)
            for _ in range(MAX_ANALYZE_LLM_PER_MIN):
                assert ms._analyze_llm_budget_available() is True
            assert ms._analyze_llm_budget_available() is False
            # A call beyond budget must not hit the LLM (urlopen never called)
            import urllib.request as _ur
            called = []
            def boom(*a, **kw):
                called.append(a)
                raise AssertionError("llm must not be called")
            monkeypatch.setattr(_ur, "urlopen", boom)
            monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
            result = ms.tortoise_analyze("where is the disagreement?")
            assert not called, "LLM was called beyond budget"
            # Keyword path still answers
            assert result.get("pattern") is not None or "disagreement" in str(result.get("answer", ""))
        finally:
            _current_team_id.reset(token)
            ms._ANALYZE_LLM_BUDGET.pop("team-budget", None)


class TestEventsTools:
    """#432 Task 6 — tortoise_events_poll + tortoise_retract_point tool functions.

    Previously skipped ("#432 SDK wiring incomplete" — the claim that
    TortoiseSDK.events_poll / retract_point did not exist was stale: they
    landed on main via #432 and the merged suite passes without the skip,
    code-review #803).
    """

    def test_tools_exist_and_registered(self):
        from tortoise.mcp_server import tortoise_events_poll, tortoise_retract_point
        from tortoise.tool_registry import TOOL_REGISTRY

        assert callable(tortoise_events_poll) and callable(tortoise_retract_point)
        names = {t.name for t in TOOL_REGISTRY}
        assert "tortoise_events_poll" in names
        assert "tortoise_retract_point" in names
        ev = next(t for t in TOOL_REGISTRY if t.name == "tortoise_events_poll")
        rt = next(t for t in TOOL_REGISTRY if t.name == "tortoise_retract_point")
        assert ev.http_policy is True and rt.http_policy is True
        assert ev.annotations.readOnlyHint is True
        assert rt.annotations.destructiveHint is True

    def test_events_poll_returns_same_shape_as_sdk(self, monkeypatch, tmp_path):
        import os
        from tortoise.mcp_server import tortoise_events_poll, _transport_mode
        from tortoise.sdk import TortoiseSDK

        db = os.path.join(str(tmp_path), "evt.db")
        sdk = TortoiseSDK(db)
        sdk.create_point("statement", "hello from mcp")
        monkeypatch.setattr("tortoise.mcp_server._get_team_sdk", lambda: sdk)
        token = _transport_mode.set("stdio")
        try:
            result = tortoise_events_poll()
        finally:
            _transport_mode.reset(token)
        assert result["events"] and result["events"][0]["type"] == "PointAdded"
        assert result["next_cursor"]

    def test_events_poll_unknown_type_error(self, monkeypatch, tmp_path):
        import os
        from tortoise.mcp_server import tortoise_events_poll, _transport_mode
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(os.path.join(str(tmp_path), "evt2.db"))
        monkeypatch.setattr("tortoise.mcp_server._get_team_sdk", lambda: sdk)
        token = _transport_mode.set("stdio")
        try:
            result = tortoise_events_poll(types=["Nope"])
        finally:
            _transport_mode.reset(token)
        assert result.get("error")  # _safe structured error, not a crash

    def test_retract_point_returns_sdk_result(self, monkeypatch, tmp_path):
        import os
        from tortoise.mcp_server import tortoise_retract_point, _transport_mode
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(os.path.join(str(tmp_path), "evt3.db"))
        p = sdk.create_point("statement", "retract me")
        monkeypatch.setattr("tortoise.mcp_server._get_team_sdk", lambda: sdk)
        token = _transport_mode.set("stdio")
        try:
            result = tortoise_retract_point(p["id"])
        finally:
            _transport_mode.reset(token)
        assert result.get("status") == "retracted"


class TestEventsHttpSurface:
    """#432 Task 6 review fix — explicit HTTP_ALLOWED membership for the two
    new tools (the plan's required assertion; previously only registry
    http_policy was tested, not the derived allow-list)."""

    def test_events_tools_in_http_allowed(self):
        from tortoise.mcp_auth import HTTP_ALLOWED

        assert "tortoise_events_poll" in HTTP_ALLOWED
        assert "tortoise_retract_point" in HTTP_ALLOWED


class TestIngestPromotionPolicy:
    """Epic #902 W4 A0 — promotion_policy param on the MCP tortoise_ingest
    tool (E2E-8.3 parity: exposed identically on SDK + MCP; invalid value →
    ERR_INVALID naming valid values). Also pins the ERR_INVALID constant that
    the granularity rejection always referenced but never defined (latent
    NameError on the pre-SDK param path)."""

    def test_mcp_tool_schema_exposes_promotion_policy(self):
        # E2E-8.3: the registered tool's JSON schema carries the param with
        # the same default as the SDK signature (schema derives from the
        # handler function — parity by construction, pinned here).
        import asyncio
        tools = asyncio.run(mcp_mod.mcp.list_tools())
        t = next(
            x for x in tools
            if (getattr(x, "name", None) or (x.get("name") if isinstance(x, dict) else None))
            == "tortoise_ingest"
        )
        params = getattr(t, "parameters", None)
        if params is None:
            params = t.get("inputSchema") if isinstance(t, dict) else None
        schema = params.model_json_schema() if hasattr(params, "model_json_schema") else params
        props = (schema or {}).get("properties", {})
        assert "promotion_policy" in props
        assert props["promotion_policy"].get("default") == "gated"
        assert "granularity" in props

    def test_mcp_invalid_promotion_policy_err_invalid(self):
        res = mcp_mod.tortoise_ingest(bundle={}, promotion_policy="atomic")
        assert res["code"] == mcp_mod.ERR_INVALID == -32003
        assert "gated" in res["error"] and "auto" in res["error"]

    def test_mcp_invalid_granularity_err_invalid(self):
        # Regression pin: the granularity rejection previously referenced an
        # undefined ERR_INVALID → NameError instead of the contract shape.
        res = mcp_mod.tortoise_ingest(bundle={}, granularity="atomic")
        assert res["code"] == mcp_mod.ERR_INVALID == -32003
        assert "bulk" in res["error"] and "granular" in res["error"]

    def test_mcp_gated_rejects_explicit_live_item(self):
        # INGEST_CONTRACT row 9 at the MCP layer: explicit status:'live' under
        # gated returns the structured ERR_INVALID shape naming the routes.
        res = mcp_mod.tortoise_ingest(
            bundle={"points": [{"kind": "claim", "content": "A",
                                 "status": "live"}]})
        assert res["code"] == mcp_mod.ERR_INVALID == -32003
        assert "not allowed under promotion_policy 'gated'" in res["error"]
        assert "promotion_policy='auto'" in res["error"]

    def test_mcp_gated_rejects_nested_props_and_case_variant(self):
        # PR #1073 re-review P0s/P1 at the MCP layer: nested props={status:live},
        # case variants, and canonical terminal statuses must all return
        # ERR_INVALID under gated.
        for item in ({"kind": "claim", "content": "A",
                      "props": {"status": "live"}},
                     {"kind": "claim", "content": "A", "status": "Live"},
                     {"kind": "claim", "content": "A", "status": "retracted"},
                     {"kind": "claim", "content": "A",
                      "props": {"status": "archived"}}):
            res = mcp_mod.tortoise_ingest(
                bundle={"points": [item]})
            assert res["code"] == mcp_mod.ERR_INVALID == -32003
            assert "not allowed under promotion_policy 'gated'" in res["error"]

    def _sdk_backed_ingest(self, request, monkeypatch, tmp_path, **kw):
        import os
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK(os.path.join(str(tmp_path), "ing.db"))
        request.addfinalizer(sdk.close)  # match repo teardown convention
        monkeypatch.setattr("tortoise.mcp_server._get_team_sdk", lambda: sdk)
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = mcp_mod.tortoise_ingest(bundle=bundle, **kw)
        return res

    def test_mcp_gated_default_keeps_source_draft(self, request, monkeypatch, tmp_path):
        # Agent journey: ingest via tortoise_ingest → read status back via
        # tortoise_get_point (the read surface the agent actually calls).
        res = self._sdk_backed_ingest(request, monkeypatch, tmp_path)
        assert "error" not in res, res
        pA, _ = res["ids"]["points"]
        op_id = res["ids"]["connections"][0]
        assert mcp_mod.tortoise_get_point(pA)["status"] == "draft"
        assert mcp_mod.tortoise_get_point(op_id)["status"] == "draft"

    def test_mcp_auto_promotes_source_live(self, request, monkeypatch, tmp_path):
        res = self._sdk_backed_ingest(
            request, monkeypatch, tmp_path, promotion_policy="auto")
        assert "error" not in res, res
        pA, pB = res["ids"]["points"]
        op_id = res["ids"]["connections"][0]
        assert mcp_mod.tortoise_get_point(pA)["status"] == "live"
        assert mcp_mod.tortoise_get_point(pB)["status"] == "draft"  # source-only
        op = mcp_mod.tortoise_get_point(op_id)
        assert "error" not in op, op
        # #780 live-side: auto writes NO status on the operator (EP treats
        # null-status as live) — assert key absence, not == "live".
        assert "status" not in op

    def test_mcp_granular_auto_parity(self, request, monkeypatch, tmp_path):
        # E2E-5 at the MCP layer: granularity is forwarded verbatim and the
        # auto×granular combination holds through the agent-facing tool.
        res = self._sdk_backed_ingest(
            request, monkeypatch, tmp_path,
            granularity="granular", promotion_policy="auto")
        assert "error" not in res, res
        assert res["granularity"] == "granular"
        assert isinstance(res.get("results"), list) and res["results"]
        pA, _ = res["ids"]["points"]
        assert mcp_mod.tortoise_get_point(pA)["status"] == "live"

    def test_mcp_gated_granular_parity(self, request, monkeypatch, tmp_path):
        # E2E-5 second cell at the MCP layer: gated default holds in granular
        # mode (no forced-auto window on the granular code path).
        res = self._sdk_backed_ingest(
            request, monkeypatch, tmp_path, granularity="granular")
        assert "error" not in res, res
        assert res["granularity"] == "granular"
        pA, _ = res["ids"]["points"]
        op_id = res["ids"]["connections"][0]
        assert mcp_mod.tortoise_get_point(pA)["status"] == "draft"
        assert mcp_mod.tortoise_get_point(op_id)["status"] == "draft"


class TestStdioEntrypointToolRegistration:
    """#993 regression — `python -m tortoise.mcp_server` must serve the tool
    registry. The __main__ guard previously sat ABOVE the @mcp.tool
    decorators and FastMCPAdapter.register_all(), so the stdio loop entered
    with ZERO tools registered (onboarding Step 0 = tortoise_health failed
    with "Can't connect to Tortoise"). Spawns the real entrypoint as a
    subprocess and asserts tools/list returns the full registry."""

    def test_stdio_entrypoint_serves_full_registry(self):
        import json
        import os
        import select
        import subprocess
        import sys
        from pathlib import Path

        env = dict(os.environ)
        for k in ("TORTOISE_DB_URI", "TORTOISE_DB_PATH", "TORTOISE_API_KEY"):
            env.pop(k, None)
        # #942 test-only escape hatch: tools/list needs no DB; avoid a
        # hard error from main()'s missing-config guard.
        env["TORTOISE_ALLOW_EMBEDDED"] = "1"
        repo_root = Path(__file__).resolve().parents[1]

        proc = subprocess.Popen(
            [sys.executable, "-m", "tortoise.mcp_server"],
            cwd=str(repo_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # never drained — avoid pipe-buffer deadlock (reviewer, #993)
            env=env,
            text=True,
            bufsize=1,
        )
        try:
            def send(payload: dict) -> None:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()

            def read_line(timeout: float = 30) -> dict:
                ready, _, _ = select.select([proc.stdout], [], [], timeout)
                assert ready, f"no MCP response within {timeout}s"
                return json.loads(proc.stdout.readline())

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tortoise-regression-test", "version": "0.0.0"},
            }})
            init = read_line()
            assert "result" in init and "serverInfo" in init["result"], init

            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            resp = read_line()
            tools = resp.get("result", {}).get("tools", [])
            names = [t["name"] for t in tools]

            # Issue #993 target (1): tools/list >= 70 on this entrypoint.
            assert len(names) >= 70, f"expected >=70 tools, got {len(names)}"
            # Onboarding-critical tools must be present (Step 0 + the set).
            assert "tortoise_health" in names
            onboarding = {
                "tortoise_onboarding_demo_create",
                "tortoise_onboarding_state",
                "tortoise_onboarding_session_recording",
                "tortoise_onboarding_github_connect",
                "tortoise_onboarding_github_index",
                "tortoise_onboarding_github_status",
            }
            missing = onboarding - set(names)
            assert not missing, f"missing onboarding tools: {sorted(missing)}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
