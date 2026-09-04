"""W3-b why-suite schema/corpus unit tests (epic #2080, issue #2100).

Hermetic (no DB, no network): the DM-9 planted-conflict manifest/gold
validators — including the anti-contamination guards (gold key inside the
manifest = validation error), the MANIFEST-RESOLVED gold checks (an entry
referencing a point the seed never plants, or a pointer kind the plant never
surfaces, is a validation error — schema conformance alone cannot catch
seed→gold drift), the metric aggregation collapse semantics, and the
compare/bless discipline (fixtures_hash/config/judge-pin mismatch ⇒
inconclusive, standing E2E-7 bars armed at config.reflex == graded, corpus
drift blessing requires corpus_bless).
"""

from __future__ import annotations

import pytest
from eval.why_suite import corpus, judge, schema
from eval.why_suite.schema import (
    VERDICT_INCONCLUSIVE,
    VERDICT_PASS,
    VERDICT_REGRESSION,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

BASE_CONFIG = {
    "suites": ["why_suite"],
    "mode": "full",
    "reflex": "graded",
    "holdout_excluded": False,
    "seed": 42,
    "extractor_posture": "llm",
}


def _config(**over) -> dict:
    cfg = dict(BASE_CONFIG)
    cfg.update(over)
    return cfg


def _manifest(**over) -> dict:
    m = corpus.load_manifest()
    m.update(over)
    return m


def _gold(**over) -> dict:
    g = corpus.gold_doc()
    g.update(over)
    return g


def _gold_entry(topic: str, **over) -> dict:
    """The committed gold entry for a topic (as a dict to mutate)."""
    for entry in corpus.gold_doc()["entries"]:
        if entry["point_id"] == topic:
            out = {"point_id": topic}
            for k, v in entry.items():
                out[k] = (
                    dict(v)
                    if isinstance(v, dict)
                    else ([dict(t) for t in v] if isinstance(v, list) else v)
                )
            out.update(over)
            return out
    raise KeyError(topic)


def _baseline(
    metrics: dict | None = None,
    *,
    config=None,
    judge_pin: str | None = None,
    justification: str | None = None,
) -> dict:
    cfg = dict(config or BASE_CONFIG)
    return {
        "schema_version": 1,
        "fixtures_hash": HASH_A,
        "judge_pin": judge_pin,
        "config": cfg,
        "justification": justification,
        "metrics": metrics or {},
        "history": [],
    }


# ── Committed corpus is valid + generator is deterministic ────────────────


def test_committed_manifest_valid():
    assert schema.validate_manifest(corpus.load_manifest()) == []


def test_committed_gold_valid_and_resolves_against_manifest():
    manifest = corpus.load_manifest()
    gold = corpus.gold_doc()
    assert schema.validate_gold(gold, manifest) == []
    assert len(gold["entries"]) == 40
    # Every planted topic has exactly one gold entry (full coverage — no
    # planted point is an ungraded datum).
    planted = {t for lst in manifest["topics"].values() for t in lst}
    assert {e["point_id"] for e in gold["entries"]} == planted


def test_judge_pin_format_and_prestep():
    pin = judge.judge_pin()
    assert pin.startswith("judge_why_suite_v1:")
    assert len(pin.split(":")[1]) == 64
    assert judge.assert_prompt_pinned() == pin
    # The pre-step fails closed on a drifted prompt (protocol change must be
    # a re-pin, never a silent edit).
    import importlib

    orig = judge.JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    try:
        judge.JUDGE_PROMPT_PATH.write_text(orig + "\n", encoding="utf-8")
        with pytest.raises(AssertionError, match="judge prompt drifted"):
            judge.assert_prompt_pinned()
    finally:
        judge.JUDGE_PROMPT_PATH.write_text(orig, encoding="utf-8")
        importlib.reload(judge)


# ── Manifest (fixture side) guards ─────────────────────────────────────────


def test_gold_key_in_manifest_is_validation_error():
    manifest = _manifest(gold={"entries": []})
    issues = schema.validate_manifest(manifest)
    assert any("gold key inside the manifest is a VALIDATION ERROR" in i for i in issues)


def test_manifest_rejects_unknown_family_and_duplicate_topic():
    topics = dict(corpus.load_manifest()["topics"])
    topics["ghost"] = ["ghost-topic-0"]
    issues = schema.validate_manifest(_manifest(topics=topics))
    assert any("unknown family 'ghost'" in i for i in issues)

    topics = dict(corpus.load_manifest()["topics"])
    topics["p9"] = ["p9-topic-0", "p9-topic-0"]
    issues = schema.validate_manifest(_manifest(topics=topics))
    assert any("duplicate topic keys" in i for i in issues)


def test_manifest_composition_is_jointly_pinned():
    manifest = _manifest(
        composition={
            "total": 40,
            "conflicted": 29,
            "clean": 11,
            "p9": 10,
            "decision": 5,
            "superseded": 5,
            "plain": 10,
        }
    )
    issues = schema.validate_manifest(manifest)
    assert any("composition.conflicted: expected 30" in i for i in issues)


# ── Gold ↔ manifest resolution (the drift gate) ───────────────────────────


def test_gold_referencing_unplanted_point_fails():
    """An entry whose point_id the seed never plants is a validation error
    (schema conformance alone can't catch seed→point-ID drift)."""
    entries = [e for e in corpus.gold_doc()["entries"] if e["point_id"] != "p9-topic-0"]
    entries.append(_gold_entry("p9-topic-0", point_id="plant-never-topic-7"))
    issues = schema.validate_gold(_gold(entries=entries), corpus.load_manifest())
    assert any("is not a topic the jointly-pinned seed plants" in i for i in issues)


def test_gold_clean_point_cannot_expect_contradiction_pointers():
    entry = _gold_entry("clean-topic-0")
    entry["expected"]["dig_deeper_targets"].append({"kind": "nand", "target_role": "counter"})
    issues = schema.validate_gold(
        _gold(
            entries=[e for e in corpus.gold_doc()["entries"] if e["point_id"] != "clean-topic-0"]
            + [entry]
        ),
        corpus.load_manifest(),
    )
    assert any("family 'clean' never surfaces a 'nand' pointer" in i for i in issues)


def test_gold_non_superseded_topic_cannot_expect_superseded_target():
    entry = _gold_entry("plain-topic-0")
    entry["expected"]["dig_deeper_targets"].append(
        {"kind": "superseded", "target_role": "successor"}
    )
    issues = schema.validate_gold(
        _gold(
            entries=[e for e in corpus.gold_doc()["entries"] if e["point_id"] != "plain-topic-0"]
            + [entry]
        ),
        corpus.load_manifest(),
    )
    assert any("family 'plain' never surfaces a 'superseded' pointer" in i for i in issues)


def test_gold_role_must_resolve_to_a_planted_role():
    entry = _gold_entry("decision-topic-0")
    entry["expected"]["dig_deeper_targets"] = [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
        {"kind": "tradeoff", "target_role": "option_z"},  # not planted
    ]
    issues = schema.validate_gold(
        _gold(
            entries=[e for e in corpus.gold_doc()["entries"] if e["point_id"] != "decision-topic-0"]
            + [entry]
        ),
        corpus.load_manifest(),
    )
    assert any("role 'option_z' is not a role the manifest plants" in i for i in issues)


def test_gold_clean_flag_must_match_family():
    entry = _gold_entry("p9-topic-0", clean=True)
    issues = schema.validate_gold(
        _gold(
            entries=[e for e in corpus.gold_doc()["entries"] if e["point_id"] != "p9-topic-0"]
            + [entry]
        ),
        corpus.load_manifest(),
    )
    assert any("family 'p9' is planted CONFLICTED — clean must be false" in i for i in issues)


def test_gold_partial_coverage_fails():
    entries = [e for e in corpus.gold_doc()["entries"] if e["point_id"] != "clean-topic-9"]
    issues = schema.validate_gold(_gold(entries=entries), corpus.load_manifest())
    assert any("39 entries but the manifest plants 40 topics" in i for i in issues)


# ── Metric aggregation ─────────────────────────────────────────────────────


def _point(**over) -> dict:
    row = {
        "point_id": "x",
        "topic": "x-topic-0",
        "family": "plain",
        "clean": False,
        "expected_conflict": True,
        "conflict_surfaced": True,
        "nav_correct": 2,
        "nav_total": 2,
        "support_sufficient": True,
        "tradeoff_sufficient": None,
        "false_positive": False,
    }
    row.update(over)
    return row


def test_aggregate_pools_metrics():
    rows = [
        # 3 conflicted (2 surfaced), 1 decision (tradeoff ok), 2 clean.
        _point(point_id="c1"),
        _point(point_id="c2"),
        _point(point_id="c3", conflict_surfaced=False),
        _point(point_id="d1", family="decision", expected_conflict=True, tradeoff_sufficient=True),
        _point(
            point_id="k1",
            family="clean",
            clean=True,
            expected_conflict=False,
            conflict_surfaced=None,
            tradeoff_sufficient=None,
            nav_correct=1,
            nav_total=1,
        ),
        _point(
            point_id="k2",
            family="clean",
            clean=True,
            expected_conflict=False,
            conflict_surfaced=None,
            tradeoff_sufficient=None,
            nav_correct=0,
            nav_total=1,
            nav_errors=[{"kind": "supports"}],
            false_positive=True,
        ),
    ]
    metrics = schema.aggregate_metrics(rows)
    assert metrics["conflict_surfacing_rate"] == pytest.approx(3 / 4)
    # nav: c1 2/2 + c2 2/2 + c3 2/2 + d1 2/2 + k1 1/1 + k2 0/1 = 9/10.
    assert metrics["dig_deeper_navigation_accuracy"] == pytest.approx(9 / 10)
    assert metrics["support_chain_sufficiency"] == 1.0
    assert metrics["tradeoff_sufficiency"] == 1.0
    assert metrics["false_positive_rate"] == pytest.approx(1 / 2)


def test_aggregate_empty_denominator_collapses_to_worst():
    metrics = schema.aggregate_metrics([_point(point_id="c1")])
    # No clean points graded → false-positive rate must NOT read as clean.
    assert metrics["false_positive_rate"] == 1.0
    # No decision points graded → tradeoff sufficiency must NOT read as 1.0.
    assert metrics["tradeoff_sufficiency"] == 0.0


# ── Compare / bless discipline ─────────────────────────────────────────────


def test_compare_pass_at_full_corpus_numbers():
    metrics = {
        "conflict_surfacing_rate": 1.0,
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    baseline = _baseline(
        metrics=metrics, judge_pin=judge.judge_pin(), justification="first publish"
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_A,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_PASS
    )


def base_config(baseline: dict) -> dict:
    return baseline["config"]


def test_compare_standing_bars_trip_below_floors():
    """The E2E-7 bars are armed at config.reflex == graded: a 0.94
    conflict-surfacing run is a REGRESSION even when the committed baseline
    itself sits below the floor (a bad first number records honestly but
    never legitimizes a future run at the same level)."""
    metrics = {
        "conflict_surfacing_rate": 0.94,  # < 0.95 floor
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    baseline = _baseline(
        metrics={
            "conflict_surfacing_rate": 0.94,  # committed bad first number
            "dig_deeper_navigation_accuracy": 1.0,
            "support_chain_sufficiency": 1.0,
            "tradeoff_sufficiency": 1.0,
            "false_positive_rate": 0.0,
        },
        judge_pin=judge.judge_pin(),
        justification="bad first number",
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_A,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_REGRESSION
    )


def test_compare_null_reflex_disarms_bars():
    """Parity with the W3-a harness semantics: under a config.reflex == null
    baseline the floors are inert (the honest pre-gate record)."""
    metrics = {
        "conflict_surfacing_rate": 0.9,
        "dig_deeper_navigation_accuracy": 0.9,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    baseline = _baseline(
        metrics=metrics,
        config=_config(reflex="null"),
        judge_pin=judge.judge_pin(),
        justification="null-reflex record",
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_A,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_PASS
    )


def test_compare_clean_false_positive_is_regression():
    metrics = {
        "conflict_surfacing_rate": 1.0,
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.1,  # clean point invented a contradiction
    }
    baseline = _baseline(
        metrics=dict(metrics), judge_pin=judge.judge_pin(), justification="first publish"
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_A,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_REGRESSION
    )


def test_compare_hash_config_pin_mismatches_are_inconclusive():
    metrics = {
        "conflict_surfacing_rate": 1.0,
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    baseline = _baseline(
        metrics=metrics, judge_pin=judge.judge_pin(), justification="first publish"
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_B,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_INCONCLUSIVE
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=_config(seed=7),
            run_fixtures_hash=HASH_A,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_INCONCLUSIVE
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_A,
            run_judge_pin="judge_why_suite_v1:" + "0" * 64,
        )
        == VERDICT_INCONCLUSIVE
    )


def test_directional_regression_vs_committed():
    metrics = {
        "conflict_surfacing_rate": 0.97,
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    committed = {
        "conflict_surfacing_rate": 1.0,  # committed is better
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    baseline = _baseline(
        metrics=committed, judge_pin=judge.judge_pin(), justification="first publish"
    )
    assert (
        schema.compare_run(
            metrics,
            baseline,
            resolved_config=base_config(baseline),
            run_fixtures_hash=HASH_A,
            run_judge_pin=judge.judge_pin(),
        )
        == VERDICT_REGRESSION
    )


def test_bless_rejects_corpus_drift_without_corpus_bless():
    run = {
        "date": "2026-09-06T00:00:00Z",
        "fixtures_hash": HASH_B,
        "judge_pin": judge.judge_pin(),
        "config": BASE_CONFIG,
        "metrics": {
            "conflict_surfacing_rate": 1.0,
            "dig_deeper_navigation_accuracy": 1.0,
            "support_chain_sufficiency": 1.0,
            "tradeoff_sufficiency": 1.0,
            "false_positive_rate": 0.0,
        },
        "failure_classes": [],
    }
    previous = _baseline(
        metrics=run["metrics"], judge_pin=judge.judge_pin(), justification="first publish"
    )
    with pytest.raises(ValueError, match="corpus drift"):
        schema.bless_baseline(previous, run, justification="regen corpus")
    # Intentional regeneration is the sanctioned corpus_bless marker.
    blessed = schema.bless_baseline(
        previous, run, justification="intentional corpus regen", corpus_bless=True
    )
    assert blessed["fixtures_hash"] == HASH_B


def test_bless_rejects_pin_change_without_protocol_bless():
    run = {
        "date": "2026-09-06T00:00:00Z",
        "fixtures_hash": HASH_A,
        "judge_pin": "judge_why_suite_v1:" + "0" * 64,
        "config": BASE_CONFIG,
        "metrics": {
            "conflict_surfacing_rate": 1.0,
            "dig_deeper_navigation_accuracy": 1.0,
            "support_chain_sufficiency": 1.0,
            "tradeoff_sufficiency": 1.0,
            "false_positive_rate": 0.0,
        },
        "failure_classes": [],
    }
    previous = _baseline(
        metrics=run["metrics"], judge_pin=judge.judge_pin(), justification="first publish"
    )
    with pytest.raises(ValueError, match="judge-protocol change"):
        schema.bless_baseline(previous, run, justification="re-pin")
    blessed = schema.bless_baseline(
        previous, run, justification="deliberate judge re-pin", protocol_bless=True
    )
    assert blessed["history"][-1].get("protocol_change") is True


def test_bless_requires_full_metric_vocabulary():
    run = {
        "date": "2026-09-06T00:00:00Z",
        "fixtures_hash": HASH_A,
        "judge_pin": judge.judge_pin(),
        "config": BASE_CONFIG,
        "metrics": {"conflict_surfacing_rate": 1.0},
        "failure_classes": [],
    }
    previous = _baseline(judge_pin=None)
    with pytest.raises(ValueError, match="missing graded dimensions"):
        schema.bless_baseline(previous, run, justification="first publish")


def test_validate_baseline_published_requires_pin_and_justification():
    metrics = {
        "conflict_surfacing_rate": 1.0,
        "dig_deeper_navigation_accuracy": 1.0,
        "support_chain_sufficiency": 1.0,
        "tradeoff_sufficiency": 1.0,
        "false_positive_rate": 0.0,
    }
    issues = schema.validate_baseline(_baseline(metrics=metrics, judge_pin=None))
    assert any("requires a pinned judge" in i for i in issues)
    issues = schema.validate_baseline(_baseline(metrics=metrics, judge_pin=judge.judge_pin()))
    assert any("requires the blessing justification" in i for i in issues)
    pending = _baseline(judge_pin=None)
    assert schema.validate_baseline(pending) == []
    # Pending must NOT carry a pin (a pin-less target would make compare's
    # pin guard inert).
    issues = schema.validate_baseline(_baseline(judge_pin=judge.judge_pin()))
    assert any("pending baseline (empty metrics) must have a null judge_pin" in i for i in issues)
