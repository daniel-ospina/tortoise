"""DE2E-7 — Gate gating (pytest-level contract) + Phase-4 MCP surface (#787).

Epic plan §7 DE2E-7: calibration_passed() marker set/unset; check_gates
blocked with reasons when Gate A open or calibration open; both closed →
clear; no graph writes while blocked. The MCP surface contract: the 5
Phase-4 tools exist in the registry and error with {error: message} via
the _safe convention (J-6).
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from tortoise.gates import check_gates
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"),
                       event_log_path=str(tmp_path / "events.jsonl"))


class TestDe2e7:

    def test_check_gates_blocked_when_gate_a_open(self):
        res = check_gates(784, issue_body="Depends on: #320, #779",
                          dependency_states={320: "open"},
                          calibration_passed=True)
        assert res["blocked"] is True
        assert any("gate A" in r for r in res["reasons"])
        assert res["gates"]["gate_a"] == "open"

    def test_check_gates_blocked_when_calibration_open(self):
        res = check_gates(784, issue_body="Depends on: #320",
                          dependency_states={320: "closed"},
                          calibration_passed=False)
        assert res["blocked"] is True
        assert any("calibration" in r for r in res["reasons"])
        assert res["gates"]["gate_b"] == "open"

    def test_check_gates_clear_when_both_closed(self):
        res = check_gates(784, issue_body="Depends on: #320",
                          dependency_states={320: "closed"},
                          calibration_passed=True)
        assert res["blocked"] is False
        assert res["reasons"] == []
        assert res["gates"] == {"gate_a": "closed", "gate_b": "passed"}

    def test_check_gates_never_writes(self, sdk):
        """'No graph writes while blocked' — the helper only reads: assert
        the graph is unchanged after a blocked check."""
        before = sdk._get_proj().g.query(
            "MATCH (n) RETURN count(n)").result_set[0][0]
        res = check_gates(784, issue_body="Depends on: #320",
                          dependency_states={320: "open"},
                          calibration_passed=False)
        assert res["blocked"] is True
        after = sdk._get_proj().g.query(
            "MATCH (n) RETURN count(n)").result_set[0][0]
        assert before == after

    def test_check_gates_gh_runner_injection(self):
        """The CLI path resolves issue state via an injectable gh runner."""
        calls = []

        def fake_gh(path):
            calls.append(path)
            issue = int(path.rsplit("/", 1)[-1])
            state = "closed" if issue == 320 else "open"
            return json.dumps({"state": state, "body": "Depends on: #320"})

        res = check_gates(787, gh_runner=fake_gh, calibration_passed=True)
        assert res["blocked"] is False
        assert calls, "gh runner must be invoked for the issue body + deps"

    def test_phase4_tools_registered(self):
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
        names = {t.name for t in TOOL_REGISTRY}

        # #4282 Phase 2: `tortoise_promote_point` retired into the
        # `tortoise_refresh_confidence` container (scope='promote', point_id=...)
        # — the capability is what this pin follows, not the surface shape.
        # #11609 Phase 2 retired `tortoise_list_dedup_candidates` and
        # `tortoise_belief_timeline` into the containers named here, by the same
        # idiom; the pin moves with the doors rather than being dropped (the SDK
        # methods `promote_point` and `belief_timeline` themselves are still live
        # and are driven below through their #3883 shims).
        phase4 = {"tortoise_mine_conversations", "tortoise_approve_merge",
                  "tortoise_review_link_candidates", "tortoise_check_confidence",
                  "tortoise_refresh_confidence"}
        assert phase4 <= names, phase4 - names
        # #11609 lane C: `tortoise_mine_conversations` is retired into the SESSION
        # container, so it is SERVED but no longer ADVERTISED (#3883 keeps the old
        # name answering). Pin both halves — the shim and its destination.
        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY
        retired_names = {t.name for t in RETIRED_TOOL_REGISTRY}
        assert "tortoise_mine_conversations" in retired_names, "the retired shim left the registry"
        assert "tortoise_mine_knowledge_from_session" in names, "Missing #787 container" 
        by_name = {t.name: t for t in TOOL_REGISTRY}
        assert "scope='promote'" in by_name["tortoise_refresh_confidence"].description
        assert by_name["tortoise_review_link_candidates"].sdk_method == "review_link_candidates"
        assert by_name["tortoise_check_confidence"].sdk_method == "check_confidence"
        # The retired doors still DRIVE their original SDK methods, which is what
        # makes each container above the same answer rather than a new capability.
        retired_by_name = {t.name: t for t in RETIRED_TOOL_REGISTRY}
        assert retired_by_name["tortoise_promote_point"].sdk_method == "promote_point"
        assert retired_by_name["tortoise_belief_timeline"].sdk_method == "belief_timeline"

    def test_mcp_handler_error_contract(self, monkeypatch):
        """J-6: handlers return {error: message} on failure — the REAL SDK
        raise path is exercised (stdio dev mode, then a raising method)."""
        import tortoise.mcp_server as mcp
        from tortoise.sdk import TortoiseSDK
        monkeypatch.setenv("TORTOISE_API_KEY", "")
        mcp._transport_mode.set("stdio")
        sdk = TortoiseSDK(db_path=os.path.join(tempfile.mkdtemp(), "t.db"))
        monkeypatch.setattr(mcp, "_get_org_sdk", lambda: sdk)
        # Real SDK raise path: nonexistent ids → ValueError → {error: ...}.
        res = mcp.tortoise_promote_point("no-such-point-123")
        assert isinstance(res, dict) and "error" in res, res
        res2 = mcp.tortoise_approve_merge("no-such-candidate", action="merge")
        assert isinstance(res2, dict) and "error" in res2, res2
        res3 = mcp.tortoise_mine_conversations()
        assert isinstance(res3, dict) and "error" in res3, res3
        # Raising SDK method → {error: message} (sanitized, no crash).
        def boom(*a, **k):
            raise RuntimeError("synthetic sdk failure")
        monkeypatch.setattr(sdk, "belief_timeline", boom)
        res4 = mcp.tortoise_belief_timeline("port 16379")
        assert isinstance(res4, dict) and "error" in res4, res4
        assert "synthetic" in res4["error"]

    def test_check_gates_open_unknown_dependency_blocks(self):
        """An open dependency NOT in the known in-epic set blocks."""
        res = check_gates(787,
                          issue_body="Depends on: #320, #9999",
                          dependency_states={320: "closed", 9999: "open"},
                          calibration_passed=True)
        assert res["blocked"] is True
        assert any("9999" in r for r in res["reasons"])
        assert res["open_dependencies"] == [9999]

    def test_check_gates_in_epic_deps_do_not_block(self):
        """Documented in-epic sequencing refs (#779/#780/#782/...) do not
        block — they carry their own gates."""
        res = check_gates(784,
                          issue_body="Depends on: #320, #779, #782, #780",
                          dependency_states={320: "closed"},
                          calibration_passed=True)
        assert res["blocked"] is False, res
