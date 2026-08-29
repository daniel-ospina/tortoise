"""Tests for TortoiseSDK.ingest — heterogeneous bulk write (epic #888 W4).

Design reference: product/2026-08-11-tooling-surface-consolidation.md (PR #912) +
ontology v3.5 reification rule (PR #910): connections carrying `operator`
(IMPL/NAND) create operator Points; connections carrying `relation` stay plain
structural edges.

Runnable with: .venv/bin/python -m pytest tests/test_ingest_bundle.py -v
"""
from __future__ import annotations

import json
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
            # Operator edge (reification rule → operator Point). §8 routing
            # (A3): a PLAIN {operator: IMPL} is operator-LESS; the
            # explicit-anchor reify:true path creates the operator — this
            # shared fixture uses reify so the batch_id/operator tests keep
            # exercising the operator stamping (their intent).
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL",
             "reify": True},
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
        # Per-item status:'live' is only allowed under promotion_policy='auto'
        # (INGEST_CONTRACT row 9: under gated it is a violation).
        bundle = {
            "points": [
                {"kind": "claim", "content": "draft point, no connections"},
                {"kind": "claim", "content": "live point", "status": "live"},
            ],
            "connections": [],
        }
        res = sdk.ingest(bundle, promotion_policy="auto")
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
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "reify": True}],
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
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "reify": True}],
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
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "reify": True}],
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

    def test_gated_rejects_explicit_live_item(self, sdk):
        # INGEST_CONTRACT row 9: under gated, an explicit status:'live' on a
        # point item is a violation — the Q2 lock is not bypassable via the
        # bundle's own status field.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A",
                 "status": "live"},
            ],
            "connections": [],
        }
        with pytest.raises(ValueError) as exc:
            sdk.ingest(bundle)
        msg = str(exc.value)
        assert "status:'live' is not allowed under promotion_policy 'gated'" in msg
        assert "promotion_policy='auto'" in msg and "update_point" in msg

    def test_auto_allows_explicit_live_item(self, sdk):
        # Same item is sanctioned under auto (the row-9 route).
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A",
                 "status": "live"},
            ],
            "connections": [],
        }
        res = sdk.ingest(bundle, promotion_policy="auto")
        pA = res["ids"]["points"][0]
        assert sdk.get_point(pA)["status"] == "live"

    def test_gated_rejects_nested_props_status_live(self, sdk):
        # PR #1073 re-review P0: the props={"status": "live"} convention is
        # flattened by create_point (_coerce_props) — the guard must catch
        # the EFFECTIVE status, not just the top-level key.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A",
                 "props": {"status": "live"}},
            ],
            "connections": [],
        }
        with pytest.raises(ValueError) as exc:
            sdk.ingest(bundle)
        assert "not allowed under promotion_policy 'gated'" in str(exc.value)

    def test_gated_rejects_case_variant_status(self, sdk):
        # PR #1073 re-review P0/P2: case/whitespace variants must not bypass
        # the guard — only the exact canonical "draft" is storable under
        # gated; every other value gets the uniform row-9 message. EP treats
        # every status except exact 'draft' as live.
        for bad in ("Live", "LIVE", "lIve", " live ", "\tlive\n",
                    "Draft", "draft ", "DRAFT"):
            bundle = {
                "points": [
                    {"ref": "pA", "kind": "claim", "content": "A",
                     "status": bad},
                ],
                "connections": [],
            }
            with pytest.raises(ValueError) as exc:
                sdk.ingest(bundle)
            assert "not allowed under promotion_policy 'gated'" in str(exc.value)

    def test_gated_rejects_explicit_none_status(self, sdk):
        # An explicit status key present with value None is a row-9 violation
        # (None would store NULL, which EP _live_only treats as LIVE) — the
        # uniform row-9 message, not the create_point vocabulary error.
        for item in (
            {"ref": "pA", "kind": "claim", "content": "A", "status": None},
            {"ref": "pA", "kind": "claim", "content": "A",
             "props": {"status": None}},
        ):
            with pytest.raises(ValueError) as exc:
                sdk.ingest({"points": [item], "connections": []})
            assert "not allowed under promotion_policy 'gated'" in str(exc.value)

    def test_gated_accepts_items_without_status_key(self, sdk):
        # Items with NO status key anywhere (top-level or nested props) are
        # accepted under gated and default to draft — the has_status flag in
        # the shared helper must not false-reject them.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A"},
                {"ref": "pB", "kind": "claim", "content": "B",
                 "props": {"note": "no status here"}},
            ],
            "connections": [],
        }
        res = sdk.ingest(bundle)  # gated default
        for pid in res["ids"]["points"]:
            assert sdk.get_point(pid)["status"] == "draft"

    def test_gated_rejects_terminal_status(self, sdk):
        # PR #1073 re-review P1: canonical terminal statuses are EP-LIVE under
        # gated ( _live_only excludes only exact 'draft') and no fresh point
        # can legitimately carry them (terminal states come from retract/-
        # supersede flows) — fail-closed under gated, top-level and nested.
        for bad in ("retracted", "superseded", "outdated", "archived"):
            for item in (
                {"ref": "pA", "kind": "claim", "content": "A", "status": bad},
                {"ref": "pA", "kind": "claim", "content": "A",
                 "props": {"status": bad}},
            ):
                bundle = {"points": [item], "connections": []}
                with pytest.raises(ValueError) as exc:
                    sdk.ingest(bundle)
                assert "not allowed under promotion_policy 'gated'" in str(
                    exc.value)

    def test_create_point_rejects_non_canonical_status(self, sdk):
        # Fail-closed vocabulary validation on the create path (PR #1073
        # re-review P0/P1): a non-canonical status must not be storable — it
        # would otherwise be treated as EP-live by _live_only. Non-str values
        # (including unhashable list/dict) raise ValueError, not TypeError.
        for bad in ("Live", "draft ", "live\n", ["live"], {"x": 1}, None):
            with pytest.raises(ValueError) as exc:
                sdk.create_point("claim", "junk status", status=bad)
            assert "Invalid status" in str(exc.value)

    def test_auto_not_retroactive_on_dedup(self, sdk):
        # "Promotes when its FIRST edge is created": a re-ingest under auto
        # of an already-deduped operator does NOT retro-promote the source.
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A implies B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "reify": True}],
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
        res = sdk.ingest(_full_bundle())  # noqa: F841
        res2 = sdk.ingest(_full_bundle())
        assert res2["created"]["points"] == 0
        assert _point_count(sdk) == 3  # 2 bundle + 1 unrelated

    def test_gated_draft_reingest_returns_duplicate_no_crash(self, sdk):
        """Issue #1905 — gated re-ingest of a draft item must return a clean
        duplicate result, never a raw ValueError.

        Regression: the dedup-hit branch forwarded the caller's status:'draft'
        (the only status gated promotion permits explicitly) into
        update_point(), which rejects any non-'live' status — so the second
        ingest of the same draft item raised a raw ValueError mid-batch.
        """
        bundle = {"points": [
            {"ref": "p1", "kind": "statement",
             "content": "#1905 gated draft re-ingest.", "status": "draft"},
        ]}
        first = sdk.ingest(bundle, promotion_policy="gated")
        assert first["created"]["points"] == 1
        assert sdk.get_point(first["ids"]["points"][0])["status"] == "draft"
        # second call: dedup hit must NOT crash; returns a clean duplicate
        second = sdk.ingest(bundle, promotion_policy="gated")
        assert second["created"]["points"] == 0
        assert second["deduped"]["points"] == 1
        assert second["ids"]["points"] == first["ids"]["points"]
        assert second.get("batch_id") is not None
        assert _point_count(sdk) == 1

    def test_gated_nested_props_status_reingest_no_crash(self, sdk):
        """Issue #1905 — same regression through the nested props= form
        (flattened by _coerce_props; status still reaches the dedup branch)."""
        bundle = {"points": [
            {"ref": "p1", "kind": "statement",
             "content": "#1905 nested status re-ingest.",
             "props": {"status": "draft", "note": "x"}},
        ]}
        first = sdk.ingest(bundle, promotion_policy="gated")
        second = sdk.ingest(bundle, promotion_policy="gated")
        assert second["created"]["points"] == 0
        assert second["deduped"]["points"] == 1
        assert second["ids"]["points"] == first["ids"]["points"]
        assert second.get("batch_id") is not None

    def test_gated_reingest_mid_bundle_no_partial_commit(self, sdk):
        """Issue #1905 — batch integrity: a dedup hit mid-bundle must not
        crash the batch and strand earlier items as committed.

        Points 1+2 are pre-existing; the re-ingest bundle re-submits them
        (dedup hits), then re-submits a draft point 3 (the #1905 crash
        trigger — a mid-bundle dedup hit that carries status:'draft') and a
        NEW point 4. The whole batch must commit: no raw ValueError, points
        3+4 created, points 1+2+3 deduped.
        """
        seed = {"points": [
            {"ref": "p1", "kind": "statement", "content": "#1905 batch A."},
            {"ref": "p2", "kind": "statement", "content": "#1905 batch B."},
            {"ref": "p3", "kind": "statement", "content": "#1905 batch C.",
             "status": "draft"},
        ]}
        sdk.ingest(seed, promotion_policy="gated")
        bundle = {"points": [
            {"ref": "p1", "kind": "statement", "content": "#1905 batch A."},
            {"ref": "p2", "kind": "statement", "content": "#1905 batch B."},
            {"ref": "p3", "kind": "statement", "content": "#1905 batch C.",
             "status": "draft"},
            {"ref": "p4", "kind": "statement", "content": "#1905 batch D."},
        ]}
        res = sdk.ingest(bundle, promotion_policy="gated")
        assert res["created"]["points"] == 1
        assert res["deduped"]["points"] == 3
        assert len(res["ids"]["points"]) == 4
        assert res.get("batch_id") is not None
        # no partial commit: the post-dedup items landed too
        assert _point_count(sdk) == 4
        p4 = sdk.get_point(res["ids"]["points"][-1])
        assert p4["content"] == "#1905 batch D."
        assert p4["status"] == "draft"


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
                {"from": "p1", "to": existing["id"], "operator": "IMPL",
                 "reify": True},
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
        """§8 routing (A3, plan §5.3 line 513): a PLAIN {operator: IMPL}
        connection is OPERATOR-LESS — a direct edge, no operator node. The
        reify:true variant is the explicit-anchor path that creates the
        operator (asserted separately below)."""
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "supports"},
                {"ref": "pB", "kind": "claim", "content": "supported"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL"}],
        }
        res = sdk.ingest(bundle, granularity="granular")
        # direct edge exists; NO operator node (operator-less per §8)
        assert _edge_count(sdk, "IMPL") == 1
        assert _operator_count(sdk, "IMPL") == 0
        assert _count(
            sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 0
        # the direct-edge conn_result shape (plan §5.5 route matrix, cycle-22)
        conn_entry = next(e for e in res["results"]
                          if e["section"] == "connections")
        result = conn_entry["result"]
        assert set(result.keys()) == {"direct_edge", "from", "to", "deduped"}
        assert result["direct_edge"] == "IMPL"
        assert result["deduped"] is False
        assert res["created"]["connections"] == 1

    def test_reify_connection_creates_operator(self, sdk):
        """§8 routing: {operator: IMPL, reify:true} is the EXPLICIT-ANCHOR
        path — create_operator (the operator node exists)."""
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "supports"},
                {"ref": "pB", "kind": "claim", "content": "supported"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "reify": True}],
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
        res = sdk.ingest(bundle)  # noqa: F841
        # structural edge exists, NO operator node created for it
        assert _edge_count(sdk, "authoredBy") == 1
        assert _operator_count(sdk, "IMPL") == 0
        assert _count(
            sdk,
            "MATCH (o:Point {is_operator:true}) RETURN count(o)",
        ) == 0

    def test_nand_operator_connection(self, sdk):
        """§8 routing: a plain {operator: NAND} connection is OPERATOR-LESS
        (direct edge with the CYCLE-25 extraction default — direction-absent
        NAND = unidirectional on the edge)."""
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "NAND"}],
        }
        res = sdk.ingest(bundle)  # noqa: F841
        assert _edge_count(sdk, "NAND") == 1
        assert _operator_count(sdk, "NAND") == 0
        # direction-absent NAND = unidirectional on the edge (extraction default)
        d = sdk._get_proj().g.query(
            "MATCH ()-[r:NAND]->() RETURN r.direction").result_set
        assert d and d[0][0] == "unidirectional"

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

    def test_point_item_requires_content(self, sdk):
        with pytest.raises(ValueError, match="content"):
            sdk.ingest({"points": [{"kind": "claim"}]})

    def test_point_item_without_kind_defaults_to_statement(self, sdk):
        # CYCLE-25 (ontology v3.8): a point item WITHOUT pointKind DEFAULTS
        # to 'statement' (the extraction write kind) — not a violation.
        res = sdk.ingest({"points": [{"ref": "p", "content": "no kind"}]})
        pid = res["ids"]["points"][0]
        assert sdk.get_point(pid)["pointKind"] == "statement"

    def test_event_point_kind_rejected(self, sdk):
        # CYCLE-25: pointKind 'event' is REMOVED (#1013) — episodic records
        # are entity items type:'event' with eventKind.
        with pytest.raises(ValueError, match="event"):
            sdk.ingest({"points": [{"kind": "event", "content": "x"}]})

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

# ── batch_id (epic #902 A4, §4.2) ──────────────────────────────────

@pytest.fixture
def sdk_logged():
    """SDK with a JSONL event log (for BatchIdStamped record assertions)."""
    base = tempfile.mkdtemp(prefix="tortoise_ingest_batch_id_test_")
    log = os.path.join(base, "events.jsonl")
    sdk = TortoiseSDK(os.path.join(base, "test.db"), event_log_path=log)
    sdk._log_path = log
    yield sdk
    sdk.close()


def _batch_records(sdk):
    """All BatchIdStamped JSONL records appended by *sdk* (in order)."""
    with open(sdk._log_path, encoding="utf-8") as f:
        return [
            json.loads(line)
            for line in f.read().splitlines()
            if line.strip()
        ]


def _batch_id_records(sdk):
    return [e for e in _batch_records(sdk) if e.get("type") == "BatchIdStamped"]


class TestBatchId:
    def test_response_carries_ulid_shaped_batch_id(self, sdk):
        import re
        ulid_re = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)
        res = sdk.ingest(_full_bundle())
        bid = res["batch_id"]
        assert isinstance(bid, str) and len(bid) == 26
        assert ulid_re.match(bid), bid

    def test_every_created_point_stamped_plain_and_operator(self, sdk):
        # E2E-5.1 assertion 2: batch_id present on EVERY bundle-created Point —
        # plain points AND the operator Point (stamped POST-WRITE keyed on the
        # returned id — create_operator accepts no props).
        res = sdk.ingest(_full_bundle())
        bid = res["batch_id"]
        stamped = _count(
            sdk,
            "MATCH (n:Point {batch_id:$b}) RETURN count(n)",
            {"b": bid},
        )
        # 2 plain points + 1 operator point
        assert stamped == 3
        unstamped = _count(
            sdk, "MATCH (n:Point) WHERE n.batch_id IS NULL RETURN count(n)")
        assert unstamped == 0
        op_id = next(i for i in res["ids"]["connections"] if isinstance(i, str))
        assert sdk.get_point(op_id)["batch_id"] == bid

    def test_batch_id_equal_across_fresh_graphs(self, sdk):
        # E2E-5.1: same canonical bundle content ⇒ same content-derived key.
        base = tempfile.mkdtemp(prefix="tortoise_ingest_batch_id_g2_")
        sdk2 = TortoiseSDK(os.path.join(base, "test.db"))
        try:
            b1 = sdk.ingest(_full_bundle())["batch_id"]
            b2 = sdk2.ingest(_full_bundle())["batch_id"]
            assert b1 == b2
        finally:
            sdk2.close()

    def test_reingest_same_bundle_same_batch_id_no_rewrite(self, sdk):
        first = sdk.ingest(_full_bundle())
        second = sdk.ingest(_full_bundle())
        assert second["batch_id"] == first["batch_id"]
        # dedup hits keep their stamp — every point still carries ONE batch_id
        # and no NEW BatchIdStamped record is emitted on a clean re-ingest.
        assert _count(
            sdk,
            "MATCH (n:Point {batch_id:$b}) RETURN count(n)",
            {"b": first["batch_id"]},
        ) == 3

    def test_reingest_does_not_duplicate_records(self, sdk_logged):
        sdk_logged.ingest(_full_bundle())
        before = len(_batch_id_records(sdk_logged))
        sdk_logged.ingest(_full_bundle())
        after = len(_batch_id_records(sdk_logged))
        assert after == before  # (h)-repair is a no-op when records exist

    def test_dedup_hit_with_different_batch_id_keeps_original(self, sdk):
        # A point already stamped with ANOTHER bundle's batch_id keeps it —
        # dedup never rewrites provenance.
        bundle = _full_bundle()
        res = sdk.ingest(bundle)
        bid = res["batch_id"]
        # simulate a point stamped with a different batch (e.g. a prior import)
        other_bid = "0AAAAAAAAAAAAAAAAAAAAAAAAA"
        pid = res["ids"]["points"][0]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.batch_id = $bid",
            params={"id": pid, "bid": other_bid},
        )
        res2 = sdk.ingest(bundle)
        assert res2["batch_id"] == bid
        assert sdk.get_point(pid)["batch_id"] == other_bid

    def test_row14_pre_existing_batch_less_point_acquires(self, sdk):
        # E2E-10 row 14: a batch-less pre-existing LIVE point that dedup-hits
        # ACQUIRES the bundle's batch_id on its first dedup-hit.
        pid = sdk.create_point("claim", "Rust is memory-safe by default.")["id"]
        sdk.update_point(pid, status="live")  # months-old LIVE graph shape
        res = sdk.ingest(_full_bundle())
        assert res["deduped"]["points"] == 1
        assert res["batch_id"] is not None
        assert sdk.get_point(pid)["batch_id"] == res["batch_id"]

    def test_stamp_when_absent_crash_position_f(self, sdk):
        # Crash position (f): create_point succeeded, the stamp never landed —
        # the retry's dedup hit applies stamp-when-absent (completing an
        # interrupted stamp, not rewriting provenance).
        pid = sdk.create_point("claim", "Rust is memory-safe by default.")["id"]
        assert "batch_id" not in sdk.get_point(pid)
        res = sdk.ingest(_full_bundle())
        assert res["deduped"]["points"] == 1
        assert sdk.get_point(pid)["batch_id"] == res["batch_id"]

    def test_h_record_repair_reemits_only_missing_record(self, sdk_logged):
        # Crash sub-position (h): the prop SET landed but the JSONL record did
        # not → the retry re-emits ONLY the missing record (never duplicates
        # existing records, never rewrites the prop).
        res = sdk_logged.ingest(_full_bundle())
        bid = res["batch_id"]
        pid = res["ids"]["points"][0]
        records = _batch_id_records(sdk_logged)
        assert len(records) == 3
        # Simulate the (h) crash: drop pid's BatchIdStamped record from the log
        kept = [e for e in records if not (
            e.get("type") == "BatchIdStamped" and e.get("id") == pid)]
        assert len(kept) == 2
        with open(sdk_logged._log_path, "w", encoding="utf-8") as f:
            for e in _batch_records(sdk_logged):
                if not (e.get("type") == "BatchIdStamped"
                        and e.get("id") == pid):
                    f.write(json.dumps(e) + "\n")
        # Prop is PRESENT (the SET survived); the record is missing.
        assert sdk_logged.get_point(pid)["batch_id"] == bid
        # Retry → repair: exactly the missing record re-emitted.
        res2 = sdk_logged.ingest(_full_bundle())
        assert res2["batch_id"] == bid
        repaired = _batch_id_records(sdk_logged)
        assert len(repaired) == 3
        assert any(
            e.get("type") == "BatchIdStamped" and e.get("id") == pid
            for e in repaired
        )
        assert sdk_logged.get_point(pid)["batch_id"] == bid  # prop untouched

    def test_operator_dedup_hit_stamp_when_absent(self, sdk):
        # Crash position (g): an operator Point created but never stamped is
        # stamped on the retry's _find_operator dedup hit.
        res = sdk.ingest(_full_bundle())
        bid = res["batch_id"]
        op_id = next(i for i in res["ids"]["connections"] if isinstance(i, str))
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) REMOVE n.batch_id",
            params={"id": op_id},
        )
        assert "batch_id" not in sdk.get_point(op_id)
        res2 = sdk.ingest(_full_bundle())
        assert res2["deduped"]["connections"] == 3
        assert sdk.get_point(op_id)["batch_id"] == bid

    def test_jsonl_record_shape_and_content_hash(self, sdk_logged):
        # §4.2 rebuild-durability record: {id, batch_id, content_hash?} via
        # the JSONL branch — plain points carry the dedup-key content_hash;
        # operator Points (not content-hash dedupable) omit it.
        res = sdk_logged.ingest(_full_bundle())
        bid = res["batch_id"]
        records = _batch_id_records(sdk_logged)
        assert len(records) == 3
        plain_ids = set(res["ids"]["points"])
        op_ids = {i for i in res["ids"]["connections"] if isinstance(i, str)}
        for rec in records:
            assert rec["type"] == "BatchIdStamped"
            assert rec["batch_id"] == bid
            assert rec["id"] in plain_ids | op_ids
            assert rec.get("projection_version") == 2  # _emit_event JSONL
        plain_recs = [r for r in records if r["id"] in plain_ids]
        op_recs = [r for r in records if r["id"] in op_ids]
        assert len(plain_recs) == 2 and len(op_recs) == 1
        assert all(r.get("content_hash") for r in plain_recs)
        assert "content_hash" not in op_recs[0]

    def test_no_graph_event_store_write_for_batch_id_record(self, sdk_logged):
        # _stamp_batch_id is a single SET + JSONL-ONLY record — NO GraphEvent
        # store write (BatchIdStamped is not in _GRAPH_EVENT_TYPES).
        sdk_logged.ingest(_full_bundle())
        assert _count(
            sdk_logged,
            "MATCH (e:GraphEvent {type:'BatchIdStamped'}) RETURN count(e)",
        ) == 0

    def test_ingest_scoped_guard_rejects_batch_id_in_props(self, sdk):
        # §5.2 check 8 (INGEST-SCOPED guard): batch_id is server-managed — a
        # bundle must not forge it. (The GLOBAL _sanitize_props rejection is
        # deferred until #785 adopts _stamp_batch_id — GATE-2 Q5.)
        for section, item in (
            ("points", {"kind": "claim", "content": "x", "batch_id": "FORGED"}),
            ("entities", {"type": "subject", "name": "x", "batch_id": "FORGED"}),
            ("sources", {"url": "https://x.example", "sourceKind": "report",
                         "batch_id": "FORGED"}),
            ("connections", {"from": "a", "to": "b", "operator": "IMPL",
                             "batch_id": "FORGED"}),
        ):
            with pytest.raises(ValueError, match="batch_id"):
                sdk.ingest({section: [item]})

    def test_guard_rejects_before_any_mutation(self, sdk):
        # The ingest-scoped guard fires BEFORE any node is written (Phase 1 —
        # zero-mutation discipline, J2).
        with pytest.raises(ValueError, match="batch_id"):
            sdk.ingest({"points": [{"kind": "claim", "content": "x",
                                    "batch_id": "FORGED"}]})
        assert _point_count(sdk) == 0

    def test_e2e56_batch_id_invariance_via_ingest(self, sdk):
        # E2E-5.6 end-to-end: re-serialized bundles (shuffled keys + reordered
        # lists + renamed refs) ingest to ONE identical batch_id on fresh
        # graphs — guards cross-graph equality and crash-retry single id.
        canonical = _full_bundle()
        bid = sdk.ingest(canonical)["batch_id"]

        def _shuffle(value):
            if isinstance(value, dict):
                return {_shuffle(k): _shuffle(v)
                        for k, v in reversed(list(value.items()))}
            if isinstance(value, list):
                return [_shuffle(v) for v in value]
            return value

        renamed = {"p1": "alpha", "p2": "beta", "s1": "gamma",
                   "src1": "delta", "c1": "1", "c2": "2", "c3": "3"}

        def _rename(x):
            if isinstance(x, dict):
                return {_rename(k): _rename(v) for k, v in x.items()}
            if isinstance(x, list):
                return [_rename(v) for v in x]
            if isinstance(x, str) and x in renamed:
                return renamed[x]
            return x

        variant = _rename(_shuffle(canonical))
        variant["connections"] = list(reversed(variant["connections"]))
        base = tempfile.mkdtemp(prefix="tortoise_ingest_batch_id_v_")
        sdk2 = TortoiseSDK(os.path.join(base, "test.db"))
        try:
            assert sdk2.ingest(variant)["batch_id"] == bid
        finally:
            sdk2.close()


# ═══════════════════════════════════════════════════════════════════════
# A3 (§5.5, #1053): the operator-dedup richer return + §8 direct-edge routing
# ═══════════════════════════════════════════════════════════════════════

class TestA3DirectEdgeRouting:
    """§8 connection-routing (A3): a PLAIN {operator: IMPL|NAND} connection
    is OPERATOR-LESS — a direct edge with label/direction/batch_id on the
    edge, no operator node; the reify:true / mitigation paths still create
    the operator."""

    def test_plain_impl_is_operator_less_direct_edge(self, sdk):
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A supports B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "label": "supports"}],
        }
        res = sdk.ingest(bundle, granularity="granular", promotion_policy="auto")
        assert _edge_count(sdk, "IMPL") == 1
        assert _operator_count(sdk, "IMPL") == 0
        # label + batch_id on the EDGE (not a node)
        row = sdk._get_proj().g.query(
            "MATCH ()-[r:IMPL]->() RETURN r.label, r.batch_id").result_set
        assert row[0][0] == "supports"
        assert row[0][1] == res["batch_id"]
        # direct-edge conn_result shape (plan §5.5 route matrix, cycle-22)
        conn = next(e for e in res["results"] if e["section"] == "connections")
        assert set(conn["result"].keys()) == {"direct_edge", "from", "to", "deduped"}
        assert conn["result"]["deduped"] is False
        # idempotent re-ingest → deduped, one edge
        r2 = sdk.ingest(bundle, granularity="granular", promotion_policy="auto")
        assert _edge_count(sdk, "IMPL") == 1
        conn2 = next(e for e in r2["results"] if e["section"] == "connections")
        assert conn2["result"]["deduped"] is True

    def test_plain_nand_direction_default_unidirectional(self, sdk):
        """CYCLE-25: a direction-absent plain NAND direct edge carries the
        extraction default 'unidirectional' on the edge."""
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "NAND"}],
        }
        sdk.ingest(bundle)
        row = sdk._get_proj().g.query(
            "MATCH ()-[r:NAND]->() RETURN r.direction").result_set
        assert row[0][0] == "unidirectional"
        assert _operator_count(sdk, "NAND") == 0


class TestA3OperatorDedupRicherReturn:
    """§5.5: _find_operator returns exact-hit | partial-absorb | decline | miss."""

    def test_exact_hit_direction_absent_matches_default(self, sdk):
        """CYCLE-17/25: a direction-ABSENT IMPL retry matches its own run-1
        operator (stored 'bidirectional') — exactly-once on the default shape."""
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A supports B"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                              "reify": True}],
        }
        first = sdk.ingest(bundle)
        second = sdk.ingest(bundle)
        assert _operator_count(sdk, "IMPL") == 1
        assert second["deduped"]["connections"] == 1
        assert second["ids"]["connections"][0] == first["ids"]["connections"][0]

    def test_direction_absent_nand_matches_stored_unidirectional(self, sdk):
        """CYCLE-25: a direction-absent NAND retry matches its run-1
        stored-'unidirectional' operator (exactly-once)."""
        bundle = {
            "points": [
                {"ref": "pA", "kind": "claim", "content": "A"},
                {"ref": "pB", "kind": "claim", "content": "B"},
            ],
            "connections": [{"from": "pA", "to": "pB", "operator": "NAND",
                              "reify": True}],
        }
        first = sdk.ingest(bundle)  # noqa: F841
        second = sdk.ingest(bundle)
        assert _operator_count(sdk, "NAND") == 1
        assert second["deduped"]["connections"] == 1

    def test_partial_absorb_completes_input_set(self, sdk):
        """§5.5 partial-absorb: a NULL-status partial operator on a PROPER
        SUBSET absorbs the retry — completes the written set on the REAL
        operator (is_operator:true, per-input multiplicity 1), one node per
        id, operator_absorb_completed warning."""
        pa = sdk.create_point("claim", "A")["id"]
        pb = sdk.create_point("claim", "B")["id"]
        pc = sdk.create_point("claim", "C")["id"]
        partial = sdk.create_operator("IMPL", pa, [pc], promote_source=False)
        bundle = {
            "points": [], "entities": [], "sources": [],
            "connections": [{"from": pa, "to": [pb, pc], "operator": "IMPL",
                              "reify": True}],
        }
        res = sdk.ingest(bundle, promotion_policy="auto")
        assert _operator_count(sdk, "IMPL") == 1
        assert res["deduped"]["connections"] == 1
        assert res["ids"]["connections"][0] == partial["id"]
        # EXACTLY ONE node per id (no duplicate from the edge-completion MERGE)
        for pid in (partial["id"], pa, pb, pc):
            assert _count(sdk, "MATCH (n:Point {id:$p}) RETURN count(n)",
                          {"p": pid}) == 1, f"duplicate node for {pid}"
        # the REAL operator (is_operator:true) has the full set, multiplicity 1
        n = _count(
            sdk,
            "MATCH (o:Point {is_operator:true, id:$p})-[:IMPL]->() "
            "RETURN count(*)",
            {"p": partial["id"]},
        )
        assert n == 3  # [A, B, C] completed
        assert any("operator_absorb_completed" in w for w in res["warnings"])

    def test_label_dropped_resubmit_does_not_absorb_labeled(self, sdk):
        """CYCLE-14/15: a label-ABSENT retry must NOT absorb a LABELED
        operator (the cross-absorption semantic-theft class) — strict miss →
        a fresh operator + partial_operator_residue on the old one."""
        pa = sdk.create_point("claim", "A")["id"]
        pb = sdk.create_point("claim", "B")["id"]
        sdk.create_operator("IMPL", pa, [pb], label="L", promote_source=False)
        bundle = {
            "points": [], "entities": [], "sources": [],
            "connections": [{"from": pa, "to": pb, "operator": "IMPL",
                              "reify": True}],
        }
        res = sdk.ingest(bundle)  # noqa: F841
        assert _operator_count(sdk, "IMPL") == 2  # strict miss → new operator
        # the labeled partial stays as residue (never absorbed by a no-label
        # retry — the semantic-theft class stays closed)
        labeled = sdk._get_proj().g.query(
            "MATCH (o:Point {is_operator:true, label:'L'}) RETURN count(o)"
        ).result_set[0][0]
        assert labeled == 1

    def test_decline_on_two_candidates(self, sdk):
        """§5.5: TWO+ qualifying partials → decline + partial_operator_residue
        warning; the retry creates a fresh complete operator."""
        pa = sdk.create_point("claim", "A")["id"]
        pb = sdk.create_point("claim", "B")["id"]
        pc = sdk.create_point("claim", "C")["id"]
        sdk.create_operator("IMPL", pa, [pc], promote_source=False)
        sdk.create_operator("IMPL", pa, [pb], promote_source=False)
        bundle = {
            "points": [], "entities": [], "sources": [],
            "connections": [{"from": pa, "to": [pb, pc], "operator": "IMPL",
                              "reify": True}],
        }
        res = sdk.ingest(bundle)
        assert _operator_count(sdk, "IMPL") == 3  # 2 residue + 1 fresh
        assert res["created"]["connections"] == 1
        assert any("partial_operator_residue" in w for w in res["warnings"])

    def test_rebuild_survives_absorbed_operator(self, sdk, tmp_path):
        """§5.5 cycle-13: the absorbed operator survives post-rebuild at the
        FULL input set, per-input multiplicity 1 (the #548 graph-only-point
        snapshot captures it from its edges), count()==1 per id."""
        pa = sdk.create_point("claim", "A")["id"]
        pb = sdk.create_point("claim", "B")["id"]
        pc = sdk.create_point("claim", "C")["id"]
        partial = sdk.create_operator("IMPL", pa, [pc], promote_source=False)
        bundle = {
            "points": [], "entities": [], "sources": [],
            "connections": [{"from": pa, "to": [pb, pc], "operator": "IMPL",
                              "reify": True}],
        }
        sdk.ingest(bundle)
        # pre-rebuild: the real operator at the full set
        n = _count(sdk,
                   "MATCH (o:Point {is_operator:true, id:$p})-[:IMPL]->() "
                   "RETURN count(*)", {"p": partial["id"]})
        assert n == 3
        # write a journal + rebuild (the #548 snapshot synthesizes OperatorAdded
        # for graph-only operator points from their edges)
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        log_path = str(events_dir / "events.jsonl")
        sdk._event_log_path = log_path
        sdk._get_proj().rebuild_all(str(events_dir))
        # post-rebuild: the operator survives at the full input set
        assert _count(sdk, "MATCH (n:Point {id:$p}) RETURN count(n)",
                      {"p": partial["id"]}) == 1
        n2 = _count(sdk,
                    "MATCH (o:Point {is_operator:true, id:$p})-[:IMPL]->() "
                    "RETURN count(*)", {"p": partial["id"]})
        assert n2 == 3
