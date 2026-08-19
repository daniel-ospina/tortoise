"""Tests for tortoise.quota (#329, #683)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tortoise.quota import (
    QuotaCheckError,
    QuotaExceededError,
    count_team_usage,
    enforce_team_limit,
    resolve_team_limits,
)

# graph-scripts/ is a hyphenated (namespace) dir — import the #947 backfill
# one-shot via path insert (AGENTS.md sibling-import convention).
_GRAPH_SCRIPTS = str(Path(__file__).resolve().parent.parent / "graph-scripts")
if _GRAPH_SCRIPTS not in sys.path:
    sys.path.insert(0, _GRAPH_SCRIPTS)


@pytest.fixture(autouse=True)
def _embedded_env(monkeypatch, tmp_path):
    """Route quota SDKs to an embedded temp DB (no Docker in CI)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "quota.db"))
    # #822: capture_session is LLM-default (regex loop removed) — quota tests
    # exercise the capture path offline via the MockModel test seam.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def reg_sdk(monkeypatch, tmp_path):
    """Registry SDK with a team provisioned (same embedded DB as the env)."""
    from tortoise.sdk import TortoiseSDK
    import os
    db = os.path.join(tmp_path, "quota.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    sdk.team_create(name="quota-team")
    yield sdk
    sdk.close()


class TestResolveTeamLimits:
    def test_missing_team_fails_closed(self):
        with pytest.raises(QuotaCheckError):
            resolve_team_limits("no-such-team")

    def test_provisioned_team_has_defaults(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # team_create writes max_api_keys from pricing.json free tier (=2),
        # but NOT max_points / max_sessions — defaults apply (points from
        # pricing max_graph_nodes=10000; sessions flat 1000 per #310).
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] == 1000
        assert limits["max_users"] == 1
        assert limits["max_graphs"] == 1


def _find_team_id(sdk) -> str:
    """Find a team id in the registry graph (test helper)."""
    rows = sdk._get_registry().query(
        "MATCH (t:Team) RETURN t.id LIMIT 1"
    ).result_set
    assert rows, "no team provisioned"
    return rows[0][0]


class TestEnforceTeamLimit:
    def test_no_limits_skips(self):
        """stdio/operator: no team context → clean skip."""
        enforce_team_limit(None, "points")  # must not raise

    def test_at_limit_raises(self, tmp_path):
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace="team1")
        sdk.create_point("statement", "A")
        limits = {"team_id": "team1", "max_points": 1}
        with pytest.raises(QuotaExceededError):
            enforce_team_limit(limits, "points", sdk=sdk)
        sdk.close()

    def test_below_limit_passes(self, tmp_path):
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace="team1")
        sdk.create_point("statement", "A")
        limits = {"team_id": "team1", "max_points": 10}
        enforce_team_limit(limits, "points", sdk=sdk)  # must not raise
        sdk.close()

    def test_counting_error_fails_closed(self, tmp_path, monkeypatch, caplog):
        """Fail-closed: a counting exception → QuotaCheckError, never a pass.

        Also verifies ERROR-level logging (#686 alerting).
        """
        from tortoise.sdk import TortoiseSDK
        import logging
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace="team1")
        limits = {"team_id": "team1", "max_points": 1000}
        def boom(*a, **kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(sdk._get_proj().g._g, "query", boom)
        with pytest.raises(QuotaCheckError) as exc_info:
            enforce_team_limit(limits, "points", sdk=sdk)
        sdk.close()
        # Verify ERROR log was emitted (#686 alerting)
        assert "quota count failed" in str(exc_info.value)
        log_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("quota count failed (fail-closed)" in r.message for r in log_records), (
            f"Expected ERROR log for count failure, got: {[r.message for r in log_records]}"
        )

    def test_unknown_resource_fails_closed(self):
        with pytest.raises(QuotaCheckError):
            enforce_team_limit({"team_id": "t", "max_points": 10}, "widgets")


# ── #683: users + graphs enforcement ──────────────────────────────────────

class TestEnforceUsersLimit:
    """User/membership quota enforcement."""

    def test_users_below_limit_passes(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # team_create does NOT create a membership; count = 0, max_users = 1
        # → below limit
        enforce_team_limit(limits, "users")  # must not raise

    def test_users_at_limit_raises(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        # Create a membership to hit the limit
        reg_sdk.membership_create(tid, "user-1", "owner")
        limits = resolve_team_limits(tid)
        # 1 membership, max_users=1 → at limit
        with pytest.raises(QuotaExceededError, match="users limit reached"):
            enforce_team_limit(limits, "users")

    def test_users_unlimited_skips(self, reg_sdk):
        """None max_users = unlimited (Team tier) — never raises."""
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        limits["max_users"] = None  # Team tier → unlimited
        enforce_team_limit(limits, "users")  # must not raise


class TestEnforceGraphsLimit:
    """Graph quota enforcement."""

    def test_graphs_below_limit_passes(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # team_create auto-creates 1 default graph; max_graphs=1
        # bump limit to 5 so we're below it
        limits["max_graphs"] = 5
        enforce_team_limit(limits, "graphs")  # must not raise

    def test_graphs_at_limit_raises(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # 1 default graph from team_create, max_graphs=1 → at limit
        with pytest.raises(QuotaExceededError, match="graphs limit reached"):
            enforce_team_limit(limits, "graphs")

    def test_graphs_unlimited_skips(self, reg_sdk):
        """None max_graphs = unlimited (pro/team tier) — never raises."""
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        limits["max_graphs"] = None  # Pro/Team tier → unlimited
        enforce_team_limit(limits, "graphs")  # must not raise


# ── #683: None (unlimited) preservation in resolvers ──────────────────────

class TestNonePreservation:
    """None → unlimited must survive all limit resolvers (P0 regression)."""

    def test_resolve_team_limits_preserves_none_users(self, reg_sdk):
        """Team-tier team with max_users=None → resolve returns None, not 1."""
        tid = _find_team_id(reg_sdk)
        # Directly set max_users=None on the Team node (Team tier semantics)
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_users = NULL",
            params={"id": tid},
        )
        limits = resolve_team_limits(tid)
        assert limits["max_users"] is None, (
            f"Expected None (unlimited), got {limits['max_users']!r}")

    def test_resolve_team_limits_preserves_none_graphs(self, reg_sdk):
        """Team-tier team with max_graphs=None → resolve returns None."""
        tid = _find_team_id(reg_sdk)
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_graphs = NULL",
            params={"id": tid},
        )
        limits = resolve_team_limits(tid)
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited), got {limits['max_graphs']!r}")

    def test_team_limits_from_node_preserves_none_users(self):
        """_team_limits_from_node: None max_users → None (not coiled to 1)."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t1", "tier": "team",
                "max_users": None, "max_graphs": None}
        limits = _team_limits_from_node(node)
        assert limits["max_users"] is None, (
            f"Expected None (unlimited Team tier), got {limits['max_users']!r}")
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited Team tier), got {limits['max_graphs']!r}")

    def test_team_limits_from_node_preserves_none_graphs(self):
        """_team_limits_from_node: None max_graphs for pro tier = unlimited."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t2", "tier": "pro",
                "max_users": 2, "max_graphs": None}
        limits = _team_limits_from_node(node)
        # max_graphs=None (pro tier) → unlimited
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited pro graphs), got {limits['max_graphs']!r}")
        # max_users=2 is explicit → preserved
        assert limits["max_users"] == 2

    def test_team_limits_from_node_explicit_zero(self):
        """P1: explicit 0 is preserved, not conflated with missing."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t3", "tier": "free",
                "max_points": 0, "max_api_keys": 0, "max_sessions": 0}
        limits = _team_limits_from_node(node)
        assert limits["max_points"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_points']!r}")
        assert limits["max_api_keys"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_api_keys']!r}")
        assert limits["max_sessions"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_sessions']!r}")

    def test_team_limits_from_node_free_tier_defaults(self):
        """Missing fields on free-tier node → pricing-aligned defaults."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t4", "tier": "free"}
        limits = _team_limits_from_node(node)
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] == 1000


# ── #947 (epic #909 slice 2): sessions branch + is_episodic + constants ──

class TestSessionsQuota:
    """#947 P0: the sessions branch counts Session nodes, NOT all nodes.

    Pre-fix ``_count_resource("sessions")`` fell through to ``MATCH (n)`` —
    ~25 nodes per captured session → false 402 after ~40 captures. The
    fixture follows conftest.provision_test_user's convention (direct
    max_sessions write on the Team node — no tier gives 40, DE2E-7).
    """

    def test_sessions_count_returns_session_nodes_and_41st_402(
            self, reg_sdk, tmp_path):
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "quota.db")
        tid = _find_team_id(reg_sdk)
        # Inject max_sessions=40 on the Team node (provision_test_user
        # convention — direct write, DE2E-7 quota fixture).
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_sessions=40",
            params={"id": tid},
        )
        tenant = TortoiseSDK(db, namespace=tid)
        try:
            # 40 minimal captures (tiny conversation — no regex extraction)
            for i in range(40):
                tenant.capture_session(
                    [{"role": "user", "content": "ok"}],
                    session_id=f"s{i:02d}",
                )
            # P0 regression: sessions count == 40, NOT the all-nodes count
            # (pre-fix this branch returned the all-nodes count → fails
            # pre-fix).
            count = count_team_usage(tid, "sessions", sdk=tenant)
            assert count == 40, (
                f"sessions count should be 40, got {count} — the P0 "
                "(MATCH (n) all-nodes fallthrough) is not fixed")
            all_nodes = tenant._get_proj().g.query(
                "MATCH (n) RETURN count(n)"
            ).result_set[0][0]
            assert all_nodes > 40, (
                f"expected >40 total nodes ({all_nodes}) — the fixture must "
                "distinguish sessions from the pre-fix all-nodes count")
            # Resolver picks the injected limit up
            limits = resolve_team_limits(tid)
            assert limits["max_sessions"] == 40
            # 41st session → 402-equivalent (DE2E-7)
            with pytest.raises(QuotaExceededError, match="sessions limit reached"):
                enforce_team_limit(limits, "sessions", sdk=tenant)
        finally:
            tenant.close()

    def test_sessions_branch_ignores_non_session_nodes(self, reg_sdk, tmp_path):
        """Only :Session nodes count — plain Points never inflate sessions."""
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "quota.db")
        tid = _find_team_id(reg_sdk)
        tenant = TortoiseSDK(db, namespace=tid)
        try:
            tenant.capture_session(
                [{"role": "user", "content": "ok"}], session_id="only_s1")
            # ~10 non-Session nodes: turn Point + Event + extracted points
            tenant.create_point("statement", "plain non-episodic point")
            for i in range(8):
                tenant.create_point("statement", f"filler point {i}")
            assert count_team_usage(tid, "sessions", sdk=tenant) == 1
            # 10 = 1 plain + 8 fillers + 1 v2-extracted value point (the
            # deterministic _V2SessionMock extracts one point from "ok";
            # extracted value points are non-episodic and count against the
            # points quota by design — the capture estimate 2×Σ accounts for
            # them, #1350/#1486). Turn points stay episodic (not counted).
            assert count_team_usage(tid, "points", sdk=tenant) == 10
        finally:
            tenant.close()


class TestIsEpisodicBackfill:
    """R-18 (DE2E-7 legacy fixture): legacy nodes lack is_episodic → the
    one-query backfill (graph-scripts/backfill_is_episodic.py) stamps them →
    the points branch counts them as episodic (no false 402)."""

    def _make_tenant(self, reg_sdk, tmp_path):
        from tortoise.sdk import TortoiseSDK
        import os
        return TortoiseSDK(
            os.path.join(tmp_path, "quota.db"), namespace=_find_team_id(reg_sdk))

    def test_legacy_nodes_backfilled_are_episodic(self, reg_sdk, tmp_path):
        from backfill_is_episodic import run_backfill
        tid = _find_team_id(reg_sdk)
        tenant = self._make_tenant(reg_sdk, tmp_path)
        try:
            proj = tenant._get_proj()
            # Pre-#947 regex-path capture nodes — written WITHOUT the flag.
            # Capture artifacts: Session + CONTAINS turn Points + the
            # sessionCaptured Event + the session Source (extractedFrom).
            proj.g.query(
                "CREATE (s:Session {id:'legacy_s1', created_at:'2026-08-01T00:00:00Z', turn_count:2})")
            proj.g.query(
                "CREATE (e:Event {id:'legacy_e1', eventKind:'sessionCaptured'})")
            proj.g.query(
                "CREATE (src:Source {id:'legacy_src1', url:'legacy://src1'})")
            proj.g.query(
                "CREATE (p1:Point {id:'legacy_p1', content:'[user] ok', "
                "pointKind:'event', is_operator:false, status:'draft'})")
            proj.g.query(
                "CREATE (p2:Point {id:'legacy_p2', content:'[assistant] ok', "
                "pointKind:'event', is_operator:false, status:'draft'})")
            proj.g.query(
                "MATCH (s:Session {id:'legacy_s1'}), (p:Point {id:'legacy_p1'}) "
                "CREATE (s)-[:CONTAINS]->(p)")
            proj.g.query(
                "MATCH (s:Session {id:'legacy_s1'}), (p:Point {id:'legacy_p2'}) "
                "CREATE (s)-[:CONTAINS]->(p)")
            proj.g.query(
                "MATCH (p:Point {id:'legacy_p1'}), (src:Source {id:'legacy_src1'}) "
                "CREATE (p)-[:extractedFrom]->(src)")
            # A NON-capture knowledge Point — must NEVER be stamped (review P1,
            # PR #976: the scoped backfill exempts only capture artifacts; a
            # label-wide stamp would permanently undercount the points quota).
            proj.g.query(
                "CREATE (k1:Point {id:'legacy_k1', content:'we decided X', "
                "pointKind:'decision', is_operator:false, status:'live'})")
            # Pre-backfill: missing flag counts as NON-episodic (fail-closed,
            # R-18) → 3 legacy Points (p1, p2, k1) inflate the points quota.
            assert count_team_usage(tid, "points", sdk=tenant) == 3
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_team_limit(
                    {"team_id": tid, "max_points": 3}, "points", sdk=tenant)
            # Dry-run reports without writing (5 capture artifacts: s, e, src,
            # p1, p2 — NOT k1)
            report = run_backfill(proj, dry_run=True)
            assert report == {"matched": 5, "updated": 0}
            # Scoped migration applies the flag to the 5 capture artifacts only
            report = run_backfill(proj)
            assert report == {"matched": 5, "updated": 5}
            # Idempotent — re-run is a no-op
            assert run_backfill(proj) == {"matched": 0, "updated": 0}
            # k1 is untouched — still counted → quota is NOT undercounted
            assert count_team_usage(tid, "points", sdk=tenant) == 1
            enforce_team_limit(
                {"team_id": tid, "max_points": 3}, "points", sdk=tenant)
            # Sessions branch still counts the (now-flagged) Session
            assert count_team_usage(tid, "sessions", sdk=tenant) == 1
        finally:
            tenant.close()

    def test_new_capture_writes_carry_flag(self, reg_sdk, tmp_path):
        """Seam note (MECE ISSUE 3): regex-fallback captures write is_episodic
        on Session + turn Points going forward — unflagged new captures would
        re-introduce the false-402 this fix eliminates."""
        from backfill_is_episodic import run_backfill
        tid = _find_team_id(reg_sdk)
        tenant = self._make_tenant(reg_sdk, tmp_path)
        try:
            proj = tenant._get_proj()
            tenant.capture_session(
                [{"role": "user", "content": "ok"}], session_id="new_s1")
            # Nothing to backfill — new capture nodes already carry the flag
            # (turn Point, Event, and the session-provenance Source all stamp
            # is_episodic at creation; _link_source stamps session refs, #1486).
            assert run_backfill(proj) == {"matched": 0, "updated": 0}
            # The turn Point is episodic; the ONE v2-extracted value point
            # (deterministic _V2SessionMock, non-episodic by design) is the
            # only point counted against the quota (#1350/#1486).
            assert count_team_usage(tid, "points", sdk=tenant) == 1
            # Session counted by the sessions branch
            assert count_team_usage(tid, "sessions", sdk=tenant) == 1
        finally:
            tenant.close()


class TestBudgetConstants:
    """#947 indicator (b): budget + Layer-1 payload caps exported from
    quota.py (epic #909 §4.4 — DE2E-7 Layer-1 51-point → 422 uses
    MAX_PAYLOAD_POINTS)."""

    def test_max_value_points_per_session(self):
        from tortoise.quota import MAX_VALUE_POINTS_PER_SESSION
        assert MAX_VALUE_POINTS_PER_SESSION == {"soft": 15, "hard": 25, "ceiling": 50}

    def test_max_payload_points(self):
        from tortoise.quota import (
            MAX_ENTITIES, MAX_OPERATORS, MAX_PAYLOAD_POINTS,
            MAX_VALUE_POINTS_PER_SESSION,
        )
        assert MAX_PAYLOAD_POINTS == 50
        assert MAX_ENTITIES == 500
        assert MAX_OPERATORS == 500
        # R-decoupling: the Layer-1 raw cap is deliberately a SEPARATE named
        # constant from the budget ceiling (same numeric value — the name
        # prevents wiring the wrong 50, plan §4.4).
        assert MAX_PAYLOAD_POINTS == MAX_VALUE_POINTS_PER_SESSION["ceiling"]
        assert MAX_PAYLOAD_POINTS == 50  # explicit value, not derived
