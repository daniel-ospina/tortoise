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


def _mock_prereg(root: Path | None = None) -> dict:
    """A pre-registration for the MOCK judge (offline wiring runs only).

    The committed protocol pins the OFFICIAL judge; a run graded by
    ``MockJudge`` is a different configuration and the bar says so — which is
    exactly why these tests build their own protocol record instead of
    borrowing the published one. A mock run can never be published as the
    measured number (its reader and judge are not the pre-registered ones).
    """
    root = root or ask_corpus.ASK_DIR
    record = m.protocol(judge_spec=m.MOCK_JUDGE_SPEC)
    record["corpus"].update({
        "hash": ask_corpus.compute_corpus_hash(root),
        "manifest_verified": True,
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
    scored = m.score(run_report=run, corpus_root=root, judge=MockJudge(),
                     mock=True)
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
                     judge=judge or MockJudge(), mock=True)
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
    assert live == {"seen": 1, "rendered_live": 0, "n": 1}, live
    assert scored["metrics"]["verbatim_no_regression"]["regressions"] == []

    # ⛔ D8: the KU rate is reported, never thresholded. Zero committed
    # current states with a green D8 gate and no verbatim regression is still
    # a PASS — the bar has no pre-set quality threshold to fail against.
    flattened = json.loads(json.dumps(scored))
    flattened["metrics"]["current_state@distilled"] = {
        "committed": 0, "n": 1, "rate": 0.0}
    assert m.evaluate_exit_bar(
        scored=flattened,
        prereg=_mock_prereg(shared_run["root"]))["verdict"] == "pass", (
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
    assert scored["bar"]["verdict"] == "regression", scored["bar"]
    assert any("D8" in reason for reason in scored["bar"]["reasons"])


def test_an_unmeasured_case_is_inconclusive_and_never_a_pass(shared_run):
    """A case that did not run is an UNMEASURED case. Counting it as a miss
    would fabricate a regression; counting it as a pass would launder a hole."""
    run = json.loads(json.dumps(shared_run["run"]))
    run["cases"][0]["arms"]["raw"] = {
        "ok": False, "answer": "", "evidence": "", "error": "boom"}
    root = shared_run["root"]
    scored = m.score(run_report=run, corpus_root=root, judge=MockJudge(),
                     mock=True)
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
    scored = m.score(run_report=run, corpus_root=root, judge=MockJudge(),
                     mock=True)
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
    assert m._main(["publish", "--scored", str(scored_path),
                    "--commit", "c" * 40, "--cost-usd", "0.02",
                    "--out", str(report), "--receipt", str(receipt),
                    "--prereg", str(prereg_path),
                    "--corpus-root", str(root)]) == 0
    assert report.read_text(encoding="utf-8").startswith("---\ntitle:")
    assert json.loads(receipt.read_text(encoding="utf-8"))["verdict"] == "pass"
