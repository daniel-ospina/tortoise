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
         predecessor, BEFORE any mutation.
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
    """The class belongs to the WRITE THAT MINTS — a re-run must not flip it."""
    first = lane.create_point("statement", "same words", claim_class="derived")
    again = lane.create_point("statement", "same words", dedup=True)
    assert again["id"] == first["id"]
    assert _props(lane, first["id"]).get(cc.PROP) == "derived"


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
    """Asserted→derived flows, derived→derived flows, asserted→asserted flows."""
    for src_class, tgt_class in (("asserted", "derived"),
                                 ("derived", "derived"),
                                 ("asserted", "asserted")):
        src, tgt = _two(lane, src_class=src_class, tgt_class=tgt_class)
        lane.create_direct_edge("IMPL", src, tgt)   # must not raise
        assert _factor(lane, src, tgt) is not None


def test_create_operator_refuses_derived_source_over_asserted(lane):
    src, tgt = _two(lane, src_class="derived", tgt_class="asserted")
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("IMPL", src, [tgt])
    with pytest.raises(ValueError, match="DERIVED"):
        lane.create_operator("NAND", src, [tgt])
    # An asserted source over a derived input is fine (corroboration).
    a_src, d_tgt = _two(lane, src_class="asserted", tgt_class="derived")
    lane.create_operator("IMPL", a_src, [d_tgt])


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
    derived = lane.create_point("statement", "derived input",
                                claim_class="derived")["id"]
    other = lane.create_point("statement", "second asserted input")["id"]
    op = lane.create_operator("IMPL", authored, [derived, other])
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


def test_read_surface_is_a_param_not_a_new_tool():
    """Indicator 3 is +0 MCP tools: the seam is a kwarg on an existing read."""
    assert "claim_class" in inspect.signature(
        TortoiseSDK.tortoise_fts_query).parameters
    assert "claim_class" in inspect.signature(
        TortoiseSDK.create_point).parameters
