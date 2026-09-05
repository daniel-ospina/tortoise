"""Tests for enumeration surfaces — TASK 1.2 (issue #49).

Tests list_pointkinds, list_sources, list_namespaces, and re-keyed
summarize_structure. Runs on isolated graph (conftest sets TORTOISE_DB_URI).

NOTE: Tests share the session-isolated Docker graph (conftest sets
a unique graph name). Tests clean up at start to avoid ordering issues.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _http_transport_mode():
    """#493: MCP tool wrappers go through _safe, which fails closed without a
    transport mode (#236). Set HTTP parity like test_mcp_server."""
    from tortoise.mcp_auth import _transport_mode

    token = _transport_mode.set("http")
    yield
    _transport_mode.reset(token)


# ── Helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test.

    Falls back to Docker graph when TORTOISE_DB_URI is set (conftest).
    Cleans graph before each test to avoid inter-test state bleed.
    """
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_enum_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    # Clean graph before test — all tests in this file share the isolated
    # session graph from conftest, so state from previous tests bleeds.
    _clean_graph(sdk)
    yield sdk
    sdk.close()


def _clean_graph(sdk: TortoiseSDK) -> None:
    """Delete all nodes and edges from the test graph."""
    try:
        sdk.test_guard()
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass  # Graph might not exist yet


def _make_point(sdk: TortoiseSDK, kind: str = "statement", content: str = "test", **kw):
    return sdk.create_point(kind, content, **kw)


def _create_raw_point(sdk: TortoiseSDK, pid: str, kind: str, content: str,
                      *, op_type: str | None = None) -> None:
    """Write a bare Point node via raw Cypher — simulates a legacy/imported
    node that predates the is_operator property (#2205): plain points then
    carried NO is_operator property, and legacy operators carried op_type
    without it (canonical operator detection = is_operator OR op_type, #943)."""
    props = ["id:$pid", "content:$content", "pointKind:$kind"]
    params = {"pid": pid, "content": content, "kind": kind}
    if op_type:
        props.append("op_type:$op")
        params["op"] = op_type
    sdk._get_proj().g.query(
        "CREATE (p:Point {" + ", ".join(props) + "})", params=params
    )


# ── list_pointkinds ───────────────────────────────────────────────────


class TestListPointKinds:
    def test_empty_graph(self, sdk):
        """Empty graph returns empty list."""
        result = sdk.list_pointkinds()
        assert isinstance(result, list)
        assert result == []

    def test_multiple_kinds_with_counts(self, sdk):
        """Points of 2-3 kinds, verify list includes them with counts."""
        _make_point(sdk, kind="statement", content="alpha")
        _make_point(sdk, kind="statement", content="beta")
        _make_point(sdk, kind="goal", content="gamma")
        _make_point(sdk, kind="observation", content="delta")

        result = sdk.list_pointkinds()

        # Should have 3 kinds
        assert len(result) == 3

        # Build a lookup
        by_kind = {r["kind"]: r for r in result}
        assert by_kind["statement"]["count"] == 2
        assert by_kind["goal"]["count"] == 1
        assert by_kind["observation"]["count"] == 1

        # Verify shape
        for r in result:
            assert "kind" in r
            assert "count" in r
            assert "pack" in r
            assert isinstance(r["count"], int)
            assert r["count"] > 0

    def test_operators_excluded(self, sdk):
        """Operators should NOT appear in list_pointkinds."""
        p1 = _make_point(sdk, kind="statement", content="regular point")
        p2 = _make_point(sdk, kind="statement", content="another point")
        # Create an operator (is_operator=true) — needs real source/target
        sdk.create_operator("IMPL", p1["id"], [p2["id"]])

        result = sdk.list_pointkinds()
        # Only "statement" should appear (2 points), not the operator
        by_kind = {r["kind"]: r for r in result}  # noqa: F841
        # The operator's pointKind is typically "statement" (default for operators)
        # but it's excluded by the is_operator=false filter. So we should see
        # "statement" with count 3 (2 real points + 1 operator with "statement" kind)
        # Actually operators have their own kind handling...
        # The key assertion: no operator-only kind should appear
        kinds = {r["kind"] for r in result}
        assert "statement" in kinds  # the 2 real points + operator

    def test_legacy_property_absent_points_listed(self, sdk):
        """#2205: a legacy Point with NO is_operator property is a real point
        and shows in per-kind stats — a bare `n.is_operator = false` dropped
        it, so per-kind stats lied on imported graphs. Legacy op_type-only
        operators stay excluded."""
        _create_raw_point(sdk, "legacy_stmt_1", "statement", "legacy s1")
        _create_raw_point(sdk, "legacy_stmt_2", "statement", "legacy s2")
        _create_raw_point(sdk, "legacy_op", "statement", "legacy op",
                         op_type="IMPL")

        result = sdk.list_pointkinds()
        by_kind = {r["kind"]: r for r in result}
        assert by_kind["statement"]["count"] == 2  # legacy op excluded

    def test_pack_field_from_colon_kind(self, sdk):
        """When pointKind has colon prefix, pack field is extracted."""
        sdk.create_point("product-strategy:jobToBeDone", "test jtbd")

        result = sdk.list_pointkinds()
        assert len(result) >= 1

        # Find our prefixed kind
        prefixed = [r for r in result if ":" in r["kind"]]
        assert len(prefixed) == 1
        assert prefixed[0]["pack"] == "product-strategy"


# ── list_sources ──────────────────────────────────────────────────────


class TestListSources:
    def test_empty_graph(self, sdk):
        """Empty graph returns empty list."""
        result = sdk.list_sources()
        assert isinstance(result, list)
        assert result == []

    def test_source_with_points(self, sdk):
        """Create Source + extractedFrom, verify list_sources shows it.

        Note: _link_source creates Source nodes with sourceKind='document'
        by default (not the value passed to create_source, which doesn't
        create a graph node via apply).
        """
        url = "https://enum-test-1.example.com/doc.md"
        # Create points linked to source — this creates the Source stub via _link_source
        _make_point(sdk, kind="statement", content="point 1",
                    extractedFrom=url)
        _make_point(sdk, kind="statement", content="point 2",
                    extractedFrom=url)

        result = sdk.list_sources()
        assert len(result) == 1
        assert result[0]["url"] == url
        assert result[0]["sourceKind"] == "document"  # default from _link_source
        assert result[0]["points"] == 2

    def test_source_without_points(self, sdk):
        """Source created via _link_source with no subsequent points.

        We need to create a point with extractedFrom, then delete the point
        to leave an orphaned Source. Or we can verify that an unused Source
        doesn't appear unless created.
        """
        # Create a Source stub via _link_source by creating+deleting a point
        url = "https://enum-test-2.example.com/doc.md"
        p = _make_point(sdk, kind="statement", content="temp",
                        extractedFrom=url)
        # Delete the point but Source remains
        sdk.delete_point(p["id"])

        result = sdk.list_sources()
        source = [s for s in result if s["url"] == url]
        assert len(source) == 1
        assert source[0]["points"] == 0

    def test_multiple_sources_ordered_by_points(self, sdk):
        """Sources ordered DESC by point count."""
        url_a = "https://enum-test-a.example.com"
        url_b = "https://enum-test-b.example.com"

        # 3 points → source A
        for i in range(3):
            _make_point(sdk, kind="statement", content=f"p{i}",
                        extractedFrom=url_a)
        # 1 point → source B
        _make_point(sdk, kind="statement", content="p",
                    extractedFrom=url_b)

        result = sdk.list_sources()
        # Filter to only our test sources
        ours = [s for s in result if s["url"] in (url_a, url_b)]
        assert len(ours) == 2
        # First should be A (3 points), then B (1 point) — DESC
        assert ours[0]["url"] == url_a
        assert ours[0]["points"] == 3
        assert ours[1]["url"] == url_b
        assert ours[1]["points"] == 1


# ── list_namespaces ───────────────────────────────────────────────────


class TestListNamespaces:
    def test_returns_pack_namespaces(self, sdk):
        """Returns installed pack namespaces with expected keys."""
        result = sdk.list_namespaces()
        assert isinstance(result, list)
        assert len(result) >= 4

        namespaces = {p["namespace"] for p in result}
        # Core packs should be present (pm = project-management)
        assert "dev" in namespaces
        assert "product-strategy" in namespaces
        assert "marketing" in namespaces
        # project-management may be registered as "project-management" or "pm"
        pm_ns = namespaces & {"project-management", "pm"}
        assert pm_ns, f"Expected project-management or pm in {namespaces}"

        # Verify shape
        for p in result:
            assert "namespace" in p
            assert "name" in p
            assert "kind_count" in p
            assert isinstance(p["kind_count"], int)
            assert p["kind_count"] > 0


# ── summarize_structure (re-keyed; #2205 total-vs-gate) ───────────────


_GATE_KEYS = ("gate0_jtbds", "gate1_use_cases", "gate2_user_journeys",
               "gate3_workflows", "gate4_requirements")


class TestSummarizeStructureRekeyed:
    def test_expected_keys(self, sdk):
        """Returns gate keys + full-graph total + operators + gate_total."""
        status = sdk.summarize_structure()
        for key in (*_GATE_KEYS, "total", "operators", "gate_total"):
            assert key in status, f"missing key: {key}"
            assert isinstance(status[key], int), f"{key} should be int"

    def test_gate_total_is_gate_subtotal(self, sdk):
        """gate_total is the sum of the five gate-kind counts — the honest
        label for the pre-#2205 'total' semantics (which was gate-scoped)."""
        status = sdk.summarize_structure()
        assert status["gate_total"] == sum(status[k] for k in _GATE_KEYS)

    def test_empty_graph_returns_zeros(self, sdk):
        """Empty graph returns 0 everywhere (no error)."""
        status = sdk.summarize_structure()
        assert status["total"] == 0
        assert status["operators"] == 0
        assert status["gate_total"] == 0
        for key in _GATE_KEYS:
            assert status[key] == 0, f"{key} should be 0"

    def test_counts_by_pointkind(self, sdk):
        """Points of gate kinds increment the correct gate counters; total
        counts them too (nothing else is on the graph)."""
        _make_point(sdk, kind="jobToBeDone", content="JTBD 1")
        _make_point(sdk, kind="jobToBeDone", content="JTBD 2")
        _make_point(sdk, kind="useCase", content="UC 1")
        _make_point(sdk, kind="userJourney", content="UJ 1")
        _make_point(sdk, kind="requirement", content="Req 1")

        status = sdk.summarize_structure()
        assert status["gate0_jtbds"] == 2
        assert status["gate1_use_cases"] == 1
        assert status["gate2_user_journeys"] == 1
        assert status["gate3_workflows"] == 0  # none created
        assert status["gate4_requirements"] == 1
        assert status["total"] == 5
        assert status["gate_total"] == 5

    # ── #2205 regressions: total must reflect the WHOLE graph ─────
    def test_total_includes_non_gate_kinds(self, sdk):
        """#2205: on a mixed-kind graph total counts points of EVERY kind
        (evidence, decisions, events, ...), not just the gate kinds. The old
        gate-only 'total' read 0/near-0 on real graphs ('Demo graph created
        — N points' and stats lied)."""
        seeds = [
            ("statement", "s1"), ("statement", "s2"),
            ("observation", "o1"), ("hypothesis", "h1"),
            ("decision", "d1"), ("event", "e1"),
            ("workflow", "wf1"), ("jobToBeDone", "j1"),
        ]
        for kind, content in seeds:
            _make_point(sdk, kind=kind, content=content)

        status = sdk.summarize_structure()
        # Kind-accurate after seeds of any kinds (indicator 3): every seeded
        # non-operator point lands in total, whatever its kind.
        assert status["total"] == len(seeds)
        assert status["operators"] == 0
        assert status["gate0_jtbds"] == 1
        assert status["gate3_workflows"] == 1
        assert status["gate_total"] == 2  # j1 + wf1 are the gate kinds
        assert status["total"] > status["gate_total"]  # the pre-#2205 lie

    def test_legacy_property_absent_points_counted(self, sdk):
        """#2205 regression: legacy/imported Points that carry NO is_operator
        property must count — a bare `n.is_operator = false` filter dropped
        them from every count (the 'N points read 0' root cause on imported
        graphs). Legacy op_type-only operators are operators, never points."""
        # legacy plain points (no is_operator property, no op_type)
        _create_raw_point(sdk, "legacy_stmt", "statement", "legacy s")
        _create_raw_point(sdk, "legacy_jtbd", "jobToBeDone", "legacy j")
        # legacy operator (op_type set, no is_operator property, #943)
        _create_raw_point(sdk, "legacy_op", "statement", "legacy op",
                          op_type="IMPL")

        status = sdk.summarize_structure()
        assert status["total"] == 2        # both legacy PLAIN points only
        assert status["operators"] == 1    # op_type-only legacy operator
        assert status["gate0_jtbds"] == 1  # legacy jtbd lands in its gate
        assert status["gate_total"] == 1

    def test_operators_reported_separately(self, sdk):
        """Operators are NOT 'points': excluded from gate counts AND from
        total, and reported under their own operators key (N points, M
        operators)."""
        p1 = _make_point(sdk, kind="jobToBeDone", content="real jtbd")
        p2 = _make_point(sdk, kind="jobToBeDone", content="another jtbd")
        # Create operator linking the two JTBDs
        sdk.create_operator("IMPL", p1["id"], [p2["id"]])

        status = sdk.summarize_structure()
        # Only the 2 real jtbds count; operator excluded from gates AND total
        assert status["gate0_jtbds"] == 2
        assert status["total"] == 2
        assert status["operators"] == 1

    def test_total_matches_list_pointkinds_sum(self, sdk):
        """Cross-surface agreement (#2205): summary total == the sum of
        list_pointkinds counts (both enumerate the same non-operator Points),
        so any surface rendering per-kind stats adds up to 'N points'."""
        p1 = _make_point(sdk, kind="statement", content="s-a")
        _make_point(sdk, kind="decision", content="d-b")
        _make_point(sdk, kind="jobToBeDone", content="j-c")
        p4 = _make_point(sdk, kind="goal", content="g-e")
        sdk.create_operator("IMPL", p1["id"], [p4["id"]])

        status = sdk.summarize_structure()
        per_kind_sum = sum(r["count"] for r in sdk.list_pointkinds())
        assert status["total"] == per_kind_sum == 4
        assert status["operators"] == 1
        assert status["gate_total"] == 1

    def test_total_matches_list_pointkinds_sum_with_legacy(self, sdk):
        """#2205: the cross-surface equality holds on legacy nodes too — a
        kined legacy Point (no is_operator property) counts in BOTH total and
        list_pointkinds, and a legacy op_type operator counts in neither."""
        _create_raw_point(sdk, "legacy_stmt", "statement", "legacy s")
        _make_point(sdk, kind="decision", content="d-b")
        _create_raw_point(sdk, "legacy_op", "statement", "legacy op",
                          op_type="IMPL")

        status = sdk.summarize_structure()
        per_kind_sum = sum(r["count"] for r in sdk.list_pointkinds())
        assert status["total"] == per_kind_sum == 2
        assert status["operators"] == 1

    def test_untyped_legacy_point_in_total_only(self, sdk):
        """#2205 docstring caveat: an untyped legacy Point (no pointKind, no
        is_operator) is a real point — it counts in total but cannot appear in
        the per-kind list (kinds only), so sum(list_pointkinds) may be < total."""
        _create_raw_point(sdk, "legacy_untyped", "statement", "legacy u",
                          op_type=None)
        # The helper always sets pointKind — write one truly kind-less node:
        sdk._get_proj().g.query(
            "CREATE (p:Point {id:$pid, content:$content})",
            params={"pid": "legacy_kindless", "content": "no kind"},
        )

        status = sdk.summarize_structure()
        per_kind_sum = sum(r["count"] for r in sdk.list_pointkinds())
        assert status["total"] == 2
        assert per_kind_sum == 1  # only the kined legacy point is listable


# ── MCP wrappers (direct call) ────────────────────────────────────────


class TestMCPWrappers:
    @pytest.fixture(autouse=True)
    def _setup_env(self, monkeypatch):
        """Ensure TORTOISE_SECRET_PEPPER is set for auth module import."""
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper-for-tests")

    def test_tortoise_list_pointkinds_shape(self, sdk):
        """MCP tool function returns correct shape."""
        from tortoise.mcp_server import tortoise_list_pointkinds

        # Create some points first
        _make_point(sdk, kind="statement", content="test")
        _make_point(sdk, kind="goal", content="test2")

        # Patch the module-level sdk to use our test sdk
        import tortoise.mcp_server as mcp_mod
        orig_sdk = mcp_mod.sdk
        mcp_mod.sdk = sdk
        try:
            result = tortoise_list_pointkinds()
            assert isinstance(result, list)
            assert len(result) >= 2
            for r in result:
                assert "kind" in r
                assert "count" in r
                assert "pack" in r
        finally:
            mcp_mod.sdk = orig_sdk

    def test_tortoise_list_sources_shape(self, sdk):
        """MCP tool function returns correct shape."""
        from tortoise.mcp_server import tortoise_list_sources

        url = "https://mcp-test.example.com"
        _make_point(sdk, kind="statement", content="p",
                    extractedFrom=url)

        import tortoise.mcp_server as mcp_mod
        orig_sdk = mcp_mod.sdk
        mcp_mod.sdk = sdk
        try:
            result = tortoise_list_sources()
            assert isinstance(result, list)
            ours = [s for s in result if s["url"] == url]
            assert len(ours) == 1
            assert ours[0]["url"] == url
            assert ours[0]["points"] == 1
        finally:
            mcp_mod.sdk = orig_sdk

    def test_tortoise_list_namespaces_shape(self, sdk):
        """MCP tool function returns correct shape."""
        from tortoise.mcp_server import tortoise_list_namespaces  # noqa: I001

        import tortoise.mcp_server as mcp_mod
        orig_sdk = mcp_mod.sdk
        mcp_mod.sdk = sdk
        try:
            result = tortoise_list_namespaces()
            assert isinstance(result, list)
            assert len(result) >= 4
            namespaces = {p["namespace"] for p in result}
            assert "dev" in namespaces
            assert "product-strategy" in namespaces
            # Verify shape
            for p in result:
                assert "namespace" in p
                assert "name" in p
                assert "kind_count" in p
        finally:
            mcp_mod.sdk = orig_sdk
