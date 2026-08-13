"""Tests for TortoiseSDK.ingest — heterogeneous bulk write (epic #888 W4).

Design reference: product/2026-08-11-tooling-surface-consolidation.md (PR #912) +
ontology v3.5 reification rule (PR #910): connections carrying `operator`
(IMPL/NAND) create operator Points; connections carrying `relation` stay plain
structural edges.

Runnable with: .venv/bin/python -m pytest tests/test_ingest_bundle.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ingest_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


# ── Graph query helpers (raw Cypher assertions) ─────────────────────

def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _query(sdk, cypher, params)
    return int(rows[0][0]) if rows else 0


def _operator_count(sdk, op_type: str = "IMPL") -> int:
    return _count(
        sdk,
        "MATCH (o:Point {is_operator:true, op_type:$op}) RETURN count(o)",
        {"op": op_type},
    )


def _edge_count(sdk, rel: str) -> int:
    return _count(sdk, f"MATCH ()-[r:{rel}]->() RETURN count(r)")


def _point_count(sdk) -> int:
    return _count(
        sdk,
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN count(n)",
    )


# ── Fixtures: canonical bundles ─────────────────────────────────────

def _full_bundle():
    """Points + subject + source + IMPL/structural/extractedFrom connections."""
    return {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "Rust is memory-safe by default."},
            {"ref": "p2", "kind": "claim",
             "content": "Rust's borrow checker prevents use-after-free."},
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
            # Operator edge (reification rule → operator Point)
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL"},
            # Structural edge (stays plain)
            {"ref": "c2", "from": "s1", "to": "p1", "relation": "authoredBy"},
            # Point → Source provenance
            {"ref": "c3", "from": "p1", "to": "src1", "relation": "extractedFrom"},
        ],
    }


# ── ingest: full bundle ─────────────────────────────────────────────

class TestIngestFullBundle:
    def test_creates_all_sections_and_wires_connections(self, sdk):
        bundle = _full_bundle()
        res = sdk.ingest(bundle)

        assert res["created"] == {
            "points": 2, "entities": 1, "sources": 1, "connections": 3,
        }
        # ids per section, in bundle order
        assert len(res["ids"]["points"]) == 2
        assert len(res["ids"]["entities"]) == 1
        assert len(res["ids"]["sources"]) == 1
        assert len(res["ids"]["connections"]) == 3
        assert res["ids"]["refs"]["p1"] == res["ids"]["points"][0]
        assert res["ids"]["refs"]["src1"] == res["ids"]["sources"][0]

        # Graph state: all nodes exist
        assert _point_count(sdk) == 2
        assert _count(sdk, "MATCH (n:Subject {name:'Ferra Labs'}) RETURN count(n)") == 1
        assert _count(
            sdk, "MATCH (s:Source {url:'https://example.com/rust-report'}) RETURN count(s)"
        ) == 1

        # IMPL connection created an operator Point + IMPL edges
        assert _operator_count(sdk, "IMPL") == 1
        p1, p2 = res["ids"]["points"]
        assert _count(
            sdk,
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "WHERE (o)-[:IMPL]->({id:$a}) AND (o)-[:IMPL]->({id:$b}) "
            "RETURN count(o)",
            {"a": p1, "b": p2},
        ) == 1

        # Structural edge stayed plain: authoredBy edge, no operator Point
        s1 = res["ids"]["entities"][0]
        assert _count(
            sdk,
            "MATCH (s:Subject {id:$sid})-[:authoredBy]->(p:Point {id:$pid}) RETURN count(*)",
            {"sid": s1, "pid": p1},
        ) == 1

        # extractedFrom wiring: (Point)-[:extractedFrom]->(Source)
        assert _edge_count(sdk, "extractedFrom") == 1
        assert _count(
            sdk,
            "MATCH (p:Point {id:$pid})-[:extractedFrom]->(s:Source {url:$url}) "
            "RETURN count(*)",
            {"pid": p1, "url": "https://example.com/rust-report"},
        ) == 1

    def test_refs_do_not_leak_as_node_properties(self, sdk):
        res = sdk.ingest(_full_bundle())
        p1 = res["ids"]["points"][0]
        point = sdk.get_point(p1)
        assert "ref" not in point
        subject = sdk._get_entity(res["ids"]["entities"][0])
        assert "ref" not in subject
        source = sdk._get_entity(res["ids"]["sources"][0])
        assert "ref" not in source

    def test_points_default_to_draft_unless_specified(self, sdk):
        bundle = {
            "points": [
                {"kind": "claim", "content": "draft point, no connections"},
                {"kind": "claim", "content": "live point", "status": "live"},
            ],
            "connections": [],
        }
        res = sdk.ingest(bundle)
        pid_draft, pid_live = res["ids"]["points"]
        assert sdk.get_point(pid_draft)["status"] == "draft"
        assert sdk.get_point(pid_live)["status"] == "live"

# ── promotion_policy (epic #902 W4 A0) ─────────────────────────────

class TestPromotionPolicy:
    """E2E-8: promotion policy is an explicit param with both behaviors.

    gated (default) → source stays draft; auto → source promotes on wire
    (#131 parity). Param is keyword-only on the SDK surface.
    """

    def test_signature_default_is_gated(self):
        import inspect
        sig = inspect.signature(TortoiseSDK.ingest)
        # Q2-lock contract pin: the signature default IS the guarantee (no
        # silent auto-promotion). The KEYWORD_ONLY half is behaviorally
        # covered by test_keyword_only_enforced — not re-asserted here.
        assert sig.parameters["promotion_policy"].default == "gated"

    def test_default_omit_gated_keeps_source_draft(self, sdk):
        # E2E-8 assertion 2: omitting the param defaults to gated — the
        # source stays draft (no silent mode exists; #131 behavior is the
        # opt-in, not the default).
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle)
        pA, pB = res["ids"]["points"]
        assert sdk.get_point(pA)["status"] == "draft"  # not auto-promoted
        assert sdk.get_point(pB)["status"] == "draft"
        # operator node itself is written draft (#780 promote_source=False)
        op_id = res["ids"]["connections"][0]
        assert sdk.get_point(op_id)["status"] == "draft"

    def test_explicit_gated_keeps_source_draft(self, sdk):
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle, promotion_policy="gated")
        pA, pB = res["ids"]["points"]
        assert sdk.get_point(pA)["status"] == "draft"

        # CYCLE-26 REVIEW FIX (P2): assert the OPERATOR node and the TARGET
        # also stay draft under explicit gated (not just the source) — closes
        # the gap that a gated-explicit operator-write regression would ship
        # green.
        op_id = next(
            i for i in res["ids"]["connections"]
            if isinstance(i, str)
        )
        assert sdk.get_point(op_id)["status"] == "draft"
        assert sdk.get_point(pB)["status"] == "draft"
    def test_invalid_promotion_policy_raises_naming_valid_values(self, sdk):
        # every invalid value raises ValueError naming the param AND the valid
        # values (cycle-21 message-content pin)
        for bad in ("atomic", "nope"):
            with pytest.raises(ValueError) as exc:
                sdk.ingest({"points": []}, promotion_policy=bad)
            msg = str(exc.value)
            assert "promotion_policy" in msg
            assert "gated" in msg and "auto" in msg

    def test_gated_granular_parity(self, sdk):
        # Policy is ORTHOGONAL to granularity (E2E-5): gated holds in both
        # modes — no silent promotion window in granular mode.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle, granularity="granular", promotion_policy="gated")
        pA, pB = res["ids"]["points"]
        op_id = res["ids"]["connections"][0]
        # Full status set mirrors the bulk counterpart — E2E-5's "identical
        # final graph" claim checked per-node, not just the source.
        assert sdk.get_point(pA)["status"] == "draft"
        assert sdk.get_point(pB)["status"] == "draft"
        assert sdk.get_point(op_id)["status"] == "draft"

    def test_auto_promotes_source_live(self, sdk):
        # E2E-8 assertion 1 (SDK surface): auto → source promotes live on
        # first IMPL edge; target stays draft; operator is NOT draft (the
        # live-side of the #780 promote_source asymmetry — gated writes an
        # explicit draft, auto writes no status → projection coalesces live).
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle, promotion_policy="auto")
        pA, pB = res["ids"]["points"]
        op_id = res["ids"]["connections"][0]
        assert sdk.get_point(pA)["status"] == "live"   # source promoted
        assert sdk.get_point(pB)["status"] == "draft"  # target stays draft
        # #780 asymmetry: gated writes explicit draft on the operator; auto
        # writes NO status prop (EP treats null-status as live). Assert the
        # honest discriminator — key absence, not == "live" (which would
        # fail on the raw-properties read surface) and not != "draft"
        # (which false-passes on garbage statuses like "retracted").
        assert "status" not in sdk.get_point(op_id)

    def test_auto_nand_promotes_source_live(self, sdk):
        # Promotion is op-type-agnostic: NAND wires the same #131 path
        # (guards against a future op-type-conditional promotion).
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A contradicts B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "NAND"}],
        }
        res = sdk.ingest(bundle, promotion_policy="auto")
        pA, _ = res["ids"]["points"]
        assert sdk.get_point(pA)["status"] == "live"

    def test_auto_granular_parity(self, sdk):
        # E2E-5 discriminating cell: auto holds in granular mode — the
        # promote flag must not be dropped on the granular code path.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle, granularity="granular", promotion_policy="auto")
        pA, pB = res["ids"]["points"]
        op_id = res["ids"]["connections"][0]
        assert sdk.get_point(pA)["status"] == "live"
        assert sdk.get_point(pB)["status"] == "draft"
        assert "status" not in sdk.get_point(op_id)  # #780 live-side

    def test_keyword_only_enforced(self, sdk):
        # Q2-lock: positional auto-promotion must NOT silently opt in — a
        # third positional arg raises TypeError (agents opt in explicitly).
        with pytest.raises(TypeError):
            sdk.ingest({"points": []}, "bulk", "auto")

    def test_auto_not_retroactive_on_dedup(self, sdk):
        # "Promotes when its FIRST edge is created": a re-ingest under auto
        # of an already-deduped operator does NOT retro-promote the source.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        first = sdk.ingest(bundle)  # gated default
        pA, _ = first["ids"]["points"]
        assert sdk.get_point(pA)["status"] == "draft"
        second = sdk.ingest(bundle, promotion_policy="auto")  # dedup path
        pA2, _ = second["ids"]["points"]
        assert pA2 == pA
        # Directly prove the dedup branch ran: operator deduped, not re-created
        assert second["deduped"]["connections"] == 1
        assert second["ids"]["connections"][0] == first["ids"]["connections"][0]
        assert sdk.get_point(pA)["status"] == "draft"  # not retro-promoted


# ── granularity ─────────────────────────────────────────────────────

class TestGranularity:
    def test_granular_returns_per_item_results(self, sdk):
        res = sdk.ingest(_full_bundle(), granularity="granular")
        assert res["granularity"] == "granular"
        results = res["results"]
        # one result per item, sections in write order (sources → points →
        # entities → connections)
        sections = [r["section"] for r in results]
        assert sections[:4] == ["sources", "points", "points", "entities"]
        assert sections[4:] == ["connections", "connections", "connections"]
        # each result carries the created id + deduped flag
        assert results[1]["result"]["id"] == res["ids"]["points"][0]
        assert results[1]["deduped"] is False
        assert results[4]["deduped"] is False
        # aggregate counts still present
        assert res["created"] == {
            "points": 2, "entities": 1, "sources": 1, "connections": 3,
        }

    def test_bulk_has_no_per_item_results(self, sdk):
        res = sdk.ingest(_full_bundle())
        assert res["granularity"] == "bulk"
        assert "results" not in res

    def test_invalid_granularity_raises(self, sdk):
        with pytest.raises(ValueError, match="granularity") as exc:
            sdk.ingest({"points": []}, granularity="atomic")
        # message-content half of the contract: valid values named (mirror of
        # the promotion_policy pin)
        assert "bulk" in str(exc.value) and "granular" in str(exc.value)


# ── idempotency ─────────────────────────────────────────────────────

class TestReingest:
    def test_reingest_same_bundle_no_duplicates(self, sdk):
        bundle = _full_bundle()
        first = sdk.ingest(bundle)
        second = sdk.ingest(bundle)

        # Nothing newly created on re-ingest
        assert second["created"] == {
            "points": 0, "entities": 0, "sources": 0, "connections": 0,
        }
        assert second["deduped"] == {
            "points": 2, "entities": 1, "sources": 1, "connections": 3,
        }
        # Same canonical ids returned (connection descriptors differ only by
        # their deduped flag — compare the stable parts)
        assert second["ids"]["points"] == first["ids"]["points"]
        assert second["ids"]["entities"] == first["ids"]["entities"]
        assert second["ids"]["sources"] == first["ids"]["sources"]
        assert second["ids"]["connections"][0] == first["ids"]["connections"][0]
        for i in (1, 2):
            assert second["ids"]["connections"][i]["relation"] \
                == first["ids"]["connections"][i]["relation"]
            assert second["ids"]["connections"][i]["from"] \
                == first["ids"]["connections"][i]["from"]
            assert second["ids"]["connections"][i]["to"] \
                == first["ids"]["connections"][i]["to"]

        # Graph has no duplicates
        assert _point_count(sdk) == 2
        assert _operator_count(sdk, "IMPL") == 1
        assert _edge_count(sdk, "IMPL") == 2  # one operator, two IMPL edges
        assert _edge_count(sdk, "authoredBy") == 1
        assert _edge_count(sdk, "extractedFrom") == 1
        assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1
        assert _count(sdk, "MATCH (n:Source) RETURN count(n)") == 1

    def test_reingest_after_extra_writes_keeps_dedup(self, sdk):
        # Re-ingest must not clobber unrelated graph state
        sdk.create_point("claim", "unrelated point")
        res = sdk.ingest(_full_bundle())
        res2 = sdk.ingest(_full_bundle())
        assert res2["created"]["points"] == 0
        assert _point_count(sdk) == 3  # 2 bundle + 1 unrelated


# ── local ref resolution ────────────────────────────────────────────

class TestLocalRefs:
    def test_connections_resolve_by_local_ref(self, sdk):
        res = sdk.ingest(_full_bundle())
        p1, p2 = res["ids"]["points"]
        s1 = res["ids"]["entities"][0]
        url = "https://example.com/rust-report"

        # IMPL operator connects exactly the two bundle points
        assert _count(
            sdk,
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "WHERE (o)-[:IMPL]->({id:$a}) AND (o)-[:IMPL]->({id:$b}) "
            "RETURN count(o)",
            {"a": p1, "b": p2},
        ) == 1

        # authoredBy wired subject → point
        assert _count(
            sdk,
            "MATCH (s:Subject {id:$sid})-[:authoredBy]->(p:Point {id:$pid}) RETURN count(*)",
            {"sid": s1, "pid": p1},
        ) == 1

        # extractedFrom wired point → source url
        assert _count(
            sdk,
            "MATCH (p:Point {id:$pid})-[:extractedFrom]->(s:Source {url:$url}) RETURN count(*)",
            {"pid": p1, "url": url},
        ) == 1

    def test_mixed_ref_and_external_ids(self, sdk):
        # A connection may reference a bundle item AND a pre-existing node
        existing = sdk.create_point("claim", "pre-existing point", status="live")
        bundle = {
            "points": [{"ref": "p1", "kind": "claim", "content": "new point"}],
            "connections": [
                {"from": "p1", "to": existing["id"], "operator": "IMPL"},
            ],
        }
        res = sdk.ingest(bundle)
        p1 = res["ids"]["points"][0]
        assert _count(
            sdk,
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "WHERE (o)-[:IMPL]->({id:$a}) AND (o)-[:IMPL]->({id:$b}) "
            "RETURN count(o)",
            {"a": p1, "b": existing["id"]},
        ) == 1

    def test_refs_in_entity_props_resolve(self, sdk):
        # authoredBy/ownedBy/managedBy + about* props may use bundle refs
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "claim one"},
                {"ref": "p2", "kind": "claim", "content": "claim two"},
            ],
            "entities": [
                {"ref": "org", "type": "subject", "name": "Acme Corp"},
                {"ref": "evt", "type": "event", "name": "Launch event",
                 "eventKind": "launch", "aboutPoint": "p2"},
            ],
            "connections": [],
        }
        res = sdk.ingest(bundle)
        org = res["ids"]["entities"][0]
        evt = res["ids"]["entities"][1]
        p2 = res["ids"]["points"][1]
        # subject → subject ownership via ref
        # event → point about edge via ref
        assert _count(
            sdk,
            "MATCH (e:Event {id:$eid})-[:aboutPoint]->(p:Point {id:$pid}) RETURN count(*)",
            {"eid": evt, "pid": p2},
        ) == 1
        assert org  # entity created

    def test_duplicate_refs_raise(self, sdk):
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "a"},
                {"ref": "p1", "kind": "claim", "content": "b"},
            ],
        }
        with pytest.raises(ValueError, match="ref"):
            sdk.ingest(bundle)

    def test_unresolvable_connection_endpoint_raises(self, sdk):
        bundle = {
            "points": [{"ref": "p1", "kind": "claim", "content": "a"}],
            "connections": [
                {"from": "p1", "to": "ghost-ref", "operator": "IMPL"},
            ],
        }
        with pytest.raises(ValueError):
            sdk.ingest(bundle)


# ── reification rule ────────────────────────────────────────────────

class TestReificationRule:
    def test_operator_connection_creates_operator(self, sdk):
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "supports"},
                {"ref": "pB", "kind": "claim", "content": "supported"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle)
        assert _operator_count(sdk, "IMPL") == 1
        op_id = res["ids"]["connections"][0]
        op = sdk.get_point(op_id)
        assert op["is_operator"] is True
        assert op["op_type"] == "IMPL"

    def test_structural_connection_stays_plain(self, sdk):
        bundle = {
            "points": [{"ref": "pA", "kind": "claim", "content": "a"}],
            "entities": [{"ref": "author", "type": "subject", "name": "Author"}],
            "connections": [{"from": "pA", "to": "author", "relation": "authoredBy"}],
        }
        res = sdk.ingest(bundle)
        # structural edge exists, NO operator node created for it
        assert _edge_count(sdk, "authoredBy") == 1
        assert _operator_count(sdk, "IMPL") == 0
        assert _count(
            sdk,
            "MATCH (o:Point {is_operator:true}) RETURN count(o)",
        ) == 0

    def test_nand_operator_connection(self, sdk):
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "NAND"}],
        }
        res = sdk.ingest(bundle)
        assert _operator_count(sdk, "NAND") == 1
        op = sdk.get_point(res["ids"]["connections"][0])
        assert op["op_type"] == "NAND"

    def test_connection_with_both_relation_and_operator_raises(self, sdk):
        bundle = {
            "points": [{"ref": "pA", "kind": "claim", "content": "a"}],
            "connections": [
                {"from": "pA", "to": "pA", "operator": "IMPL", "relation": "uses"},
            ],
        }
        with pytest.raises(ValueError, match="exactly one"):
            sdk.ingest(bundle)

    def test_unknown_relation_raises(self, sdk):
        bundle = {
            "points": [{"ref": "pA", "kind": "claim", "content": "a"}],
            "connections": [{"from": "pA", "to": "pA", "relation": "notARelation"}],
        }
        with pytest.raises(ValueError, match="relation"):
            sdk.ingest(bundle)


# ── validation ──────────────────────────────────────────────────────

class TestValidation:
    def test_missing_sections_are_fine(self, sdk):
        res = sdk.ingest({})
        assert res["created"] == {
            "points": 0, "entities": 0, "sources": 0, "connections": 0,
        }
        assert res["ids"] == {
            "points": [], "entities": [], "sources": [], "connections": [],
            "refs": {},
        }

    def test_point_item_requires_kind_and_content(self, sdk):
        with pytest.raises(ValueError, match="kind"):
            sdk.ingest({"points": [{"content": "no kind"}]})
        with pytest.raises(ValueError, match="content"):
            sdk.ingest({"points": [{"kind": "claim"}]})

    def test_entity_requires_type_and_name(self, sdk):
        with pytest.raises(ValueError, match="type"):
            sdk.ingest({"entities": [{"name": "x"}]})
        with pytest.raises(ValueError, match="type"):
            sdk.ingest({"entities": [{"type": "gadget", "name": "x"}]})

    def test_event_entity_requires_eventKind(self, sdk):
        with pytest.raises(ValueError, match="eventKind"):
            sdk.ingest({"entities": [{"type": "event", "name": "launch"}]})

    def test_source_requires_url_and_sourceKind(self, sdk):
        with pytest.raises(ValueError, match="url"):
            sdk.ingest({"sources": [{"sourceKind": "report"}]})
        with pytest.raises(ValueError, match="sourceKind"):
            sdk.ingest({"sources": [{"url": "https://x.example"}]})

    def test_connection_requires_from_and_to(self, sdk):
        with pytest.raises(ValueError, match="from"):
            sdk.ingest({"connections": [{"to": "x", "operator": "IMPL"}]})


# ── regression ──────────────────────────────────────────────────────

class TestRegression:
    def test_batch_create_points_still_works(self, sdk):
        points = [
            {"kind": "claim", "content": "one"},
            {"kind": "claim", "content": "two"},
        ]
        created = sdk.batch_create_points(points)
        assert len(created) == 2
        assert {p["content"] for p in created} == {"one", "two"}
        assert _point_count(sdk) == 2

    def test_create_point_dedup_still_works(self, sdk):
        p1 = sdk.create_point("claim", "same", dedup=True)
        p2 = sdk.create_point("claim", "same", dedup=True)
        assert p1["id"] == p2["id"]
