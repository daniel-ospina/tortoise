"""Issue #2071 tests — spot-check full-semantic judge (owner decision
2026-08-31).

Locks the implemented contract (steps 1–7 of the scoping package,
docs/planning/2026-08-31-2071-scoping-package.md):

  1. the 21-question composition is COMMITTED (reproducibility gap closed)
     with the 0a995998 int-answer quirk cleaned;
  2. ``ask_spotcheck._grade`` routes EVERY question to the semantic judge
     (``build_judge(_judge_spec())`` — the official gpt-4o anscheck, the spec
     named explicitly and never inherited from the run default; benchmark-identical
     to the graded eval); the word-overlap bar is REMOVED from the live
     path (no callers remain — pinned both behaviorally and in source);
  3. the no-key runtime contract: fail-fast pre-flight names the judge
     provider key BEFORE any question is graded — never a silent fallback
     to the removed bar;
  4. output provenance: per-question ``judge``/``judge_model`` fields;
  5. the I2 consistency harness (spot-check semantic vs graded-eval
     semantic on the shared population — agreement by construction) with
     the divergence policy pinned (over-credit = blocking; temporal
     off-by-one + _abs marker = recorded findings);
  6/7. the I1 key-gated probe harness with the offline fake-judge pin
       (3/3 True on curated correct paraphrases);
  the graded eval surface is untouched (JUDGE_RUBRIC_ID/fingerprint
  unchanged — source-pinned).

Fully offline: scripted fakes, no API keys, no dataset download (the
composed fixture ships in-repo).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import (  # noqa: E402, RUF100
    _COMMITTED_FIXTURE,
    _grade,
    _load_composition,
    _normalize,
    _record,
    _require_judge_key,
)
from tools.ask_spotcheck import (
    main as ask_spotcheck_main,
)
from tools.ask_spotcheck_consistency import (  # noqa: E402, RUF100
    CURATED_ANSWERS,
    LONG_GOLD_QIDS,
    OFFLINE_RULES,
    _default_answers,
    _offline_judge,
    run_consistency,
)
from tools.ask_spotcheck_probe import (  # noqa: E402, RUF100
    _probes,
    run_probe,
)
from tools.ask_spotcheck_probe import (
    main as probe_main,
)
from tools.longmem_eval.judge import (  # noqa: E402, RUF100
    _TEMPLATES,
    MockJudge,
    ScriptedSemanticJudge,
)

FIXTURE = Path(_COMMITTED_FIXTURE)
LEGACY_FIXTURE = "/tmp/ask_spotcheck.json"


def _composition() -> list[dict]:
    return _load_composition(str(FIXTURE))


class _RecordingJudge:
    """Scriptable semantic-judge fake that records every call (the
    compliant-model pattern — the recorded call shape pins the judge-call
    contract, e.g. that _grade forwards the composition's question_type
    and abstention flag exactly as the graded eval's run.py call site)."""

    model_id = "recording-fake"

    def __init__(self, verdict: bool = True):
        self.verdict = verdict
        self.calls: list[dict] = []

    def judge(self, *, question_type, question, answer, hypothesis,
              abstention) -> bool:
        self.calls.append(dict(
            question_type=question_type, question=question, answer=answer,
            hypothesis=hypothesis, abstention=abstention))
        return self.verdict

    def ping(self, probe: str) -> str:
        return "recording ping ok"


def _result(answer: str, *, abstained: bool = False,
            question_type: str | None = None) -> dict:
    return {"abstained": abstained, "answer": answer,
            "question_type": question_type}


# ── Step 1: the committed fixture ─────────────────────────────────────────

def test_fixture_committed_21_questions_with_long_golds():
    """The 21-question composition is committed in-repo (reproducibility
    gap closed, #2071 step 1) with the 3 SSP long-gold questions and their
    rubric golds (79/68/63 words)."""
    questions = _composition()
    assert len(questions) == 21
    by_id = {q["question_id"]: q for q in questions}
    assert LONG_GOLD_QIDS == ("d6233ab6", "1d4e3b97", "b0479f84")
    assert len(by_id["d6233ab6"]["answer"].split()) == 79
    assert len(by_id["1d4e3b97"]["answer"].split()) == 68
    assert len(by_id["b0479f84"]["answer"].split()) == 63
    # every question carries the fields the live lane + judge need
    for q in questions:
        assert q["question_id"] and q["question"] and q["answer"]
        assert q.get("question_type"), q["question_id"]
        assert q.get("haystack_sessions")


def test_fixture_data_quirk_0a995998_answer_stringified():
    """The composition data quirk is cleaned: 0a995998's int answer is a
    string in the committed fixture (the old /tmp file carried an int)."""
    by_id = {q["question_id"]: q for q in _composition()}
    assert by_id["0a995998"]["answer"] == "3"
    assert isinstance(by_id["0a995998"]["answer"], str)
    assert all(isinstance(q["answer"], str) for q in _composition())


def test_fixture_matches_legacy_composition():
    """Compat pin: the committed fixture's question set + golds equal the
    legacy /tmp/ask_spotcheck.json composition (the pre-#2071 live file),
    so a regenerated fixture cannot silently change the measured
    population. Skips where the legacy file is absent (portability)."""
    if not os.path.exists(LEGACY_FIXTURE):
        pytest.skip(f"{LEGACY_FIXTURE} absent — legacy compat pin skipped")
    with open(LEGACY_FIXTURE) as f:
        legacy = json.load(f)
    assert isinstance(legacy, list)
    committed = _composition()
    assert {q["question_id"] for q in committed} == \
        {q["question_id"] for q in legacy}
    c = {q["question_id"]: q for q in committed}
    for q in legacy:
        assert c[q["question_id"]]["answer"] == str(q["answer"])


def test_load_composition_resolution_order(tmp_path, monkeypatch):
    """Path resolution: explicit path → TORTOISE_SPOTCHECK_FIXTURE env →
    committed fixture → /tmp legacy; accepts a {questions: [...]} wrapper."""
    # explicit path wins
    assert _load_composition(str(FIXTURE))[0]["question_id"]
    # env wins over the committed default
    wrapper = {"description": "provenance wrapper", "questions": []}
    p = tmp_path / "wrapper.json"
    p.write_text(json.dumps(wrapper))
    monkeypatch.setenv("TORTOISE_SPOTCHECK_FIXTURE", str(p))
    assert _load_composition(None) == []
    # committed fallback when neither given nor env
    monkeypatch.delenv("TORTOISE_SPOTCHECK_FIXTURE", raising=False)
    loaded = _load_composition(None)
    assert len(loaded) == 21
    # missing everywhere (incl. the committed default and the /tmp legacy
    # fallback) → FileNotFoundError naming the candidates
    import tools.ask_spotcheck as _ask_spotcheck_mod
    monkeypatch.setattr(
        _ask_spotcheck_mod, "_LEGACY_FIXTURE",
        str(tmp_path / "legacy-missing.json"))
    monkeypatch.setattr(
        _ask_spotcheck_mod, "_COMMITTED_FIXTURE",
        str(tmp_path / "committed-missing.json"))
    with pytest.raises(FileNotFoundError):
        _load_composition(str(tmp_path / "nope.json"))
    # unknown wrapper shape → ValueError
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"oops": 1}))
    with pytest.raises(ValueError):
        _load_composition(str(bad))


def test_composition_question_types_all_judge_gradeable():
    """Every composition question_type is an official anscheck template —
    the semantic judge can grade EVERY question (the owner decision's
    full-semantic contract) without ValueError."""
    for q in _composition():
        assert q["question_type"] in _TEMPLATES, q["question_id"]


# ── Step 2: full-semantic grading in _grade ───────────────────────────────

def test_grade_routes_every_question_to_semantic_judge():
    """Non-_abs questions: _grade calls the semantic judge EXACTLY once per
    question with the run.py call-site shape (question_type from the
    composition, answer=gold, hypothesis=reader answer, abstention=False)
    and returns the judge's verdict with kind='llm'."""
    questions = _composition()
    for q in questions:
        if "_abs" in q["question_id"]:
            continue
        judge = _RecordingJudge(verdict=True)
        ok, note, kind = _grade(q, _result("some answer"), judge)
        assert ok is True and kind == "llm", q["question_id"]
        assert "semantic judge=True" in note
        assert len(judge.calls) == 1, q["question_id"]
        call = judge.calls[0]
        assert call["question_type"] == q["question_type"]
        assert call["question"] == q["question"]
        assert call["answer"] == q["answer"]
        assert call["hypothesis"] == "some answer"
        assert call["abstention"] is False


def test_grade_short_gold_no_word_overlap_decided_by_judge():
    """The word-overlap bar is GONE from the live path: a short-gold answer
    with ZERO exact-word overlap ('Two weeks' → 'A fortnight') is graded by
    the judge's verdict, not by lexical overlap — under the removed bar this
    graded False; under the full-semantic path the judge decides."""
    by_id = {q["question_id"]: q for q in _composition()}
    q = by_id["e4e14d04"]
    assert _normalize(q["answer"]) == "two weeks"
    judge = _RecordingJudge(verdict=True)
    ok, _note, kind = _grade(q, _result("A fortnight"), judge)
    assert ok is True and kind == "llm"
    assert judge.calls[0]["hypothesis"] == "A fortnight"
    # and the judge's False verdict propagates (no hidden lexical rescue)
    judge2 = _RecordingJudge(verdict=False)
    ok2, _note2, _kind2 = _grade(q, _result("A fortnight"), judge2)
    assert ok2 is False


def test_word_overlap_bar_removed_from_source():
    """Source pin: the removed bar's arithmetic (``max(2,
    len(gold_words)//2)`` on unique words) has no remaining CODE callers in
    the spot-check module — the only mention left is the docstring that
    records the removal."""
    src = Path(
        Path(__file__).resolve().parent.parent / "tools" / "ask_spotcheck.py"
    ).read_text()
    # the old-bar variable/arithmetic exist only in the removal-record
    # docstring, never in code
    assert src.count("gold_words") == 1
    assert "gold_words = set" not in src
    assert "len(gold_words) // 2" not in src


def test_grade_abs_marker_path_short_circuits_before_judge():
    """The _abs marker path is UNCHANGED and precedes the judge call: an
    abstained-with-marker answer grades True deterministically (kind
    'marker') and the judge is NOT called."""
    by_id = {q["question_id"]: q for q in _composition()}
    q = by_id["f4f1d8a4_abs"]
    judge = _RecordingJudge(verdict=False)  # would fail if called
    ok, note, kind = _grade(
        q, _result("The asked information is absent from the context.",
                   abstained=True), judge)
    assert ok is True and kind == "marker"
    assert "abstain=True" in note
    assert judge.calls == []


def test_grade_abs_falls_through_to_judge_abstention_template():
    """An _abs answer that did NOT abstain with markers falls through to the
    semantic judge with abstention=True (the judge's abstention template
    decides — a committed-but-wrong _abs answer must not pass)."""
    by_id = {q["question_id"]: q for q in _composition()}
    q = by_id["f4f1d8a4_abs"]
    judge = _RecordingJudge(verdict=False)
    ok, _note, kind = _grade(
        q, _result("Your dad gave you a watch.", abstained=False), judge)
    assert ok is False and kind == "llm"
    assert judge.calls[0]["abstention"] is True
    assert judge.calls[0]["question_type"] == q["question_type"]


# ── Step 4: provenance ────────────────────────────────────────────────────

def test_record_includes_judge_provenance_fields():
    """Spot-check output records per-question scoring provenance (#2071
    step 4): judge (llm|marker) + judge_model."""
    by_id = {q["question_id"]: q for q in _composition()}
    q = by_id["0100672e"]
    rec = _record(q, _result("$12"), _RecordingJudge(verdict=True))
    assert rec["judge"] == "llm"
    assert rec["judge_model"] == "recording-fake"
    assert rec["ok"] is True
    assert rec["question_id"] == "0100672e"
    assert rec["abstained"] is False
    abs_q = by_id["ba358f49_abs"]
    rec2 = _record(
        abs_q,
        _result("The asked information is absent from the context.",
                abstained=True),
        _RecordingJudge(verdict=False))
    assert rec2["judge"] == "marker"
    assert rec2["ok"] is True


# ── Step 3: no-key runtime contract ───────────────────────────────────────

def test_fail_fast_no_key_names_openai_api_key(monkeypatch):
    """No-key contract: with the default judge spec
    (openai:gpt-4o-2024-08-06) and OPENAI_API_KEY absent, the pre-flight
    check raises a RuntimeError NAMING OPENAI_API_KEY — never a silent
    fallback to the removed word-overlap bar."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    with pytest.raises(RuntimeError) as ei:
        _require_judge_key()
    assert "OPENAI_API_KEY" in str(ei.value)
    assert "word-overlap" in str(ei.value)


def test_fail_fast_passes_with_key(monkeypatch):
    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _require_judge_key() == "OPENAI_API_KEY"


def test_one_judge_spec_serves_the_preflight_and_the_judge(monkeypatch):
    """#8486: the pre-flight and ``build_judge`` must resolve from ONE spec.

    The pre-flight names the OFFICIAL judge; ``build_judge()`` called with no
    spec resolves ``DEFAULT_JUDGE_MODEL``, which the #8486 owner ruling of
    2026-10-27 re-pinned to DeepSeek. With the two unlinked they disagreed
    about which model was about to grade — measured as two silent failures, both
    the #2071 decision inverted: a box holding only ``OPENAI_API_KEY`` passed
    the key check and then died on an uncaught ``ValueError``, and a both-keys
    box graded with a NON-official judge while printing the official key env.
    So the fallback is pinned to the OFFICIAL constant, and NO call site may
    build a judge without it — pinned in SOURCE, because the behavioural path
    needs a live key to observe.
    """
    import tools.ask_spotcheck as _mod
    from tools.longmem_eval.judge import DEFAULT_JUDGE_MODEL, OFFICIAL_JUDGE_MODEL

    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    assert _mod._judge_spec() == OFFICIAL_JUDGE_MODEL
    assert _mod._judge_spec() != DEFAULT_JUDGE_MODEL

    src = Path(_mod.__file__).read_text(encoding="utf-8")

    # ⛔ AST, NOT A REGEX (review finding): a regex keyed on `=`/`return` sees
    # only the call shapes someone happened to write. This walks every CALL to
    # `build_judge` in the module and requires the spec it passes to BE
    # `_judge_spec()` — the single owner the pre-flight and the grader both read
    # — or a literal `mock=True`. Two blank spots closed together here:
    #
    #   • SHAPES THAT LOOK LIKE AN ARGUMENT BUT ARE NOT (#8486 review):
    #     `build_judge(None)` passes a positional `None` and
    #     `build_judge(mock=False)` passes a FALSY `mock` — both fall through to
    #     ``judge.DEFAULT_JUDGE_MODEL`` (`build_judge`'s `raw_spec = spec or env
    #     or DEFAULT_JUDGE_MODEL`, and its MockJudge branch needs a TRUTHY
    #     `mock`), so skipping on "has an arg" or "has a mock kwarg" re-created
    #     the exact split this pin exists to stop.
    #   • A SPEC THAT IS NOT THE OWNER'S (#8486 review):
    #     `build_judge(OFFICIAL_JUDGE_MODEL)` — or any other literal — passes an
    #     EXPLICIT spec, so a "has an argument ⇒ explicit" rule waved it
    #     through while the pre-flight checked the env-resolved spec and the
    #     grader graded another. Same defect one step along: the pin has to
    #     check WHO decides the spec, not that somebody did.
    def _bare_build_judge_calls(source: str, where: str) -> list[str]:
        tree = ast.parse(source)
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", None))
            if name != "build_judge":
                continue
            # The spec must BE the owner's: the `spec` argument — positional,
            # or the keyword form `spec=_judge_spec()` — has to be a CALL to
            # `_judge_spec()`, not a literal, a name, or `None`.
            spec_arg = node.args[0] if node.args else None
            if spec_arg is None:
                for k in node.keywords:
                    if k.arg == "spec":
                        spec_arg = k.value
            if (isinstance(spec_arg, ast.Call)
                    and getattr(spec_arg.func, "id", None) == "_judge_spec"):
                continue
            # `mock=True` is the one other explicit form; `mock=False` is the
            # default judged by a real model, so it still has to name its spec.
            if any(k.arg == "mock"
                   and isinstance(k.value, ast.Constant)
                   and k.value.value is True
                   for k in node.keywords):
                continue
            bad.append(f"{where}:{node.lineno}")
        return bad

    assert _bare_build_judge_calls(src, "ask_spotcheck.py") == []

    # ⛔ THE #2071 LANE IS THREE MODULES, NOT ONE. The scan originally read
    # `tools/ask_spotcheck.py` alone while the claim was "NO call site may build
    # a judge without it" — so the two SIBLING legs kept the split (pre-flight
    # on the official key, judge on the DeepSeek default) and the suite stayed
    # green over it (caught in review, #8486).
    #
    # The set is ENUMERATED HERE, not derived from the tree, and that is what the
    # assertion below pins: it is the two modules that import the pre-flight
    # today (verified: `_require_judge_key`/`_judge_spec` importers in `tools/`
    # are exactly these), so a rename cannot silently shrink the scan to an
    # empty set. ⛔ A FOURTH IMPORTER WOULD NOT BE SCANNED — if one appears,
    # extend this tuple; the comment says what is checked rather than claiming
    # "every module", which is the over-claim a previous round shipped.
    import tools.ask_spotcheck_consistency as _consistency
    import tools.ask_spotcheck_probe as _probe

    siblings = {Path(m.__file__).name: m for m in (_probe, _consistency)}
    assert set(siblings) == {"ask_spotcheck_probe.py",
                             "ask_spotcheck_consistency.py"}, siblings
    for name, mod in siblings.items():
        sibling_src = Path(mod.__file__).read_text(encoding="utf-8")
        assert _bare_build_judge_calls(sibling_src, name) == [], (
            f"{name} builds a judge without `_judge_spec()` as its spec: its "
            f"pre-flight checks one judge's key while a different model grades "
            f"(#8486)")
        # ⛔ NO SUBSTRING CHECK HERE (review finding): `assert "_judge_spec" in
        # sibling_src` was TRUE for a module whose only mention was a docstring
        # or an import, so it stayed green over a call site that re-derived its
        # own spec — a claim satisfied by prose is not a check. The AST scan
        # above is now the load-bearing half for BOTH defects (a missing spec
        # and a spec that is not the owner's), so a second, weaker assertion
        # would only invite the same false confidence back.

    # ⛔ THE GUARD'S OWN COVERAGE (#8486 review). A source pin whose scope is
    # narrower than its claim is how the #2071 split survived two rounds, so
    # the shapes that INHERIT the default — or that pass a spec the pre-flight
    # did not resolve — are asserted directly.
    #
    # ⛔ AND THE ASSERTION PINS THE SET, NOT ITS SIZE (review finding): asserting
    # `!= []` only asked that SOME shape be flagged, so a helper mutated back to
    # "any argument counts as explicit" still passed on the argument-less shapes
    # while silently losing the four "who decides the spec" cases. Every shape is
    # on its own line and every line number is required, so a rule that stops
    # firing is a red row naming the shape it stopped firing on.
    inheriting = [
        "build_judge()",                      # 1 — no spec at all
        "build_judge(None)",                  # 2 — falsy positional
        "build_judge('')",                    # 3 — falsy constant
        "build_judge(mock=False)",            # 4 — falsy `mock` judges for real
        "build_judge(spec=None)",             # 5 — falsy keyword spec
        "build_judge(OFFICIAL_JUDGE_MODEL)",  # 6 — a spec that is not the owner's
        "build_judge(spec)",                  # 7 — a name, not the owner's call
        "build_judge(other_spec())",          # 8 — some OTHER resolver
    ]
    assert _bare_build_judge_calls("\n".join(inheriting) + "\n", "mutant") == [
        f"mutant:{i}" for i in range(1, len(inheriting) + 1)], (
        "the guard stopped flagging an inheriting shape — see the list above; a "
        "narrower helper passes `!= []` and re-opens the #8486 split")
    explicit = [
        "build_judge(_judge_spec())",
        "build_judge(spec=_judge_spec())",
        "build_judge(mock=True)",
    ]
    assert _bare_build_judge_calls("\n".join(explicit) + "\n", "explicit") == [], (
        "the guard flags a call that DOES name its spec through the owner")


def test_probe_live_path_builds_the_judge_it_preflighted(monkeypatch, capsys):
    """The I1 probe's live path must grade with the spec its pre-flight checked.

    The source pin above is structural; this is the behavioural half, and it
    needs no network: `build_judge` is replaced by a recorder, so the test sees
    the SPEC ARGUMENT the tool actually passes. With only the official key set
    (the box shape the split broke), the recorded spec must still be the
    OFFICIAL one — before this fix it was whatever `DEFAULT_JUDGE_MODEL`
    happened to be.
    """
    import tools.ask_spotcheck_probe as _probe
    from tools.longmem_eval.judge import OFFICIAL_JUDGE_MODEL, ScriptedSemanticJudge

    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for k in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    seen = []

    def _recorder(spec=None, **kw):
        seen.append(spec)
        return ScriptedSemanticJudge(rules=OFFLINE_RULES)

    monkeypatch.setattr(_probe, "build_judge", _recorder)
    rc = _probe.main(["--fixture", str(FIXTURE)])
    assert seen == [OFFICIAL_JUDGE_MODEL], seen
    assert rc == 0


def test_fail_fast_honors_judge_model_provider(monkeypatch):
    """TORTOISE_LME_JUDGE_MODEL naming another provider checks THAT
    provider's key."""
    monkeypatch.setenv("TORTOISE_LME_JUDGE_MODEL", "openrouter:qwen/qwen3.8-max")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as ei:
        _require_judge_key()
    assert "OPENROUTER_API_KEY" in str(ei.value)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert _require_judge_key() == "OPENROUTER_API_KEY"
    # unknown provider → ValueError naming the known set
    monkeypatch.setenv("TORTOISE_LME_JUDGE_MODEL", "notaprovider:model")
    with pytest.raises(ValueError) as ei:
        _require_judge_key()
    assert "notaprovider" in str(ei.value)


def test_ask_spotcheck_main_fail_fast_exit(monkeypatch, capsys):
    """main() exits 2 BEFORE any question is graded when the judge key is
    absent — the observed contract for key-free local runs."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    rc = ask_spotcheck_main(["--fixture", str(FIXTURE)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err


def test_probe_live_gated_without_key(monkeypatch, capsys):
    """The I1 probe's LIVE path is key-gated: without the judge key it
    exits 2 naming the prerequisite (never attempts the LLM call)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    rc = probe_main(["--fixture", str(FIXTURE)])
    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


# ── MockJudge CI substitute boundary + scripted semantic fake ─────────────

def test_mockjudge_containment_boundary_pinned():
    """The CI substitute boundary (why the live lane MUST be semantic): the
    deterministic containment judge still Falses a correct-but-paraphrased
    long-gold answer (unreachable bar) and still credits verbatim short
    golds (precision preserved — the no-regression floor)."""
    by_id = {q["question_id"]: q for q in _composition()}
    q = by_id["d6233ab6"]
    # correct paraphrase → containment False (the #2071 defect class)
    assert MockJudge().judge(
        question_type="single-session-preference",
        question=q["question"], answer=q["answer"],
        hypothesis=CURATED_ANSWERS["d6233ab6"]["answer"],
        abstention=False) is False
    # verbatim short gold → containment True (short-gold precision intact)
    short = by_id["0100672e"]
    assert MockJudge().judge(
        question_type="multi-session", question=short["question"],
        answer=short["answer"], hypothesis=short["answer"],
        abstention=False) is True


def test_scripted_semantic_judge_pins():
    """The scripted semantic fake (offline CI substitute for long-gold
    fixtures): scripted paraphrase → True, wrong answer → default False,
    and it accepts the full Judge.judge call shape."""
    judge = ScriptedSemanticJudge(rules=OFFLINE_RULES)
    assert judge.judge(
        question_type="single-session-preference", question="q",
        answer="gold", hypothesis=CURATED_ANSWERS["d6233ab6"]["answer"],
        abstention=False) is True
    assert judge.judge(
        question_type="single-session-preference", question="q",
        answer="gold", hypothesis="Documentaries are a fine choice.",
        abstention=False) is False
    assert judge.ping("x") == "scripted semantic ping ok"
    assert judge.model_id == "scripted-semantic"


# ── Step 5: I2 consistency harness ────────────────────────────────────────

def test_consistency_lane_agreement_by_construction_offline():
    """Offline: the spot-check semantic verdict and the graded-eval semantic
    verdict (the run.py judge call) agree on every question — 1.0 by
    construction — with the pinned scripted judge; the only CI-parity flips
    are the 3 long-gold-unreachable class (recorded findings, not
    blocking)."""
    questions = _composition()
    report = run_consistency(
        questions, judge=_offline_judge(questions),
        answers=_default_answers(questions))
    assert report["lane_agreement"] == 1.0
    assert report["n"] == 21
    assert report["blocking"] is False
    classes = {f["class"] for f in report["findings"]}
    assert classes == {"long-gold-unreachable"}
    assert {f["question_id"] for f in report["findings"]} == set(LONG_GOLD_QIDS)
    # every finding in the expected class is non-blocking
    assert all(not f["blocking"] for f in report["findings"])


def test_consistency_long_gold_containment_vs_semantic_flip():
    """The long-gold flip is exactly the point: containment (False — the
    unreachable bar) vs semantic (True on the curated correct paraphrase)
    per long-gold question."""
    questions = _composition()
    report = run_consistency(
        questions, judge=_offline_judge(questions),
        answers=_default_answers(questions))
    for r in report["records"]:
        if r["question_id"] in LONG_GOLD_QIDS:
            assert r["containment_ok"] is False
            assert r["eval_ok"] is True


def test_consistency_temporal_offbyone_recorded_finding_not_blocking():
    """Divergence policy (ii): the temporal off-by-one class — containment
    penalizes it, the official template forbids penalizing it — is a
    RECORDED finding, NOT a defect (the bug being fixed)."""
    questions = _composition()
    by_id = {q["question_id"]: q for q in questions}
    q = by_id["gpt4_8279ba02"]
    assert q["question_type"] == "temporal-reasoning"
    # an off-by-one answer: correct day-count intent, wrong numbers
    # ('10 days ago...' → '9 days ago...')
    off_by_one = re.sub(r"\d+", lambda m: str(int(m.group(0)) - 1), q["answer"])
    assert off_by_one != q["answer"]
    answers = _default_answers(questions)
    answers[q["question_id"]] = {"answer": off_by_one, "abstained": False}
    # scripted semantic judge pinned to the official-template behavior:
    # do NOT penalize off-by-one day counts (the #2071 bug class)
    judge = ScriptedSemanticJudge(rules=[("9 days ago", True)])
    report = run_consistency(questions, judge=judge, answers=answers)
    assert report["blocking"] is False
    temporal_findings = [
        f for f in report["findings"]
        if f["class"] == "temporal-off-by-one"]
    assert any(f["question_id"] == q["question_id"]
               for f in temporal_findings)
    for f in temporal_findings:
        assert f["blocking"] is False
    # the semantic judge credits the off-by-one answer while containment
    # does not — the official-template behavior (containment penalized it)
    rec = next(r for r in report["records"]
               if r["question_id"] == q["question_id"])
    assert rec["containment_ok"] is False and rec["eval_ok"] is True


def test_consistency_overcredit_is_blocking():
    """Divergence policy (i): the semantic judge OVER-CREDITING a
    factually-wrong answer (known_wrong hypothesis graded True) is a
    BLOCKING finding."""
    questions = _composition()
    by_id = {q["question_id"]: q for q in questions}
    q = by_id["0100672e"]
    answers = _default_answers(questions)
    answers[q["question_id"]] = {
        "answer": "The mugs cost $500 and the office was delighted.",
        "abstained": False, "known_wrong": True}
    # a judge that (incorrectly) credits the wrong content — the pin
    judge = ScriptedSemanticJudge(
        rules=[("the office was delighted", True)])
    report = run_consistency(questions, judge=judge, answers=answers)
    assert report["blocking"] is True
    overcredit = [f for f in report["findings"]
                  if f["class"] == "overcredit"]
    assert any(f["question_id"] == q["question_id"]
               for f in overcredit)
    assert all(f["blocking"] for f in overcredit)


def test_consistency_abs_marker_lane_divergence_recorded():
    """Divergence policy (iii): an _abs marker short-circuit differing from
    the judge's abstention-template verdict is a RECORDED (non-blocking)
    finding — the marker vocabulary is a documented class, not a defect."""
    questions = _composition()
    answers = _default_answers(questions)
    # judge with NO rules → the marker short-circuit (True) diverges from
    # the judge's default (False)
    judge = ScriptedSemanticJudge(rules=[])
    report = run_consistency(questions, judge=judge, answers=answers)
    assert report["blocking"] is False
    lane = [f for f in report["findings"]
            if f["class"] == "abs-marker-lane"]
    assert lane, "expected recorded abs-marker-lane findings"
    assert all(f["blocking"] is False for f in lane)
    # the divergent records are exactly the _abs questions
    assert {f["question_id"] for f in lane} == {
        q["question_id"] for q in questions if "_abs" in q["question_id"]}


# ── Step 6/7: I1 probe harness ────────────────────────────────────────────

def test_probe_offline_3_of_3_true():
    """Indicator I1, offline pin: the scripted fake judges the curated
    correct-paraphrase answers True on all 3 long-gold questions (the
    key-free CI pin of the live probe)."""
    probes = _probes(_composition())
    assert [p["question_id"] for p in probes] == list(LONG_GOLD_QIDS)
    verdicts = run_probe(
        ScriptedSemanticJudge(rules=OFFLINE_RULES), probes)
    assert [v["ok"] for v in verdicts] == [True, True, True]
    # curated paraphrases are human-verified-correct and non-trivial:
    # none of them reproduces the gold verbatim (a lexical bar would fail)
    by_id = {q["question_id"]: q for q in _composition()}
    for v in verdicts:
        gold = by_id[v["question_id"]]["answer"]
        assert v["hypothesis"] not in gold
        assert gold not in v["hypothesis"]


def test_probe_offline_cli_exit_zero():
    """The offline probe CLI exits 0 with 3/3 True."""
    assert probe_main(["--fixture", str(FIXTURE), "--offline"]) == 0


# ── Cross-cutting: graded eval surface untouched ──────────────────────────

def test_judge_rubric_fingerprint_untouched():
    """The graded eval surface is UNTOUCHED: JUDGE_RUBRIC_ID stays
    'longmemeval-official' and both fingerprint sites derive from it (no
    rubric change → no stale-checkpoint risk; verification-only pin)."""
    src = Path(Path(__file__).resolve().parent.parent
               / "tools" / "longmem_eval" / "run.py").read_text()
    assert 'JUDGE_RUBRIC_ID = "longmemeval-official"' in src
    # the fingerprint lines derive the hash from JUDGE_RUBRIC_ID
    assert re.search(r"judge_rubric_id_hash.*_sha16\(JUDGE_RUBRIC_ID\)",
                     src, re.S)


def test_nearest_miss_grading_still_strict():
    """The #1949 near-miss decision is untouched: NEAR_MISS_GRADING stays
    'strict' (the eval rubric is not relaxed by the #2071 spot-check
    change)."""
    from tools.longmem_eval.judge import NEAR_MISS_GRADING
    assert NEAR_MISS_GRADING == "strict"
