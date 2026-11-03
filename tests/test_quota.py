"""Tests for tortoise.quota (#329, #683)."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tortoise.quota import (
    LIMIT_CONTACT,  #5425 shared contact path
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
        # #5425: the shared gate is the surface MOST customers hit, so it names
        # the human route too ("if they want more they need to speak with us").
        assert payload["message"] == (
            "Team points limit reached (1). Upgrade your plan to increase it."
            + LIMIT_CONTACT)
        assert "support@premiselabs.co" in payload["message"]
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

    def test_missing_limit_key_fails_closed(self, caplog):
        """#310 GAP-B: a MISSING limit key is fail-closed; present-None is not.

        THE HOLE THIS CLOSES (measured by the #9567 capstone): the suite had no
        test of the missing-key branch, so mutating its
        ``raise QuotaCheckError(...)`` to ``return`` left this file GREEN — a
        fail-OPEN regression of the exact guard the branch exists to be. The
        two-line pair below is what makes that mutation red: same dict, one key
        REMOVED (refused) versus PRESENT-and-None (unlimited, allowed).

        Nothing is counted: the branch fires before the count, so no sdk.
        """
        import logging
        with pytest.raises(QuotaCheckError, match="missing max_points"):
            enforce_org_limit({"org_id": "team1"}, "points")
        enforce_org_limit({"org_id": "team1", "max_points": None}, "points")
        # ALERTING (#4051 review round 5): this refusal is a DATA FAULT that
        # refuses every write, and it raised silently — `_check_org_limit`
        # documents that a refusal is logged with org_id and resource, and then
        # demoted the whole class to DEBUG, so the fault was ops-invisible. The
        # pin names the FIELD, not the shared prefix, so a mutant that logs a
        # different key stays green only if it really logs this one. Mutation
        # REDs it: drop the `_logger.error` at the missing-key raise site.
        errors = [r.getMessage() for r in caplog.records
                  if r.levelno >= logging.ERROR]
        assert any("quota limit missing (fail-closed)" in m
                   and "resource=points" in m and "max_points" in m
                   for m in errors), errors

    def test_unreadable_limit_fails_closed(self, caplog):
        """#4051: a limit that cannot be READ is refused, never "unlimited".

        The limit is read from the org's OWN source (here `max_points`), so a
        corrupt value must not degrade into no limit at all. WHICH lanes arrive
        raw, and which die earlier, is stated ONCE — in `quota.enforce_org_limit`'s
        SCOPE note — and deliberately NOT restated here: this docstring used to
        carry a second copy, and it contradicted both that note and itself (it
        named a lane as never reaching this gate and then, one clause later, as
        the lane that reaches it raw). A copy is how the two drifted apart.

        Mutation REDs this: drop the validation and let the non-numeric value
        reach the comparison — pre-change it raised a bare `TypeError`, refused
        by accident, naming nothing, and escaping the documented
        `QuotaCheckError` contract every caller handles.
        """
        with pytest.raises(QuotaCheckError) as exc_info:
            enforce_org_limit({"org_id": "t", "max_points": "unreadable"},
                             "points")
        assert "max_points" in str(exc_info.value), (
            f"the refusal must name the unreadable surface: {exc_info.value}")
        assert "unreadable" in str(exc_info.value)
        # A float-FORMATTED string is the SAME refusal, not a silent truncation:
        # `int("1000.0")` raises ValueError, so it never reaches the
        # comparison. Pinned because the docstring states the three shapes
        # explicitly, and a later "helpful" widening to `int(float(x))` would
        # honour it instead — turning a stored "1000.0" from a named refusal
        # into a cap of 1000 (review round 6 on #4051).
        with pytest.raises(QuotaCheckError) as dotted_exc:
            enforce_org_limit({"org_id": "t", "max_points": "1000.0"},
                               "points")
        assert "max_points" in str(dotted_exc.value), dotted_exc.value
        # A NON-FINITE value is the same class: `int(float('inf'))` raises
        # OverflowError, which must become this refusal rather than an unmapped
        # 500 that names nothing (review round 1 on #4051). Mutation REDs it:
        # narrow the except clause back to (TypeError, ValueError).
        with pytest.raises(QuotaCheckError) as inf_exc:
            enforce_org_limit({"org_id": "t", "max_points": float("inf")},
                              "points")
        assert "max_points" in str(inf_exc.value)
        # The field named must be the field READ — not a hardcoded
        # `max_points` (review round 2 on #4051: with only `points` cases, a
        # mutant that hardcodes the key stayed GREEN, so a corrupt `max_users`
        # would be reported as `max_points`). Mutation REDs this: replace
        # `{limit_key}` with the literal `max_points` in the raise above.
        with pytest.raises(QuotaCheckError) as users_exc:
            enforce_org_limit({"org_id": "t", "max_users": "unreadable"},
                              "users")
        users_msg = str(users_exc.value)
        assert "max_users" in users_msg, users_msg
        assert "max_points" not in users_msg, users_msg
        # The `documents` branch DERIVES its cap from the same field, so it must
        # refuse a non-finite value the same way (it used to escape as a bare
        # `OverflowError` — the parity the code comment now claims).
        with pytest.raises(QuotaCheckError):
            enforce_org_limit({"org_id": "t", "max_points": float("inf")},
                              "documents")
        # ALERTING is part of the fail-closed contract (#686): this refusal is
        # raised BEFORE the count, so it cannot rely on `_count_resource`'s
        # ERROR log — and the pre-change crash did reach `logger.exception` +
        # Sentry, so a silent refusal here would downgrade a money-gate data
        # fault to an ops-invisible 500 (review round 4 on #4051).
        # ⛔ PIN THE FIELD, NOT THE SHARED PREFIX: this list also contains the
        # `documents` branch's log from the call above, so a prefix-only match
        # is satisfied by the WRONG raise site — verified by mutation, deleting
        # the GENERIC `_logger.error` left a prefix-only assertion GREEN (review
        # round 3 on #4051). Naming `resource=points` makes each site pin its
        # own record. Mutation REDs it: drop the `_logger.error` at the generic
        # raise site.
        import logging
        errors = [r.getMessage() for r in caplog.records
                  if r.levelno >= logging.ERROR]
        assert any("quota limit unreadable (fail-closed)" in m
                   and "resource=points" in m for m in errors), errors
        # The `documents` branch is a SEPARATE raise site and logs separately —
        # pin it on its own, by clearing first. Without this, the generic-path
        # log above satisfies any later assertion and a silent documents
        # branch would stay green (review round 3 on #4051: `_check_org_limit`
        # documents the documents surface as logged, and it was not).
        caplog.clear()
        with pytest.raises(QuotaCheckError):
            enforce_org_limit({"org_id": "t", "max_points": float("inf")},
                              "documents")
        docs_errors = [r.getMessage() for r in caplog.records
                       if r.levelno >= logging.ERROR]
        assert any("quota limit unreadable (fail-closed)" in m
                   and "documents" in m for m in docs_errors), docs_errors


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


# ── #1726 Slice 1: documents resource (derived-constant cap) ─────
# (#4282: the #947 sessions-branch class above went with `capture_session`;
#  the count property it pinned still holds at test_quota.py's hosted pins
#  and by test_issue_4010_sessions_unlimited.)

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

    # ── #5140 P1: the meter key is not caller-demotable ────────────────────

    def test_document_kind_cannot_be_demoted_out_of_the_meter(
            self, reg_sdk, tmp_path):
        """#5140 P1 (the B3 *effect*, reached off the declared B3 route).

        B3 declares the route `DocumentCreated`; the DECLARED EFFECT is
        "documents stop being metered". The effect was reachable without that
        route at all, because the meter key is caller-writable on the two
        public `:Source` surfaces: `_SOURCE_EXTRA_PROPS` (the `create_source`
        passthrough) and `_SOURCE_NODE_PROPS` (the generic `update_entity`
        surface). One call demoted an existing document to `transcript` — or
        nulled the genre — and the node left the metered set, freeing a slot
        per document, repeatably. The cap did not hold.

        MEASURED on the pre-fix code, 3 seeded document Sources: the meter read
        3 -> 2 via `create_source(did, 'document', documentKind='transcript')`
        and 2 -> 1 via `update_entity(did, documentKind='transcript')` — both
        after `enforce_org_limit` had already refused at the derived cap, i.e.
        the refusal the previous test pins was bypassable one call later.
        """
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            reg_sdk._get_registry().query(
                "MATCH (t:Team {id:$id}) SET t.max_points = 1",
                params={"id": tid})
            for i in range(10):
                self._seed_doc(tenant, i, kind="report")
            limits = resolve_org_limits(tid)
            assert count_org_usage(tid, "documents", sdk=tenant) == 10
            # precondition: the derived cap is genuinely reached
            with pytest.raises(QuotaExceededError, match="documents limit"):
                enforce_org_limit(limits, "documents", sdk=tenant)

            # ⛔ BOTH spellings on BOTH caller surfaces. The document writer
            # reads `$dk` from the snake spelling and OVERWRITES the camelCase
            # genre, so admitting the snake form would hide the same demotion
            # one document-path write later.
            attempts = {
                "create_source/documentKind": (lambda: tenant.create_source(
                    "doc_0", "document", documentKind="transcript"),
                    "metered `documents` set"),
                "create_source/document_kind": (lambda: tenant.create_source(
                    "doc_1", "document", document_kind="transcript"),
                    "inert second spelling"),
                "update_entity/documentKind": (lambda: tenant.update_entity(
                    "doc_2", documentKind="transcript"),
                    "metered `documents` set"),
                "update_entity/document_kind": (lambda: tenant.update_entity(
                    "doc_3", document_kind="transcript"),
                    "inert second spelling"),
                # `None` through `SET n += $p` REMOVES the property, so the
                # generic surface's null is a demotion too (the passthrough
                # skips null values — see `_persist_extra_props`).
                "update_entity/documentKind=None": (lambda: tenant.update_entity(
                    "doc_4", documentKind=None), "metered `documents` set"),
                # ⛔ #5140 REVIEW CYCLE 3 — the snake spelling is refused for
                # EVERY value on a metered document, not only the demoting one.
                # Its ONLY possible effect there is to put the genre on the
                # node in a second place (`document_kind` next to
                # `documentKind`), which is what the rule forbids. The
                # value-scoped rule admitted `document_kind='brief'` (a real
                # second home) and refused `document_kind=None` (the one snake
                # write that cannot demote, and the cleanup of that home) —
                # both arms are pinned here: they FAIL on the value-scoped
                # predicate, the first with DID NOT RAISE.
                "update_entity/document_kind='brief' (2nd home)": (
                    lambda: tenant.update_entity("doc_5", document_kind="brief"),
                    "inert second spelling"),
                "update_entity/document_kind=None": (
                    lambda: tenant.update_entity("doc_6", document_kind=None),
                    "inert second spelling"),
                "create_source/document_kind='report' (2nd home)": (
                    lambda: tenant.create_source(
                        "doc_7", "document", document_kind="report"),
                    "inert second spelling"),
            }
            for label, (attempt, expected) in attempts.items():
                with pytest.raises(ValueError, match=expected):
                    attempt()
                assert count_org_usage(tid, "documents", sdk=tenant) == 10, (
                    f"{label} escaped the documents meter")
            kinds = tenant._get_proj().g.query(
                "MATCH (s:Source) WHERE s.documentKind = 'transcript' "
                "RETURN count(s)").result_set[0][0]
            assert kinds == 0, f"{kinds} document(s) demoted to transcript"
            # …and NO second home was created by any of the refusals: the
            # genre lives in `documentKind` only.
            second_home = tenant._get_proj().g.query(
                "MATCH (s:Source) WHERE s.document_kind IS NOT NULL "
                "RETURN count(s)").result_set[0][0]
            assert second_home == 0, (
                f"{second_home} :Source node(s) carry `document_kind` beside "
                f"`documentKind` — the second home the rule refuses")

            # ⛔ NON-OVER-REACH: the guard must not turn the surfaces into a
            # no-op for the genre axis. (a) one metered genre -> another still
            # lands, and (b) SETTING a genre where none is stored is how a
            # document `:Source` is minted through these surfaces at all.
            tenant.create_source("doc_0", "document", documentKind="brief")
            assert tenant._get_proj().g.query(
                "MATCH (s:Source {url:'doc_0'}) RETURN s.documentKind"
            ).result_set[0][0] == "brief"
            self._seed_doc(tenant, 20, kind=None)   # provenance Source
            tenant.create_source("doc_20", "document", documentKind="report")
            assert tenant._get_proj().g.query(
                "MATCH (s:Source {url:'doc_20'}) RETURN s.documentKind"
            ).result_set[0][0] == "report"
            assert count_org_usage(tid, "documents", sdk=tenant) == 11
        finally:
            tenant.close()

    def test_document_writer_cannot_demote_out_of_the_meter(
            self, reg_sdk, tmp_path):
        """#5140 P1, review cycle 2 — the THIRD door: the document writer itself.

        The two guarded surfaces above are not the only writers of the genre.
        `_upsert_document`'s fixed clause used a plain
        `s.documentKind=coalesce($dk, s.documentKind, '')`, and `$dk` is
        caller-chosen, so the DEMOTION was still one call away — from two
        documented routes:

          * ``EventAPI.add_document(<existing doc id>, document_kind=...)`` —
            the doc id is the caller's `:Source` MERGE key;
          * ``sdk._doc_write(frontmatter, <derived doc id>, ...)`` — the
            ``POST /v1/index/docs`` re-index path, whose id is DERIVED from the
            file path, so re-indexing one file with ``type: transcript`` hits
            the SAME node, journals the record, and survives ``rebuild_all``.

        MEASURED before the fix (meter 3 -> 2 -> 1, one call each). The clause
        is the fix rather than a Python guard because that same statement IS
        the journal replay fold: the drop must be total, so a rebuild over a
        pre-guard record re-materialises the metered genre instead of aborting.

        ⛔ #5140 REVIEW CYCLE 3: the fold's drop was SILENT to the tenant — both
        calls below returned normally (the index job even counted the file as
        re-indexed) while the graph kept the old genre, so a genuine genre
        correction became unobservable and no caller could tell. The producers
        now REFUSE loudly, before the journal append, via
        `quota.refuse_document_writer_demotion`; the CASE stays as the
        replay-safe fold (`test_a_pre_guard_demotion_replays_to_the_metered_genre`
        below). These calls FAIL on the pre-cycle-3 code with DID NOT RAISE.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            for i in range(3):
                self._seed_doc(tenant, i, kind="report")
            assert count_org_usage(tid, "documents", sdk=tenant) == 3
            events = str(tmp_path / "dw_events.jsonl")
            api = EventAPI(EventLog(events), initiated_by="extractor",
                           projection=tenant._get_proj())

            # (1) EventAPI.add_document on an EXISTING document's id.
            with pytest.raises(ValueError, match="metered `documents` set"):
                api.add_document("doc_1", "doc 1", document_kind="transcript")
            # (2) the index path's own writer, same file-derived id.
            with pytest.raises(ValueError, match="metered `documents` set"):
                tenant._doc_write({"type": "transcript"}, "doc_0", "doc 0",
                                  str(tmp_path / "doc_0.md"), None)

            # ⛔ The refusal must land BEFORE the commit point: a journaled
            # demotion would replay, and a `/v1/index/docs` job would report a
            # write that never happened.
            import os
            assert not os.path.exists(events) or os.path.getsize(events) == 0, (
                "a refused document write was JOURNALED — a rebuild would "
                "replay it")
            kinds = tenant._get_proj().g.query(
                "MATCH (s:Source) WHERE s.url IN ['doc_0','doc_1'] "
                "RETURN s.url, s.documentKind ORDER BY s.url").result_set
            assert [list(r) for r in kinds] == [
                ["doc_0", "report"], ["doc_1", "report"]], (
                f"a metered document was demoted by the document writer: {kinds!r}")
            assert count_org_usage(tid, "documents", sdk=tenant) == 3, \
                "the document writer demoted a document out of the meter"

            # ⛔ NON-OVER-REACH, on the same writer: a fresh transcript document
            # is still mintable (nothing metered stored to demote), a
            # metered-to-metered retarget still lands, and re-writing the genre
            # a transcript already carries is a no-op, not a refusal.
            api.add_document("doc_new", "fresh transcript",
                             document_kind="transcript")
            api.add_document("doc_2", "doc 2", document_kind="brief")
            api.add_document("doc_new", "fresh transcript",
                             document_kind="transcript")
            kinds2 = tenant._get_proj().g.query(
                "MATCH (s:Source) WHERE s.url IN ['doc_2','doc_new'] "
                "RETURN s.url, s.documentKind ORDER BY s.url").result_set
            assert [list(r) for r in kinds2] == [
                ["doc_2", "brief"], ["doc_new", "transcript"]], kinds2
            assert count_org_usage(tid, "documents", sdk=tenant) == 3
        finally:
            tenant.close()

    def test_a_pre_guard_demotion_replays_to_the_metered_genre(
            self, reg_sdk, tmp_path):
        """#5140: the fold must stay TOTAL over a journal that predates the fix.

        The producers refuse a demotion, but the SAME clause is the replay fold,
        and a journal written BEFORE the guard can hold a `DocumentCreated` that
        demotes its document. `rebuild_all` over such a log must re-materialise
        the metered genre (fail-closed: over-counts) and must NOT raise —
        refusing at replay would abort every rebuild of that log.

        FAILS on a fold that raises (rebuild raises) and on the pre-#5140
        `coalesce($dk, …)`, which replays the demotion and reads 'transcript'.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            events = str(tmp_path / "replay_events.jsonl")
            api = EventAPI(EventLog(events), initiated_by="extractor",
                           projection=tenant._get_proj())
            api.add_document("doc_r", "Report doc", document_kind="report")
            # A LEGACY (pre-guard) record that demotes the same node.
            EventLog(events).append({"type": "DocumentCreated", "id": "doc_r",
                                     "title": "Report doc",
                                     "document_kind": "transcript"})
            assert count_org_usage(tid, "documents", sdk=tenant) == 1
            tenant._get_proj().rebuild_all(
                str(tmp_path), confirm_destructive=True)
            kinds = tenant._get_proj().g.query(
                "MATCH (s:Source {url:'doc_r'}) RETURN s.documentKind"
            ).result_set
            assert kinds and kinds[0][0] == "report", (
                f"a pre-guard demotion re-materialised as {kinds!r} — the "
                f"rebuild must fail CLOSED (keep the metered genre)")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1, \
                "the replayed demotion dropped the document out of the meter"
        finally:
            tenant.close()

    def test_the_source_genre_guard_costs_nothing_without_the_key(
            self, reg_sdk, tmp_path, monkeypatch):
        """#5140 (review cycle 2, P2): the guard is off the hot write path.

        `create_source` is THE `:Source` writer (`_index_source_merge` calls it
        per indexed file) and almost no caller supplies a genre, so the stored-
        kind read MUST be gated on the key's PRESENCE. An unconditional read
        puts 1-3 graph round trips on every capture/connector write for a key
        nobody sent. FAILS on the unconditional revision (the no-genre call
        below records a read).
        """
        from tortoise.sdk import TortoiseSDK

        tid, tenant = self._tenant(tmp_path, reg_sdk)
        reads: list[str] = []
        original = TortoiseSDK._stored_source_document_kinds

        def spy(self, g, id_val):
            reads.append(id_val)
            return original(self, g, id_val)

        monkeypatch.setattr(TortoiseSDK, "_stored_source_document_kinds", spy)
        try:
            tenant.create_source("plain_src", "web")
            tenant.create_source("plain_src_2", "web", raw_state="present")
            assert reads == [], (
                f"the genre guard read the graph for writes that carry no "
                f"genre: {reads}")
            # …and it IS engaged when the key is present (the gate is a gate,
            # not a disable).
            tenant.create_source("doc_g", "document", documentKind="report")
            assert reads == ["doc_g"], reads
            assert count_org_usage(tid, "documents", sdk=tenant) == 1
        finally:
            tenant.close()

    def test_demotion_guard_fails_closed_on_every_node_the_write_reaches(
            self, reg_sdk, tmp_path):
        """#5140 P1 (review cycle 4) — the guard must read ALL nodes, not one row.

        `update_entity` writes ``MATCH (n:Source {key:$id}) SET n += $p``, which
        applies to EVERY node sharing the key. An earlier revision of the stored-
        genre reader returned ``rows[0][0]`` of the first matching key, so a
        non-metered sibling that the engine listed first answered for the whole
        set: the guard read 'nothing metered stored', admitted the write, and the
        `SET` demoted the metered sibling out of the cap. MEASURED on that
        revision with this exact shape: meter 1 -> 0.

        FAILS on the first-row reader (the `pytest.raises` below does not
        raise), and it is order-sensitive there by construction — the decoy is
        inserted FIRST so it is the row a first-row reader sees.
        """
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            g = tenant._get_proj().g
            # decoy FIRST: same `id` the write addresses, no genre
            g.query("CREATE (s:Source {id:$i, url:$u, title:'decoy'})",
                    params={"i": "shared5140", "u": "decoy_url"})
            # the metered document, reachable by the SAME key
            g.query("CREATE (s:Source {id:$i, url:$u, title:'doc', "
                    "documentKind:'report'})",
                    params={"i": "shared5140", "u": "shared5140"})
            assert count_org_usage(tid, "documents", sdk=tenant) == 1
            with pytest.raises(ValueError, match="metered `documents` set"):
                tenant.update_entity("shared5140", documentKind="transcript")
            kinds = g.query(
                "MATCH (s:Source) WHERE s.id = 'shared5140' "
                "RETURN s.documentKind ORDER BY s.url").result_set
            assert [r[0] for r in kinds] == [None, "report"], (
                f"a metered sibling was demoted behind a non-metered decoy: "
                f"{kinds!r}")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1, \
                "the multi-node write demoted a document out of the meter"
        finally:
            tenant.close()

    def test_meter_predicate_halves_agree(self, reg_sdk, tmp_path):
        """#5140: the `documents` predicate has ONE definition, in two halves.

        `count_org_usage` counts with `DOCUMENT_METER_PREDICATE` (Cypher) and
        the write guard tests with `is_metered_document_kind` (Python). The
        escape above existed because the rule had a counter and no defender;
        two halves that can drift would put it back, so pin them to each other
        across the values the write paths actually produce (`''` is the
        projection's CREATE default, #5026/D10; `'transcript'` is the capture
        genre; `None` is a kind-less provenance Source).
        """
        from tortoise.quota import is_metered_document_kind
        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            g = tenant._get_proj().g
            for kind in (None, "", "report", "brief", "transcript", "other"):
                g.query("MATCH (s:Source) DETACH DELETE s")
                g.query("CREATE (s:Source {url:'probe5140', documentKind:$k})",
                        params={"k": kind})
                # ⛔ Through the COUNTER, not through a copy of the predicate: the
                # wiring `_count_resource` -> `DOCUMENT_METER_PREDICATE` is what
                # the write guards are pinned against, so a constant that stopped
                # being used would show up here instead of passing silently.
                counted = count_org_usage(tid, "documents", sdk=tenant)
                assert bool(counted) == is_metered_document_kind(kind), (
                    f"predicate halves disagree on {kind!r}: "
                    f"count={counted} python={is_metered_document_kind(kind)}")
                g.query("MATCH (s:Source) DETACH DELETE s")
        finally:
            tenant.close()

    def test_session_commit_transcript_merge_cannot_demote_a_document(
            self, reg_sdk, tmp_path):
        """#5140 P1 (review cycle 3) — the FOURTH writer of the meter key.

        `/v1/sessions/commit` mints the session transcript with
        ``s.documentKind='transcript'`` written UNCONDITIONALLY (a plain `SET`,
        unlike the `coalesce`-guarded `sourceKind`/`ingestedAt` on the same
        statement), and the id it MERGEs on is fully caller-computable:
        ``doc_<sha256(f'{session_id}:{captured_at}')>`` from two request-body
        fields. So a tenant could seed a metered document `:Source` at that url
        and demote it with one commit — repeatable per document, the same P1
        escape this PR closes on the other doors (the defensible reading of
        "one predicate, one defender" is every writer of the key).

        FAILS on the pre-fix statement: the seeded `report` document reads
        `'transcript'` after the commit and the meter drops 1 -> 0.

        NON-OVER-REACH on the same statement: a commit for a session with NO
        document at that id still mints the transcript, and the document's
        OTHER fields still update (the clamp is a genre clamp, not a skipped
        write) — so the negative arm cannot pass by the write being a no-op.
        """
        from tortoise import hosted_api
        from tortoise.ids import content_hash

        tid, tenant = self._tenant(tmp_path, reg_sdk)
        try:
            sid, cap = "sess_clamp_5140", "2026-02-03T04:05:06Z"
            did = f"doc_{content_hash(f'{sid}:{cap}')}"
            tenant.create_source(did, "document", documentKind="report")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1

            payload, plan = _transcript_commit(sid, cap, summary="clamped")
            hosted_api._execute_commit_writes(tenant, payload, plan)

            rows = tenant._get_proj().g.query(
                "MATCH (s:Source {url:$u}) "
                "RETURN s.documentKind, s.title",
                params={"u": did}).result_set
            assert rows and rows[0][0] == "report", (
                f"the /v1/sessions/commit transcript MERGE demoted a metered "
                f"document to {rows!r}")
            assert rows[0][1] == "clamped", (
                "the commit must still write the document's other fields — "
                "the clamp is a GENRE clamp, not a skipped write")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1, \
                "the commit demoted a document out of the meter"

            # non-over-reach: a session with no document at its derived id
            # still mints its transcript (the genre stays writable where none
            # is stored), and a transcript is not metered.
            sid2 = "sess_clamp_5140_fresh"
            did2 = f"doc_{content_hash(f'{sid2}:{cap}')}"
            payload2, plan2 = _transcript_commit(sid2, cap, summary="fresh")
            hosted_api._execute_commit_writes(tenant, payload2, plan2)
            rows2 = tenant._get_proj().g.query(
                "MATCH (s:Source {url:$u}) RETURN s.documentKind, s.sourceKind",
                params={"u": did2}).result_set
            assert rows2 and list(rows2[0]) == ["transcript", "document"], (
                f"a fresh session transcript was not minted: {rows2!r}")
            assert count_org_usage(tid, "documents", sdk=tenant) == 1
        finally:
            tenant.close()


def _transcript_commit(session_id: str, captured_at: str, *, summary: str):
    """A minimal, valid `CommitPayload`/`CommitPlan` for the hosted write
    phase (#5140 review cycle 3): the transcript document and its Session are
    written by `_execute_commit_writes` itself, so an EMPTY reconcile still
    drives the statement under test — the same seam
    `test_commit_supersession_parity.py` uses for the full path."""
    from tortoise.commit_schema import (  # noqa: I001
        BudgetDecision, CommitPayload, CommitPlan, ExtractorInfo,
        ReconcileResult, Telemetry, TelemetryCounts, TelemetryExtractor,
        TelemetryModel,
    )

    payload = CommitPayload(
        session_id=session_id,
        client_commit_id=f"ccid-{session_id}",
        captured_at=captured_at,
        extractor=ExtractorInfo(version="value@1.0.0", mode="byok",
                                calibration_version="v3"),
        summary=summary,
        telemetry=Telemetry(
            extractor=TelemetryExtractor(version="value@1.0.0", mode="byok",
                                         calibration_version="v3"),
            model=TelemetryModel(provider="anthropic", id="m", cfg_hash="h"),
            counts=TelemetryCounts(kept=0, candidate=0, segment=0, window=1,
                                   empty_windows=0),
            keep_ratio=1.0, dedup_hits=0),
    )
    plan = CommitPlan(
        payload=payload, duplicate=False, first_adjudication=True,
        reconcile=ReconcileResult(points=[], entities=[]),
        budget=BudgetDecision(outcome="ok", cumulative_after=0),
    )
    return payload, plan


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



# ── #1977: direct event writes count against the quota cap ────────────────

class TestEventQuota:
    """#1977 (code-review gate on PR #1974, the #1911 fix):
    ``tortoise_create_event`` is ``_quota_gated("points")``, but the points
    count did not include the Event label — a DIRECT call defaults to
    ``is_episodic=None`` and mints a flag-less Event that consumed zero
    quota, so the gate could never refuse (its failure mode was
    indistinguishable from its success mode). The count now includes
    non-episodic Event nodes, mirroring the Point fail-closed R-18 rule;
    capture-minted Events (stamped ``is_episodic: true``) stay excluded."""

    def _tenant(self, reg_sdk, tmp_path):
        from tortoise.sdk import TortoiseSDK  # noqa: I001
        import os
        return TortoiseSDK(
            os.path.join(tmp_path, "quota.db"),
            namespace=_find_org_id(reg_sdk))

    def test_direct_events_count_toward_points(self, reg_sdk, tmp_path):
        """A direct ``create_event`` (the MCP tool's default shape,
        ``is_episodic=None``) is a graph node and consumes the node cap."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_event("kickoff", "meeting")
            tenant.create_event("deploy", "deployment")
            assert count_org_usage(tid, "points", sdk=tenant) == 2
        finally:
            tenant.close()

    def test_direct_event_write_402_at_cap(self, reg_sdk, tmp_path):
        """The gate REFUSES — pre-fix it could never refuse, because the
        count could not see the Event the write was about to add."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_event("e1", "meeting")
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit(
                    {"org_id": tid, "max_points": 1}, "points", sdk=tenant)
        finally:
            tenant.close()

    def test_event_write_passes_below_cap(self, reg_sdk, tmp_path):
        """Below the cap the gate still ADMITS — the fix must not turn the
        points gate into an unconditional refusal for event writers."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant.create_event("e1", "meeting")
            enforce_org_limit(
                {"org_id": tid, "max_points": 2}, "points", sdk=tenant)
        finally:
            tenant.close()

    def test_episodic_event_is_excluded(self, reg_sdk, tmp_path):
        """The discriminator is the ``is_episodic`` flag, not the label —
        an episodic Event (what capture/transcript minting stamps) consumes
        no quota, exactly as an episodic Point does not."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant._get_proj().g.query(
                "CREATE (e:Event {id:'ep1', eventId:'ep1', "
                "eventKind:'AgentSession', is_episodic:true})")
            assert count_org_usage(tid, "points", sdk=tenant) == 0
            # and a flag-less Event beside it counts once
            tenant.create_event("direct", "meeting")
            assert count_org_usage(tid, "points", sdk=tenant) == 1
        finally:
            tenant.close()

    def test_session_index_event_is_charged(self, reg_sdk, tmp_path):
        """The session INDEX path (``_session_event_write``) writes its
        AgentSession Event with ``is_episodic`` UNSET ("no is_episodic on
        the index path"), so it IS charged. Pins the quota.py #1977 comment:
        the Event branch is a flag discriminator, NOT a sessionCaptured/
        AgentSession kind allowlist — only the three writers that explicitly
        stamp ``is_episodic: true`` (sdk/hosted ``capture_session`` mint, the
        hosted commit path) stay excluded."""
        tid = _find_org_id(reg_sdk)
        tenant = self._tenant(reg_sdk, tmp_path)
        try:
            tenant._session_event_write(
                {"sessionId": "idx_s1", "title": "Indexed"},
                "body text", "/tmp/idx_s1.md", "ev_idx1", "idx_s1",
                "hash1", "Indexed", False, None, "idx_s1.md", "idx_s1.md")
            rows = tenant._get_proj().g.query(
                "MATCH (e:Event) RETURN e.eventKind, e.is_episodic"
            ).result_set
            assert rows == [["AgentSession", None]]
            assert count_org_usage(tid, "points", sdk=tenant) == 1
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
            # R-18) → 4 legacy nodes (p1, p2, k1 plus the flag-less
            # sessionCaptured EVENT — #1977 added the Event label to the
            # points count under the same R-18 rule) inflate the points
            # quota. The backfill's Event statement exists for exactly this
            # kind, so the remedy below covers the Event branch too.
            assert count_org_usage(tid, "points", sdk=tenant) == 4
            with pytest.raises(QuotaExceededError, match="points limit reached"):
                enforce_org_limit(
                    {"org_id": tid, "max_points": 4}, "points", sdk=tenant)
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
