"""#2800 — MemoryAgentBench Conflict Resolution: pinned data + official metric.

Two properties are load-bearing and tested here:

* **The dataset is pinned by content.** A digest mismatch refuses; a missing
  parquet reader refuses; no path returns a number from an unverified file.
* **The metric is the benchmark's own.** ``normalize_answer`` /
  ``substring_exact_match_score`` / ``score_max_over_ground_truths`` /
  ``parse_output`` are ports of ``utils/eval_other_utils.py`` in
  ``HUST-AI-HYZ/MemoryAgentBench``. The cases below pin the behaviours that
  decide scores (articles dropped, punctuation dropped, substring direction,
  any-of alternatives, ``Answer:`` extraction), so a future edit cannot
  quietly change what "accuracy" means.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.parity.mabench import (
    CR_CONFIGS,
    CR_SHA256,
    CrItem,
    DatasetDigestError,
    MabenchError,
    PyarrowUnavailable,
    load_cr_items,
    normalize_answer,
    parse_output,
    score_cr,
    score_max_over_ground_truths,
    substring_exact_match_score,
    verify_digest,
)


class TestDigestPinning:
    def test_matching_digest_passes(self, tmp_path):
        f = tmp_path / "ok.bin"
        f.write_bytes(b"pinned bytes")
        verify_digest(f, expected=hashlib.sha256(b"pinned bytes").hexdigest())

    def test_mismatch_refuses(self, tmp_path):
        f = tmp_path / "tampered.bin"
        f.write_bytes(b"tampered")
        with pytest.raises(DatasetDigestError, match="digest mismatch"):
            verify_digest(f, expected=CR_SHA256)

    def test_pinned_digest_is_a_full_sha256(self):
        assert len(CR_SHA256) == 64 and all(
            c in "0123456789abcdef" for c in CR_SHA256)

    def test_eight_configs_pinned(self):
        assert len(CR_CONFIGS) == 8
        assert "factconsolidation_sh_6k" in CR_CONFIGS
        assert "factconsolidation_mh_262k" in CR_CONFIGS


class TestMissingReaderFailsClosed:
    def test_pyarrow_absent_is_explicit(self, monkeypatch, tmp_path):
        """A missing parquet reader must be an explicit refusal — never an
        accuracy derived from nothing."""
        import builtins

        real_import = builtins.__import__

        def _no_pyarrow(name, *a, **kw):
            if name.startswith("pyarrow"):
                raise ImportError("no pyarrow here")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _no_pyarrow)
        with pytest.raises(PyarrowUnavailable, match="parity extra"):
            load_cr_items("factconsolidation_sh_6k", path=tmp_path / "x.parquet")

    def test_unknown_config_refuses(self):
        with pytest.raises(MabenchError, match="unknown CR config"):
            load_cr_items("factconsolidation_xx_1k")


class TestOfficialMetric:
    """Behaviours ported from the benchmark's own implementation."""

    @pytest.mark.parametrize("prediction,gold,expected", [
        ("Belgium", "Belgium", True),
        ("belgium", "Belgium", True),                     # case
        ("The answer is Belgium.", "Belgium", True),      # substring of pred
        ("in the Belgium area", "Belgium", True),         # articles stripped
        ("France", "Belgium", False),
        ("Bel", "Belgium", False),                        # NOT the reverse
        ("Belgium is in Europe", "Belgium", True),
        ("", "Belgium", False),
    ])
    def test_substring_rule(self, prediction, gold, expected):
        assert substring_exact_match_score(prediction, gold) is expected

    def test_articles_and_punctuation_dropped(self):
        assert normalize_answer("The U.S.A., an area!") == "usa area"
        assert normalize_answer("  Multiple   spaces  ") == "multiple spaces"

    def test_empty_gold_refuses_instead_of_matching_everything(self):
        with pytest.raises(MabenchError, match="vacuous match"):
            substring_exact_match_score("anything", "the")

    def test_any_of_alternatives_scores(self):
        assert score_max_over_ground_truths("It was Rodez", ["Rodez", "Paris"])
        assert score_max_over_ground_truths("Paris", [["Rodez", "Paris"]])
        assert not score_max_over_ground_truths("Lyon", ["Rodez", "Paris"])

    def test_no_ground_truths_refuses(self):
        with pytest.raises(MabenchError, match="no ground truths"):
            score_max_over_ground_truths("x", [])

    def test_answer_prefix_extraction(self):
        assert parse_output("Reasoning...\nAnswer: Belgium", "Answer:") == "Belgium"
        assert parse_output("Belgium", "Answer:") == "Belgium"
        assert parse_output("ANSWER:    Belgium  ") == "Belgium"


class TestScoreCr:
    def _items(self):
        return (
            CrItem(qa_pair_id="q1", config="c", question="?",
                   accepted=("Belgium",)),
            CrItem(qa_pair_id="q2", config="c", question="?",
                   accepted=("Rodez", "Rhodez")),
        )

    def test_accuracy_and_full_denominator(self):
        acc, n = score_cr({"q1": "Answer: Belgium", "q2": "Answer: Rhodez"},
                          self._items())
        assert (acc, n) == (1.0, 2)

    def test_unanswered_items_count_as_wrong_not_skipped(self):
        """Dropping the hard cases must not be able to inflate accuracy."""
        acc, n = score_cr({"q1": "Answer: Belgium"}, self._items())
        assert (acc, n) == (0.5, 2)

    def test_unparseable_output_counts_as_wrong(self):
        acc, n = score_cr({"q1": "", "q2": "Answer: Rodez"}, self._items())
        assert (acc, n) == (0.5, 2)

    def test_no_items_refuses(self):
        with pytest.raises(MabenchError, match="no items to score"):
            score_cr({}, ())

    def test_item_without_accepted_answers_cannot_be_constructed(self):
        with pytest.raises(ValueError, match="no accepted answer"):
            CrItem(qa_pair_id="q", config="c", question="?", accepted=())


@pytest.mark.skipif(
    not __import__("os").environ.get("BATTERY_PARITY_MABENCH_E2E"),
    reason="opt-in: downloads the published 1.4 MB CR parquet and verifies "
           "its digest — set BATTERY_PARITY_MABENCH_E2E=1 (needs pyarrow)")
def test_published_dataset_downloads_and_verifies(tmp_path):
    """Against the PUBLISHED file: the digest matches the Hub's, and one
    pinned config loads the documented 100 QA pairs (counts read from the
    dataset on 2026-09-10). Opt-in because it hits the network."""
    from battery.parity.mabench import fetch_cr_parquet

    src = fetch_cr_parquet(dest_dir=tmp_path)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == CR_SHA256
    items = load_cr_items("factconsolidation_sh_6k", path=src)
    assert len(items) == 100, "the pinned file carries 100 QA pairs per config"
    assert all(i.accepted for i in items)
    assert all(i.config == "factconsolidation_sh_6k" for i in items)
