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

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)
    # The ordered arm PROVED the source slot (all-int idx, ``ep.py``'s own
    # ``ORDER BY coalesce(r.idx, 0), c.id``), so the message asserts it instead
    # of hedging — and it must NOT borrow the candidate arm's caveat.
    _msg = str(_exc.value)
    assert "at the SOURCE position" in _msg, _msg
    assert "may read as the SOURCE" not in _msg, _msg

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


def test_supersede_repoint_lets_a_same_class_supersede_through_a_null_idx(lane):
    """No ``idx`` on the slot edges ⇒ the loader reads position 0, so the door does too.

    An idx-less (legacy/migrated) operator is NOT an unorderable layout: the load
    side reads the factor with ``ORDER BY coalesce(r.idx, 0), c.id``, so a NULL
    ``idx`` IS position 0 and a tie is broken by the claim ID. The door coerces
    the same way, takes the same single candidate source, and JUDGES — it does
    not stand down.

    ⛔ The pre-fix stand-down here rested on "``ep.py`` clears
    ``op_idx_known[op_id]`` under ``if idx is None``, and that flag gates its
    #2351 backstop". ``#10404`` deleted that gate, so on the merged result the
    load side JUDGED an idx-less factor while the door skipped it — the #10195
    fail-open, reachable by REMOVING a property.

    ⚠️ TWO cases were needed to say that honestly, because the door now ORDERS
    this layout. This one keeps the successor COMPUTED, so no candidate
    assignment can override and the supersede must go through (the
    anti-over-refusal half); ``…refuses_an_idx_less_derived_over_asserted``
    authors the successor and pins the refusal (the discriminating half — it is
    the SAME wiring as the pre-fix case below, which consequently had to change:
    with ``src`` tie-broken first it is exactly derived-source-over-asserted-
    successor, i.e. the illegal factor, not an exemption).
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL|NAND]->() REMOVE r.idx",
        params={"op": op["id"]})
    # COMPUTED successor: same class on both sides, so the guard has nothing to
    # say under ANY candidate source assignment.
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    assert lane.supersede_point(old, new)["edges_transferred"] == 1
    # The slot MOVES and the absent ``idx`` stays absent — 2a reads ``idx``
    # opaquely, exactly as it preserves a string one.
    assert _operator_rows(lane, new) == [[op["id"], "IMPL", None]]
    assert _operator_rows(lane, old) == []


def test_supersede_repoint_refuses_an_idx_less_derived_over_asserted(lane):
    """The idx-less twin of ``…refuses_a_raw_written_slot_idx`` (#10195 P2).

    The pre-fix door skipped every operator whose slot edges carried no ``idx``,
    on the premise that the LOAD side stood down there too. ``#10404`` deleted
    that stand-down (``ep.py``: "⛔ NOT gated on ``op_idx_known`` (#10364)"), so
    on the merged result the loader judges an idx-less factor while the door
    skipped it — the same fail-open #10195 is filed for, on the one axis left,
    reachable by REMOVING a property instead of writing a hostile value:

        src(derived) --IMPL(->)--> old(derived)   # no ``idx`` on either edge
        supersede old -> new(asserted)            # door: skip ⇒ SUCCEEDS
        ⇒ derived --IMPL--> asserted, no verdict, no warning, no record,
          which the very next EP run drops and reports.

    The layout is not even unorderable: ``ep.py`` reads the factor with
    ``ORDER BY coalesce(r.idx, 0), c.id``, so a NULL ``idx`` IS position 0 and
    the tie breaks on the claim ID — exactly the duplicated-``idx`` arm the door
    already reads. So the door coerces NULL to 0 and judges, and must REFUSE
    this wiring before any mutation.

    ⛔ THE TWO IDS ARE PINNED, because position 0 is tie-broken LEXICOGRAPHICALLY
    and ``ids.ulid()`` is ``<ms-hex>-<uuid4().hex[:12]>`` (``tortoise/ids.py``) —
    two mints in the same millisecond sort by their RANDOM suffix, so the source
    would be a coin flip. With ``src`` pinned first it provably IS the source,
    the successor is authored, and the rebuilt factor is the illegal one.

    Pre-fix this is RED: the door took ``continue``, so the supersede returned
    success and ``pytest.raises`` cannot pass.
    """
    src = lane.create_point("statement", "computed source",
                            claim_class="derived",
                            id="0000000001-aaaaaaaaaaaa")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # The legacy / pre-guard / migrated shape: NO ``idx`` on any slot edge. This
    # is a missing property, not a value any writer had to forge.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->() REMOVE r.idx",
        params={"op": op["id"]})
    new = lane.create_point("statement", "authored v2",
                            id="0000000002-bbbbbbbbbbbb")["id"]

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    # Refused BEFORE any mutation, and the refused wiring is not in the graph.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", None]]
    assert _operator_rows(lane, new) == []


@pytest.mark.parametrize("int_slot, str_idx", [(1, "1"), (0, "0")])
def test_supersede_repoint_lets_a_same_class_supersede_through_a_string_idx(
        lane, int_slot, str_idx):
    """A slot ``idx`` stored as a STRING is neither a crash nor a refusal.

    The graph is schemaless, so any hand/foreign writer can leave ``r.idx='1'``
    beside an int ``idx`` on the same operator. Two failure modes sit on that
    shape and this test pins that neither is taken:

    * sorting raw keys raises ``TypeError`` and aborts a supersede the class
      guard is not even about (the pre-fix crash);
    * SKIPPING the factor would be a fail-open exemption — EP's load side keeps
      it (``op_idx_known`` is cleared for a NULL ``idx`` only).

    So a mixed-type layout takes the candidate arm: no position is provably the
    source, every input is a candidate, and here both endpoints are computed, so
    no assignment overrides and the supersede must go through and carry the
    predecessor's slot — idx VALUE and type preserved, as 2a reads ``idx``
    opaquely.

    ⚠️ This is an ANTI-OVER-REFUSAL pin, not a discriminator: it passes on the
    pre-fix stand-down too (a skip also lets the supersede through), so it cannot
    witness the fail-open. `…refuses_a_raw_written_slot_idx` and
    `…refuses_when_a_string_idx_outranks_the_source` are the cases that do.
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


@pytest.mark.parametrize("raw_idx", ["1", "+-1", "slot-one", 1.0])
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

    Three unprovable-looking shapes are pinned: a string that looks like a slot
    number (`'1'`), a string that only LOOKS numeric (`'+-1'` — the shape that
    crashed the first cut of the coercion, because `str.lstrip('+-').isdigit()`
    accepts it but `int()` refuses it), one that names no number at all
    (`'slot-one'`), and a FLOAT (`1.0`). Each takes the same candidate arm —
    MEASURED: the engine's `ORDER BY coalesce(r.idx, 0), c.id` groups a STRING slot
    AHEAD of a numeric one, so no numeric coercion can reproduce the slot order and
    none is attempted — and each must still REFUSE this wiring.

    ⚠️ The `float` case is INCLUDED and it is discriminating (#10418 review P3).
    An earlier cut omitted it, claiming the engine returned `1` (an `int`) for a
    property written `1.0`, so the case would take the int path and "pass" on the
    pre-fix code too. That does not reproduce: `SET r.idx = 1.0` over an operator's
    slot edges returns `[(1.0, 'Float'), (1, 'Integer')]` (measured on the
    carve-out engine, 2026-10-31), i.e. the layout IS mixed, the door takes the
    candidate arm, and the pre-fix blanket skip let the supersede through.
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

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)

    # ONE non-``int`` ``idx`` beside an ``int`` one is a MIXED layout: the slot
    # order the loader reads is the engine's CROSS-TYPE grouping, which this door
    # deliberately does not re-derive — so THAT is what the refusal must say
    # (#10418 review P3). Pre-fix the message was a single phrase for the whole
    # candidate arm: "the slots carry an idx that cannot be ordered".
    _msg = str(_exc.value)
    if isinstance(raw_idx, str):
        assert "MIXED types" in _msg, _msg
    else:
        # ⚠️ A FLOAT ``idx`` is measured UNSTABLE in storage, so this case pins
        # the VERDICT, not the phrase: `SET r.idx = 1.0` over an operator's slot
        # edges returned `[(1.0, 'Float'), (1, 'Integer')]` in one measurement and
        # all-`Integer` in this one, i.e. which arm the door takes depends on the
        # value the engine hands back (Float → the mixed/candidate arm; Integer →
        # the ordered arm, where the source really IS proven). Both arms REFUSE
        # this wiring, which is the property that matters — asserting a phrase
        # here would pin the engine's storage choice, not the door.
        assert ("MIXED types" in _msg or "at the SOURCE position" in _msg), _msg
    assert "cannot be ordered" not in _msg, _msg

    # Refused BEFORE any mutation, and the refused wiring is not in the graph.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", raw_idx]]
    assert _operator_rows(lane, new) == []


def test_supersede_repoint_refuses_when_a_string_idx_outranks_the_source(lane):
    """The mirror a numeric coercion cannot see — measured on the engine.

    ``ORDER BY coalesce(r.idx, 0), c.id`` groups by TYPE before it compares
    values on FalkorDB, so a STRING slot sorts AHEAD of a numeric one. Here the
    DERIVED claim sits at ``idx='1'`` and the predecessor at int ``0``: the
    loader reads the STRING as the source, so the rebuilt factor is
    ``derived --IMPL--> asserted`` — while a door that coerced ``'1'``→1 would
    read the predecessor's slot as the source, see nothing to refuse, and mint
    exactly the wiring #10195 is filed for (measured: the load side then reports
    ``derived_over_asserted`` for the operator the supersede created).

    So a mixed-type layout is NOT ordered: every input is a candidate source and
    any overriding assignment refuses.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    # Predecessor at the int slot 0, the other input at 1 — then make the SECOND
    # slot a string, i.e. the type mix a hand/foreign writer leaves behind.
    op = lane.create_operator("IMPL", old, [src], direction="->")
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->(n:Point {id:$tgt}) "
        "SET r.idx = '1'",
        params={"op": op["id"], "tgt": src})
    new = lane.create_point("statement", "authored v2")["id"]

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)

    # int ``0`` beside str ``'1'`` — the CROSS-TYPE arm, and the refusal says so.
    assert "MIXED types" in str(_exc.value), _exc.value

    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, new) == []


def test_supersede_repoint_refuses_a_uniformly_non_int_slot_layout(lane):
    """A layout with NO ``int`` idx at all is still JUDGED — fail CLOSED.

    The siblings above pin the MIXED layouts; this pins the homogeneous
    NON-``int`` one, which the door also declines to order (``sdk.py``: slot
    order is re-implemented for an all-``int`` layout only, so every other
    layout takes the candidate arm — a recorded, deliberately fail-closed
    trade-off: a false refusal, never an exemption).

    It is the safe arm because the wiring is illegal under EITHER reading: all
    strings, the engine orders ``'0'`` before ``'1'``, so the load side reads the
    DERIVED claim as the source of ``derived --IMPL--> asserted`` and skips the
    factor — so refusing it is PARITY with the loader, not over-refusal.

    Pre-fix (any non-``int`` ``idx`` ⇒ blanket stand-down) this supersede went
    through and minted exactly that factor with no verdict, no warning, no
    record (#10195 review P2).
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # BOTH slots re-stamped as strings — the door's ordered set is all-int only.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->() SET r.idx = '1'",
        params={"op": op["id"]})
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->(n:Point {id:$src}) SET r.idx = '0'",
        params={"op": op["id"], "src": src})
    # The successor is AUTHORED — issue #10195's reproduction, plus the layout.
    new = lane.create_point("statement", "authored v2")["id"]

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)

    # ⛔ THE STATED CAUSE IS THE DOOR'S OWN CHOICE, not an engine limitation
    # (#10418 review P3). Both slots are strings, i.e. ONE type group — the
    # engine DOES order them (comparison inside a group is type-local), so
    # "cannot be ordered" was false and sent the caller hunting a graph defect
    # that is not there. What is true is that the door declines to re-derive
    # that per-type order, and the message must say that instead.
    _msg = str(_exc.value)
    assert "per-type slot order" in _msg, _msg
    assert "cannot be ordered" not in _msg, _msg
    assert "MIXED types" not in _msg, _msg

    # Refused BEFORE any mutation, and the refused wiring is not in the graph.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", "1"]]
    assert _operator_rows(lane, new) == []


def test_supersede_repoint_judges_a_duplicated_slot_idx(lane):
    """One ``idx`` carried by TWO inputs is judged on the loader's own tie-break.

    Pre-fix this layout was a blanket skip, so the supersede minted
    ``derived --IMPL--> asserted`` with no verdict. The load side does NOT skip
    it: it builds the factor from ``ORDER BY coalesce(r.idx, 0), c.id`` and takes
    the FIRST row's claim as the source, so the door mirrors exactly that — one
    candidate, tie-broken by claim id — instead of either skipping (fail-open) or
    judging every tied input (which would refuse a shape the loader keeps).

    The tie-break is load-bearing here: the ``src`` that sorts first IS the
    source slot the loader reads, and once the successor is authored the rebuilt
    factor is the derived-over-asserted wiring the door refuses.

    ⛔ THE TWO IDS ARE PINNED, because the tie-break is LEXICOGRAPHIC and the
    minted id is NOT creation-ordered: ``ids.ulid()`` is
    ``<ms-hex>-<uuid4().hex[:12]>`` (``tortoise/ids.py:26``), so two points
    created inside the SAME millisecond sort by their RANDOM suffix. The ULID
    spec only guarantees same-millisecond creation order for a STATEFUL MONOTONIC
    generator that increments the randomness on each draw, which this is not. So
    the earlier-created point is not reliably the smaller id, and when the
    successor drew the smaller suffix the door (correctly, in parity with
    ``ep.py``'s ``ORDER BY coalesce(r.idx, 0), c.id``) took IT as the source,
    judged ``asserted --IMPL--> derived``, and let the supersede through — making
    this test's expectation, not the door, the wrong half. Pinning ``src < new``
    makes the SHAPE decide the verdict instead of the draw (a flaky test that
    fails on a random 12-hex draw is not a coverage guarantee).
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived",
                            id="0000000001-aaaaaaaaaaaa")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # Collapse the target slot onto position 0: two inputs, one ``idx``.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->() WHERE r.idx = 1 SET r.idx = 0",
        params={"op": op["id"]})
    new = lane.create_point("statement", "authored v2",
                            id="0000000002-bbbbbbbbbbbb")["id"]

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_rows(lane, old) == [[op["id"], "IMPL", 0]]
    assert _operator_rows(lane, new) == []


def test_supersede_repoint_takes_the_loaders_tie_break_the_other_way(lane):
    """The MIRROR of the case above: the SUCCESSOR sorting first is the source.

    The same duplicated ``idx`` layout with the ids the other way round. Here
    ``new`` is the smaller claim id, so it is the input the loader reads as the
    source — the rebuilt factor is ``asserted(new) --IMPL--> derived(src)``,
    which overrides nothing, so the door must PASS the supersede and transfer the
    predecessor's slot.

    This is the half that pins the round-1 decision ("one candidate, tie-broken
    by claim id") from the over-refusal side: a door that judged EVERY tied input
    would find the ``derived(src) --IMPL--> asserted(new)`` assignment among the
    candidates and refuse a shape the load side keeps. Both ids are pinned for
    the reason given on the case above — ``ids.ulid()`` has a random 12-hex
    suffix, so creation order is not id order inside one millisecond.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    src = lane.create_point("statement", "computed source",
                            claim_class="derived",
                            id="0000000002-bbbbbbbbbbbb")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # The same collapse: two inputs, one ``idx``.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL]->() WHERE r.idx = 1 SET r.idx = 0",
        params={"op": op["id"]})
    new = lane.create_point("statement", "authored v2",
                            id="0000000001-aaaaaaaaaaaa")["id"]

    # No raise: ``new`` outranks ``src`` at the tied idx, it is the source the
    # loader reads, and asserted-over-derived is the legal direction.
    assert lane.supersede_point(old, new)["edges_transferred"] == 1
    assert _operator_rows(lane, new) == [[op["id"], "IMPL", 0]]
    assert _operator_rows(lane, old) == []


def test_supersede_repoint_judges_every_stored_direction(lane):
    """A duplicated operator NODE's OTHER direction is judged, not dropped.

    ``Point`` ids are not unique (#7822 — the same fan-out the lifecycle guard
    and ``classes_for_ids`` already read), so ONE operator ``id`` can be carried
    by two nodes that disagree about ``direction``. Collapsing those rows to one
    stored value picks an arbitrary row; collapsing them to the per-type default
    is not conservative either, because a NAND's ABSENT default is
    ``unidirectional`` — ONE leg — so a ``<-`` row's BACKWARD leg goes unjudged.
    That is the same fail-open the mixed-``idx`` arm closes, on the direction
    axis: the door would be more permissive than the load side.

    The factor here is ``asserted source --NAND--> old``, legal under ``->``, and
    the predecessor's successor is DERIVED — so the ``->`` leg refuses nothing
    while the ``<-`` leg is exactly ``derived_over_asserted`` (the load side
    reports it when it reads that row). The door must refuse either way.
    """
    src = lane.create_point("statement", "authored source")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("NAND", src, [old], direction="->")
    # A SECOND node under the SAME operator id — the raw-write shape, and the
    # way this codebase already models a non-unique Point id — carrying the
    # other stored direction and its own copy of the operator's slot edges.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op}) "
        "CREATE (dup:Point) SET dup = op "
        "SET dup.direction = '<-' "
        "WITH op, dup "
        "MATCH (op)-[r:NAND]->(i) "
        "CREATE (dup)-[:NAND {idx: r.idx}]->(i)",
        params={"op": op["id"]})
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    with pytest.raises(ValueError, match="re-pointing the OPERATOR"):
        lane.supersede_point(old, new)

    # Refused BEFORE any mutation, and the refused wiring is not in the graph.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
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


def _operator_shaped_rows(lane, claim_id):
    """``(op_id, rel, idx)`` for every operator edge reaching ``claim_id``.

    The CANONICAL operator population — ``is_operator = true OR op_type IS NOT
    NULL``, the predicate EP's Batch-1 filter reads — so a legacy / migrated
    operator that lost the boolean flag is still visible. ``_operator_rows``
    above is deliberately the NARROW form: most cases in this file pin what the
    ordinary writer mints; only the flag-less cases need this one.
    """
    return lane._get_proj().g.query(
        "MATCH (op:Point)-[r:IMPL|NAND]->(n:Point {id:$id}) "
        "WHERE (op.is_operator = true OR op.op_type IS NOT NULL) "
        "RETURN op.id, type(r), r.idx",
        params={"id": claim_id}).result_set


def test_supersede_repoint_judges_a_flag_less_operator(lane):
    """The operator POPULATION is the canonical one, not ``is_operator`` alone.

    EP's Batch-1 predicate is ``(o.is_operator = true OR o.op_type IS NOT
    NULL)`` — a legacy / migrated operator carries ``op_type`` and may have
    lost the boolean flag (`graph-scripts/backfill_is_operator.py` stamps
    exactly that class) — and the repo spells that same disjunction at five
    other sites. The pre-flight read only ``is_operator:true``, so this node was
    invisible to it TWICE: the population query missed it (the whole guard was
    skipped) and the slot query missed it (so the code took the "no slot rows
    ⇒ no factor to judge" branch) — while 2a-DIRECT, whose "is the far endpoint
    an operator?" set was the same narrow predicate, treated its ``IMPL`` edge
    as an operator-LESS direct edge and re-pointed it. Net: #10195's exact harm,
    reached by REMOVING a property instead of writing a hostile value:

        src(derived) --IMPL(idx 1)--> old(derived)   # no ``is_operator``
        supersede old -> new(asserted)               # door: no verdict
        ⇒ derived --IMPL--> asserted, a factor the loader BUILDS (``op_type``
          is set) and drops with ``derived_over_asserted`` — no verdict, no
          warning, no record on the write side.

    Pre-fix this is RED: the supersede returned success, so ``pytest.raises``
    cannot pass.
    """
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # The legacy / migrated operator shape: ``op_type`` present, the boolean
    # flag never written or dropped by a migration — a MISSING property.
    lane._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.is_operator",
        params={"id": op["id"]})
    new = lane.create_point("statement", "authored v2")["id"]

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    # Refused BEFORE any mutation: neither point terminalised, the edge is still
    # at the predecessor with its slot position, and nothing reached the
    # successor.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_shaped_rows(lane, old) == [[op["id"], "IMPL", 1]]
    assert _operator_shaped_rows(lane, new) == []


def test_supersede_repoint_carries_a_flag_less_operator_edge_with_its_slot(
        lane):
    """The same population fix on the LEGAL case: 2a transfers it, 2a-DIRECT not.

    An operator edge must keep its ``idx`` slot position across the supersede,
    because that position IS the factor's source/target role, and its ``INPUT``
    support mirror must follow the support source (#6180). Both are 2a's job.
    2a-DIRECT's MERGE is BARE over ``DIRECT_EDGE_ATTR_KEYS`` — ``idx`` is not a
    member, so the slot position is LOST — and it emits a ``DirectEdgeRepoint``
    descriptor for a factor the loader builds as an OPERATOR factor, which is
    the topology the replay then reproduces.

    Pre-fix RED on all three assertions: the edge comes back with ``idx`` NULL,
    the mirror stays on the dead prior, and one ``DirectEdgeRepoint`` is
    emitted for an operator edge.
    """
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    lane._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.is_operator",
        params={"id": op["id"]})
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    lane.supersede_point(old, new)

    # The slot position survived, and the operator's SUPPORT mirror followed.
    assert _operator_shaped_rows(lane, old) == []
    assert _operator_shaped_rows(lane, new) == [[op["id"], "IMPL", 1]]
    assert {
        (row[0], row[1]) for row in lane._get_proj().g.query(
            "MATCH (n:Point)-[ri:INPUT]->(op:Point {id:$op}) "
            "RETURN n.id, ri.idx",
            params={"op": op["id"]}).result_set
    } == {(src, 0), (new, 1)}   # the support source, and the successor at the
    # slot the predecessor held — never the dead prior.
    # No direct-edge re-point descriptor for an OPERATOR edge (the replay would
    # re-MERGE it as a bare direct edge).
    assert lane._get_proj().g.query(
        "MATCH (e:GraphEvent) WHERE e.type='DirectEdgeRepoint' "
        "RETURN count(e)").result_set[0][0] == 0


@pytest.mark.parametrize("stored_op_type", ["hasPart", "composedOf"])
def test_supersede_repoint_judges_a_stored_op_type_outside_the_pair(
        lane, stored_op_type):
    """A stored ``op_type`` outside ``IMPL|NAND`` is no exemption.

    ``create_operator`` routes every other ``op_type`` to a ``hasPart`` edge, so
    this shape is raw-write only — but EP's Batch-1 predicate filters on
    ``op_type IS NOT NULL``, not on its VALUE, so the loader still builds this
    ``IMPL`` factor and runs the #2351 backstop on it. The door skipped it: it
    iterated only the STORED ``op_type`` and ``continue``d on anything outside
    the pair, so the supersede carried no verdict at all. The mechanism to
    judge is the REL TYPE that carried the operator into the set — the same
    fallback the absent-``op_type`` arm already takes.

    Pre-fix RED: the supersede succeeded.
    """
    src = lane.create_point("statement", "computed source",
                            claim_class="derived")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    lane._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.op_type=$t",
        params={"id": op["id"], "t": stored_op_type})
    new = lane.create_point("statement", "authored v2")["id"]

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)

    assert _props(lane, old).get("status") == "live"
    assert _operator_shaped_rows(lane, old) == [[op["id"], "IMPL", 1]]
    assert _operator_shaped_rows(lane, new) == []


def test_supersede_repoint_judges_the_REL_TYPE_when_the_stored_op_type_is_weaker(
        lane):
    """The MECHANISM a re-pointed edge carries is judged, not just the stored one.

    The door used to judge ONE mechanism per operator: the stored ``op_type``
    when it was in the pair, else whichever rel type ``edges_result`` returned
    FIRST (``.setdefault(...)`` over a statement with no ``ORDER BY``). The two
    are not equivalent, and the stored one can be the WEAKER of the two —
    ``_canonical_direction("NAND", None)`` is ``unidirectional`` (one leg),
    while ``IMPL``'s absent default is ``bidirectional`` (both legs, which is
    also what ``ep.py`` canonicalizes an absent direction to). Here the node
    records ``NAND`` while the edge 2a re-points is an ``IMPL``, and the wiring
    is derived-target-under-asserted-source — refused on IMPL's BACKWARD leg,
    invisible to NAND's forward-only reading:

        src(asserted) --IMPL(no direction, idx 1)--> old(derived)
        supersede old -> new(derived)

    Pre-fix the supersede went through with no verdict — #10195's harm on the
    MECHANISM axis, reachable by a raw ``op_type`` write. RED pre-fix and
    deterministic (the stored value is in the pair, so there is no row-order
    coin flip): the union below is what makes it refuse.
    """
    src = lane.create_point("statement", "authored source")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    # Legal AS WRITTEN (``->`` is the corroboration leg). The absent-direction
    # shape is then restored by the raw write below — that is the legacy form.
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # A stored mechanism the node does not actually carry, and no direction:
    # the rel type 2a re-points is the ONLY record of the real mechanism.
    lane._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.op_type='NAND' REMOVE n.direction",
        params={"id": op["id"]})
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    with pytest.raises(ValueError, match="re-pointing the OPERATOR") as _exc:
        lane.supersede_point(old, new)

    # The mechanism that BLOCKS is the one the edge carries — named, so the
    # caller is not told about a NAND they will not find.
    assert "(IMPL," in str(_exc.value), _exc.value
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_shaped_rows(lane, old) == [[op["id"], "IMPL", 1]]
    assert _operator_shaped_rows(lane, new) == []


def test_supersede_repoint_judges_BOTH_mechanisms_of_a_legacy_operator(lane):
    """Two mechanisms into the predecessor, no stored record — judge BOTH.

    The shape ``.setdefault(...)`` could not handle: one operator with an
    ``IMPL`` AND a ``NAND`` edge into the predecessor, no stored ``op_type``
    and no stored ``direction`` (a legacy node, or the raw-write
    ``op_type='hasPart'`` shape). 2a re-points BOTH edges, so both mechanisms
    reach the rebuilt factor — but the door judged whichever one the
    ``edges_result`` row order happened to hand ``setdefault`` first, and here
    the two disagree: ``NAND``'s absent default is ``unidirectional`` (forward
    only ⇒ the asserted source over the derived target is legal), ``IMPL``'s is
    ``bidirectional`` (its BACKWARD leg refuses exactly that wiring). So the
    verdict — and the refusal text — flipped between runs, which is the one
    thing the slot query's own ``ORDER BY op.id, inp.id`` was added to prevent.

    Post-fix every mechanism the re-pointed edge carries is judged, so the
    refusal holds whichever row comes back first. RED pre-fix on the NAND-first
    order (the assertable half): there the supersede succeeded and minted
    ``derived --IMPL--> asserted`` with no verdict, no warning, no record.
    """
    src = lane.create_point("statement", "authored source")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # 2a transfers ``IMPL|NAND`` edges and nothing narrower.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op}), (old:Point {id:$old}) "
        "MERGE (op)-[r:NAND {idx:1}]->(old)",
        params={"op": op["id"], "old": old})
    lane._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.op_type, n.direction",
        params={"id": op["id"]})
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    with pytest.raises(ValueError, match="re-pointing the OPERATOR"):
        lane.supersede_point(old, new)

    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _operator_shaped_rows(lane, new) == []


def test_supersede_repoint_survives_a_non_string_stored_direction(lane):
    """A raw-written non-string ``direction`` must not CRASH the door.

    ``_canonical_direction`` returns a stored value VERBATIM (only an absent one
    is defaulted), so the door's iteration over the stored directions can hold
    mixed types — and the bare ``sorted({1, "->"})`` raised
    ``TypeError: '<' not supported between instances of 'str' and 'int'``, a
    traceback aborting a supersede this guard is not even about (#10418 review
    P3). It is reachable exactly the way the rest of this arm's raw shapes are: a
    duplicated operator id (#7822 — ids are not unique), one node carrying the
    token and one a number.

    The verdict is unchanged: a number is not a direction token, so it reads as
    ABSENT (``direction_flows``' both-ways default) and its BACKWARD leg refuses
    the derived target under the asserted source. RED pre-fix with
    ``TypeError`` — which is not a ``ValueError``, so ``pytest.raises`` fails.
    """
    src = lane.create_point("statement", "authored source")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    op = lane.create_operator("IMPL", src, [old], direction="->")
    # The same operator id on a SECOND node — #7822's non-unique-id shape —
    # carrying a numeric ``direction`` and its own slot edge into the prior.
    lane._get_proj().g.query(
        "MATCH (op:Point {id:$op}) "
        "CREATE (dup:Point) SET dup = properties(op) SET dup.direction = 1",
        params={"op": op["id"]})
    lane._get_proj().g.query(
        "MATCH (dup:Point {id:$op}), (old:Point {id:$old}) "
        "WHERE dup.direction = 1 CREATE (dup)-[:IMPL {idx:1}]->(old)",
        params={"op": op["id"], "old": old})
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]

    with pytest.raises(ValueError, match="re-pointing the OPERATOR"):
        lane.supersede_point(old, new)

    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"


# ── 3b. the 2a-DIRECT re-point door (#9727) ────────────

def _direct_rows(lane, pid):
    """Every operator-less ``IMPL|NAND`` edge incident to ``pid``."""
    return lane._get_proj().g.query(
        "MATCH (a)-[r:IMPL|NAND]->(b) WHERE a.id=$id OR b.id=$id "
        "RETURN a.id, type(r), coalesce(r.direction, ''), b.id "
        "ORDER BY a.id, b.id",
        params={"id": pid}).result_set


def test_supersede_repoint_refuses_a_derived_over_asserted_direct_edge(lane):
    """#9727 — the OPERATOR-LESS re-point arm, reached with NO raw write.

    Both acts are individually LEGAL, and composing them mints the edge the
    creation door refuses:

      1. ``create_direct_edge(IMPL, old, t)`` with ``old`` and ``t`` both
         derived — a same-class edge, so #2351 has nothing to refuse.
      2. ``supersede_point(old, new)`` with an AUTHORED ``new`` — legal, because
         the #2351 supersede guard only refuses a DERIVED successor replacing
         an ASSERTED predecessor, and this is the reverse.
      3. 2a-DIRECT then re-points (1)'s edge onto ``new``: the graph now holds
         ``new``(asserted) -[IMPL {bidirectional}]-> ``t``(derived), whose
         flows-back leg is the ``derived -> asserted`` override (#2351).

    The class of the endpoints was judged ONCE, at creation, on the
    PRE-SUPERSEDE pair — and the supersede replaced one of them. So the wiring
    is judged here, on the pair the graph holds AFTER the write. MEASURED on
    the pre-fix code: the supersede returned ``edges_transferred=1`` and
    ``create_direct_edge(IMPL, new, t)`` refuses that exact pair, i.e. the
    write-path invariant the module states was false for a reachable door.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    new = lane.create_point("statement", "authored v2")["id"]  # asserted
    t = lane.create_point("statement", "computed peer",
                          claim_class="derived")["id"]
    lane.create_direct_edge("IMPL", old, t)
    before = _direct_rows(lane, old)
    assert before and before[0][0] == old and before[0][3] == t, before

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)
    _msg = str(_exc.value)
    # It names the SURFACE it refused on, so the caller is not sent looking at
    # ``create_direct_edge`` for a call they never made.
    assert "re-pointing the IMPL edge" in _msg, _msg

    # Refused BEFORE any mutation (#329/#4021): both points still live, the
    # edge still on the predecessor, nothing on the successor.
    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _direct_rows(lane, old) == before
    assert _direct_rows(lane, new) == []
    assert _direct_rows(lane, t) == before

    # And the wiring the re-point would have minted is one the creation door
    # refuses — the invariant this arm was breaking.
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_direct_edge("IMPL", new, t)


def test_supersede_repoint_refuses_the_filed_shape_over_a_pre_guard_edge(lane):
    """#9727's literal reachable shape: the far endpoint is ASSERTED.

    ``old`` derived, ``new`` derived (so the existing supersede guard passes —
    it only compares new vs old), and ``old -[IMPL]-> t`` with ``t`` an
    unmarked claim — the population the module's own EP backstop describes as
    "edges that predate the guard or were bulk-written", written here through
    the raw helper because the write path refuses to create it TODAY. The
    re-point must not launder that pre-guard edge into a fresh successor.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]
    t = lane.create_point("statement", "authored target")["id"]  # asserted
    _raw_direct_edge(lane, old, t, direction="bidirectional")

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)
    assert "re-pointing the IMPL edge" in str(_exc.value)

    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _direct_rows(lane, new) == []


def test_supersede_repoint_still_carries_the_legal_class_transfers(lane):
    """The door must not over-refuse: same-class and CORROBORATION transfers.

    Three shapes, all of which the load side keeps and the re-point must still
    perform: (A) derived -> derived, (B) an asserted successor over an asserted
    far endpoint, (C) a ``<-`` edge whose only flowing leg is
    asserted -> derived, i.e. the corroboration direction #2351 deliberately
    leaves open.
    """
    # (A) derived successor, derived far endpoint — same class.
    a_old = lane.create_point("statement", "a v1", claim_class="derived")["id"]
    a_new = lane.create_point("statement", "a v2", claim_class="derived")["id"]
    a_t = lane.create_point("statement", "a peer", claim_class="derived")["id"]
    lane.create_direct_edge("IMPL", a_old, a_t)
    lane.supersede_point(a_old, a_new)
    assert _direct_rows(lane, a_old) == []
    assert _direct_rows(lane, a_new)[0][0] == a_new

    # (B) authored successor over an AUTHORED far endpoint.
    b_old = lane.create_point("statement", "b v1", claim_class="derived")["id"]
    b_new = lane.create_point("statement", "b v2")["id"]
    b_t = lane.create_point("statement", "b target")["id"]  # asserted
    _raw_direct_edge(lane, b_old, b_t, direction="bidirectional")
    lane.supersede_point(b_old, b_new)
    assert _direct_rows(lane, b_new)[0][0] == b_new

    # (C) ``<-`` from a derived predecessor onto an ASSERTED target: the only
    # leg that flows is target -> source (asserted -> derived), so the
    # successor legitimately inherits it.
    c_old = lane.create_point("statement", "c v1", claim_class="derived")["id"]
    c_new = lane.create_point("statement", "c v2")["id"]
    c_t = lane.create_point("statement", "c corroborator")["id"]
    lane.create_direct_edge("IMPL", c_old, c_t, direction="<-")
    lane.supersede_point(c_old, c_new)
    _rows = _direct_rows(lane, c_new)
    assert _rows and _rows[0][0] == c_new and _rows[0][2] == "<-", _rows


def test_supersede_repoint_refuses_the_INCOMING_edge(lane):
    """The ``in`` arm: the judged wiring is ``tid -> new``, not ``new -> tid``.

    Reachable with no raw write: ``t``(derived) -[IMPL]-> ``old``(derived) is a
    legal same-class edge, and an AUTHORED successor may replace a derived
    predecessor — so the composition mints ``t``(derived) -> ``new``(asserted),
    the override on the forward leg. The re-point's ``in`` branch swaps which
    endpoint is judged as the SOURCE; a swap that inverted would be silently
    fail-open for every incoming edge, so this pins it.
    """
    t = lane.create_point("statement", "computed corroborator",
                          claim_class="derived")["id"]
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    new = lane.create_point("statement", "authored v2")["id"]  # asserted
    lane.create_direct_edge("IMPL", t, old)
    assert _direct_rows(lane, old)[0][0] == t

    with pytest.raises(ValueError, match="DERIVED") as _exc:
        lane.supersede_point(old, new)
    _msg = str(_exc.value)
    # The message names the wiring it judged (``t -> new``), not the edge's
    # pre-supersede orientation.
    assert f"{t!r}->{new!r}" in _msg, _msg

    assert _props(lane, old).get("status") == "live"
    assert _props(lane, new).get("status") == "live"
    assert _direct_rows(lane, new) == []


def test_supersede_repoint_survives_a_far_endpoint_with_no_id(lane):
    """A far endpoint carrying NO ``id`` must not abort the supersede.

    The 2a-DIRECT traversal is unlabelled, so a raw/legacy node without an
    ``id`` property reaches the door (``ep.py``'s Batch-3 NULL guard exists for
    that shape). Pre-fix such a row simply failed the MERGE and was deleted; the
    guard must not turn it into a ``TypeError`` on a sorted set of mixed types
    — a traceback aborting a supersede the guard is not about (#10195 review
    P3's lesson, one arm over).
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]
    lane._get_proj().g.query(
        "MATCH (old:Point {id:$old}) "
        "CREATE (x:Point {content: 'no id'})-"
        "[:IMPL {direction: 'bidirectional'}]->(old)",
        params={"old": old})

    lane.supersede_point(old, new)  # must not raise

    assert _props(lane, new).get("status") == "live"
    assert _direct_rows(lane, old) == []


def test_supersede_repoint_refuses_the_backward_leg_too(lane):
    """The mirror: a legal ``->`` edge flips into the override on re-point.

    ``old``(derived) -[IMPL {->}]-> ``t``(asserted) — at CREATE time only the
    source->target leg flows, i.e. derived -> asserted, which the door refuses.
    So the guard's own verdict on the FLOWING leg is what is pinned here, on an
    edge whose far endpoint is asserted and whose successor is derived: the
    successor would stand at the source position the predecessor occupied, and
    the flowing leg would be exactly ``derived -> asserted``.
    """
    old = lane.create_point("statement", "computed v1",
                            claim_class="derived")["id"]
    new = lane.create_point("statement", "computed v2",
                            claim_class="derived")["id"]
    t = lane.create_point("statement", "authored target")["id"]
    _raw_direct_edge(lane, old, t, direction="unidirectional")

    with pytest.raises(ValueError, match="DERIVED"):
        lane.supersede_point(old, new)
    assert _direct_rows(lane, new) == []


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


def test_ep_backstop_judges_the_override_on_an_idx_less_operator(lane):
    """#10364: no ``idx`` must NOT stand the OVERRIDE guard down.

    The two guards in ``_affected_factors`` need different evidence, which is
    why they were wrongly gated together:

      * the SOURCE-SLOT guard asks *"was the idx-0 source stripped?"* — a
        verdict that is only meaningful if idx 0 IS the source, so it must
        stand down when ``idx`` is absent (#943 review);
      * the OVERRIDE guard asks a **positional** question — which leg of THIS
        factor pushes a derived claim's belief into an asserted one — and the
        positions it judges are exactly the ``input_ids`` ``_update_factor``
        reads (``receives = bwd if c == input_ids[0] else fwd``). Belief flow is
        positional, so the verdict is computable with no ``idx`` at all.

    Measured before the fix: the idx-less factor kept its stored ``->``
    unchanged (``_last_skipped_factors == []``) while the identical wiring with
    ``idx`` was dropped as ``derived_over_asserted`` — the same wiring
    propagated or not depending only on whether the edges carry ``idx``.

    The edge order is pinned by leaving ``idx`` on the target edge, so the
    derived input is provably ``input_ids[0]``; ``op_idx_known`` is still false
    because an operator is idx-unknown as soon as ANY input edge lacks ``idx``.
    """
    src = lane.create_point("statement", "computed source")["id"]
    tgt = lane.create_point("statement", "authored target")["id"]
    op = lane.create_operator("IMPL", src, [tgt], direction="->")
    # The class and the missing idx are written AFTER creation — the
    # pre-guard / bulk-written / migrated shape the backstop exists for.
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.claimClass='derived'",
        params={"id": src})
    lane._get_proj().g.query(
        "MATCH (o:Point {id:$oid})-[r:IMPL]->(c) "
        "WHERE c.id=$cid REMOVE r.idx",
        params={"oid": op["id"], "cid": src})
    ep = TortoiseEP(lane._get_proj())
    factors = ep._affected_factors({tgt}, skipped=ep._last_skipped_factors)
    reasons = [sk.get("reason") for sk in ep._last_skipped_factors]
    assert "derived_over_asserted" in reasons, (
        "an idx-less derived->asserted factor must be dropped and reported, "
        f"never propagated with its direction unchanged; got {reasons!r}"
    )
    assert all(f[0] != op["id"] for f in factors), (
        "the dropped operator must not appear in the factor list"
    )


def test_ep_backstop_narrows_rather_than_nukes_an_idx_less_factor(lane):
    """#10364: the idx-less verdict NARROWS the live leg, keeping corroboration.

    A guard that dropped every idx-less mixed-class factor would over-block the
    shape the write doors keep (an authored belief corroborating a computed
    one). On a ``<->`` factor the override leg is blocked and the reverse leg
    survives, which is exactly the behaviour the idx-full path already has.
    """
    src = lane.create_point("statement", "computed source")["id"]
    tgt = lane.create_point("statement", "authored target")["id"]
    op = lane.create_operator("IMPL", src, [tgt])  # bidirectional
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.claimClass='derived'",
        params={"id": src})
    lane._get_proj().g.query(
        "MATCH (o:Point {id:$oid})-[r:IMPL]->(c) "
        "WHERE c.id=$cid REMOVE r.idx",
        params={"oid": op["id"], "cid": src})
    factor = _factor(lane, op["id"], tgt)
    assert factor is not None, "the corroboration leg must survive"
    assert factor[5] == "<-", (
        "the derived->asserted leg must be blocked and the asserted->derived "
        f"leg kept; got {factor[5]!r} (unchanged means the guard stood down)"
    )


def _flowing_legs(factor) -> set[tuple[str, str]]:
    """The endpoint pairs a factor's RESOLVED direction lets belief flow over.

    ``_update_factor`` reads ``input_ids[0]`` as the source (``ep._messages``:
    ``receives = bwd if c == input_ids[0] else fwd``), so ``fwd`` carries
    ``input_ids[0] -> rest`` and ``bwd`` carries ``rest -> input_ids[0]``.

    A class verdict has to be read in those terms, because the SAME token names
    a DIFFERENT leg once the slot-0 endpoint changes: on an all-idx-less
    operator which endpoint that is depends on the ``c.id`` tie-break, so
    asserting on the token would assert on the tie-break instead of on the
    rule.
    """
    input_ids = factor[2]
    fwd, bwd = cc.direction_flows(factor[5])
    legs: set[tuple[str, str]] = set()
    if fwd:
        legs |= {(input_ids[0], other) for other in input_ids[1:]}
    if bwd:
        legs |= {(other, input_ids[0]) for other in input_ids[1:]}
    return legs


@pytest.mark.parametrize("derived_sorts_first", [True, False],
                         ids=["derived-id-first", "asserted-id-first"])
@pytest.mark.parametrize("edges_carry_idx", [True, False],
                         ids=["idx-present", "idx-absent"])
def test_ep_backstop_parity_between_idx_present_and_idx_absent(
        lane, derived_sorts_first, edges_carry_idx):
    """#10364 indicator (2): the verdict is the SAME verdict either way.

    The two tests above strip ``idx`` from ONE edge, so the surviving ``idx``
    still pins the order (0 < 1) and ``c.id`` never decides anything. The shape
    #10364 actually measured is the ALL-idx-less operator: every row coalesces
    to position 0 and the tie breaks lexicographically (``ORDER BY
    coalesce(r.idx, 0), c.id``), so WHICH endpoint lands in the source slot —
    and therefore which leg a given direction token names — depends on the ids.

    This is the parity row of the issue's verification checklist. The ids are
    controlled so BOTH orderings occur, and the assertion is made on the LEG
    that flows rather than on the token, because one verdict reads as ``<-`` or
    ``->`` depending on which endpoint the tie-break put in slot 0. In every
    one of the four shapes exactly one leg may flow, and it must be
    asserted -> derived: the derived claim is corroborated and never pushes
    belief INTO the authored one.
    """
    derived_id = ("aaa-derived-10364" if derived_sorts_first
                  else "zzz-derived-10364")
    asserted_id = ("zzz-asserted-10364" if derived_sorts_first
                   else "aaa-asserted-10364")
    lane.create_point("statement", "computed claim", id=derived_id)
    lane.create_point("statement", "authored claim", id=asserted_id)
    op = lane.create_operator("IMPL", derived_id, [asserted_id],
                              direction="<->")
    # The class is written AFTER creation: the write door refuses this wiring
    # outright, and the backstop exists for the shapes that predate or bypass
    # it (#2351).
    lane._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id=$id SET n.claimClass='derived'",
        params={"id": derived_id})
    if not edges_carry_idx:
        lane._get_proj().g.query(
            "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->() REMOVE r.idx",
            params={"oid": op["id"]})
    ep = TortoiseEP(lane._get_proj())
    factors = ep._affected_factors({derived_id, asserted_id},
                                   skipped=ep._last_skipped_factors)
    mine = [f for f in factors if f[0] == op["id"]]
    assert len(mine) == 1, (
        "the corroboration leg must keep the factor alive; "
        f"got {mine!r} (skipped={ep._last_skipped_factors!r})")
    if not edges_carry_idx:
        # The point of this shape: with NO idx anywhere, slot 0 is decided by
        # the `c.id` tie-break — pin that, so the case cannot silently stop
        # exercising the ordering it exists for.
        assert mine[0][2][0] == min(derived_id, asserted_id), (
            "the all-idx-less shape is supposed to exercise the `c.id` "
            f"tie-break; slot 0 was {mine[0][2][0]!r}")
    assert _flowing_legs(mine[0]) == {(asserted_id, derived_id)}, (
        "the only leg that may flow is asserted -> derived: a bidirectional "
        "verdict means the override guard stood down, and a derived -> "
        f"asserted leg is the hole #10364 measures; got "
        f"{sorted(_flowing_legs(mine[0]))!r} for direction {mine[0][5]!r}")


def test_ep_source_slot_guard_still_stands_down_on_an_idx_less_operator(lane):
    """#10364 checklist row 3 / #943: the OTHER guard must stay gated.

    The source-slot guard asks *"was the idx-0 SOURCE stripped?"* — a verdict
    that is only meaningful when idx 0 IS provably the source, because
    renumbering a live target into the source slot inverts directional
    semantics. With no ``idx`` it must stand down; this change leaves it gated.

    Pinning the stand-down is not decoration: this PR changed the gating of the
    guard immediately beside it, so "make the two consistent" is the natural
    way in which #943's inversion could be re-introduced.

    Two shapes identical but for ``idx``: with ``idx`` the stripped draft source
    is detected and the factor is dropped as ``source_stripped``; without it the
    guard stands down and emits no such record (the factor is kept, with a live
    target renumbered into slot 0 — the renumbering the guard could not rule
    out).
    """
    def _wire(suffix: str, *, keep_idx: bool) -> tuple[str, str, str]:
        draft_src = lane.create_point(
            "statement", "draft source", status="draft",
            id=f"aaa-draft-source-{suffix}")["id"]
        t1 = lane.create_point("statement", "target one",
                               id=f"mmm-target-{suffix}")["id"]
        t2 = lane.create_point("statement", "target two",
                               id=f"zzz-target-{suffix}")["id"]
        op = lane.create_operator("IMPL", draft_src, [t1, t2],
                                  direction="->", promote_source=False)
        # The operator starts draft too — stand in for the #785 promote flow
        # making the OPERATOR live while its source stays draft.
        lane._get_proj().g.query(
            "MATCH (o:Point {id:$id}) SET o.status='live'",
            params={"id": op["id"]})
        if not keep_idx:
            lane._get_proj().g.query(
                "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->() REMOVE r.idx",
                params={"oid": op["id"]})
        return op["id"], t1, t2

    with_idx, t1, t2 = _wire("idx", keep_idx=True)
    ep = TortoiseEP(lane._get_proj())
    skipped: list[dict] = []
    factors = ep._affected_factors({t1, t2}, skipped=skipped)
    assert all(f[0] != with_idx for f in factors)
    assert [s["reason"] for s in skipped if s["operator_id"] == with_idx] == [
        "source_stripped"], "control: with idx the source slot IS provable"

    no_idx, t3, t4 = _wire("noidx", keep_idx=False)
    ep2 = TortoiseEP(lane._get_proj())
    skipped2: list[dict] = []
    factors2 = ep2._affected_factors({t3, t4}, skipped=skipped2)
    assert "source_stripped" not in [
        s["reason"] for s in skipped2 if s["operator_id"] == no_idx], (
        "the source-slot guard fired on an idx-less operator — the #943 "
        "inversion: position 0 is not provably the source, so it must stand "
        "down rather than judge a slot it cannot name")
    assert any(f[0] == no_idx for f in factors2), (
        "standing down means the factor is KEPT (it is the judge that is "
        "disabled, not the factor); a drop here would mean some other arm "
        f"removed it: skipped={skipped2!r}")


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
