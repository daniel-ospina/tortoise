#!/usr/bin/env python3
"""#8503 — the MUTATION ARM: does the gate FAIL when a mechanism regresses?

A number that cannot go down pins nothing. Each check below takes a mechanism
this PR fixed, reverts it IN MEMORY (never on disk — the tree is the artifact
under review), and asserts that the pin that protects it now FAILS. Print-only
script: exit 0 means every mutation was DETECTED, exit 1 means at least one
mutation slipped past its pin (a hole in the gate, not in the mechanism).

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


def _expect(cond: bool, label: str, detail: str = "") -> None:
    status = "DETECTED" if cond else "MISSED  "
    print(f"  [{status}] {label}")
    if not cond:
        FAILURES.append(label)
    if detail:
        for line in detail.strip().splitlines():
            print(f"             {line}")


def _mutate_constant(name: str, old: str, new: str = "") -> None:
    """Replace text in a module-level prompt constant, in memory only."""
    current = getattr(v2, name)
    assert old in current, f"mutation anchor not found in {name}: {old[:60]!r}"
    setattr(v2, name, current.replace(old, new, 1))


# ── 1. the SELECTION rules ────────────────────────────────────────────────
# The pins that protect them are tests/test_extractor_v2.py::
# TestOperatorSelectionRules. Each mutation deletes the rule text and asks the
# SAME question the pin asks: does the rendered prompt still carry it?
def check_selection_rules() -> None:
    print("\n1. operator-selection rules (prompt text pins)")
    cases = [
        ("S2_TMPL", "the SPEAKER is not a filter on CLAIMS",
         "assistant-asserted claims are licensed"),
        ("S2_TMPL", "A QUESTION THAT ASSERTS A CLAIM CARRIES THAT CLAIM",
         "an asserting question carries its claim"),
        ("S2_TMPL", "An operational OUTCOME that EVIDENCES a claim",
         "an operational outcome is a durable claim"),
    ]
    for const, anchor, label in cases:
        original = getattr(v2, const)
        try:
            _mutate_constant(const, anchor)
            _expect(anchor not in getattr(v2, const),
                    f"{const}: {label}")
        finally:
            setattr(v2, const, original)

    # the shared S2/S4 rule block reaches BOTH stages from one source
    shared = v2._s2s4_rules()
    for anchor, label in (
        ("A THEORY THE CONVERSATION DISPLACES",
         "a displaced theory is a NAND attacker (never IMPL)"),
        ("An ENTITY-level supersede is NOT this channel",
         "an entity supersede writes no reversal edge"),
        ("The SRC of a MITIGATES is ANY point", "every warrant point is a src"),
    ):
        _expect(anchor in shared and anchor in v2.render_s2_prompt()
                and anchor in v2.render_s4_prompt(
                    "story", {"entities": [], "events": [], "points": []},
                    {"entities": [], "events": [], "points": [],
                     "operators": []}),
                f"shared rule reaches S2+S4: {label}")


# ── 2. the SUPERSEDE recall band (the measured drop) ───────────────────────
# ARTIFACTS, not invented values:
#   * the REF is the one wp07 actually emitted, quoted from the #8503
#     run receipt's extraction_warnings — "point supersedes='We ship the
#     lease fix all-at-once behind a single global kil' matches no S3
#     prior" — which is the pre-fix write path dropping the CORRECTS.
#   * the PRIOR is the wp06 planted endpoint's emitted point (the gold's
#     `wp07_bluepeak_followup_op_04.to.verbatim_anchor`, plus the emitted tail).
def check_supersede_band() -> None:
    print("\n2. SUPERSEDE recall band (the drop the receipt measured)")
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
    _expect(bool(formed), "band ON: the drifted ref forms the CORRECTS record",
            f"supersessions={shipped['supersessions']}")

    original = v2.SUPERSEDE_REF_OVERLAP
    try:
        v2.SUPERSEDE_REF_OVERLAP = 1.01  # the pre-#8503 exact-only behaviour
        mutated = v2.execute_embed(embed, search, session_id="s2")
    finally:
        v2.SUPERSEDE_REF_OVERLAP = original
    dropped = [s for s in mutated["supersessions"]
               if s["superseded"] == "pt_wp06_decision"]
    _expect(not dropped,
            "band OFF: the same ref is DROPPED again (the mutation is real)",
            "and the drop is named: " + "; ".join(
                w for w in mutated["warnings"] if "supersedes" in w)[:160])


# ── 3. the attribution classifier (the receipt's discriminator) ────────────
def check_drop_attribution() -> None:
    print("\n3. edge_missing attribution (operator drop vs supersede refusal)")
    results = [{
        "session_id": "wp07_bluepeak_followup",
        "extraction_warnings": [
            "entity 'per-service flags' supersedes='all-at-once rollout' does "
            "not resolve to an S3 search result",
            "point supersedes='We ship the lease fix' matches no S3 prior",
            "operator dropped: src/dst did not resolve to an emitted point",
        ],
    }]
    shipped = runner.operator_drop_warnings(results)
    _expect(shipped["operator_drops"] == 1
            and shipped["supersede_refusals"] == 2,
            "shipped classifier separates the two drop classes")

    original = runner._OPERATOR_DROP_MARKERS
    try:
        # the FIRST cut of this classifier: one merged marker set, which
        # counted supersede refusals as operator drops (measured: 5 "drops"
        # on a run where the operator pass dropped nothing).
        runner._OPERATOR_DROP_MARKERS = original + (
            "point supersedes=",
            "does not resolve to an S3 search result",
        )
        merged = runner.operator_drop_warnings(results)
    finally:
        runner._OPERATOR_DROP_MARKERS = original
    _expect(merged["operator_drops"] != shipped["operator_drops"],
            "merged-marker mutation is DETECTED (counts move 1 -> 3)",
            f"merged={merged['operator_drops']} shipped={shipped['operator_drops']}")


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
