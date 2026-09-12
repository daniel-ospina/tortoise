"""Tests for the #3011 Track E 4-arm context-assembly runner (hermetic).

Every test here is offline (mock reader + fake judge + an in-memory graph
seam) unless it is explicitly marked to need a live FalkorDB
(``TORTOISE_DB_URI``) — those skip cleanly when the env var is absent.

Coverage maps onto the task's acceptance list:

* each arm selects the right context source (A/B/C/D);
* arm D builds the header-only no-context control;
* the four arms share ONE byte-identical prompt scaffolding;
* metric 5 (answer-bearing-claim presence) — trivial claim never matches,
  the 0.80 boundary, and the end-to-end ``graph_metrics`` composition;
* metric 8 requires a typed-relation endpoint in the answer-bearing set;
* metric 9 is content-free (an unrelated point from a gold session matches);
* the per-class counts sum to 52 (interval 19 / ordering 32 / current-state 1);
* the report shape is consumable (required keys + ``classify_outcome``);
* the remaining controls: fresh namespace per question, reader constancy,
  the §9.5 artifact gate, and the CLI surface.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import types
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import tools.longmem_eval.context_assembly_arms as caa  # noqa: E402
from tools.longmem_eval import gold_evidence_claims as gec  # noqa: E402
from tools.longmem_eval import leakage_guard as lg  # noqa: E402
from tortoise.reader import (  # noqa: E402
    LLMReader,
    build_reader_user_message,
    system_prompt_for,
)
from tortoise.retrieval import render_context  # noqa: E402
from tortoise.subgraph import Candidate, Relation, Subgraph  # noqa: E402
from tortoise.subgraph_render import EMPTY_CONTEXT_SENTINEL  # noqa: E402

_ROWS = _REPO_ROOT / "tests" / "_assembly_census.json"
_ARTIFACT = _REPO_ROOT / caa.DEFAULT_GOLD_ARTIFACT


# ── fixtures / helpers ────────────────────────────────────────────────────


def _row(qid: str = "q1", *, qtype: str = "multi-session",
         qdate: str = "2026-07-10") -> dict:
    return {
        "question_id": qid,
        "question_type": qtype,
        "question": "Where is the user flying?",
        "answer": "Lisbon",
        "question_date": qdate,
        "haystack_session_ids": ["sid-1"],
        "haystack_dates": ["2023-05-06"],
        "haystack_sessions": [[
            {"role": "user", "content": "I'm flying to Lisbon on 6 May."},
        ]],
        "answer_session_ids": ["sid-1"],
    }


def _row_two_sessions(qid: str = "q1") -> dict:
    """Two haystack sessions; the SECOND is gold (positional resolution)."""
    return {
        "question_id": qid,
        "question_type": "multi-session",
        "question": "Where is the user flying?",
        "answer": "Lisbon",
        "question_date": "2026-07-10",
        "haystack_session_ids": ["sid-0", "sid-1"],
        "haystack_dates": ["2023-01-01", "2023-05-06"],
        "haystack_sessions": [
            [{"role": "user", "content": "Unrelated: I like tea."}],
            [{"role": "user", "content": "I'm flying to Lisbon on 6 May."},
             {"role": "assistant", "content": "Got it."}],
        ],
        "answer_session_ids": ["sid-1"],
    }


_CLAIM_TEXT = "The user is flying to Lisbon on 6 May 2023."


def _point_props(point_id: str, **overrides) -> dict:
    props = {
        "id": point_id,
        "content": _CLAIM_TEXT,
        "session_id": "sid-1",
        "source_turn_id": "lme:q1:s0:t0",
        "createdAt": "2023-05-06T10:00:00Z",
        "posterior_alpha": 4.0,
        "posterior_beta": 1.0,
    }
    props.update(overrides)
    return props


def _sg(*, zero_seed: bool = False) -> Subgraph:
    """A one-anchor subgraph with provenance + measured EP confidence."""
    if zero_seed:
        return Subgraph(seeds=(), anchors=(), candidates=(), relations=(),
                        zero_seed=True, reserved_overflow=0, seed_fn="vector")
    return Subgraph(
        seeds=(("P1", 0.9),),
        anchors=("P1",),
        candidates=(),
        relations=(),
        zero_seed=False,
        reserved_overflow=0,
        seed_fn="vector",
        content_by_id={"P1": _CLAIM_TEXT},
        point_props={"P1": _point_props("P1")},
    )


def _sg_two_seeds() -> Subgraph:
    """Seeds deliberately NOT in ascending-id order (rank order is preserved)."""
    return Subgraph(
        seeds=(("P2", 0.91), ("P1", 0.42)),
        anchors=("P2",),
        candidates=(),
        relations=(),
        zero_seed=False,
        reserved_overflow=0,
        seed_fn="vector",
        content_by_id={"P2": _CLAIM_TEXT},
        point_props={"P2": _point_props("P2")},
    )


def _sg_with_reserved_relation() -> Subgraph:
    """Anchored NAND relation: dropped first when the word budget is tiny."""
    return Subgraph(
        seeds=(("P1", 0.9),),
        anchors=("P1",),
        candidates=(
            Candidate(point_id="P2", content="Other claim.", anchor_id="P1",
                      edge_type="NAND", hop=1, s_norm=1.0, raw_score=0.8,
                      score=0.8, damped=False, reserved=True),
        ),
        relations=(Relation(source_id="P1", relation="NAND",
                            target_id="P2", target_label=""),),
        zero_seed=False,
        reserved_overflow=0,
        seed_fn="vector",
        content_by_id={"P1": _CLAIM_TEXT, "P2": "Other claim."},
        point_props={"P1": _point_props("P1"),
                     "P2": _point_props("P2", content="Other claim.")},
    )


class _RecordingModel:
    def __init__(self, reply: str = "Lisbon") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


class _FakeJudge:
    model_id = "test-judge"
    model_spec = "test:judge"

    def __init__(self, verdict: bool = True) -> None:
        self.verdict = verdict
        self.calls: list[tuple] = []

    def judge(self, *, question_type, question, answer, hypothesis,
              abstention) -> bool:
        self.calls.append((question_type, question, answer, hypothesis,
                           abstention))
        return self.verdict

    def ping(self, probe: str) -> str:
        return "ok"


class _FakeQueryResult:
    def __init__(self, rows: list) -> None:
        self.result_set = rows


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def query(self, cypher: str, params: dict | None = None) -> _FakeQueryResult:
        del params
        self.calls.append(cypher)
        if "is_operator" in cypher and "RETURN n.id" in cypher:
            return _FakeQueryResult([
                ["P1", "content one", "sid-1"],
                ["P2", "content two", None],
            ])
        if "IMPL|NAND" in cypher:
            return _FakeQueryResult([["P1"], ["P3"]])
        if "CORRECTS" in cypher:
            return _FakeQueryResult([["P2", "P3"]])
        raise AssertionError(f"unexpected Cypher: {cypher!r}")


class _FakeSdk:
    def __init__(self, graph: _FakeGraph) -> None:
        self._graph = graph

    def _get_proj(self):
        return types.SimpleNamespace(g=self._graph)


def _patch_render_seams(monkeypatch, *, subgraph: Subgraph | None = None) -> None:
    # The subgraph seam is for B/C. Arm A is the gold-verbatim oracle and does
    # NOT go through the retrieval lane (there is no retrieve_for_question
    # import to patch — see test_arm_a_does_not_use_the_retrieval_lane).
    monkeypatch.setattr(
        caa, "build_subgraph",
        lambda sdk, question, namespace=None: subgraph or _sg())


def _run_arms(monkeypatch, reader, judge, *, rows=None, arms=caa.ARMS,
              qid_to_class=None, scan_fn=None, namespaces=None,
              subgraph=None, max_words=caa.DEFAULT_MAX_WORDS):
    _patch_render_seams(monkeypatch, subgraph=subgraph)

    def _factory(qid: str):
        namespace = f"ns-{qid}"
        if namespaces is not None:
            namespaces.append(qid)
        return object(), namespace

    return caa.run_experiment(
        rows or [_row()], reader=reader, judge=judge, arms=arms,
        sdk_factory=_factory, gold_claims={},
        qid_to_class=qid_to_class or {"q1": "interval"},
        scan_fn=scan_fn or (lambda sdk: caa.GraphScan(
            points=(), typed_relation_endpoint_ids=frozenset())),
        ep_fn=None, max_retries=0, max_words=max_words)


# ══ 1. each arm selects the right context source ══════════════════════════


def test_arm_a_is_the_gold_sessions_rendered_verbatim(monkeypatch):
    """Arm A is the gold-verbatim ORACLE CEILING (spec §3), not retrieval."""
    _patch_render_seams(monkeypatch)
    ctx = caa.build_context_arm(
        "A", caa.load_question_context(_row_two_sessions()), sdk=object())
    # The gold session (haystack index 1) renders verbatim, turn text intact.
    assert "I'm flying to Lisbon on 6 May." in ctx.text
    assert "Got it." in ctx.text
    # The non-gold session is NOT in the oracle's context.
    assert "Unrelated: I like tea." not in ctx.text
    # The frozen Current Date header + the render_context block shape survive.
    assert ctx.text.startswith("Current Date: 2026-07-10")
    assert "[session 1]" in ctx.text
    assert "(session date 2023-05-06)" in ctx.text
    assert "[session 0]" not in ctx.text
    assert ctx.context_source == "gold-verbatim"
    # A is never the subgraph sentinel.
    assert EMPTY_CONTEXT_SENTINEL not in ctx.text


def test_arm_a_resolves_answer_session_ids_positionally(monkeypatch):
    """The gold read is the positional join on ``answer_session_ids``."""
    _patch_render_seams(monkeypatch)
    row = _row_two_sessions()
    row["answer_session_ids"] = ["sid-0"]
    ctx = caa.build_context_arm(
        "A", caa.load_question_context(row), sdk=object())
    assert "Unrelated: I like tea." in ctx.text
    assert "I'm flying to Lisbon on 6 May." not in ctx.text


def test_arm_a_does_not_use_the_retrieval_lane(monkeypatch):
    """P0 regression: arm A must never call ``retrieve_for_question``."""
    assert not hasattr(caa, "retrieve_for_question")
    assert "retrieve_for_question" not in inspect.getsource(
        caa.build_context_arm)
    _patch_render_seams(monkeypatch)
    ctx = caa.build_context_arm(
        "A", caa.load_question_context(_row()), sdk=object())
    assert "I'm flying to Lisbon on 6 May." in ctx.text


def _row_many_gold_sessions(*, n: int = 4, words: int = 30,
                            qdate: str = "2026-07-10") -> dict:
    """``n`` gold sessions, each one turn of ``words`` unique tokens.

    Session ``i`` carries the token family ``s{i}w0 … s{i}w{words-1}`` so
    whole-block retention can be distinguished from a mid-block cut.
    """
    sids = [f"gsid-{i}" for i in range(n)]
    sessions = [
        [{"role": "user",
          "content": " ".join(f"s{i}w{j}" for j in range(words))}]
        for i in range(n)
    ]
    return {
        "question_id": "q1",
        "question_type": "multi-session",
        "question": "What do the sessions say?",
        "answer": "alpha beta gamma",
        "question_date": qdate,
        "haystack_session_ids": list(sids),
        "haystack_dates": [f"2023-01-0{i + 1}" for i in range(n)],
        "haystack_sessions": sessions,
        "answer_session_ids": list(sids),
    }


def _expected_gold_hits(row: dict) -> list[dict]:
    """The spec's arm-A hit shape, built independently of the runner."""
    hits: list[dict] = []
    for i, (sid, session) in enumerate(zip(
            row["haystack_session_ids"], row["haystack_sessions"], strict=True)):
        transcript = "\n".join(
            f"{str(turn.get('role') or 'unknown').title()}: "
            f"{turn.get('content') or ''}" for turn in session)
        hits.append({
            "id": f"lme:{row['question_id']}:s{i}",
            "content": transcript,
            "session_id": str(sid),
            "lme_session_index": i,
            "session_date": row["haystack_dates"][i],
        })
    return hits


def test_arm_a_raises_on_an_unresolvable_gold_session(monkeypatch):
    _patch_render_seams(monkeypatch)
    row = _row()
    row["answer_session_ids"] = ["sid-missing"]
    with pytest.raises(ValueError, match="not resolvable"):
        caa.build_context_arm(
            "A", caa.load_question_context(row), sdk=object())


# ── P0: arm A is capped at the SAME §5 budget as B/C ─────────────────────


def test_arm_a_under_the_cap_is_byte_identical_to_render_context(monkeypatch):
    """Below the cap, the block-preserving renderer changes nothing."""
    _patch_render_seams(monkeypatch)
    row = _row_many_gold_sessions()
    qctx = caa.load_question_context(row)
    ctx = caa.build_context_arm(
        "A", qctx, sdk=object(), max_words=caa.DEFAULT_MAX_WORDS)
    assert ctx.text == render_context(
        _expected_gold_hits(row), question_date=row["question_date"])
    assert ctx.budget_words <= caa.DEFAULT_MAX_WORDS


def test_arm_a_drops_whole_trailing_session_blocks_at_the_cap(monkeypatch):
    """P0: arm A is truncated at ``max_words`` by whole session blocks."""
    _patch_render_seams(monkeypatch)
    row = _row_many_gold_sessions(n=4, words=30)
    qctx = caa.load_question_context(row)
    full = caa.build_context_arm("A", qctx, sdk=object(), max_words=10**6)
    capped = caa.build_context_arm("A", qctx, sdk=object(), max_words=120)

    # The cap binds (the untruncated render does NOT fit) and holds after it.
    assert full.budget_words > 120
    assert capped.budget_words <= 120
    assert capped.budget_words == int(
        len(capped.text.split()) * 1.1)

    # Truncation is a whole-block prefix: the capped text is exactly the first
    # m blocks of the full text, so no block was cut mid-line.
    full_parts = full.text.split("\n\n")
    capped_parts = capped.text.split("\n\n")
    assert 0 < len(capped_parts) < len(full_parts)
    assert full_parts[:len(capped_parts)] == capped_parts

    # Every retained gold session is intact; every dropped one is fully gone.
    for i in range(4):
        if f"s{i}w0" in capped.text:
            assert all(f"s{i}w{j}" in capped.text for j in range(30)), i
        else:
            assert all(f"s{i}w{j}" not in capped.text for j in range(30)), i
    # Retained sessions are a prefix of the gold order (trailing drops only).
    retained = [i for i in range(4) if f"s{i}w0" in capped.text]
    assert retained == list(range(len(retained)))


def test_all_four_arms_share_one_final_text_budget(monkeypatch):
    """§5: the cap is measured on the final text and binds every arm."""
    _patch_render_seams(monkeypatch)
    qctx = caa.load_question_context(_row_many_gold_sessions(n=3, words=25))
    for cap in (60, 120, 200):
        for arm in caa.ARMS:
            ctx = caa.build_context_arm(arm, qctx, sdk=object(), max_words=cap)
            assert ctx.budget_words <= cap, (arm, cap, ctx.budget_words)


def test_b_c_reserve_the_header_budget_so_the_final_text_fits(monkeypatch):
    """A naive pre-header budget overshoots; the reserved budget does not."""
    from tortoise.subgraph_render import render_arm_b
    _patch_render_seams(monkeypatch)
    qctx = caa.load_question_context(_row())
    sg = _sg()
    full = caa.build_context_arm("B", qctx, sdk=object(), max_words=10**6)
    cap = full.budget_words - 1
    assert full.budget_words == cap + 1

    capped = caa.build_context_arm("B", qctx, sdk=object(), max_words=cap)
    assert capped.budget_words <= cap

    # The same cap handed straight to the renderer (the pre-header budget the
    # fix removes) leaves the final text one unit over: the regression guard.
    naive = render_arm_b(
        sg, haystack_session_ids=qctx.haystack_session_ids,
        max_words=cap, points_by_id=sg.point_props)
    naive_final = caa._with_date_header(naive.text, qctx.question_date)
    assert caa._budget_words(naive_final) > cap


def test_arm_a_truncation_never_drops_the_date_header(monkeypatch):
    _patch_render_seams(monkeypatch)
    row = _row_many_gold_sessions(n=3, words=40)
    ctx = caa.build_context_arm(
        "A", caa.load_question_context(row), sdk=object(), max_words=1)
    assert ctx.text == f"Current Date: {row['question_date']}"


def test_arm_b_uses_subgraph_and_excludes_raw_turn_text(monkeypatch):
    _patch_render_seams(monkeypatch)
    ctx = caa.build_context_arm(
        "B", caa.load_question_context(_row()), sdk=object())
    assert _CLAIM_TEXT in ctx.text
    assert "confidence: 0.80" in ctx.text
    assert "> user:" not in ctx.text          # NO raw turn text (§3)
    assert ctx.context_source == "subgraph"
    assert ctx.claims_rendered == 1
    assert ctx.zero_seed is False
    assert ctx.seed_fn == "vector"


def test_arm_c_is_arm_b_plus_provenance_turns(monkeypatch):
    _patch_render_seams(monkeypatch)
    qctx = caa.load_question_context(_row())
    b = caa.build_context_arm("B", qctx, sdk=object())
    c = caa.build_context_arm("C", qctx, sdk=object())
    assert "> user: I'm flying to Lisbon on 6 May." in c.text
    assert "> user:" not in b.text
    assert _CLAIM_TEXT in c.text
    assert c.context_source == "union"
    assert c.word_count >= b.word_count


def test_arm_b_zero_seed_renders_sentinel_with_header(monkeypatch):
    _patch_render_seams(monkeypatch, subgraph=_sg(zero_seed=True))
    ctx = caa.build_context_arm(
        "B", caa.load_question_context(_row()), sdk=object())
    assert EMPTY_CONTEXT_SENTINEL in ctx.text
    assert ctx.text.startswith("Current Date: 2026-07-10")
    assert ctx.zero_seed is True


# ══ 2. arm D — header-only no-context control ═════════════════════════════


def test_arm_d_builds_the_header_only_context():
    ctx = caa.build_context_arm(
        "D", caa.load_question_context(_row()), sdk=object())
    assert ctx.text == render_context([], question_date="2026-07-10")
    assert ctx.text == "Current Date: 2026-07-10\n\n"
    assert ctx.context_source == "no-context"
    assert ctx.zero_seed is None


def test_arm_d_without_a_date_renders_empty_evidence():
    ctx = caa.build_context_arm(
        "D", caa.load_question_context(_row(qdate="")), sdk=object())
    assert ctx.text == ""


# ══ §3 provenance — reserved_overflow + seed selection + manifest ════════


def test_reserved_overflow_propagates_from_render_to_the_run(monkeypatch):
    reader = LLMReader(_RecordingModel(), model_id="test-reader",
                       model_spec="test:reader")
    result = _run_arms(
        monkeypatch, reader, _FakeJudge(),
        subgraph=_sg_with_reserved_relation(), arms=("B", "C"), max_words=1)
    for arm in ("B", "C"):
        outcome = result["reports"][arm]["outcomes"][0]
        assert outcome["reserved_overflow"] >= 1
        assert result["reserved_overflow"]["q1"][arm] == (
            outcome["reserved_overflow"])
    assert result["reports"]["B"]["metrics"]["subgraph_size"][
        "total_relations_rendered"] == 0


def test_reserved_overflow_is_absent_when_nothing_is_dropped(monkeypatch):
    reader = LLMReader(_RecordingModel(), model_id="test-reader",
                       model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge(), arms=("B",))
    assert result["reports"]["B"]["outcomes"][0]["reserved_overflow"] == 0
    assert result["reserved_overflow"] == {}


def test_seed_ids_and_seed_fn_are_recorded_in_rank_order(monkeypatch):
    reader = LLMReader(_RecordingModel(), model_id="test-reader",
                       model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge(),
                       subgraph=_sg_two_seeds(), arms=("B",))
    outcome = result["reports"]["B"]["outcomes"][0]
    assert outcome["seed_ids"] == ["P2", "P1"]
    assert outcome["seed_fn"] == "vector"
    assert result["seed_selection"]["q1"]["B"] == {
        "seed_fn": "vector", "seed_ids": ["P2", "P1"]}


def test_manifest_records_seeds_serializer_and_permutation_provenance():
    reader = types.SimpleNamespace(model_spec="r", model_id="r")
    judge = types.SimpleNamespace(model_spec="j", model_id="j")
    manifest = caa.build_manifest(
        arms=caa.ARMS, gold_path="/tmp/gold.json", gold_sha="deadbeef",
        max_words=caa.DEFAULT_MAX_WORDS, reader=reader, judge=judge,
        seed_selection={"q1": {"B": {"seed_fn": "vector",
                                       "seed_ids": ["P1"]}}},
        reserved_overflow={"q1": {"B": 2, "C": 1}},
        embedder_model="BAAI/bge-small-en-v1.5",
        serializer_git_sha="abc123")
    # (b) serializer module path + git sha (§3 Frozen artifact).
    assert manifest["serializer_module_path"] == "tortoise/subgraph_render.py"
    assert manifest["serializer_git_sha"] == "abc123"
    # (c) the fixed §10.1 permutation seed, matching the leakage guard's own.
    assert manifest["session_index_permutation_seed"] == 0xD1CE
    assert manifest["session_index_permutation_seed"] == inspect.signature(
        lg.fixed_session_index_permutation).parameters["seed"].default
    # (a) eight selected seed ids in rank order + seed_fn + embedder model id.
    seeds = manifest["seed_selection"]
    assert seeds["seed_fn"] == "vector"
    assert seeds["mixed_seed_functions"] is False
    assert seeds["seed_limit"] == 64
    assert seeds["seed_count"] == 8
    assert seeds["embedder_model_id"] == "BAAI/bge-small-en-v1.5"
    assert seeds["per_question"]["q1"]["B"] == {
        "seed_fn": "vector", "seed_ids": ["P1"]}
    # reserved_overflow flags the question with the dropped-count.
    overflow = manifest["reserved_overflow"]
    assert overflow["flagged_question_ids"] == ["q1"]
    assert overflow["n_flagged_questions"] == 1
    assert overflow["total_dropped_reserved_lines"] == 3
    assert overflow["per_question"]["q1"] == {"B": 2, "C": 1}
    # arm A is no longer documented as a retrieval arm.
    arm_a_note = manifest["resolved_ambiguities"]["arm_a_source"]
    assert "GOLD" in arm_a_note
    assert "Task directive" not in arm_a_note


def test_manifest_documents_the_metric5_and_budget_resolutions():
    reader = types.SimpleNamespace(model_spec="r", model_id="r")
    judge = types.SimpleNamespace(model_spec="j", model_id="j")
    manifest = caa.build_manifest(
        arms=caa.ARMS, gold_path="g", gold_sha="s",
        max_words=caa.DEFAULT_MAX_WORDS, reader=reader, judge=judge)
    resolved = manifest["resolved_ambiguities"]
    # P2: the cardinality is stated, and it is the LIST one the tokenizer
    # docstring and the artifact's ``tokens`` field use.
    note = resolved["metric5_denominator"]
    assert "LIST" in note
    assert "len(tokenize(g))" in note
    assert "MIN_GOLD_TOKENS" in note
    assert "gold_evidence_claims.tokenize" in note
    # P0: the budget resolution says the cap binds the FINAL text, all arms.
    budget = resolved["context_budget_enforcement"]
    assert "FINAL" in budget
    assert "blocks" in budget
    assert "header" in budget


def test_manifest_defaults_repo_head_sha_and_embedder_model():
    reader = types.SimpleNamespace(model_spec="r", model_id="r")
    judge = types.SimpleNamespace(model_spec="j", model_id="j")
    manifest = caa.build_manifest(
        arms=caa.ARMS, gold_path="g", gold_sha="s",
        max_words=caa.DEFAULT_MAX_WORDS, reader=reader, judge=judge)
    sha = caa.repo_git_sha()
    assert manifest["serializer_git_sha"] == sha
    assert sha is None or (len(sha) == 40
                           and all(c in "0123456789abcdef" for c in sha))
    assert manifest["seed_selection"]["embedder_model_id"] == (
        caa.embedder_model_id())
    assert caa.embedder_model_id()
    assert manifest["seed_selection"]["per_question"] == {}
    assert manifest["reserved_overflow"]["per_question"] == {}


def test_run_manifest_carries_the_run_seed_and_overflow_provenance(monkeypatch):
    reader = LLMReader(_RecordingModel(), model_id="test-reader",
                       model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge(),
                       subgraph=_sg_with_reserved_relation(), arms=("B",),
                       max_words=1)
    manifest = caa.build_manifest(
        arms=("B",), gold_path="g", gold_sha="s", max_words=1,
        reader=reader, judge=_FakeJudge(),
        seed_selection=result["seed_selection"],
        reserved_overflow=result["reserved_overflow"],
        serializer_git_sha="x", embedder_model="m")
    assert manifest["seed_selection"]["per_question"]["q1"]["B"][
        "seed_ids"] == ["P1"]
    assert manifest["reserved_overflow"]["per_question"]["q1"]["B"] >= 1


# ══ 3. ONE prompt scaffolding across all four arms ════════════════════════


def test_four_arms_share_one_prompt_scaffolding(monkeypatch):
    model = _RecordingModel(reply="Lisbon")
    reader = LLMReader(model, model_id="test-reader", model_spec="test:reader")
    judge = _FakeJudge()
    result = _run_arms(monkeypatch, reader, judge)
    # one reader call per arm, same question.
    assert len(model.calls) == 4
    assert len(result["reports"]) == 4

    question = _row()["question"]
    suffix = f"\n\nQuestion: {question}\n\nAnswer:"
    systems = {system for system, _ in model.calls}
    assert systems == {system_prompt_for("multi-session")}
    evidences = []
    for _system, user in model.calls:
        assert user.startswith("Memory context:\n")
        assert user.endswith(suffix)
        assert user == build_reader_user_message(
            user[len("Memory context:\n"):-len(suffix)], question)
        evidences.append(user[len("Memory context:\n"):-len(suffix)])
    # The scaffolding is identical; only the {context} slot differs.
    assert len(set(evidences)) == 4


def test_prompt_hash_covers_the_scaffolding_and_is_stable():
    assert caa.reader_prompt_hash() == caa.reader_prompt_hash()
    assert len(caa.reader_prompt_hash()) == 16
    assert caa.reader_prompt_hash() != caa.stopwords_hash()


# ══ metric 5 — answer-bearing-claim presence ══════════════════════════════


def test_metric5_trivial_claim_never_matches():
    claim = {"claim": "Lisbon May offsite", "tokens": 3, "trivial": True}
    # Every token is present — still no match: trivial is never matched.
    assert caa.claim_matches_point(claim, "Lisbon May offsite plans") is False


def test_metric5_threshold_boundary_exactly_080_matches():
    claim = {"claim": "alpha beta gamma delta epsilon", "trivial": False}
    # 4 / 5 == 0.80 -> match at the boundary.
    assert caa.claim_matches_point(claim, "alpha beta gamma delta zzz") is True
    # 3 / 5 == 0.60 -> below the boundary.
    assert caa.claim_matches_point(claim, "alpha beta gamma zzz www") is False


def test_metric5_all_stopword_claim_never_matches():
    claim = {"claim": "the of and", "tokens": 0, "trivial": True}
    assert caa.claim_matches_point(claim, "the of and") is False


def test_metric5_denominator_uses_list_cardinality(monkeypatch):
    """P2: the denominator is ``len(tokenize(g))`` (list), not the set size.

    Worked example: gold ``|tokens(g)|`` is 9 (the token ``days`` repeats →
    set size 8). 7 shared distinct tokens give 7/9 = 0.778 (NO match) under
    list cardinality but 7/8 = 0.875 (match) under set cardinality.
    """
    claim = {
        "claim": "30 days. 31 days (including the last day) is also "
                 "acceptable.",
        "trivial": False,
    }
    claim["tokens"] = len(gec.tokenize(claim["claim"]))
    point = "30 days 31 days including last day also extra"
    assert claim["tokens"] == 9
    assert len(set(gec.tokenize(claim["claim"]))) == 8
    assert caa.claim_matches_point(claim, point) is False
    # One more shared token clears 0.80 on the list denominator (8/9 = 0.889).
    assert caa.claim_matches_point(claim, point + " acceptable") is True


def test_metric5_denominator_equals_the_artifact_token_field():
    """The denominator and the ``tokens`` field that drives ``trivial`` agree."""
    for text in (
        "30 days. 31 days (including the last day) is also acceptable.",
        "alpha beta gamma delta",
    ):
        tokens = gec.tokenize(text)
        claim = {"claim": text, "tokens": len(tokens),
                 "trivial": len(tokens) < gec.MIN_GOLD_TOKENS}
        # The artifact's ``tokens`` value IS the list denominator, so the
        # self-match ratio is (#distinct) / (recorded tokens).
        assert claim["tokens"] == len(tokens) >= gec.MIN_GOLD_TOKENS
        assert caa.claim_matches_point(claim, text) is (
            len(set(tokens)) / len(tokens) >= 0.80)


def test_metric5_duplicate_heavy_claim_is_not_auto_matched():
    """List cardinality is conservative: repetition inflates the denominator.

    ``"cat cat cat dog"`` has 4 recorded tokens but only 2 distinct ones, so
    a point containing every distinct token scores 2/4 = 0.50, not 1.00. A
    set denominator would shrink to 2 and auto-match — the exact
    tiny-denominator degeneracy ``MIN_GOLD_TOKENS`` exists to close, and the
    reason the denominator must stay the recorded list length.
    """
    text = "cat cat cat dog"
    tokens = gec.tokenize(text)
    assert len(tokens) == 4 and len(set(tokens)) == 2
    claim = {"claim": text, "tokens": len(tokens), "trivial": False}
    assert caa.claim_matches_point(claim, text) is False


def test_metric5_subthreshold_claim_never_matches_even_with_a_wrong_flag():
    """The trivial threshold is enforced on live tokens, not only the flag."""
    claim = {"claim": "Lisbon May", "tokens": 2, "trivial": False}
    # Every token is present (set ratio 1.0) — still refused: 2 < MIN_GOLD_TOKENS.
    assert caa.claim_matches_point(claim, "Lisbon May offsite plans") is False
    assert caa.claim_matches_point(
        {"claim": "Lisbon May", "tokens": 2, "trivial": True},
        "Lisbon May offsite plans") is False


def test_metric5_needs_at_least_one_answer_bearing_point():
    claim = {"claim": "alpha beta gamma delta epsilon", "trivial": False}
    empty = caa.GraphScan(points=(), typed_relation_endpoint_ids=frozenset())
    assert caa.metric5_present(empty, [claim]) is False
    hit = caa.GraphScan(
        points=(caa.GraphPoint("P1", "alpha beta gamma delta zzz", "s1"),),
        typed_relation_endpoint_ids=frozenset())
    assert caa.metric5_present(hit, [claim]) is True


# ══ metric 8 — typed-relation presence ════════════════════════════════════


def test_metric8_requires_typed_relation_endpoint_in_answer_bearing_set():
    scan = caa.GraphScan(
        points=(caa.GraphPoint("A", "claim a", None),
                caa.GraphPoint("B", "claim b", None)),
        typed_relation_endpoint_ids=frozenset({"B"}))
    # B is relation-endpointed but NOT answer-bearing -> 0.
    assert caa.metric8_present(scan, {"A"}) is False
    # A is answer-bearing and relation-endpointed -> 1.
    assert caa.metric8_present(
        replace(scan, typed_relation_endpoint_ids=frozenset({"A"})),
        {"A"}) is True


# ══ metric 9 — gold-provenance presence (content-free) ════════════════════


def test_metric9_is_content_free_unrelated_point_from_gold_session():
    scan = caa.GraphScan(
        points=(caa.GraphPoint(
            "P1", "completely unrelated grocery list", "gold-session"),),
        typed_relation_endpoint_ids=frozenset())
    assert caa.metric9_present(scan, {"gold-session"}) is True
    assert caa.metric9_present(scan, {"some-other-session"}) is False
    assert caa.metric9_present(scan, set()) is False


def test_graph_metrics_composes_5_8_9_end_to_end():
    claim = {"claim": "alpha beta gamma delta epsilon", "trivial": False}
    scan = caa.GraphScan(
        points=(caa.GraphPoint("P1", "alpha beta gamma delta zzz", "s1"),),
        typed_relation_endpoint_ids=frozenset({"P1"}))
    gm = caa.graph_metrics(scan, [claim], ["s1"])
    assert gm["answer_bearing_claim_present"] == 1
    assert gm["typed_relation_present"] == 1
    assert gm["gold_provenance_present"] == 1
    assert gm["answer_bearing_point_ids"] == ["P1"]


def test_graph_metric_rates_exclude_abstention_controls():
    per = {
        "q1": {"answer_bearing_claim_present": 1,
               "typed_relation_present": 1, "gold_provenance_present": 1},
        "q2": {"answer_bearing_claim_present": 0,
               "typed_relation_present": 1, "gold_provenance_present": 0},
        "q3_abs": {"answer_bearing_claim_present": 0,
                   "typed_relation_present": 1, "gold_provenance_present": 1},
    }
    rates = caa.graph_metric_rates(
        per, [q for q in per if not q.endswith("_abs")])
    assert rates["n"] == 2
    assert rates["metric5_rate"] == 0.5
    assert rates["metric8_rate"] == 1.0
    assert rates["metric9_rate"] == 0.5
    assert rates["conjunction_rate"] == 0.5


def test_run_experiment_wires_the_graph_metrics_into_outcomes(monkeypatch):
    claim = {"claim": "alpha beta gamma delta epsilon", "trivial": False}
    scan = caa.GraphScan(
        points=(caa.GraphPoint("P1", "alpha beta gamma delta zzz", "sid-1"),),
        typed_relation_endpoint_ids=frozenset({"P1"}))
    model = _RecordingModel(reply="Lisbon")
    reader = LLMReader(model, model_id="test-reader", model_spec="test:reader")
    result = _run_arms(
        monkeypatch, reader, _FakeJudge(),
        scan_fn=lambda sdk: scan)
    # gold_claims={} -> no answer-bearing point, so metrics 5 and 8 are 0.
    gm = result["reports"]["A"]["outcomes"][0]["metrics"]
    assert gm["answer_bearing_claim_present"] == 0
    assert result["graph_metrics"]["metric5_rate"] == 0.0
    assert result["graph_metrics"]["metric8_rate"] == 0.0
    # ... and metric 5/8 become 1 once the artifact supplies the claim.
    result2 = caa.run_experiment(
        [_row()], reader=reader, judge=_FakeJudge(), arms=("A",),
        sdk_factory=lambda qid: (object(), f"ns-{qid}"),
        gold_claims={"q1": [claim]}, qid_to_class={"q1": "interval"},
        scan_fn=lambda sdk: scan, ep_fn=None, max_retries=0)
    assert result2["graph_metrics"]["metric5_rate"] == 1.0
    assert result2["graph_metrics"]["metric8_rate"] == 1.0
    assert result2["graph_metrics"]["conjunction_rate"] == 1.0


# ══ metric 4 — per-class counts sum to 52 ═════════════════════════════════


def test_per_class_counts_sum_to_52():
    classes = caa.load_census_classes(_ROWS)
    answerable = Counter(
        cls for qid, cls in classes.items() if not qid.endswith("_abs"))
    assert sum(answerable.values()) == 52
    assert answerable["interval"] == 19
    assert answerable["ordering/compare"] == 32
    assert answerable["current-state"] == 1


def test_zero_seed_rate_is_reported_for_b_and_c_only(monkeypatch):
    model = _RecordingModel(reply="Lisbon")
    reader = LLMReader(model, model_id="test-reader", model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge(),
                       subgraph=_sg(zero_seed=True))
    assert result["reports"]["B"]["metrics"]["zero_seed_rate"] == 1.0
    assert result["reports"]["C"]["metrics"]["zero_seed_rate"] == 1.0
    assert result["reports"]["A"]["metrics"]["zero_seed_rate"] is None


def test_arm_metrics_reports_metric6_and_answerable_correct(monkeypatch):
    model = _RecordingModel(reply="Lisbon")
    reader = LLMReader(model, model_id="test-reader", model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge())
    b = result["reports"]["B"]["metrics"]
    assert b["correct_answerable"] == 1
    assert b["answerable_n"] == 1
    assert b["correct_per_1k_words"] is not None
    assert b["subgraph_size"]["mean_claims_admitted_per_question"] == 1.0
    assert b["subgraph_size"]["mean_claims_rendered_per_question"] == 1.0
    assert b["subgraph_size"]["total_relations_rendered"] == 0
    # arm A renders no subgraph — metric 6 is absent, not fabricated.
    assert "subgraph_size" not in result["reports"]["A"]["metrics"]
    # metric 7 — the arm-D floor is reported on every arm.
    assert result["reports"]["A"]["metrics"]["arm_d_floor"] == {
        "correct": 1, "n": 1}


def test_refusal_rate_uses_the_product_classifier(monkeypatch):
    reader = LLMReader(_RecordingModel(reply="I don't know."),
                       model_id="test-reader", model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge(verdict=False))
    assert result["reports"]["A"]["metrics"]["refusal_rate"] == 1.0


# ══ report shape is consumable ════════════════════════════════════════════


def test_report_shape_is_consumable(monkeypatch, tmp_path):
    model = _RecordingModel(reply="Lisbon")
    reader = LLMReader(model, model_id="test-reader", model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge(),
                       qid_to_class=caa.load_census_classes(_ROWS))
    for arm in caa.ARMS:
        report = result["reports"][arm]
        # exact existing-lane top-level shape.
        assert set(report) >= {"arm", "methodology", "n_outcomes", "outcomes"}
        assert report["arm"] == arm
        assert report["n_outcomes"] == len(report["outcomes"]) == 1
        for key in ("reader_model_spec", "reader_prompt_hash", "judge_model"):
            assert str(report["methodology"].get(key) or "").strip()
        outcome = report["outcomes"][0]
        for key in ("question_id", "label", "hypothesis", "context_tokens",
                    "measure_facts"):
            assert key in outcome

        # the #2578 consumer classifies the outcome without error.
        from tools.longmem_eval.measure_temporal import classify_outcome
        verdict = classify_outcome(outcome)
        assert verdict["correct"] is True

        # and the file round-trips.
        path = caa.write_report(report, tmp_path)
        assert json.loads(path.read_text(encoding="utf-8"))["arm"] == arm


def test_reader_constancy_passes_for_four_aligned_arms_and_detects_mismatch():
    meta = {
        arm: {"reader_model_spec": "test:reader",
              "reader_prompt_hash": caa.reader_prompt_hash(),
              "judge_model": "test:judge", "reader_model": "test-reader"}
        for arm in caa.ARMS
    }
    caa.assert_reader_constancy(meta)  # must not raise
    bad = {arm: dict(block) for arm, block in meta.items()}
    bad["B"]["reader_prompt_hash"] = "different"
    with pytest.raises(ValueError):
        caa.assert_reader_constancy(bad)


def test_all_four_methodology_blocks_are_constancy_aligned(monkeypatch):
    model = _RecordingModel(reply="Lisbon")
    reader = LLMReader(model, model_id="test-reader", model_spec="test:reader")
    result = _run_arms(monkeypatch, reader, _FakeJudge())
    blocks = result["methodologies"]
    assert set(blocks) == set(caa.ARMS)
    assert {b["reader_model_spec"] for b in blocks.values()} == {"test:reader"}
    assert {b["judge_model"] for b in blocks.values()} == {"test:judge"}
    assert {b["reader_prompt_hash"] for b in blocks.values()} == {
        caa.reader_prompt_hash()}


# ══ environment constancy — fresh namespace per question ══════════════════


def test_fresh_namespace_per_question(monkeypatch):
    seen: list[str] = []
    result = _run_arms(
        monkeypatch, LLMReader(_RecordingModel(), model_id="test-reader",
                               model_spec="test:reader"),
        _FakeJudge(), rows=[_row("q1"), _row("q2")], namespaces=seen)
    assert seen == ["q1", "q2"]
    namespaces = {o["namespace"]
                  for o in result["reports"]["A"]["outcomes"]}
    assert namespaces == {"ns-q1", "ns-q2"}


# ══ graph scan (hermetic seam) + live skip ════════════════════════════════


def test_scan_eval_graph_reads_points_and_typed_relations():
    graph = _FakeGraph()
    scan = caa.scan_eval_graph(_FakeSdk(graph))
    assert {p.point_id for p in scan.points} == {"P1", "P2"}
    assert scan.points[0].session_id == "sid-1"
    assert scan.points[1].session_id is None
    assert scan.typed_relation_endpoint_ids == frozenset({"P1", "P2", "P3"})


@pytest.mark.skipif(not os.environ.get("TORTOISE_DB_URI"),
                    reason="live FalkorDB required (TORTOISE_DB_URI unset)")
def test_scan_eval_graph_live_smoke():
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(namespace="caa_test_empty_namespace")
    try:
        scan = caa.scan_eval_graph(sdk)
        assert isinstance(scan, caa.GraphScan)
    finally:
        sdk.close()


# ══ controls / CLI / §9.5 gate ════════════════════════════════════════════


def test_render_path_has_no_gold_input():
    params = inspect.signature(caa.build_context_arm).parameters
    assert not any("gold" in name.lower() for name in params)
    # metrics 5/8/9 live in the separate metrics layer.
    assert callable(caa.load_gold_claims)
    assert callable(caa.answer_bearing_point_ids)


def test_word_stats_reports_the_metric2_distribution():
    stats = caa.word_stats([10, 20, 30, 40])
    assert stats["n"] == 4
    assert stats["mean"] == 25.0
    assert stats["median"] == 25.0
    assert stats["max"] == 40
    assert stats["distinct"] == 4
    assert stats["iqr"] is not None


def test_cli_parser_accepts_the_mandated_flags():
    args = caa.build_arg_parser().parse_args([
        "--instances", "i.json", "--work-dir", "w", "--out-dir", "o",
        "--arm", "B", "--limit", "3", "--mock"])
    assert args.instances == "i.json"
    assert args.work_dir == "w"
    assert args.out_dir == "o"
    assert args.arm == "B"
    assert args.limit == 3
    assert args.mock is True
    default = caa.build_arg_parser().parse_args([
        "--instances", "i", "--work-dir", "w", "--out-dir", "o"])
    assert default.arm is None


def test_main_refuses_without_the_gold_artifact(tmp_path, capsys):
    instances = tmp_path / "instances.json"
    instances.write_text("[]", encoding="utf-8")
    rc = caa.main([
        "--instances", str(instances),
        "--work-dir", str(tmp_path / "work"),
        "--out-dir", str(tmp_path / "out"),
        "--gold-artifact", str(tmp_path / "missing.json"),
        "--mock"])
    assert rc == 2
    assert "artifact not found" in capsys.readouterr().err


def test_gold_artifact_gate_matches_the_spec_path():
    assert caa.DEFAULT_GOLD_ARTIFACT.endswith(
        "gold-evidence-claims.json")
    assert _ARTIFACT.exists()
