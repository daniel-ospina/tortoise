"""#9097 — the sealed ask exit measurement must be able to say NO.

The defect this pins is not a wrong number: it is that the epic's Q3 exit
claim had an INSTRUMENT (#9095's ``--distilled`` leg prints a number) but no
BENCHMARK — no pre-registered protocol, no sealed gold the runner cannot read,
no hash-pinned corpus, and no exit-bar evaluator at all, so:

* the "no regression on verbatim/raw" half of the exit bar was **unmeasurable**
  (the #9095 corpus is knowledge-update only — there was no verbatim case in
  existence, and no paired comparison to lose one);
* a number from it could not be published (nothing fixed the bar before the
  run, so the bar could always be chosen after seeing the result);
* nothing could return FAIL, so a red result was indistinguishable from an
  unmeasured one.

These tests cover, one per verification-checklist row of the issue:

  1. pre-registration precedes the run — the protocol record pins the corpus
     hash and is REFUSED over an unverifying corpus;
  2. sealed gold — the RUN step cannot read the answer key (proved with a
     sentinel gold, not with a promise);
  3. publication mechanics — the receipt carries sha + model + hash, and it
     cannot overstate the evidence it carries;
  4. the exit bar evaluates correctly — a deliberately regressed verbatim case
     and a dropped supersession BOTH fail the bar, and the KU rate is not a
     threshold;
  5. report comparability — the rendered report states the pre-registered bar
     and claims no threshold it did not pre-register.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.ask import corpus as ask_corpus
from tests.eval.ask import generate_corpus
from tools.longmem_eval import measure_ask_distilled as m
from tools.longmem_eval import w7_publish
from tools.longmem_eval.judge import MockJudge
from tortoise.ask_lane import _reset_ask_reader_cache_for_tests

# ── stubs ─────────────────────────────────────────────────────────────────
#
# The READER's quality is not what these tests measure (the defect is the
# missing benchmark). What they measure is the bar: can it pass a clean run,
# and can it say NO to each failure it pre-registered? So the reader is a
# deterministic stub whose answers come from a question map, and the failure
# mode under test is modelled explicitly.

class _RecordingReader:
    """Answers from a question→answer map; records every prompt it is given."""

    last_completion_tokens = 8

    def __init__(self, answers: dict[str, str], prompts: list[str]):
        self.answers = answers
        self.prompts = prompts

    def complete(self, *, system: str, user: str) -> str:
        self.prompts.append(user)
        for fragment, answer in self.answers.items():
            if fragment in user:
                return answer
        return ""

    def close(self) -> None:
        pass


class _ConfusedByDistillationReader(_RecordingReader):
    """A reader that is DEGRADED when a distilled claim is in front of it.

    Models the exact failure the verbatim half of the exit bar exists to catch:
    the fact is present verbatim in a turn AND as a distilled claim, and the
    claim's rendered form (a validity window instead of the sentence's own
    tense) misleads the reader into not committing it. A raw-arm run of the
    same case has no claim, so the same reader commits it there — which is what
    makes the comparison PAIRED rather than a threshold.
    """

    def complete(self, *, system: str, user: str) -> str:
        self.prompts.append(user)
        if "[valid " in user:
            for fragment in self.answers:
                if fragment in user:
                    return "I don't have that information."
        for fragment, answer in self.answers.items():
            if fragment in user:
                return answer
        return ""


def _answer_map() -> dict[str, str]:
    """question fragment → the answer a competent reader commits."""
    return {
        "Where do I live?": "I live in Porto.",
        "What diet am I on?": "I eat a vegan diet.",
        "Who do I work for?": "I work at Northwind Traders.",
        "What is my badge number?": "QX-4417",
        "Which ferry do I take to the office?": "The 07:15 ferry",
        "What am I allergic to?": "Shellfish",
    }


class _StubJudge(MockJudge):
    """The mock's containment grading, under an identity the receipt can state.

    ``MockJudge`` cannot publish a PASS (its pin is the mock pin, by design):
    a mock-graded run proves the *wiring*, never the measurement. These tests
    do need a receipt that validates, so the stub declares its own identity and
    the protocol pre-registers THAT — which is the same honest path a real
    judge takes, with a deterministic grader standing in for the API.
    """

    model_id = "test-stub-judge"


def _factory(reader):
    return lambda: reader


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    # The ambient shell may carry TORTOISE_API_URL (fleet env) — the local
    # lane is the surface under test. The reader spec is pinned too, so the
    # pre-registration/config comparison is not at the mercy of the operator's
    # shell.
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.delenv("TORTOISE_ASK_MODEL", raising=False)
    monkeypatch.delenv("TORTOISE_LME_JUDGE_MODEL", raising=False)
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


def _prereg(tmp_path: Path, *, judge_spec: str | None = None) -> tuple[dict, Path]:
    path = tmp_path / "preregistration.json"
    record = m.write_preregistration(path, judge_spec=judge_spec)
    return record, path


#: A two-case subset corpus for the run-path tests: one knowledge-update case
#: (the D8 gate) and one verbatim case (the no-regression clause). The full
#: committed corpus stays the published one; a wiring test needs both SHAPES,
#: not the published denominator, and running six cases twice per assertion
#: would cost CI minutes for no extra coverage.
_SMALL_CASES = ("ask01_residence", "ask04_badge")


def _small_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "small-corpus"
    cases = tuple(case for case in generate_corpus._CASES
                  if case["case_id"] in _SMALL_CASES)
    assert len(cases) == len(_SMALL_CASES)
    generate_corpus.write_corpus(root, cases)
    return root


def _mock_prereg(root: Path | None = None,
                 judge_spec: str | None = None) -> dict:
    """A pre-registration for the MOCK judge (offline wiring runs only).

    The committed protocol pins the OFFICIAL judge; a run graded by
    ``MockJudge`` is a different configuration and the bar says so — which is
    exactly why these tests build their own protocol record instead of
    borrowing the published one. A mock run can never be published as the
    measured number (its reader and judge are not the pre-registered ones).
    """
    root = root or ask_corpus.ASK_DIR
    record = m.protocol(judge_spec=judge_spec or _StubJudge.model_id)
    record["corpus"].update({
        "hash": ask_corpus.compute_corpus_hash(root),
        "manifest_verified": True,
        "cases": ask_corpus.case_ids(root),
        "n_cases": len(ask_corpus.case_ids(root)),
    })
    return record


@pytest.fixture(scope="module")
def shared_run(tmp_path_factory):
    """ONE run of the two-case subset, shared by the assertions that read it.

    These tests grade the same observations in different ways (the bar, the
    receipt, the report, the config comparisons). Re-running the lane per
    assertion would cost CI minutes and measure nothing new — the substrate is
    a stub reader, so the observations are deterministic.
    """
    import os

    from tortoise.ask_lane import _reset_ask_reader_cache_for_tests as _reset

    saved = {key: os.environ.pop(key, None)
             for key in ("TORTOISE_API_URL", "TORTOISE_ASK_MODEL",
                         "TORTOISE_LME_JUDGE_MODEL")}
    _reset()
    tmp = tmp_path_factory.mktemp("shared-run")
    root = _small_corpus(tmp)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    scored = m.score(run_report=run, corpus_root=root, judge=_StubJudge(),
                     judge_spec=_StubJudge.model_id)
    scored["bar"] = m.evaluate_exit_bar(
        scored=scored, manifest=ask_corpus.verify_manifest(root),
        prereg=_mock_prereg(root))
    _reset()
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value
    return {"root": root, "run": run, "scored": scored}


def _scored(tmp_path: Path, *, mutate=None, reader=None, judge=None):
    root = _small_corpus(tmp_path)
    reader = reader or _RecordingReader(_answer_map(), [])
    run = m.run_sealed(corpus_root=root, reader_factory=_factory(reader),
                       mutate=mutate)
    scored = m.score(run_report=run, corpus_root=root,
                     judge=judge or _StubJudge(),
                     judge_spec=(judge or _StubJudge()).model_id)
    scored["bar"] = m.evaluate_exit_bar(
        scored=scored, manifest=ask_corpus.verify_manifest(root),
        prereg=_mock_prereg(root))
    return run, scored


# ── 1. pre-registration precedes the run ──────────────────────────────────

def test_preregistration_pins_the_corpus_and_the_protocol(tmp_path):
    record, path = _prereg(tmp_path)
    assert path.exists()
    assert record["written_before_any_arm_run"] is True
    assert record["corpus"]["hash"] == ask_corpus.compute_corpus_hash()
    assert record["corpus"]["n_cases"] == len(ask_corpus.case_ids())
    assert record["arms"] == ["raw", "distilled"]
    assert record["gate_arm"] == "distilled"
    # Both classes are in the pre-registration, not just the graded one: the
    # verbatim class IS the "no regression" half of the exit bar.
    assert set(record["corpus"]["classes"]) == {"knowledge-update", "verbatim"}
    # The decision rule is written down BEFORE the run, and it says which
    # numbers are not thresholds.
    rule = record["decision_rule"]
    assert rule["regression"]
    assert "no pass threshold is pre-set" in rule["reported_not_thresholded"]
    assert "first published run" in rule["reported_not_thresholded"]
    # The COMMITTED protocol record is exactly what a fresh render produces
    # (a hand-edited protocol would be a different bar).
    committed = m.load_preregistration()
    fresh = m.protocol()
    fresh["corpus"].update({
        "hash": ask_corpus.compute_corpus_hash(),
        "manifest_verified": True,
        "n_cases": len(ask_corpus.case_ids()),
    })
    assert committed == fresh


def test_preregistration_is_refused_over_a_corpus_that_does_not_verify(tmp_path):
    """A protocol that pins a hash nobody checks is the stale-flag failure: it
    records a past state and is never revalidated. Writing one over a drifted
    corpus is refused outright."""
    root = tmp_path / "corpus"
    generate_corpus.write_corpus(root)
    (root / "gold" / "ask01_residence.gold.json").write_text(
        '{"answer": "I live in Lisbon."}\n', encoding="utf-8")
    assert ask_corpus.verify_manifest(root)["ok"] is False
    with pytest.raises(ValueError, match="refusing to pre-register"):
        m.write_preregistration(tmp_path / "prereg.json", corpus_root=root)


# ── 2. the sealed key is not readable by the RUN step ─────────────────────

def test_the_runner_cannot_read_the_sealed_gold(tmp_path):
    """Sentinel proof, not a promise: put a unique token in every gold file and
    assert it never reaches a reader prompt or the observations.

    The run step is handed ``corpus.load_fixtures`` (``fixtures/*.json`` only),
    so the answer key is not merely "not read" by convention — it is not
    reachable through the API the runner is given.
    """
    sentinel = "SEALED-GOLD-SENTINEL-9097"
    root = _small_corpus(tmp_path)
    for path in (root / "gold").glob("*.gold.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["answer"] = f"{doc['answer']} {sentinel}"
        path.write_text(ask_corpus.dump_json(doc), encoding="utf-8")

    prompts: list[str] = []
    run = m.run_sealed(
        corpus_root=root,
        reader_factory=_factory(_RecordingReader(_answer_map(), prompts)))
    assert prompts, "the run made no reader calls — nothing was measured"
    for prompt in prompts:
        assert sentinel not in prompt, (
            "the sealed gold leaked into a reader prompt")
    assert sentinel not in json.dumps(run), (
        "the sealed gold leaked into the run observations")


def test_a_fixture_carrying_a_grading_key_is_refused(tmp_path):
    """The sealed-key rule is enforced at the boundary the runner crosses."""
    leaked = dict(ask_corpus.load_fixtures()[0], answer="I live in Porto.")
    root = tmp_path / "corpus"
    (root / "fixtures").mkdir(parents=True)
    (root / "fixtures" / "leak.json").write_text(
        json.dumps(leaked), encoding="utf-8")
    with pytest.raises(ValueError, match="sealed corpus violation"):
        ask_corpus.load_fixtures(root)


# ── 3. the exit bar: PASS, and three ways to say NO ───────────────────────

def test_a_clean_run_passes_with_no_knowledge_update_threshold(shared_run):
    """The green path — and the KU rate is NOT what makes it green."""
    run, scored = shared_run["run"], json.loads(json.dumps(shared_run["scored"]))
    assert len(run["cases"]) == len(_SMALL_CASES)
    assert scored["bar"]["verdict"] == "pass", scored["bar"]
    live = scored["metrics"]["superseded_live@distilled"]
    assert live == {"seen": 1, "rendered_live": 0, "n": 1, "n_cases": 1}, live
    assert scored["metrics"]["verbatim_no_regression"]["regressions"] == []

    # ⛔ D8: the KU rate is reported, never thresholded. A run whose reader
    # commits NOTHING on the knowledge-update case, with a green D8 gate and no
    # verbatim regression, is still a PASS — the bar has no pre-set quality
    # threshold to fail against. (Proved with a genuinely silent reader rather
    # than by editing the metrics block: a hand-edited summary is refused now.)
    class _NoKuJudge(_StubJudge):
        """Committed everywhere except the knowledge-update answer."""
        def judge(self, **kwargs):
            if "Porto" in (kwargs.get("answer") or ""):
                return False
            return super().judge(**kwargs)

    no_ku = m.score(run_report=shared_run["run"], corpus_root=shared_run["root"],
                    judge=_NoKuJudge(), judge_spec=_NoKuJudge.model_id)
    no_ku["bar"] = m.evaluate_exit_bar(
        scored=no_ku, prereg=_mock_prereg(shared_run["root"]))
    assert no_ku["metrics"]["current_state@distilled"]["committed"] == 0
    assert no_ku["bar"]["verdict"] == "pass", (
        "the exit bar must not invent a knowledge-update threshold — D8 says "
        "the first published run sets that number")


def test_a_verbatim_regression_fails_the_bar(tmp_path):
    """A KU win with a verbatim loss is a FAIL, not a trade.

    The same case, same run, both arms: the raw arm commits the verbatim fact
    and the distilled arm loses it. This is the clause that could not exist
    before this issue — the #9095 corpus had no verbatim case at all.
    """
    reader = _ConfusedByDistillationReader(_answer_map(), [])
    _run, scored = _scored(tmp_path, reader=reader)
    verbatim = scored["metrics"]["verbatim_no_regression"]
    assert verbatim["regressions"], (
        "the degraded reader must lose at least one verbatim case on the "
        f"distilled arm (metrics: {verbatim})")
    assert scored["bar"]["verdict"] == "regression", scored["bar"]
    assert scored["bar"]["failure_origin"] == "gate_regression"
    assert any("verbatim/raw" in reason for reason in scored["bar"]["reasons"])
    # The KU half may well be green — the FAIL is the verbatim loss alone.
    assert scored["metrics"]["superseded_live@distilled"]["rendered_live"] == 0


def test_dropping_the_planted_supersession_turns_the_d8_gate_red(tmp_path):
    """Falsification probe through the real lane: lose the supersession and the
    dead claim reaches the reader as a LIVE claim. Without this the gate could
    pass on any corpus at all."""
    _run, scored = _scored(tmp_path, mutate=m._drop_supersession)
    live = scored["metrics"]["superseded_live@distilled"]
    assert live["rendered_live"] == live["n"] == 1, live
    # A probe measures the GATE, not the product: its readings prove the gate
    # can see a live superseded claim, but they can never be published as the
    # measurement (that would report a deliberately broken run as the result).
    assert scored["bar"]["verdict"] == "inconclusive", scored["bar"]
    assert any("falsification probe" in reason
               for reason in scored["bar"]["reasons"])
    as_measurement = json.loads(json.dumps(scored))
    as_measurement.pop("mutated")
    probed = m.evaluate_exit_bar(scored=as_measurement,
                                 prereg=_mock_prereg(tmp_path / "small-corpus"))
    assert probed["verdict"] == "regression", probed
    assert any("D8" in reason for reason in probed["reasons"])


def test_an_empty_or_class_stripped_run_cannot_pass(shared_run):
    """The no-op gate: every clause of the bar is per-case, so a result set with
    no cases — or with one graded CLASS stripped out — divides by a denominator
    it never measured. `seen == n` is 0 == 0 and a corpus with no verbatim case
    trivially has "no verbatim regression". Both must be INCONCLUSIVE."""
    root = shared_run["root"]
    prereg = _mock_prereg(root)

    empty = json.loads(json.dumps(shared_run["scored"]))
    empty["cases"] = []
    bar = m.evaluate_exit_bar(scored=empty, prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar
    assert "NO cases" in bar["reasons"][0]

    # Relabel (do not drop) the verbatim row: the CLASS coverage must be
    # checked even when the case count is intact — otherwise a mislabelled case
    # silently empties the no-regression half of the bar.
    stripped = json.loads(json.dumps(shared_run["scored"]))
    for row in stripped["cases"]:
        if row["class"] == "verbatim":
            row["class"] = "knowledge-update"
    bar = m.evaluate_exit_bar(scored=stripped, prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar
    assert "missing class" in bar["reasons"][0]

    subset = json.loads(json.dumps(shared_run["scored"]))
    subset["cases"] = subset["cases"][:1]
    bar = m.evaluate_exit_bar(scored=subset, prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar


def test_an_unmeasured_case_is_inconclusive_and_never_a_pass(shared_run):
    """A case that did not run is an UNMEASURED case. Counting it as a miss
    would fabricate a regression; counting it as a pass would launder a hole."""
    run = json.loads(json.dumps(shared_run["run"]))
    run["cases"][0]["arms"]["raw"] = {
        "ok": False, "answer": "", "evidence": "", "error": "boom"}
    root = shared_run["root"]
    scored = m.score(run_report=run, corpus_root=root, judge=_StubJudge(),
                     judge_spec=_StubJudge.model_id)
    bar = m.evaluate_exit_bar(scored=scored, prereg=_mock_prereg(root))
    assert bar["verdict"] == "inconclusive", bar
    assert bar["failure_origin"] == "runner_error"
    assert bar["run_status"] == "failed"


# ── 4. integrity: a run over a different corpus/config is INCONCLUSIVE ────

def test_a_gold_only_edit_invalidates_the_preregistration(tmp_path):
    """A re-keyed answer key must never be absorbed silently."""
    root = _small_corpus(tmp_path)
    prereg = m.write_preregistration(tmp_path / "prereg.json", corpus_root=root)
    before = ask_corpus.compute_corpus_hash(root)

    # Edit the GOLD only (fixtures untouched) — the classic silent re-key.
    path = root / "gold" / "ask04_badge.gold.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["answer"] = "QX-0000"
    path.write_text(ask_corpus.dump_json(doc), encoding="utf-8")
    after = ask_corpus.compute_corpus_hash(root)
    assert after != before, "a gold-only edit MUST change the corpus hash"

    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    scored = m.score(run_report=run, corpus_root=root, judge=_StubJudge(),
                     judge_spec=_StubJudge.model_id)
    bar = m.evaluate_exit_bar(scored=scored,
                              manifest=ask_corpus.verify_manifest(root),
                              prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar
    assert bar["failure_origin"] == "hash_mismatch"


def test_a_config_mismatch_is_inconclusive_not_a_silent_pass(shared_run):
    """A run on a different reader than the pre-registered one is not the same
    benchmark — it can neither pass nor regress the claim."""
    scored = shared_run["scored"]
    drift = json.loads(json.dumps(_mock_prereg(shared_run["root"])))
    drift["reader"]["model_spec"] = "openrouter:some/other-reader"
    bar = m.evaluate_exit_bar(scored=scored, prereg=drift)
    assert bar["verdict"] == "inconclusive", bar
    assert bar["failure_origin"] == "config_mismatch"
    # …and with no pre-registration at all there is no fixed bar.
    bar = m.evaluate_exit_bar(scored=scored, prereg=None)
    assert bar["verdict"] == "inconclusive"
    assert "no pre-registration" in bar["reasons"][0]


def test_a_manifest_mismatch_is_inconclusive(shared_run):
    scored = shared_run["scored"]
    bar = m.evaluate_exit_bar(
        scored=scored,
        manifest={"ok": False, "missing": ["gold/x.gold.json"],
                  "corpus_hash": scored["corpus_hash"]},
        prereg=m.load_preregistration())
    assert bar["verdict"] == "inconclusive"
    assert bar["failure_origin"] == "hash_mismatch"


# ── 5. publication mechanics ──────────────────────────────────────────────

def test_the_receipt_carries_sha_model_and_hash_and_validates(shared_run):
    scored = shared_run["scored"]
    receipt = m.build_receipt(scored=scored, commit="a" * 40, cost_usd=0.01,
                              prereg_sha256="sha256:" + "b" * 64)
    assert receipt["commit"] == "a" * 40
    assert receipt["corpus_hash"] == scored["corpus_hash"]
    assert receipt["corpus_hash"].startswith("sha256:")
    assert receipt["reader_model_spec"] == m.resolve_reader_spec()
    assert receipt["judge_model"]
    assert receipt["verdict"] == "pass"
    assert set(m.REQUIRED_METRICS).issubset(receipt["metrics"])
    assert m.validate_ask_receipt(receipt) == [], m.validate_ask_receipt(receipt)


def test_a_receipt_may_not_overstate_its_own_evidence(shared_run):
    """The auditor's check re-derives the verdict: a receipt that says PASS
    while its own metrics show a live superseded claim is invalid."""
    scored = shared_run["scored"]
    receipt = m.build_receipt(scored=scored, commit="a" * 40, cost_usd=0.01)
    assert m.validate_ask_receipt(receipt) == []
    receipt["metrics"]["superseded_live@distilled"]["rendered_live"] = 1
    issues = m.validate_ask_receipt(receipt)
    assert issues and "contradicts" in issues[0], issues

    receipt = m.build_receipt(scored=scored, commit="a" * 40, cost_usd=0.01)
    receipt["metrics"]["verbatim_no_regression"]["regressions"] = ["ask04_badge"]
    assert m.validate_ask_receipt(receipt), (
        "a PASS receipt with a verbatim regression in its own metrics must be "
        "invalid")


def test_w7_validate_receipt_is_longmemeval_gated_so_the_ask_bar_is_its_own(shared_run):
    """The recorded reuse boundary (decision 4): build_receipt and
    VERDICT_VALUES are benchmark-generic; validate_receipt is not."""
    scored = shared_run["scored"]
    receipt = m.build_receipt(scored=scored, commit="a" * 40, cost_usd=0.01)
    w7_issues = w7_publish.validate_receipt(receipt)
    assert any("official_recall_all@" in issue for issue in w7_issues), (
        "w7_publish.validate_receipt must reject an ask receipt (it demands "
        f"the LongMemEval metric surface) — got {w7_issues}")
    assert m.validate_ask_receipt(receipt) == []


def test_the_report_claims_no_unpre_registered_threshold(shared_run):
    scored = shared_run["scored"]
    receipt = m.build_receipt(scored=scored, commit="a" * 40, cost_usd=0.01)
    prereg = m.load_preregistration()
    text = m.render_report(receipt=receipt, scored=scored, prereg=prereg,
                           prereg_sha256="sha256:" + "b" * 64)
    assert "Reported, NOT thresholded" in text
    assert "No pass threshold is claimed" in text
    assert receipt["corpus_hash"] in text and receipt["commit"] in text
    assert receipt["reader_model_spec"] in text
    # It publishes the bar it pre-registered, including its FAIL clause.
    assert "No verbatim/raw regression" in text


# ── 6. the CLI writes what the pipeline reads ─────────────────────────────

def test_cli_preregister_run_score_publish(tmp_path, monkeypatch):
    """End to end through the CLI, with the mock judge (wiring, not a number).

    The published report must be REFUSED if re-evaluated against a
    pre-registration that no longer describes the corpus.
    """
    monkeypatch.chdir(tmp_path)
    root = _small_corpus(tmp_path)
    prereg_path = tmp_path / "prereg.json"
    assert m._main(["preregister", "--out", str(prereg_path),
                    "--judge", "mock", "--corpus-root", str(root)]) == 0
    assert prereg_path.exists()

    # A stub reader is installed through the lane's own seam so the CLI run
    # needs no provider key.
    import tortoise.ask_lane as ask_lane_mod

    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: _RecordingReader(_answer_map(), []))
    obs_path = tmp_path / "obs.json"
    assert m._main(["run", "--out", str(obs_path),
                    "--corpus-root", str(root)]) == 0
    scored_path = tmp_path / "scored.json"
    assert m._main(["score", "--run", str(obs_path), "--out", str(scored_path),
                    "--mock", "--prereg", str(prereg_path),
                    "--corpus-root", str(root)]) == 0
    scored = json.loads(scored_path.read_text(encoding="utf-8"))
    assert scored["bar"]["verdict"] == "pass", scored["bar"]

    report = tmp_path / "run-report.md"
    receipt = tmp_path / "receipt.json"
    # A MOCK-graded run is wiring, never a measurement: publish refuses it even
    # though its bar verdict is PASS.
    assert m._main(["publish", "--scored", str(scored_path),
                    "--commit", "c" * 40, "--cost-usd", "0.02",
                    "--out", str(report), "--receipt", str(receipt),
                    "--prereg", str(prereg_path),
                    "--corpus-root", str(root)]) == 2
    assert not report.exists() and not receipt.exists()
    return

def test_cli_publishes_a_run_under_an_honest_judge_identity(tmp_path):
    """The publish path end to end, with the judge identity the receipt states.

    The mock pin cannot carry a PASS (``test_cli_preregister_run_score_publish``
    asserts that refusal). Here the same wiring runs under a stub judge whose
    identity is pre-registered and recorded, which is the shape a real judge
    takes — so the receipt validates, the report renders, and the numbers in it
    are the numbers the bar read.
    """
    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    scored = m.score(run_report=run, corpus_root=root, judge=_StubJudge(),
                     judge_spec=_StubJudge.model_id)
    prereg = _mock_prereg(root)
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg), encoding="utf-8")
    scored["bar"] = m.evaluate_exit_bar(
        scored=scored, manifest=ask_corpus.verify_manifest(root), prereg=prereg)
    assert scored["bar"]["verdict"] == "pass", scored["bar"]
    scored_path = tmp_path / "scored.json"
    scored_path.write_text(json.dumps(scored), encoding="utf-8")

    report = tmp_path / "run-report.md"
    receipt_path = tmp_path / "receipt.json"
    assert m._main(["publish", "--scored", str(scored_path),
                    "--commit", "c" * 40, "--cost-usd", "0.02",
                    "--out", str(report), "--receipt", str(receipt_path),
                    "--prereg", str(prereg_path),
                    "--corpus-root", str(root)]) == 0
    assert report.read_text(encoding="utf-8").startswith("---\ntitle:")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["verdict"] == "pass"
    assert receipt["judge_model"] == _StubJudge.model_id
    assert m.validate_ask_receipt(receipt) == []


# ── reviewer-driven hardening: the bar must not be satisfiable by construction ──

def test_a_metrics_block_that_disagrees_with_the_rows_cannot_pass(shared_run):
    """A SUMMARY IS NOT A RESULT.

    The bar's clauses all read the metrics block, which is a plain dict in a
    JSON artifact — editable from outside the audit trail. If the rows say a
    superseded claim was RENDERED LIVE and the summary says 0 of 1, a bar that
    trusts the summary publishes a PASS over a red reading.
    """
    scored = json.loads(json.dumps(shared_run["scored"]))
    row = next(r for r in scored["cases"] if r["superseded"])
    row["superseded"][0]["rendered_live"] = True  # the rows now contradict…
    bar = m.evaluate_exit_bar(scored=scored,
                              prereg=_mock_prereg(shared_run["root"]))
    assert bar["verdict"] == "inconclusive", bar
    assert any("disagrees with the per-case rows" in r for r in bar["reasons"])


def test_the_judge_identity_is_derived_from_the_object_that_graded(tmp_path):
    """A caller-supplied judge may not be recorded under a spec it does not
    match: `score(judge=MockJudge())` with the official spec in scope would
    otherwise satisfy the pre-registered judge check while a stub graded every
    answer, and publish the stub's numbers under the official pin."""
    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    official = "openai:gpt-4o-2024-08-06"
    scored = m.score(run_report=run, corpus_root=root, judge=MockJudge(),
                     judge_spec=official)
    assert scored["judge_model_spec"] == m.MOCK_JUDGE_SPEC, scored["judge_model_spec"]
    assert scored["judge_pin"] == m.MOCK_JUDGE_PIN
    # …and the pre-registration's official config check refuses it
    prereg = m.protocol(judge_spec=official)
    prereg["corpus"].update({"hash": scored["corpus_hash"],
                             "cases": ask_corpus.case_ids(root),
                             "classes": sorted({r["class"] for r in scored["cases"]})})
    bar = m.evaluate_exit_bar(scored=scored, prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar


def test_a_mock_graded_receipt_cannot_claim_the_official_pin(tmp_path):
    """The pin travels with the spec: a receipt pairing the official pin with a
    mock judge is rejected, and a mock pin can never carry a PASS."""
    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    scored = m.score(run_report=run, corpus_root=root, mock=True)
    scored["bar"] = m.evaluate_exit_bar(
        scored=scored, prereg=_mock_prereg(root,
                                           judge_spec=m.MOCK_JUDGE_SPEC))
    assert scored["bar"]["verdict"] == "pass", scored["bar"]
    receipt = m.build_receipt(scored=scored, commit="e" * 40, cost_usd=0.0,
                              prereg_sha256="0" * 64)
    mock_issues = m.validate_ask_receipt(receipt)
    assert any("MOCK-graded run cannot publish" in i for i in mock_issues), mock_issues
    # forging the pin onto the same receipt is caught by the derived-pin rule
    receipt["judge_pin"] = m.JUDGE_PIN
    issues = m.validate_ask_receipt(receipt)
    assert any("does not belong to judge spec" in i for i in issues), issues


def test_a_nested_grading_key_is_refused(tmp_path):
    """The sealed-key rule cannot be a one-depth check: nesting the answer one
    level down walked past the loader, the generator and the corpus test."""
    root = _small_corpus(tmp_path)
    fixture = json.loads(json.dumps(ask_corpus.load_fixtures(root)[0]))
    fixture["distilled"]["claims"][0]["answer"] = "leaked"
    path = root / "fixtures" / f"{fixture['case_id']}.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="grading key"):
        ask_corpus.load_fixtures(root)
    assert ask_corpus.find_grading_keys(fixture) == [
        "distilled.claims[0].answer"]


def test_every_planted_supersession_is_gated_not_just_the_first(tmp_path):
    """The denominator is planted CLAIMS. Gating only the first pair while
    counting cases would leave a second planted dead claim ungated and still
    publish a green D8 row."""
    case = {
        "case_id": "ask99_two_pairs",
        "class": ask_corpus.CLASS_KNOWLEDGE_UPDATE,
        "question_type": "knowledge-update",
        "question": "Where do I live and what is my name?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("a-old", "a-new", "b-old", "b-new"),
            "dates": ("2023/01/02 (Mon) 10:00", "2023/02/03 (Fri) 10:00",
                      "2023/03/04 (Sat) 10:00", "2023/04/05 (Wed) 10:00"),
            "sessions": (({"role": "user", "content": "I live in Lisbon."},),
                         ({"role": "user", "content": "I live in Porto."},),
                         ({"role": "user", "content": "My name is Bea."},),
                         ({"role": "user", "content": "My name is Ana."},)),
        },
        "claims": (
            {"text": "I live in Lisbon.", "session_id": "a-old",
             "valid_from": "2023-01-02"},
            {"text": "I live in Porto.", "session_id": "a-new",
             "valid_from": "2023-02-03"},
            {"text": "My name is Bea.", "session_id": "b-old",
             "valid_from": "2023-03-04"},
            {"text": "My name is Ana.", "session_id": "b-new",
             "valid_from": "2023-04-05"},
        ),
        "supersessions": (
            {"old": "I live in Lisbon.", "new": "I live in Porto."},
            {"old": "My name is Bea.", "new": "My name is Ana."},
        ),
        "judge_template": "knowledge-update",
        "gold": {"answer": "I live in Porto and my name is Ana."},
    }
    assert generate_corpus._issues(case) == []
    root = tmp_path / "two-pairs"
    # Rendered directly: `write_corpus` enforces the corpus-LEVEL invariant that
    # both classes exist (the exit bar needs both halves), and this case is a
    # single-class probe. That guard stays untouched.
    for rel, payload in generate_corpus.render((case,)).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    answers = {"Where do I live and what is my name?":
               "I live in Porto and my name is Ana."}
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(answers, [])))
    scored = m.score(run_report=run, corpus_root=root, judge=_StubJudge(),
                     judge_spec=_StubJudge.model_id)
    live = scored["metrics"]["superseded_live@distilled"]
    assert live["n"] == 2 and live["n_cases"] == 1, live
    assert live["seen"] == 2 and live["rendered_live"] == 0, live
    # …and the probe that drops BOTH pairs proves the gate reads both
    probed = m.run_sealed(corpus_root=root, mutate=m._drop_supersession,
                          reader_factory=_factory(_RecordingReader(answers, [])))
    scored_probe = m.score(run_report=probed, corpus_root=root,
                           judge=_StubJudge(), judge_spec=_StubJudge.model_id)
    assert scored_probe["metrics"]["superseded_live@distilled"][
        "rendered_live"] == 2


def test_a_pre_registered_field_is_enforced_not_just_written(shared_run):
    """A field the bar never reads is documentation, not a pre-registration."""
    scored = json.loads(json.dumps(shared_run["scored"]))
    prereg = _mock_prereg(shared_run["root"])
    prereg["gate_arm"] = "raw"
    bar = m.evaluate_exit_bar(scored=scored, prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar
    assert any("gate_arm" in r for r in bar["reasons"]), bar["reasons"]


def test_a_run_over_different_fixtures_cannot_be_scored_as_this_corpus(tmp_path):
    """The corpus hash binds the corpus to the pre-registration; the fixtures
    hash binds the RUN to the corpus it is scored against."""
    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    run["fixtures_hash"] = "sha256:" + "0" * 64
    scored = m.score(run_report=run, corpus_root=root, judge=_StubJudge(),
                     judge_spec=_StubJudge.model_id)
    bar = m.evaluate_exit_bar(scored=scored, prereg=_mock_prereg(root))
    assert bar["verdict"] == "inconclusive", bar
    assert any("fixtures_hash" in r for r in bar["reasons"]), bar["reasons"]
