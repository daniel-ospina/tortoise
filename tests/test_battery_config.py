"""Task 2 tests — config loaders: corpus schema, gold verify, empty-corpus
guard, [cal] hash, budget estimate."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from battery.config import (
    BudgetConfig,
    load_arms,
    load_budget,
    load_corpus,
    load_thresholds,
    scenarios_by_tier,
)
from battery.enums import Tier
from battery.exceptions import EmptyCorpus, GoldVerificationError

CONFIG = Path(__file__).parent.parent / "battery" / "config"
GOLDS = Path(__file__).parent.parent / "battery" / "golds"


def _write_corpus(tmp_path, scenarios: list[dict]) -> Path:
    p = tmp_path / "corpus.yaml"
    p.write_text(yaml.safe_dump({"scenarios": scenarios}), encoding="utf-8")
    return p


def _gold(tmp_path, name: str = "gold.txt", content: str = "gold answer") -> dict:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return {"path": name, "sha256": hashlib.sha256(content.encode()).hexdigest()}


@pytest.fixture()
def default_corpus():
    return load_corpus(CONFIG / "corpus.yaml", gold_base=GOLDS)


class TestCorpusSchema:
    def test_production_corpus_loads(self, default_corpus):
        # #1407 production corpus replaces the #1406 2-scenario smoke corpus.
        assert len(default_corpus) >= 100
        assert default_corpus[0].tier is Tier.PROBE
        assert default_corpus[0].prompt_pack  # prompt -> prompt_pack normalized
        # k is pinned per planted contradiction (plan §4; production schema
        # nests it in planted_contradictions[].k — normalized to
        # contradiction_pairs[].injection_turn by the loader).
        ct = [s for s in default_corpus
              if s.task_type == "contradiction" and s.contradiction_pairs]
        assert ct and all(
            p.injection_turn == 5 for s in ct for p in s.contradiction_pairs)

    def test_type_violation_rejected(self, tmp_path):
        p = _write_corpus(tmp_path, [{"id": "x", "tier": "nonsense", "family": "f"}])
        with pytest.raises(Exception):  # noqa: B017
            load_corpus(p)

    def test_optional_fields_ok(self, tmp_path):
        sc = [{"id": "a", "tier": "probe", "family": "f",
               "split": "waves", "evidence_scripts": ["e1"]}]
        loaded = load_corpus(_write_corpus(tmp_path, sc))
        assert loaded[0].split == "waves"
        assert loaded[0].evidence_scripts == ("e1",)

    def test_tier_filter(self, default_corpus):
        probes = scenarios_by_tier(default_corpus, Tier.PROBE)
        streams = scenarios_by_tier(default_corpus, Tier.STREAM)
        assert len(probes) > 0 and len(streams) > 0
        assert all(s.tier is Tier.PROBE for s in probes)

    def test_episode_context_has_no_gold(self, default_corpus):
        with_gold = next(s for s in default_corpus if s.golds())
        ctx = str(with_gold.to_episode_context())
        assert with_gold.golds()[0] not in ctx  # gold text absent


class TestGoldBoundary:
    def test_golds_only_via_golds_surface(self, default_corpus):
        with_gold = next(s for s in default_corpus if s.golds())
        golds = with_gold.golds()
        assert len(golds) == 1 and golds[0]  # sealed gold accessible scorer-side

    def test_missing_gold_fails_closed(self, tmp_path):
        sc = [{"id": "a", "tier": "probe", "family": "f",
               "gold_ref": {"path": "does-not-exist.txt", "sha256": "abc"}}]
        with pytest.raises(GoldVerificationError):
            load_corpus(_write_corpus(tmp_path, sc), gold_base=tmp_path)

    def test_sha_mismatch_fails_closed(self, tmp_path):
        g = _gold(tmp_path)
        g["sha256"] = "0" * 64
        sc = [{"id": "a", "tier": "probe", "family": "f", "gold_ref": g}]
        with pytest.raises(GoldVerificationError):
            load_corpus(_write_corpus(tmp_path, sc), gold_base=tmp_path)

    def test_present_gold_verifies(self, tmp_path):
        g = _gold(tmp_path)
        sc = [{"id": "a", "tier": "probe", "family": "f", "gold_ref": g}]
        loaded = load_corpus(_write_corpus(tmp_path, sc), gold_base=tmp_path)
        assert loaded[0].gold_ref is not None


class TestEmptyCorpus:
    def test_zero_scenarios_raises(self, tmp_path):
        with pytest.raises(EmptyCorpus):
            load_corpus(_write_corpus(tmp_path, []))


class TestCalTable:
    def test_hash_stable_across_order(self):
        t1 = load_thresholds(CONFIG / "thresholds.yaml")
        from battery.config.thresholds import ThresholdsConfig
        # order reversal of BOTH the [cal] rows and the determinism
        # tolerance rows (Task 7 fold-in) must not drift the hash
        t2 = ThresholdsConfig(
            cal_rows=tuple(reversed(t1.cal_rows)),
            determinism_tolerances=tuple(reversed(t1.determinism_tolerances)))
        assert t1.cal_table_hash() == t2.cal_table_hash()

    def test_hash_changes_on_value_change(self):
        from battery.config.thresholds import ThresholdsConfig
        t1 = ThresholdsConfig(cal_rows=(("m", "a", 0.5),))
        t2 = ThresholdsConfig(cal_rows=(("m", "a", 0.6),))
        assert t1.cal_table_hash() != t2.cal_table_hash()

    def test_epsilon_default(self):
        t = load_thresholds(CONFIG / "thresholds.yaml")
        assert t.determinism_epsilon == 1e-6


class TestDeterminismTolerances:
    """#2284 Task 7 — E2E-7.1 re-scope: determinism.tolerances resolve from
    thresholds.yaml (never a test-local constant) and fold into the same
    cal-table hash the `calibrate --print` route prints."""

    def test_tolerances_resolve_from_thresholds_yaml(self):
        t = load_thresholds(CONFIG / "thresholds.yaml")
        tols = dict(t.determinism_tolerances)
        # the seeded per-metric rows exist and sit at the transcript-locked
        # epsilon floor — every value asserted AGAINST the loaded epsilon,
        # never a literal constant in the test
        assert tols, "determinism.tolerances must not be empty"
        assert all(v == t.determinism_epsilon for v in tols.values())
        # the measured mock-lane metrics are all seeded (transcript-locked
        # derived/objective — measured |Δ| = 0.0, ≤ the epsilon floor)
        for mid in ("n_turns", "n_tool_calls", "re_derivations",
                    "total_tokens", "outcome_ok", "outcome_failed"):
            assert mid in tols

    def test_tolerance_table_folds_into_cal_table_hash(self):
        t1 = load_thresholds(CONFIG / "thresholds.yaml")
        from battery.config.thresholds import ThresholdsConfig
        # tolerance rows participate in the canonical hash: dropping them
        # drifts the hash, a single tolerance re-lock drifts it, and order
        # is canonical (no false drift)
        assert t1.determinism_tolerances
        without = ThresholdsConfig(cal_rows=t1.cal_rows)
        assert without.cal_table_hash() != t1.cal_table_hash()
        moved = ThresholdsConfig(
            cal_rows=t1.cal_rows,
            determinism_tolerances=tuple(reversed(t1.determinism_tolerances)))
        assert moved.cal_table_hash() == t1.cal_table_hash()
        row = t1.determinism_tolerances[0]
        relocked = ThresholdsConfig(
            cal_rows=t1.cal_rows,
            determinism_tolerances=(
                (row[0], row[1] * 2), *t1.determinism_tolerances[1:]))
        assert relocked.cal_table_hash() != t1.cal_table_hash()

    def test_calibrate_print_route_covers_tolerance_table(self):
        """The hash `battery calibrate --print` prints (report.calibrate.
        cal_table_hash) must fold the determinism tolerance rows in — a
        tolerance re-lock is a reviewable table change, never silent."""
        from battery.config.thresholds import ThresholdsConfig
        from battery.report.calibrate import cal_table_hash as route_hash
        t1 = load_thresholds(CONFIG / "thresholds.yaml")
        # the route delegates to the same canonical implementation…
        assert route_hash(t1.cal_rows, t1.determinism_tolerances) == \
            ThresholdsConfig(cal_rows=t1.cal_rows,
                             determinism_tolerances=t1.determinism_tolerances
                             ).cal_table_hash()
        # …and the printed hash drifts when the tolerance table is dropped
        # or re-locked (fold-in is visible on the route)
        assert route_hash(t1.cal_rows) != route_hash(
            t1.cal_rows, t1.determinism_tolerances)

    def test_arms_tokens_carry_measured_after_exposure_note(self):
        """arms.yaml expected_tokens_per_episode values stay provisional
        until exposure part 1 (#2284 Task 8): every non-mock arm's token
        field carries the measured_after_exposure annotation (comments
        only — no schema change; mock's 64 is a lane cap, not a budget
        guess, and is exempt)."""
        text = (CONFIG / "arms.yaml").read_text(encoding="utf-8")
        from battery.config import load_arms
        arms = load_arms(CONFIG / "arms.yaml")
        blocks = text.split("- arm_id:")
        for arm_id in sorted(arms):
            if arm_id == "mock":
                continue
            block = next(b for b in blocks[1:]
                         if b.splitlines()[0].strip() == arm_id)
            assert "expected_tokens_per_episode:" in block
            assert "measured_after_exposure" in block, \
                f"arm {arm_id}: token field not annotated measured_after_exposure"

    def test_arms_token_guess_annotations_row_self_consistent(self):
        """PR #2341 review round 2, P2: per-row annotations are
        self-consistent — the "(800 tok/ep guess)" parenthetical sits only
        on a4's row (the only arm whose value IS 800); the corpus-level 800
        guess explanation lives once in the header, never on non-800 rows
        (a0=500, a1=600, a2/a2b=700, a3=400)."""
        text = (CONFIG / "arms.yaml").read_text(encoding="utf-8")
        from battery.config import load_arms
        arms = load_arms(CONFIG / "arms.yaml")
        values = {a: arms[a].expected_tokens_per_episode for a in arms}
        blocks = text.split("- arm_id:")
        header = blocks[0]
        for arm_id, value in values.items():
            if arm_id == "mock":
                continue  # lane cap, no budget guess annotation
            block = next(b for b in blocks[1:]
                         if b.splitlines()[0].strip() == arm_id)
            guess = "(800 tok/ep guess)" in block
            if value == 800:
                assert guess, f"arm {arm_id}: 800-value row should carry the guess"
            else:
                assert not guess, \
                    f"arm {arm_id}: value {value} annotated with the 800 guess"
        # the corpus-level guess explanation lives in the header exactly once
        assert header.count("800 tok/ep guess") == 1


class TestArmsAndBudget:
    def test_arms_load(self):
        arms = load_arms(CONFIG / "arms.yaml")
        assert set(arms) == {"mock", "a0", "a1", "a2", "a2b", "a3", "a4"}
        assert arms["mock"].price_per_1k_usd == 0.0

    def test_budget_estimate(self):
        budget = load_budget(CONFIG / "budget.yaml")
        assert budget.over_budget(n_episodes=10, estimated_cost_usd=0.01) is None

    def test_budget_cost_refusal(self):
        b = BudgetConfig(max_estimated_cost_usd=1.0)
        assert b.over_budget(n_episodes=10, estimated_cost_usd=2.0) is not None

    def test_budget_max_episodes_wins(self):
        b = BudgetConfig(max_episodes=5)
        # --max-episodes 10 > budget 5 → refuse (budget wins)
        assert b.over_budget(n_episodes=6, estimated_cost_usd=0.0,
                             requested_max_episodes=10) is not None
        # --max-episodes 3 < budget 5 → refuse at 6 episodes
        assert b.over_budget(n_episodes=4, estimated_cost_usd=0.0,
                             requested_max_episodes=3) is not None
