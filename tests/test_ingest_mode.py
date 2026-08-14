"""E2E-5 (epic #902 A6, issue #1056): mode-param E2E — granularity mode
isomorphism.

Same bundle via ``granularity="bulk"`` on G1 and ``granularity="granular"``
on G2 → ISOMORPHIC final graph (same node sets, same statuses, same edge
sets, same operator sets — compared MODULO per-run volatile props:
batch_id/ids/timestamps). The response shape is the ONLY behavioral fork:
bulk returns aggregated counts with NO ``results`` key; granular returns the
same aggregates PLUS per-item ``results`` (assertion 2).

Covers E2E-5 assertions 1-7:
  1. Isomorphic final state modulo batch_id + batch_id present on every
     bundle-created Point (incl. the operator Point), ULID-shaped, EQUAL
     across G1/G2 (content-derived key, §4.2).
  2. Bulk: aggregated counts, NO results key. Granular: per-item results
     present. Response shape = the only fork.
  3. granularity="anything-else" raises on SDK AND returns {error, code:
     ERR_INVALID} via MCP tortoise_ingest (both surfaces) — message content
     names the valid values (cycle-21 pin).
  4. Repeat with promotion_policy="auto" on both — still isomorphic
     (policy independent of granularity).
  5. Granular invalid-item semantics: a bundle with one invalid item via
     granularity="granular" → SAME whole-bundle BundleValidationError with
     violations, zero mutations (Phase 1 is mode-invariant, §5.2); failure
     response has NO results key (bulk failure shape).
  6. batch_id canonicalization invariance (E2E-5.6): re-serialize the SAME
     logical bundle SEVEN ways (a)-(g) + (h) tie shape → ONE identical
     batch_id across all serializations and equal to the canonical run,
     each ingested on a FRESH graph (§4.2 (a)-(h)).
  7. Empty bundle (E2E-5.7): ingest the empty bundle under BOTH
     granularities AND BOTH policies → success, created/deduped all zero,
     well-formed batch_id (ULID-shaped, deterministic across repeated
     calls), empty ids (+ empty results under granular); re-ingest of the
     SAME empty bundle → IDENTICAL batch_id.

PLUS the granular key-for-key leg (issue indicator 2): ``results[].result``
per the §5.5 route matrix through the MCP layer — operator route
``{"operator_id", "deduped"}``, relation route ``{"relation", "from", "to",
"deduped"}``. SHIPPED-SHAPE DELTA (plan-vs-code, documented in-test): the
DIRECT-EDGE route's conn_result is not yet reachable through ingest — the
shipped ingest routes plain IMPL/NAND operator-keyed connections to
create_operator (operator route; the §8 direct-edge routing integration is
A3-owned, #1053 OPEN); the shipped ``create_direct_edge`` writer returns
``{"direct_edge", "from", "to", "created", "deduped"}`` (5 keys — the plan's
route-matrix pin ``{"direct_edge", "from", "to", "deduped"}`` omits the
shipped ``created`` key). Asserted against the SHIPPED writer shape; the
ingest-level direct-edge conn_result lands with A3 and E2E-15(q).

Track A — no sentinel gates; runs on FalkorDBLite (embedded tempfile
db_path). Runnable with:
    .venv/bin/python -m pytest tests/test_ingest_mode.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

# SDK's own ULID-shaped regex (test_ingest_bundle.py convention) — batch_id
# must satisfy it.
_CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)

# Per-run volatile props excluded from the isomorphism comparison (the
# plan's "modulo batch_id" — extended to the other per-run-derived props:
# ids (per-call ULIDs), timestamps (assigned at write, sdk.py:744), and
# source defaults). Deliberately NARROW: semantic payload props (title /
# contentHash / externalId / version on Source nodes, content on Points)
# stay in the signature — a future regression deriving them differently
# per mode must not be invisible (review-gate P2-1).
_VOLATILE_PROPS = frozenset({
    "id", "batch_id", "createdAt", "updatedAt", "ingestedAt",
})


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ingest_mode_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


# ── Graph-state helpers (raw Cypher assertions) ─────────────────────

def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _query(sdk, cypher, params)
    return int(rows[0][0]) if rows else 0


def _graph_signature(sdk):
    """Isomorphism signature: (node_set, edge_set) modulo volatile props.

    Nodes are keyed by (label tuple, sorted non-volatile props); edges by
    (type, from-node-key, to-node-key, sorted non-volatile props). Internal
    GraphEvent nodes (the JSONL event-log mirror) are excluded — they carry
    per-run event ids/timestamps and are not graph semantic state. batch_id
    is excluded from BOTH node and edge props (asserted separately per the
    plan's "compared MODULO batch_id").
    """
    node_keys: dict[str, tuple] = {}
    rows = _query(sdk, "MATCH (n) RETURN n.id, labels(n), properties(n)")
    for nid, labels, props in rows:
        labels = tuple(sorted(labels or []))
        if "GraphEvent" in labels:
            continue
        props = props or {}
        clean = tuple(sorted(
            (k, str(v)) for k, v in props.items() if k not in _VOLATILE_PROPS))
        node_keys[nid] = (labels, clean)
    edge_rows = _query(sdk, "MATCH (a)-[r]->(b) RETURN type(r), a.id, b.id, properties(r)")
    edge_set = set()
    for typ, fa, tb, props in edge_rows:
        props = props or {}
        clean = tuple(sorted(
            (k, str(v)) for k, v in props.items() if k != "batch_id"))
        edge_set.add((typ, node_keys.get(fa), node_keys.get(tb), clean))
    # multiset comparison (review-gate P2-2): a mode that double-writes a
    # node/edge with IDENTICAL props must be caught — frozenset dedupe
    # would collapse it; Counter preserves multiplicity.
    import collections
    return (collections.Counter(node_keys.values()),
            collections.Counter(edge_set))


def _fresh_sdk(tmp_path=None):
    """A brand-new embedded SDK on its own tempfile db (for fresh-graph legs)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ingest_mode_"), "test.db")
    return TortoiseSDK(db_path)


# ── Canonical bundle fixture (E2E-5.6 canonical run) ────────────────

def _canonical_bundle():
    """Same-logical bundle used for both the isomorphism legs and the
    batch_id canonicalization variants. Includes a unicode diacritic in point
    content (so the NFC/NFD variant (d) is meaningful), a float confidence
    (variants (b)/(e)), refs (variant (g)), and all three shipped connection
    routes reachable through ingest (operator + relation)."""
    return {
        "points": [
            {"ref": "p1", "kind": "claim",
             "content": "El café está listo — Rust es seguro."},
            {"ref": "p2", "kind": "claim",
             "content": "El borrow checker evita use-after-free."},
        ],
        "entities": [
            {"ref": "s1", "type": "subject", "name": "Ferra Labs",
             "subjectKind": "organization"},
        ],
        "sources": [
            {"ref": "src1", "url": "https://example.com/rust-report",
             "sourceKind": "report", "tier": "T1"},
        ],
        "connections": [
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL",
             "direction": "unidirectional", "confidence": 0.3},
            {"ref": "c2", "from": "s1", "to": "p1", "relation": "authoredBy"},
            {"ref": "c3", "from": "p1", "to": "src1",
             "relation": "extractedFrom"},
        ],
    }


def _shuffle_keys(value):
    """Recursively reverse dict key order at every level (variant (a))."""
    if isinstance(value, dict):
        return {_shuffle_keys(k): _shuffle_keys(v)
                for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_shuffle_keys(v) for v in value]
    return value


def _rename_refs(bundle, mapping):
    """Rename every ref KEY (and every value addressing one) (variant (g))."""
    def _rn(x):
        if isinstance(x, dict):
            return {_rn(k): _rn(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_rn(v) for v in x]
        if isinstance(x, str) and x in mapping:
            return mapping[x]
        return x
    return _rn(copy.deepcopy(bundle))


# ── E2E-5 assertion 1/2/4: isomorphism ──────────────────────────────

class TestModeIsomorphism:
    """E2E-5 assertions 1, 2, 4 — same bundle, both modes, identical graph.

    Setup: two fresh graphs, each pre-seeded with an identical live point L
    (per the plan's E2E-5 setup)."""

    BUNDLE = None  # set at class level below (needs no instance state)

    def _seed_live_point(self, sdk):
        sdk.create_point("statement", "Pre-existing live anchor L.",
                         status="live")

    def _ingest_pair(self, policy: str):
        """Ingest the same bundle on two fresh graphs: bulk on G1, granular
        on G2. Returns (bulk_res, gran_res, sig_bulk, sig_gran)."""
        g1 = _fresh_sdk()
        g2 = _fresh_sdk()
        try:
            self._seed_live_point(g1)
            self._seed_live_point(g2)
            bundle = json.loads(json.dumps(_canonical_bundle()))
            r_bulk = g1.ingest(bundle, granularity="bulk",
                               promotion_policy=policy)
            r_gran = g2.ingest(bundle, granularity="granular",
                               promotion_policy=policy)
            sig_bulk = _graph_signature(g1)
            sig_gran = _graph_signature(g2)
            return r_bulk, r_gran, sig_bulk, sig_gran, g1, g2
        finally:
            g1.close()
            g2.close()

    def test_isomorphic_gated(self):
        # E2E-5 assertion 1 (gated): identical node sets, statuses, edge
        # sets, operator sets — compared modulo batch_id.
        r_bulk, r_gran, sig_bulk, sig_gran, _, _ = self._ingest_pair("gated")
        nodes_bulk, edges_bulk = sig_bulk
        nodes_gran, edges_gran = sig_gran
        assert nodes_bulk == nodes_gran, (
            "node sets diverge between bulk and granular"
        )
        assert edges_bulk == edges_gran, (
            "edge sets diverge between bulk and granular"
        )
        # same aggregate counts
        assert r_bulk["created"] == r_gran["created"]
        assert r_bulk["deduped"] == r_gran["deduped"]

    def test_batch_id_equal_across_graphs_and_ulid_shaped(self):
        # E2E-5 assertion 1 batch_id half: present on every bundle-created
        # Point (plain + operator), ULID-shaped, EQUAL across G1/G2 (same
        # canonical bundle content ⇒ same content-derived key, §4.2).
        r_bulk, r_gran, _, _, g1, g2 = self._ingest_pair("gated")
        b1, b2 = r_bulk["batch_id"], r_gran["batch_id"]
        assert b1 == b2, "batch_id must be content-derived and equal across graphs"
        for bid in (b1, b2):
            assert isinstance(bid, str) and len(bid) == 26
            assert _CROCKFORD_ULID_RE.match(bid), bid
        # every bundle-created Point carries it — the 2 plain points (the
        # bundle's plain IMPL routes to an OPERATOR-LESS DIRECT EDGE since
        # A3 #1053 — the edge carries the batch_id on the EDGE, §5.3)
        for g, r in ((g1, r_bulk), (g2, r_gran)):
            stamped = _count(
                g,
                "MATCH (n:Point {batch_id:$b}) RETURN count(n)",
                {"b": b1},
            )
            assert stamped == 2, "2 plain points stamped"
            assert _count(
                g, "MATCH ()-[r:IMPL|NAND {batch_id:$b}]->() RETURN count(r)",
                {"b": b1},
            ) == 1, "the plain IMPL direct edge carries the batch_id"
            assert _count(
                g, "MATCH (n:Point) WHERE n.batch_id IS NULL RETURN count(n)"
            ) == 1, "only the pre-seeded live anchor L is unstamped"

    def test_isomorphic_auto(self):
        # E2E-5 assertion 4: repeat with promotion_policy="auto" on both —
        # still isomorphic (policy ORTHOGONAL to granularity, Q2).
        r_bulk, r_gran, sig_bulk, sig_gran, _, _ = self._ingest_pair("auto")
        assert sig_bulk[0] == sig_gran[0], "node sets diverge under auto"
        assert sig_bulk[1] == sig_gran[1], "edge sets diverge under auto"
        assert r_bulk["batch_id"] == r_gran["batch_id"]
        # auto semantics visible on BOTH graphs identically — the source
        # point promotes live on its first operator edge, the target stays
        # draft (#131 parity, identical in both modes — E2E-8 assertion 1)
        p_src = r_bulk["ids"]["points"][0]
        p_tgt = r_bulk["ids"]["points"][1]
        assert p_src and p_tgt  # ids present (status asserted via signature)

    def test_response_shape_is_the_only_fork(self):
        # E2E-5 assertion 2: bulk = aggregated counts, NO results key;
        # granular = per-item results present. Response shape is the ONLY
        # behavioral fork.
        r_bulk, r_gran, _, _, _, _ = self._ingest_pair("gated")
        assert "results" not in r_bulk
        assert r_bulk["created"] == r_gran["created"]
        assert r_bulk["deduped"] == r_gran["deduped"]
        results = r_gran["results"]
        # 2 points + 1 subject entity + 1 source + 3 connections = 7 items
        assert isinstance(results, list) and len(results) == 7
        sections = [r["section"] for r in results]
        # write order: sources → points → entities → connections
        assert sections[0] == "sources"
        assert sections[1:3] == ["points", "points"]
        assert sections[3] == "entities"
        assert sections[4:] == ["connections", "connections", "connections"]
        # each result carries its created id + deduped flag
        assert results[1]["result"]["id"] == r_gran["ids"]["points"][0]
        assert results[1]["deduped"] is False
        assert all("deduped" in r for r in results)


# ── E2E-5 assertion 3: invalid mode value ───────────────────────────

class TestInvalidModeValue:
    """E2E-5 assertion 3 — granularity="anything-else" raises on SDK AND
    returns {error, code: ERR_INVALID} via MCP tortoise_ingest (both
    surfaces); message content names the valid values (cycle-21 pin)."""

    def test_sdk_invalid_granularity_raises_naming_valid_values(self, sdk):
        for bad in ("anything-else", "atomic", "chunky"):
            with pytest.raises(ValueError, match="granularity") as exc:
                sdk.ingest({"points": []}, granularity=bad)
            msg = str(exc.value)
            assert "granularity" in msg
            assert "bulk" in msg and "granular" in msg

    def test_mcp_invalid_granularity_err_invalid(self):
        import tortoise.mcp_server as mcp_mod
        res = mcp_mod.tortoise_ingest(bundle={"points": []}, granularity="atomic")
        assert res["code"] == mcp_mod.ERR_INVALID == -32003
        assert "granularity" in res["error"]
        assert "bulk" in res["error"] and "granular" in res["error"]

    def test_mcp_invalid_promotion_policy_err_invalid(self):
        # cycle-21 message-content pin: promotion_policy typos (the other
        # most common first-time typo) name the valid values too.
        import tortoise.mcp_server as mcp_mod
        res = mcp_mod.tortoise_ingest(bundle={"points": []},
                                      promotion_policy="atomic")
        assert res["code"] == mcp_mod.ERR_INVALID == -32003
        assert "promotion_policy" in res["error"]
        assert "gated" in res["error"] and "auto" in res["error"]


# ── E2E-5 assertion 5: granular invalid-item semantics ──────────────

class TestGranularInvalidItem:
    """E2E-5 assertion 5 — a bundle with one invalid item via
    granularity="granular" → SAME whole-bundle BundleValidationError with
    violations, zero mutations (Phase 1 is mode-invariant, §5.2); the
    failure shape is the bulk shape (no results key)."""

    INVALID_BUNDLE = {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "connections": [
            {"from": "p1", "to": "01GHOST00000000000000000000", "operator": "IMPL"},
            {"from": "p2", "to": "p1", "operator": "SUPPORTS"},
        ],
    }

    def test_same_bundle_error_both_granularities_zero_mutation(self, sdk):
        from tortoise.exceptions import BundleValidationError
        violations_by_mode = {}
        for mode in ("bulk", "granular"):
            db = _fresh_sdk()
            try:
                with pytest.raises(BundleValidationError) as exc:
                    db.ingest(json.loads(json.dumps(self.INVALID_BUNDLE)),
                              granularity=mode)
                violations_by_mode[mode] = exc.value.violations
                # zero mutations — no points, no operators, no edges
                assert _count(db, "MATCH (n:Point) RETURN count(n)") == 0
                assert _count(db, "MATCH (n:Subject) RETURN count(n)") == 0
                assert _count(db, "MATCH ()-[r]->() RETURN count(r)") == 0
            finally:
                db.close()
        # SAME whole-bundle error in both modes: same violation messages
        msgs_bulk = [v["message"] for v in violations_by_mode["bulk"]]
        msgs_gran = [v["message"] for v in violations_by_mode["granular"]]
        assert msgs_bulk == msgs_gran
        assert any("does not exist" in m for m in msgs_gran)
        assert any("SUPPORTS" in m for m in msgs_gran)

    def test_mcp_failure_shape_no_results_key(self):
        # MCP surface: the failure response is the bulk failure shape —
        # {error, code: ERR_BUNDLE_INVALID, violations}, NO results key.
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                                       _transport_mode)
        from tortoise.sdk import TortoiseSDK
        _transport_mode.set("stdio")
        _current_team_id.set(None)
        _current_team_limits.set(None)
        db = _fresh_sdk()
        _orig_get_team_sdk = mcp_mod._get_team_sdk
        mcp_mod._get_team_sdk = lambda: db
        try:
            res = mcp_mod.tortoise_ingest(
                bundle=json.loads(json.dumps(self.INVALID_BUNDLE)),
                granularity="granular")
        finally:
            _transport_mode.set(None)
            _current_team_id.set(None)
            _current_team_limits.set(None)
            mcp_mod._get_team_sdk = _orig_get_team_sdk
            db.close()
        assert res["code"] == mcp_mod.ERR_BUNDLE_INVALID == -32008
        assert "violations" in res and res["violations"]
        assert "results" not in res


# ── Granular key-for-key leg (§5.5 route matrix, through MCP layer) ─

class TestGranularResultsKeyForKey:
    """Granular results[].result key-for-key per the §5.5 route matrix,
    exercised THROUGH the MCP layer (in-process tortoise_ingest with a
    temp SDK, §7 leg-policy precedent: test_mcp_server.py runs _safe/tools
    directly in stdio mode).

    operator route = {"operator_id", "deduped"} (shipped sdk.py:4153/4160)
    relation route = {"relation", "from", "to", "deduped"} (shipped sdk.py)
    direct-edge route = {"direct_edge", "from", "to", "deduped"} per the
      plan pin — SHIPPED DELTA documented in test_direct_edge_route below:
      the ingest-level direct-edge conn_result is A3-owned (#1053 OPEN;
      shipped ingest routes plain IMPL/NAND operator-keyed connections to
      create_operator); the shipped create_direct_edge writer returns
      {"direct_edge", "from", "to", "created", "deduped"} (5 keys — the
      plan's 4-key pin omits the shipped `created` key).
    """

    def _mcp_sdk(self):
        """A temp SDK wired into the in-process MCP layer (stdio transport,
        no team context — quota skipped). Returns (db, cleanup)."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                                       _transport_mode)
        _transport_mode.set("stdio")
        _current_team_id.set(None)
        _current_team_limits.set(None)
        db = _fresh_sdk()
        _orig_get_team_sdk = mcp_mod._get_team_sdk
        mcp_mod._get_team_sdk = lambda: db

        def cleanup():
            _transport_mode.set(None)
            _current_team_id.set(None)
            _current_team_limits.set(None)
            mcp_mod._get_team_sdk = _orig_get_team_sdk
            db.close()

        return db, cleanup

    def _mcp_ingest_on(self, db, bundle, **kw):
        import tortoise.mcp_server as mcp_mod
        return mcp_mod.tortoise_ingest(bundle=bundle, **kw)

    def _mcp_ingest(self, bundle, **kw):
        db, cleanup = self._mcp_sdk()
        try:
            return self._mcp_ingest_on(db, bundle, **kw)
        finally:
            cleanup()

    def test_operator_route_result_key_for_key(self):
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "A implies B."},
                {"ref": "p2", "kind": "claim", "content": "B."},
            ],
            "connections": [
                # mitigation-bearing → the OPERATOR route (post-A3 #1053 a
                # PLAIN IMPL would route to a direct edge)
                {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL",
                 "mitigation": {"reason": "x", "strength": 0.6}},
            ],
        }
        res = self._mcp_ingest(bundle, granularity="granular",
                               promotion_policy="gated")
        assert "error" not in res, res
        conn_results = [r for r in res["results"]
                        if r["section"] == "connections"]
        assert len(conn_results) == 1
        result = conn_results[0]["result"]
        # key-for-key per the §5.5 route matrix (operator route)
        assert set(result.keys()) == {"operator_id", "deduped"}
        assert isinstance(result["operator_id"], str)
        assert result["deduped"] is False
        # created connection → ids["connections"] carries the bare operator
        # id str (descriptor contract, §5.5)
        assert res["ids"]["connections"] == [result["operator_id"]]

    def test_operator_route_deduped_hit_key_for_key(self):
        # re-submission → deduped=True on the SAME key set (deduped flag on
        # both branches, sdk.py:4153/4160 — cycle-24 pin).
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "A implies B."},
                {"ref": "p2", "kind": "claim", "content": "B."},
            ],
            "connections": [
                {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL",
                 "mitigation": {"reason": "x", "strength": 0.6}},
            ],
        }
        # re-submission against the SAME graph → deduped hit
        db, cleanup = self._mcp_sdk()
        try:
            first = self._mcp_ingest_on(db, bundle, granularity="granular",
                                        promotion_policy="gated")
            assert "error" not in first, first
            second = self._mcp_ingest_on(db, bundle, granularity="granular",
                                         promotion_policy="gated")
        finally:
            cleanup()
        assert "error" not in second, second
        conn_results = [r for r in second["results"]
                        if r["section"] == "connections"]
        result = conn_results[0]["result"]
        assert set(result.keys()) == {"operator_id", "deduped"}
        assert result["deduped"] is True
        assert second["created"]["connections"] == 0
        assert second["deduped"]["connections"] == 1

    def test_relation_route_result_key_for_key(self):
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "A."},
                {"ref": "p2", "kind": "claim", "content": "B."},
            ],
            "entities": [
                {"ref": "s1", "type": "subject", "name": "Ferra Labs",
                 "subjectKind": "organization"},
            ],
            "connections": [
                {"ref": "c1", "from": "s1", "to": "p1",
                 "relation": "authoredBy"},
                {"ref": "c2", "from": "p2", "to": "p1",
                 "relation": "references"},
            ],
        }
        res = self._mcp_ingest(bundle, granularity="granular",
                               promotion_policy="gated")
        assert "error" not in res, res
        conn_results = [r for r in res["results"]
                        if r["section"] == "connections"]
        assert len(conn_results) == 2
        for r in conn_results:
            result = r["result"]
            # key-for-key per the §5.5 route matrix (relation route)
            assert set(result.keys()) == {"relation", "from", "to", "deduped"}
            assert result["deduped"] is False
        assert res["created"]["connections"] == 2

    def test_direct_edge_route_shipped_shape(self):
        # SHIPPED-SHAPE: the §5.5 route matrix pins the DIRECT-EDGE
        # conn_result as {"direct_edge", "from", "to", "deduped"};
        # post-A3 #1053 the ingest routing sends plain IMPL/NAND operator-
        # keyed connections through create_direct_edge (the direct-edge
        # route) — the SHIPPED create_direct_edge writer returns
        # {"direct_edge", "from", "to", "created", "deduped"} — the plan's
        # 4-key pin omits the shipped `created` key. Asserted here against
        # the SHIPPED writer shape; the ingest-level conn_result lands with
        # A3 and E2E-15(q).
        sdk = _fresh_sdk()
        try:
            pa = sdk.create_point("claim", "A implies B.")["id"]
            pb = sdk.create_point("claim", "B.")["id"]
            r = sdk.create_direct_edge("IMPL", pa, pb)
            assert set(r.keys()) == {"direct_edge", "from", "to",
                                     "created", "deduped"}
            assert r["direct_edge"] == "IMPL"
            assert r["from"] == pa and r["to"] == pb
            assert r["created"] is True and r["deduped"] is False
            # exactly ONE edge, no operator node (operator-less direct edge)
            assert _count(
                sdk,
                "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) "
                "RETURN count(r)",
                {"a": pa, "b": pb},
            ) == 1
            assert _count(sdk, "MATCH (o:Point {is_operator:true}) "
                               "RETURN count(o)") == 0
            # idempotent re-submission → deduped flag flips on the SAME key
            # set (created→False, deduped→True)
            r2 = sdk.create_direct_edge("IMPL", pa, pb)
            assert set(r2.keys()) == {"direct_edge", "from", "to",
                                      "created", "deduped"}
            assert r2["created"] is False and r2["deduped"] is True
            assert _count(
                sdk,
                "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) "
                "RETURN count(r)",
                {"a": pa, "b": pb},
            ) == 1  # no parallel edge
        finally:
            sdk.close()


# ── E2E-5.6: batch_id canonicalization invariance ──────────────────

class TestBatchIdCanonicalization:
    """E2E-5 assertion 6 / E2E-5.6 — re-serialize the SAME logical bundle
    SEVEN ways (a)-(g) + (h) tie shape and ingest each on a FRESH graph →
    ONE identical batch_id across all serializations and equal to the
    canonical run (§4.2 (a)-(f) + ref-expansion pin + cycle-11 (h))."""

    def _batch_id_on_fresh(self, bundle, granularity="bulk"):
        db = _fresh_sdk()
        try:
            res = db.ingest(json.loads(json.dumps(bundle)),
                            granularity=granularity)
            return res["batch_id"]
        finally:
            db.close()

    def test_seven_serializations_one_batch_id(self):
        canonical = self._batch_id_on_fresh(_canonical_bundle())
        assert canonical == self._batch_id_on_fresh(_canonical_bundle())

        # (a) shuffled dict key order at every nesting level
        assert self._batch_id_on_fresh(_shuffle_keys(_canonical_bundle())) \
            == canonical
        # (b) re-formatted floats — 0.30 / 0.3 / 3e-1 converge to one repr
        for formatted in (0.30, 0.3, 3e-1):
            variant = copy.deepcopy(_canonical_bundle())
            variant["connections"][0]["confidence"] = formatted
            assert self._batch_id_on_fresh(variant) == canonical, formatted
        # (c) reordered connection list
        variant = copy.deepcopy(_canonical_bundle())
        variant["connections"] = list(reversed(variant["connections"]))
        assert self._batch_id_on_fresh(variant) == canonical
        # (d) NFC-vs-NFD unicode — precomposed vs decomposed point content
        variant = copy.deepcopy(_canonical_bundle())
        variant["points"][0]["content"] = unicodedata.normalize(
            "NFD", variant["points"][0]["content"])
        assert variant["points"][0]["content"] != _canonical_bundle()[
            "points"][0]["content"], "NFD must differ from NFC form"
        assert self._batch_id_on_fresh(variant) == canonical
        # (e) int-vs-float numerics — confidence 1 vs 1.0 collapse
        # (both serialize to "1"; they differ from the canonical run whose
        # confidence is 0.3 — the invariance is int≡float, not ≡canonical)
        int_variant = copy.deepcopy(_canonical_bundle())
        int_variant["connections"][0]["confidence"] = 1
        float_variant = copy.deepcopy(int_variant)
        float_variant["connections"][0]["confidence"] = 1.0
        assert self._batch_id_on_fresh(int_variant) \
            == self._batch_id_on_fresh(float_variant)
        assert self._batch_id_on_fresh(int_variant) != canonical
        # (f) reordered points/entities/sources ITEM lists
        variant = copy.deepcopy(_canonical_bundle())
        variant["points"] = list(reversed(variant["points"]))
        variant["sources"] = list(reversed(variant["sources"]))
        variant["entities"] = list(reversed(variant["entities"]))
        assert self._batch_id_on_fresh(variant) == canonical
        # (g) ref-KEY rename — every ref KEY replaced by a fresh name, items
        # unchanged (END-TO-END pin of the §4.2 ref-expansion pin)
        renamed = _rename_refs(
            _canonical_bundle(),
            {"p1": "point-alpha", "p2": "point-beta", "s1": "subject-gamma",
             "src1": "source-delta", "c1": "conn-1", "c2": "conn-2",
             "c3": "conn-3"},
        )
        assert self._batch_id_on_fresh(renamed) == canonical

    def test_h_same_pair_reify_label_tie_shape(self):
        # cycle-11 (h): two same-pair reify connections differing ONLY in
        # label, connection list reordered + label/mitigation/reify reordered
        # at the item level → identical batch_id (a regression dropping
        # label/reify from the sort key splits this class).
        base = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "A"},
                {"ref": "p2", "kind": "claim", "content": "B"},
            ],
            "connections": [
                {"from": "p1", "to": "p2", "operator": "IMPL",
                 "reify": True, "label": "addresses"},
                {"from": "p1", "to": "p2", "operator": "IMPL",
                 "reify": True, "label": "opposes"},
            ],
        }
        canonical = self._batch_id_on_fresh(base)
        variant = {
            "points": [
                {"ref": "p2", "kind": "claim", "content": "B"},
                {"ref": "p1", "kind": "claim", "content": "A"},
            ],
            "connections": [
                {"reify": True, "label": "opposes", "operator": "IMPL",
                 "to": "p2", "from": "p1"},
                {"label": "addresses", "reify": True, "to": "p2",
                 "from": "p1", "operator": "IMPL"},
            ],
        }
        assert self._batch_id_on_fresh(variant) == canonical

    def test_different_content_different_batch_id(self):
        a = _canonical_bundle()
        b = _canonical_bundle()
        b["points"][0]["content"] = "Different content entirely."
        assert self._batch_id_on_fresh(a) != self._batch_id_on_fresh(b)


# ── E2E-5.7: empty bundle ───────────────────────────────────────────

class TestEmptyBundle:
    """E2E-5 assertion 7 / E2E-5.7 — the empty bundle under BOTH
    granularities AND BOTH policies: success, created/deduped all zero,
    well-formed batch_id (ULID-shaped, deterministic across repeated calls),
    empty ids (+ empty results under granular); re-ingest of the SAME empty
    bundle → IDENTICAL batch_id."""

    EMPTY = {"points": [], "entities": [], "sources": [], "connections": []}

    def test_empty_bundle_all_modes_all_policies(self):
        for granularity in ("bulk", "granular"):
            for policy in ("gated", "auto"):
                db = _fresh_sdk()
                try:
                    res = db.ingest(json.loads(json.dumps(self.EMPTY)),
                                    granularity=granularity,
                                    promotion_policy=policy)
                finally:
                    db.close()
                assert "error" not in res
                assert res["created"] == {
                    "points": 0, "entities": 0, "sources": 0, "connections": 0,
                }
                assert res["deduped"] == {
                    "points": 0, "entities": 0, "sources": 0, "connections": 0,
                }
                bid = res["batch_id"]
                assert isinstance(bid, str) and len(bid) == 26
                assert _CROCKFORD_ULID_RE.match(bid), bid
                assert res["ids"] == {
                    "points": [], "entities": [], "sources": [],
                    "connections": [], "refs": {},
                }
                assert res["nudges"] == []
                # A3 #1053 shipped the warnings contract (E2E-6.2): the empty
                # bundle carries an EMPTY warnings list
                assert res["warnings"] == [], res["warnings"]
                if granularity == "granular":
                    assert res["results"] == []
                else:
                    assert "results" not in res

    def test_empty_bundle_batch_id_deterministic_and_identical_reingest(self):
        db = _fresh_sdk()
        try:
            b1 = db.ingest(json.loads(json.dumps(self.EMPTY)))["batch_id"]
            b2 = db.ingest(json.loads(json.dumps(self.EMPTY)))["batch_id"]
            # re-ingest of the SAME empty bundle → IDENTICAL batch_id
            assert b1 == b2
            # and identical across granularity modes on a fresh graph
            db2 = _fresh_sdk()
            try:
                b_gran = db2.ingest(json.loads(json.dumps(self.EMPTY)),
                                    granularity="granular")["batch_id"]
            finally:
                db2.close()
            assert b_gran == b1
        finally:
            db.close()
