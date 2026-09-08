"""#2292 Task 2 — declarative anchored yes/no validation protocol seam."""
from __future__ import annotations

from pathlib import Path

from battery.judge.client import JudgeCall, JudgeClient
from battery.judge.gate import DECLARATIVE_VOCAB, validate_rubric
from battery.judge.rubric import load_rubric_spec
from battery.judge.rubric import render_rubric_prompt as render_prompt_text

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"


class _YesJudge(JudgeClient):
    """Deterministic declarative judge: consistent 'yes' unless the item
    render is contradictory (then 'no') — passes every leg."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        low = prompt.lower()
        verdict = "no" if ("contradict" in low and "opposite" in low) else "yes"
        return JudgeCall(rubric_id, item_id, verdict, 0.9)


class _FlipJudge(JudgeClient):
    """Judge that flips yes/no on the retest call slot (item_id abba-*ba) —
    fails the declarative judge retest-consistency leg."""

    def judge(self, rubric_id, item_id, prompt, temperature=0.0):
        flip = item_id.endswith("-ba")
        return JudgeCall(rubric_id, item_id, "no" if flip else "yes", 0.8)


def test_declarative_good_rubric_passes_on_spec_items():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    n = len(spec.items)
    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _YesJudge(),
                          pairs, ["yes"] * n + ["no"], ["yes"] * n + ["no"],
                          n_items=n, vocabulary=DECLARATIVE_VOCAB)
    assert rec.passed
    assert len(rec.irt_infit) == n            # per-ANCHOR item infit


def test_declarative_retest_inconsistency_blocks():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    # round-2 relabel: "position-bias" was vacuous in declarative mode —
    # byte-identical renders in "swapped" positions give byte-identical
    # prompts, so a temp-0 judge always agrees and the binomial passes by
    # construction. The leg is judge RETEST-consistency (the same anchored
    # render judged twice must agree). _FlipJudge flips on the retest call
    # slot (item_id abba-*ba) → retest-inconsistent → the leg must block.
    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _FlipJudge(),
                          pairs, [], [], n_items=len(spec.items),
                          vocabulary=DECLARATIVE_VOCAB)
    assert not rec.passed and "retest" in rec.blocked_reason


def test_declarative_stress_all_identical_consistent():
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    # declarative: the stress probe over two IDENTICAL renders must return
    # the SAME yes/no verdict twice (stochastic-stability), and a verdict
    # must be produced (non-degenerate) — there is no 'tie' label in the
    # anchored vocabulary, so the pairwise all-identical->tie expectation
    # is replaced by a consistency expectation (vocabulary-parameterized).
    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _YesJudge(),
                          pairs, [], [], n_items=len(spec.items),
                          vocabulary=DECLARATIVE_VOCAB)
    assert all(rec.stress.values()), rec.blocked_reason


def test_gold_anchor_block_catches_all_yes_judge():
    # round-2: the gold-anchor set must include >= 1 expected-'no' render
    # (no-share > 20% of the block) — a degenerate all-yes judge then scores
    # below the 0.8 agreement bar and FAILS the anchor leg. Without an
    # expected-'no' anchor, a judge that says yes to every render is
    # indistinguishable from a good one (byte-identical-render retests and
    # a self-consistent judge both pass — the anchor leg is the only catch).
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    labels = [g["expected"] for g in spec.gold_anchors]   # Task-1 JSON shape
    no_share = labels.count("no") / len(labels)
    assert labels.count("no") >= 1 and no_share > 0.2

    class _AllYes(_YesJudge):          # degenerate: never says 'no'
        def judge(self, rubric_id, item_id, prompt, temperature=0.0):
            return JudgeCall(rubric_id, item_id, "yes", 0.9)

    rec = validate_rubric("r2-coverage", render_prompt_text(spec), _AllYes(),
                          [], [], [], n_items=len(spec.items),
                          vocabulary=DECLARATIVE_VOCAB)
    assert not rec.passed and "gold-anchor" in rec.blocked_reason


def test_exact_binomial_small_n():
    from battery.judge.gate import exact_binomial_p
    assert exact_binomial_p(agree=5, n=5) < 0.05      # 0.5^5 = 0.03125
    assert exact_binomial_p(agree=4, n=4) >= 0.05     # 0.0625 exact — blocks


def test_irt_leg_judges_anchored_renders_not_bare_probes():
    # RED for plan-review P1+P2: the pairwise IRT loop hardcodes contentless
    # `probe {i}` prompts (gate.py:147) AND iterates n_items * 3 judgments
    # (`for i in range(n_items * 3)`) — an irt_renders list spec'd at length
    # ">= n_items" would IndexError on a >=4-item rubric. Declarative mode
    # must judge the REAL anchored renders fed via irt_renders (>= 3 *
    # n_items — the true loop bound); assert the irt-* prompts carry anchored
    # content and that the loop fed exactly the bound, never bare probes.
    spec = load_rubric_spec(CONFIG, "r2-coverage")
    n_items = len(spec.items)
    renders = [f"evidence render {i}: the agent weighed a counter-argument "
               f"against its position and revised it."
               for i in range(3 * n_items)]     # >= 3*n_items: loop bound
    seen: list[str] = []

    class _Recording(_YesJudge):
        def judge(self, rubric_id, item_id, prompt, temperature=0.0):
            if item_id.startswith("irt-"):
                seen.append(prompt)
            return super().judge(rubric_id, item_id, prompt, temperature)

    pairs = [(f"evidence-{i}-a", f"evidence-{i}-b") for i in range(6)]
    validate_rubric("r2-coverage", render_prompt_text(spec), _Recording(),
                    pairs, ["yes"] * n_items, ["yes"] * n_items,
                    n_items=n_items, irt_renders=renders,
                    vocabulary=DECLARATIVE_VOCAB)
    assert len(seen) == 3 * n_items, \
        f"IRT leg issued {len(seen)} judgments, expected {3 * n_items}"
    assert all("counter-argument" in p for p in seen), \
        "IRT prompts are contentless bare probes, not anchored item renders"
