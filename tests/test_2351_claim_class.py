"""#2351 — the asserted-vs-derived CLAIM CLASS: marker, override guard, read side.

Epic #2349 workstream E-derived. Before this, a claim the system COMPUTED out of
other beliefs was indistinguishable from one someone AUTHORED — same Point, same
EP belief, no marker — so an inference could out-rank, supersede, or
belief-cascade over authored ground truth. This suite pins the three halves of
the fix, and each half has a test that REDS without it:

  1. THE MARKER. ``create_point(claim_class='derived')`` persists
     ``claimClass='derived'``; the DEFAULT and an explicit ``'asserted'`` store
     NOTHING, because absence IS asserted — that is what makes the marker
     migration-free for every claim already in every graph (indicator 4). A
     dedup hit folds onto the existing claim WITHOUT re-stamping provenance,
     and an unknown class fails LOUD before any write.
  2. THE GUARD (indicator 2), at the write path AND in the EP factor load:
       * ``create_direct_edge`` / ``create_operator`` refuse a DERIVED source
         wired above an ASSERTED target — for IMPL *and* NAND (a NAND attack
         lowers the target's belief: the same override, other direction).
       * ``supersede_point`` refuses a DERIVED successor replacing an ASSERTED
         predecessor, BEFORE any mutation — and (the 2a OPERATOR re-point,
         #10195) refuses a supersede whose REBUILT operator factor would be
         derived-over-asserted, judged with the same predicate.
       * ``TortoiseEP._affected_factors`` — the BACKSTOP for edges that predate
         the guard or were bulk-written — blocks only the leg that pushes the
         computed claim's belief INTO the authored one, KEEPS the reverse leg
         (a derived claim must stay corroborable, or the class is a degradation
         rather than a provenance marker), and DROPS + REPORTS a factor whose
         every leg it blocked (#6105 — never silently discarded while the run
         reports ``converged``).
  3. THE READ SIDE (indicator 3). ``tortoise_fts_query(claim_class=…)`` filters
     at the pre-truncation seam and reports each hit's class; ``None`` emits no
     key, so every pre-#2351 caller is byte-identical.

Deliberately NOT tested here: WHICH producers stamp ``derived`` (dream,
projection-derived, extraction cross-links). That is a POLICY question the
issue assigns to the epic's policy layer — this ships the vocabulary, the guard
and the read contract, and a producer adopts them by passing the param.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_2351_claim_class.py -q
"""
from __future__ import annotations

import inspect

import pytest

from tortoise import claim_class as cc
from tortoise.ep import TortoiseEP
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def lane(tmp_path):
    """A fresh SDK on its own graph, closed on teardown."""
    sdk = TortoiseSDK(str(tmp_path / "t.db"))
    yield sdk
    sdk.close()


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id RETURN properties(n)",
        params={"id": pid}).result_set
    assert rows, f"point {pid!r} not found"
    return rows[0][0]


def _factor(sdk: TortoiseSDK, op_id: str, claim_id: str):
    """The resolved factor for ``op_id`` over ``claim_id``, or None."""
    factors = TortoiseEP(sdk._get_proj())._affected_factors({claim_id})
    for factor in factors:
        if factor[0] == op_id:
            return factor
    return None


# ── 1. the vocabulary ────────────────────────────────────────────────────────

def test_vocabulary_is_closed_and_absence_is_asserted():
    """Two classes, and ``None``/junk read as asserted — never raise on a read."""
    assert sorted(cc.CLAIM_CLASS_VALUES) == ["asserted", "derived"]
    assert cc.validate(None) == cc.ASSERTED        # absent = the default class
    assert cc.validate("asserted") == cc.ASSERTED
    assert cc.validate("derived") == cc.DERIVED
    # Read side is TOTAL: a hand-edited / foreign graph must not fail a search.
    for junk in (None, "", "weird", 7, "DERIVED "):
        assert cc.normalize(junk) == cc.ASSERTED
    # Write side fails LOUD — a typo must never become a third state.
    for bad in ("bogus", "", 3):
        with pytest.raises(ValueError):
            cc.validate(bad)


def test_override_rule_is_directional():
    """DERIVED over ASSERTED blocks; the two same-class wirings never do."""
    assert cc.blocks_override({"derived"}, {"asserted"}) is True
    assert cc.blocks_override({"asserted"}, {"derived"}) is False
    assert cc.blocks_override({"derived"}, {"derived"}) is False
    assert cc.blocks_override({"asserted"}, {"asserted"}) is False
    # An id carrying NO marker at all is asserted — which is what makes the
    # guard bite on the pre-marker graph without a migration.
    assert cc.blocks_override({"derived"}, None) is True
    assert cc.any_asserted(None) is True


def test_factor_direction_blocks_one_leg_and_keeps_the_other():
    """The guard blocks the belief-into-authored leg, never the corroboration."""
    # derived source, asserted target, mutual: fwd blocked, bwd survives.
    assert cc.factor_direction("<->", {"derived"}, {"asserted"}) == "<-"
    # ... and with only the fwd leg, every leg is blocked ⇒ DROP.
    assert cc.factor_direction("->", {"derived"}, {"asserted"}) is None
    # The reverse wiring blocks the OTHER leg, not the same one.
    assert cc.factor_direction("<->", {"asserted"}, {"derived"}) == "->"
    # Same class: untouched, and an absent token is not confused with DROP.
    assert cc.factor_direction("<->", {"derived"}, {"derived"}) == "<->"
    assert cc.factor_direction(None, {"derived"}, {"asserted"}) == "<-"


# ── 2. the marker on write ───────────────────────────────────────────────────

def test_derived_is_stored_and_asserted_is_absent(lane):
    """Only ``derived`` is persisted — so no migration and no re-stamp."""
    derived = lane.create_point("statement", "computed from other beliefs",
                                claim_class="derived")
    authored = lane.create_point("statement", "told to us", claim_class="asserted")
    default = lane.create_point("statement", "told to us, no argument")

    assert _props(lane, derived["id"]).get(cc.PROP) == "derived"
    # Both spellings of the default class store NOTHING: absence IS asserted.
    assert cc.PROP not in _props(lane, authored["id"])
    assert cc.PROP not in _props(lane, default["id"])


def test_nested_prop_form_is_validated_and_not_persisted_as_asserted(lane):
    """The prop passthrough must not smuggle an unvalidated third class."""
    pid = lane.create_point("statement", "x", claimClass="derived")["id"]
    assert _props(lane, pid).get(cc.PROP) == "derived"
    with pytest.raises(ValueError):
        lane.create_point("statement", "y", claimClass="derivedish")


def test_unknown_class_refused_with_nothing_written(lane):
    """A refused write creates no Point at all (fail-closed, #2199 posture)."""
    before = lane._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    with pytest.raises(ValueError):
        lane.create_point("statement", "z", claim_class="bogus")
    after = lane._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    assert after == before


def test_dedup_hit_does_not_restamp_provenance(lane):
    """The class belongs to the WRITE THAT MINTS — a re-run must not flip it.

    The DANGEROUS form is a dedup re-run that NAMES a class: if the class rode
    the dedup forward, a re-submission could silently re-label an authored
    claim as derived (or bury a derived one). Both directions are pinned here,
    so the assertion can fail.
    """
    first = lane.create_point("statement", "same words", claim_class="derived")
    again = lane.create_point("statement", "same words", dedup=True)
    assert again["id"] == first["id"]
    assert _props(lane, first["id"]).get(cc.PROP) == "derived"

    # A dedup re-run that NAMES the ASSERTED class must not bury the marker.
    lane.create_point("statement", "same words", dedup=True,
                      claim_class="asserted")
    assert _props(lane, first["id"]).get(cc.PROP) == "derived", (
        "a dedup hit must not re-stamp a derived claim as authored")

    # ...and the mirror: naming DERIVED must not promote an authored claim.
    plain = lane.create_point("statement", "authored words")
    lane.create_point("statement", "authored words", dedup=True,
                      claim_class="derived")
    assert cc.PROP not in _props(lane, plain["id"]), (
        "a dedup hit must not promote an authored claim to derived")


# ── 3. the write-path guard ──────────────────────────────────────────────────

def _two(lane, *, src_class, tgt_class):
    src = lane.create_point("statement", "src", claim_class=src_class)["id"]
    tgt = lane.create_point("statement", "tgt", claim_class=tgt_class)["id"]
    return src, tgt


def test_direct_edge_refuses_derived_above_asserted(lane):
    src, tgt = _two(lane, src_class="derived", tgt_class="asserted")
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_direct_edge("IMPL", src, tgt)
    # NAND is the same override in the other direction: it LOWERS the target.
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_direct_edge("NAND", src, tgt)


def test_nothing_is_wired_when_the_direct_edge_is_refused(lane):
    """The refusal is BEFORE the MERGE — a refused call writes nothing."""
    src, tgt = _two(lane, src_class="derived", tgt_class="asserted")
    with pytest.raises(ValueError):
        lane.create_direct_edge("IMPL", src, tgt)
    n = lane._get_proj().g.query(
        "MATCH ()-[r:IMPL|NAND]->() RETURN count(r)").result_set[0][0]
    assert n == 0


def test_direct_edge_allows_the_legal_wirings(lane):
    """Same-class wirings flow both ways; a MIXED one must state its legal leg.

    A class-mixed bidirectional edge flows BOTH legs, so one of them is always
    an override — the DOOR refuses it and asks the caller to name the leg it
    means (`->` = the authored claim corroborates the computed one). The EP
    backstop is the LENIENT side: it NARROWS an edge that already exists.
    """
    for src_class, tgt_class in (("derived", "derived"),
                                 ("asserted", "asserted")):
        src, tgt = _two(lane, src_class=src_class, tgt_class=tgt_class)
        lane.create_direct_edge("IMPL", src, tgt)   # must not raise
        assert _factor(lane, src, tgt) is not None
    # Corroboration is stated one-way: the asserted claim feeds the derived.
    a_src, d_tgt = _two(lane, src_class="asserted", tgt_class="derived")
    lane.create_direct_edge("IMPL", a_src, d_tgt, direction="->")
    assert _factor(lane, a_src, d_tgt) is not None
    # The same wiring asked for BIDIRECTIONALLY flows the override leg too.
    a2, d2 = _two(lane, src_class="asserted", tgt_class="derived")
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_direct_edge("IMPL", a2, d2)


def test_create_operator_refuses_derived_source_over_asserted(lane):
    src, tgt = _two(lane, src_class="derived", tgt_class="asserted")
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("IMPL", src, [tgt])
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("NAND", src, [tgt])
    # An asserted source over a derived input is fine — stated one-way.
    a_src, d_tgt = _two(lane, src_class="asserted", tgt_class="derived")
    lane.create_operator("IMPL", a_src, [d_tgt], direction="->")
    # ...but the same operator requested BIDIRECTIONALLY flows the override leg.
    a2, d2 = _two(lane, src_class="asserted", tgt_class="derived")
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("IMPL", a2, [d2])


def test_supersede_refuses_a_derived_successor(lane):
    """Supersession IS an override — a computed claim never terminalises one."""
    old = lane.create_point("statement", "authored belief")["id"]
    new = lane.create_point("statement", "computed replacement",
                            claim_class="derived")["id"]
    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)
    # Refused BEFORE any mutation: the predecessor is still live.
    assert _props(lane, old).get("status") == "live"


def test_supersede_allows_a_derived_predecessor(lane):
    """Replacing a computed claim with a corrected one is legal."""
    old = lane.create_point("statement", "v1", claim_class="derived")["id"]
    new = lane.create_point("statement", "v2", claim_class="derived")["id"]
    lane.supersede_point(old, new)
    assert _props(lane, old).get("status") == "superseded"


# ── 3b. the 2a OPERATOR re-point door (#10195) ───────────────────────────────
#
# ``supersede_point``'s 2a arm re-points the predecessor's OPERATOR edges
# ``(op)-[IMPL|NAND {idx}]->(old)`` onto the successor with a raw edge MERGE +
# delete. Operator SLOT POSITION is what ``create_operator``'s guard reads
# (``inputs[0]`` is the factor's SOURCE slot, the rest are targets), so
# replacing an input can move a claim across the class boundary and mint the
# ``derived --IMPL--> asserted`` factor that door refuses. The pre-flight
# rebuilds each affected operator's slots with old substituted by new and
# judges them with the SAME ``direction_blocks_create`` predicate.

def _operator_rows(lane, claim_id):
    """``(op_id, rel, idx)`` for every operator edge reaching ``claim_id``."""
    return lane._get_proj().g.query(
        "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->"
        "(n:Point {id:$id}) RETURN op.id, type(r), r.idx",
        params={"id": claim_id}).result_set


def test_supersede_repoint_refuses_a_derived_source_over_the_successor(lane):
    """The target-slot route: the successor's CLASS flips the factor.

    Legal at creation time (both endpoints computed), then the successor is
    AUTHORED — so re-pointing makes ``src(derived) --IMPL--> new(asserted)``,
    the exact wiring ``create_operator`` refuses. Pre-fix the supersede
    reported success and the graph held the refused factor.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    new = lane.create_point("statement", "authored v2")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    # Refused BEFORE any mutation: neither point terminalised, the edge is
    # still at the predecessor, and nothing was created at the successor.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", 1]]
    assert _operator_rows(lane, new) == []
    # The door the re-point was imitating keeps refusing the same wiring.
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("IMPL", src, [new], direction="->")


def test_supersede_repoint_refuses_a_derived_claim_in_the_source_slot(lane):
    """The source-slot route: the predecessor itself is at ``idx 0``.

    The operator was legal while the predecessor was asserted; a producer then
    re-stamped it DERIVED (no door accepts that — ``update_point`` refuses the
    class outright, so this is the raw producer shape). Re-pointing moves the
    computed claim into the factor's SOURCE slot above asserted targets.
    """
    old = lane.create_point("statement", "v1")["id"]
    t1 = lane.create_point("statement", "authored target 1")["id"]
    t2 = lane.create_point("statement", "authored target 2")["id"]
    op = lane.create_operator("IMPL", old, [t1, t2], direction="->")
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.claimClass='derived'",
        params={"id": old})
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    assert _props(lane, old).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", 0]]
    assert _operator_rows(lane, new) == []


def test_supersede_repoint_judges_the_leg_that_flows(lane):
    """A one-way corroboration survives the re-point; same-class does not care.

    ``asserted --NAND(->)--> derived`` flows source→target ONLY, which is the
    CORROBORATION leg, so it is legal and a derived successor may inherit it.
    Judging the source slot's class alone (ignoring the direction) would refuse
    this — the inversion the predicate exists to prevent.
    """
    src = lane.create_point("statement", "authored source")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("NAND", src, [old], direction="->")
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    assert lane.supersede_point(old, new)["edges_transferred"] == 1
    assert _operator_rows(lane, new) == [[op["id"], "NAND", 1]]
    assert _operator_rows(lane, old) == []


def test_supersede_repoint_leaves_an_unprovable_source_slot_alone(lane):
    """No ``idx`` on the slot edges ⇒ neither side can name the SOURCE slot.

    An idx-less (legacy) operator coalesces every input to position 0 and the
    tie breaks lexicographically, so ``input_ids[0]`` is not provably the
    source — the ONE condition for which the load side itself distrusts
    position 0 (``ep.py`` clears ``op_idx_known[op_id]`` under ``if idx is
    None``, and that flag gates its #2351 backstop). The door stands down WITH
    the load side rather than judging a slot it cannot name, because a door
    refusing a shape the load side keeps is its own divergence.

    ⛔ This parity is NULL-``idx`` only. The TYPE leg pinned by the test below
    is NOT a stand-down: the load side keeps judging a numeric-string ``idx``
    (``op_idx_known`` is keyed on NULL alone), so the door judges it too —
    standing down there was the fail-open #10195 review P2 found.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL|NAND]->() REMOVE r.idx",
        params={"op": op["id"]})
    new = lane.create_point("statement", "authored v2")["id"]

    assert lane.supersede_point(old, new)["edges_transferred"] == 1
    assert len(_operator_rows(lane, new)) == 1
    assert _operator_rows(lane, old) == []


@pytest.mark.parametrize("int_slot, str_idx", [(1, "1"), (0, "0")])
def test_supersede_repoint_coerces_a_numeric_string_slot_idx(
        lane, int_slot, str_idx):
    """A NUMERIC-STRING slot ``idx`` is a position, not a crash and not a skip.

    The graph is schemaless, so any hand/foreign writer can leave ``r.idx='1'``
    beside an int ``idx`` on the same operator. Two failure modes sit on that
    shape and this test pins that neither is taken:

    * sorting raw keys raises ``TypeError`` and aborts a supersede the class
      guard is not even about (the pre-fix crash);
    * SKIPPING the factor would be worse — EP's load side keeps it
      (``op_idx_known`` is cleared for a NULL ``idx`` only, and ``ep.py`` orders
      ``coalesce(r.idx, 0)``), so a skip is a fail-open exemption for the door.

    So the string is COERCED and the layout judged. Here both endpoints are
    computed, so the guard has nothing to say and the supersede must go through
    and carry the predecessor's slot — idx VALUE and type preserved, as 2a
    reads ``idx`` opaquely.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # Re-stamp ONE slot's idx as a string — no door writes this shape, which is
    # exactly why it is the raw one a foreign writer leaves behind.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->() WHERE r.idx = $from "
        "SET r.idx = $to",
        params={"op": op["id"], "from": int_slot, "to": str_idx})

    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    # Same class on both sides: the guard has nothing to say, so the supersede
    # must go through and carry the predecessor's slot — idx VALUE and type
    # preserved, as 2a reads ``idx`` opaquely.
    moved_idx = str_idx if int_slot == 1 else 1
    assert lane.supersede_point(old, new)["edges_transferred"] == 1
    assert _operator_rows(lane, new) == [[op["id"], "IMPL", moved_idx]]
    assert _operator_rows(lane, old) == []


@pytest.mark.parametrize("raw_idx", ["1", "slot-one"])
def test_supersede_repoint_refuses_a_raw_written_slot_idx(lane, raw_idx):
    """#10195 crossed with a raw-written ``idx`` — the door must still refuse.

    This is the fail-open the pre-fix stand-down opened (review P2): the door
    skipped any operator whose slot ``idx`` was not an ``int``, justified by
    "EP's backstop stands down on exactly that evidence" — which is FALSE.
    ``ep.py`` clears ``op_idx_known`` under ``if idx is None`` ONLY, so it still
    builds and JUDGES a factor whose slot carries ``r.idx='1'``. The door was
    therefore MORE permissive than the loader, and one string property defeated
    the whole guard: the supersede minted ``derived --IMPL--> asserted`` with no
    verdict, no warning and no record.

    Both unprovable-looking shapes are pinned: a numeric string naming a position
    (coerced and judged exactly) and a value that cannot be ordered at all (which
    is judged over EVERY candidate source and refused if any blocks —
    fail-closed, never a silent exemption).

    ⚠️ No `float` parametrization: MEASURED, FalkorDB returns `1` (an `int`) for
    a property written as `1.0`, so a float-idx case would take the int path and
    "pass" on the pre-fix code too — a non-discriminating case is not coverage.
    The door still coerces `float` defensively (a driver that returned one would
    otherwise be pushed onto the unorderable arm).
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->() WHERE r.idx = 1 "
        "SET r.idx = $to",
        params={"op": op["id"], "to": raw_idx})
    # The successor is AUTHORED — issue #10195's reproduction, plus the layout.
    new = lane.create_point("statement", "authored v2")["id"]

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    # Refused BEFORE any mutation, and the refused wiring is not in the graph.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", raw_idx]]
    assert _operator_rows(lane, new) == []


def test_supersede_repoint_leaves_an_alreadyDecided_operator_alone(lane):
    """The #1080 carve-out the pre-flight depends on, pinned.

    An operator labelled ``alreadyDecided`` is NOT re-pointed by 2a (a dedup
    context edge declares the PRIOR a duplicate, so it stays on the dead prior),
    which is why the pre-flight filters it out of the judged set. Drop that
    filter and this supersede — whose operator the body deliberately leaves
    behind — is refused for a wiring that is never written.

    The operator here is exactly the shape the guard refuses when it DOES move
    (``derived --IMPL-->`` a successor that is asserted), so only the carve-out
    stands between this call and a false refusal.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->",
                              label="alreadyDecided")
    new = lane.create_point("statement", "authored v2")["id"]

    # No raise: the operator is not re-pointed, so no rebuilt layout exists.
    assert lane.supersede_point(old, new)["edges_transferred"] == 0
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", 1]]
    assert _operator_rows(lane, new) == []


# ── 4. the EP-load backstop ──────────────────────────────────────────────────

def _raw_direct_edge(lane, src, tgt, *, direction):
    """Write an IMPL edge BYPASSING the write guard — the pre-guard shape."""
    lane._get_proj().g.query(
        "MATCH (a:Point), (b:Point) WHERE a.id=$s AND b.id=$t "
        f"MERGE (a)-[r:IMPL {{direction: '{direction}'}}]->(b)",
        params={"s": src, "t": tgt})


def test_ep_backstop_keeps_the_corroboration_leg(lane):
    """A pre-guard derived→asserted edge still cannot push belief upward.

    The factor survives with its direction rewritten: the authored claim may
    corroborate the computed one, but the computed one may not raise the
    authored belief. Dropping the WHOLE factor here would make every derived
    claim EP-inert beside any authored one — a degradation, not a marker.
    """
    src, tgt = _two(lane, src_class="derived", tgt_class="asserted")
    _raw_direct_edge(lane, src, tgt, direction="<->")
    factor = _factor(lane, src, tgt)
    assert factor is not None, "the corroboration leg must survive"
    assert factor[5] == "<-", (
        "the derived→asserted leg must be blocked; the factor direction should "
        f"be <- (asserted corroborates derived), got {factor[5]!r}"
    )


def test_ep_backstop_drops_and_reports_a_fully_blocked_factor(lane):
    """Every leg blocked ⇒ DROP, and the run says so (#6105, never silent)."""
    src, tgt = _two(lane, src_class="derived", tgt_class="asserted")
    _raw_direct_edge(lane, src, tgt, direction="->")
    ep = TortoiseEP(lane._get_proj())
    factors = ep._affected_factors({src, tgt}, skipped=ep._last_skipped_factors)
    assert all(f[0] != src for f in factors), (
        f"a fully-blocked factor must not propagate: {factors!r}")
    reasons = [sk["reason"] for sk in ep._last_skipped_factors]
    assert "derived_over_asserted" in reasons, (
        f"the drop must be REPORTED, got {reasons!r}")
    # #6105: ``participating_inputs`` is the count the record's own ``inputs``
    # array names — a direct-edge factor has exactly two endpoints, so a
    # hardcoded 0 both contradicts the record and misreports why the arm was
    # skipped to every consumer that filters or sums on this field.
    rec = next(sk for sk in ep._last_skipped_factors
               if sk["reason"] == "derived_over_asserted")
    assert rec["participating_inputs"] == 2, rec
    assert len(rec["inputs"]) == 2, rec


def test_ep_backstop_classifies_the_slot_ep_actually_uses(lane):
    """The guard reads the source class from the LIVE list — the slot EP uses.

    A stripped input is NOT in the factor: ``_affected_factors`` hands the
    live-filtered ids to ``_update_factor``, whose source slot is
    ``input_ids[0]`` (fwd writes into ``input_ids[1]``). Classifying idx 0 of
    the FULL list therefore describes a claim the factor does not contain —
    and on a bidirectional factor the #780/#2422 source-stripped guard does not
    fire, so it would block the corroboration leg and let the derived->asserted
    one flow, i.e. let the computed claim push belief INTO the authored one.
    """
    authored = lane.create_point("statement", "asserted source")["id"]
    derived = lane.create_point("statement", "derived input")["id"]
    other = lane.create_point("statement", "second asserted input")["id"]
    op = lane.create_operator("IMPL", authored, [derived, other])
    # The class is stamped AFTER creation — a raw write, the way a bulk or
    # projection producer (and any pre-guard graph) can — because the door
    # refuses a class-mixed bidirectional wiring.
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.claimClass='derived'",
        params={"id": derived})
    # The source goes draft AFTER creation — bypassing the write guard the way
    # a status change legitimately can.
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.status='draft'",
        params={"id": authored})
    factor = _factor(lane, op["id"], derived)
    assert factor is not None, "the factor must not be dropped"
    # The stripped asserted source is gone, so the surviving factor is
    # [derived, other] and EP's SOURCE slot is the DERIVED claim. Only the
    # reverse leg may flow: `<-` (the asserted input corroborates the derived
    # one) — `->` is exactly the derived->asserted override the class forbids.
    assert factor[5] == "<-", (
        "the guard must classify the slot EP actually uses (the first LIVE "
        "input = the derived claim), so only the reverse leg may be blocked, "
        f"got {factor[5]!r}")


def test_read_of_a_non_point_type_refuses_the_class_filter(lane):
    """``claimClass`` is a Point property — a non-Point read must refuse it.

    Silently ignoring the filter would return the rows the caller asked to
    exclude (every document is unmarked = asserted), which reads as an answer.
    """
    lane.create_point("statement", "zebra point", claim_class="derived")
    with pytest.raises(ValueError, match="Point property"):
        lane.tortoise_fts_query("zebra", entity_type="document",
                                claim_class="asserted")


def test_ingest_partial_absorb_cannot_wire_a_derived_source(lane):
    """The write-path invariant holds on the INGEST door too (#2351 review).

    ``ingest``'s partial-absorb branch completes an existing operator's input
    set with a raw edge MERGE — the same edge shape ``create_operator``
    refuses to write. Without the guard there a derived source is wired above
    an asserted input and the caller is told the connection succeeded.
    """
    derived = lane.create_point("statement", "derived source",
                                claim_class="derived")["id"]
    first = lane.create_point("statement", "first input",
                              claim_class="derived")["id"]
    # Legal at creation time (both endpoints computed).
    lane.create_operator("IMPL", derived, [first], promote_source=False)
    # The first input later becomes AUTHORED — a legitimate transition the
    # write guard cannot intercept.
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id REMOVE n.claimClass",
        params={"id": first})
    asserted = lane.create_point("statement", "asserted input")["id"]
    bundle = {
        "points": [], "entities": [], "sources": [],
        "connections": [{"from": derived, "to": [asserted, first],
                          "operator": "IMPL", "reify": True}],
    }
    with pytest.raises(ValueError, match="DERIVED"):
        lane.ingest(bundle, promotion_policy="auto")


def test_ingest_partial_absorb_allows_a_non_point_target(lane):
    """A :Object is not a claim — the ingest door agrees with the others.

    ``classes_for_ids`` is :Point-anchored, so a non-Point id is ABSENT from the
    class map. Reading that absence as "asserted" refused a derived source
    whose absorbed inputs are Objects — a shape ``create_operator`` admits — so
    the two doors onto one edge shape disagreed (#2351 review). The targets
    here are ONLY Objects: a derived Point target would make the readmission
    legal for the wrong reason.
    """
    derived = lane.create_point("statement", "derived source",
                                claim_class="derived")["id"]
    obj1, obj2 = "obj-2351-ingest-1", "obj-2351-ingest-2"
    for oid in (obj1, obj2):
        lane._get_proj().g.query(
            "CREATE (o:Object {id:$id, name:'thing'})", params={"id": oid})
    lane.create_operator("IMPL", derived, [obj1], promote_source=False)
    bundle = {
        "points": [], "entities": [], "sources": [],
        "connections": [{"from": derived, "to": [obj1, obj2],
                          "operator": "IMPL", "reify": True}],
    }
    lane.ingest(bundle, promotion_policy="auto")   # must not raise


def test_ep_backstop_leaves_same_class_factors_alone(lane):
    """Two asserted claims (no marker) propagate exactly as before."""
    src, tgt = _two(lane, src_class="asserted", tgt_class="asserted")
    _raw_direct_edge(lane, src, tgt, direction="<->")
    factor = _factor(lane, src, tgt)
    assert factor is not None and factor[5] == "<->"


# ── 5. the read side ─────────────────────────────────────────────────────────

def test_read_filter_separates_the_classes(lane):
    """``claim_class=`` filters, and reports each hit's class."""
    d = lane.create_point("statement", "zebra computed claim",
                          claim_class="derived")["id"]
    a = lane.create_point("statement", "zebra authored claim")["id"]

    derived_hits = lane.tortoise_fts_query("zebra", claim_class="derived")
    asserted_hits = lane.tortoise_fts_query("zebra", claim_class="asserted")

    assert {r["id"] for r in derived_hits} == {d}
    # The authored set includes the UNMARKED claim — absence IS asserted, which
    # is what makes the filter usable without migrating the existing graph.
    assert {r["id"] for r in asserted_hits} == {a}
    # ... and the filter ANNOTATES, so a consumer can tell the two apart.
    assert derived_hits[0].get("claim_class") == "derived"
    assert asserted_hits[0].get("claim_class") == "asserted"


def test_read_without_the_param_is_unchanged(lane):
    """``None`` = no filter AND no key — byte-identical for every old caller."""
    d = lane.create_point("statement", "zebra claim", claim_class="derived")
    a = lane.create_point("statement", "zebra claim 2")
    rows = lane.tortoise_fts_query("zebra")
    assert rows
    assert {r["id"] for r in rows} == {d["id"], a["id"]}, "None must not filter"
    for row in rows:
        assert "claim_class" not in row, "None must not emit the annotation"


def test_the_write_guard_judges_the_leg_that_flows(lane):
    """A one-way ``<-`` is judged on its target->source leg (#2351 review P1).

    Judging the SOURCE slot alone inverted both arms: it refused ``<-`` FROM a
    derived claim (only target->source flows, i.e. the permitted corroboration)
    and admitted ``<-`` INTO a derived target (whose flowing leg is exactly the
    derived->asserted push). The door and the EP backstop must agree.
    """
    derived = lane.create_point("statement", "computed",
                                claim_class="derived")["id"]
    asserted = lane.create_point("statement", "authored")["id"]

    # `<-` from a DERIVED source: the asserted claim corroborates it. Legal.
    lane.create_direct_edge("IMPL", derived, asserted, direction="<-")
    lane.create_operator("IMPL", derived, [asserted], direction="<-")

    # `<-` INTO a derived target: the flowing leg is derived -> asserted.
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_direct_edge("IMPL", asserted, derived, direction="<-")
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("IMPL", asserted, [derived], direction="<-")

    # `->` is the mirror: the derived SOURCE is the one that may not push.
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_direct_edge("IMPL", derived, asserted, direction="->")


def test_a_derived_claim_may_be_wired_to_a_non_point_endpoint(lane):
    """A :Object is not a CLAIM — its absent marker is not "asserted".

    ``claimClass`` is a :Point property. Reading an Object's absence as
    "asserted" refused a derived point wired to an Object, a shape #7902 made
    writable (owner ruling #7813) — and told the caller to mark the Object
    derived, which no door can do (#2351 review P1).
    """
    derived = lane.create_point("statement", "computed",
                                claim_class="derived")["id"]
    obj = "obj-2351-endpoint"
    lane._get_proj().g.query(
        "CREATE (o:Object {id:$id, name:'thing'})", params={"id": obj})
    lane.create_direct_edge("IMPL", derived, obj)
    lane.create_operator("IMPL", derived, [obj])


def test_ep_backstop_does_not_read_a_non_point_target_as_asserted(lane):
    """A :Object target is not an ASSERTED claim — the load side must agree.

    The write doors build their class sets from ``:Point`` rows only, so a
    DERIVED point wired to an ``:Object`` is admitted. Reading that Object's
    absent ``claimClass`` as the asserted DEFAULT at load reintroduced the very
    conflation the doors exclude: the factor was narrowed from
    ``bidirectional`` to ``<-``, so the computed claim silently could no longer
    feed the endpoint it was wired to (#2351 review P1).
    """
    obj = "obj-2351-ep-impl-target"
    lane._get_proj().g.query(
        "CREATE (o:Object {id:$id, name:'thing'})", params={"id": obj})
    derived = lane.create_point("statement", "computed",
                                claim_class="derived")["id"]
    op = lane.create_operator("IMPL", derived, [obj])
    factor = _factor(lane, op["id"], obj)
    assert factor is not None, "the factor must be built"
    assert factor[5] == "bidirectional", (
        "a non-claim target cannot be overridden, so the guard must not touch "
        f"the direction; got {factor[5]!r}"
    )


def test_ep_backstop_keeps_a_nand_attack_on_a_non_point_endpoint(lane):
    """A NAND whose target is an ``:Object`` must not be DROPPED as an override.

    A one-way NAND from a DERIVED claim to an ``:Object`` flows only the
    forward leg, so once the Object's absence read as "asserted" BOTH legs were
    blocked: the factor was dropped and reported as ``derived_over_asserted`` —
    an override that cannot exist, because the target is not a claim (#2351
    review P1).
    """
    obj = "obj-2351-ep-nand-target"
    lane._get_proj().g.query(
        "CREATE (o:Object {id:$id, name:'thing'})", params={"id": obj})
    derived = lane.create_point("statement", "computed",
                                claim_class="derived")["id"]
    op = lane.create_operator("NAND", derived, [obj], direction="->")
    ep = TortoiseEP(lane._get_proj())
    factors = ep._affected_factors({obj}, skipped=ep._last_skipped_factors)
    reasons = [sk.get("reason") for sk in ep._last_skipped_factors]
    assert "derived_over_asserted" not in reasons, (
        "a non-claim target is not an asserted claim — no override to report; "
        f"got {reasons!r}"
    )
    factor = next((f for f in factors if f[0] == op["id"]), None)
    assert factor is not None, "the factor must not be dropped"
    assert factor[5] == "->", (
        f"the declared direction must stand, got {factor[5]!r}")


def test_ep_backstop_does_not_narrow_a_direct_edge_to_a_non_point(lane):
    """The direct-edge arm has the same rule as the operator arm."""
    obj = "obj-2351-ep-direct-target"
    lane._get_proj().g.query(
        "CREATE (o:Object {id:$id, name:'thing'})", params={"id": obj})
    derived = lane.create_point("statement", "computed",
                                claim_class="derived")["id"]
    lane.create_direct_edge("IMPL", derived, obj)
    factor = _factor(lane, derived, obj)
    assert factor is not None, "the direct-edge factor must be built"
    assert factor[5] == "bidirectional", (
        "a non-claim endpoint participates in neither side of the guard; "
        f"got {factor[5]!r}"
    )


def test_ep_backstop_leaves_an_unprovable_source_slot_alone(lane):
    """No idx ⇒ the source slot is NOT provable, so no class verdict (#943).

    ``ORDER BY coalesce(r.idx, 0), c.id`` makes every idx-less input position
    0 and breaks the tie lexicographically on a legacy/migrated operator, so
    ``input_ids[0]`` may be a target. Judging a class against that slot can
    block the CORROBORATION leg and leave the override leg flowing — the exact
    inversion of this guard. The sibling source-slot guard already refuses to
    act when ``op_idx_known`` is false (#943 review); the class guard must do
    the same or it acts on a guess (#2351 review P2).
    """
    src = lane.create_point("statement", "authored source")["id"]
    computed = lane.create_point("statement", "computed input")["id"]
    op = lane.create_operator("IMPL", src, [computed])  # legal: both asserted
    # The class and the missing idx are written AFTER creation — the
    # pre-guard / bulk-written / migrated shape the backstop exists for.
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.claimClass='derived'",
        params={"id": computed})
    lane._get_proj().g.query(
        "MATCH (o:Point {id:$oid})-[r:IMPL]->(c) REMOVE r.idx",
        params={"oid": op["id"]})
    factor = _factor(lane, op["id"], computed)
    assert factor is not None, "the factor must not be dropped"
    assert factor[5] == "bidirectional", (
        "with no idx the source slot is unprovable, so the guard must not "
        f"narrow the factor on a guess; got {factor[5]!r}"
    )


def test_ingest_partial_absorb_canonicalizes_an_absent_nand_direction(lane):
    """An absent direction means NAND's ``unidirectional``, not the mutual one.

    ``direction_flows(None)`` is ``(True, True)`` — the MUTUAL default — but a
    direction-omitting NAND canonicalizes to ``unidirectional`` (the extraction
    default), so its only flowing leg is source→target. Judging the RAW token
    made the ingest door see a back-leg that never flows and REFUSE a wiring
    ``create_operator`` accepts for the identical edge shape (#2351 review P2).
    """
    src = lane.create_point("statement", "authored source")["id"]
    computed = lane.create_point("statement", "computed input",
                                 claim_class="derived")["id"]
    other = lane.create_point("statement", "second input")["id"]
    # Legal at creation: NAND with a one-way direction flows source→target
    # only, i.e. asserted → derived, which is the CORROBORATION leg.
    lane.create_operator("NAND", src, [computed], direction="->")
    bundle = {
        "points": [], "entities": [], "sources": [],
        "connections": [{"from": src, "to": [computed, other],
                          "operator": "NAND", "reify": True}],
    }
    lane.ingest(bundle, promotion_policy="auto")   # must not raise


def test_the_class_filter_runs_before_the_limit(lane):
    """The filter is PRE-TRUNCATION, so ``limit`` counts filtered candidates.

    Filtering after the cut would silently under-fill the caller's ``limit`` —
    the regression the ``exclude_status`` seam comment exists to prevent. The
    asserted row is given the stronger match so a post-cut filter returns
    nothing rather than passing by luck.
    """
    asserted = lane.create_point(
        "statement", "zebra zebra zebra zebra zebra authored")["id"]
    derived = lane.create_point("statement", "zebra computed",
                                claim_class="derived")["id"]
    rows = lane.tortoise_fts_query("zebra", claim_class="derived", limit=1)
    assert [r["id"] for r in rows] == [derived], (
        f"the filter must run before ``limit``; got {[r['id'] for r in rows]!r} "
        f"(asserted={asserted!r})")


def test_operator_with_no_targets_is_not_a_derived_override(lane):
    """An EMPTY target list is not "an asserted target" (#2351 review P2).

    ``any_asserted()`` returns True for an empty class set on purpose (one id
    whose node carries no marker IS asserted), but that must not be reused for
    an empty COLLECTION of targets — a derived source with nothing to override
    is a legal call.
    """
    pid = lane.create_point("statement", "computed",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", pid, [])
    assert op["id"]


def test_update_point_refuses_the_class_outright(lane):
    """``claimClass`` is a CREATE-time marker, not a revision prop (#2351).

    It is folded on ``PointAdded`` but no ``PointRevised`` arm replays it, so
    accepting it here wrote a LIVE-ONLY marker: a rebuild dropped it (a DERIVED
    claim became AUTHORED and the override guard stopped refusing it) or
    resurrected it after a clear. The door refuses every spelling instead.
    """
    pid = lane.create_point("statement", "computed",
                            claim_class="derived")["id"]
    for spelling in ("claimClass", "claim_class"):
        with pytest.raises(ValueError, match="written by create_point"):
            lane.update_point(pid, **{spelling: "asserted"})
        with pytest.raises(ValueError, match="written by create_point"):
            lane.update_point(pid, **{spelling: "derived"})
        with pytest.raises(ValueError, match="written by create_point"):
            lane.update_point(pid, **{spelling: "junk"})
    assert _props(lane, pid).get(cc.PROP) == "derived", (
        "a refused update must not change the stored class")


def test_update_entity_cannot_restamp_or_clear_the_class(lane):
    """The SECOND documented ``:Point`` update door refuses it too (#2351).

    ``commit_schema`` lists the pair ``update_point`` / ``_update_entity``, and
    ``update_entity`` is the generic tenant surface (``tortoise_update_entity``)
    whose Point branch writes caller props with a live ``SET n += $p``. With the
    refusal living only in ``update_point``, the same four escapes stayed open
    one door over: ``'asserted'`` moves a derived claim out of every #2351
    guard, ``None`` deletes the marker (no marker reads as asserted), a junk
    token persists a class the closed vocabulary exists to prevent, and all of
    them are LIVE-ONLY — a rebuild silently drops the write. The refusal is at
    the shared ``_sanitize_props`` chokepoint, which is what this test pins and
    which is why it REDS when the ``reject_claim_class`` flag is removed from
    this door.
    """
    pid = lane.create_point("statement", "computed claim",
                            claim_class="derived")["id"]
    for spelling in ("claimClass", "claim_class"):
        for bad in ("asserted", None, "bogus"):
            with pytest.raises(ValueError, match="written by create_point"):
                lane.update_entity(pid, **{spelling: bad})
            # The refusal is BEFORE the write: the marker is untouched.
            assert _props(lane, pid).get(cc.PROP) == cc.DERIVED
    # ... and the door still WORKS for an ordinary property, so the refusal is
    # one name and not a dead surface.
    lane.update_entity(pid, summary="authored summary")
    assert _props(lane, pid).get("summary") == "authored summary"
    assert _props(lane, pid).get(cc.PROP) == cc.DERIVED


def test_single_input_factor_is_not_reported_as_an_override(lane):
    """No targets is not "an asserted target" (#2351 review).

    ``any_asserted(∅)`` is True for ONE id carrying no marker; it must not be
    reused for an empty COLLECTION of targets, or a derived claim's degenerate
    single-input factor is dropped and misreported as an override that cannot
    exist (the write surface guards the same conflation).
    """
    pid = lane.create_point("statement", "computed",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", pid, [], direction="->")
    ep = TortoiseEP(lane._get_proj())
    factors = ep._affected_factors({pid}, skipped=ep._last_skipped_factors)
    reasons = [sk.get("reason") for sk in ep._last_skipped_factors]
    assert "derived_over_asserted" not in reasons, (
        f"a factor with no targets can override nothing, got {reasons!r}")
    assert any(f[0] == op["id"] for f in factors), (
        "the degenerate factor must still be built — it is a no-op, not a drop")


def test_class_marker_survives_a_rebuild(tmp_path):
    """The marker is REPLAY-OWNED: a rebuild must not drop it (#2351 P0).

    ``create_point`` folds ``claimClass`` on ``PointAdded``, so the class
    survives a journal replay. That is the invariant ``update_point`` cannot
    honour — it journals a revision no fold replays — and the reason that door
    refuses the key instead of writing a live-only marker.
    """
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "rebuild.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        from tortoise.projection.entities import _EntityHandlers
        assert "claimClass" in _EntityHandlers._POINT_DECLARED_PROPS, (
            "the marker must be DECLARED, not merely restored by the open-set "
            "passthrough — an undeclared prop logs drift on every rebuild "
            "(#2795), and nothing else pins the declaration")
        pid = sdk.create_point("statement", "computed",
                               claim_class="derived")["id"]
        assert _props(sdk, pid).get(cc.PROP) == "derived"
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert _props(sdk, pid).get(cc.PROP) == "derived", (
            "rebuild dropped the class marker — a DERIVED claim silently "
            "became AUTHORED and the override guard stopped refusing it")
    finally:
        sdk.close()


def test_degraded_fallback_tier_honours_the_class_filter(lane, monkeypatch):
    """A class-filtered read on a SICK store must not return excluded rows.

    The fallback tiers return BEFORE the primary filter block, so the filter
    and annotation are applied to the corpus by hand there. Without that, a
    degraded read silently hands back the claims the caller asked to exclude —
    the one failure mode a filter must never have.
    """
    from tortoise import search_engine

    derived = lane.create_point("statement", "zebra derived claim",
                                claim_class="derived")["id"]
    asserted = lane.create_point("statement", "zebra asserted claim")["id"]

    def _all_legs_failed(*_a, **_k):
        return {}  # every strategy failed ⇒ the in-memory TF-IDF tier

    monkeypatch.setattr(search_engine, "degradation_chain", _all_legs_failed)
    rows = lane.tortoise_fts_query("zebra", claim_class="derived")
    ids = {r.get("id") for r in rows}
    assert ids and ids == {derived}, (
        f"the degraded tier must still exclude the asserted claim, got {ids!r}")
    assert asserted not in ids
    assert all(r.get("claim_class") == "derived" for r in rows), (
        f"the tier must annotate its rows, got {rows!r}")


def test_read_surface_is_a_param_not_a_new_tool():
    """Indicator 3 is +0 MCP tools: the seam is a kwarg on an existing read."""
    assert "claim_class" in inspect.signature(
        TortoiseSDK.tortoise_fts_query).parameters
    assert "claim_class" in inspect.signature(
        TortoiseSDK.create_point).parameters
