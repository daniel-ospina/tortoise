"""#2292 Task 4 — pre-exposure validation on real probe evidence
(``battery validate-judge --rubric <id> --evidence <bundle.json>``).

ONE validation run over REAL deliberation text lands BEFORE #2284 exposure
part 1 — the only honest proof the R2 anchors discriminate on the real
rendering surface. The record persists by rubric id so R2 scoring is
unblocked; scoring WITHOUT the record still raises JudgeGateBlocked.

Legs over the evidence bundle (declarative anchored-yes/no protocol):
- judge retest-consistency: the SAME per-anchor render judged twice in two
  independent calls must agree (exact binomial p < 0.05 over >= 8
  identical-render retest pairs; byte-identical prompts make "position
  swap" vacuous in the single-construct vocabulary — the leg measures
  judge self-consistency, never position bias);
- inter-judge reliability: Cohen's kappa >= 0.70 between TWO real judge
  configs (BATTERY_JUDGE_MODEL + BATTERY_JUDGE_MODEL_2, both temp 0) —
  a single model at temp 0 yields kappa==1.0 by construction and never
  counts as reliability, so the real path FAILS CLOSED when the second
  config is absent;
- per-item IRT infit [0.7, 1.3] over anchored item renders;
- gold-anchor agreement >= 0.8 (the block's >= 1 expected-'no' render with
  no-share > 20% catches a degenerate all-yes judge);
- judge spend metered + HARD-STOPPED against the judge-leg reserve line
  (budget.yaml judge_leg_reserve_usd) — never a silent overshoot.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from battery.exceptions import ConfigError
from battery.judge.client import JudgeCall, JudgeClient
from battery.judge.gate import (
    DECLARATIVE_VOCAB,
    RubricRegistry,
    ValidationRecord,
    validate_rubric,
)
from battery.judge.rubric import (
    load_rubric_spec,
    render_rubric_prompt,
)

#: Minimum identical-render retest pairs (>= 8 -> p<0.05 needs >= 7/8 agree).
MIN_RETEST_PAIRS = 8


@dataclass
class _SpendMeter:
    """Accumulates judge-leg spend; HARD-STOPS against the reserve."""

    reserve_usd: float | None = None
    total_usd: float = 0.0
    calls: int = 0

    def record(self, call: JudgeCall) -> None:
        self.total_usd += float(call.cost_usd or 0.0)
        self.calls += 1
        if self.reserve_usd is not None and self.total_usd > self.reserve_usd:
            raise ConfigError(
                f"judge-leg reserve exceeded: ${self.total_usd:.4f} spent > "
                f"reserve ${self.reserve_usd:.4f} — aborting the validation "
                f"run (reserve line: budget.yaml judge_leg_reserve_usd)")


class _MeteredClient:
    """JudgeClient wrapper that records every real/mock call into the meter
    (the JudgeCall usage fields carry the metered cost)."""

    def __init__(self, client: JudgeClient, meter: _SpendMeter):
        self._client = client
        self._meter = meter

    def judge(self, rubric_id: str, item_id: str, prompt: str,
              temperature: float = 0.0) -> JudgeCall:
        call = self._client.judge(rubric_id, item_id, prompt,
                                  temperature=temperature)
        self._meter.record(call)
        return call


def _load_bundle(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        return path_or_dict
    p = Path(path_or_dict)
    if not p.is_file():
        raise ConfigError(f"evidence bundle not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _real_client(model_env: str) -> JudgeClient:
    """Real judge client from an env model id; refuses when the model id is
    absent (JudgeClient has NO default — absent env means mock BY DESIGN,
    and a mock would silently produce a kappa==1.0 lie on the real path)."""
    model = os.environ.get(model_env, "")
    if not model:
        raise ConfigError(
            f"real validation needs {model_env} (bare OpenRouter slug); "
            f"absent => mock by design — never a real-path pass")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise ConfigError(
            "real validation refuses to start: OPENROUTER_API_KEY absent")
    return JudgeClient(model_id=model)


def run_evidence_validation(*, config_dir: str | Path, rubric_id: str,
                            evidence,
                            judge_a: JudgeClient | None = None,
                            judge_b: JudgeClient | None = None,
                            records_path: str | Path,
                            reserve_usd: float | None = None,
                            force_mock: bool = False,
                            ) -> ValidationRecord:
    """Run the declarative validation battery over an evidence bundle.

    Real path (judge_a/judge_b None): builds Judge A from
    BATTERY_JUDGE_MODEL and Judge B from BATTERY_JUDGE_MODEL_2 (absent
    second config => ConfigError — fail-closed against a degenerate
    single-model kappa==1.0). Hermetic tests inject mock judges.
    """
    bundle = _load_bundle(evidence)
    renders = [str(r.get("render", "")) for r in
               bundle.get("rubrics", {}).get(rubric_id, [])
               if str(r.get("render", "")).strip()]
    if not renders:
        raise ConfigError(
            f"evidence bundle has no renders for rubric {rubric_id!r}")
    # second neutral guard (review #2575 A-P2): an EXTERNALLY supplied
    # --evidence bundle bypasses the probe producer's scrub+lint — re-lint
    # every render before it can reach a judge prompt (a leaked tool verb /
    # arm id / edge count never enters a graded construct).
    from battery.judge.rubric import lint_evidence_neutral
    for r in renders:
        lint_evidence_neutral(r)

    spec = load_rubric_spec(Path(config_dir), rubric_id)
    if not spec.is_itemized:
        raise ConfigError(
            f"rubric {rubric_id!r} is not itemized — --evidence requires a "
            f"JSON itemized rubric (declarative anchored-yes/no protocol)")
    rubric_text = render_rubric_prompt(spec)
    n_items = len(spec.items)
    if n_items < 1:
        raise ConfigError(f"rubric {rubric_id!r} has no items")

    meter = _SpendMeter(reserve_usd=reserve_usd)
    if judge_a is None:
        if force_mock:
            judge_a = JudgeClient(force_mock=True)
        else:
            judge_a = _real_client("BATTERY_JUDGE_MODEL")
    if judge_b is None:
        if force_mock:
            judge_b = JudgeClient(force_mock=True)
        else:
            judge_b = _real_client("BATTERY_JUDGE_MODEL_2")
    a = _MeteredClient(judge_a, meter)
    b = _MeteredClient(judge_b, meter)

    # Retest pairs: the SAME render judged twice (identical prompts ->
    # byte-identical; agreement measures judge self-consistency).
    pair_renders = renders[:MIN_RETEST_PAIRS]
    if len(pair_renders) < MIN_RETEST_PAIRS:
        raise ConfigError(
            f"evidence bundle has {len(renders)} renders; the retest leg "
            f"needs >= {MIN_RETEST_PAIRS} (identical-render pairs)")
    retest_pairs = [(r, r) for r in pair_renders]

    # Kappa leg: TWO judge configs over the SAME anchored item renders
    # (inter-judge reliability — never one model at temp 0). The pool is
    # the item x render product SUPPLEMENTED with the rubric's own
    # gold-anchor renders judged on their items: a yes-skewed pool (real
    # deliberation mostly satisfies the rubric) caps Cohen's kappa below
    # the 0.70 bar by the skewed-marginal kappa paradox even at ~0.91 raw
    # agreement — the gold block (>= 1 expected-no, no-share > 20%) is the
    # rubric's own balanced negative material and belongs in the pool.
    items = spec.items
    kappa_prompts: list[str] = []
    for ridx in range(len(renders)):
        for iidx in range(n_items):
            it = items[iidx]
            kappa_prompts.append(
                f"Rubric item: {it['text']}\n\nEvidence: {renders[ridx]}\n"
                f"Answer YES or NO.")
    for ga in spec.gold_anchors:
        item_text = next(
            (str(i.get("text")) for i in items
             if i.get("id") == ga.get("item_id")), None)
        if item_text:
            kappa_prompts.append(
                f"Rubric item: {item_text}\n\n"
                f"Evidence: {ga.get('render', '')}\nAnswer YES or NO.")
    labels_a = [a.judge(rubric_id, f"kappa-a{i}", p).verdict
                for i, p in enumerate(kappa_prompts)]
    labels_b = [b.judge(rubric_id, f"kappa-b{i}", p).verdict
                for i, p in enumerate(kappa_prompts)]
    if os.environ.get("BATTERY_DEBUG"):
        from collections import Counter
        print(f"[debug] kappa labels_a={Counter(labels_a)}",
              f"labels_b={Counter(labels_b)}", file=__import__("sys").stderr)

    # IRT renders: the full anchored render pool (the gate cycles
    # item x render combos at the live 3 x n_items bound).
    irt_renders = renders

    record = validate_rubric(
        rubric_id, rubric_text, a, retest_pairs, labels_a, labels_b,
        n_items=n_items, vocabulary=DECLARATIVE_VOCAB,
        irt_renders=irt_renders, gold_anchors=spec.gold_anchors,
        # Owner decision A (2026-09-08): REAL-text inter-judge reliability
        # is gated on Gwet's AC1 >= 0.70 (paradox-resistant on the
        # necessarily yes-skewed pool of real deliberation); Cohen's kappa
        # stays the gate on judge-balanced mock pools (gate default). The
        # IRT leg is MEASURED but not gating here: per-item infit at the
        # probe corpus's ~3 renders/item is under-powered (real-path IRT
        # re-arms at the #2284 Task-8 exposure pool).
        reliability_bar="ac1", irt_gate=False)

    registry = RubricRegistry(records_path)
    registry.save(record)
    return record


def meter_report(meter: _SpendMeter) -> dict:
    return {"judge_calls": meter.calls, "judge_spend_usd": meter.total_usd}
