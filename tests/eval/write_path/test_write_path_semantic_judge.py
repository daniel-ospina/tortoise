"""#5085 — the additive blind BANDED semantic judge + its calibration harness.

Hermetic unit/contract layer over ``judge.BandedSalienceJudge``,
``judge.aggregate_banded``, the runner's additive ``semantic_judge`` receipt
block, and ``calibration``.  No DB/network/LLM: the judge's model is always an
injected fake, so the protocol's MECHANICS are pinned here and the model's
behavior is measured by the committed real run.

The load-bearing properties asserted:

* the probability is an AGREEMENT FRACTION over repeated independent
  judgements (never a verbalized confidence);
* every unit is judged in BOTH prompt orders and the orders are averaged
  (position-bias mitigation);
* judge-blindness is enforced by a guard that RAISES — and the guard-removal
  test proves the blindness test is actually sensitive to that guard;
* a paraphrase-only rewrite that the mechanical anchor bar scores 0 can be
  banded same_fact (≥ 0.80), and a CONTRADICTING stored point is not scored
  as preserved — even when the mechanical anchor bar sees the anchor and
  counts it;
* a mechanical-lane receipt is byte-identical (no new keys);
* κ/α are NEVER invented — pending until a human labels.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.eval.write_path import calibration, corpus, grading, judge, runner

# ── Synthetic gold/memory helpers (mirror the grading-test conventions) ────


def _unit(uid: str, anchor: str, *, rephrase: bool = True) -> dict:
    return {
        "id": uid,
        "survival": {
            "via_anchor": anchor,
            "accepts_rephrase_linked": rephrase,
            "provenance_required": True,
            "ep_update_required": True,
        },
    }


def gold_with(units) -> dict:
    return {
        "schema_version": 1,
        "session_id": "synthetic",
        "scenario": "test",
        "planted_units": [
            {
                "id": u["id"],
                "kind": "fact",
                "verbatim_anchor": u["survival"]["via_anchor"],
                "notability": "high",
                "depth_bucket": "early",
                "planted_turn": 1,
            }
            for u in units
        ],
        "distractors": [],
        "attribution_hazards": [],
        "salient_units": units,
        "distractor_leakage_tolerance": 1,
    }


def _point(pid: str, content: str) -> dict:
    return {
        "point_id": pid,
        "content": content,
        "provenance_present": True,
        "ep_updated": True,
    }


# ── Fake judge models ──────────────────────────────────────────────────────


class _CannedModel:
    """Returns canned raw responses in order; records every prompt."""

    def __init__(self, responses: list[str], *, temperature: float = 0.0) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []
        self.temperature = temperature

    def complete(self, *, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if self.responses:
            return self.responses.pop(0)
        return '{"u1": "NOT_PRESERVED"}'


class _OrderBiasedModel:
    """Says PRESERVED only when the MEMORY block comes FIRST.

    Models the documented systematic position bias: without the two-order
    swap-and-average this unit would score 1.0 or 0.0 depending on the single
    order chosen; with it, 0.5.
    """

    def complete(self, *, system: str, user: str) -> str:
        preserved = user.strip().startswith("## MEMORY NOTES")
        return json.dumps(
            {"u1": "PRESERVED" if preserved else "NOT_PRESERVED"}, separators=(",", ":")
        )


class _PolarityModel:
    """A crude deterministic entailer: token coverage + negator parity.

    Used to show the CONTRADICTION mirror non-circularly: the verdict is
    derived from the prompt's own probe/memory text, not hardcoded.
    """

    _NEGATORS = frozenset({"no", "not", "never", "none", "without", "zero"})
    _STOP = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "to",
        "of", "and", "or", "in", "on", "at", "for", "with", "it", "its",
        "this", "that", "by", "as", "we", "they",
    })

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        cleaned = "".join(c if c.isalnum() else " " for c in text.lower())
        return {
            t for t in cleaned.split() if t not in cls._STOP and len(t) > 1
        }

    @classmethod
    def _probes_and_memory(cls, user: str) -> tuple[dict[str, str], str]:
        blocks: dict[str, str] = {}
        for part in user.split("## "):
            if not part.strip():
                continue
            head, _, body = part.partition("\n")
            blocks[head.strip()] = body
        probes: dict[str, str] = {}
        for line in blocks.get("PROBES", "").splitlines():
            if ": " in line:
                pid, _, text = line.partition(": ")
                probes[pid.strip()] = text.strip()
        memory_lines = [
            line.split(". ", 1)[1]
            for line in blocks.get("MEMORY NOTES", "").splitlines()
            if line.strip() and ". " in line
        ]
        return probes, " ".join(memory_lines)

    def complete(self, *, system: str, user: str) -> str:
        probes, memory = self._probes_and_memory(user)
        memory_tokens = self._tokens(memory)
        memory_neg = bool(memory_tokens & self._NEGATORS)
        out: dict[str, str] = {}
        for pid, probe in probes.items():
            probe_tokens = self._tokens(probe)
            if not probe_tokens:
                out[pid] = "NOT_PRESERVED"
                continue
            coverage = len(probe_tokens & memory_tokens) / len(probe_tokens)
            polarity_ok = (bool(probe_tokens & self._NEGATORS) == memory_neg)
            out[pid] = (
                "PRESERVED" if coverage >= 0.5 and polarity_ok else "NOT_PRESERVED"
            )
        return json.dumps(out)


def _arm(model, **kwargs) -> judge.BandedSalienceJudge:
    return judge.BandedSalienceJudge(
        model_factory=lambda _name: model, **kwargs
    )


# ── Band table + weights ───────────────────────────────────────────────────


def test_band_mapping_is_the_owner_table():
    assert judge.band_for_probability(1.0) == judge.BAND_SAME_FACT
    assert judge.band_for_probability(0.80) == judge.BAND_SAME_FACT
    assert judge.band_for_probability(0.79) == judge.BAND_LIKELY
    assert judge.band_for_probability(0.70) == judge.BAND_LIKELY
    assert judge.band_for_probability(0.69) == judge.BAND_MAYBE
    assert judge.band_for_probability(0.50) == judge.BAND_MAYBE
    assert judge.band_for_probability(0.49) == judge.BAND_LIKELY_NOT
    assert judge.band_for_probability(0.0) == judge.BAND_LIKELY_NOT


def test_band_weights_are_interval_midpoints_of_the_thresholds():
    # Arithmetic on the owner's thresholds only — no invented constant.
    assert judge.BAND_WEIGHTS == {
        "same_fact": 0.90, "likely": 0.75, "maybe": 0.60, "likely_not": 0.25,
    }
    with pytest.raises(ValueError):
        judge.band_weight("nope")


def test_semantic_pin_is_distinct_from_the_mechanical_pin():
    assert judge.SEMANTIC_JUDGE_PIN != judge.JUDGE_PIN_MECHANICAL
    assert judge.PARAPHRASE_PROMPT_VERSION in judge.SEMANTIC_JUDGE_PIN
    assert judge.SEMANTIC_PROMPT_VERSION in judge.SEMANTIC_JUDGE_PIN


# ── Probability is an agreement fraction ───────────────────────────────────


def test_probability_is_the_agreement_fraction_over_repeated_judgements():
    # 2 samples × 2 orders = 4 votes; 3 of 4 PRESERVED → 0.75.
    model = _CannedModel([
        '{"u1": "PRESERVED"}',
        '{"u1": "PRESERVED"}',
        '{"u1": "NOT_PRESERVED"}',
        '{"u1": "PRESERVED"}',
    ])
    arm = _arm(model, samples=2)
    units = arm.judge_units({"u1": "probe"}, ["a memory note"])
    assert units["u1"]["probability"] == 0.75
    assert units["u1"]["votes_yes"] == 3
    assert units["u1"]["votes_total"] == 4
    assert units["u1"]["band"] == judge.BAND_LIKELY


def test_unanimous_agreement_is_probability_one():
    model = _CannedModel(['{"u1": "PRESERVED"}'] * 4)
    units = _arm(model, samples=2).judge_units({"u1": "probe"}, ["note"])
    assert units["u1"]["probability"] == 1.0
    assert units["u1"]["band"] == judge.BAND_SAME_FACT


def test_protocol_error_never_counts_as_agreement():
    model = _CannedModel(['{"u1": "MAYBE"}'])
    with pytest.raises(judge.JudgeProtocolError):
        _arm(model, samples=1, orders=("probes_first",)).judge_units(
            {"u1": "probe"}, ["note"]
        )


# ── Swap-and-average (position bias) ───────────────────────────────────────


def test_swap_and_average_blunts_position_bias():
    """An order-biased judge scores 0.5, not 1.0/0.0 — both orders are used."""
    arm = _arm(_OrderBiasedModel(), samples=3)
    units = arm.judge_units({"u1": "probe"}, ["note"])
    assert units["u1"]["probability"] == 0.5
    per_order = units["u1"]["orders"]
    assert per_order["probes_first"]["probability"] == 0.0
    assert per_order["memory_first"]["probability"] == 1.0
    assert set(per_order) == set(judge.PROMPT_ORDERS)


def test_both_prompt_orders_contain_the_same_blocks():
    probes = {"u1": "a probe"}
    memory = ["a note"]
    first = judge.build_banded_prompt(probes, memory, order="probes_first")
    second = judge.build_banded_prompt(probes, memory, order="memory_first")
    assert first.strip().startswith("## PROBES")
    assert second.strip().startswith("## MEMORY NOTES")
    assert "## PROBES\nu1: a probe" in first and "## PROBES\nu1: a probe" in second
    assert "1. a note" in first and "1. a note" in second
    with pytest.raises(ValueError):
        judge.build_banded_prompt(probes, memory, order="sideways")


def test_sampling_temperature_is_applied_to_the_judge_model():
    """temp 0.0 would make every sample identical and collapse the bands."""
    model = _CannedModel(['{"u1": "PRESERVED"}'], temperature=0.0)
    arm = _arm(model, samples=1, orders=("probes_first",), temperature=0.7)
    assert model.temperature == 0.7
    assert arm.temperature == 0.7


# ── The regression this issue is about ─────────────────────────────────────


def test_paraphrase_only_rewrite_bands_same_fact_while_the_anchor_bar_scores_zero():
    """Paraphrase-only preservation: mechanical 0 (today), judged ≥ 0.80."""
    anchor = "the quarry backfill stalled on a duplicate ingest batch"
    paraphrase = "two writers double-submitted the replay job and it wedged"
    gold = gold_with([_unit("u1", anchor)])
    points = [_point("p1", paraphrase)]

    # Today's mechanical anchor bar (with the #2405 paraphrase leg) scores it 0:
    # the anchor's content tokens do not appear in the rewrite.
    mechanical = grading.macro_survival_counts(gold, points)
    assert mechanical["survived"] == 0, mechanical
    assert grading.survival_match(paraphrase, anchor) is False

    # The banded judge — given the verdict a real judge returns for a
    # paraphrase — bands it same_fact.  (The verdict is stubbed here; the
    # MODEL's behavior is what the committed real run measures.)
    model = _CannedModel(['{"u1": "PRESERVED"}'] * 4)
    arm = _arm(model, samples=2, anchors=[anchor])
    units = arm.judge_units({"u1": paraphrase}, [paraphrase])
    assert units["u1"]["probability"] == 1.0
    assert units["u1"]["band"] == judge.BAND_SAME_FACT

    aggregate = judge.aggregate_banded(
        [{"unit_id": "u1", **units["u1"]}]
    )
    assert aggregate["semantic_survival_rate"] == 1.0
    assert aggregate["band_weighted_score"] >= 0.80


def test_contradicting_stored_point_is_not_banded_preserved():
    """The mirror: the anchor bar sees the anchor; the judge sees the CONTRADICTION."""
    anchor = "the lease rows were locked by a duplicate batch"
    # The PROBE is the paraphrase-level claim (blind: it must not carry the anchor).
    probe = "a repeated batch locked the lease rows"
    # The stored Point contains the anchor VERBATIM but asserts the opposite —
    # the memory block is observed data, so the blindness guard does NOT fire
    # on it (the probe is what must stay anchor-free).
    contradicting = (
        "the duplicate batch never locked the lease rows; the claim that "
        "the lease rows were locked by a duplicate batch is wrong"
    )
    gold = gold_with([_unit("u1", anchor, rephrase=False)])
    points = [_point("p1", contradicting)]
    # The mechanical anchor bar counts it as survived (verbatim substring).
    assert grading.macro_survival_counts(gold, points)["survived"] == 1

    arm = _arm(_PolarityModel(), samples=2, anchors=[anchor])
    units = arm.judge_units({"u1": probe}, [contradicting])
    assert units["u1"]["probability"] == 0.0
    assert units["u1"]["band"] == judge.BAND_LIKELY_NOT


def test_reworded_date_unit_stays_on_the_anchor_bar():
    """A reworded date is corruption: the mechanical bar still fails it."""
    anchor = "the release was frozen on October 15 before the freeze window"
    reworded = "the release was frozen in mid-October ahead of the change freeze"
    gold = gold_with([_unit("u1", anchor, rephrase=False)])
    points = [_point("p1", reworded)]
    assert grading.macro_survival_counts(gold, points)["survived"] == 0
    # …and the semantic block does not replace/alter that mechanical result:
    # the two live side by side with independent values.
    arm = _arm(_CannedModel(['{"u1": "PRESERVED"}'] * 4), samples=2, anchors=[anchor])
    units = arm.judge_units({"u1": reworded}, [reworded])
    assert judge.aggregate_banded([units["u1"]])["semantic_survival_rate"] == 1.0


# ── Judge-blindness (load-bearing) ─────────────────────────────────────────


def _anchors_of(gold: dict) -> list[str]:
    return [u["survival"]["via_anchor"] for u in gold["salient_units"]]


def test_banded_prompt_is_blind_to_anchors_over_the_real_corpus():
    for session_id in corpus.session_ids():
        gold = corpus.load_gold(session_id)
        probes = {u["id"]: f"generic probe for unit {u['id']}" for u in gold["salient_units"]}
        memory = ["a plausible retained note", "another note"]
        anchors = _anchors_of(gold)
        for order in judge.PROMPT_ORDERS:
            user = judge.build_banded_prompt(probes, memory, order=order)
            leak = judge.prompt_leaks_anchor(judge._BANDED_SYSTEM + "\n" + user, anchors)
            assert leak is None, (
                f"{session_id}/{order}: blind prompt leaked anchor {leak!r}"
            )


def test_blindness_guard_raises_when_a_probe_echoes_an_anchor():
    anchor = "the quarry backfill stalled on a duplicate ingest batch"
    arm = _arm(
        _CannedModel(['{"u1": "PRESERVED"}']),
        samples=1,
        orders=("probes_first",),
        anchors=[anchor],
    )
    with pytest.raises(judge.JudgeBlindnessError):
        arm.judge_units({"u1": anchor}, ["unrelated note"])


def test_blindness_guard_is_what_detects_the_leak(monkeypatch):
    """Proof the blindness test is SENSITIVE: remove the guard → no raise.

    With ``prompt_leaks_anchor`` neutralized (the guard removed) the SAME
    leaky probe flows straight into the judge call — so the raising test above
    is exercising the guard, not some incidental failure.
    """
    anchor = "the quarry backfill stalled on a duplicate ingest batch"
    monkeypatch.setattr(judge, "prompt_leaks_anchor", lambda *a, **k: None)
    model = _CannedModel(['{"u1": "PRESERVED"}'])
    arm = _arm(model, samples=1, orders=("probes_first",), anchors=[anchor])
    units = arm.judge_units({"u1": anchor}, ["unrelated note"])
    assert units["u1"]["probability"] == 1.0  # the leak was NOT caught
    assert anchor in model.prompts[0][1]  # …and it did reach the judge


def test_paraphrase_echoing_the_anchor_is_rejected_and_retried():
    """A single echoing paraphrase retries; the blindness guarantee holds.

    Measured: a real full-corpus run failed on exactly this (the paraphrase
    model echoed 'Maya is the natural driver for the flip'), so one echo must
    not fail a whole run — but a persistent echo still must.
    """
    anchor = "the quarantine lease is held by one owner"

    class _EchoThenClean:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        def complete(self, *, system: str, user: str) -> str:
            self.calls += 1
            self.prompts.append(user)
            text = anchor if self.calls == 1 else "one owner holds the lease"
            return json.dumps({"paraphrase": text})

    model = _EchoThenClean()
    arm = _arm(model, samples=1, orders=("probes_first",), anchors=[anchor])
    probes = arm.synthesize_probes(gold_with([_unit("u1", anchor)]), {})
    assert probes["u1"] == "one owner holds the lease"
    assert arm.leak_retries_used == 1
    assert arm.leak_rejections == 1
    # The retry must be a DIFFERENT input (at temp 0.0 an identical prompt can
    # reproduce the same echo); the gold-seeing retry names the leaked span.
    assert model.prompts[1] != model.prompts[0]
    assert anchor in model.prompts[1]


def test_persistent_paraphrase_echo_still_fails_the_arm():
    anchor = "the quarantine lease is held by one owner"

    class _AlwaysEcho:
        def complete(self, *, system: str, user: str) -> str:
            return json.dumps({"paraphrase": anchor})

    arm = _arm(
        _AlwaysEcho(), samples=1, orders=("probes_first",), anchors=[anchor],
        max_paraphrase_leak_retries=2,
    )
    with pytest.raises(judge.JudgeBlindnessError):
        arm.synthesize_probes(gold_with([_unit("u1", anchor)]), {})
    assert arm.leak_rejections == 3  # initial + 2 retries


# ── Aggregation ────────────────────────────────────────────────────────────


def test_aggregate_banded_distribution_and_weighted_score():
    units = [
        {"unit_id": "a", "probability": 1.0},    # same_fact
        {"unit_id": "b", "probability": 0.8},    # same_fact
        {"unit_id": "c", "probability": 0.7},    # likely
        {"unit_id": "d", "probability": 0.5},    # maybe
        {"unit_id": "e", "probability": 0.0},    # likely_not
    ]
    aggregate = judge.aggregate_banded(units)
    assert aggregate["band_distribution"] == {
        "same_fact": 2, "likely": 1, "maybe": 1, "likely_not": 1,
    }
    assert aggregate["units_total"] == 5
    assert aggregate["same_fact_rate"] == 0.4
    assert aggregate["semantic_survival_rate"] == 0.8
    assert aggregate["band_weighted_score"] == round(
        (0.90 + 0.90 + 0.75 + 0.60 + 0.25) / 5, 6
    )
    # The bands were written back onto the units the caller passed.
    assert [u["band"] for u in units] == [
        "same_fact", "same_fact", "likely", "maybe", "likely_not",
    ]


def test_aggregate_banded_empty_is_null_never_zero():
    aggregate = judge.aggregate_banded([])
    assert aggregate["band_weighted_score"] is None
    assert aggregate["semantic_survival_rate"] is None
    assert aggregate["units_total"] == 0


# ── Receipt additivity ─────────────────────────────────────────────────────


def _completed_report() -> dict:
    return {
        "run_id": "w2b-test",
        "date": "2026-01-01T00:00:00Z",
        "run_status": "completed",
        "verdict": "inconclusive",
        "failure_origin": None,
        "commit": "abc1234",
        "corpus_hash": "sha256:deadbeef",
        "judge_pin": judge.JUDGE_PIN_MECHANICAL,
        "resolved_config": {"extractor_posture": "llm"},
        "cost_usd": 0.0,
        "quote_spans_total": 0,
        "operator_audit": None,
        "metrics": {key: 1.0 for key in runner.schema.METRIC_VALUES},
        "session_results": [],
        "notes": [],
        "log": [],
    }


def test_mechanical_receipt_has_no_semantic_keys():
    """The mechanical lane's receipt is additive-free — byte-identical shape."""
    report = _completed_report()
    report["semantic_judge"] = None
    receipt = runner.build_receipt(report)
    assert "semantic_judge" not in receipt
    assert "semantic_judge_pin" not in receipt
    assert runner.validate_receipt(receipt) == []


def test_semantic_receipt_carries_the_pin_and_validates():
    block = {
        "status": "completed",
        "pin": judge.SEMANTIC_JUDGE_PIN,
        "band_distribution": {"same_fact": 1, "likely": 0, "maybe": 0, "likely_not": 0},
        "band_weighted_score": 0.9,
        "probability_mean": 1.0,
        "semantic_survival_rate": 1.0,
        "same_fact_rate": 1.0,
        "units": [],
    }
    report = _completed_report()
    report["semantic_judge"] = block
    receipt = runner.build_receipt(report)
    assert receipt["semantic_judge_pin"] == judge.SEMANTIC_JUDGE_PIN
    assert runner.validate_receipt(receipt) == []


def test_semantic_receipt_validation_rejects_a_pin_mismatch():
    block = {
        "status": "completed", "pin": judge.SEMANTIC_JUDGE_PIN,
        "band_distribution": {"same_fact": 0, "likely": 0, "maybe": 0, "likely_not": 0},
        "band_weighted_score": None, "probability_mean": None,
        "semantic_survival_rate": None, "same_fact_rate": None,
    }
    report = _completed_report()
    report["semantic_judge"] = block
    receipt = runner.build_receipt(report)
    receipt["semantic_judge_pin"] = "wrong-pin"
    issues = runner.validate_receipt(receipt)
    assert any("semantic_judge_pin" in issue for issue in issues)


def test_semantic_arm_is_refused_on_the_deterministic_lane(monkeypatch):
    """The judged lane must never silently enter the CI (m2) gate."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    report = runner.run_benchmark(judge_mode="semantic")
    assert report["run_status"] == "failed"
    assert report["failure_origin"] == "runner_error"
    assert "requires the llm extractor posture" in json.dumps(report["log"]) + \
        json.dumps(report)


# ── Calibration harness (κ/α pending labels only) ──────────────────────────


def _semantic_block() -> dict:
    units = []
    bands = [("u_same", 1.0), ("u_likely", 0.7), ("u_maybe", 0.5), ("u_neg", 0.0)]
    for index in range(40):
        unit_id, probability = bands[index % 4]
        units.append({
            "session_id": "wp01_quarry_debug",
            "unit_id": f"{unit_id}_{index}",
            "probe": f"probe {index}",
            "probability": probability,
            "band": judge.band_for_probability(probability),
            "votes_yes": int(probability * 10),
            "votes_total": 10,
        })
    return {
        "status": "completed",
        "pin": judge.SEMANTIC_JUDGE_PIN,
        "units": units,
        "memory_by_session": {"wp01_quarry_debug": ["note one", "note two"]},
    }


def test_calibration_sample_is_deterministic_and_spans_all_four_bands():
    block = _semantic_block()
    first = calibration.build_calibration_sample(block, n=30, run_id="w2b-x")
    second = calibration.build_calibration_sample(block, n=30, run_id="w2b-x")
    assert first == second
    assert first["n_units"] == 30
    assert set(first["bands_present"]) == set(judge.BAND_ORDER)
    assert first["bands_missing"] == []
    assert all(item["human_band"] is None for item in first["items"])
    assert first["items"][0]["memory_notes"] == ["note one", "note two"]
    assert first["semantic_judge_pin"] == judge.SEMANTIC_JUDGE_PIN


def test_calibration_report_is_pending_and_never_invents_kappa():
    sample = calibration.build_calibration_sample(_semantic_block(), n=30)
    report = calibration.calibration_report(sample)
    assert report["labels_pending"] is True
    assert report["cohen_kappa"] is None
    assert report["krippendorff_alpha"] is None
    assert "pending" in report["kappa_note"]


def test_calibration_report_computes_kappa_alpha_once_labeled():
    sample = calibration.build_calibration_sample(_semantic_block(), n=30)
    for item in sample["items"]:
        item["human_band"] = item["judge_band"]
    report = calibration.calibration_report(sample)
    assert report["labels_pending"] is False
    assert report["cohen_kappa"] == 1.0
    assert report["krippendorff_alpha"] == 1.0


def test_cohen_kappa_known_value():
    # 10 items, 9 agree; judge margins 6/4, human 5/5 → po=0.9, pe=0.5, κ=0.8.
    judge_labels = ["same_fact"] * 6 + ["likely_not"] * 4
    human_labels = ["same_fact"] * 5 + ["likely_not"] * 5
    assert calibration.cohen_kappa(judge_labels, human_labels) == pytest.approx(0.8)


def test_cohen_kappa_rejects_degenerate_and_mismatched_inputs():
    with pytest.raises(ValueError):
        calibration.cohen_kappa(["same_fact"], ["same_fact", "likely"])
    with pytest.raises(ValueError):
        calibration.cohen_kappa([], [])
    with pytest.raises(ValueError):
        calibration.cohen_kappa(["nope"], ["same_fact"])
    with pytest.raises(ValueError):
        # expected agreement 1.0 → κ undefined, never a fabricated 0/1.
        calibration.cohen_kappa(["same_fact"] * 3, ["same_fact"] * 3)


def test_krippendorff_alpha_perfect_and_degenerate():
    # coders × units: two coders, two units, perfect agreement.
    assert calibration.krippendorff_alpha(
        [["same_fact", "likely"], ["same_fact", "likely"]]
    ) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        calibration.krippendorff_alpha([["same_fact", None]])
    with pytest.raises(ValueError):
        # 2 coders but only ONE unit carries two codings
        calibration.krippendorff_alpha([["same_fact"], ["same_fact"]])
    with pytest.raises(ValueError):
        calibration.krippendorff_alpha([["nope", "nope"], ["nope", "nope"]])


def test_default_judge_model_is_a_registry_key_not_a_raw_id():
    """The pre-#5085 default was a raw id with no MODELS entry (would raise)."""
    from tests.model_adapters import MODELS

    assert judge.DEFAULT_JUDGE_MODEL in MODELS
    assert "deepseek-flash" in MODELS


# ── Runner wiring (hermetic: snapshot + judge model both stubbed) ──────────


class _WiringModel:
    """Answers the paraphrase stage, then marks every probe PRESERVED."""

    def complete(self, *, system: str, user: str) -> str:
        if "Write the paraphrase probe" in user:
            return '{"paraphrase": "a neutral paraphrase of the planted claim"}'
        probe_ids = re.findall(r"^(\w+): ", user, flags=re.MULTILINE)
        return json.dumps({pid: "PRESERVED" for pid in sorted(set(probe_ids))})


def test_run_semantic_judge_wires_probes_bands_pin_and_memory(monkeypatch):
    session_id = "wp01_quarry_debug"
    memory_note = "the retry path skipped the assigned-owner check"
    monkeypatch.setattr(
        runner,
        "snapshot_session",
        lambda _sdk, _sid: {"points": [_point("p1", memory_note)], "rephrase_edges": []},
    )
    block = runner._run_semantic_judge(
        None,
        [session_id],
        corpus.WRITE_PATH_DIR,
        samples=2,
        judge_factory=lambda _name: _WiringModel(),
    )
    gold = corpus.load_gold(session_id)
    assert block["status"] == "completed"
    assert block["pin"] == judge.SEMANTIC_JUDGE_PIN
    assert block["samples"] == 2
    assert len(block["units"]) == len(gold["salient_units"])
    assert block["band_distribution"]["same_fact"] == len(block["units"])
    assert block["band_weighted_score"] == 0.90
    assert block["memory_by_session"][session_id] == [memory_note]
    assert all(u["unit_id"] for u in block["units"])
    assert all(u["probe"] for u in block["units"])
    assert block["logprobs"]["available"] is False


def test_run_semantic_judge_blindness_violation_propagates(monkeypatch):
    """A leaky paraphrase must NOT be captured as a soft failure."""
    session_id = "wp01_quarry_debug"
    gold = corpus.load_gold(session_id)
    leaky = gold["salient_units"][0]["survival"]["via_anchor"]

    class _LeakyParaphrase(_WiringModel):
        def complete(self, *, system: str, user: str) -> str:
            if "Write the paraphrase probe" in user:
                return json.dumps({"paraphrase": leaky})
            return super().complete(system=system, user=user)

    monkeypatch.setattr(
        runner,
        "snapshot_session",
        lambda _sdk, _sid: {"points": [_point("p1", "a note")], "rephrase_edges": []},
    )
    with pytest.raises(judge.JudgeBlindnessError):
        runner._run_semantic_judge(
            None,
            [session_id],
            corpus.WRITE_PATH_DIR,
            samples=1,
            judge_factory=lambda _name: _LeakyParaphrase(),
        )


# ── Review-cycle regressions (round 1) ─────────────────────────────────────


class _UsageModel:
    """Fake adapter exposing the production cost attributes."""

    def __init__(self, *, last_cost_usd, last_cost=None) -> None:
        self.id = "upstage/solar-pro4"
        self.temperature = 0.0
        self.last_cost_usd = last_cost_usd
        if last_cost is not None:
            self.last_cost = last_cost

    def complete(self, *, system: str, user: str) -> str:  # pragma: no cover
        return "{}"


def test_record_usage_never_prices_a_token_count_as_usd():
    """P1 (#5085 review): ``last_cost`` is ``usage.total_tokens``, not USD.

    The production adapter sets ``last_cost_usd = None`` when the route
    reports no charge — falling back to ``last_cost`` there would publish a
    spend figure in the thousands.
    """
    arm = _arm(_UsageModel(last_cost_usd=None, last_cost=4321))
    arm.record_usage(arm._judge_model)
    assert arm.cost_usd == 0.0
    assert arm.cost_priced_calls == 0


def test_record_usage_folds_a_reported_dollar_figure():
    arm = _arm(_UsageModel(last_cost_usd=0.0025, last_cost=4321))
    arm.record_usage(arm._judge_model)
    arm.record_usage(arm._judge_model)
    assert arm.cost_usd == pytest.approx(0.005)
    assert arm.cost_priced_calls == 2


def test_model_id_family_normalises_the_lane_prefix():
    assert runner._model_id_family("deepseek/deepseek-v4-flash") == "deepseek-v4-flash"
    assert runner._model_id_family("deepseek-v4-flash") == "deepseek-v4-flash"
    assert runner._model_id_family("upstage/solar-pro4") == "solar-pro4"
    assert runner._model_id_family("solar-pro4") == "solar-pro4"


def test_run_semantic_judge_compares_wire_ids_for_independence(monkeypatch):
    """Independence is a claim about MODELS — compare wire ids, not a key."""
    session_id = "wp01_quarry_debug"
    monkeypatch.setattr(
        runner,
        "snapshot_session",
        lambda _sdk, _sid: {"points": [_point("p1", "a note")], "rephrase_edges": []},
    )
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    block = runner._run_semantic_judge(
        None,
        [session_id],
        corpus.WRITE_PATH_DIR,
        samples=3,
        judge_factory=lambda _name: _WiringModel(),
    )
    # _WiringModel carries no ``id``; the wire id falls back to the registry key
    # ("solar-pro4"), which still differs from the deepseek extractor default.
    assert block["model_id"] == "solar-pro4"
    assert "WARNING" not in block["model_independence"]

    monkeypatch.setenv("TORTOISE_EXTRACT_MODEL", "upstage/solar-pro4")
    block = runner._run_semantic_judge(
        None,
        [session_id],
        corpus.WRITE_PATH_DIR,
        samples=3,
        judge_factory=lambda _name: _WiringModel(),
    )
    assert "WARNING" in block["model_independence"]


def test_run_semantic_judge_judge_calls_excludes_paraphrase_calls(monkeypatch):
    session_id = "wp01_quarry_debug"
    monkeypatch.setattr(
        runner,
        "snapshot_session",
        lambda _sdk, _sid: {"points": [_point("p1", "a note")], "rephrase_edges": []},
    )
    block = runner._run_semantic_judge(
        None,
        [session_id],
        corpus.WRITE_PATH_DIR,
        samples=3,
        judge_factory=lambda _name: _WiringModel(),
    )
    units = len(block["units"])
    assert block["paraphrase_calls"] == units
    # One verdict call per (order, sample) scores ALL units at once, so the
    # count is samples x orders — the paraphrase calls are counted separately.
    assert block["judge_calls"] == 3 * len(judge.PROMPT_ORDERS)
    # The two counts are separate ledgers, not one total: the verdict calls
    # (6) are fewer than the per-unit paraphrase calls (16) here, whereas the
    # old ``prompt_count`` field would have reported their sum (22).
    assert block["judge_calls"] != block["paraphrase_calls"]


def test_run_benchmark_refuses_a_degenerate_sample_count():
    with pytest.raises(ValueError, match="judge_samples must be >= 3"):
        runner.run_benchmark(judge_mode="semantic", judge_samples=2)


def test_receipt_validation_rejects_a_distribution_sum_mismatch():
    block = {
        "status": "completed", "pin": judge.SEMANTIC_JUDGE_PIN,
        "units_total": 4,
        "band_distribution": {"same_fact": 1, "likely": 1, "maybe": 1, "likely_not": 0},
        "band_weighted_score": 0.75, "probability_mean": 0.75,
        "semantic_survival_rate": 1.0, "same_fact_rate": 0.333333,
        "units": [],
    }
    report = _completed_report()
    report["semantic_judge"] = block
    receipt = runner.build_receipt(report)
    issues = runner.validate_receipt(receipt)
    assert any("counts sum to" in issue for issue in issues)


def test_receipt_validation_rejects_a_unit_band_disagreement():
    block = {
        "status": "completed", "pin": judge.SEMANTIC_JUDGE_PIN,
        "units_total": 1,
        "band_distribution": {"same_fact": 0, "likely": 1, "maybe": 0, "likely_not": 0},
        "band_weighted_score": 0.75, "probability_mean": 0.75,
        "semantic_survival_rate": 1.0, "same_fact_rate": 0.0,
        "units": [
            {"unit_id": "u1", "probability": 0.75, "band": "same_fact",
             "votes_yes": 3, "votes_total": 4},
        ],
    }
    report = _completed_report()
    report["semantic_judge"] = block
    receipt = runner.build_receipt(report)
    issues = runner.validate_receipt(receipt)
    assert any("disagrees with band_for_probability" in issue for issue in issues)


def test_logprob_crosscheck_pools_samples_across_sessions():
    assert judge.logprob_crosscheck_from_samples([])["available"] is False
    pooled = judge.logprob_crosscheck_from_samples([-0.5, -1.5])
    assert pooled["available"] is True
    assert pooled["token_logprob_mean"] == -1.0
