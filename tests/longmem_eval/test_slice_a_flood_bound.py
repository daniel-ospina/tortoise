"""#8043 guard — the re-specified Slice A gate's structural leg is measured,
pinned, and cannot be mis-read as an end-to-end result.

Companion to ``tools/longmem_eval/slice_a_flood_bound.py`` (the instrument) and
``docs/runbook/8043-slice-a-t-gate.md`` (the gate record). HERMETIC: no graph,
no model, no IO — the instrument drives the SHIPPED product functions
(``tortoise.retrieval.assemble_context`` / ``package_evidence_pool`` /
``render_context``).

Two independent jobs:

1. **Receipt reproducibility** — the committed receipt
   (``docs/runbook/8043-slice-a-t-gate-receipt.json``) is a function of the
   shipped code, so regenerating it must produce a byte-identical file. If a
   refactor moves the window, the test reds on the diff rather than letting a
   stale number stand as a measurement (#5064's design-generated-from-registry
   shape, applied to this receipt).

2. **The gate's invariants** — stated on the instrument output directly, so a
   receipt regenerated to a wrong value cannot pass by agreeing with itself:
     * the pre-#2978 arm reproduces the expired baseline's shape (content-less
       slots > 0) — the reason the old T is not re-runnable;
     * the shipped predicate alone still lets ONE fact take the whole window
       (so #2978 did not bound the flood);
     * the assembly bounds the flood to <= 1 + max_verbatim and no
       value-critical claim is lost;
     * a provenance-free pool does NOT get that bound (the measured #8658
       scope limit — still the shape of a DEGRADED fallback read);
     * the duplicate-free control comes back byte-identical (no regression);
     * the receipt names NO reader and NO judge — it can never be quoted as
       the old T's end-to-end measurement.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.longmem_eval import slice_a_flood_bound as instrument  # noqa: E402
from tortoise.retrieval import DEFAULT_PACKAGE_MAX_VERBATIM  # noqa: E402

RECEIPT = ROOT / "docs" / "runbook" / "8043-slice-a-t-gate-receipt.json"


@pytest.fixture(scope="module")
def receipt() -> dict:
    return instrument.measure()


# ── 1. the receipt is a reproducible function of the shipped code ─────────


def test_instrument_receipt_reproduces_byte_identically() -> None:
    """Regenerating the receipt produces NO diff — the committed numbers are
    the shipped code's output, not a hand-edited claim."""
    assert RECEIPT.exists(), f"missing receipt: {RECEIPT}"
    assert instrument.render_receipt() == RECEIPT.read_text(encoding="utf-8"), (
        "the Slice A flooding-bound receipt drifted from the instrument — "
        "re-run `.venv/bin/python -m tools.longmem_eval.slice_a_flood_bound` "
        "and commit the regenerated receipt"
    )


def test_receipt_matches_measure_directly(receipt: dict) -> None:
    """`--check` and the test read the SAME bytes (no writer/reader skew)."""
    committed = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert committed == json.loads(instrument.render_receipt(receipt))


# ── 2. the honesty guard: never read this as the old T ───────────────────


def test_receipt_is_not_an_end_to_end_result(receipt: dict) -> None:
    """The instrument measures WINDOW COMPOSITION. It names no reader and no
    judge, and says why — so it cannot be quoted as the old T's A/B (whose
    reader/judge froze wave §3 and whose baseline expired with #2978)."""
    assert receipt["reader"] is None
    assert receipt["judge"] is None
    assert "NOT run" in receipt["why_no_reader"]
    assert "no answer quality is measured" in receipt["instrument"]["caveat"]
    for arm in receipt["arms"].values():
        assert "reader" not in arm and "accuracy" not in arm


# ── 3. the gate's invariants, stated on the measurement itself ───────────


def test_pre_2978_arm_reproduces_the_expired_baselines_shape(receipt: dict) -> None:
    """The pre-#2978 predicate on the C2 shape admits content-less operator
    slots and leaves the value claims out — the window the old T measured
    against. Its expiry is the reason T-as-written cannot be re-run."""
    pre = receipt["arms"]["pre_2978"]
    assert pre["admission_predicate"].startswith("pre-#2978")
    assert pre["contentless_slots"] > 0
    assert pre["contentless_slot_share"] >= 0.5
    assert pre["flood_slots"] > 1, "the flood must occupy many slots pre-fix"
    assert pre["value_claims_present"] == []


def test_2978_alone_does_not_bound_the_flood(receipt: dict) -> None:
    """#2978 skipped the empty hits — it did NOT bound the duplicate flood.

    The freed slots are reallocated to the highest-ranked REAL hits, which on
    a coverage-loop flood are the duplicates: the shipped predicate alone
    fills the whole window with ONE fact (the #2683 defect, undiminished)."""
    post = receipt["arms"]["post_2978"]
    assert post["contentless_slots"] == 0
    assert post["admitted"] == instrument.WINDOW_ITEM_CAP
    assert post["flood_slots"] == post["admitted"]
    assert post["flood_slot_share"] == 1.0
    assert post["value_claims_present"] == []


def test_assembly_bounds_one_fact_to_one_plus_max_verbatim(receipt: dict) -> None:
    """The A6 arm holds a fact to <= 1 + max_verbatim slots, and the freed
    slots go to DISTINCT evidence — including every value-critical claim."""
    arm = receipt["arms"]["post_2978_assembly"]
    assert arm["assembly"] is True
    assert arm["flood_slots"] <= 1 + DEFAULT_PACKAGE_MAX_VERBATIM
    assert arm["flood_slot_share"] < 0.5
    assert arm["admitted"] < receipt["arms"]["post_2978"]["admitted"]
    assert arm["value_slots"] == 4
    assert len(arm["value_claims_present"]) == 4
    assert arm["other_slots"] == 8
    assert arm["package_stats"]["packages"] < receipt["pool_census"]["pool_items"]


def test_value_critical_claims_survive_the_collapse(receipt: dict) -> None:
    """300/400 (tea set) and 12/60 (mugs) never merge, and both pairs are in
    the window once the flood is bounded.

    On THIS fixture the refusal is structural, not the value guard: measured
    normalised overlap is 0.80 and 0.60 against a 0.90 collapse threshold and
    the four turns are distinct, so no value pair is a collapse candidate on
    its own account. The guard IS still consulted, and this test pins that to
    the COUNTERS the instrument records — not to words in a sentence. The
    difference is not academic: an earlier revision pinned the substring
    "not exercised here" and was false; its replacement pinned "still
    consulted" and would have survived deleting the same-turn call site (a real
    regression: with it gone, 300 and 400 in one turn collapse). A claim about
    a mechanism has to move when the mechanism moves, so the assertions below
    read `guard_calls` / `guard_refusals` / `disabling_guard_changes_output`
    and check the `why` reproduces them.

    Neither "the guard caused this" nor "the guard never runs" is true here.
    """
    arm = receipt["arms"]["post_2978_assembly"]
    assert set(arm["value_claims_present"]) == {"v1", "v2", "v3", "v4"}
    assert arm["value_slots"] == 4
    verdict = receipt["verdicts"]["value_safety"]
    assert "assembly=4/4" in verdict["measured"]
    # ── the leading clause is measured too, not written ─────────────────────
    # #8945: TWO quantities, each beside the threshold it actually answers to.
    # The old receipt published the QUOTE overlap under the label "normalised
    # overlap" and compared it in prose to the CONTENT threshold (0.90), so
    # the one number a reader could check answered a different question than
    # the sentence asked. The 300/400 quote overlap is 0.80 — ABOVE the 0.75
    # same-turn threshold — which the single mis-labelled figure concealed.
    pairs_v = verdict["value_pairs"]
    assert pairs_v == [
        {"a": "v1", "b": "v2", "values": "300/400",
         "content_overlap": 0.8, "content_below_threshold": True,
         "content_leg_reachable": False,
         "quote_overlap": 0.8, "quote_below_threshold": False,
         "same_turn_leg_reachable": False,
         "turns": ["t1", "t2"], "distinct_turns": True},
        {"a": "v3", "b": "v4", "values": "12/60",
         "content_overlap": 0.6, "content_below_threshold": True,
         "content_leg_reachable": False,
         "quote_overlap": 0.6, "quote_below_threshold": True,
         "same_turn_leg_reachable": False,
         "turns": ["t3", "t4"], "distinct_turns": True},
    ], pairs_v
    assert verdict["collapse_threshold"] == 0.9
    assert verdict["same_turn_threshold"] == 0.75
    for p in pairs_v:
        # each comparison is DERIVED from the number it names (#8945)
        assert p["content_below_threshold"] is (
            p["content_overlap"] < verdict["collapse_threshold"]), p
        assert p["quote_below_threshold"] is (
            p["quote_overlap"] < verdict["same_turn_threshold"]), p
        assert p["content_leg_reachable"] is not p["content_below_threshold"], p
        assert p["same_turn_leg_reachable"] is (
            not p["quote_below_threshold"] and not p["distinct_turns"]), p
        assert p["distinct_turns"] is True, p
        assert f"content overlap {p['content_overlap']:.2f}" in verdict["why"], verdict["why"]
        assert f"quote overlap {p['quote_overlap']:.2f}" in verdict["why"], verdict["why"]
        assert f"turns {p['turns'][0]}/{p['turns'][1]}" in verdict["why"], verdict["why"]
    assert f"{verdict['collapse_threshold']:.2f} near-verbatim threshold" in verdict["why"]
    assert f"{verdict['same_turn_threshold']:.2f} same-turn threshold" in verdict["why"]
    # the tail is conditional on the measurement, never fixed prose
    assert all(p["distinct_turns"] for p in pairs_v)
    assert "carries distinct turns" in verdict["why"], verdict["why"]
    assert "NOT all on distinct turns" not in verdict["why"], verdict["why"]

    # ── the guard is CONSULTED: measured, per arm, in the receipt ────────────
    calls = verdict["guard_calls"]
    refusals = verdict["guard_refusals"]
    assert calls == {
        "post_2978_assembly": 40,
        "post_2978_assembly_value_marked": 40,
        "provenance_free_assembly": 39,
    }, calls
    assert refusals == {
        "post_2978_assembly": 1,
        "post_2978_assembly_value_marked": 1,
        "provenance_free_assembly": 0,
    }, refusals
    assert verdict["guard_total_calls"] == sum(calls.values()) == 119
    assert verdict["guard_total_refusals"] == sum(refusals.values()) == 2

    # The two refusals are ONE incidental same-turn pair per provenance-bearing
    # arm. The provenance-free arm refuses NOTHING because its same-turn leg is
    # inert (no `source_turn_id`) — NOT because it calls the guard less: it still
    # makes its 39 pass-1 content-leg calls, all accepted. Both recorded pairs
    # carry the same source turn and disjoint quotes, which is why a refusal
    # decides nothing.
    pairs = verdict["refused_pairs"]
    assert len(pairs) == 2, pairs
    assert {p["arm"] for p in pairs} == {
        "post_2978_assembly",
        "post_2978_assembly_value_marked",
    }
    assert {p["a"] for p in pairs} == {"the tea set cost 300 dollars"}
    assert {p["b"] for p in pairs} == {"the cat sheds a lot"}
    assert all(p["same_source_turn"] is True for p in pairs)
    assert all(p["quote_overlap"] == 0.0 for p in pairs)

    # ── and it determines NOTHING: the counterfactual is measured too ────────
    assert verdict["disabling_guard_changes_output"] is False

    # ── the sentence is composed FROM those numbers, so it cannot drift ──────
    assert f"{verdict['guard_total_calls']} calls" in verdict["why"], verdict["why"]
    assert f"{verdict['guard_total_refusals']} of them refusals" in verdict["why"], verdict["why"]
    assert "leaves this arm's ids/order unchanged" in verdict["why"], verdict["why"]


def test_value_pairs_sentence_is_composed_not_fixed() -> None:
    """#8945 acceptance: a pair FORCED above a threshold must not produce the
    "clear both collapse legs" wording, and the field the sentence names must
    be the field the comparison used.

    This feeds the composer directly with synthetic pairs, so it fails even if
    the fixture that ``measure()`` builds never happens to produce an
    above-threshold pair again. That is the whole defect: the old generator
    emitted the "sit below 0.90" clause UNCONDITIONALLY, so no fixture could
    ever have falsified it.
    """
    def _pair(**over: object) -> dict:
        base = {
            "a": "x1", "b": "x2", "values": "300/400",
            "content_overlap": 0.5, "content_below_threshold": True,
            "content_leg_reachable": False,
            "quote_overlap": 0.5, "quote_below_threshold": True,
            "same_turn_leg_reachable": False,
            "turns": ["t1", "t2"], "distinct_turns": True,
        }
        base.update(over)
        return base

    below = instrument._value_pairs_sentence([_pair()])
    assert "clear both collapse legs on their own terms" in below, below
    assert "no value pair is a collapse candidate" in below, below

    # (1) content overlap forced ABOVE the near-verbatim threshold
    above_content = instrument._value_pairs_sentence(
        [_pair(content_overlap=0.99, content_below_threshold=False,
               content_leg_reachable=True)]
    )
    assert "clear both collapse legs on their own terms" not in above_content, above_content
    assert "do not clear both collapse legs" in above_content, above_content
    assert "reaches at least one leg's ratio" in above_content, above_content
    assert "300/400" in above_content, above_content
    # and the number it names is the number it was given
    assert "content overlap 0.99" in above_content, above_content

    # (2) quote overlap above the same-turn threshold but on DISTINCT turns is
    # NOT a reachable leg — the precondition is checked, not just the ratio
    above_quote_unreachable = instrument._value_pairs_sentence(
        [_pair(quote_overlap=0.99, quote_below_threshold=False)]
    )
    assert "no value pair is a collapse candidate" in above_quote_unreachable
    assert "unreachable" in above_quote_unreachable, above_quote_unreachable

    # (3) same pair, now sharing a turn — the leg IS reachable and the
    # sentence must say so rather than repeating (2)
    above_quote_reachable = instrument._value_pairs_sentence(
        [_pair(quote_overlap=0.99, quote_below_threshold=False,
               same_turn_leg_reachable=True, distinct_turns=False)]
    )
    assert "no value pair is a collapse candidate" not in above_quote_reachable
    assert "reaches at least one leg's ratio" in above_quote_reachable
    assert "SAME TURN" in above_quote_reachable, above_quote_reachable


def test_refused_pair_overlap_is_measured_not_silently_null(receipt: dict) -> None:
    """#8945 item 3: the ``hasattr`` guard degraded a rename to a plausible
    ``null``. The field is now a direct call, so it is always a number — and
    the source carries no guard that could hide a rename."""
    pairs = receipt["verdicts"]["value_safety"]["refused_pairs"]
    assert pairs, "precondition: the arms refuse at least one pair"
    for p in pairs:
        assert isinstance(p["quote_overlap"], float), p
        assert p["quote_overlap"] is not None, p
    source = (ROOT / "tools" / "longmem_eval" / "slice_a_flood_bound.py").read_text()
    assert 'hasattr(retrieval, "_pkg_overlap")' not in source, (
        "the hasattr guard is back — it turns a rename into a plausible null "
        "instead of an error (#8945)"
    )


def test_no_dupe_control_observes_the_tier_boundary(receipt: dict) -> None:
    """#9114: the single-tier control cannot see the D3 regroup, so the receipt
    carries a second control that CAN — a raw chunk ranked above the
    statements, which under D3 is demoted behind every statement by design."""
    single = receipt["controls"]["no_dupe_no_regression"]
    assert single["ids_identical"] is True
    assert "SINGLE-TIER" in single["scope"], single["scope"]

    boundary = receipt["controls"]["no_dupe_tier_boundary"]
    assert boundary["collapsed_duplicates"] == 0
    assert boundary["package_items"] == boundary["pool_items"]
    # the fixture really does rank the chunk above the statements
    assert boundary["pool_order"][0] == "mc0", boundary["pool_order"]
    assert boundary["pool_tiers"][0] == 2, boundary["pool_tiers"]
    assert set(boundary["pool_tiers"][1:]) == {0}, boundary["pool_tiers"]
    # …and the arm demotes it: byte-identity is FALSE by design here
    assert boundary["ids_identical"] is False, boundary
    assert boundary["package_order"][-1] == "mc0", boundary["package_order"]
    assert boundary["package_tiers"] == sorted(boundary["package_tiers"]), boundary
    # the tier-scoped assertions, which the pool CAN falsify
    assert boundary["tier_key_order"] == boundary["package_order"], boundary
    assert boundary["tier_regroup_matches_key"] is True, boundary
    assert boundary["ids_identical_within_tier"] is True, boundary

def test_tier_control_fails_when_the_tier_key_leaves_the_sort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#9114 acceptance, EXECUTED: remove ``_package_type_tier`` from the SORT
    KEY and the control goes False.

    Patched at ``_package_tier`` (the sort key), NOT at
    ``_package_type_tier`` (the tier function). Breaking the function would
    degenerate BOTH sides — the expected projection and the observed order
    would each collapse to rank order — and the control would pass while the
    mechanism was broken. Breaking only the key is what the criterion means,
    and it is the regression that could actually ship.
    """
    before = instrument.measure()["controls"]["no_dupe_tier_boundary"]
    assert before["tier_regroup_matches_key"] is True, before

    def _rank_only(h: dict, mark_for: object = None) -> tuple[int, int, int]:
        return (0, 0, h.get("_pkg_rank", 0))

    monkeypatch.setattr(instrument.retrieval, "_package_tier", _rank_only)
    after = instrument.measure()["controls"]["no_dupe_tier_boundary"]
    assert after["tier_regroup_matches_key"] is False, after
    # the chunk stayed at rank 0, so the pool came back untouched
    assert after["ids_identical"] is True, after


def test_marked_arm_carries_the_value_class_census(receipt: dict) -> None:
    """With the eval's read-time value marks, 4 packages carry the #1763/#1945
    value class — a MARK CENSUS, not an ordering fact.

    Renamed from ``test_marked_arm_orders_the_value_claims_first`` (#9092):
    under D3 the hit TYPE picks the package tier and the mark only breaks ties
    INSIDE a tier, and this receipt is order-blind by construction
    (``_slot_census`` records counts + text containment, never an id order).
    So this test cannot measure the ordering contract its old name asserted;
    the ordering is pinned hermetically in
    ``tests/test_evidence_assembly.py`` (tier key + the unmarked regroup).
    """
    marked = receipt["arms"]["post_2978_assembly_value_marked"]
    assert marked["package_stats"]["value_first_packages"] == 4
    plain = receipt["arms"]["post_2978_assembly"]
    assert plain["package_stats"]["value_first_packages"] == 0
    # the admission SET is the same either way (ordering, never membership)
    assert marked["admitted"] == plain["admitted"]
    assert marked["by_fact"] == plain["by_fact"]


def test_provenance_free_pool_does_not_get_the_bound(receipt: dict) -> None:
    """The measured scope limit (#8658): pass 1's content legs still dedup the
    STATEMENT flood, but pass 2 — a point's OWN source chunks/turns — is inert
    without ``quote``/``source_turn_id``, so the same pool costs 12 flood slots
    instead of 2. That was the product ask-lane shape until #8658 landed (the
    read path now carries the provenance); the arm stays live because a
    DEGRADED fallback read still has no provenance."""
    bare = receipt["arms"]["provenance_free_assembly"]
    assert bare["package_stats"]["verbatim_refs_kept"] == 0
    # pass 1 is provenance-independent, so it still collapses the STATEMENT
    # flood — 91 of 139 here. Pinning it stops the docstring drifting back to
    # "an untouched package", which the receipt falsifies.
    stats = bare["package_stats"]
    assert stats["package_items"] < stats["pool_items"]
    assert stats["collapsed_duplicates"] == stats["pool_items"] - stats["package_items"]
    assert bare["flood_slots"] > receipt["arms"]["post_2978_assembly"]["flood_slots"]
    verdict = receipt["verdicts"]["provenance_leg_is_lane_scoped"]
    assert verdict["measured"], "the scope limit must be MEASURED, not asserted"
    assert "INERT" in verdict["why"], verdict["why"]


def test_duplicate_free_control_is_byte_identical(receipt: dict) -> None:
    """OFF-parity / no-regression: on a duplicate-free, provenance-bearing
    pool the arm returns the input unchanged (same ids, same order), so
    flipping it ON cannot regress a single-evidence question."""
    control = receipt["controls"]["no_dupe_no_regression"]
    assert control["collapsed_duplicates"] == 0
    assert control["package_items"] == control["pool_items"]
    assert control["ids_identical"] is True


def test_pool_is_the_documented_c2_shape(receipt: dict) -> None:
    """The fixture's own provenance: §1's "79 merged points" and the
    content-less class are IN the pool, so the arms above measure the shape
    the wave recorded rather than an arbitrary list."""
    census = receipt["pool_census"]
    assert census["by_fact"]["flood"] >= 79
    assert census["contentless_hits"] == 24
    assert census["by_fact"]["value"] == 4
    assert census["by_fact"]["other"] == 8


#: Tokens that LOOK like an arm key. Anchored on the three arm families rather
#: than a generic ``[a-z_]+`` sweep: a generic sweep flags ordinary identifiers
#: (``build_c2_pool``) and the guard would be silenced instead of obeyed.
_ARMISH = re.compile(
    # `[a-z0-9_]+`, not `[a-z_]+`: a suffix may carry a digit, and a
    # digit-bearing token (`…_assembly_v2`) would otherwise slip past this
    # guard without a trailing word boundary.
    r"\b(?:pre_2978(?:_[a-z0-9_]+)?|post_2978(?:_[a-z0-9_]+)?|provenance_free(?:_[a-z0-9_]+)?)\b"
)


def test_every_arm_name_the_prose_cites_is_a_receipt_key(receipt: dict) -> None:
    """Two review cycles of #8043 failed on THIS defect, so it is pinned.

    A docstring called the provenance-free arm ``provenance_free`` and another
    called the value-marked arm ``post_2978_assembly_marked``; neither is a
    name the receipt records (``provenance_free_assembly`` and
    ``post_2978_assembly_value_marked`` are), so a reader following the prose
    to the receipt found nothing. Nothing parsed those names, so both passed
    CI silently. This is the doc-side companion of the receipt-reproducibility
    pin above: that one stops a NUMBER drifting from the code, this one stops a
    NAME drifting from the receipt.

    SCOPE: the instrument and the gate record — the two artifacts a reader is
    sent to for a number. The bare ``pre_2978`` / ``post_2978`` forms are
    exempt: they are prose adjectives ("the pre-#2978 predicate") as often as
    keys, so only a token carrying a SUFFIX claims to be a name. A cited token
    must be a key ANYWHERE in the receipt, not only under ``arms`` —
    ``pre_2978_arm`` is a real key under ``instrument``.
    """
    arms = set(receipt["arms"])
    assert arms, "precondition: the receipt records arms"

    def all_keys(node: object) -> set[str]:
        if isinstance(node, dict):
            out: set[str] = set()
            for key, value in node.items():
                out.add(key)
                out |= all_keys(value)
            return out
        if isinstance(node, list):
            out = set()
            for item in node:
                out |= all_keys(item)
            return out
        return set()

    known = all_keys(receipt)
    missing: dict[str, list[str]] = {}
    for path in (
        ROOT / "tools" / "longmem_eval" / "slice_a_flood_bound.py",
        ROOT / "docs" / "runbook" / "8043-slice-a-t-gate.md",
    ):
        cited = {tok for tok in _ARMISH.findall(path.read_text()) if tok not in known}
        cited -= {"pre_2978", "post_2978"}
        if cited:
            missing[str(path.relative_to(ROOT))] = sorted(cited)

    assert not missing, (
        "prose cites name(s) the receipt records nowhere: "
        f"{missing} — the receipt's arms are {sorted(arms)}"
    )
