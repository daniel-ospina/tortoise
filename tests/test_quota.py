"""Tests for tortoise.quota (#329, #683)."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tortoise.quota import (
    QUOTA_REFUSAL_CODE,
    QuotaCheckError,
    QuotaExceededError,
    count_org_usage,
    enforce_org_limit,
    quota_refusal_payload,
    resolve_org_limits,
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
    from tortoise.sdk import TortoiseSDK  # noqa: I001
    import os
    db = os.path.join(tmp_path, "quota.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    sdk.org_create(name="quota-team")
    yield sdk
    sdk.close()


class TestResolveTeamLimits:
    def test_missing_team_fails_closed(self):
        with pytest.raises(QuotaCheckError):
            resolve_org_limits("no-such-team")

    def test_provisioned_team_has_defaults(self, reg_sdk):
        tid = _find_org_id(reg_sdk)
        limits = resolve_org_limits(tid)
        # team_create writes max_api_keys from pricing.json free tier (=2),
        # but NOT max_points — the pricing default applies (max_graph_nodes
        # = 10000). max_sessions has no default at all: it is UNLIMITED per
        # #4010 (present-but-None).
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] is None
        assert limits["max_users"] == 1
        assert limits["max_graphs"] == 1


def _find_org_id(sdk) -> str:
    """Find a team id in the registry graph (test helper)."""
    rows = sdk._get_registry().query(
        "MATCH (t:Team) RETURN t.id LIMIT 1"
    ).result_set
    assert rows, "no team provisioned"
    return rows[0][0]


class TestEnforceTeamLimit:
    def test_no_limits_skips(self):
        """stdio/operator: no team context → clean skip."""
        enforce_org_limit(None, "points")  # must not raise

    def test_at_limit_raises(self, tmp_path):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace=f"test_quota_team1_{os.urandom(4).hex()}")
        sdk.create_point("statement", "A")
        limits = {"org_id": "team1", "max_points": 1}
        with pytest.raises(QuotaExceededError) as exc_info:
            enforce_org_limit(limits, "points", sdk=sdk)
        # #4614: the generic branch reports the resource and the values it
        # compared — the payload is not a capture-path special case.
        assert (exc_info.value.resource, exc_info.value.used,
                exc_info.value.limit) == ("points", 1, 1)
        payload = quota_refusal_payload(exc_info.value)
        assert payload["code"] == QUOTA_REFUSAL_CODE
        assert payload["resource"] == "points"
        assert payload["used"] == 1 and payload["limit"] == 1
        assert payload["message"] == "Team points limit reached (1). " \
            "Upgrade your plan to increase it."
        sdk.close()

    def test_below_limit_passes(self, tmp_path):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace=f"test_quota_team1_{os.urandom(4).hex()}")
        sdk.create_point("statement", "A")
        limits = {"org_id": "team1", "max_points": 10}
        enforce_org_limit(limits, "points", sdk=sdk)  # must not raise
        sdk.close()

    def test_counting_error_fails_closed(self, tmp_path, monkeypatch, caplog):
        """Fail-closed: a counting exception → QuotaCheckError, never a pass.

        Also verifies ERROR-level logging (#686 alerting).
        """
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import logging
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace=f"test_quota_team1_{os.urandom(4).hex()}")
        limits = {"org_id": "team1", "max_points": 1000}
        def boom(*a, **kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(sdk._get_proj().g._g, "query", boom)
        with pytest.raises(QuotaCheckError) as exc_info:
            enforce_org_limit(limits, "points", sdk=sdk)
        sdk.close()
        # Verify ERROR log was emitted (#686 alerting)
        assert "quota count failed" in str(exc_info.value)
        log_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("quota count failed (fail-closed)" in r.message for r in log_records), (
            f"Expected ERROR log for count failure, got: {[r.message for r in log_records]}"
        )

    def test_unknown_resource_fails_closed(self):
        with pytest.raises(QuotaCheckError):
            enforce_org_limit({"org_id": "t", "max_points": 10}, "widgets")


# ── #683: users + graphs enforcement ──────────────────────────────────────

class TestEnforceUsersLimit:
    """User/membership quota enforcement."""

    def test_users_below_limit_passes(self, reg_sdk):
        tid = _find_org_id(reg_sdk)
        limits = resolve_org_limits(tid)
        # team_create does NOT create a membership; count = 0, max_users = 1
        # → below limit
        enforce_org_limit(limits, "users")  # must not raise

    def test_users_at_limit_raises(self, reg_sdk):
        tid = _find_org_id(reg_sdk)
        # Create a membership to hit the limit
        reg_sdk.membership_create(tid, "user-1", "owner")
        limits = resolve_org_limits(tid)
        # 1 membership, max_users=1 → at limit
        with pytest.raises(QuotaExceededError, match="users limit reached"):
            enforce_org_limit(limits, "users")

    def test_users_unlimited_skips(self, reg_sdk):
        """None max_users = unlimited (Team tier) — never raises."""
        tid = _find_org_id(reg_sdk)
        limits = resolve_org_limits(tid)
        limits["max_users"] = None  # Team tier → unlimited
        enforce_org_limit(limits, "users")  # must not raise


class TestEnforceGraphsLimit:
    """Graph quota enforcement."""

    def test_graphs_below_limit_passes(self, reg_sdk):
        tid = _find_org_id(reg_sdk)
        limits = resolve_org_limits(tid)
        # team_create auto-creates 1 default graph; max_graphs=1
        # bump limit to 5 so we're below it
        limits["max_graphs"] = 5
        enforce_org_limit(limits, "graphs")  # must not raise

    def test_graphs_at_limit_raises(self, reg_sdk):
        tid = _find_org_id(reg_sdk)
        limits = resolve_org_limits(tid)
        # 1 default graph from team_create, max_graphs=1 → at limit
        with pytest.raises(QuotaExceededError, match="graphs limit reached"):
            enforce_org_limit(limits, "graphs")

    def test_graphs_unlimited_skips(self, reg_sdk):
        """None max_graphs = unlimited (pro/team tier) — never raises."""
        tid = _find_org_id(reg_sdk)
        limits = resolve_org_limits(tid)
        limits["max_graphs"] = None  # Pro/Team tier → unlimited
        enforce_org_limit(limits, "graphs")  # must not raise


# ── #683: None (unlimited) preservation in resolvers ──────────────────────

class TestNonePreservation:
    """None → unlimited must survive all limit resolvers (P0 regression)."""

    def test_resolve_team_limits_preserves_none_users(self, reg_sdk):
        """Team-tier team with max_users=None → resolve returns None, not 1."""
        tid = _find_org_id(reg_sdk)
        # Directly set max_users=None on the Team node (Team tier semantics)
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_users = NULL",
            params={"id": tid},
        )
        limits = resolve_org_limits(tid)
        assert limits["max_users"] is None, (
            f"Expected None (unlimited), got {limits['max_users']!r}")

    def test_resolve_team_limits_preserves_none_graphs(self, reg_sdk):
        """Team-tier team with max_graphs=None → resolve returns None."""
        tid = _find_org_id(reg_sdk)
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_graphs = NULL",
            params={"id": tid},
        )
        limits = resolve_org_limits(tid)
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited), got {limits['max_graphs']!r}")

    def test_team_limits_from_node_preserves_none_users(self):
        """_org_limits_from_node: None max_users → None (not coiled to 1)."""
        from tortoise.hosted_api import _org_limits_from_node
        node = {"id": "t1", "tier": "team",
                "max_users": None, "max_graphs": None}
        limits = _org_limits_from_node(node)
        assert limits["max_users"] is None, (
            f"Expected None (unlimited Team tier), got {limits['max_users']!r}")
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited Team tier), got {limits['max_graphs']!r}")

    def test_team_limits_from_node_preserves_none_graphs(self):
        """_org_limits_from_node: None max_graphs for pro tier = unlimited."""
        from tortoise.hosted_api import _org_limits_from_node
        node = {"id": "t2", "tier": "pro",
                "max_users": 2, "max_graphs": None}
        limits = _org_limits_from_node(node)
        # max_graphs=None (pro tier) → unlimited
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited pro graphs), got {limits['max_graphs']!r}")
        # max_users=2 is explicit → preserved
        assert limits["max_users"] == 2

    def test_team_limits_from_node_explicit_zero(self):
        """P1: explicit 0 is preserved, not conflated with missing.

        #4010: max_sessions is the exception — sessions have no cap, so a
        stored 0 is NOT honoured (it would be a zero-session cap)."""
        from tortoise.hosted_api import _org_limits_from_node
        node = {"id": "t3", "tier": "free",
                "max_points": 0, "max_api_keys": 0, "max_sessions": 0}
        limits = _org_limits_from_node(node)
        assert limits["max_points"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_points']!r}")
        assert limits["max_api_keys"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_api_keys']!r}")
        assert limits["max_sessions"] is None, (
            f"sessions are unlimited (#4010) — a stored 0 must not cap them, "
            f"got {limits['max_sessions']!r}")

    def test_team_limits_from_node_free_tier_defaults(self):
        """Missing fields on free-tier node → pricing-aligned defaults."""
        from tortoise.hosted_api import _org_limits_from_node
        node = {"id": "t4", "tier": "free"}
        limits = _org_limits_from_node(node)
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] is None  # unlimited (#4010)


# ── #947 (epic #909 slice 2): sessions branch + is_episodic + constants ──

class TestSessionsQuota:
    """#947 P0: the sessions branch counts Session nodes, NOT all nodes.

    Pre-fix ``_count_resource("sessions")`` fell through to ``MATCH (n)`` —
    ~25 nodes per captured session → false 402 after ~40 captures. #4010: the
    stored ``t.max_sessions`` is no longer honoured as a cap (sessions are
    unlimited for every tier), so the same fixture that used to gate the 41st
    capture now stores it — the #947 count property is unchanged.
    """

    def test_sessions_count_returns_session_nodes_and_41st_lands(
            self, reg_sdk, tmp_path):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        db = os.path.join(tmp_path, "quota.db")
        tid = _find_org_id(reg_sdk)
        # Inject max_sessions=40 on the Team node (provision_test_user
        # convention — direct write, DE2E-7 quota fixture). #4010: a direct
        # stored write is deliberately NOT honoured as a cap.
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_sessions=40",
            params={"id": tid},
        )
        tenant = TortoiseSDK(db, namespace=tid)
        try:
            # 40 minimal captures (tiny conversation — no regex extraction)
            for i in range(40):
                tenant.capture_session(
                    [{"role": "user", "content": "okay"}],
                    session_id=f"s{i:02d}",
                )
            # P0 regression: sessions count == 40, NOT the all-nodes count
            # (pre-fix this branch returned the all-nodes count → fails
            # pre-fix).
            count = count_org_usage(tid, "sessions", sdk=tenant)
            assert count == 40, (
                f"sessions count should be 40, got {count} — the P0 "
                "(MATCH (n) all-nodes fallthrough) is not fixed")
            all_nodes = tenant._get_proj().g.query(
                "MATCH (n) RETURN count(n)"
            ).result_set[0][0]
            assert all_nodes > 40, (
                f"expected >40 total nodes ({all_nodes}) — the fixture must "
                "distinguish sessions from the pre-fix all-nodes count")
            # #4010: the stored 40 is NOT honoured — the resolver yields None.
            limits = resolve_org_limits(tid)
            assert limits["max_sessions"] is None, (
                "a stored max_sessions=40 was honoured as a cap — the #4010 "
                "trap is open")
            enforce_org_limit(limits, "sessions", sdk=tenant)  # no raise
            # 41st session is STORED, not refused (was a 402 before #4010).
            tenant.capture_session(
                [{"role": "user", "content": "okay"}], session_id="s40")
            assert count_org_usage(tid, "sessions", sdk=tenant) == 41
        finally:
            tenant.close()

    def test_sessions_branch_ignores_non_session_nodes(self, reg_sdk, tmp_path):
        """Only :Session nodes count — plain Points never inflate sessions."""
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        db = os.path.join(tmp_path, "quota.db")
        tid = _find_org_id(reg_sdk)
        tenant = TortoiseSDK(db, namespace=tid)
        try:
            tenant.capture_session(
                [{"role": "user", "content": "okay"}], session_id="only_s1")
            # ~10 non-Session nodes: turn Point + Event + extracted points
            tenant.create_point("statement", "plain non-episodic point")
            for i in range(8):
                tenant.create_point("statement", f"filler point {i}")
            assert count_org_usage(tid, "sessions", sdk=tenant) == 1
            # 11 = 1 plain + 8 fillers + 1 v2-extracted value point + 1
            # v2-minted Object entity (the deterministic _V2SessionMock
            # extracts one point and one entity from "ok"); extracted value
            # points AND minted entities are non-episodic and count against
            # the quota by design — the capture estimate 3×Σ accounts for
            # both, #1350/#1486/#1911). Turn points stay episodic (not
            # counted).
            assert count_org_usage(tid, "points", sdk=tenant) == 11
        finally:
            tenant.close()


# ── #1726 Slice 1: documents resource (derived-constant cap) ─────

class TestDocumentsQuota:
    """#1726: the documents gate fires on /v1/index/docs ONLY, with the
    derived-constant cap (max_documents = max_points ×
    _DOCUMENTS_FROM_POINTS_FACTOR — deliberately NOT a pricing.json field).
    D10 (ONTOLOGY v3.15 §4.4): the count is over document-bearing :Source
    nodes (documentKind IS NOT NULL AND <> 'transcript'); a Source with NO
    documentKind (session/connector/provenance) is NOT metered, and session
    transcripts are excluded."""

    def _tenant(self, tmp_path, reg_sdk):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        db = os.path.join(tmp_path, "quota.db")
        tid = _find_org_id(reg_sdk)
        return tid, TortoiseSDK(db, namespace=tid)

    def _seed_doc(self, tenant, i: int, kind: str | None) -> None:
        """Seed a document-bearing :Source (D10: a document IS a :Source).

        kind=None omits documentKind entirely (a session/connector/provenance
        Source); kind='' mirrors the projection's frontmatter-less doc write
        (``s.documentKind = coalesce($dk, s.documentKind, '')`` — a null or
        omitted ``$dk`` lands as the non-null empty string)."""
        if kind is None:
            tenant._get_proj().g.query(
                "CREATE (s:Source {url:$url, title:$t})",
                params={"url": f"doc_{i}", "t": f"doc {i}"})
        else:
            tenant._get_proj().g.query(
                "CREATE (s:Source {url:$url, title:$t, documentKind:$k})",
                params={"url": f"doc_{i}", "t": f"doc {i}", "k": kind})

    def test_null_kind_not_counted_kindless_doc_counts(self, reg_sdk, tmp_path):
        """D10: a :Source with NO documentKind (session/connector/provenance)
        is NOT a document and must not be metered. A kindless docs-endpoint
        doc — the projection writes documentKind='' for a frontmatter-less doc
        — IS document-bearing and COUNTS, so it never leaks past the gate."""
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            for i in range(3):
                self._seed_doc(tenant, i, kind=None)
            assert count_org_usage(tid, "documents", sdk=tenant) == 0
            for i in range(3, 6):
                self._seed_doc(tenant, i, kind="")
            assert count_org_usage(tid, "documents", sdk=tenant) == 3
        finally:
            tenant.close()

    def test_the_refusal_reports_the_derived_limit(self, reg_sdk, tmp_path):
        """#4614: the documents cap is DERIVED (`max_points x factor`) and is
        deliberately NOT in `_RESOURCE_LIMIT_KEYS` — so its refusal must carry
        the limit the branch actually compared. Reporting the raw `max_points`
        would understate the bound by the factor, and reporting nothing would
        leave the caller to prose-match its way to a number.
        """
        from tortoise.quota import _DOCUMENTS_FROM_POINTS_FACTOR
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            limit = 1 * _DOCUMENTS_FROM_POINTS_FACTOR
            for i in range(limit):
                self._seed_doc(tenant, i, kind="brief")
            with pytest.raises(QuotaExceededError) as exc_info:
                enforce_org_limit(
                    {"org_id": tid, "max_points": 1}, "documents", sdk=tenant)
            err = exc_info.value
            assert err.resource == "documents"
            assert err.limit == limit, (
                f"reported {err.limit!r}, but the gate compared {limit!r} "
                f"(max_points x {_DOCUMENTS_FROM_POINTS_FACTOR})")
            assert err.used == limit
            payload = quota_refusal_payload(err)
            assert payload["code"] == QUOTA_REFUSAL_CODE
            assert payload["resource"] == "documents"
            assert payload["limit"] == limit and payload["used"] == limit
        finally:
            tenant.close()

    def test_transcript_not_counted(self, reg_sdk, tmp_path):
        """Session transcripts (documentKind='transcript') do NOT consume the
        documents gate — a captured session never 402s docs indexing."""
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            self._seed_doc(tenant, 0, kind="brief")
            self._seed_doc(tenant, 1, kind="transcript")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1
        finally:
            tenant.close()

    def test_b3_legacy_documentcreated_path_cannot_escape_the_cap(
            self, reg_sdk, tmp_path):
        """Adversarial B3 (#5026/D10): a document created through the OLD
        (DocumentCreated) path is a document-bearing :Source and IS metered —
        it cannot escape the documents cap by being written off the index
        path. The gate then refuses at the derived cap."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            assert count_org_usage(tid, "documents", sdk=tenant) == 0
            log = EventLog(str(tmp_path / "b3_events.jsonl"))
            api = EventAPI(log, initiated_by="extractor",
                           projection=tenant._get_proj())
            api.add_document("doc/b3-legacy.md", "Legacy B3")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1, \
                "legacy DocumentCreated doc escaped the :Source documents cap"
            # derived cap 0*10 == 0 → the single legacy doc is OVER the cap
            with pytest.raises(QuotaExceededError,
                               match="documents limit reached"):
                enforce_org_limit({"org_id": tid, "max_points": 0},
                                  "documents", sdk=tenant)
        finally:
            tenant.close()

    def test_b3_null_document_kind_cannot_escape_the_cap(
            self, reg_sdk, tmp_path):
        """Adversarial B3 (#5026/D10): an explicit
        ``document_kind=None`` (ingest's YAML ``type:`` decodes to None;
        ``EventAPI.add_document(document_kind=None)``; a null field in a
        replayed JSONL line) must NOT leave the document Source NULL-kind —
        the meter reads ``documentKind IS NOT NULL``, so a NULL kind would
        escape the cap. The projection coerces a null/absent kind to '' on
        CREATE."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            assert count_org_usage(tid, "documents", sdk=tenant) == 0
            log = EventLog(str(tmp_path / "b3_null_events.jsonl"))
            api = EventAPI(log, initiated_by="extractor",
                           projection=tenant._get_proj())
            for i in range(3):
                api.add_document(f"doc/b3-null-{i}.md", f"Null kind {i}",
                                 document_kind=None)
            kinds = tenant._get_proj().g.query(
                "MATCH (s:Source) WHERE s.url STARTS WITH 'doc/b3-null' "
                "RETURN s.documentKind").result_set
            assert kinds and all(r[0] is not None for r in kinds), kinds
            assert count_org_usage(tid, "documents", sdk=tenant) == 3, \
                "null-kind document escaped the :Source documents cap"
            with pytest.raises(QuotaExceededError,
                               match="documents limit reached"):
                enforce_org_limit({"org_id": tid, "max_points": 0},
                                  "documents", sdk=tenant)
        finally:
            tenant.close()

    def test_b4_provenance_source_not_over_counted(self, reg_sdk, tmp_path):
        """Adversarial B4 (#5026/D10): an ordinary connector/provenance
        :Source (sourceKind + contentHash, NO documentKind) is NOT metered by
        the documents cap — a COALESCE-to-empty predicate would meter every
        such node (the #1726 price change D10 forbids)."""
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            for i in range(4):
                tenant._get_proj().g.query(
                    "CREATE (s:Source {url:$url, sourceKind:'github', "
                    "contentHash:'h', title:$t})",
                    params={"url": f"https://gh/{i}", "t": f"repo {i}"})
            assert count_org_usage(tid, "documents", sdk=tenant) == 0, \
                "provenance Sources were over-counted as documents"
            # one real document among them still counts exactly once
            self._seed_doc(tenant, 9, kind="report")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1
        finally:
            tenant.close()

    def test_derived_cap_402_and_points_independent(self, reg_sdk, tmp_path):
        """max_documents is DERIVED from max_points (10×) — the points gate
        itself is irrelevant to the documents resource."""
        from tortoise.quota import _DOCUMENTS_FROM_POINTS_FACTOR
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            reg_sdk._get_registry().query(
                "MATCH (t:Team {id:$id}) SET t.max_points = 1",
                params={"id": tid})
            # 2 non-episodic points → OVER the points cap (max_points=1) —
            # irrelevant here; 9 docs < 10 (derived cap) → passes
            tenant.create_point("statement", "claim one")
            tenant.create_point("statement", "claim two")
            for i in range(9):
                self._seed_doc(tenant, i, kind="brief")
            limits = resolve_org_limits(tid)
            assert limits["max_points"] == 1
            assert (1 * _DOCUMENTS_FROM_POINTS_FACTOR) == 10
            enforce_org_limit(limits, "documents", sdk=tenant)  # no raise
            # 10th doc → 402-equivalent at the derived cap
            self._seed_doc(tenant, 9, kind="brief")
            with pytest.raises(QuotaExceededError, match="documents limit reached"):
                enforce_org_limit(limits, "documents", sdk=tenant)
        finally:
            tenant.close()


# ── #1911: object/subject writes count against the quota cap ──────────────

class TestObjectSubjectQuota:
    """#1911 (bug-hunt 2026-08-28 server P2-1): /v1/objects + /v1/subjects
    gate on ``_check_team_limit(team, "points")``, but the points count
    used to count ONLY ``:Point`` nodes — Object/Subject nodes carry only
    their own labels (projection/entities.py) and were never counted, so a
    free team could write unbounded objects/subjects without ever 402ing.
    The points count now includes Object+Subject nodes (max_points IS the
    pricing max_graph_nodes node cap)."""

    def _tenant(self, reg_sdk, tmp_path):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        return TortoiseSDK(
            os.path.join(tmp_path, "quota.db"),
            namespace=_find_org_id(reg_sdk))

    def test_points_count_includes_objects_and_subjects(self, reg_sdk, tmp_path):
        """Indicator (1): the quota count includes Object+Subject nodes."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_point("statement", "a plain point")
            tenant.create_object("acme", objectKind="org")
            tenant.create_object("globex", objectKind="org")
            tenant.create_subject("alice", subjectKind="person")
            assert count_org_usage(tid, "points", sdk=tenant) == 4
        finally:
            tenant.close()

    def test_object_write_402_at_cap(self, reg_sdk, tmp_path):
        """Indicator (2): an object write 402s at the cap (pre-fix the
        points count could never see Object nodes → no 402)."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_object("o1", objectKind="org")
            limits = {"org_id": tid, "max_points": 1}
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit(limits, "points", sdk=tenant)
        finally:
            tenant.close()

    def test_subject_write_402_at_cap(self, reg_sdk, tmp_path):
        """Indicator (2): a subject write 402s at the cap."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_subject("s1", subjectKind="person")
            limits = {"org_id": tid, "max_points": 1}
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit(limits, "points", sdk=tenant)
        finally:
            tenant.close()

    def test_object_subject_mix_402_at_cap(self, reg_sdk, tmp_path):
        """Mixed object+subject nodes consume the cap together."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_object("o1", objectKind="org")
            tenant.create_subject("s1", subjectKind="person")
            # 2 nodes → at/over a cap of 2 → 402
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit({"org_id": tid, "max_points": 2},
                                   "points", sdk=tenant)
            # 2 nodes below a cap of 3 → passes
            enforce_org_limit({"org_id": tid, "max_points": 3},
                               "points", sdk=tenant)
        finally:
            tenant.close()

    def test_point_quota_unchanged_for_point_only_graph(
            self, reg_sdk, tmp_path):
        """Regression: the Point predicate is untouched — a pure-point
        graph counts exactly as before (#1911 must not move the points cap
        for existing point-only usage)."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_point("statement", "one")
            tenant.create_point("statement", "two")
            tenant.create_point("statement", "three")
            assert count_org_usage(tid, "points", sdk=tenant) == 3
            # at/over cap → 402; below cap → passes (unchanged semantics)
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit({"org_id": tid, "max_points": 3},
                                   "points", sdk=tenant)
            enforce_org_limit({"org_id": tid, "max_points": 4},
                               "points", sdk=tenant)
        finally:
            tenant.close()

    def test_capture_minted_object_counts_against_quota(
            self, reg_sdk, tmp_path):
        """#1911 + capture interplay: the deterministic _V2SessionMock mints
        a non-episodic Object entity ('the strategy') alongside the extracted
        value point — the Object now counts against the quota too (pre-fix
        the Points-only count saw the value point but never the entity)."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.capture_session(
                [{"role": "user", "content": "okay"}], session_id="cap_s1")
            # 1 value point + 1 minted Object = 2 (the Session/turn Point/
            # Event/Source nodes stay episodic and are excluded)
            assert count_org_usage(tid, "points", sdk=tenant) == 2
        finally:
            tenant.close()


class TestIsEpisodicBackfill:
    """R-18 (DE2E-7 legacy fixture): legacy nodes lack is_episodic → the
    one-query backfill (graph-scripts/backfill_is_episodic.py) stamps them →
    the points branch counts them as episodic (no false 402)."""

    def _make_tenant(self, reg_sdk, tmp_path):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        return TortoiseSDK(
            os.path.join(tmp_path, "quota.db"), namespace=_find_org_id(reg_sdk))

    def test_legacy_nodes_backfilled_are_episodic(self, reg_sdk, tmp_path):
        from backfill_is_episodic import run_backfill
        tid = _find_org_id(reg_sdk)
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
            assert count_org_usage(tid, "points", sdk=tenant) == 3
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit(
                    {"org_id": tid, "max_points": 3}, "points", sdk=tenant)
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
            assert count_org_usage(tid, "points", sdk=tenant) == 1
            enforce_org_limit(
                {"org_id": tid, "max_points": 3}, "points", sdk=tenant)
            # Sessions branch still counts the (now-flagged) Session
            assert count_org_usage(tid, "sessions", sdk=tenant) == 1
        finally:
            tenant.close()

    def test_new_capture_writes_carry_flag(self, reg_sdk, tmp_path):
        """Seam note (MECE ISSUE 3): regex-fallback captures write is_episodic
        on Session + turn Points going forward — unflagged new captures would
        re-introduce the false-402 this fix eliminates."""
        from backfill_is_episodic import run_backfill
        tid = _find_org_id(reg_sdk)
        tenant = self._make_tenant(reg_sdk, tmp_path)
        try:
            proj = tenant._get_proj()
            tenant.capture_session(
                [{"role": "user", "content": "okay"}], session_id="new_s1")
            # Nothing to backfill — new capture nodes already carry the flag
            # (turn Point, Event, and the session-provenance Source all stamp
            # is_episodic at creation; _link_source stamps session refs, #1486).
            assert run_backfill(proj) == {"matched": 0, "updated": 0}
            # The turn Point is episodic; the ONE v2-extracted value point
            # (deterministic _V2SessionMock, non-episodic by design) plus the
            # v2-minted Object entity ('the strategy') are counted against
            # the quota (#1350/#1486; the entity count is #1911 — capture-
            # minted entities are non-episodic and previously invisible to
            # the Points-only count).
            assert count_org_usage(tid, "points", sdk=tenant) == 2
            # Session counted by the sessions branch
            assert count_org_usage(tid, "sessions", sdk=tenant) == 1
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
        from tortoise.quota import (  # noqa: I001
            MAX_ENTITIES, MAX_OPERATORS, MAX_PAYLOAD_POINTS,
            MAX_VALUE_POINTS_PER_SESSION,
        )
        assert MAX_PAYLOAD_POINTS == 50
        assert MAX_ENTITIES == 500
        assert MAX_OPERATORS == 500
        # R-decoupling: the Layer-1 raw cap is deliberately a SEPARATE named
        # constant from the budget ceiling (same numeric value — the name
        # prevents wiring the wrong 50, plan §4.4).
        assert MAX_PAYLOAD_POINTS == MAX_VALUE_POINTS_PER_SESSION["ceiling"]  # noqa: SIM300
        assert MAX_PAYLOAD_POINTS == 50  # explicit value, not derived


# ── The ask-lane bounded-execution cluster (#1987 P2, retained by #3849) ───
# Moved here from tests/test_ask_api.py, which #3849 deleted with the REST
# surface. `run_ask_bounded` and the exec-floor semantics it implements are
# RETAINED in tortoise/quota.py (documented RETIRED-but-not-purged pending
# #3849 §7 D5) — so the guarantee stays pinned rather than going untested
# with the file that happened to host it. It needs no REST surface: it
# drives `run_ask_bounded` directly.

def test_ask_exec_floor_guarantees_execution(monkeypatch):
    """#1987 P2: a queued ask released before the queue-wait cap gets a real
    execution window (completes) rather than a near-zero remaining budget; a
    request released past the cap 504s at acquire WITHOUT starting the call
    (no wasted model call)."""
    import asyncio

    import tortoise.quota as quota_mod

    monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 3.0)
    monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 1.0)

    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return "ok"

    async def _queue_and_release(release_at: float):
        loop = asyncio.get_running_loop()
        sem = quota_mod._ask_state_for_loop(loop)["sem"]
        # hold all 8 global slots → the ask queues behind the semaphore
        for _ in range(quota_mod._ASK_GLOBAL_SEMAPHORE_SIZE):
            await sem.acquire()
        task = asyncio.ensure_future(quota_mod.run_ask_bounded(_fn, None))
        await asyncio.sleep(release_at)
        for _ in range(quota_mod._ASK_GLOBAL_SEMAPHORE_SIZE):
            sem.release()
        return await task

    # released at ~1.5s (< the 2.0s queue-wait cap) → acquires, remaining
    # ~1.5s >= the 1.0s execution floor → completes (no bogus 504)
    assert asyncio.run(_queue_and_release(1.5)) == "ok"
    assert calls["n"] == 1

    # control: released at ~2.5s (> the 2.0s cap) → 504 at acquire, the call
    # never starts (no wasted model call)
    calls["n"] = 0
    with pytest.raises(quota_mod.AskBoundedTimeoutError):
        asyncio.run(_queue_and_release(2.5))
    assert calls["n"] == 0


# ── #4355: the single-slot predicate must agree with the count, both lanes ──

class TestApiKeySlotParity:
    """`api_key_occupies_slot` is NOT a fourth count — it is the api_keys cap
    predicate applied to ONE id, and the rotate primitive credits a slot on the
    strength of it. The credit is only sound if the predicate accepts EXACTLY
    the rows `_count_resource(org, 'api_keys')` charges for. These tests pin
    that identity on both lanes over the whole state space that matters:
    live-durable, live-bootstrap (cap-exempt), revoked, expired, and the
    legacy NULL `created_via` (durable, must count — fail-closed).
    """

    def test_registry_lane_count_equals_accepted_ids(self, reg_sdk):
        from tortoise.quota import _count_resource, api_key_occupies_slot

        tid = _find_org_id(reg_sdk)
        reg = reg_sdk._get_registry()
        now = datetime.now(UTC)
        states = {
            "live-durable": {"revoked_at": None, "created_via": "dashboard",
                             "expires_at": None},
            "live-bootstrap": {"revoked_at": None, "created_via": "bootstrap",
                               "expires_at": (now + timedelta(hours=24)).isoformat()},
            "live-legacy-null": {"revoked_at": None, "created_via": None,
                                 "expires_at": None},
            "revoked": {"revoked_at": now.isoformat(), "created_via": "dashboard",
                        "expires_at": None},
            "expired": {"revoked_at": None, "created_via": "dashboard",
                        "expires_at": (now - timedelta(days=1)).isoformat()},
            "expired-bootstrap": {"revoked_at": None, "created_via": "bootstrap",
                                  "expires_at": (now - timedelta(days=1)).isoformat()},
        }
        for name, s in states.items():
            reg.query(
                "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$h, "
                "key_prefix:$kp, created_by:'u1', created_at:$ca, "
                "revoked_at:$ra, expires_at:$ea, created_via:$cv})",
                params={"id": f"k-{name}", "tid": tid, "h": f"h-{name}",
                        "kp": f"p-{name}", "ca": now.isoformat(),
                        "ra": s["revoked_at"], "ea": s["expires_at"],
                        "cv": s["created_via"]},
            )

        count = _count_resource(tid, "api_keys", sdk=reg_sdk)
        accepted = [k for k in states if api_key_occupies_slot(tid, f"k-{k}",
                                                              sdk=reg_sdk)]
        assert count == len(accepted), (
            f"count={count} but the slot predicate accepts {accepted}"
        )
        assert set(accepted) == {"live-durable", "live-legacy-null"}, (
            "the predicate must charge live durable + live legacy-NULL rows "
            f"and nothing else, got {accepted}"
        )

    def test_registry_lane_unknown_and_empty_ids_are_false(self, reg_sdk):
        from tortoise.quota import api_key_occupies_slot

        tid = _find_org_id(reg_sdk)
        assert api_key_occupies_slot(tid, "no-such-key", sdk=reg_sdk) is False
        assert api_key_occupies_slot("", "k", sdk=reg_sdk) is False
        assert api_key_occupies_slot(tid, "", sdk=reg_sdk) is False

    def test_supabase_lane_count_equals_accepted_ids(self, monkeypatch):
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane
        from tortoise.quota import _count_resource, api_key_occupies_slot

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://slot-parity.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        now = datetime.now(UTC)
        tid = "team-slot-parity"
        rows = [
            {"id": "k-live-durable", "org_id": tid, "created_via": "dashboard",
             "revoked_at": None, "expires_at": None},
            {"id": "k-live-bootstrap", "org_id": tid, "created_via": "bootstrap",
             "revoked_at": None,
             "expires_at": (now + timedelta(hours=24)).isoformat()},
            {"id": "k-live-legacy-null", "org_id": tid, "created_via": None,
             "revoked_at": None, "expires_at": None},
            {"id": "k-revoked", "org_id": tid, "created_via": "dashboard",
             "revoked_at": now.isoformat(), "expires_at": None},
            {"id": "k-expired", "org_id": tid, "created_via": "dashboard",
             "revoked_at": None,
             "expires_at": (now - timedelta(days=1)).isoformat()},
        ]
        fake.seed("api_keys", rows)

        count = _count_resource(tid, "api_keys")
        accepted = [r["id"] for r in rows if api_key_occupies_slot(tid, r["id"])]
        assert count == len(accepted), (
            f"count={count} but the slot predicate accepts {accepted}"
        )
        assert set(accepted) == {"k-live-durable", "k-live-legacy-null"}, accepted

    def test_credit_widens_the_gate_by_exactly_one(self, reg_sdk):
        """`enforce_org_limit(slot_credit=1)` moves the boundary by exactly
        one slot — it is a credit, not an exemption: at 2/2 the plain gate
        refuses, the credited gate admits, and a THIRD live key makes the
        credited gate refuse again.”"""
        from tortoise.quota import _count_resource

        tid = _find_org_id(reg_sdk)
        reg = reg_sdk._get_registry()
        limits = {"org_id": tid, "max_api_keys": 2}  # free tier

        def _seed(kid: str) -> None:
            reg.query(
                "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$h, "
                "key_prefix:$kp, created_by:'u1', created_at:$ca, "
                "revoked_at:NULL, expires_at:NULL, created_via:'dashboard'})",
                params={"id": kid, "tid": tid, "h": f"h-{kid}",
                        "kp": f"p-{kid}", "ca": datetime.now(UTC).isoformat()},
            )

        enforce_org_limit(limits, "api_keys", sdk=reg_sdk)          # 0/2
        _seed("slot-1")
        enforce_org_limit(limits, "api_keys", sdk=reg_sdk)          # 1/2
        assert _count_resource(tid, "api_keys", sdk=reg_sdk) == 1
        _seed("slot-2")
        with pytest.raises(QuotaExceededError) as unc:                    # 2/2 → over
            enforce_org_limit(limits, "api_keys", sdk=reg_sdk)
        # #4614: the refusal carries the numbers the gate COMPARED, so a
        # caller can act on it without parsing the message. `used` is the
        # counted value at the raise, and `resource`/`limit` name what was
        # exhausted.
        assert (unc.value.resource, unc.value.used, unc.value.limit) == (
            "api_keys", 2, 2)
        enforce_org_limit(limits, "api_keys", sdk=reg_sdk,
                          slot_credit=1)                              # 2-1 → ok
        assert _count_resource(tid, "api_keys", sdk=reg_sdk) == 2
        _seed("slot-3")
        with pytest.raises(QuotaExceededError) as credited:           # 3-1 → over
            enforce_org_limit(limits, "api_keys", sdk=reg_sdk, slot_credit=1)
        # The payload follows the CREDIT (`count - slot_credit`), not the raw
        # count — it reports the left-hand side of the comparison that
        # actually produced the refusal. Reporting the raw 3 here would
        # describe a decision the gate did not make.
        assert (credited.value.used, credited.value.limit) == (2, 2)
        # The credit is applied LITERALLY (`count - slot_credit >= limit`) — it
        # is not clamped, so ONLY the caller's occupancy proof bounds it at 1.
        # Over-crediting admits (the free-slot hazard); under-crediting
        # over-tightens (fail-closed). Pinned here so the shape cannot drift
        # silently; the guard itself is the single caller + test_rotate_key's
        # revoked/expired/bootstrap refusals.
        enforce_org_limit(limits, "api_keys", sdk=reg_sdk, slot_credit=4)


# ── #4614: the refusal is a distinguishable STATE, not a sentence ──────────


class TestStructuredRefusal:
    """A quota refusal must carry a machine-readable category (#4614).

    The gate answered with a bare prose ``detail``, so no caller could tell a
    quota refusal from any other 402 without matching the message text — and
    our own clients are documented as forbidden from doing exactly that
    (``capture_spool.classify_failure``: *"a capacity/billing refusal is a
    category, not a string"*). These pin the house payload shape (#2789,
    `_one_free_org_detail`) so the category cannot be flattened back to prose.
    """

    def test_carries_the_code_and_the_numbers(self):
        exc = QuotaExceededError(
            "Team points limit reached (25000). Upgrade your plan to increase it.",
            resource="points", used=24965, limit=25000, estimate=1044)
        payload = quota_refusal_payload(exc)
        assert payload["code"] == QUOTA_REFUSAL_CODE == "quota_exceeded"
        assert payload["resource"] == "points"
        assert payload["used"] == 24965
        assert payload["limit"] == 25000
        assert payload["estimate"] == 1044
        # The prose survives verbatim inside the payload: a human reader — and
        # the dashboard's `Last attempt — <detail>` sub-line, which flattens a
        # dict detail to its `message` — loses nothing.
        assert payload["message"] == str(exc)

    def test_omits_only_what_the_raise_site_did_not_know(self):
        """An absent number beats a fabricated one.

        The dashboard falls back to `/v1/team`'s allowance when a refusal
        carries none (`keyAllowance.capLimitFrom`), so inventing a figure
        would REPLACE a real one with a lie. But a present 0 is a real value
        (a 0-cap plan refuses every write) and must be kept — dropping it
        would hide an absolute cap.
        """
        payload = quota_refusal_payload(
            QuotaExceededError("Team graphs limit reached (1)."))
        assert payload == {
            "code": QUOTA_REFUSAL_CODE,
            "message": "Team graphs limit reached (1).",
        }
        zero = quota_refusal_payload(
            QuotaExceededError("Team points limit reached (0).",
                               resource="points", used=0, limit=0))
        assert zero["used"] == 0 and zero["limit"] == 0

    def test_a_subclass_category_survives_the_generic_builder(self):
        """The code lives on the EXCEPTION, so a generic caller cannot
        mislabel a subclass's refusal as the base category.

        A cohort SPEND cap and a plan NODE cap are both 402s. Reading the
        first as `quota_exceeded` would send the user to buy a bigger plan
        that cannot lift it — which is the whole reason the payload exists.
        """
        from tortoise.cohort_cost import (
            COHORT_COST_REFUSAL_CODE,
            CohortCostCapExceeded,
        )

        payload = quota_refusal_payload(
            CohortCostCapExceeded("cohort spend cap reached"))
        assert payload["code"] == COHORT_COST_REFUSAL_CODE == "cohort_cost_cap"
        assert payload["code"] != QUOTA_REFUSAL_CODE
        # It names no resource/limit — and must not invent one.
        assert "resource" not in payload and "limit" not in payload
        assert payload["message"] == "cohort spend cap reached"

    def test_the_fields_are_keyword_only(self):
        """A raise site that knows only its message still works.

        Positional construction would let a message be silently dropped into a
        numeric field by a caller who did not read the signature.
        """
        with pytest.raises(TypeError):
            QuotaExceededError("msg", "points", 1, 2)

    def test_the_payload_stringifies_to_the_message(self):
        """#4614: a consumer that only has `str(detail)` must see the sentence.

        The MCP capture twin reads `getattr(e, "detail", ...)` and stringifies
        it. Editing that handler would red `surface-guard` (CONTRIBUTING: add
        response fields in the assembly layer, not inside a tool function), so
        the payload answers `str()` here instead. JSON serialization must be
        unchanged — `json.dumps` still emits an object.
        """
        import json

        payload = quota_refusal_payload(QuotaExceededError(
            "Team points limit reached (1). Upgrade your plan to increase it.",
            resource="points", used=1, limit=1))
        assert isinstance(payload, dict)
        assert str(payload) == (
            "Team points limit reached (1). Upgrade your plan to increase it.")
        assert json.loads(json.dumps(payload)) == {
            "code": QUOTA_REFUSAL_CODE,
            "resource": "points",
            "used": 1,
            "limit": 1,
            "message": "Team points limit reached (1). Upgrade your plan "
                       "to increase it.",
        }
