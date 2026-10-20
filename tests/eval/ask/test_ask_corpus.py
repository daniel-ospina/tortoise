"""Contract tests for the sealed ask corpus (issue #9097).

The corpus is what the published number rests on, so its invariants are pinned
here rather than described in prose:

* it is COMMITTED, hermetic, hand-authored and byte-deterministic;
* the manifest covers fixtures AND gold (a gold-only edit changes the hash);
* no fixture carries a grading key, and the sealed loader refuses one;
* both graded classes exist with a denominator that is not n=1;
* the pending baseline presets NO quality bar (D8: first-run-pending).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.eval.ask import corpus, generate_corpus


def test_the_committed_corpus_verifies_and_the_generator_is_idempotent():
    assert corpus.ASK_DIR.exists()
    report = corpus.verify_manifest()
    assert report["ok"], report
    assert report["corpus_hash"] == corpus.compute_corpus_hash()
    # A fresh render is byte-identical to the committed bytes (the fix-wave
    # guarantee: re-running the generator cannot silently revise the corpus).
    assert generate_corpus.drift() == []
    assert generate_corpus.validate_cases() == []


def test_both_graded_classes_are_present_with_a_real_denominator():
    """The exit bar's "no verbatim/verbatim regression" half is only measurable
    if a verbatim class EXISTS — the #9095 corpus had none, which is why the bar
    was unmeasurable before this corpus."""
    fixtures = corpus.load_fixtures()
    classes = [f["class"] for f in fixtures]
    assert classes.count(corpus.CLASS_KNOWLEDGE_UPDATE) >= 3, classes
    assert classes.count(corpus.CLASS_VERBATIM) >= 3, classes
    assert set(classes) == set(corpus.CLASSES)
    # Case ids are unique and every case has a sealed gold.
    ids = [f["case_id"] for f in fixtures]
    assert len(ids) == len(set(ids))
    assert sorted(ids) == corpus.case_ids()
    assert sorted(corpus.load_golds()) == sorted(ids)


def test_no_fixture_carries_a_grading_key():
    """The answer key's only on-disk home is ``gold/`` — at ANY depth.

    Scanned recursively on purpose: a top-level check passes a fixture that
    nests the answer one level down (``distilled.claims[i].answer``), which is
    the shape a later case would most plausibly grow into.
    """
    for path in sorted(corpus.FIXTURES_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert corpus.find_grading_keys(doc) == [], (
            f"{path.name} carries grading key(s) "
            f"{corpus.find_grading_keys(doc)}")


def test_every_distilled_claim_is_verbatim_in_its_own_session():
    """The invariant that makes raw vs distilled a paired read of the SAME
    fact: both arms hold the same words, so a difference measures the
    distillation rather than a corpus difference."""
    for fixture in corpus.load_fixtures():
        sessions = dict(zip(fixture["haystack_session_ids"],
                            fixture["haystack_sessions"], strict=True))
        for claim in fixture["distilled"]["claims"]:
            turns = " ".join(turn["content"] for turn in sessions[claim["session_id"]])
            assert claim["text"].lower() in turns.lower(), (
                f"{fixture['case_id']}: claim {claim['text']!r} is not in a "
                f"{claim['session_id']!r} turn")


def test_the_knowledge_update_gold_is_the_post_supersession_state():
    """For a KU case the answer IS the newer claim: the case asks for the
    CURRENT state, which is what the exit bar's number means."""
    for fixture in corpus.load_fixtures():
        if fixture["class"] != corpus.CLASS_KNOWLEDGE_UPDATE:
            continue
        gold = corpus.load_gold(fixture["case_id"])
        sup = fixture["distilled"]["supersessions"][0]
        assert gold["answer"] == sup["new"], fixture["case_id"]
        assert sup["old"] != sup["new"], fixture["case_id"]


def test_a_gold_only_edit_changes_the_corpus_hash_and_breaks_the_manifest(
        tmp_path):
    root = tmp_path / "corpus"
    generate_corpus.write_corpus(root)
    before = corpus.compute_corpus_hash(root)
    assert corpus.verify_manifest(root)["ok"] is True

    path = root / "gold" / "ask01_residence.gold.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["answer"] = "I live in Lisbon."
    path.write_text(corpus.dump_json(doc), encoding="utf-8")

    assert corpus.compute_corpus_hash(root) != before
    report = corpus.verify_manifest(root)
    assert report["ok"] is False
    assert report["mismatched"] or report["corpus_hash"] != before


def test_an_extra_fixture_breaks_the_manifest(tmp_path):
    root = tmp_path / "corpus"
    generate_corpus.write_corpus(root)
    (root / "fixtures" / "ask99_extra.json").write_text(
        json.dumps({"case_id": "ask99_extra", "class": "verbatim"}),
        encoding="utf-8")
    report = corpus.verify_manifest(root)
    assert report["ok"] is False
    assert report["extra"] == ["fixtures/ask99_extra.json"]


def test_the_pending_baseline_presets_no_quality_bar():
    baseline = corpus.load_baseline() if hasattr(corpus, "load_baseline") \
        else corpus.read_json(corpus.BASELINE_PATH)
    assert baseline["corpus_hash"] == corpus.compute_corpus_hash()
    # D8: first-run-pending. No metric, no threshold, no justification.
    assert baseline["metrics"] == {}
    assert baseline["history"] == []
    assert baseline["judge_pin"] is None
    assert baseline["justification"] is None
    fresh = corpus.first_run_pending_baseline()
    assert fresh["metrics"] == {} and fresh["judge_pin"] is None
    assert fresh["corpus_hash"] == baseline["corpus_hash"]


def test_the_manifest_declares_the_sealed_rule():
    manifest = corpus.load_manifest()
    assert manifest["schema"] == corpus.MANIFEST_SCHEMA
    assert manifest["sealed"]["gold_dir"] == "gold/"
    assert "fixtures/*.json only" in manifest["sealed"]["rule"]
