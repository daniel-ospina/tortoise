"""Epic #902 W4 A1 — shared pre-validation check helpers.

Tests the plan §5.2 checks 1-8 as consumed by BOTH `_validate_bundle` (Phase
1 — collecting ALL violations, zero mutation) and ingest()'s Phase-2
defense-in-depth raises. The key indicator: **Phase 1 catches EVERY violation
class Phase 2 can raise** (the parity test) — zero Phase-2-only raise classes.

CYCLE-25 amendments covered: pointKind vocabulary (statement canonical, legacy
write-compat, `event` REJECTED, kind-absent → statement), quote ≤200 permitted,
c_cal rejected. CYCLE-26: check-5's fail-closed policy-feasibility rejection is
REMOVED (gated operator connections accepted); the retained piece (gated +
explicit status:"live" → violation) is asserted.

Runnable with: .venv/bin/python -m pytest tests/test_ingest_validation.py -v
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
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ingest_val_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _query(sdk, cypher, params)
    return int(rows[0][0]) if rows else 0


def _point_count(sdk) -> int:
    return _count(
        sdk,
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN count(n)",
    )


def _operator_count(sdk, op_type: str = "IMPL") -> int:
    return _count(
        sdk,
        "MATCH (o:Point {is_operator:true, op_type:$op}) RETURN count(o)",
        {"op": op_type},
    )


def _edge_count(sdk, rel: str) -> int:
    return _count(sdk, f"MATCH ()-[r:{rel}]->() RETURN count(r)")


def _points_pair(refs=("pA", "pB"), contents=("A implies B", "B")):
    return {"points": [
        {"ref": refs[0], "kind": "statement", "content": contents[0]},
        {"ref": refs[1], "kind": "statement", "content": contents[1]},
    ]}


# ── Phase-1/2 parity (issue #1047 indicator 2) ──────────────────────

class TestPhase1Phase2Parity:
    """Every violation class Phase 2 (mid-write raises) can raise must ALSO
    be caught by Phase 1 (_validate_bundle) — zero Phase-2-only classes.

    Parity holds by construction (both phases consume the same shared check
    helpers with the same message shapes); this test pins it against
    regression by exercising each class through BOTH surfaces."""

    def _assert_both_phases(self, sdk, bundle, *, promotion_policy="gated"):
        viols = sdk._validate_bundle(bundle, promotion_policy=promotion_policy)
        assert viols, "Phase 1 missed a violation class Phase 2 raises"
        with pytest.raises(ValueError) as exc:
            sdk.ingest(bundle, promotion_policy=promotion_policy)
        # Phase 2's raise carries the FIRST Phase-1 message (the shipped
        # message contract) — message-level parity.
        assert str(exc.value) == viols[0]["message"]

    def test_item_shape_classes(self, sdk):
        cases = {
            "source not dict": {"sources": ["nope"]},
            "point missing content": {"points": [{"kind": "statement"}]},
            "source missing url": {"sources": [{"sourceKind": "report"}]},
            "source missing sourceKind": {"sources": [{"url": "https://x.example"}]},
            "entity missing type": {"entities": [{"name": "x"}]},
            "entity invalid type": {"entities": [{"type": "gadget", "name": "x"}]},
            "entity missing name": {"entities": [{"type": "subject"}]},
            "event entity missing eventKind": {"entities": [{"type": "event", "name": "e"}]},
            "document entity missing documentKind": {"entities": [{"type": "document", "name": "d"}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_kind_vocabulary_class(self, sdk):
        self._assert_both_phases(
            sdk, {"points": [{"kind": "event", "content": "x"}]})

    def test_ref_classes(self, sdk):
        cases = {
            "duplicate refs": {
                "points": [
                    {"ref": "p", "kind": "statement", "content": "a"},
                    {"ref": "p", "kind": "statement", "content": "b"},
                ],
            },
            "ULID-shaped ref": {
                "points": [{"ref": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                            "kind": "statement", "content": "a"}],
            },
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_nonstring_fields_classes(self, sdk):
        # REVIEW-FIX P1/P2 (cycle-26): non-string fields must be Phase-1
        # violations (Phase 2 would raise/crash AFTER partial commits).
        cases = {
            "non-string entity type": {"entities": [{"type": 5, "name": "x"}]},
            "non-string point content": {"points": [
                {"kind": "statement", "content": 5}]},
            "non-string connection from": _points_pair() | {
                "connections": [{"from": 5, "to": "pB", "operator": "IMPL"}]},
            "non-string to item": _points_pair() | {
                "connections": [{"from": "pA", "to": [5], "operator": "IMPL"}]},
            "non-string kind": {"points": [
                {"kind": 5, "content": "x"}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_connection_contract_classes(self, sdk):
        cases = {
            "missing from": {"points": [{"ref": "p", "kind": "statement", "content": "a"}],
                             "connections": [{"to": "p", "operator": "IMPL"}]},
            "missing to": {"points": [{"ref": "p", "kind": "statement", "content": "a"}],
                           "connections": [{"from": "p", "operator": "IMPL"}]},
            "both relation and operator": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "relation": "uses"}]},
            "neither relation nor operator": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB"}]},
            "unknown operator": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "SUPPORTS"}]},
            "unknown relation": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "relation": "notARelation"}]},
            "IMPL self-edge": _points_pair() | {"connections": [
                {"from": "pA", "to": "pA", "operator": "IMPL"}]},
            "to not list/str": _points_pair() | {"connections": [
                {"from": "pA", "to": 5, "operator": "IMPL"}]},
            "empty to (relation)": _points_pair() | {"connections": [
                {"from": "pA", "to": [], "relation": "hasPart"}]},
            "empty to (operator)": _points_pair() | {"connections": [
                {"from": "pA", "to": [], "operator": "IMPL"}]},
            "multi-item to on plain IMPL": _points_pair() | {"connections": [
                {"from": "pA", "to": ["pB", "pA"], "operator": "IMPL"}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_field_validity_classes(self, sdk):
        cases = {
            "bad direction": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "direction": "sideways"}]},
            "bad confidence": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "confidence": 1.7}]},
            "bad weight": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "weight": -0.5}]},
            "empty mitigation reason": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "mitigation": {"reason": "", "strength": 0.2}}]},
            "bad mitigation strength": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "mitigation": {"reason": "r", "strength": 2.0}}]},
            "relation carries direction": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "relation": "hasPart",
                 "direction": "unidirectional"}]},
            "relation carries confidence": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "relation": "hasPart",
                 "confidence": 0.5}]},
            "reify carries confidence": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "reify": True,
                 "confidence": 0.5}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_duplicate_classes(self, sdk):
        cases = {
            "direction conflict": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "direction": "bidirectional"},
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "direction": "unidirectional"}]},
            "confidence conflict": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "confidence": 0.5},
                {"from": "pA", "to": "pB", "operator": "IMPL", "confidence": 0.7}]},
            "mitigation strength conflict": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "mitigation": {"reason": "r", "strength": 0.2}},
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "mitigation": {"reason": "r", "strength": 0.5}}]},
            "mitigation reason conflict (same label)": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L1",
                 "mitigation": {"reason": "r1", "strength": 0.2}},
                {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L1",
                 "mitigation": {"reason": "r2", "strength": 0.2}}]},
            "label absent vs present": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L1"},
                {"from": "pA", "to": "pB", "operator": "IMPL"}]},
            "label conflict on direct path": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L1"},
                {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L2"}]},
            "NAND absent vs bidirectional": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "NAND"},
                {"from": "pA", "to": "pB", "operator": "NAND",
                 "direction": "bidirectional"}]},
            "IMPL absent vs unidirectional": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL"},
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "direction": "unidirectional"}]},
            "reversed bidirectional pair": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "operator": "IMPL",
                 "direction": "bidirectional"},
                {"from": "pB", "to": "pA", "operator": "IMPL"}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_gated_live_and_batch_guard_classes(self, sdk):
        cases = {
            "gated explicit live": {"points": [
                {"kind": "statement", "content": "a", "status": "live"}]},
            "batch_id in point item": {"points": [
                {"kind": "statement", "content": "a", "batch_id": "01J8"}]},
            "batch_id in source item": {"sources": [
                {"url": "https://x.example", "sourceKind": "report",
                 "batch_id": "01J8"}]},
            "c_cal on point": {"points": [
                {"kind": "statement", "content": "a", "c_cal": 0.5}]},
            "quote over cap": {"points": [
                {"kind": "statement", "content": "a", "quote": "x" * 201}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_endpoint_classes_need_pre_seeded_graph(self, sdk):
        existing = sdk.create_point("statement", "pre-existing point",
                                    status="live")
        terminal = sdk.create_point("statement", "terminal point",
                                    status="superseded")
        src = sdk.create_source("https://pre.example/s1", "report")
        src_url = src.get("url") or src.get("id")

        cases = {
            "external endpoint does not exist": {
                "points": [{"ref": "p1", "kind": "statement", "content": "a"}],
                "connections": [{"from": "p1", "to": "ghost-ref",
                                 "operator": "IMPL"}]},
            "external endpoint is a Source (typed)": {
                "points": [{"ref": "p1", "kind": "statement", "content": "a"}],
                "connections": [{"from": "p1", "to": src_url,
                                 "operator": "IMPL"}]},
            "external terminal endpoint on direct edge": {
                "points": [{"ref": "p1", "kind": "statement", "content": "a"}],
                "connections": [{"from": "p1", "to": terminal["id"],
                                 "operator": "IMPL"}]},
            "bundle-local source item as endpoint (typed)": {
                "points": [{"ref": "p1", "kind": "statement", "content": "a"}],
                "sources": [{"ref": "sL", "url": "https://x.example",
                             "sourceKind": "report"}],
                "connections": [{"from": "p1", "to": "sL", "operator": "IMPL"}]},
            "bundle-local operator-shaped point item as endpoint (typed)": {
                "points": [
                    {"ref": "p1", "kind": "statement", "content": "a"},
                    {"ref": "pO", "kind": "statement", "content": "op-shaped",
                     "is_operator": True},
                ],
                "connections": [{"from": "p1", "to": "pO", "operator": "IMPL"}]},
            "bundle-local point hitting terminal dedup (direct edge)": {
                "points": [{"ref": "p1", "kind": "statement", "content": "a"},
                           {"ref": "pT", "kind": "statement",
                            "content": "terminal point"}],
                "connections": [{"from": "p1", "to": "pT", "operator": "IMPL"}]},
        }
        for name, bundle in cases.items():
            self._assert_both_phases(sdk, bundle)

    def test_gated_operator_connections_accepted_under_parity(self, sdk):
        # CYCLE-26: check-5's fail-closed policy-feasibility rejection is
        # REMOVED — gated + operator-requiring connections are ACCEPTED.
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL",
             "mitigation": {"reason": "r", "strength": 0.2}}]}
        assert sdk._validate_bundle(bundle, promotion_policy="gated") == []
        res = sdk.ingest(bundle)  # default gated — must commit, not raise
        assert res["created"]["connections"] == 1


# ── E2E-1 matrix: aggregated violations + zero mutations ────────────

class TestE2E1Matrix:
    """E2E-1: malformed bulk bundle rejected with ALL violations, zero
    mutations, under BOTH granularities (Phase 1 is mode-invariant)."""

    def test_aggregated_violations_both_granularities(self, sdk):
        # c3 needs an EXISTING wrong-type endpoint (a pre-seeded Source).
        pre = sdk.create_source("https://pre.example/s", "report")
        bundle = {
            "points": [
                {"ref": "p1", "kind": "statement", "content": "one"},
                {"ref": "p2", "kind": "statement", "content": "two"},
                {"ref": "p3", "kind": "statement", "content": "three"},
                {"ref": "pO", "kind": "statement", "content": "op-shaped",
                 "is_operator": True},
            ],
            "sources": [{"ref": "srcL", "url": "https://local.example/s",
                         "sourceKind": "report"}],
            "connections": [
                {"from": "p1", "to": "p2", "operator": "IMPL"},           # c0 valid
                {"from": "p3", "to": "01GHOSTREF", "operator": "IMPL"},   # c1 ghost
                {"from": "p1", "to": "p2", "operator": "SUPPORTS"},       # c2 vocab
                {"from": "p1", "to": pre.get("url"), "operator": "IMPL"},  # c3 typed ext
                {"from": "p1", "to": "srcL", "operator": "IMPL"},         # c4 typed local
                {"from": "p2", "to": "pO", "operator": "IMPL"},           # c5 typed local op
                {"from": "p1", "to": ["p2", "p3"], "operator": "IMPL"},   # c7 multi-item
            ],
        }
        for granularity in ("bulk", "granular"):
            viols = sdk._validate_bundle(bundle)
            assert len(viols) >= 6, f"expected aggregated violations ({granularity})"
            messages = [v["message"] for v in viols]
            joined = "\n".join(messages)
            assert any("does not exist" in m for m in messages), joined       # c1
            assert any("SUPPORTS" in m for m in messages), joined             # c2
            assert any("must be a plain Point" in m for m in messages), joined  # c3/c4/c5
            assert any("multi-item 'to'" in m for m in messages), joined      # c7
            # zero mutations — only the pre-seeded Source exists
            assert _point_count(sdk) == 0
            assert _operator_count(sdk) == 0
            assert _edge_count(sdk, "IMPL") == 0
            assert _count(sdk, "MATCH (s:Source) RETURN count(s)") == 1
            with pytest.raises(ValueError):
                sdk.ingest(bundle, granularity=granularity)

    def test_corrected_bundle_commits_and_reingests_clean(self, sdk):
        bundle = {
            "points": [
                {"ref": "p1", "kind": "statement", "content": "one"},
                {"ref": "p2", "kind": "statement", "content": "two"},
                {"ref": "p3", "kind": "statement", "content": "three"},
            ],
            "connections": [
                {"from": "p1", "to": "p2", "operator": "IMPL"},
                {"from": "p1", "to": "p3", "operator": "NAND"},
            ],
        }
        assert sdk._validate_bundle(bundle) == []
        first = sdk.ingest(bundle)
        assert first["created"]["points"] == 3
        assert first["created"]["connections"] == 2
        second = sdk.ingest(bundle)
        assert second["created"]["points"] == 0
        assert second["created"]["connections"] == 0
        assert second["deduped"]["connections"] == 2  # exactly-once


# ── CYCLE-25 rows ───────────────────────────────────────────────────

class TestCycle25Vocabulary:
    def test_statement_is_canonical(self, sdk):
        res = sdk.ingest({"points": [{"kind": "statement", "content": "s"}]})
        pid = res["ids"]["points"][0]
        assert sdk.get_point(pid)["pointKind"] == "statement"

    def test_legacy_kind_write_compat(self, sdk):
        # A legacy write-compat kind (decision) still writes (compat-only).
        res = sdk.ingest({"points": [{"kind": "decision", "content": "d"}]})
        pid = res["ids"]["points"][0]
        assert sdk.get_point(pid)["pointKind"] == "decision"

    def test_event_pointkind_rejected_phase1(self, sdk):
        viols = sdk._validate_bundle(
            {"points": [{"kind": "event", "content": "x"}]})
        assert any("'event'" in v["message"] for v in viols)
        with pytest.raises(ValueError, match="event"):
            sdk.ingest({"points": [{"kind": "event", "content": "x"}]})

    def test_quote_permitted_within_cap(self, sdk):
        bundle = {"points": [{"kind": "statement", "content": "a",
                              "quote": "provenance quote"}]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        pid = res["ids"]["points"][0]
        assert sdk.get_point(pid).get("quote") == "provenance quote"


# ── duplicates: clean dedup vs conflict (§5.2.7) ────────────────────

class TestDuplicateConnections:
    def test_identical_duplicates_clean_dedup(self, sdk):
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL"},
            {"from": "pA", "to": "pB", "operator": "IMPL"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1
        assert res["deduped"]["connections"] == 1
        assert _operator_count(sdk, "IMPL") == 1

    def test_iml_absent_equals_bidirectional(self, sdk):
        # CYCLE-25 per-op_type canonicalization: IMPL absent ≡ bidirectional
        # → IDENTICAL duplicates → clean dedup (no conflict).
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL"},
            {"from": "pA", "to": "pB", "operator": "IMPL",
             "direction": "bidirectional"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["deduped"]["connections"] == 1

    def test_nand_absent_equals_unidirectional(self, sdk):
        # NAND absent ≡ unidirectional (the extraction default) → IDENTICAL
        # duplicates per the §5.2.7 predicate → Phase 1 accepts (no conflict).
        # (The write-time dedup of the direction-absent NAND lookup is A3's
        # _find_operator builder change — asserted there, not here.)
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "NAND"},
            {"from": "pA", "to": "pB", "operator": "NAND",
             "direction": "unidirectional"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] >= 1  # commits; dedup count is A3's

    def test_external_plain_point_endpoint_accepted(self, sdk):
        # Positive control: a pre-existing plain Point is a legal endpoint on
        # the operator route (Phase 1 accepts, ingest commits).
        existing = sdk.create_point("statement", "pre-existing point",
                                    status="live")
        bundle = {
            "points": [{"ref": "p1", "kind": "statement", "content": "a"}],
            "connections": [{"from": "p1", "to": existing["id"],
                             "operator": "IMPL"}],
        }
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1

    def test_label_differing_mitigation_pairs_legal(self, sdk):
        # cycle-13 boundary: label-differing same-pair IMPL+mitigation pairs
        # are LEGAL on the operator path (two operators, one mitigation each).
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L1",
             "mitigation": {"reason": "r1", "strength": 0.2}},
            {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L2",
             "mitigation": {"reason": "r2", "strength": 0.2}},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 2

    def test_reversed_unidirectional_pair_legal(self, sdk):
        # a→b AND b→a where BOTH are unidirectional is legal (two directional
        # edges); only a bidirectional half makes it a contradiction.
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL",
             "direction": "unidirectional"},
            {"from": "pB", "to": "pA", "operator": "IMPL",
             "direction": "unidirectional"},
        ]}
        assert sdk._validate_bundle(bundle) == []


# ── terminal-status guard (E2E-11.7: direct-edge rejection) ─────────

class TestTerminalStatusGuard:
    def test_direct_edge_terminal_external_endpoint_rejected(self, sdk):
        terminal = sdk.create_point("statement", "old claim",
                                    status="superseded")
        bundle = {
            "points": [{"ref": "p1", "kind": "statement", "content": "new"}],
            "connections": [{"from": "p1", "to": terminal["id"],
                             "operator": "IMPL"}],
        }
        viols = sdk._validate_bundle(bundle)
        assert any("terminal" in v["message"] for v in viols)
        with pytest.raises(ValueError):
            sdk.ingest(bundle)

    def test_operator_mediated_terminal_endpoint_accepted(self, sdk):
        # CARVE-OUT (cycle-4): operator-mediated connections to terminal
        # points keep today's behavior (established editorial surface) — the
        # guard is direct-edge-scoped only.
        terminal = sdk.create_point("statement", "old claim",
                                    status="superseded")
        bundle = {
            "points": [{"ref": "p1", "kind": "statement", "content": "new"}],
            "connections": [{"from": "p1", "to": terminal["id"], "operator": "IMPL",
                             "mitigation": {"reason": "r", "strength": 0.2}}],
        }
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1

    def test_bundle_local_point_hitting_terminal_dedup_rejected(self, sdk):
        # cycle-17/18: a bundle-local point item whose (content_hash, kind)
        # dedup-hits an EXISTING terminal point is a Phase-1 violation on a
        # direct-edge connection (E2E-11.7 first-submission leg).
        sdk.create_point("statement", "same content", status="superseded")
        bundle = {
            "points": [
                {"ref": "p1", "kind": "statement", "content": "other"},
                {"ref": "pT", "kind": "statement", "content": "same content"},
            ],
            "connections": [{"from": "p1", "to": "pT", "operator": "IMPL"}],
        }
        viols = sdk._validate_bundle(bundle)
        assert any("terminal" in v["message"] for v in viols), viols
        with pytest.raises(ValueError):
            sdk.ingest(bundle)
