#!/usr/bin/env python3
"""#8503 — the MUTATION ARM: does the gate FAIL when a mechanism regresses?

A number that cannot go down pins nothing. Each check below takes a mechanism
this PR fixed, reverts it IN MEMORY (never on disk — the tree is the artifact
under review), and asserts that the pin which protects it FAILS.

Section 1 calls the REAL pin functions from ``tests/test_extractor_v2.py`` and
``tests/eval/write_path/test_write_path_runner.py`` under the mutation — a
first cut of this script only re-read the mutated constant and called that
"detected", which is true by construction and would have passed with every pin
deleted (code-review finding, #8503).

Print-only: exit 0 means every mutation was DETECTED by its own pin, exit 1
means at least one slipped past — a hole in the gate, not in the mechanism.

Run:
    PYTHONPATH=$PWD .venv/bin/python docs/evidence/8503/mutation_arm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tortoise.extractor_v2 as v2  # noqa: E402
from tests.eval.write_path import runner  # noqa: E402

FAILURES: list[str] = []


def _expect_detected(pin_fails: bool, label: str, detail: str = "") -> None:
    status = "DETECTED" if pin_fails else "MISSED  "
    print(f"  [{status}] {label}")
    if not pin_fails:
        FAILURES.append(label)
    if detail:
        for line in detail.strip().splitlines():
            print(f"             {line}")


def _pin_fails(fn, *args, **kwargs) -> bool:
    """True when the pin RAISES under the mutation — i.e. it caught it."""
    try:
        fn(*args, **kwargs)
    except AssertionError:
        return True
    except Exception as exc:  # noqa: BLE001 - any raise is still a detection
        print(f"             (pin raised {type(exc).__name__}: {exc})")
        return True
    return False


# ── 1. the SELECTION rules — the REAL pins, detonated ──────────────────────
def check_selection_rules() -> None:
    print("\n1. operator-selection rules (the real pins run under mutation)")
    from tests.test_extractor_v2 import TestOperatorSelectionRules as Pins

    pins = Pins()
    cases = [
        ("S2_TMPL", "the SPEAKER is not a filter on CLAIMS",
         pins.test_assistant_asserted_claims_are_licensed_not_excluded,
         "assistant-asserted claims are licensed"),
        ("S2_TMPL", "A QUESTION THAT ASSERTS A CLAIM CARRIES THAT CLAIM",
         pins.test_assistant_asserted_claims_are_licensed_not_excluded,
         "an asserting question carries its claim"),
        ("S2_TMPL", "An operational OUTCOME that EVIDENCES a claim",
         pins.test_operational_outcome_that_evidences_a_claim_is_durable,
         "an operational outcome is a durable claim"),
    ]
    for const, anchor, pin, label in cases:
        original = getattr(v2, const)
        try:
            assert anchor in original, f"mutation anchor missing: {anchor!r}"
            setattr(v2, const, original.replace(anchor, "", 1))
            caught = _pin_fails(pin)
        finally:
            setattr(v2, const, original)
        _expect_detected(caught, f"{const}: {label}")

    shared_cases = [
        ("A THEORY THE CONVERSATION DISPLACES",
         pins.test_displaced_theory_is_a_nand_attacker_never_an_impl,
         "a displaced theory is a NAND attacker (never IMPL)"),
        ("An ENTITY-level supersede is NOT this channel",
         pins.test_entity_supersede_is_not_the_reversal_channel,
         "an entity supersede writes no reversal edge"),
        ("The SRC of a MITIGATES is ANY point",
         pins.test_one_mitigates_per_action_pair,
         "every warrant point is a src"),
    ]
    for anchor, pin, label in shared_cases:
        original = v2.OPERATOR_SEMANTICS_RULE
        try:
            assert anchor in original, f"mutation anchor missing: {anchor!r}"
            v2.OPERATOR_SEMANTICS_RULE = original.replace(anchor, "", 1)
            caught = _pin_fails(pin)
        finally:
            v2.OPERATOR_SEMANTICS_RULE = original
        _expect_detected(caught, f"shared rule: {label}")


# ── 2. the SUPERSEDE recall leg (the measured drop) ────────────────────────
# ARTIFACTS, not invented values:
#   * the REF is the one wp07 actually emitted, quoted from the #8503 run
#     receipt's extraction_warnings — "point supersedes='We ship the lease fix
#     all-at-once behind a single global kil' matches no S3 prior" — the
#     pre-fix write path dropping the CORRECTS.
#   * the PRIOR is the wp06 planted endpoint's emitted point.
def check_supersede_band() -> None:
    print("\n2. SUPERSEDE recall leg (the drop the receipt measured)")
    from tests.test_extractor_v2 import TestOperatorSemantics2552 as Pins

    ref = "We ship the lease fix all-at-once behind a single global kill flag"
    prior = ("Ship the lease fix all-at-once behind a single global kill flag "
             "rather than a week of per-shard canary")
    search = {"entities": [], "events": [],
              "points": [{"id": "pt_wp06_decision", "content": prior,
                          "kind": "statement"}]}
    embed = {"entities": [], "events": [],
             "points": [{"content": "roll out per-service flags for the next "
                                    "rollout instead of one global kill flag",
                          "pointKind": "statement",
                          "supersedes": ref}],
             "operators": []}

    shipped = v2.execute_embed(embed, search, session_id="s1")
    formed = [s for s in shipped["supersessions"]
              if s["superseded"] == "pt_wp06_decision"]
    _expect_detected(bool(formed), "leg ON: the drifted ref forms the CORRECTS",
                     f"supersessions={shipped['supersessions']}")

    original = v2._supersede_band_candidates
    try:
        v2._supersede_band_candidates = lambda *_a, **_k: []  # the pre-#8503 code
        mutated = v2.execute_embed(embed, search, session_id="s2")
    finally:
        v2._supersede_band_candidates = original
    _expect_detected(
        not [s for s in mutated["supersessions"]
             if s["superseded"] == "pt_wp06_decision"],
        "leg OFF: the same ref is DROPPED again (the mutation is real)",
        "and the drop is named: " + "; ".join(
            w for w in mutated["warnings"] if "supersedes" in w)[:160])

    # and the SAFETY direction: the pin must FAIL when the leg starts folding
    # a substitution (a different entity / number / negation / role order).
    v2._supersede_band_candidates = lambda ref_, pts: list(pts)  # folds anything
    try:
        caught = _pin_fails(
            Pins().test_supersede_ref_drift_resolves_through_the_recall_band)
    finally:
        v2._supersede_band_candidates = original
    _expect_detected(caught,
                     "a leg that folds EVERY prior is caught by the same pin")

    # the SHAPE regression review round 2 asked for: put the coverage ratio
    # back in place of the contiguous-span predicate. The negative pin
    # must red — otherwise the predicate is pinned only by prose.
    from tests.test_extractor_v2 import TestOperatorSemantics2552 as SafetyPin
    original_contains = v2._tokens_contain_contiguous
    try:
        def _ratio(ref_tokens, prior_tokens, _min=0.85):
            ref_set, prior_set = set(ref_tokens), set(prior_tokens)
            if not ref_set:
                return False
            return len(ref_set & prior_set) / len(ref_set) >= _min
        v2._tokens_contain_contiguous = _ratio
        caught = _pin_fails(
            SafetyPin().test_supersede_band_refuses_every_substitution)
    finally:
        v2._tokens_contain_contiguous = original_contains
    _expect_detected(caught,
                     "restoring the COVERAGE RATIO is caught by the negative "
                     "pin (the predicate is pinned, not just described)")

    # and the OTHER shape regression: undo the contiguity tightening, back to
    # the ordered-subsequence test the leg shipped for one commit. Nothing but
    # the contiguity discriminator distinguishes them, so if this is not
    # DETECTED the tightening is unpinned.
    def _ordered(ref_tokens, prior_tokens):
        if not ref_tokens:
            return False
        remaining = iter(prior_tokens)
        return all(tok in remaining for tok in ref_tokens)
    try:
        v2._tokens_contain_contiguous = _ordered
        caught = _pin_fails(
            SafetyPin().test_supersede_band_refuses_every_substitution)
    finally:
        v2._tokens_contain_contiguous = original_contains
    _expect_detected(caught,
                     "reverting to the ORDERED-SUBSEQUENCE predicate is "
                     "caught by the negative pin (the contiguity tightening is "
                     "pinned, not just described)")


# ── 3. the attribution classifier (the receipt's discriminator) ────────────
def check_drop_attribution() -> None:
    print("\n3. edge_missing attribution (operator drop vs supersede refusal)")
    from tests.eval.write_path.test_write_path_runner import (
        test_operator_drop_warnings_ignores_benign_extraction_warnings,
        test_operator_drop_warnings_separates_the_replace_channel,
    )

    results = [{
        "session_id": "wp07_bluepeak_followup",
        "extraction_warnings": [
            "entity 'per-service flags' supersedes='all-at-once rollout' does "
            "not resolve to an S3 search result",
            "point supersedes='We ship the lease fix' matches no S3 prior",
            # the recall leg's SUCCESS line — must NOT count as a refusal
            "point-supersede recall band (#8503): ref 'We ship the lease fix' "
            "is a contiguous token span of 'pt_wp06_decision'",
            "operator dropped: src/dst did not resolve to an emitted point",
        ],
    }]
    shipped = runner.operator_drop_warnings(results)
    _expect_detected(shipped["operator_drops"] == 1
                     and shipped["supersede_refusals"] == 2,
                     "shipped classifier separates the classes AND ignores the "
                     "recall leg's success line",
                     f"operator_drops={shipped['operator_drops']} "
                     f"supersede_refusals={shipped['supersede_refusals']}")

    # the recall leg's SUCCESS line must never read as a refusal: mutate the
    # refusal markers to swallow it and the pin must red.
    from tests.eval.write_path.test_write_path_runner import (
        test_recall_leg_success_warning_is_not_a_refusal,
    )
    original = runner._SUPERSEDE_REFUSAL_MARKERS
    try:
        runner._SUPERSEDE_REFUSAL_MARKERS = original + (
            "point-supersede recall band",)
        caught = _pin_fails(test_recall_leg_success_warning_is_not_a_refusal)
    finally:
        runner._SUPERSEDE_REFUSAL_MARKERS = original
    _expect_detected(caught,
                     "a marker that swallows the recall leg's SUCCESS line is "
                     "DETECTED by its pin")

    # the ORIGINAL defect, re-injected: a merged marker set that counts
    # entity-lifecycle notices as operator drops.
    original = runner._OPERATOR_DROP_MARKERS
    try:
        runner._OPERATOR_DROP_MARKERS = original + (
            "is not expressible in the Layer-1 payload",)
        caught = _pin_fails(
            test_operator_drop_warnings_separates_the_replace_channel)
    finally:
        runner._OPERATOR_DROP_MARKERS = original
    _expect_detected(caught,
                     "the merged-marker defect (entity notices counted as "
                     "operator drops) is DETECTED by its pin")


def main() -> int:
    check_selection_rules()
    check_supersede_band()
    check_drop_attribution()
    print()
    if FAILURES:
        print(f"MUTATION ARM: {len(FAILURES)} mutation(s) SLIPPED PAST THE GATE:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("MUTATION ARM: every injected regression was detected by its pin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
