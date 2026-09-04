"""W3 harness schema/grading unit tests (issue #2099 W3-a).

Hermetic (no DB, no network): the DM-6/7 fixture/gold validators, the
harness metric aggregation, and the compare/bless discipline — including
the anti-gaming guards (gold-key-in-fixture rejection, config-mismatch ⇒
inconclusive, reflex-gated standing bars, the always-live isolation gate).
"""
from __future__ import annotations

import pytest
from eval.harness import grading, schema
from eval.harness.schema import (
    FALSE_FIRE_TOLERANCE,
    KTA_FAILURE_TOLERANCE,
    PUSH_PRECISION_FLOOR,
    VERDICT_INCONCLUSIVE,
    VERDICT_PASS,
    VERDICT_REGRESSION,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

BASE_CONFIG = {
    "suites": sorted(schema.SUITE_VALUES),
    "mode": "BPRE",
    "reflex": "null",
    "holdout_excluded": True,
    "seed": 42,
    "extractor_posture": "llm",
}


def _fixture(**over) -> dict:
    fx = {
        "suite": "know_to_ask", "seed": 1, "holdout": False,
        "turns": [
            {"role": "user", "content": "What did Alice say about the deal?"},
            {"role": "assistant", "content": "Alice flagged the pricing as aggressive."},
        ],
    }
    fx.update(over)
    return fx


def _gold(**over) -> dict:
    g = {
        "suite": "know_to_ask", "schema_version": 1, "session_id": "kta_x",
        "per_turn": [
            {"turn": 1, "should_retrieve": True, "pointers": ["pt_a"]},
            {"turn": 2, "should_retrieve": False},
        ],
    }
    g.update(over)
    return g


def _baseline(metrics: dict | None = None, *, config=None, reflex: str = "null",
              judge_pin: str = "w3-volunteering-memory-mechanical-v1",
              justification: str = "first publish") -> dict:
    cfg = dict(config or BASE_CONFIG)
    cfg["reflex"] = reflex
    return {
        "schema_version": 1,
        "fixtures_hash": HASH_A,
        "judge_pin": judge_pin,
        "config": cfg,
        "justification": justification,
        "metrics": metrics or {},
        "history": [],
    }


def _config(reflex: str = "null") -> dict:
    cfg = dict(BASE_CONFIG)
    cfg["reflex"] = reflex
    return cfg


# ── Fixture/gold validation ────────────────────────────────────────────────

class TestFixtureValidation:
    def test_valid_fixture_passes(self):
        assert schema.validate_fixture(_fixture()) == []

    def test_gold_key_in_fixture_is_rejected(self):
        fx = _fixture(gold=_gold())
        issues = schema.validate_fixture(fx)
        assert any("unexpected key 'gold'" in i for i in issues)

    def test_empty_turns_rejected(self):
        assert any("non-empty list" in i
                   for i in schema.validate_fixture(_fixture(turns=[])))

    def test_unknown_turn_role_rejected(self):
        fx = _fixture(turns=[{"role": "system", "content": "hi"}])
        assert any("role" in i for i in schema.validate_fixture(fx))

    def test_isolation_fixture_requires_team(self):
        fx = _fixture(suite="isolation", turns=[{"role": "user", "content": "x"}])
        assert any("requires a fixture.team" in i for i in schema.validate_fixture(fx))
        fx2 = _fixture(suite="isolation", team="team_a",
                       turns=[{"role": "user", "content": "x"}])
        assert schema.validate_fixture(fx2) == []

    def test_harness_visible_fields_only(self):
        fx = _fixture(session_id="leak", internal_note="hidden")
        assert any("unexpected key 'internal_note'" in i
                   for i in schema.validate_fixture(fx))


class TestGoldValidation:
    def test_valid_per_turn_gold_passes(self):
        assert schema.validate_gold(_gold()) == []

    def test_non_retrieve_turn_cannot_carry_pointers(self):
        g = _gold(per_turn=[
            {"turn": 1, "should_retrieve": False, "pointers": ["pt_a"]},
        ])
        assert any("cannot carry pointers" in i for i in schema.validate_gold(g))

    def test_turn_out_of_range_rejected_when_fixture_given(self):
        fx = _fixture(turns=[{"role": "user", "content": "q"}])
        g = _gold(per_turn=[{"turn": 5, "should_retrieve": True}])
        assert any("exceeds fixture turn count" in i
                   for i in schema.validate_gold(g, fixture=fx))

    def test_suite_mismatch_between_fixture_and_gold(self):
        fx = _fixture(suite="push")
        g = _gold(suite="know_to_ask")
        issues = schema.fixture_gold_consistent(fx, g, "kta_x")
        assert any("suite mismatch" in i for i in issues)

    def test_gold_session_id_must_match_stem(self):
        fx = _fixture()
        g = _gold(session_id="other")
        issues = schema.fixture_gold_consistent(fx, g, "kta_x")
        assert any("session_id mismatch" in i for i in issues)

    def test_write_back_gold_shape(self):
        g = {
            "suite": "write_back", "schema_version": 1, "session_id": "wb_x",
            "write_back": {"planted_points": ["anchor one"],
                           "provenance_required": True},
        }
        assert schema.validate_gold(g) == []

    def test_continuity_gold_shape(self):
        g = {
            "suite": "continuity", "schema_version": 1, "session_id": "cw_x",
            "continuity": {"writer_session": "cw_x_writer",
                           "reader_planted": ["decision"],
                           "reader_queries": ["what did we decide?"]},
        }
        assert schema.validate_gold(g) == []


# ── Metric aggregation ─────────────────────────────────────────────────────

class TestAggregation:
    def test_pooled_aggregation(self):
        results = [
            {"kta": {"missed": 1, "should": 2},
             "false_fire": {"fires": 0, "silent_required": 3},
             "push": {"prec_num": 2, "prec_den": 2,
                      "recall_num": 2, "recall_den": 4},
             "write_back": {"survived": 1, "total": 1},
             "continuity": {"surfaced": 1, "total": 1},
             "isolation": {"violations": 0}},
            {"kta": {"missed": 0, "should": 1},
             "false_fire": {"fires": 1, "silent_required": 2},
             "push": {"prec_num": 0, "prec_den": 1,
                      "recall_num": 0, "recall_den": 1},
             "write_back": {"survived": 0, "total": 1},
             "continuity": {"surfaced": 0, "total": 2},
             "isolation": {"violations": 1}},
        ]
        metrics = schema.aggregate_metrics(results)
        assert metrics["know_to_ask_failure_rate"] == pytest.approx(1 / 3)
        assert metrics["false_fire_rate"] == pytest.approx(1 / 5)
        assert metrics["push_precision"] == pytest.approx(2 / 3)
        assert metrics["push_recall"] == pytest.approx(2 / 5)
        assert metrics["write_back_fidelity"] == pytest.approx(0.5)
        assert metrics["continuity_recall"] == pytest.approx(1 / 3)
        assert metrics["source_isolation_violations"] == 1

    def test_empty_denominator_collapses_to_worst(self):
        """An empty denominator collapses to the metric's WORST value — a
        suite with no graded demand must never read as a clean pass
        (review round-1 P1/C fix: minimize rates collapse to 1.0, maximize
        to 0.0)."""
        metrics = schema.aggregate_metrics([])
        assert metrics["know_to_ask_failure_rate"] == 1.0
        assert metrics["false_fire_rate"] == 1.0
        assert metrics["push_precision"] == 0.0
        assert metrics["source_isolation_violations"] == 0


# ── Graders ────────────────────────────────────────────────────────────────

class TestGraders:
    def test_kta_null_reflex_misses_everything(self):
        gold = _gold(per_turn=[
            {"turn": 1, "should_retrieve": True, "pointers": ["pt_a"]},
            {"turn": 2, "should_retrieve": False},
        ])
        result = grading.grade_kta("s", gold, injected={})
        assert result["kta"] == {"missed": 1, "should": 1}
        assert result["false_fire"] == {"fires": 0, "silent_required": 1}

    def test_kta_injection_prevents_miss(self):
        gold = _gold(per_turn=[
            {"turn": 1, "should_retrieve": True},
            {"turn": 2, "should_retrieve": False},
        ])
        result = grading.grade_kta("s", gold, injected={1: ["pt_a"]})
        assert result["kta"]["missed"] == 0

    def test_kta_false_fire_detected(self):
        gold = _gold(per_turn=[
            {"turn": 1, "should_retrieve": False},
        ])
        result = grading.grade_kta("s", gold, injected={1: ["pt_a"]})
        assert result["false_fire"] == {"fires": 1, "silent_required": 1}

    def test_push_budget_and_precision(self):
        gold = {"suite": "push", "per_turn": [
            {"turn": 1, "should_retrieve": True, "pointers": ["a", "b"]},
        ]}
        # Injects a + c (c not gold-acceptable) under budget 3.
        result = grading.grade_push("s", gold, injected={1: ["a", "c"]}, budget=3)
        assert result["push"] == {"prec_num": 1, "prec_den": 2,
                                  "recall_num": 1, "recall_den": 2}

    def test_push_duplicate_injection_cannot_exceed_recall_1(self):
        """Second-model P2: duplicate injected pointer ids must not over-count
        recall past 1.0 (recall measures unique GOLD coverage)."""
        gold = {"suite": "push", "per_turn": [
            {"turn": 1, "should_retrieve": True, "pointers": ["a"]},
        ]}
        result = grading.grade_push("s", gold, injected={1: ["a", "a", "a"]})
        assert result["push"]["recall_num"] == 1
        assert result["push"]["recall_den"] == 1
        assert result["push"]["prec_num"] == 1
        assert result["push"]["prec_den"] == 1  # deduped before the budget

    def test_write_back_fidelity_and_provenance(self):
        gold = {"suite": "write_back", "write_back": {
            "planted_points": ["alpha decision", "beta decision"],
            "provenance_required": True,
        }}
        points = [
            {"content": "the alpha decision stands", "provenance_present": True},
            {"content": "gamma unrelated", "provenance_present": True},
        ]
        result = grading.grade_write_back("s", gold, points)
        assert result["write_back"]["survived"] == 1
        assert result["write_back"]["total"] == 2
        assert result["write_back"]["missing"] == ["beta decision"]

    def test_write_back_unprovenanced_match_does_not_survive(self):
        """Review round-1 P1: with provenance_required, a content match whose
        provenance was stripped is NOT a fidelity survival — a provenance-
        stripping write path must not pass fidelity 1.0."""
        gold = {"suite": "write_back", "write_back": {
            "planted_points": ["alpha decision"],
            "provenance_required": True,
        }}
        stripped = [{"content": "the alpha decision stands",
                     "provenance_present": False}]
        result = grading.grade_write_back("s", gold, stripped)
        assert result["write_back"]["survived"] == 0
        assert result["write_back"]["unprovenanced"] == 1
        assert result["write_back"]["missing"] == []

    def test_push_courtesy_fire_is_a_false_fire(self):
        """Review round-1 P2: an injection at a should_retrieve:false push
        turn is a false fire (the push seam's anti-gaming surface)."""
        gold = {"suite": "push", "per_turn": [
            {"turn": 1, "should_retrieve": True, "pointers": ["a"]},
            {"turn": 2, "should_retrieve": False},
        ]}
        result = grading.grade_push("s", gold, injected={1: ["a"], 2: ["a"]})
        assert result["push"]["prec_num"] == 1
        assert result["push"]["prec_den"] == 1  # only retrieve-turn injections scored
        assert result["false_fire"] == {"fires": 1, "silent_required": 1}

    def test_continuity_surfaces_planted_anchors(self):
        gold = {"suite": "continuity", "continuity": {
            "writer_session": "w", "reader_planted": ["exponential backoff"],
            "reader_queries": ["q"],
        }}
        result = grading.grade_continuity(
            "s", gold, ["we moved ingest retry to exponential backoff with a 5x cap."]
        )
        assert result["continuity"]["surfaced"] == 1
        assert result["continuity"]["total"] == 1

    def test_isolation_detects_cross_team_leak(self):
        gold = {"suite": "isolation", "teams": {
            "team_a": {"anchors": ["alpha fact"]},
            "team_b": {"anchors": ["beta fact"]},
        }}
        leaked = grading.grade_isolation(
            "s", gold,
            [{"content": "alpha fact is true", "provenance_present": True},
             {"content": "beta fact leaked in", "provenance_present": True}],
            own_team="team_a",
        )
        assert leaked["isolation"]["violations"] == 1
        clean = grading.grade_isolation(
            "s", gold,
            [{"content": "alpha fact is true", "provenance_present": True}],
            own_team="team_a",
        )
        assert clean["isolation"]["violations"] == 0


# ── compare_run / bless discipline ─────────────────────────────────────────

class TestCompare:
    def test_pending_baseline_inconclusive(self):
        baseline = _baseline(metrics={})
        assert schema.compare_run(
            schema.aggregate_metrics([]), baseline,
            resolved_config=BASE_CONFIG, run_fixtures_hash=HASH_A,
        ) == VERDICT_INCONCLUSIVE

    def test_hash_mismatch_inconclusive(self):
        baseline = _baseline(metrics={"write_back_fidelity": 1.0})
        assert schema.compare_run(
            {"write_back_fidelity": 1.0}, baseline,
            resolved_config=BASE_CONFIG, run_fixtures_hash=HASH_B,
        ) == VERDICT_INCONCLUSIVE

    def test_config_mismatch_inconclusive(self):
        baseline = _baseline(metrics={"write_back_fidelity": 1.0})
        other_config = dict(BASE_CONFIG)
        other_config["seed"] = 7
        assert schema.compare_run(
            {"write_back_fidelity": 1.0}, baseline,
            resolved_config=other_config, run_fixtures_hash=HASH_A,
        ) == VERDICT_INCONCLUSIVE

    def test_worse_direction_regresses(self):
        baseline = _baseline(metrics={"write_back_fidelity": 1.0})
        assert schema.compare_run(
            {"write_back_fidelity": 0.5}, baseline,
            resolved_config=BASE_CONFIG, run_fixtures_hash=HASH_A,
        ) == VERDICT_REGRESSION

    def test_isolation_gate_live_under_null_reflex(self):
        baseline = _baseline(
            {"write_back_fidelity": 1.0, "continuity_recall": 1.0},
            reflex="null",
        )
        metrics = {"write_back_fidelity": 1.0, "continuity_recall": 1.0,
                   "source_isolation_violations": 2}
        assert schema.compare_run(
            metrics, baseline,
            resolved_config=BASE_CONFIG, run_fixtures_hash=HASH_A,
        ) == VERDICT_REGRESSION

    def test_kta_bar_inactive_under_null_reflex(self):
        baseline = _baseline(
            {"know_to_ask_failure_rate": 1.0}, reflex="null",
        )
        # Same bad kta number as committed → not worse → PASS (reflex null).
        assert schema.compare_run(
            {"know_to_ask_failure_rate": 1.0}, baseline,
            resolved_config=BASE_CONFIG, run_fixtures_hash=HASH_A,
        ) == VERDICT_PASS

    def test_kta_bar_active_under_graded_reflex(self):
        baseline = _baseline(
            {"know_to_ask_failure_rate": 1.0}, reflex="graded",
        )
        graded_cfg = _config(reflex="graded")
        # Committed number is itself over the 0.00 bar — never legitimized.
        assert schema.compare_run(
            {"know_to_ask_failure_rate": 1.0}, baseline,
            resolved_config=graded_cfg, run_fixtures_hash=HASH_A,
        ) == VERDICT_REGRESSION
        # A run at the bar passes.
        assert schema.compare_run(
            {"know_to_ask_failure_rate": KTA_FAILURE_TOLERANCE}, baseline,
            resolved_config=graded_cfg, run_fixtures_hash=HASH_A,
        ) == VERDICT_PASS

    def test_false_fire_and_push_floors_gated_on_graded(self):
        baseline = _baseline(
            {"false_fire_rate": 0.05, "push_precision": 0.5}, reflex="graded",
        )
        graded_cfg = _config(reflex="graded")
        metrics = {"false_fire_rate": FALSE_FIRE_TOLERANCE,
                   "push_precision": PUSH_PRECISION_FLOOR}
        assert schema.compare_run(
            metrics, baseline,
            resolved_config=graded_cfg, run_fixtures_hash=HASH_A,
        ) == VERDICT_PASS
        assert schema.compare_run(
            {"false_fire_rate": 0.10, "push_precision": 1.0}, baseline,
            resolved_config=graded_cfg, run_fixtures_hash=HASH_A,
        ) == VERDICT_REGRESSION


class TestBless:
    FULL_METRICS = schema.aggregate_metrics([])

    def _run(self, **over) -> dict:
        run = {
            "date": "2026-09-03T00:00:00Z",
            "fixtures_hash": HASH_A,
            "judge_pin": "w3-volunteering-memory-mechanical-v1",
            "config": BASE_CONFIG,
            "metrics": self.FULL_METRICS,
            "failure_classes": ["no-reflex"],
        }
        run.update(over)
        return run

    def test_first_publish_blesses_pending(self):
        pending = _baseline(metrics={})
        blessed = schema.bless_baseline(
            pending, self._run(), justification="first numbers"
        )
        assert blessed["metrics"] == self.FULL_METRICS
        assert len(blessed["history"]) == 1

    def test_bless_requires_justification_and_pin(self):
        pending = _baseline(metrics={})
        with pytest.raises(ValueError, match="justification"):
            schema.bless_baseline(pending, self._run(), justification="  ")
        no_pin = self._run(judge_pin=None)
        with pytest.raises(ValueError, match="judge_pin"):
            schema.bless_baseline(pending, no_pin, justification="x")

    def test_bless_rejects_corpus_drift_without_corpus_bless(self):
        pending = _baseline(metrics=self.FULL_METRICS)
        with pytest.raises(ValueError, match="corpus drift"):
            schema.bless_baseline(
                pending, self._run(fixtures_hash=HASH_B),
                justification="drift",
            )

    def test_bless_regression_records_verdict_not_raise(self):
        bad_metrics = dict(self.FULL_METRICS)
        bad_metrics["write_back_fidelity"] = 0.0
        pending = _baseline(
            {**self.FULL_METRICS, "write_back_fidelity": 1.0},
        )
        # A regression IS blessable with justification (fix-wave protocol: a
        # bad number records honestly) — the verdict lands in history.
        blessed = schema.bless_baseline(
            pending, self._run(metrics=bad_metrics),
            justification="write-path regression recorded per fix-wave",
        )
        assert blessed["history"][-1]["verdict"] == VERDICT_REGRESSION
        # An inconclusive compare (drift) is NOT blessable — always raises
        # (the corpus-drift guard fires before compare).
        with pytest.raises(ValueError, match="corpus drift"):
            schema.bless_baseline(
                pending, self._run(fixtures_hash=HASH_B, corpus_bless=False),
                justification="drifted",
            )

    def test_reflex_repin_requires_protocol_bless(self):
        pending = _baseline(metrics=self.FULL_METRICS, reflex="null")
        graded_cfg = dict(BASE_CONFIG)
        graded_cfg["reflex"] = "graded"
        with pytest.raises(ValueError, match="config differs"):
            schema.bless_baseline(
                pending, self._run(config=graded_cfg), justification="reflex"
            )
        blessed = schema.bless_baseline(
            pending, self._run(config=graded_cfg),
            justification="W4 reflex landed; graded bars now live",
            protocol_bless=True,
        )
        assert blessed["config"]["reflex"] == "graded"
        assert blessed["history"][-1].get("reflex_graded") is True


class TestValidateBaseline:
    def test_pending_baseline_valid(self):
        # Pending: empty metrics ⇒ null judge_pin AND null justification.
        pending = _baseline(metrics={}, judge_pin=None, justification=None)
        assert schema.validate_baseline(pending) == []

    def test_published_requires_pin_and_justification(self):
        full = schema.aggregate_metrics([])
        no_pin = _baseline(metrics=full, judge_pin=None)
        no_just = _baseline(metrics=full, justification=None)
        assert any("requires a pinned judge" in i
                   for i in schema.validate_baseline(no_pin))
        assert any("requires the blessing justification" in i
                   for i in schema.validate_baseline(no_just))
        # And a pending baseline must not carry a pin/justification.
        stamped = _baseline(metrics={})
        assert any("pending baseline" in i for i in schema.validate_baseline(stamped))

    def test_published_baseline_must_have_full_metric_vocabulary(self):
        partial = _baseline(metrics={"write_back_fidelity": 1.0})
        issues = schema.validate_baseline(partial)
        assert any("missing graded dimensions" in i for i in issues)

    def test_unknown_metric_rejected(self):
        baseline = _baseline(metrics={**schema.aggregate_metrics([]),
                                     "made_up": 1.0})
        assert any("unknown metric 'made_up'" in i
                   for i in schema.validate_baseline(baseline))

    def test_metric_range_checked(self):
        bad = _baseline(metrics={**schema.aggregate_metrics([]),
                                 "false_fire_rate": 1.7})
        assert any("fraction in [0, 1]" in i for i in schema.validate_baseline(bad))
