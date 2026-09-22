"""Metadata/schema checks for the bit-level kind eval set (issue #1695,
Task 2 / D0-3) and the D0-2 probe record.

Lane-agnostic (pure metadata + numpy — no LLM, no DB): the D0-2 probe
degrades to TF-IDF when the embedder is unavailable, so the probe smoke
test skips only when BOTH the embedder and sklearn are missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.kind_eval import (  # noqa: E402, RUF100
    DEFAULT_GOLD,
    GOLD_SPLITS,
    load_gold,
    run_eval,
    run_probe,
    validate_gold_metadata,
)
from tortoise import extractor_v2 as v2  # noqa: E402, RUF100


@pytest.fixture(scope="module")
def gold_bits():
    return load_gold(DEFAULT_GOLD)


@pytest.fixture(scope="module")
def vocab():
    return v2.master_kind_forms(v2.build_master_list())


class TestGoldSetMetadata:
    def test_mini_gold_loads(self, gold_bits):
        assert len(gold_bits) >= 10

    def test_metadata_contract(self, gold_bits, vocab):
        stats = validate_gold_metadata(gold_bits, vocab)
        # provenance + authors present (owner/adjudicator discipline)
        assert stats["authors"], "provenance.author required"
        # both splits present
        assert set(stats["splits"]) == set(GOLD_SPLITS)
        # pack-stratum minimum: at least one non-core kind bit (the full
        # per-pack n=392 target is a run-level corpus question, audited via
        # tools/longmem_eval/dataset_audit.py)
        assert stats["pack_stratum_bits"] >= 1
        # nearMiss subset present
        assert stats["near_miss_bits"] >= 1
        # all three bit types present
        assert set(stats["types"]) == {"entity", "event", "point"}

    def test_gold_kinds_are_absolute_not_unclassified(self, gold_bits):
        for b in gold_bits:
            assert str(b["gold_kind"]).lower() != "unclassified"
            assert b["gold_kind"]  # ABSOLUTE labels, never empty

    def test_near_miss_refs_in_vocabulary(self, gold_bits, vocab):
        validate_gold_metadata(gold_bits, vocab)  # raises on violation

    def test_rejects_missing_provenance(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"id": "x", "content": "c", "type": "entity", '
            '"gold_kind": "core:plan", "split": "calibrate"}\n'
        )
        with pytest.raises(ValueError, match="provenance"):
            load_gold(p)

    def test_rejects_unclassified_gold(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"id": "x", "content": "c", "type": "entity", '
            '"gold_kind": "unclassified", "split": "calibrate", '
            '"provenance": {"source": "t"}}\n'
        )
        with pytest.raises(ValueError, match="sentinel"):
            load_gold(p)

    def test_rejects_minted_gold_kind(self, gold_bits, vocab, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"id": "x", "content": "c", "type": "entity", '
            '"gold_kind": "worktree", "split": "calibrate", '
            '"provenance": {"source": "t", "author": "a"}}\n'
        )
        bits = load_gold(p)
        with pytest.raises(ValueError, match="closed vocabulary"):
            validate_gold_metadata(bits, vocab)

    def test_rejects_missing_split(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"id": "x", "content": "c", "type": "entity", '
            '"gold_kind": "core:plan", "split": "train", '
            '"provenance": {"source": "t"}}\n'
        )
        with pytest.raises(ValueError, match="split"):
            load_gold(p)

    def test_rejects_duplicate_ids(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"id": "x", "content": "c1", "type": "entity", '
            '"gold_kind": "core:plan", "split": "calibrate", '
            '"provenance": {"source": "t", "author": "a"}}\n'
            '{"id": "x", "content": "c2", "type": "entity", '
            '"gold_kind": "core:goal", "split": "holdout", '
            '"provenance": {"source": "t", "author": "a"}}\n'
        )
        with pytest.raises(ValueError, match="duplicate bit id"):
            load_gold(p)

    def test_rejects_missing_author(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(
            '{"id": "x", "content": "c", "type": "entity", '
            '"gold_kind": "core:plan", "split": "calibrate", '
            '"provenance": {"source": "t"}}\n'
        )
        with pytest.raises(ValueError, match="author"):
            load_gold(p)

    def test_rejects_zero_pack_stratum(self, gold_bits, vocab, tmp_path):
        """The pack-stratum minimum is ENFORCED, not just reported: a gold
        set with zero non-core kinds cannot gate the pack stratum."""
        p = tmp_path / "nopack.jsonl"
        with open(p, "w") as f:
            for i in range(4):
                f.write(
                    json.dumps(
                        {
                            "id": f"c{i}",
                            "content": f"bit {i}",
                            "type": "entity",
                            "gold_kind": "core:plan",
                            "split": "calibrate" if i % 2 else "holdout",
                            "provenance": {"source": "t", "author": "a"},
                        }
                    )
                    + "\n"
                )
        bits = load_gold(p)
        with pytest.raises(ValueError, match="pack-stratum"):
            validate_gold_metadata(bits, vocab)


class TestEvalSurface:
    def test_eval_metadata_only_before_classifier(self, gold_bits, vocab):
        """run_eval returns the metadata block always; the classifier-backed
        metrics are graceful before Task 4 lands."""
        result = run_eval(DEFAULT_GOLD, arm="compact")
        assert result["arm"] == "compact"
        assert result["gold_metadata"]["bits"] == len(gold_bits)


class TestD02Probe:
    def test_probe_runs_and_reports_gate(self, monkeypatch):
        """The probe must run (pennies, no LLM) and produce the pre-
        registered gate record. The mini-gold is the instrument — the gate
        itself is evaluated at RUN level on the full vocabulary+gold.
        Force the TF-IDF degrade lane (monkeypatched embedder) so the test
        never waits on a cold bge-small load."""
        import tortoise.embeddings as emb

        monkeypatch.setattr(emb.EmbeddingModel, "get", staticmethod(lambda: None))
        try:
            record = run_probe(DEFAULT_GOLD)
        except (ImportError, ModuleNotFoundError):
            pytest.skip("no embedder AND no sklearn — probe cannot run")
        assert record["probe"] == "D0-2"
        assert set(record["gate"]) == {"tail_lt_40pct", "top5_hit_ge_85pct"}
        assert record["kinds"] >= 50  # the 54-kind vocabulary
        assert record["gold_bits"] == len(load_gold(DEFAULT_GOLD))
        assert 0.0 <= record["top5_hit_rate"] <= 1.0
        assert 0.0 <= record["tail_fraction"] <= 1.0
        assert isinstance(record["inter_kind_sim_mean"], (int, float))
