"""Track E1 (#3011) — gold-evidence claim artifact builder tests.

Hermetic: no DB, no network. Every assertion exercises the pure derivation in
``tools/longmem_eval/gold_evidence_claims.py``; the CLI tests read/write only
``tmp_path`` files.

Covered (per the Track E1 brief):
  * the frozen tokenizer — ASCII punctuation *deleted* (``don't`` → ``dont``,
    ``12,000`` → ``12000``), lowercase, whitespace-only split, stopword removal
  * the frozen stopword-list choice (reused, not redefined)
  * sentence splitting on all four delimiters, role-prefix stripping, trim,
    empty-span drop
  * the gold ``answer`` entering as exactly ONE claim (never sentence-split)
  * ``MIN_GOLD_TOKENS`` boundary, the zero-token case never dividing
  * the non-emptiness construction failure raising
  * the exact artifact JSON schema + sha256 sibling
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import gold_evidence_claims as gec
from tools.longmem_eval import leakage_guard as lg
from tortoise.sparse import SPARSE_STOPWORDS

# ── helpers ────────────────────────────────────────────────────────────────


def _row(
    qid: str = "q1",
    *,
    answer: str = "alpha beta gamma delta",
    sessions: list[tuple[str, list[dict]]] | None = None,
    gold_ids: list[str] | None = None,
    question: str = "what?",
) -> dict:
    """Minimal LongMemEval row (the shape verified against the 55-row slice)."""
    if sessions is None:
        sessions = [("sess_a", [{"role": "user",
                                 "content": "alpha beta gamma.",
                                 "has_answer": True}])]
    if gold_ids is None:
        gold_ids = [sessions[0][0]]
    return {
        "question_id": qid,
        "question_type": "temporal-reasoning",
        "question": question,
        "question_date": "2023/02/01 (Wed) 10:20",
        "answer": answer,
        "answer_session_ids": gold_ids,
        "haystack_session_ids": [sid for sid, _ in sessions],
        "haystack_sessions": [turns for _, turns in sessions],
    }


# ── frozen constants / stopword reuse ──────────────────────────────────────


def test_frozen_constants():
    assert gec.MIN_GOLD_TOKENS == 3
    assert isinstance(gec.STOPWORDS, frozenset)


def test_stopwords_reuses_sparse_set_verbatim():
    """The list is imported, not redefined (spec §4 leaves the list unnamed)."""
    assert gec.STOPWORDS is SPARSE_STOPWORDS
    assert gec.STOPWORDS == SPARSE_STOPWORDS


def test_stopwords_are_removed_after_punctuation_deletion():
    tokens = gec.tokenize("the cat is on the mat")
    assert tokens == ["cat", "mat"]


def test_chosen_list_is_the_small_sparse_set_not_an_aggressive_one():
    """Pins the choice: ``actually`` is a stopword in the eval's probe list but
    NOT in the reused product set, so it must survive as content."""
    assert "actually" not in gec.STOPWORDS
    assert gec.tokenize("actually") == ["actually"]


def test_artifact_path_matches_the_leakage_guard_constant():
    """The builder and the §10 static assertion must name the same path."""
    assert gec.ARTIFACT_PATH == lg.GOLD_EVIDENCE_ARTIFACT_PATH
    assert gec.ARTIFACT_FILENAME == lg.GOLD_EVIDENCE_ARTIFACT_FILENAME


# ── tokenizer ──────────────────────────────────────────────────────────────


def test_tokenizer_deletes_ascii_punctuation_not_replaces_with_space():
    assert gec.tokenize("don't") == ["dont"]
    assert gec.tokenize("12,000") == ["12000"]
    assert gec.tokenize("well-known") == ["wellknown"]
    assert gec.tokenize("(hello)") == ["hello"]


def test_tokenizer_lowercases():
    assert gec.tokenize("HELLO World") == ["hello", "world"]


def test_tokenizer_splits_on_whitespace_only_never_word_boundary():
    # punctuation is deleted, so adjacent words fuse into ONE token
    assert gec.tokenize("alpha.beta") == ["alphabeta"]
    assert gec.tokenize("alpha,beta") == ["alphabeta"]
    # with whitespace present, the (now punctuation-free) words survive apart
    assert gec.tokenize("alpha, beta") == ["alpha", "beta"]
    assert gec.tokenize("alpha-beta gamma") == ["alphabeta", "gamma"]


def test_tokenizer_splits_on_tabs_and_newlines_too():
    assert gec.tokenize("alpha\tbeta\ngamma") == ["alpha", "beta", "gamma"]


def test_tokenizer_drops_empty_tokens_from_punctuation_only_runs():
    assert gec.tokenize("!!!") == []
    assert gec.tokenize("alpha !!! beta") == ["alpha", "beta"]


def test_tokenizer_preserves_duplicates_and_non_ascii_punctuation():
    assert gec.tokenize("cat cat") == ["cat", "cat"]
    # spec deletes ASCII punctuation only — an em-dash is not ASCII
    assert gec.tokenize("caf\u00e9\u2014bar") == ["caf\u00e9\u2014bar"]


def test_tokenizer_returns_a_fresh_list():
    out = gec.tokenize("alpha beta")
    out.append("mutated")
    assert gec.tokenize("alpha beta") == ["alpha", "beta"]


# ── sentence spans ─────────────────────────────────────────────────────────


def test_sentence_spans_split_on_all_four_delimiters():
    text = "One two. Three four! Five six? Seven eight\nNine ten"
    assert gec.sentence_spans(text) == [
        "One two", "Three four", "Five six", "Seven eight", "Nine ten",
    ]


def test_sentence_spans_strip_role_prefix():
    assert gec.sentence_spans("[user] Hello there") == ["Hello there"]
    assert gec.sentence_spans("[assistant]   Spaced out") == ["Spaced out"]
    assert gec.sentence_spans("[SYSTEM] Case insensitive") == ["Case insensitive"]


def test_sentence_spans_strip_role_prefix_from_the_first_span_only():
    assert gec.sentence_spans("[user] One two. Three four") == [
        "One two", "Three four",
    ]


def test_sentence_spans_trim_and_drop_empty():
    assert gec.sentence_spans("  alpha  ") == ["alpha"]
    assert gec.sentence_spans("...\n\n! ?") == []
    assert gec.sentence_spans("") == []


def test_sentence_spans_do_not_strip_a_non_role_bracket():
    assert gec.sentence_spans("[note] keep me") == ["[note] keep me"]


# ── claim derivation ───────────────────────────────────────────────────────


def test_gold_answer_enters_as_exactly_one_claim_never_split():
    answer = "7 days. 8 days (including the last day) is also acceptable."
    row = _row(answer=answer)
    claims = gec.build_claims_for_question(row)
    gold = [c for c in claims if c["source_turn_id"] == "gold_answer"]
    assert len(gold) == 1
    assert gold[0]["claim"] == answer  # verbatim, internal periods intact
    assert gold[0]["tokens"] == len(gec.tokenize(answer))
    assert gold[0]["trivial"] is False


def test_source_turn_id_uses_positional_session_and_turn_index():
    sessions = [
        ("sess_a", [{"role": "user", "content": "zeroth turn"}]),
        ("sess_b", [
            {"role": "user", "content": "ignored turn"},
            {"role": "user", "content": "kappa lambda mu", "has_answer": True},
        ]),
    ]
    row = _row(qid="q9", sessions=sessions, gold_ids=["sess_b"])
    ids = [c["source_turn_id"] for c in gec.build_claims_for_question(row)]
    assert ids == ["lme:q9:s1:t1", "gold_answer"]


def test_only_has_answer_turns_in_gold_sessions_contribute():
    sessions = [
        ("sess_other", [
            {"role": "user", "content": "alpha beta gamma", "has_answer": True},
        ]),
        ("sess_gold", [
            {"role": "user", "content": "eta theta iota"},  # gold, no mark
            {"role": "user", "content": "kappa lambda mu", "has_answer": True},
        ]),
    ]
    row = _row(qid="q7", sessions=sessions, gold_ids=["sess_gold"])
    ids = [c["source_turn_id"] for c in gec.build_claims_for_question(row)]
    assert ids == ["lme:q7:s1:t1", "gold_answer"]


def test_has_answer_false_is_excluded():
    sessions = [("sess_gold", [
        {"role": "user", "content": "kappa lambda mu", "has_answer": False},
    ])]
    row = _row(qid="q7", sessions=sessions, gold_ids=["sess_gold"])
    ids = [c["source_turn_id"] for c in gec.build_claims_for_question(row)]
    assert ids == ["gold_answer"]


def test_turn_content_is_sentence_split_into_separate_claims():
    sessions = [("sess_gold", [
        {"role": "user", "content": "Alpha beta gamma. Delta epsilon zeta!",
         "has_answer": True},
    ])]
    row = _row(qid="q3", sessions=sessions, gold_ids=["sess_gold"])
    claims = gec.build_claims_for_question(row)
    assert [(c["claim"], c["source_turn_id"]) for c in claims] == [
        ("Alpha beta gamma", "lme:q3:s0:t0"),
        ("Delta epsilon zeta", "lme:q3:s0:t0"),
        ("alpha beta gamma delta", "gold_answer"),
    ]


def test_role_prefixed_turn_content_is_stripped():
    sessions = [("sess_gold", [
        {"role": "user",
         "content": "[user] Alpha beta gamma. Delta epsilon zeta.",
         "has_answer": True},
    ])]
    row = _row(qid="q4", sessions=sessions, gold_ids=["sess_gold"])
    claims = gec.build_claims_for_question(row)
    assert [c["claim"] for c in claims] == [
        "Alpha beta gamma", "Delta epsilon zeta", "alpha beta gamma delta",
    ]


# ── MIN_GOLD_TOKENS / triviality / zero-token ──────────────────────────────


def test_min_gold_tokens_boundary():
    sessions = [("sess_gold", [
        {"role": "user", "content": "alpha beta. gamma delta epsilon.",
         "has_answer": True},
    ])]
    row = _row(qid="q5", sessions=sessions, gold_ids=["sess_gold"],
               answer="zeta eta theta")
    claims = gec.build_claims_for_question(row)
    by_claim = {c["claim"]: c for c in claims}
    two = by_claim["alpha beta"]
    three = by_claim["gamma delta epsilon"]
    assert two["tokens"] == 2 and two["trivial"] is True
    assert three["tokens"] == 3 and three["trivial"] is False
    assert gec.MIN_GOLD_TOKENS == 3


def test_all_stopword_span_is_zero_tokens_and_never_divides():
    """The 0-token span is exactly the ``< MIN_GOLD_TOKENS`` case: flagged
    trivial and never matched — and no division is performed anywhere."""
    sessions = [("sess_gold", [
        {"role": "user", "content": "[user] the is and !", "has_answer": True},
    ])]
    row = _row(qid="q6", sessions=sessions, gold_ids=["sess_gold"],
               answer="zeta eta theta")
    claims = gec.build_claims_for_question(row)  # must not raise
    zero = [c for c in claims if c["claim"] == "the is and"]
    assert len(zero) == 1
    assert zero[0]["tokens"] == 0
    assert zero[0]["trivial"] is True
    # every zero-token claim is marked trivial (never matchable)
    assert all(c["trivial"] for c in claims if c["tokens"] == 0)


def test_punctuation_only_span_yields_no_claim_at_all():
    sessions = [("sess_gold", [
        {"role": "user", "content": "... ! ? \n", "has_answer": True},
    ])]
    row = _row(qid="q6b", sessions=sessions, gold_ids=["sess_gold"],
               answer="zeta eta theta")
    claims = gec.build_claims_for_question(row)
    assert [c["source_turn_id"] for c in claims] == ["gold_answer"]


# ── non-emptiness construction failure ────────────────────────────────────


def test_non_emptiness_failure_raises():
    sessions = [("sess_gold", [
        {"role": "user", "content": "the is and", "has_answer": True},
    ])]
    row = _row(qid="qfail", sessions=sessions, gold_ids=["sess_gold"],
               answer="the and to")
    with pytest.raises(gec.GoldEvidenceConstructionError, match="qfail"):
        gec.build_claims_for_question(row)


def test_build_artifact_raises_on_non_emptiness_failure():
    bad = _row(qid="qfail", answer="the and to",
               sessions=[("sess_gold", [
                   {"role": "user", "content": "the is and",
                    "has_answer": True},
               ])],
               gold_ids=["sess_gold"])
    with pytest.raises(gec.GoldEvidenceConstructionError, match="qfail"):
        gec.build_artifact([bad])


def test_missing_gold_session_raises():
    row = _row(qid="qmiss")
    row["answer_session_ids"] = ["not_in_haystack"]
    with pytest.raises(gec.GoldEvidenceConstructionError, match="not_in_haystack"):
        gec.build_claims_for_question(row)


def test_ragged_haystack_raises():
    row = _row(qid="qrag")
    row["haystack_sessions"] = row["haystack_sessions"] + [[]]
    with pytest.raises(gec.GoldEvidenceConstructionError, match="ragged"):
        gec.build_claims_for_question(row)


def test_missing_question_id_raises():
    row = _row()
    row["question_id"] = ""
    with pytest.raises(gec.GoldEvidenceConstructionError):
        gec.build_claims_for_question(row)


def test_duplicate_question_id_raises():
    with pytest.raises(gec.GoldEvidenceConstructionError, match="duplicate"):
        gec.build_artifact([_row(qid="qdup"), _row(qid="qdup")])


# ── artifact schema ────────────────────────────────────────────────────────


_SOURCE_ID_RE = re.compile(r"^(lme:.+:s\d+:t\d+|gold_answer)$")


def test_artifact_json_schema_exact():
    art = gec.build_artifact([_row(qid="q1"), _row(qid="q2")])
    assert set(art) == {"q1", "q2"}
    for claims in art.values():
        assert isinstance(claims, list) and claims
        assert any(not c["trivial"] for c in claims)  # non-emptiness
        for c in claims:
            assert set(c) == {"claim", "source_turn_id", "tokens", "trivial"}
            assert isinstance(c["claim"], str)
            assert isinstance(c["source_turn_id"], str)
            assert _SOURCE_ID_RE.match(c["source_turn_id"]), c["source_turn_id"]
            assert type(c["tokens"]) is int
            assert type(c["trivial"]) is bool
            assert c["tokens"] == len(gec.tokenize(c["claim"]))
            assert c["trivial"] == (c["tokens"] < gec.MIN_GOLD_TOKENS)
        assert [c["source_turn_id"] for c in claims][-1] == "gold_answer"


def test_artifact_is_json_serializable_and_keyed_by_qid():
    art = gec.build_artifact([_row(qid="q1")])
    reloaded = json.loads(json.dumps(art))
    assert reloaded == art


def test_artifact_key_order_is_sorted_by_qid():
    rows = [_row(qid="q_b"), _row(qid="q_a"), _row(qid="q_c")]
    assert list(gec.build_artifact(rows)) == ["q_a", "q_b", "q_c"]


# ── sha256 + CLI ───────────────────────────────────────────────────────────


def test_artifact_sha256_reads_the_written_bytes(tmp_path: Path):
    p = tmp_path / "a.json"
    p.write_text("{}\n", encoding="utf-8")
    import hashlib
    assert gec.artifact_sha256(p) == hashlib.sha256(b"{}\n").hexdigest()


def test_main_writes_artifact_and_sibling_sha256(tmp_path: Path, capsys):
    rows = [_row(qid="q2"), _row(qid="q1")]
    instances = tmp_path / "instances.json"
    instances.write_text(json.dumps(rows), encoding="utf-8")
    out = tmp_path / "gold-evidence-claims.json"

    assert gec.main(["--instances", str(instances), "--out", str(out)]) == 0
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == gec.build_artifact(rows)

    sha_path = tmp_path / "gold-evidence-claims.json.sha256"
    assert sha_path.exists()
    digest = gec.artifact_sha256(out)
    assert sha_path.read_text(encoding="utf-8").split()[0] == digest
    assert digest in capsys.readouterr().out


def test_main_creates_the_output_directory(tmp_path: Path):
    instances = tmp_path / "instances.json"
    instances.write_text(json.dumps([_row()]), encoding="utf-8")
    out = tmp_path / "nested" / "dir" / "gold-evidence-claims.json"
    assert gec.main(["--instances", str(instances), "--out", str(out)]) == 0
    assert out.exists()


def test_main_fails_closed_on_construction_failure(tmp_path: Path, capsys):
    bad = _row(qid="qfail", answer="the and to",
               sessions=[("sess_gold", [
                   {"role": "user", "content": "the is and",
                    "has_answer": True},
               ])],
               gold_ids=["sess_gold"])
    instances = tmp_path / "instances.json"
    instances.write_text(json.dumps([bad]), encoding="utf-8")
    out = tmp_path / "gold-evidence-claims.json"

    assert gec.main(["--instances", str(instances), "--out", str(out)]) == 2
    assert not out.exists()  # no partial artifact
    assert not (tmp_path / "gold-evidence-claims.json.sha256").exists()
    assert "qfail" in capsys.readouterr().err


def test_main_rejects_a_non_list_instances_file(tmp_path: Path, capsys):
    instances = tmp_path / "instances.json"
    instances.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    out = tmp_path / "gold-evidence-claims.json"
    assert gec.main(["--instances", str(instances), "--out", str(out)]) == 2
    assert not out.exists()
    assert "list" in capsys.readouterr().err
