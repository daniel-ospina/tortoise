"""Epic #902 W4 A1 — shared pre-validation check helpers.

Tests the plan §5.2 checks 1-8 as consumed by BOTH `_validate_bundle` (Phase
1 — collecting ALL violations, zero mutation) and ingest()'s Phase-2
defense-in-depth raises. The key indicator: **every violation class the shared
checks own is caught by Phase 1 too** (the parity test) — no Phase-2-only class
among THEM. It is not a universal: the `_sanitize_props` reject set beyond the
keys the ingest gate owns (an ENTITY item's caller-supplied `id`, the belief
props `credibility`/`ep_alpha`/`ep_beta`/`baseline_set`/`baseline_source`)
passes Phase 1 and raises mid-write — that is #8755, and it is why the wording
above is "the classes these checks own" rather than "every class".

CYCLE-25 amendments covered: pointKind vocabulary (statement canonical, legacy
write-compat, `event` REJECTED, kind-absent → statement), quote ≤200 permitted,
c_cal rejected. CYCLE-26: check-5's fail-closed policy-feasibility rejection is
REMOVED (gated operator connections accepted); the retained piece (gated +
explicit status:"live" → violation) is asserted.

Runnable with: .venv/bin/python -m pytest tests/test_ingest_validation.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.exceptions import BundleValidationError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ingest_val_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


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


def _object_pair(refs=("oA", "oB"), names=("Object A", "Object B")):
    """Object↔Object — the common non-Point-bearing pair, so the shape the
    direct-edge rules still govern (#8499 probes `:Point` only; `:Subject`/
    `:Event` pairs keep the direct edge too, but Object↔Object is the pair a
    bundle can mint with no pre-seeded graph)."""
    return {"entities": [
        {"ref": refs[0], "type": "object", "name": names[0]},
        {"ref": refs[1], "type": "object", "name": names[1]},
    ]}


def _object_triple(refs=("oA", "oB", "oC")):
    """A three-Object bundle — the shape the multi-item `to` rule still
    governs (#9173): no plain `:Point` endpoint, so nothing is promoted and
    `create_direct_edge` (ONE target) is what the connection would reach."""
    return {"entities": [
        {"ref": r, "type": "object", "name": f"Object {r}"} for r in refs
    ]}


# ── Phase-1/2 parity (issue #1047 indicator 2) ──────────────────────

class TestPhase1Phase2Parity:
    """Every violation class these shared checks own must ALSO be caught by
    Phase 1 (_validate_bundle) — no Phase-2-only class among them.

    Parity holds by construction (both phases consume the same shared check
    helpers with the same message shapes); this test pins it against
    regression by exercising each class through BOTH surfaces. The classes the
    helpers do NOT own (#8755's `_sanitize_props` reject set) are outside this
    claim by design, not by oversight."""

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
        for name, bundle in cases.items():  # noqa: B007
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
            # #1553: prefixed entity ids (label[:3] + '-' + sha256[:26], e.g.
            # sub-<hex26>) shadow existing nodes via refs.get(x, x) just like
            # bare ULIDs — _is_entity_id covers both shapes.
            "prefixed entity-id-shaped ref": {
                "points": [{"ref": "sub-0123456789abcdef0123456789",
                            "kind": "statement", "content": "a"}],
            },
            # #3586 review: rejection is CANONICAL-shape-scoped — a ref whose
            # digest no minter emits cannot shadow a node, so it is ACCEPTED.
            # Pinned by test_noncanonical_digest_ref_accepted below (this table
            # is violations-only).
        }
        for name, bundle in cases.items():  # noqa: B007
            self._assert_both_phases(sdk, bundle)

    def test_noncanonical_digest_ref_accepted(self, sdk):
        """#3586 review: `_check_refs`'s hard rejection must use the CANONICAL
        id shape, not the lenient about*-seam one.

        A ref whose digest is not a shape any minter emits (`pr-<hex16>`;
        `_entity_name_id` is ``label[:3]-<sha256[:26]>``) cannot shadow a
        canonical node id, so rejecting it fails the WHOLE bundle closed on
        input that used to ingest. Before, the lenient ``_is_entity_id``
        (``[a-z]{2,8}-<16..64 hex>``, IGNORECASE) was shared with `_check_refs`,
        so ``pr-0123456789abcdef`` became a `BundleValidationError` — a
        backwards-incompatible ingest change. Uppercase digests are likewise
        accepted (the canonical digest is lowercase, so there is nothing to
        shadow)."""
        bundle = {"points": [
            {"ref": "pr-0123456789abcdef",
             "kind": "statement", "content": "a"},
            {"ref": "sub-0123456789ABCDEF0123456789",
             "kind": "statement", "content": "b"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["points"] == 2

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
            "non-string content + direct connection": {
                "points": [
                    {"ref": "p1", "kind": "statement", "content": 5},
                    {"ref": "p2", "kind": "statement", "content": "b"},
                ],
                "connections": [
                    {"from": "p1", "to": "p2", "operator": "IMPL"}]},
        }
        for name, bundle in cases.items():  # noqa: B007
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
            "multi-item to on plain IMPL": _object_triple() | {"connections": [
                {"from": "oA", "to": ["oB", "oC"], "operator": "IMPL"}]},
        }
        for name, bundle in cases.items():  # noqa: B007
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
            # #5244: `mitigation` is an OPERATOR-route attribute (a mitigation
            # damps the operator bridge it attacks, #4937). A structural edge
            # has no operator to damp, so the key was accepted and dropped —
            # the same accept-and-drop defect the operator route carried.
            "relation carries mitigation": _points_pair() | {"connections": [
                {"from": "pA", "to": "pB", "relation": "hasPart",
                 "mitigation": {"reason": "r", "strength": 0.3}}]},
            "relation carries malformed mitigation": _points_pair() | {
                "connections": [
                    {"from": "pA", "to": "pB", "relation": "hasPart",
                     "mitigation": "dampen it"}]},
        }
        for name, bundle in cases.items():  # noqa: B007
            self._assert_both_phases(sdk, bundle)

    def test_relation_mitigation_refusal_names_the_sanctioned_route(self, sdk):
        """#5244 — refusing `mitigation` on a relation connection must tell the
        caller what to do instead (the operator route / sdk.mitigate_operator),
        and must mutate nothing (Phase-1, zero-mutation contract)."""
        from tortoise.exceptions import BundleValidationError
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "relation": "hasPart",
             "mitigation": {"reason": "r", "strength": 0.3}}]}
        with pytest.raises(BundleValidationError) as exc:
            sdk.ingest(bundle)
        msg = exc.value.violations[0]["message"]
        assert "mitigate_operator" in msg, msg
        assert "operator" in msg and "mitigation" in msg, msg
        assert _point_count(sdk) == 0, "zero-mutation on the rejection"
        assert _edge_count(sdk, "mitigated_by") == 0

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
            "label conflict on the direct-edge path (Object↔Object)":
                _object_pair() | {"connections": [
                    {"from": "oA", "to": "oB", "operator": "IMPL",
                     "label": "L1"},
                    {"from": "oA", "to": "oB", "operator": "IMPL",
                     "label": "L2"}]},
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
        for name, bundle in cases.items():  # noqa: B007
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
        for name, bundle in cases.items():  # noqa: B007
            self._assert_both_phases(sdk, bundle)

    def test_endpoint_classes_need_pre_seeded_graph(self, sdk):
        existing = sdk.create_point("statement", "pre-existing point",  # noqa: F841
                                    status="live")
        src = sdk.create_source("https://pre.example/s1", "report")
        src_url = src.get("url") or src.get("id")
        # #9173: the two terminal legs below are spelled with OBJECTS. A plain
        # IMPL whose endpoints include a plain `:Point` is promoted to the
        # operator route (#8499), which accepts terminal endpoints — so a point
        # spelling would assert a refusal the contract no longer makes, while
        # Object↔Object keeps the direct edge and the guard with it.
        terminal_obj = sdk.create_object("Terminal object")
        _query(sdk, "MATCH (n:Object {name:$n}) SET n.status='superseded'",
               {"n": "Terminal object"})
        live_obj = sdk.create_object("Live object")

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
                "connections": [{"from": live_obj["id"],
                                 "to": terminal_obj["id"],
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
            # ⛔ RETIRED (#3590 round-5 review F1): this case used to assert that
            # a bundle-local entity naming a TERMINAL-ONLY holder is refused in
            # both phases, because the write MERGEd onto that terminal node by
            # name. The create now resolves over LIVE carriers only, so such a
            # name MINTS a fresh live Object and the direct edge is legal — no
            # violation is left to have parity about. The positive shape is
            # pinned by `test_a_bundle_minting_past_a_terminal_holder_is_accepted`
            # below.
        }
        for name, bundle in cases.items():  # noqa: B007
            self._assert_both_phases(sdk, bundle)

    def test_a_bundle_minting_past_a_terminal_holder_is_accepted(self, sdk):
        """#3590 round-5 review (F1): a terminal-only holder does not block a mint.

        `resolve_or_mint` resolves over LIVE carriers, so a name whose only
        carrier is terminal is a DIFFERENT incarnation (R5) and the create mints
        a fresh live node. Phase 1 used to read its `#7925` axes off that
        terminal carrier, so it refused the bundle with "resolves to terminal"
        — a false block describing a mechanism (name-MERGE) the change removed.
        Parity here is the ABSENCE of a violation in both phases.
        """
        sdk.create_object("Terminal object")
        _query(sdk, "MATCH (n:Object {name:$n}) SET n.status='superseded'",
               {"n": "Terminal object"})
        bundle = {
            "entities": [
                {"ref": "oA", "type": "object", "name": "Live one"},
                {"ref": "oT", "type": "object", "name": "Terminal object"},
            ],
            "connections": [{"from": "oA", "to": "oT", "operator": "IMPL"}],
        }
        assert sdk._validate_bundle(bundle, promotion_policy="gated") == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1

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
            # #9173: c7 must be an Object↔Object pair. A multi-item `to` on a
            # POINT-BEARING pair is no longer a violation — the endpoint-kind
            # gate promotes that connection to the operator route, which fans
            # out — so a point pair here would assert a refusal the contract no
            # longer makes.
            "entities": [
                {"ref": "o1", "type": "object", "name": "Object one"},
                {"ref": "o2", "type": "object", "name": "Object two"},
                {"ref": "o3", "type": "object", "name": "Object three"},
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
                {"from": "o1", "to": ["o2", "o3"], "operator": "IMPL"},   # c7 multi-item
            ],
        }
        for granularity in ("bulk", "granular"):
            viols = sdk._validate_bundle(bundle)
            assert len(viols) >= 6, f"expected aggregated violations ({granularity})"
            messages = [v["message"] for v in viols]
            joined = "\n".join(messages)
            assert any("does not exist" in m for m in messages), joined       # c1
            assert any("SUPPORTS" in m for m in messages), joined             # c2
            assert any("must be a Point/Subject/Object/Event endpoint — got a"
                       in m for m in messages), joined  # c3/c4/c5
            assert any("multi-item 'to'" in m for m in messages), joined      # c7
            # zero mutations — only the pre-seeded Source exists
            assert _point_count(sdk) == 0
            assert _operator_count(sdk) == 0
            assert _edge_count(sdk, "IMPL") == 0
            assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0
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
        """§8 routing (A3) + the endpoint-kind gate (#8499): two IDENTICAL
        plain IMPL connections between :Points dedup cleanly — and the pair is
        REIFIED, not operator-less.

        ⛔ Owner ruling 2026-10-13 (#8499; reverses #1061's operator-less
        Point→Point arm): **a Point↔Point relation is operator-mediated.**
        An operator-less edge can be neither mitigated nor annotated, so it
        cannot carry the epistemic weighting a Point's relations claim — the
        reason they exist at all, and why the direct shape is reserved for
        ``Object↔Object``. (The old ``rebuild_all`` loss — A10 #1048 — is
        retired: #8499 landed the ``DirectEdgeCreated`` fold.)
        Dedup is unaffected: the second identical connection still dedups, now
        at the operator level (``_find_operator`` exact hit).
        """
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL"},
            {"from": "pA", "to": "pB", "operator": "IMPL"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1
        assert res["deduped"]["connections"] == 1
        # Reified: ONE operator node, whose IMPL edge runs to EACH input.
        assert _operator_count(sdk, "IMPL") == 1
        assert _edge_count(sdk, "IMPL") == 2
        # The reroute is REPORTED, never silent (the caller wrote the direct
        # spelling and got the operator form).
        assert any("point_mediated_route" in w for w in res["warnings"]), \
            res["warnings"]

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

    # ── #8623: the direct-edge conflict rule is EFFECTIVE-route scoped ──

    def test_label_differing_point_pair_legal(self, sdk):
        """#8623 — two same-pair plain IMPL connections differing only in
        ``label`` between :Points are LEGAL: the effective route is the
        OPERATOR route (owner ruling 2026-10-13, #8499), whose `_find_operator`
        dedup key INCLUDES ``label``, so both operators are distinct and both
        commit.

        The refused version applied the DIRECT-edge rule, which exists because
        a direct edge MERGEs on ``(src, tgt, type)`` — the second would
        silently overwrite the first's label. That premise does not hold on the
        route these connections actually take, so the refusal was stricter than
        the semantics it guards (the caller was told to migrate via
        ``reify:true`` ×2 for a case that no longer needs it).
        """
        bundle = _points_pair() | {"connections": [
            {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L1"},
            {"from": "pA", "to": "pB", "operator": "IMPL", "label": "L2"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 2, res
        assert res["deduped"]["connections"] == 0, res
        # Two operators, one per label — NOT one overwriting the other.
        labels = sorted(
            r[0] for r in _query(
                sdk, "MATCH (o:Point {is_operator:true}) RETURN o.label"))
        assert labels == ["L1", "L2"], labels
        # The endpoint-kind gate still REPORTS the reroute (never silent).
        assert any("point_mediated_route" in w for w in res["warnings"]), \
            res["warnings"]

    def test_label_differing_point_pair_legal_with_external_endpoint(self, sdk):
        """#8623 — the same case where ONE endpoint is a pre-existing external
        Point and the other is a bundle-local ``points`` ref: both endpoints
        resolve as Point-bearing (the bundle ref is minted as a plain :Point in
        step 2, the external id is probed), so the pair is operator-mediated
        and the differing labels are legal."""
        existing = sdk.create_point("statement", "pre-existing point",
                                    status="live")
        bundle = {
            "points": [{"ref": "pB", "kind": "statement", "content": "B"}],
            "connections": [
                {"from": existing["id"], "to": "pB", "operator": "IMPL",
                 "label": "L1"},
                {"from": existing["id"], "to": "pB", "operator": "IMPL",
                 "label": "L2"},
            ],
        }
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 2, res
        labels = sorted(
            r[0] for r in _query(
                sdk, "MATCH (o:Point {is_operator:true}) RETURN o.label"))
        assert labels == ["L1", "L2"], labels

    def test_label_differing_object_pair_still_conflict(self, sdk):
        """#8623 boundary — ``Object↔Object`` KEEPS the direct-edge rule: it is
        the one pair that stays operator-less (#8499), so the second edge would
        MERGE onto ``(src, tgt, type)`` and silently overwrite the first's
        label. Rejecting is the fail-closed answer to that ambiguity."""
        bundle = _object_pair() | {"connections": [
            {"from": "oA", "to": "oB", "operator": "IMPL", "label": "L1"},
            {"from": "oA", "to": "oB", "operator": "IMPL", "label": "L2"},
        ]}
        viols = sdk._validate_bundle(bundle)
        assert viols, "Phase 1 accepted a direct-edge label conflict"
        assert "conflicting" in viols[0]["message"], viols
        with pytest.raises(ValueError):
            sdk.ingest(bundle)
        # Zero mutation on the rejection.
        assert _edge_count(sdk, "IMPL") == 0

    def test_identical_object_pair_duplicates_dedup_clean(self, sdk):
        """#8623 boundary — the direct arm's CLEAN-dedup half is unchanged: two
        identical same-pair direct edges still dedup onto one edge."""
        bundle = _object_pair() | {"connections": [
            {"from": "oA", "to": "oB", "operator": "IMPL"},
            {"from": "oA", "to": "oB", "operator": "IMPL"},
        ]}
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1, res
        assert res["deduped"]["connections"] == 1, res
        assert _edge_count(sdk, "IMPL") == 1

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
        # A genuinely-direct pair (Object↔Object — no plain `:Point`
        # endpoint, so nothing is promoted, #8499). #9173: this used to be
        # spelled with a bundle-local POINT, which made it a promotion test by
        # accident — the Point-bearing pair is reified into an operator, which
        # accepts a terminal endpoint, so the guard never applied to it.
        terminal = sdk.create_object("Terminal object")
        _query(sdk, "MATCH (n:Object {name:$n}) SET n.status='superseded'",
               {"n": "Terminal object"})
        live = sdk.create_object("Live object")
        bundle = {"connections": [{"from": live["id"], "to": terminal["id"],
                                   "operator": "IMPL"}]}
        viols = sdk._validate_bundle(bundle)
        assert any("terminal" in v["message"] for v in viols), viols
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

    def test_bundle_local_point_hitting_terminal_dedup_refused_on_operator_route(
            self, sdk):
        # cycle-17/18: a bundle-local point item whose (content_hash, kind)
        # dedup-hits an EXISTING terminal point.
        #
        # ⛔ #9173 FLIPPED THIS TEST to ACCEPT, on the reasoning that a
        # `points` ref endpoint is Point-bearing BY CONSTRUCTION, so the
        # endpoint-kind gate (#8499) writes that connection as an operator and
        # `create_direct_edge` — the only primitive that refuses a terminal
        # endpoint — is never reached. ⛔ #9819 FLIPPED IT BACK, and the two
        # flips are about DIFFERENT axes: #9173 scoped the EDGE prediction
        # (operator-mediated wiring to a terminal point stays legal), but the
        # content dedup ALSO makes the write FOLD onto the terminal point —
        # `create_point`'s content resolution is unconditional, so the fold
        # reaches `_stamp_batch_id` and mutates the retracted/superseded node
        # (`batch_id` stamped, `updatedAt` moved, `status` still `retracted`)
        # on the operator route exactly as on the direct route. The point WRITE
        # is a separate axis #9173 never decided. What this test now pins is
        # that the bundle is refused in Phase 1 with ZERO mutation, so the
        # terminal point is not rewritten and no earlier item is committed.
        terminal = sdk.create_point("statement", "same content",
                                    status="superseded")["id"]
        before = sdk.get_point(terminal)
        bundle = {
            "points": [
                {"ref": "p1", "kind": "statement", "content": "other"},
                {"ref": "pT", "kind": "statement", "content": "same content"},
            ],
            "connections": [{"from": "p1", "to": "pT", "operator": "IMPL"}],
        }
        viols = sdk._validate_bundle(bundle)
        assert any("#9819" in v["message"] for v in viols), viols
        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest(bundle)
        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "the content fold stamped the retracted point (#9819)")
        assert after.get("updatedAt") == before.get("updatedAt")
        assert _operator_count(sdk) == 0, (
            "Phase-1 must reject BEFORE any write — no operator was wired")

    def test_a_non_string_kind_gets_the_structured_verdict_not_a_typeerror(
            self, sdk):
        """#9819 fix round: an UNHASHABLE ``kind`` raised a raw
        ``TypeError: unhashable type`` out of Phase 1, hiding the structured
        verdict.

        The content guard keys its identity maps by ``(identity, pointKind)``,
        and the item's kind came straight from ``item.get("kind")``: a
        ``kind: {"a": 1}`` made those lookups hash a dict. ``_check_kind``
        ALREADY refuses such a bundle ("pointKind must be a string"), so
        nothing was mis-written — but the caller got a ``TypeError`` instead of
        the violation, and a ``TypeError`` is not the Phase-1 contract (a
        caller cannot branch on it). ``_item_point_kind`` now always returns a
        ``str``, so every key hashes.

        RED BEFORE (measured): ``TypeError: unhashable type: 'dict'``.
        """
        for kind in ({"a": 1}, ["x"], 5):
            with pytest.raises(BundleValidationError, match="must be a string"):
                sdk.ingest({"points": [{"ref": "p1", "kind": kind,
                                         "content": "NONSTR-KIND",
                                         "key": "nonstr-k-9819"}]})
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'NONSTR-KIND' RETURN n.id"
        ).result_set == [], "the bundle wrote a point before refusing"

    def test_bundle_local_terminal_dedup_after_rebuild(self, sdk):
        # #2971: a Point can carry `content_hash = NULL` — a crash between the
        # node CREATE and the props SET (a partial write), or a graph written
        # before #2795 D2. (A rebuild no longer produces it: #2795 D2 has
        # `_upsert_point_props` RE-DERIVE the hash from the replayed content,
        # so a rebuilt graph keeps its hashes.) With the hash absent, the
        # content-hash MATCH the guard used missed that point and the
        # cycle-17/18 guard silently stopped matching it — the PREDICATE gap
        # this test pins, via the hash-less content+kind fallback. Simulate
        # that state directly (clear the terminal point's `content_hash`) and
        # assert the guard still MATCHES it (measured pre-#2971: the bundle was
        # then refused only by Phase-2 `_check_endpoint_race`, a mid-write raise
        # with point count 1 -> 2 and an orphan left behind).
        #
        # ⛔ #9173: the MATCH no longer implies an ingest-level REFUSAL, and this
        # test no longer asserts one — a bundle-local `points` ref is
        # Point-bearing, the endpoint-kind gate (#8499) reifies the connection,
        # and `create_direct_edge` (the primitive the old refusal predicted) is
        # never reached. See the assertion block below.
        #
        # Scope note: this simulates the TERMINAL-STATUS state (`status=
        # 'superseded'`, `outdated` unset) — the shape a direct status write
        # produces, and ONE of the two legs the guard matches. A point
        # superseded through `supersede_point` carries `outdated=true` as
        # well; that second leg was the pre-existing gap #3142 closed (the
        # guard now matches it, via the shared `_terminal_expression`), so
        # both legs are covered.
        terminal = sdk.create_point("statement", "same content",
                                    status="superseded")
        _query(sdk, "MATCH (n:Point {id:$id}) REMOVE n.content_hash",
               {"id": terminal["id"]})
        # The precondition is asserted FIRST so a hash-preserving regression
        # cannot pass this leg vacuously.
        assert _query(
            sdk, "MATCH (n:Point {id:$id}) RETURN n.content_hash",
            {"id": terminal["id"]})[0][0] is None
        # The GUARD still matches (the hash-less fallback is what this test is
        # about) — ⬛ #9173: what no longer follows is a REFUSAL of the
        # bundle. The endpoint is a bundle-local `points` ref, so the pair is
        # Point-bearing and the write reifies it into an operator; only
        # `create_direct_edge` refuses a terminal endpoint, and that primitive
        # is not reached. Asserting a refusal here asserted the over-refusal.
        assert sdk._find_terminal_dedup_hit("same content", "statement") \
            == terminal["id"]
        bundle = {
            "points": [
                {"ref": "p1", "kind": "statement", "content": "other"},
                {"ref": "pT", "kind": "statement", "content": "same content"},
            ],
            "connections": [{"from": "p1", "to": "pT", "operator": "IMPL"}],
        }
        # ⛔ #9819 FLIPPED THIS BACK: the guard MATCHING is now an ingest-level
        # REFUSAL, because the content fold MUTATES the terminal point it
        # resolves ("#9819" in the violation names that arm). The hash-less
        # fallback the test is about is still what makes the guard MATCH —
        # asserted above — and the refusal is the consequence.
        viols = sdk._validate_bundle(bundle)
        assert any("#9819" in v["message"] for v in viols), viols
        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest(bundle)
        assert _operator_count(sdk) == 0, "Phase-1 must reject before any write"

    def test_terminal_dedup_hit_mirrors_create_point_dedup_predicate(self, sdk):
        # #2971 predicate leg: the guard exists to catch a bundle-local ref
        # that will DEDUP onto a terminal point, so its watch set must equal
        # the WRITE PATH's dedup set. Since #2949 that set is defined ONCE, in
        # the shared `_dedup_match_clauses`: `(n.is_operator IS NULL OR
        # n.is_operator = false) AND (n.op_type IS NULL OR n.is_operator =
        # false)`. The assertion below is therefore GUARD == WRITER, checked on
        # both sides rather than hard-coded: whatever the writer resolves, the
        # guard must resolve too — otherwise the guard fails OPEN (the writer
        # dedups a terminal point, the guard does not see it) or FALSELY
        # rejects (the guard matches a node the writer would never dedup onto).
        # The `op_type` disjunct is deliberately NOT the counting form's bare
        # `... AND n.op_type IS NULL` (`summarize_structure` /
        # `list_pointkinds` / `ep.py::_graph_claim_count`): a bare
        # `op_type IS NULL` conjunct drops the reachable `is_operator=false +
        # op_type` hybrid that the writer DOES dedup, turning Phase 1 into a
        # Phase-2 mid-write raise (#2949 review).
        g = sdk._get_proj().g
        # reachable hybrid: modern is_operator=false PLUS a caller-supplied
        # op_type; the writer's shared predicate matches it
        sdk.create_point("statement", "hybrid content", op_type="IMPL",
                         status="superseded")
        # legacy property-absent plain Point (no is_operator, no op_type) —
        # the writer DOES dedup it (the #2949 F1 fix; before it the guard
        # failed OPEN on exactly this shape)
        g.query("CREATE (n:Point {id:'legacy-plain', "
                "content:'legacy content', pointKind:'statement', "
                "status:'superseded'})")
        # legacy operator (op_type set, is_operator ABSENT) — a :Point node,
        # but excluded by the predicate's `op_type` disjunct, so never a
        # plain-Point dedup target on EITHER side
        g.query("CREATE (o:Point {id:'legacy-op', op_type:'IMPL', "
                "content:'operator content', pointKind:'statement', "
                "status:'superseded'})")
        for content in ("hybrid content", "legacy content"):
            writer = sdk._find_point_by_content(content, pointKind="statement")
            guard = sdk._find_terminal_dedup_hit(content, "statement")
            assert writer is not None, f"the writer must dedup {content!r}"
            assert guard == writer, f"guard/writer disagree on {content!r}"
        # the excluded legacy operator shape is None on BOTH sides
        assert sdk._find_terminal_dedup_hit(
            "operator content", "statement") is None
        assert sdk._find_point_by_content(
            "operator content", pointKind="statement") is None

    def test_terminal_dedup_fallback_gated_on_writer_hash_state(self, sdk):
        # #2971 gating leg: `create_point` / `ingest_bundle` reach their A10
        # fallback ONLY when the UNFILTERED content_hash+kind lookup is empty —
        # a hash-present sibling means the writer resolves there and never
        # consults a hash-less point. The guard must mirror that ordering, or
        # it FALSELY rejects a bundle whose edge legally lands on a LIVE
        # hash-present duplicate (review of #2971 caught it). When every
        # duplicate is genuinely hash-less (a partial write, or a graph written
        # before #2795 D2) there is no hash for the writer to resolve to, so
        # the fallback still fires.
        term = sdk.create_point("statement", "same content",
                                status="superseded")
        _query(sdk, "MATCH (n:Point {id:$id}) REMOVE n.content_hash",
               {"id": term["id"]})
        # legal duplicate: same content+kind, LIVE, hash present (dedup off)
        live = sdk.create_point("statement", "same content", status="live",
                                dedup=False)
        assert _query(
            sdk, "MATCH (n:Point {id:$id}) RETURN n.content_hash",
            {"id": live["id"]})[0][0] is not None
        # the writer resolves to the live sibling, so the guard must NOT hit
        assert sdk._find_terminal_dedup_hit("same content", "statement") is None
        bundle = {
            "points": [
                {"ref": "p1", "kind": "statement", "content": "other"},
                {"ref": "pT", "kind": "statement", "content": "same content"},
            ],
            "connections": [{"from": "p1", "to": "pT", "operator": "IMPL"}],
        }
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1

    def test_terminal_dedup_fallback_is_conservative_on_hashless_duplicates(
            self, sdk):
        # #2971 ambiguity pin: with several HASH-LESS duplicates sharing the
        # content+kind, the write path's own pick is unspecified (its A10
        # fallback carries no ORDER BY), so the guard is deliberately
        # conservative — a terminal candidate rejects. Over-rejecting an
        # ambiguous duplicate pair is the safe direction (wiring to a terminal
        # point is the failure this guard exists to prevent). This is a
        # stronger form of the gating leg above: there the live sibling had a
        # hash (so the writer provably never reaches the fallback); here it
        # does not.
        term = sdk.create_point("statement", "same content",
                                status="superseded")
        _query(sdk, "MATCH (n:Point {id:$id}) REMOVE n.content_hash",
               {"id": term["id"]})
        live = sdk.create_point("statement", "same content", status="live",
                                dedup=False)
        _query(sdk, "MATCH (n:Point {id:$id}) REMOVE n.content_hash",
               {"id": live["id"]})
        # both are hash-less; the guard stays on the safe side
        assert sdk._find_terminal_dedup_hit(
            "same content", "statement") == term["id"]


# ── #9173: the direct-edge-scoped rules read the EFFECTIVE route ─────
#
# `_connection_route` reads the connection ITEM only — its 'direct' is a
# REQUEST. The write loop resolves the endpoints and then promotes a
# Point-bearing pair to the OPERATOR route (endpoint-kind gate, owner ruling
# 2026-10-13, #8499), where `create_operator` fans out over multiple targets
# and accepts a terminal endpoint. A Phase-1 rule that predicts what
# `create_direct_edge` will do must therefore ask the EFFECTIVE route: judging
# the request refused a shape the engine writes correctly, and the only escape
# it offered was `reify: true` on a connection that needed no such anchor.
# #8623 fixed the duplicate-conflict label arm this way; these were the
# residual arms.

class TestEffectiveRouteScope9173:
    def test_multi_item_to_on_point_bearing_pair_accepted_and_commits(self, sdk):
        # The measured shape A: plain multi-item `to` between bundle-local
        # Points. Before #9173 this returned one violation and the identical
        # connection with `reify: True` was accepted — the operator route IS
        # the route this connection takes.
        bundle = {
            "points": [{"ref": "a", "kind": "statement", "content": "A"},
                       {"ref": "b", "kind": "statement", "content": "B"},
                       {"ref": "c", "kind": "statement", "content": "C"}],
            "connections": [{"from": "a", "to": ["b", "c"],
                             "operator": "IMPL"}],
        }
        assert sdk._validate_bundle(bundle) == []
        res = sdk.ingest(bundle)
        assert res["created"]["connections"] == 1
        # promoted, never an operator-less edge (the fan-out shape)
        assert isinstance(res["ids"]["connections"][0], str)  # operator id
        assert _operator_count(sdk) == 1

    def test_multi_item_to_on_object_pair_still_rejected(self, sdk):
        # Coverage is NOT lost: Object↔Object keeps the direct shape (#8499),
        # so multi-item `to` is still a Phase-1 violation there.
        bundle = _object_triple() | {"connections": [
            {"from": "oA", "to": ["oB", "oC"], "operator": "IMPL"}]}
        viols = sdk._validate_bundle(bundle)
        assert any("multi-item 'to'" in v["message"] for v in viols), viols
        with pytest.raises(ValueError):
            sdk.ingest(bundle)
        # ⛔ review cycle 2: `ingest` raises the Phase-1 `BundleValidationError`
        # BEFORE Phase 2 runs, so an equality assertion here would pin nothing
        # about the Phase-2 arm (it cannot fail). Exercise the arm — and the
        # parameter that scopes it — directly instead.
        conn = bundle["connections"][0]
        direct: list[dict] = []
        sdk._check_connection(0, conn, direct, "direct")
        assert any("multi-item 'to'" in v["message"] for v in direct), direct
        via_operator: list[dict] = []
        sdk._check_connection(0, conn, via_operator, "operator")
        assert via_operator == [], via_operator

    def test_terminal_endpoint_on_point_bearing_pair_accepted_and_commits(self, sdk):
        # The measured shape B: a plain IMPL to an existing TERMINAL point.
        # `create_operator` does not evaluate endpoint status, so the guard's
        # premise (a mid-write raise from `create_direct_edge`) cannot hold on
        # the route this connection takes.
        terminal = sdk.create_point("statement", "terminal",
                                    status="superseded")
        live = sdk.create_point("statement", "live", status="live")
        bundle = {"connections": [{"from": live["id"], "to": terminal["id"],
                                   "operator": "IMPL"}]}
        assert sdk._validate_bundle(bundle) == []
        assert sdk.ingest(bundle)["created"]["connections"] == 1

    def test_terminal_endpoint_on_object_pair_still_rejected(self, sdk):
        # The direct-edge guard keeps its coverage on the pair that still takes
        # the direct edge. (The twin of the test above, on the OTHER side of
        # the endpoint-kind gate.)
        terminal = sdk.create_object("Terminal object")
        _query(sdk, "MATCH (n:Object {name:$n}) SET n.status='superseded'",
               {"n": "Terminal object"})
        live = sdk.create_object("Live object")
        bundle = {"connections": [{"from": live["id"], "to": terminal["id"],
                                   "operator": "IMPL"}]}
        viols = sdk._validate_bundle(bundle)
        assert any("terminal" in v["message"] for v in viols), viols
        with pytest.raises(ValueError):
            sdk.ingest(bundle)

    def test_unevaluable_status_endpoint_on_point_bearing_pair_accepted(self, sdk):
        # The `_status_is_evaluable` arm is the same class of prediction: an
        # ARRAY status raises `TypeError` inside `create_direct_edge`'s
        # frozenset test — a primitive the promoted pair never calls.
        terminal = sdk.create_point("statement", "arr", status="superseded")
        _query(sdk, "MATCH (n:Point {id:$i}) SET n.status=['superseded']",
               {"i": terminal["id"]})
        live = sdk.create_point("statement", "live2", status="live")
        bundle = {"connections": [{"from": live["id"], "to": terminal["id"],
                                   "operator": "IMPL"}]}
        assert sdk._validate_bundle(bundle) == []
        assert sdk.ingest(bundle)["created"]["connections"] == 1

    def test_effective_route_carve_out_is_still_accepted(self, sdk):
        # The cycle-4 carve-out is unchanged: an explicitly operator-mediated
        # connection to a terminal point keeps its behaviour, and the promoted
        # connection now reaches the SAME verdict through the same route.
        terminal = sdk.create_point("statement", "old claim",
                                    status="superseded")
        bundle = {
            "points": [{"ref": "p1", "kind": "statement", "content": "new"}],
            "connections": [{"from": "p1", "to": terminal["id"],
                             "operator": "IMPL",
                             "mitigation": {"reason": "r", "strength": 0.2}}],
        }
        assert sdk._validate_bundle(bundle) == []
        assert sdk.ingest(bundle)["created"]["connections"] == 1


# ── Server-managed keys are read at the write DOOR's fold depth (#7925 r5) ──
#
# `_coerce_props` folds one nested `props` map per application and a door may
# apply it twice (points: `create_point` + the dedup relay into `update_point`;
# subject/object/event: `create_*` + `create_entity`), while `document` folds
# once. A reader pinned to the TOP LEVEL let a single-wrapped spelling reach
# `_sanitize_props`, which raised in step 2/3 with the earlier items already
# committed (measured). Reading at the door depth closes that without
# over-reading a `document` (one fold).

@pytest.mark.parametrize("key,value", [("is_episodic", True),
                                       ("sourceVersion", "v1")])
def test_server_managed_key_is_refused_when_single_wrapped(sdk, key, value):
    bundle = {"points": [{"kind": "statement", "content": "p-one"},
                         {"kind": "statement", "content": "p-two",
                          "props": {key: value}}]}
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any(key in m and "server-managed" in m for m in msgs), msgs
    from tortoise.exceptions import BundleValidationError
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _point_count(sdk) == 0


def test_server_managed_key_is_refused_on_an_entity_item_too(sdk):
    bundle = {"points": [{"kind": "statement", "content": "e-one"}],
              "entities": [{"type": "subject", "name": "S1",
                            "props": {"is_episodic": True}}]}
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("is_episodic" in m for m in msgs), msgs
    assert _point_count(sdk) == 0


def test_server_managed_key_beyond_the_document_door_depth_is_not_over_read(sdk):
    """A `document` item folds ONCE, so a level-2 spelling is invisible to the
    writer — refusing it would over-reject a bundle the writer performs."""
    deep = {"sources": [{"url": "https://x.example/s", "sourceKind": "report",
                         "props": {"props": {"sourceVersion": "v1"}}}]}
    assert sdk._validate_bundle(deep) == []
    shallow = {"sources": [{"url": "https://x.example/s", "sourceKind": "report",
                            "props": {"sourceVersion": "v1"}}]}
    assert any("sourceVersion" in v["message"]
               for v in sdk._validate_bundle(shallow))


@pytest.mark.parametrize("key,value", [
    ("is_episodic", True),
    ("sourceVersion", "v1"),
    ("__runId", "R"),
    ("_merge_run_id", "R"),
])
def test_server_managed_key_is_refused_when_double_wrapped_on_a_point(
        sdk, key, value):
    """Every server-managed key this gate reads is read at the WRITE DOOR's fold
    depth, so a `props: {props: {...}}` spelling is refused too.

    The contract doc claimed these four were "still one level deep" — a residual
    that did not exist (the writer runs `_coerce_props` twice on this door), and
    the direction that matters: a reader told it is closed has no reason to
    re-open it.
    """
    bundle = {"points": [{"kind": "statement", "content": "p-one"},
                         {"kind": "statement", "content": "p-two",
                          "props": {"props": {key: value}}}]}
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any(key in m and "server-managed" in m for m in msgs), msgs
    with pytest.raises(ValueError):
        sdk.ingest(bundle)
    assert _point_count(sdk) == 0


def test_entity_type_vocabulary_is_derived_from_one_map(sdk):
    """Phase 1, Phase 2 and the writer dispatch on the SAME vocabulary.

    `_ENTITY_ITEM_LABELS` is the declaration, the messages are built from it, and
    every key it declares must have a step-3 writer branch — a key without one
    would be accepted by Phase 1 and raise a raw ValueError in step 3, after
    step 2's items committed. That second half is asserted directly (each key is
    handed to `create_entity`), because a message-only check passes on a map
    entry the writer cannot handle (measured).
    """
    from tortoise.sdk import _ENTITY_ITEM_LABELS

    keys = list(_ENTITY_ITEM_LABELS)
    bundle = {"entities": [{"type": "bogus", "name": "X"}]}
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("|".join(keys) in m for m in msgs), msgs
    with pytest.raises(ValueError, match="|".join(keys)):
        sdk.ingest(bundle)
    with pytest.raises(ValueError) as exc:
        sdk.create_entity("bogus", "X")
    for key in keys:
        assert key in str(exc.value), str(exc.value)
        # every declared key must reach a writer branch: the "unknown type"
        # messages are the only place a missing branch surfaces, so assert the
        # call does not produce one
        try:
            sdk.create_entity(key, f"probe-{key}")
        except ValueError as err:  # a writer refusal is fine; an unknown type is not
            assert "unknown type" not in str(err), (key, str(err))
        # …and ingest's STEP-3 dispatch must handle it too: a generic-writer
        # branch does not prove the bundle path knows the type, and a type that
        # only the generic writer knows raises in step 3 with step 2 committed.
        item = {"ref": f"v-{key}", "type": key, "name": f"probe-{key}"}
        if key == "event":
            item["eventKind"] = "meeting"
        if key == "document":
            item["documentKind"] = "report"
        try:
            sdk.ingest({"entities": [item]})
        except ValueError as err:
            assert "type must be" not in str(err), (key, str(err))
