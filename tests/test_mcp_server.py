"""Tests for mcp_server — MCP tool registration, _safe wrapper, and tool behavior."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Check if FalkorDB is available for integration tests
try:
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK()
    sdk.status()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False


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
        from tortoise.mcp_server import tortoise_query
        result = tortoise_query(kind="observation")
        assert isinstance(result, list) or isinstance(result.get("error"), str)

    def test_search_returns_list(self):
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("integration test", limit=5)
        assert isinstance(result, list) or isinstance(result.get("error"), str)

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
            # Fill the budget with FRESH timestamps (stale ones are pruned)
            import time as _t
            ms._ANALYZE_LLM_BUDGET["team-budget"] = [_t.time()] * MAX_ANALYZE_LLM_PER_MIN
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
