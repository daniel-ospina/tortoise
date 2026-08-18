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
    def test_smoke_corpus_loads(self, default_corpus):
        assert [s.id for s in default_corpus] == ["r1-001", "r1-002"]
        assert default_corpus[0].tier is Tier.PROBE
        assert default_corpus[0].contradiction_pairs[0].injection_turn == 3

    def test_type_violation_rejected(self, tmp_path):
        p = _write_corpus(tmp_path, [{"id": "x", "tier": "nonsense", "family": "f"}])
        with pytest.raises(Exception):
            load_corpus(p)

    def test_optional_fields_ok(self, tmp_path):
        sc = [{"id": "a", "tier": "probe", "family": "f",
               "split": "waves", "evidence_scripts": ["e1"]}]
        loaded = load_corpus(_write_corpus(tmp_path, sc))
        assert loaded[0].split == "waves"
        assert loaded[0].evidence_scripts == ("e1",)

    def test_tier_filter(self, default_corpus):
        assert len(scenarios_by_tier(default_corpus, Tier.PROBE)) == 2
        assert scenarios_by_tier(default_corpus, Tier.STREAM) == []

    def test_episode_context_has_no_gold(self, default_corpus):
        ctx = str(default_corpus[0].to_episode_context())
        assert "The decision is to adopt" not in ctx  # gold text absent


class TestGoldBoundary:
    def test_golds_only_via_golds_surface(self, default_corpus):
        golds = default_corpus[0].golds()
        assert len(golds) == 1 and "decision is to adopt" in golds[0]

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
        t2 = ThresholdsConfig(cal_rows=tuple(reversed(t1.cal_rows)))
        assert t1.cal_table_hash() == t2.cal_table_hash()

    def test_hash_changes_on_value_change(self):
        from battery.config.thresholds import ThresholdsConfig
        t1 = ThresholdsConfig(cal_rows=(("m", "a", 0.5),))
        t2 = ThresholdsConfig(cal_rows=(("m", "a", 0.6),))
        assert t1.cal_table_hash() != t2.cal_table_hash()

    def test_epsilon_default(self):
        t = load_thresholds(CONFIG / "thresholds.yaml")
        assert t.determinism_epsilon == 1e-6


class TestArmsAndBudget:
    def test_arms_load(self):
        arms = load_arms(CONFIG / "arms.yaml")
        assert set(arms) == {"mock", "a0", "a4"}
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
