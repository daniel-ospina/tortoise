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

import functools
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


# ── frozen-artifact structural invariants (regression: #3011 P2) ───────────
#
# The committed artifact must be *derived from* the frozen LongMemEval-S
# dataset, never from a hand-edited slice of it. A slice that had dropped a
# non-gold session silently shifted ``gpt4_76048e76``'s gold session from
# haystack index 20 to 19 and shipped three claims citing a non-gold,
# ``has_answer``-less session. These tests close that hole at two levels:
#
#   * ``_FROZEN_GOLD_SESSIONS`` — a hermetic, inline snapshot of the frozen
#     dataset's gold structure (CI has no 277 MB dataset): every cited
#     ``source_turn_id`` must name a session in ``answer_session_ids`` and a
#     turn that actually carries ``has_answer: true``;
#   * a dataset-backed rebuild asserting the committed bytes and sha256 are
#     exactly what the committed builder produces from the frozen dataset,
#     idempotently across two runs (skipped when the dataset is not cached,
#     matching ``tests/test_dataset_audit.py``).

#: Frozen gold-session index for the #2578 55-Q analysis subset, derived from
#: ``~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json`` via the
#: committed selectors (``measure_temporal.deterministic_subset`` +
#: ``dedup_instance_sessions``): ``qid -> {position in haystack_session_ids of
#: each answer_session_ids entry: turn indices carrying has_answer: true}``.
#: Keys are exactly the ``answer_session_ids`` positions, so a key miss is a
#: reference to a non-gold session; an empty tuple is a gold session whose
#: turns carry no ``has_answer`` mark (four exist — they must never be cited).
_FROZEN_GOLD_SESSIONS: dict[str, dict[int, tuple[int, ...]]] = {
    "gpt4_59149c77": {4: (0,), 27: (6,)},
    "gpt4_fa19884c": {17: (8,), 36: (0,)},
    "gpt4_4929293a": {16: (0,), 27: (0,)},
    "gpt4_1d4ab0c9": {13: (0,), 30: (0,)},
    "0db4c65d": {29: (0,), 39: (0,)},
    "gpt4_1916e0ea": {4: (6,), 17: (0,)},
    "gpt4_7a0daae1": {9: (6,), 12: (0,)},
    "gpt4_1e4a8aeb": {2: (0,), 40: (0,)},
    "gpt4_4fc4f797": {21: (10,), 35: (0,)},
    "4dfccbf7": {35: (4,), 38: (8,)},
    "gpt4_61e13b3c": {31: (0,), 44: (0,)},
    "gpt4_4ef30696": {2: (0,), 39: (0,)},
    "gpt4_8e165409": {3: (0,), 30: (8,)},
    "gpt4_74aed68e": {2: (0,), 32: (0,)},
    "gpt4_21adecb5": {24: (0,), 37: (0,)},
    "gpt4_98f46fc6": {6: (0,), 13: (6,)},
    "gpt4_68e94287": {13: (0,), 21: (0,)},
    "gpt4_e414231e": {1: (0,), 24: (0,)},
    "gpt4_2487a7cb": {20: (2,), 40: (10,)},
    "gpt4_76048e76": {18: (0,), 20: (0,)},
    "gpt4_2312f94c": {7: (0,), 19: (0,)},
    "08f4fc43": {23: (0,), 27: (0,)},
    "2c63a862": {31: (8,), 41: (0,)},
    "gpt4_385a5000": {10: (0,), 47: (0,)},
    "2a1811e2": {17: (0,), 25: (0,)},
    "gpt4_0b2f1d21": {12: (0,), 41: (0,)},
    "f0853d11": {18: (0,), 22: (0,)},
    "gpt4_6ed717ea": {14: (0,), 20: (0,)},
    "gpt4_70e84552": {10: (0,), 40: (0,)},
    "a3838d2b": {12: (0,), 24: (0,), 30: (0,), 33: (0,), 35: (0,), 37: (0,)},
    "gpt4_93159ced": {0: (4, 10), 23: (2,)},
    "gpt4_2d58bcd6": {4: (0,), 43: (0,)},
    "gpt4_65aabe59": {19: (0, 4), 36: (4,)},
    "gpt4_483dd43c": {19: (0,), 39: (0,)},
    "dcfa8644": {15: (0,), 35: (0,)},
    "gpt4_b4a80587": {11: (2,), 25: (0,)},
    "gpt4_8c8961ae": {5: (0,), 19: (10,)},
    "gpt4_d9af6064": {39: (0,), 41: (0,)},
    "gpt4_7de946e7": {27: (6,), 39: (0,)},
    "gpt4_d31cdae3": {20: (10,), 28: (0,)},
    "gpt4_cd90e484": {33: (4,), 38: (0,)},
    "gpt4_88806d6e": {9: (0,), 41: (0,)},
    "gpt4_93f6379c": {3: (0,), 14: (), 36: (0,)},
    "gpt4_78cf46a3": {34: (0,), 44: (6,)},
    "gpt4_0a05b494": {7: (2,), 36: (2,)},
    "gpt4_1a1dc16d": {32: (0,), 40: (6,)},
    "gpt4_2f584639": {9: (2,), 18: (0,)},
    "gpt4_213fd887": {7: (0,), 16: (2,)},
    "gpt4_5438fa52": {3: (0,), 24: (4,)},
    "gpt4_c27434e8": {18: (0,), 27: (0,)},
    "gpt4_fe651585": {40: (0,), 46: (8,)},
    "8c18457d": {9: (4, 10), 30: (0,)},
    "gpt4_93159ced_abs": {9: (), 29: ()},
    "gpt4_c27434e8_abs": {0: (0,), 37: ()},
    "gpt4_fe651585_abs": {2: (), 8: (0,)},
}

_FROZEN_DATASET = (Path.home() / ".cache" / "tortoise-longmemeval"
                   / "longmemeval_s_cleaned.json")

#: The committed artifact path, resolved from this test file (repo root is two
#: levels up from ``tests/longmem_eval/``).
_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / gec.ARTIFACT_PATH

_TURN_ID_RE = re.compile(r"^lme:.+:s(\d+):t(\d+)$")


def _committed_artifact() -> dict:
    return json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))


def _source_turn_index(source_turn_id: str) -> tuple[int, int]:
    """``lme:<qid>:s<si>:t<ti>`` -> ``(si, ti)``."""
    match = _TURN_ID_RE.match(source_turn_id)
    assert match, source_turn_id
    return int(match.group(1)), int(match.group(2))


def _require_frozen_dataset() -> None:
    if not _FROZEN_DATASET.is_file():
        pytest.skip("frozen LongMemEval-S dataset not cached (CI)")


@functools.lru_cache(maxsize=1)
def _canonical_artifact_inputs() -> tuple[dict, ...]:
    """The committed producer's input: the #2578 55-Q census subset joined to
    the frozen dataset, with the pre-registered session dedup applied.

    Cached: parsing the 277 MB frozen dataset once per test would dominate
    this file's runtime. Callers treat the rows as read-only."""
    from tools.longmem_eval import measure_temporal as mt

    cache = json.loads(_FROZEN_DATASET.read_text(encoding="utf-8"))
    by_qid = {row["question_id"]: row for row in cache}
    rows = [by_qid[r["qid"]]
            for r in mt.deterministic_subset(mt.load_census()["rows"])]
    return tuple(mt.dedup_instance_sessions(row) for row in rows)


def test_committed_artifact_covers_the_frozen_55_question_subset():
    assert set(_committed_artifact()) == set(_FROZEN_GOLD_SESSIONS)


def test_committed_artifact_source_turns_all_carry_has_answer():
    """Every cited turn is a turn of a gold session marked ``has_answer``."""
    for qid, claims in _committed_artifact().items():
        for claim in claims:
            if claim["source_turn_id"] == "gold_answer":
                continue
            si, ti = _source_turn_index(claim["source_turn_id"])
            gold = _FROZEN_GOLD_SESSIONS[qid]
            assert si in gold, (qid, claim["source_turn_id"])
            assert ti in gold[si], (qid, claim["source_turn_id"])


def test_committed_artifact_never_references_a_session_outside_gold_set():
    """Explicit second invariant: every cited session is a member of
    ``answer_session_ids``. ``_FROZEN_GOLD_SESSIONS``' keys are exactly the
    ``answer_session_ids`` positions, so a key miss is a reference to a
    session outside the gold set.
    """
    for qid, claims in _committed_artifact().items():
        referenced = {
            _source_turn_index(c["source_turn_id"])[0]
            for c in claims if c["source_turn_id"] != "gold_answer"}
        assert referenced <= set(_FROZEN_GOLD_SESSIONS[qid]), qid


def test_frozen_gold_index_matches_the_cached_dataset():
    """The inline snapshot cannot go stale silently: re-derive it from the
    frozen dataset when present."""
    _require_frozen_dataset()
    derived: dict[str, dict[int, tuple[int, ...]]] = {}
    for row in _canonical_artifact_inputs():
        gold = set(row["answer_session_ids"])
        derived[row["question_id"]] = {
            i: tuple(t for t, turn in enumerate(row["haystack_sessions"][i])
                     if turn.get("has_answer"))
            for i, sid in enumerate(row["haystack_session_ids"])
            if sid in gold}
    assert derived == _FROZEN_GOLD_SESSIONS


def test_committed_artifact_rebuilds_byte_identically_from_frozen_dataset(
        tmp_path: Path):
    """The committed artifact's bytes and sha256 are exactly the committed
    builder's output from the frozen dataset — and the build is idempotent
    (two runs, identical bytes and identical digest)."""
    _require_frozen_dataset()
    rows = _canonical_artifact_inputs()
    instances = tmp_path / "instances.json"
    instances.write_text(json.dumps(list(rows)), encoding="utf-8")

    outs = [tmp_path / f"rebuild{i}.json" for i in (1, 2)]
    for out in outs:
        assert gec.main(["--instances", str(instances), "--out", str(out)]) == 0

    # idempotence: identical bytes and identical sha256 across the two runs
    assert outs[0].read_bytes() == outs[1].read_bytes()
    digest = gec.artifact_sha256(outs[0])
    assert digest == gec.artifact_sha256(outs[1])
    sha1 = (tmp_path / "rebuild1.json.sha256").read_text(encoding="utf-8")
    assert sha1.split()[0] == digest

    # the frozen artifact is exactly the builder output, and its recorded
    # sha256 is the digest of those bytes (spec §9.5 provenance chain)
    assert outs[0].read_bytes() == _ARTIFACT_PATH.read_bytes()
    sha_path = _ARTIFACT_PATH.with_name(_ARTIFACT_PATH.name + ".sha256")
    assert sha_path.read_text(encoding="utf-8").split()[0] == digest
    assert gec.artifact_sha256(_ARTIFACT_PATH) == digest
