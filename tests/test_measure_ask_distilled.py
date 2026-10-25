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

import copy
import json
import os
import subprocess
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


class _StubJudge:
    """The mock's containment grading, under an identity the receipt can state.

    ⛔ Deliberately NOT a ``MockJudge`` subclass: a mock is recorded as a mock
    unconditionally (that is what stops a stub collecting the official judge
    pin), so a subclass could never carry its own identity. This delegates the
    grading rule and declares its own ``model_id``, which is the same honest
    path a real judge takes — a deterministic grader standing in for the API.
    """

    model_id = "test-stub-judge"
    rubric_id = m.JUDGE_RUBRIC_ID

    def __init__(self):
        self._mock = MockJudge()

    def judge(self, **kwargs):
        return self._mock.judge(**kwargs)


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


def _small_cases() -> tuple[dict, ...]:
    cases = tuple(case for case in generate_corpus._CASES
                  if case["case_id"] in _SMALL_CASES)
    assert len(cases) == len(_SMALL_CASES)
    return cases


def _small_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "small-corpus"
    generate_corpus.write_corpus(root, _small_cases())
    return root


def _binding(commit: str, corpus_hash: str,
             prereg_sha256: str | None = None) -> dict:
    """A structurally valid commit binding, for the tests that grade something
    ELSE about the receipt.

    The binding these tests use is the synthetic one on purpose: the property
    under test in each of them is a different receipt field, and building a git
    repository per assertion would add minutes without measuring anything new.
    That the *verified* binding is the real thing — resolved from a repository,
    with the corpus and the protocol re-hashed at the recorded sha — is proved
    once, end to end, by
    ``test_a_published_report_carries_the_verified_commit_binding``.
    """
    return {
        "schema": m.COMMIT_BINDING_SCHEMA,
        "run_commit": commit,
        "run_commit_requested": commit,
        "corpus_path": "tests/eval/ask",
        "corpus_hash": corpus_hash,
        "prereg_path": "tests/eval/ask/preregistration.json",
        "prereg_sha256": prereg_sha256 or "sha256:" + "0" * 64,
        "prereg_commit": "b" * 40,
        "prereg_commit_time": "2026-01-01T00:00:00+00:00",
        "run_commit_time": "2026-01-02T00:00:00+00:00",
        "prereg_commit_epoch": 1767225600,
        "run_commit_epoch": 1767312000,
        "ordering": "proven",
        "ordering_proof": "synthetic — the verified-binding test is the real one",
    }


def _receipt(scored: dict, *, commit: str = "a" * 40, cost_usd: float = 0.0,
             prereg_sha256: str | None = None) -> dict:
    """`build_receipt` with a matching binding (see :func:`_binding`).

    The pre-registration hash goes to BOTH halves — the receipt's
    `resolved_config` records the published protocol's hash and the binding
    records the one re-hashed at the commit — because
    :func:`m._validate_commit_binding` cross-checks them unconditionally: a
    fixture that carried only one of them would be exactly the inconsistency
    that check exists to catch.
    """
    scored = copy.deepcopy(scored)
    recorded = scored.get("code_identity") or {}
    scored["code_identity"] = {
        "schema": m.CODE_IDENTITY_SCHEMA,
        "commit": commit,
        "dirty_paths": [],
        # A run that STARTED and ENDED on that tree: the two halves have to
        # agree or the receipt is refused for having straddled two trees, which
        # is a different finding from the one each test here is making.
        "at_end": {"schema": m.CODE_IDENTITY_SCHEMA, "commit": commit,
                   "dirty_paths": [], "repo": recorded.get("repo"),
                   "reason": None},
        "repo": recorded.get("repo"),
        "reason": None,
    }
    prereg_sha256 = prereg_sha256 or "sha256:" + "0" * 64
    return m.build_receipt(
        scored=scored, commit=commit, cost_usd=cost_usd,
        prereg_sha256=prereg_sha256,
        commit_binding=_binding(commit, scored["corpus_hash"], prereg_sha256))


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
    # Every test run is handed a reader FACTORY, so the run records
    # `injected` — and the protocol must describe the reader the run used, not
    # the product default (`deepseek/deepseek-v4-flash`), or a stub-reader
    # wiring run would publish as the pinned product reader.
    record["reader"]["model_spec"] = m.INJECTED_READER_SPEC
    record["reader"]["injected"] = True
    record["corpus"].update({
        "hash": ask_corpus.compute_corpus_hash(root),
        "manifest_verified": True,
        "cases": ask_corpus.case_ids(root),
        "n_cases": len(ask_corpus.case_ids(root)),
    })
    return record


def _git_env() -> dict[str, str]:
    """Host-independent env for the fixture's own git calls.

    The throwaway repositories are built with the INHERITED environment, so
    nothing in them is hermetic by default: a global ``commit.gpgsign = true``
    makes ``git commit`` fail (the ``assert`` below then reads as an ERROR, not
    a finding), a global ``core.hooksPath`` (this fleet's harness sets one) runs
    the HOST's hooks inside the fixture, and ``init.defaultBranch`` /
    ``init.templateDir`` are ambient too. Both config files are pointed at
    ``os.devnull`` and every inherited ``GIT_*`` variable is dropped — the same
    shape as ``tests/test_who_is_on.py::_git_env`` and
    ``tests/test_lane_stash.py::_env``.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def _git_hermetic(repo: Path, *args: str, date: str | None = None
                  ) -> subprocess.CompletedProcess:
    """``git -C repo <args>`` under :func:`_git_env`.

    ``date`` pins BOTH the author and committer date to one explicit ISO
    timestamp carrying an explicit UTC offset — the ordering tests need two
    commits whose offsets differ, which is exactly what a real lane history
    looks like here.
    """
    env = _git_env()
    if date is not None:
        env["GIT_COMMITTER_DATE"] = date
        env["GIT_AUTHOR_DATE"] = date
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=False, env=env)


def _git_commit(repo: Path, message: str, *, date: str | None = None) -> str:
    """Commit the worktree in a throwaway repo; return the full sha.

    ``-c commit.gpgsign=false`` AND the ``os.devnull`` config env (see
    :func:`_git_env`): signing is refused by name, and every ambient config
    knob — hooks, init defaults — never reaches the fixture at all.
    """
    assert _git_hermetic(repo, "add", "-A").returncode == 0
    proc = _git_hermetic(
        repo, "-c", "user.name=pi-test",
        "-c", "user.email=pi-test@example.invalid",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message, date=date)
    assert proc.returncode == 0, proc.stderr
    sha = m._git_out(repo, "rev-parse", "HEAD")
    assert sha
    return sha


def _git_init(repo: Path) -> None:
    """Create a throwaway repo with BOTH ambient init knobs neutralised.

    ``-b main`` pins ``init.defaultBranch``; ``--template=`` pins the template
    SOURCE, so no template — a host ``init.templateDir``, ``$GIT_TEMPLATE_DIR``,
    or git's own default directory — can drop files into the fixture's
    ``.git``. That matters for hooks: ``git init`` copies every non-dot file of
    the template into ``$GIT_DIR`` (git-init[1], TEMPLATE DIRECTORY), so a
    template shipping an executable ``hooks/pre-commit`` lands in
    ``.git/hooks``, and a hermetic commit cannot switch it off by config —
    nulling ``core.hooksPath`` makes git fall back to ``.git/hooks``. Every
    throwaway repo in this file is built HERE, so a fixture cannot be hermetic
    in one helper and ambient in the next.
    """
    assert _git_hermetic(repo, "init", "-q", "-b", "main",
                         "--template=").returncode == 0


def _make_dated_repo(base: Path, *, prereg_date: str, run_date: str) -> dict:
    """A two-commit repo whose commits carry EXPLICIT committer dates.

    The two ``%cI`` strings are decided by the dates, not by commit order, so a
    caller can build a parent/child pair whose TEXT order and EPOCH order
    disagree — the shape mixed UTC offsets produce in this repository.
    """
    repo = base / "dated-repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git_init(repo)
    corpus_dir = repo / "tests" / "eval" / "ask"
    generate_corpus.write_corpus(corpus_dir, _small_cases())
    prereg_path = corpus_dir / "preregistration.json"
    prereg_path.write_text(json.dumps(_mock_prereg(corpus_dir)),
                           encoding="utf-8")
    prereg_commit = _git_commit(repo, "protocol: the pre-registration",
                               date=prereg_date)
    (repo / "run.marker").write_text("the run happened here\n", encoding="utf-8")
    run_commit = _git_commit(repo, "run: the sealed corpus executed",
                             date=run_date)
    return {"repo": repo, "corpus_dir": corpus_dir, "prereg_path": prereg_path,
            "prereg_commit": prereg_commit, "run_commit": run_commit}


def _make_bound_repo(base: Path) -> dict:
    """A throwaway repository in which the ordering claim is TRUE.

    Three commits, each carrying something a different assertion needs:

    * ``c1`` the corpus + the pre-registration — the protocol commit;
    * ``c2`` a run marker — the commit a publish may legitimately record;
    * ``c3`` the SAME corpus with one sealed gold answer RE-KEYED — a real sha
      that cannot reproduce the scored corpus, i.e. the shape of a report that
      cites a tree its numbers did not come from.
    """
    repo = base / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git_init(repo)
    corpus_dir = repo / "tests" / "eval" / "ask"
    generate_corpus.write_corpus(corpus_dir, _small_cases())
    prereg_path = corpus_dir / "preregistration.json"
    prereg_path.write_text(json.dumps(_mock_prereg(corpus_dir)),
                           encoding="utf-8")
    prereg_commit = _git_commit(repo, "protocol: the pre-registration")
    (repo / "run.marker").write_text("the run happened here\n", encoding="utf-8")
    run_commit = _git_commit(repo, "run: the sealed corpus executed")
    gold = sorted((corpus_dir / "gold").glob("*.gold.json"))[0]
    original_gold = gold.read_text(encoding="utf-8")
    rekeyed = json.loads(original_gold)
    rekeyed["answer"] = "a different answer entirely"
    gold.write_text(json.dumps(rekeyed, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    rekeyed_commit = _git_commit(repo, "gold: re-keyed after the run")
    # The WORKTREE goes back to ``c2`` — the commit a publish may legitimately
    # record — so the mismatch lives ONLY in the commit ``c3``, which is what
    # makes the refusal below a test of the sha and not of the disk. It also
    # leaves HEAD clean at the run commit, which is the shape a published run
    # must have (a run records the tree it executed from, and that tree has to
    # BE the commit the report names).
    assert m._git(repo, "checkout", "-q", run_commit).returncode == 0
    assert m.code_identity(repo)["dirty_paths"] == []
    return {"repo": repo, "corpus_dir": corpus_dir, "prereg_path": prereg_path,
            "prereg_commit": prereg_commit, "run_commit": run_commit,
            "rekeyed_commit": rekeyed_commit}


@pytest.fixture(scope="module")
def bound_repo(tmp_path_factory):
    return _make_bound_repo(tmp_path_factory.mktemp("bound-repo"))


@pytest.fixture(scope="module")
def shared_run(tmp_path_factory, bound_repo):
    """ONE run of the two-case subset, shared by the assertions that read it.

    These tests grade the same observations in different ways (the bar, the
    receipt, the report, the config comparisons). Re-running the lane per
    assertion would cost CI minutes and measure nothing new — the substrate is
    a stub reader, so the observations are deterministic.

    The run records its code identity from ``bound_repo`` (HEAD == the commit a
    publish may name, clean): the corpus it reads lives in a tmp directory, and
    both corpora are generated from ``_small_cases()``, so they are byte-for-byte
    the same corpus the bound repo carries.
    """
    import os

    from tortoise.ask_lane import _reset_ask_reader_cache_for_tests as _reset

    saved = {key: os.environ.pop(key, None)
             for key in ("TORTOISE_API_URL", "TORTOISE_ASK_MODEL",
                         "TORTOISE_LME_JUDGE_MODEL")}
    _reset()
    tmp = tmp_path_factory.mktemp("shared-run")
    root = _small_corpus(tmp)
    run = m.run_sealed(corpus_root=root, repo_root=bound_repo["repo"],
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
    receipt = _receipt(scored, cost_usd=0.01,
                       prereg_sha256="sha256:" + "b" * 64)
    assert receipt["commit"] == "a" * 40
    assert receipt["corpus_hash"] == scored["corpus_hash"]
    assert receipt["corpus_hash"].startswith("sha256:")
    # The run was handed a reader FACTORY, so it records `injected` — never the
    # env/product default it did not use (`resolve_reader_spec()`). That is what
    # stops a stub-reader run from publishing as the pinned product reader.
    assert receipt["reader_model_spec"] == m.INJECTED_READER_SPEC
    assert receipt["reader_model_spec"] != m.resolve_reader_spec()
    assert receipt["judge_model"]
    assert receipt["verdict"] == "pass"
    assert set(m.REQUIRED_METRICS).issubset(receipt["metrics"])
    assert m.validate_ask_receipt(receipt) == [], m.validate_ask_receipt(receipt)


def test_a_receipt_may_not_overstate_its_own_evidence(shared_run):
    """The auditor's check re-derives the verdict: a receipt that says PASS
    while its own metrics show a live superseded claim is invalid."""
    scored = shared_run["scored"]
    receipt = _receipt(scored, cost_usd=0.01)
    assert m.validate_ask_receipt(receipt) == []
    receipt["metrics"]["superseded_live@distilled"]["rendered_live"] = 1
    issues = m.validate_ask_receipt(receipt)
    assert issues and "contradicts" in issues[0], issues

    receipt = _receipt(scored, cost_usd=0.01)
    receipt["metrics"]["verbatim_no_regression"]["regressions"] = ["ask04_badge"]
    assert m.validate_ask_receipt(receipt), (
        "a PASS receipt with a verbatim regression in its own metrics must be "
        "invalid")


def test_w7_validate_receipt_is_longmemeval_gated_so_the_ask_bar_is_its_own(shared_run):
    """The recorded reuse boundary (decision 4): build_receipt and
    VERDICT_VALUES are benchmark-generic; validate_receipt is not."""
    scored = shared_run["scored"]
    receipt = _receipt(scored, cost_usd=0.01)
    w7_issues = w7_publish.validate_receipt(receipt)
    assert any("official_recall_all@" in issue for issue in w7_issues), (
        "w7_publish.validate_receipt must reject an ask receipt (it demands "
        f"the LongMemEval metric surface) — got {w7_issues}")
    assert m.validate_ask_receipt(receipt) == []


def test_the_report_claims_no_unpre_registered_threshold(shared_run):
    scored = shared_run["scored"]
    receipt = _receipt(scored, cost_usd=0.01)
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

def test_cli_preregister_run_score_publish(tmp_path, monkeypatch, capsys):
    """End to end through the CLI, with the mock judge (wiring, not a number).

    A MOCK-graded run is wiring, never a measurement: publish refuses it even
    though its bar verdict is PASS. Asserting `rc == 2` alone would NOT prove
    that: a refusal from any other clause satisfies it, and there is now more
    than one (the CLI `run` records THIS checkout's tree, so a publish naming a
    tmp repository's commit also fails the code-identity check). The assertion
    therefore names the clause this test exists for.
    """
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    corpus_dir = repo / "tests" / "eval" / "ask"
    generate_corpus.write_corpus(corpus_dir, _small_cases())
    prereg_path = corpus_dir / "preregistration.json"
    assert m._main(["preregister", "--out", str(prereg_path),
                    "--judge", "mock", "--corpus-root", str(corpus_dir)]) == 0
    assert prereg_path.exists()
    _git_commit(repo, "protocol: the pre-registration")

    # A stub reader is installed through the lane's own seam so the CLI run
    # needs no provider key.
    import tortoise.ask_lane as ask_lane_mod

    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: _RecordingReader(_answer_map(), []))
    obs_path = tmp_path / "obs.json"
    assert m._main(["run", "--out", str(obs_path),
                    "--corpus-root", str(corpus_dir)]) == 0
    scored_path = tmp_path / "scored.json"
    assert m._main(["score", "--run", str(obs_path), "--out", str(scored_path),
                    "--mock", "--prereg", str(prereg_path),
                    "--corpus-root", str(corpus_dir)]) == 0
    scored = json.loads(scored_path.read_text(encoding="utf-8"))
    assert scored["bar"]["verdict"] == "pass", scored["bar"]
    (repo / "run.marker").write_text("the run happened here\n", encoding="utf-8")
    run_commit = _git_commit(repo, "run: the sealed corpus executed")
    # The commit binding is VERIFIED here, so the refusal asserted below can
    # only be the mock pin — not a binding failure standing in for it.
    binding, reasons = m.verify_commit_binding(
        commit=run_commit, repo_root=repo, corpus_root=corpus_dir,
        prereg=prereg_path)
    assert binding is not None and reasons == [], reasons

    report = tmp_path / "run-report.md"
    receipt = tmp_path / "receipt.json"
    pub = ["publish", "--scored", str(scored_path), "--commit", run_commit,
           "--cost-usd", "0.02", "--out", str(report),
           "--receipt", str(receipt), "--prereg", str(prereg_path),
           "--corpus-root", str(corpus_dir), "--repo", str(repo)]
    assert m._main(pub) == 2
    err = capsys.readouterr().err
    assert "MOCK-graded run cannot publish" in err, err
    assert not report.exists() and not receipt.exists()


def test_cli_publishes_a_run_under_an_honest_judge_identity(published):
    """The publish path end to end, with the judge identity the receipt states.

    The mock pin cannot carry a PASS (``test_cli_preregister_run_score_publish``
    asserts that refusal). Here the same wiring runs under a stub judge whose
    identity is pre-registered and recorded, which is the shape a real judge
    takes — so the receipt validates, the report renders, and the numbers in it
    are the numbers the bar read.
    """
    assert published["rc"] == 0
    assert published["report"].read_text(encoding="utf-8").startswith("---\ntitle:")
    receipt = published["receipt"]
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
    receipt = _receipt(scored, commit="e" * 40, cost_usd=0.0,
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


def test_a_corpus_with_no_planted_claim_cannot_pass(shared_run):
    """An EMPTY gate is not a green gate.

    Both regression clauses are satisfied by an empty measurement: `seen != n`
    is `0 != 0`, and a raw arm that committed nothing has nothing to lose. A
    corpus that plants no superseded claim (or a class-stripped run) therefore
    published `PASS` over a D8 gate that observed nothing.
    """
    scored = json.loads(json.dumps(shared_run["scored"]))
    for row in scored["cases"]:
        row["superseded"] = []
    scored["metrics"] = m.aggregate(scored["cases"])  # internally consistent…
    assert scored["metrics"]["superseded_live@distilled"]["n"] == 0
    bar = m.evaluate_exit_bar(scored=scored,
                              prereg=_mock_prereg(shared_run["root"]))
    assert bar["verdict"] == "inconclusive", bar  # …and still refused
    assert any("plants NO superseded claim" in r for r in bar["reasons"]), bar


def test_a_raw_arm_that_committed_nothing_cannot_claim_no_regression(shared_run):
    """'No regression' needs something to regress FROM."""

    class _SilentJudge(_StubJudge):
        def judge(self, **kwargs):
            return False

    scored = m.score(run_report=shared_run["run"], corpus_root=shared_run["root"],
                     judge=_SilentJudge(), judge_spec=_SilentJudge.model_id)
    assert scored["metrics"]["verbatim_no_regression"]["raw_committed"] == 0
    bar = m.evaluate_exit_bar(scored=scored,
                              prereg=_mock_prereg(shared_run["root"]))
    assert bar["verdict"] == "inconclusive", bar
    assert any("nothing for the distilled arm to regress FROM" in r
               for r in bar["reasons"]), bar


def test_a_mock_judge_can_never_carry_the_official_pin(tmp_path):
    """The pin is an allow-list, not a default.

    `judge_pin_for` returned the OFFICIAL pin for every spec except the literal
    `"mock"`, and MockJudge's own id is `"mock-judge"` — so declaring that spec
    recorded the stub and collected the official pin.
    """
    assert m.judge_pin_for("mock-judge") == "unpinned:mock-judge"
    assert m.judge_pin_for("some-other-judge") == "unpinned:some-other-judge"
    assert m.judge_pin_for(m.MOCK_JUDGE_SPEC) == m.MOCK_JUDGE_PIN
    assert m.judge_pin_for(m.DEFAULT_JUDGE_MODEL) == m.JUDGE_PIN
    # the official judge is the MODEL, not the transport
    assert m.judge_pin_for("openrouter:openai/gpt-4o-2024-08-06") == m.JUDGE_PIN

    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    scored = m.score(run_report=run, corpus_root=root, judge=MockJudge(),
                     judge_spec="mock-judge")
    assert scored["judge_model_spec"] == m.MOCK_JUDGE_SPEC
    assert scored["judge_pin"] == m.MOCK_JUDGE_PIN


def test_an_injected_reader_is_recorded_as_injected(shared_run):
    """The run records the reader that ANSWERED, not the env default it did not
    use: recording `resolve_reader_spec()` made a stub-reader run publish as the
    pinned product reader, and the bar's reader clause pass against it."""
    assert shared_run["run"]["reader_model_spec"] == m.INJECTED_READER_SPEC
    assert shared_run["run"]["reader_injected"] is True
    scored = json.loads(json.dumps(shared_run["scored"]))
    official = m.protocol()  # the pre-registered product reader
    official["corpus"].update({"hash": scored["corpus_hash"],
                               "cases": [r["case_id"] for r in scored["cases"]],
                               "classes": [r["class"] for r in scored["cases"]]})
    bar = m.evaluate_exit_bar(scored=scored, prereg=official)
    assert bar["verdict"] == "inconclusive", bar
    assert any("reader" in r for r in bar["reasons"]), bar["reasons"]


def test_the_rubric_hash_follows_the_judge_that_graded(tmp_path):
    """A guard that compares a constant to itself can never fire."""

    class _OtherRubric(_StubJudge):
        rubric_id = "a-different-anscheck-template"

    assert m.judge_rubric_id_hash() != m.judge_rubric_id_hash(_OtherRubric.rubric_id)
    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    scored = m.score(run_report=run, corpus_root=root, judge=_OtherRubric(),
                     judge_spec=_OtherRubric.model_id)
    assert scored["judge_rubric_id_hash"] == m.judge_rubric_id_hash(
        _OtherRubric.rubric_id)
    prereg = _mock_prereg(root)
    bar = m.evaluate_exit_bar(scored=scored, prereg=prereg)
    assert bar["verdict"] == "inconclusive", bar
    assert any("rubric_id_hash" in r for r in bar["reasons"]), bar["reasons"]


def test_score_refuses_a_report_it_cannot_grade(tmp_path):
    """A missing arm or a foreign case is refused with a diagnosis, not a
    KeyError: the bar has an `inconclusive` clause for each, and the clause can
    never fire if the scorer crashes first."""
    root = _small_corpus(tmp_path)
    run = m.run_sealed(corpus_root=root,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    one_armed = json.loads(json.dumps(run))
    one_armed["arms"] = ["raw"]
    for case in one_armed["cases"]:
        case["arms"] = {"raw": case["arms"]["raw"]}
    with pytest.raises(ValueError, match="never measured"):
        m.score(run_report=one_armed, corpus_root=root, mock=True)

    foreign = json.loads(json.dumps(run))
    foreign["cases"][0]["case_id"] = "ask99_ghost"
    with pytest.raises(ValueError, match="ask99_ghost"):
        m.score(run_report=foreign, corpus_root=root, mock=True)


def test_a_receipt_rejects_a_zero_denominator(shared_run):
    """A vacuous PASS must be refused at the publication boundary too — the
    receipt's own comment promised this and the code only rejected `n < 0`."""
    receipt = _receipt(shared_run["scored"], commit="f" * 40,
                       cost_usd=0.0, prereg_sha256="0" * 64)
    assert m.validate_ask_receipt(receipt) == []
    receipt["metrics"]["superseded_live@distilled"] = {
        "seen": 0, "rendered_live": 0, "n": 0, "n_cases": 0}
    issues = m.validate_ask_receipt(receipt)
    assert any("superseded_live@distilled.n" in i for i in issues), issues


def test_the_report_renders_an_unobserved_claim_as_unmeasured(shared_run):
    """The three-state cell must not assert the D8 failure it did not measure:
    `seen=False, rendered_live=False, flagged=False` used to print
    `unseen / live-unflagged`."""
    scored = json.loads(json.dumps(shared_run["scored"]))
    row = next(r for r in scored["cases"] if r["superseded"])
    row["superseded"][0].update({"seen": False, "rendered_live": False,
                                 "flagged": False})
    report = m.render_report(
        receipt=_receipt(shared_run["scored"], cost_usd=0.0,
                         prereg_sha256="0" * 64),
        scored=scored, prereg=_mock_prereg(shared_run["root"]),
        prereg_sha256="0" * 64)
    assert "unseen / —" in report
    assert "unseen / live-unflagged" not in report


# ── 7. the published artifact is BOUND to the commit it records ───────────
#
# Targets 1 and 2 of #9097 are claims about history — "pre-registration
# committed BEFORE the run (verifiable, not asserted)" and "reproducible from
# the recorded sha alone". Both used to be satisfied by prose: `--commit` was a
# free-text field, so a report could name a sha that does not resolve, a sha
# whose tree carries a DIFFERENT corpus or protocol, or a sha at which the
# pre-registration entered history in that same commit (i.e. possibly authored
# after the numbers). The report rendered anyway, and its §6 told a reader to
# `git checkout <sha>`.

def _publish_argv(*, scored_path: Path, commit: str, report: Path,
                  receipt: Path, corpus_root: Path, prereg: Path,
                  repo: Path | None = None) -> list[str]:
    argv = ["publish", "--scored", str(scored_path), "--commit", commit,
            "--cost-usd", "0.02", "--out", str(report),
            "--receipt", str(receipt), "--prereg", str(prereg),
            "--corpus-root", str(corpus_root)]
    if repo is not None:
        argv += ["--repo", str(repo)]
    return argv


@pytest.fixture(scope="module")
def published(tmp_path_factory, shared_run, bound_repo):
    """ONE successful publish of the shared run, from a repository whose
    ordering claim is true. Read by the two assertions that need the artifact."""
    out = tmp_path_factory.mktemp("published")
    scored_path = out / "scored.json"
    scored_path.write_text(json.dumps(shared_run["scored"]), encoding="utf-8")
    report = out / "run-report.md"
    receipt_path = out / "receipt.json"
    rc = m._main(_publish_argv(
        scored_path=scored_path, commit=bound_repo["run_commit"], report=report,
        receipt=receipt_path, corpus_root=bound_repo["corpus_dir"],
        prereg=bound_repo["prereg_path"], repo=bound_repo["repo"]))
    receipt = (json.loads(receipt_path.read_text(encoding="utf-8"))
               if receipt_path.exists() else {})
    return {"rc": rc, "report": report, "receipt": receipt}


def test_the_verified_binding_proves_the_recorded_sha_vouches_for_the_run(
        bound_repo, shared_run):
    """The repository answers the three questions, not the report.

    (a) the sha resolves; (b) the corpus AT that sha re-hashes to the scored
    corpus and the pre-registration blob there is byte-identical to the
    published one; (c) the pre-registration's own commit is a PROPER ancestor
    of the run commit.
    """
    binding, reasons = m.verify_commit_binding(
        commit=bound_repo["run_commit"], repo_root=bound_repo["repo"],
        corpus_root=bound_repo["corpus_dir"], prereg=bound_repo["prereg_path"])
    assert reasons == []
    assert binding is not None
    assert binding["schema"] == m.COMMIT_BINDING_SCHEMA
    assert binding["run_commit"] == bound_repo["run_commit"]
    assert binding["prereg_commit"] == bound_repo["prereg_commit"]
    assert binding["ordering"] == "proven"
    # (b) is the part that makes "reproducible from the sha alone" true: the
    # corpus re-hashed out of the commit is the corpus that was scored.
    assert binding["corpus_hash"] == ask_corpus.compute_corpus_hash(
        bound_repo["corpus_dir"])
    assert binding["corpus_hash"] == shared_run["scored"]["corpus_hash"]
    assert binding["prereg_sha256"] == ask_corpus.sha256_file(
        bound_repo["prereg_path"])
    # the commit times are recorded, so a reviewer can re-check without git
    assert binding["prereg_commit_time"] and binding["run_commit_time"]


def test_publish_refuses_a_commit_that_does_not_resolve(tmp_path, shared_run,
                                                        capsys):
    """FAIL-BEFORE: this publish returned 0 and wrote the report.

    A `--commit` that resolves to nothing is the purest form of the defect the
    issue names — the report's whole reproducibility sentence points at a sha
    that does not exist, and nothing noticed.
    """
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(_mock_prereg(shared_run["root"])),
                           encoding="utf-8")
    scored_path = tmp_path / "scored.json"
    scored_path.write_text(json.dumps(shared_run["scored"]), encoding="utf-8")
    report = tmp_path / "run-report.md"
    receipt = tmp_path / "receipt.json"
    rc = m._main(_publish_argv(
        scored_path=scored_path, commit="c" * 40, report=report,
        receipt=receipt, corpus_root=shared_run["root"], prereg=prereg_path))
    err = capsys.readouterr().err
    assert rc == 2, "a sha that resolves to nothing must never be published"
    assert "is not a commit" in err, err
    assert not report.exists() and not receipt.exists()


def test_publish_refuses_a_commit_whose_corpus_is_not_the_scored_one(
        tmp_path, shared_run, bound_repo, capsys):
    """A REAL commit that carries a DIFFERENT corpus cannot vouch for the run.

    `c3` of the fixture re-keys one sealed gold answer after the run. The sha
    resolves, the protocol at it is identical, the ordering holds — and the
    corpus does not hash to the scored one, so `git checkout <sha>` reproduces
    a corpus these numbers did not come from.
    """
    scored_path = tmp_path / "scored.json"
    scored_path.write_text(json.dumps(shared_run["scored"]), encoding="utf-8")
    report = tmp_path / "run-report.md"
    receipt = tmp_path / "receipt.json"
    rc = m._main(_publish_argv(
        scored_path=scored_path, commit=bound_repo["rekeyed_commit"],
        report=report, receipt=receipt, corpus_root=bound_repo["corpus_dir"],
        prereg=bound_repo["prereg_path"], repo=bound_repo["repo"]))
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "DIFFERENT corpus" in err, err
    assert not report.exists() and not receipt.exists()


def test_the_ordering_claim_is_not_satisfied_when_the_protocol_shares_the_run_commit(
        bound_repo):
    """`written_before_any_arm_run: true` is a declaration; this is the check.

    Recording the run at the commit that INTRODUCES the pre-registration proves
    nothing — the protocol could have been written after the numbers were seen.
    The bar needs the pre-registration's commit to be a proper ANCESTOR.
    """
    binding, reasons = m.verify_commit_binding(
        commit=bound_repo["prereg_commit"], repo_root=bound_repo["repo"],
        corpus_root=bound_repo["corpus_dir"], prereg=bound_repo["prereg_path"])
    assert binding is None
    assert any("ordering NOT proven" in reason for reason in reasons), reasons


def test_a_shallow_boundary_at_the_prereg_commit_is_unprovable(tmp_path):
    """A grafted boundary is an ordering that CANNOT be shown, so publish refuses.

    `.git/shallow` is what `git clone --depth=N` leaves behind, and git reports
    a grafted commit's parents as none — every path then looks added by that
    commit, so `rev-list -1 -- <prereg>` returns it and the ancestry test is
    trivially true. Writing the boundary file by hand is the same shape git
    consumes (measured: `rev-list -1 <commit> -- <path>` still answers normally
    with the file in place), so this pins the refusal without cloning. Each
    shallow test builds its OWN repository: the boundary file must not leak into
    the module-scoped fixture the other ordering tests share.
    """
    fixture = _make_bound_repo(tmp_path)
    repo = Path(fixture["repo"])
    shallow = repo / ".git" / "shallow"
    shallow.write_text(fixture["prereg_commit"] + "\n", encoding="utf-8")
    # Non-vacuity: the boundary really is what the reader sees, and history is
    # still walkable — so the refusal below comes from the guard, not from git
    # having died on the file.
    assert m._shallow_boundary(repo) == {fixture["prereg_commit"]}
    assert m._git_out(repo, "rev-list", "-1", fixture["run_commit"], "--",
                      "tests/eval/ask/preregistration.json") is not None

    binding, reasons = m.verify_commit_binding(
        commit=fixture["run_commit"], repo_root=repo,
        corpus_root=fixture["corpus_dir"], prereg=fixture["prereg_path"])
    assert binding is None, "UNPROVABLE must never be recorded as a proof"
    assert any("UNPROVABLE" in reason and "SHALLOW boundary" in reason
               for reason in reasons), reasons


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root can read a 0o000 file, so the read would not fail")
def test_an_unreadable_shallow_file_is_unprovable_not_absent(tmp_path):
    """FAIL-BEFORE: an unreadable boundary read as `not shallow` — a false PASS.

    `_shallow_boundary` folded every `OSError` into `set()`, so a shallow file
    that exists but cannot be read disengaged the only guard implementing
    "UNPROVABLE, refuse": on a clone whose boundary IS the pre-registration
    commit every downstream ordering check then passes trivially and the
    binding is recorded `proven`. An unreadable boundary is UNKNOWN, never
    absent. Measured: git tolerates an unreadable `.git/shallow` (rev-list still
    answers), so the read failure is reached rather than shadowed by a git error.
    """
    fixture = _make_bound_repo(tmp_path)
    repo = Path(fixture["repo"])
    shallow = repo / ".git" / "shallow"
    shallow.write_text(fixture["prereg_commit"] + "\n", encoding="utf-8")
    shallow.chmod(0o000)
    try:
        assert m._shallow_boundary(repo) is None, (
            "an unreadable boundary must not read as 'this clone is complete'")
        binding, reasons = m.verify_commit_binding(
            commit=fixture["run_commit"], repo_root=repo,
            corpus_root=fixture["corpus_dir"], prereg=fixture["prereg_path"])
    finally:
        shallow.chmod(0o644)
    assert binding is None
    assert any("cannot READ" in reason and "UNPROVABLE" in reason
               for reason in reasons), reasons


def test_the_ordering_cross_check_compares_instants_not_wall_clock_text(
        tmp_path):
    """FAIL-BEFORE: the clock cross-check refused a correctly ordered pair.

    `%cI` renders each commit in its OWN UTC offset, so comparing two `%cI`
    strings compares wall clocks: `"…T12:00+02:00" > "…T10:30-05:00"` is True
    even though the first instant (10:00Z) is 5h30m EARLIER than the second
    (15:30Z). The ancestry check at the same site passes; only the text
    comparison refused — with a false accusation of rewritten history. Mixed
    offsets are the norm here (lane commits `-05:00`, others `+00:00`).
    """
    repo = _make_dated_repo(
        tmp_path, prereg_date="2026-01-01T12:00:00+02:00",
        run_date="2026-01-01T10:30:00-05:00")
    binding, reasons = m.verify_commit_binding(
        commit=repo["run_commit"], repo_root=repo["repo"],
        corpus_root=repo["corpus_dir"], prereg=repo["prereg_path"])
    assert reasons == []
    assert binding is not None
    assert binding["ordering"] == "proven"
    # Non-vacuity: the ISO TEXT really does sort the wrong way (that is what
    # the pre-fix `>` compared), while the EPOCHS — the instants — do not.
    assert binding["prereg_commit_time"] > binding["run_commit_time"]
    assert binding["prereg_commit_epoch"] < binding["run_commit_epoch"]


def test_the_ordering_cross_check_catches_a_rewritten_clock_across_offsets(
        tmp_path):
    """FAIL-BEFORE (the worse direction): it FAILED OPEN.

    The text comparison is symmetric, so when the pre-registration's committer
    date is genuinely LATER than the run's — rewritten history, the one thing
    this cross-check exists to catch — an offset difference can order the
    strings the other way and the check stayed silent while the binding was
    recorded `proven` and rendered into §6 as evidence.
    """
    repo = _make_dated_repo(
        tmp_path, prereg_date="2026-01-01T09:00:00-05:00",
        run_date="2026-01-01T12:00:00+02:00")
    binding, reasons = m.verify_commit_binding(
        commit=repo["run_commit"], repo_root=repo["repo"],
        corpus_root=repo["corpus_dir"], prereg=repo["prereg_path"])
    assert binding is None
    assert any("ordering NOT proven" in reason for reason in reasons), reasons
    assert any("LATER than the run commit" in reason for reason in reasons)
    # Non-vacuity: the ISO text sorts the way a text comparison reads as fine.
    prereg_text = m._git_out(repo["repo"], "show", "-s", "--format=%cI",
                             repo["prereg_commit"])
    run_text = m._git_out(repo["repo"], "show", "-s", "--format=%cI",
                          repo["run_commit"])
    assert prereg_text < run_text, (prereg_text, run_text)
    assert m._commit_epoch(repo["repo"], repo["prereg_commit"]) > \
        m._commit_epoch(repo["repo"], repo["run_commit"])


def test_the_commit_times_are_recorded_as_epochs_beside_the_iso_strings(
        bound_repo):
    """A receipt-only auditor re-checks the polarity without running git.

    The ISO strings cannot decide it (they carry their own offsets), so the
    comparable datum has to travel IN the binding: both epochs, ordered the
    same way the ancestry proof orders the commits.
    """
    binding, reasons = m.verify_commit_binding(
        commit=bound_repo["run_commit"], repo_root=bound_repo["repo"],
        corpus_root=bound_repo["corpus_dir"], prereg=bound_repo["prereg_path"])
    assert reasons == []
    assert isinstance(binding["prereg_commit_epoch"], int)
    assert isinstance(binding["run_commit_epoch"], int)
    assert binding["prereg_commit_epoch"] <= binding["run_commit_epoch"]
    # Each epoch IS the epoch of the commit whose ISO string travels beside it.
    assert m._commit_epoch(bound_repo["repo"], binding["prereg_commit"]) == \
        binding["prereg_commit_epoch"]
    assert m._commit_epoch(bound_repo["repo"], binding["run_commit"]) == \
        binding["run_commit_epoch"]
    assert binding["prereg_commit_time"] and binding["run_commit_time"]


def test_the_fixture_commits_are_hermetic_against_ambient_git_config(
        tmp_path, monkeypatch):
    """A HOSTILE global git config must not turn the fixtures into errors.

    `_git_commit` used to be a bare `git commit` inheriting the environment, so
    `commit.gpgsign = true` made the `assert` fire (an ERROR, not a finding) and
    a global `core.hooksPath` ran the host's hooks inside the fixture this fleet
    builds. The two probes prove the config really is live — so the fixture's
    success below is not luck — and a bare `git init` under `init.defaultBranch
    = trunk` proves the branch pin is load-bearing.
    """
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    hostile = tmp_path / "hostile-gitconfig"
    hostile.write_text(
        f"[commit]\n\tgpgsign = true\n[init]\n\tdefaultBranch = trunk\n"
        f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    # A BARE init leaves the ambient default branch in place — the hostile one.
    assert m._git(repo, "init", "-q").returncode == 0
    # `symbolic-ref` reads the unborn branch; `rev-parse --abbrev-ref HEAD`
    # needs a commit and errors on a fresh repo.
    assert m._git_out(repo, "symbolic-ref", "--short", "HEAD") == "trunk"
    assert m._git(repo, "add", "-A").returncode == 0
    # Probe 1: signing is live, so a bare commit fails.
    bare = m._git(repo, "-c", "user.name=pi-test",
                  "-c", "user.email=pi-test@example.invalid",
                  "commit", "-q", "-m", "bare")
    assert bare.returncode != 0, (
        "the hostile config is not live — this test would pass for the wrong "
        f"reason (stdout={bare.stdout!r})")
    # Probe 2: the hook is live too, so even an unsigned bare commit fails.
    hooked = m._git(repo, "-c", "commit.gpgsign=false",
                    "-c", "user.name=pi-test",
                    "-c", "user.email=pi-test@example.invalid",
                    "commit", "-q", "-m", "hooked")
    assert hooked.returncode != 0, (
        "the hooksPath is not live — this test would pass for the wrong "
        f"reason (stdout={hooked.stdout!r})")

    # ... and the fixture's own path ignores every one of those knobs.
    sha = _git_commit(repo, "hermetic")
    assert len(sha) == 40


def test_the_fixture_init_ignores_an_ambient_template_dir(tmp_path, monkeypatch):
    """The fixture's OWN init must not inherit a host template or branch default.

    `git init` copies every non-dot file of the template directory into
    `$GIT_DIR` (git-init[1], TEMPLATE DIRECTORY), so a host template shipping
    an executable `hooks/pre-commit` lands in the fixture's `.git/hooks` — and
    the hermetic commit cannot switch it off by config, because nulling
    `core.hooksPath` makes git fall back to `.git/hooks`. A bare `git init`
    under the hostile config (the shape `test_cli_preregister_run_score_publish`
    used before `_git_init`) demonstrates all three exposures at once — trunk
    branch, copied hook, failing commit — so the assertions below are not
    satisfied by luck.
    """
    template = tmp_path / "hostile-template"
    (template / "hooks").mkdir(parents=True)
    (template / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 1\n",
                                                   encoding="utf-8")
    (template / "hooks" / "pre-commit").chmod(0o755)
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    hostile = tmp_path / "hostile-gitconfig"
    hostile.write_text(
        f"[init]\n\tdefaultBranch = trunk\n\ttemplateDir = {template}\n"
        f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))

    # Probe: a BARE init inherits both of them — the branch AND the hook.
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    assert m._git(ambient, "init", "-q").returncode == 0
    assert m._git_out(ambient, "symbolic-ref", "--short", "HEAD") == "trunk"
    assert (ambient / ".git" / "hooks" / "pre-commit").exists(), (
        "the hostile init.templateDir is not live — this test would pass for "
        "the wrong reason")

    # ... and the fixture's own init inherits NEITHER, so the commit below
    # cannot be run through a hook that arrived from outside the repository.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    assert m._git_out(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    assert len(_git_commit(repo, "hermetic")) == 40


def test_a_published_report_carries_the_verified_binding(published, bound_repo):
    """The artifact a reviewer reads must carry the proof, not the assertion.

    §1 and §6 are rendered from the binding the publisher verified, so the
    report can be audited (`git merge-base --is-ancestor <prereg> <run>`) from
    the text alone.
    """
    assert published["rc"] == 0
    receipt = published["receipt"]
    binding = receipt["commit_binding"]
    assert binding["run_commit"] == bound_repo["run_commit"]
    assert binding["prereg_commit"] == bound_repo["prereg_commit"]
    assert binding["ordering"] == "proven"
    assert receipt["commit"] == bound_repo["run_commit"]
    assert m.validate_ask_receipt(receipt) == []
    text = published["report"].read_text(encoding="utf-8")
    assert bound_repo["run_commit"] in text
    assert bound_repo["prereg_commit"] in text
    assert "**PROVEN**" in text
    assert (f"git merge-base --is-ancestor {bound_repo['prereg_commit']} "
            f"{bound_repo['run_commit']}") in text


def test_a_receipt_may_not_publish_an_unproven_ordering(shared_run):
    """The auditor's half: a receipt that states a sha without the binding, or
    carries a binding whose ordering was not proven, is invalid."""
    scored = shared_run["scored"]
    good = _receipt(scored)
    assert m.validate_ask_receipt(good) == []

    missing = {key: value for key, value in good.items()
               if key != "commit_binding"}
    assert any("commit_binding: missing" in issue
               for issue in m.validate_ask_receipt(missing))

    unproven = _receipt(scored)
    unproven["commit_binding"]["ordering"] = "unproven"
    assert any("PROVEN ordering" in issue
               for issue in m.validate_ask_receipt(unproven))

    placeholder = _receipt(scored)
    placeholder["commit_binding"]["run_commit"] = "HEAD"
    assert any("not a full 40-hex sha" in issue
               for issue in m.validate_ask_receipt(placeholder))

    same_commit = _receipt(scored)
    same_commit["commit_binding"]["prereg_commit"] = same_commit["commit"]
    assert any("share a commit" in issue
               for issue in m.validate_ask_receipt(same_commit))

    other_corpus = _receipt(scored)
    other_corpus["commit_binding"]["corpus_hash"] = "sha256:" + "9" * 64
    assert any("does not vouch for the corpus" in issue
               for issue in m.validate_ask_receipt(other_corpus))

    # The ordering is a claim about INSTANTS: an ISO pair alone cannot decide
    # it, so a receipt that drops the epochs is not re-checkable.
    no_epochs = _receipt(scored)
    del no_epochs["commit_binding"]["prereg_commit_epoch"]
    del no_epochs["commit_binding"]["run_commit_epoch"]
    assert any("expected epoch seconds" in issue
               for issue in m.validate_ask_receipt(no_epochs))

    # ... and the polarity is checked, not just the presence.
    inverted = _receipt(scored)
    inverted["commit_binding"]["prereg_commit_epoch"] = 1767312000
    inverted["commit_binding"]["run_commit_epoch"] = 1767225600
    assert any("is LATER than the run commit's" in issue
               for issue in m.validate_ask_receipt(inverted))

    # DELETING the datum a cross-check compares against must fail, not skip: the
    # pre-registration hash the binding carries has nothing to corroborate it
    # without `resolved_config.preregistration_sha256`, and the old `if
    # prereg_sha and ...` guard let a forger drop the field and validate clean.
    dropped_prereg_sha = _receipt(scored)
    del dropped_prereg_sha["resolved_config"]["preregistration_sha256"]
    assert any("preregistration_sha256: missing" in issue
               for issue in m.validate_ask_receipt(dropped_prereg_sha)), (
        "removing the field the check compares against must invalidate the "
        "receipt, not disable the check")

    # The pre-registration's commit is the value §6 hands a re-verifier, so it
    # needs the same full-sha rule as the run commit — a placeholder there makes
    # the published `git merge-base --is-ancestor` unrunnable while the receipt
    # still reads `ordering: proven`.
    placeholder_prereg = _receipt(scored)
    placeholder_prereg["commit_binding"]["prereg_commit"] = "b" * 12
    assert any("commit_binding.prereg_commit" in issue and "40-hex" in issue
               for issue in m.validate_ask_receipt(placeholder_prereg))

    # `ordering: proven` is a claim that rests on a proof, so the proof is part
    # of what must be present — otherwise the receipt asserts precedence while
    # carrying none.
    no_proof = _receipt(scored)
    del no_proof["commit_binding"]["ordering_proof"]
    assert any("commit_binding.ordering_proof" in issue
               for issue in m.validate_ask_receipt(no_proof))

# ── 8. the RUN records the tree that produced the numbers ─────────────────
#
# §7's binding makes the named sha vouch for the CORPUS and the PROTOCOL at
# that sha. It says nothing about the tree the NUMBERS came from: a lane can run
# the corpus at tree A, then name a commit B whose corpus and protocol are
# byte-identical (any `main` merge between the run and the publish has exactly
# that shape) and still publish a report telling a reader to `git checkout B`.
# These pin the other half — the run records the commit it executed from and
# whether that tree was clean, and publish refuses a run it cannot vouch for.

def test_the_run_records_the_commit_and_the_clean_tree_it_ran_from(bound_repo):
    """The record is produced by the step that ran, not by the publisher."""
    identity = m.code_identity(bound_repo["repo"])
    assert identity["schema"] == m.CODE_IDENTITY_SCHEMA
    assert identity["commit"] == bound_repo["run_commit"]
    assert identity["dirty_paths"] == []

    run = m.run_sealed(corpus_root=bound_repo["corpus_dir"],
                       repo_root=bound_repo["repo"],
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    assert run["code_identity"]["commit"] == bound_repo["run_commit"]
    assert run["code_identity"]["dirty_paths"] == []
    scored = m.score(run_report=run, corpus_root=bound_repo["corpus_dir"],
                     judge=_StubJudge(), judge_spec=_StubJudge.model_id)
    # The scored artifact carries it forward; publish compares it, so a score
    # step that dropped it would turn the check off silently.
    assert scored["code_identity"]["commit"] == bound_repo["run_commit"]


def test_a_run_that_records_no_tree_cannot_be_published(shared_run):
    """`run_sealed()` with no repository records the absence, and a run that
    cannot say where it came from is not publishable — the receipt refuses it."""
    run = m.run_sealed(corpus_root=shared_run["root"],
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])))
    assert run["code_identity"]["commit"] is None
    assert "no repository" in run["code_identity"]["reason"]
    scored = copy.deepcopy(shared_run["scored"])
    scored["code_identity"] = run["code_identity"]
    receipt = m.build_receipt(
        scored=scored, commit=shared_run["scored"]["code_identity"]["commit"],
        cost_usd=0.0,
        commit_binding=_binding(shared_run["scored"]["code_identity"]["commit"],
                                scored["corpus_hash"]))
    assert any("not a full 40-hex sha" in issue
               for issue in m.validate_ask_receipt(receipt))


def test_publish_refuses_a_run_that_ran_from_a_different_commit(
        tmp_path, shared_run, bound_repo, capsys):
    """FAIL-BEFORE: this publish returned 0 and wrote the report.

    The run recorded `c1`; the publish names `c2`. Both commits carry the same
    corpus and the same protocol — which is exactly why §7's binding passes —
    and the numbers in the report came from `c1`'s code, so `git checkout c2`
    reproduces this corpus and re-runs DIFFERENT code. That is the residual of
    "reproducible from the recorded sha alone" that a free-text `--commit` left
    open one step earlier in the pipeline.
    """
    scored = copy.deepcopy(shared_run["scored"])
    scored["code_identity"]["commit"] = bound_repo["prereg_commit"]
    scored_path = tmp_path / "scored.json"
    scored_path.write_text(json.dumps(scored), encoding="utf-8")
    report = tmp_path / "run-report.md"
    receipt = tmp_path / "receipt.json"
    rc = m._main(_publish_argv(
        scored_path=scored_path, commit=bound_repo["run_commit"], report=report,
        receipt=receipt, corpus_root=bound_repo["corpus_dir"],
        prereg=bound_repo["prereg_path"], repo=bound_repo["repo"]))
    err = capsys.readouterr().err
    assert rc == 2, "a run from a DIFFERENT tree must never be published"
    assert "DIFFERENT tree" in err, err
    assert not report.exists() and not receipt.exists()


def test_publish_refuses_a_run_made_from_a_dirty_tree(
        tmp_path, shared_run, bound_repo, capsys):
    """FAIL-BEFORE: this publish returned 0 and wrote the report.

    A run from a tree with uncommitted changes reproduces from "sha plus a
    patch nobody recorded", which is not the recorded sha alone. The path is
    named in the refusal, so the lane knows what to commit and re-run.
    """
    scored = copy.deepcopy(shared_run["scored"])
    scored["code_identity"]["dirty_paths"] = ["tortoise/ask_lane.py"]
    scored_path = tmp_path / "scored.json"
    scored_path.write_text(json.dumps(scored), encoding="utf-8")
    report = tmp_path / "run-report.md"
    receipt = tmp_path / "receipt.json"
    rc = m._main(_publish_argv(
        scored_path=scored_path, commit=bound_repo["run_commit"], report=report,
        receipt=receipt, corpus_root=bound_repo["corpus_dir"],
        prereg=bound_repo["prereg_path"], repo=bound_repo["repo"]))
    err = capsys.readouterr().err
    assert rc == 2, "a run from a dirty tree must never be published"
    assert "uncommitted path" in err and "tortoise/ask_lane.py" in err, err
    assert not report.exists() and not receipt.exists()


def test_the_published_report_names_the_tree_the_run_executed_from(published,
                                                                  bound_repo):
    """§1 and §6 must state it, so a reader can audit the provenance from the
    rendered document rather than from the machinery that made it."""
    assert published["rc"] == 0
    identity = published["receipt"]["code_identity"]
    assert identity["commit"] == bound_repo["run_commit"]
    assert identity["dirty_paths"] == []
    text = published["report"].read_text(encoding="utf-8")
    # TWICE: §1's provenance row AND §6's binding table. Asserting presence
    # alone passes on §1 by itself, which would let the §6 row be deleted
    # unnoticed — and §6 is the half a re-verifier re-checks the binding from.
    assert text.count("Tree the run executed from") == 2
    assert text.count("clean at run time") >= 1


# ── 8b. the checks that ask the repository must be MEASURED, not injected ──
#
# The refusal tests above inject `code_identity` values, so they pin the
# VALIDATION. None of them ran `code_identity()` against a dirty tree, which
# left the half that decides everything — is a dirty tree DETECTED? — unpinned:
# a `dirty_paths` that was always `[]` (or a `git status` failure read as clean)
# passed the whole file. These measure the detection, and they measure the two
# ways a run can escape its own record: a tree that changes WHILE it runs, and a
# renderer that turns an absent record into the word "clean".

def _make_surface_repo(base: Path) -> Path:
    """A throwaway repo that HAS the executed surface (``tortoise/``+``tools/``).

    ``code_identity`` asks git about the surface a measurement run can import,
    so a mechanism test needs a repository where that surface EXISTS — the
    corpus fixtures in this file (a corpus, a pre-registration and a marker) do
    not. Also asserts the surface is clean before a test dirties it, which is
    the other half of the discriminating power: the new scan must not refuse a
    tree that is actually clean.
    """
    repo = base / "surface-repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tortoise").mkdir()
    _git_init(repo)
    (repo / "tools" / "probe_step.py").write_text("VALUE = 1\n",
                                                   encoding="utf-8")
    (repo / "tortoise" / "ask_lane.py").write_text("VALUE = 1\n",
                                                    encoding="utf-8")
    assert _git_commit(repo, "the executed surface")
    assert m.code_identity(repo)["dirty_paths"] == []
    return repo


def test_an_IGNORE_RULE_cannot_hide_an_untracked_executed_module(tmp_path):
    """FAIL-BEFORE: an ignored, importable module read as CLEAN.

    `git status --untracked-files=normal` never lists an ignored path, so a
    `.gitignore` entry (or `.git/info/exclude`, or a global `core.excludesFile`)
    hides an untracked module that a run imports — the tree reads clean and the
    report tells a reader the numbers came from the recorded sha. The premise is
    asserted (`status` really prints nothing) so this cannot pass by the harness
    failing to reproduce the hiding rule.
    """
    repo = _make_surface_repo(tmp_path)
    (repo / ".gitignore").write_text("local_patch.py\n", encoding="utf-8")
    assert _git_commit(repo, "ignore: a local patch")
    patched = repo / "tortoise" / "local_patch.py"
    patched.write_text("VALUE = 999  # unrecorded patch\n", encoding="utf-8")
    assert m._git(repo, "status", "--porcelain",
                  "--untracked-files=normal").stdout.strip() == "", (
        "the fixture did not reproduce the hiding ignore rule")
    identity = m.code_identity(repo)
    assert any("tortoise/local_patch.py" in path
               for path in identity["dirty_paths"]), identity


def test_an_INDEX_FLAG_cannot_hide_a_modified_executed_module(tmp_path):
    """FAIL-BEFORE: a modified module behind an index flag read as CLEAN.

    `--assume-unchanged` and `--skip-worktree` both tell git not to examine a
    file's working-tree content (git-update-index, ASSUME UNCHANGED / and
    SKIP-WORKTREE BIT), so the patched executed module prints NOTHING in
    `git status` while the bytes on disk are the patched ones — the exact false
    PASS this record exists to close, with no receipt field tampered with.
    Both flags are exercised: they are different mechanisms (the tag is `h`
    versus `S`) and a check that reads only one of them leaves the other open.
    """
    for flag in ("--assume-unchanged", "--skip-worktree"):
        repo = _make_surface_repo(tmp_path / flag.lstrip("-"))
        target = "tools/probe_step.py"
        assert m._git(repo, "update-index", flag, target).returncode == 0
        (repo / target).write_text("VALUE = 999  # unrecorded patch\n",
                                   encoding="utf-8")
        assert m._git(repo, "status", "--porcelain",
                      "--untracked-files=normal").stdout.strip() == "", (
            f"the fixture did not reproduce the {flag} hiding flag")
        identity = m.code_identity(repo)
        assert any("tools/probe_step.py" in path
                   for path in identity["dirty_paths"]), (flag, identity)
        # ... and clearing the flag clears the refusal: the record is about the
        # suppression, not about the file having a patch someone could see.
        assert m._git(repo, "update-index", "--no" + flag[1:],
                      target).returncode == 0
        assert m._git(repo, "checkout", "--", target).returncode == 0
        assert m.code_identity(repo)["dirty_paths"] == [], flag


def test_a_dirty_tree_is_DETECTED_and_refused_end_to_end(bound_repo, tmp_path,
                                                         capsys):
    """FAIL-BEFORE with `code_identity` forced clean: this published (rc 0).

    The identity is COMPUTED from a genuinely dirty repository — a modified
    tracked file and an untracked one — and carried through `run_sealed` and
    `score`, so nothing in the chain is injected. The refusal must then come
    from the record the run made.
    """
    repo = bound_repo["repo"]
    tracked = repo / "run.marker"
    tracked.write_text("edited after the run\n", encoding="utf-8")
    (repo / "untracked_note.txt").write_text("never committed\n",
                                              encoding="utf-8")
    # ⛔ try/finally: `bound_repo` is MODULE-scoped, so an assertion that fires
    # below would otherwise leave HEAD dirty and fail every later test in the
    # file for a reason that is not its own subject.
    try:
        identity = m.code_identity(repo)
        assert "run.marker" in identity["dirty_paths"], identity
        assert "untracked_note.txt" in identity["dirty_paths"], identity

        run = m.run_sealed(corpus_root=bound_repo["corpus_dir"], repo_root=repo,
                           reader_factory=_factory(_RecordingReader(_answer_map(), [])))
        assert run["code_identity"]["dirty_paths"], "the run lost the dirty paths"
        scored = m.score(run_report=run, corpus_root=bound_repo["corpus_dir"],
                         judge=_StubJudge(), judge_spec=_StubJudge.model_id)
        scored_path = tmp_path / "scored.json"
        scored_path.write_text(json.dumps(scored), encoding="utf-8")
        report = tmp_path / "run-report.md"
        receipt = tmp_path / "receipt.json"
        rc = m._main(_publish_argv(
            scored_path=scored_path, commit=bound_repo["run_commit"], report=report,
            receipt=receipt, corpus_root=bound_repo["corpus_dir"],
            prereg=bound_repo["prereg_path"], repo=repo))
        err = capsys.readouterr().err
        assert rc == 2, "a run from a dirty tree must never be published"
        assert "uncommitted path" in err and "untracked_note.txt" in err, err
        assert not report.exists() and not receipt.exists()
    finally:
        # Leave the fixture as the other tests expect it (the module is scoped).
        m._git(repo, "checkout", "--", "run.marker")
        (repo / "untracked_note.txt").unlink(missing_ok=True)
    assert m.code_identity(repo)["dirty_paths"] == []


def test_a_tree_that_moves_WHILE_the_run_measures_is_refused(
        bound_repo, tmp_path, capsys):
    """FAIL-BEFORE with the identity captured AFTER the case loop: this published.

    A real reader call takes seconds; a lane that commits while the run is in
    flight leaves a clean worktree at a NEWER commit. Recording the identity at
    the end would then name that newer commit as "the tree the numbers came
    from" — the exact false PASS this record exists to close, reached with no
    misbehaviour at all. The identity is read BEFORE the run and re-read after
    it, and a start≠end pair is refused because no single sha reproduces a run
    that straddled two trees.
    """
    repo = bound_repo["repo"]
    start_commit = m.code_identity(repo)["commit"]

    moved = []

    def _commit_mid_run(_sdk):
        """Runs INSIDE the case loop (the gate arm's post-seed hook), so the
        commit lands while the measurement is in flight — the shape a lane
        produces when it lands its own work between starting a run and reading
        its numbers. Idempotent: the hook fires once per gate arm, and only the
        FIRST call has anything to commit."""
        if moved:
            return
        moved.append(1)
        (repo / "mid_run.marker").write_text("landed mid-run\n",
                                            encoding="utf-8")
        m._git(repo, "add", "mid_run.marker")
        assert _git_commit(repo, "moved during the run")

    run = m.run_sealed(corpus_root=bound_repo["corpus_dir"], repo_root=repo,
                       reader_factory=_factory(_RecordingReader(_answer_map(), [])),
                       mutate=_commit_mid_run,
                       mutate_label="test hook: the tree moved mid-run")
    # ⛔ try/finally: `bound_repo` is MODULE-scoped. Without it, one failing
    # assertion below leaves HEAD at the mid-run commit and every later test in
    # the file fails for a reason that is not its own subject.
    try:
        identity = run["code_identity"]
        assert identity["commit"] == start_commit, (
            "the record must name the tree the run STARTED on")
        assert identity["at_end"]["commit"] != start_commit, (
            "the run did not notice that HEAD moved under it")

        scored = m.score(run_report=run, corpus_root=bound_repo["corpus_dir"],
                         judge=_StubJudge(), judge_spec=_StubJudge.model_id)
        scored_path = tmp_path / "scored.json"
        scored_path.write_text(json.dumps(scored), encoding="utf-8")
        report = tmp_path / "run-report.md"
        receipt = tmp_path / "receipt.json"
        rc = m._main(_publish_argv(
            scored_path=scored_path, commit=start_commit, report=report,
            receipt=receipt, corpus_root=bound_repo["corpus_dir"],
            prereg=bound_repo["prereg_path"], repo=repo))
        err = capsys.readouterr().err
        assert rc == 2, "a run that straddled two trees must never be published"
        assert "CHANGED while the run was in progress" in err, err
        assert not report.exists() and not receipt.exists()
    finally:
        # Leave the module-scoped fixture where the other tests expect it.
        m._git(repo, "checkout", "-q", start_commit)
    assert m.code_identity(repo)["commit"] == start_commit


def test_an_absent_record_never_renders_as_a_clean_tree(shared_run, tmp_path):
    """FAIL-BEFORE: `(identity or {}).get('dirty_paths') or []` rendered
    "clean at run time" for a receipt that carried NO record at all — the
    fail-open default in the one function the README calls the audit surface."""
    scored = copy.deepcopy(shared_run["scored"])
    scored.pop("code_identity", None)
    receipt = m.build_receipt(
        scored=scored, commit="a" * 40, cost_usd=0.0,
        commit_binding=_binding("a" * 40, scored["corpus_hash"]))
    assert "code_identity" not in receipt, "nothing to render — that is the point"
    prereg, _ = _prereg(tmp_path)
    text = m.render_report(receipt=receipt, scored=scored, prereg=prereg,
                           prereg_sha256="sha256:" + "0" * 64)
    assert "NOT RECORDED" in text
    assert "clean at run time" not in text

    # ⛔ And a record that NAMES NO TREE — `code_identity(None)`, the shape a
    # run without a repository produces — is the same assertion with an empty
    # `dirty_paths` list: a list, and empty, so a renderer that checks only the
    # list renders "clean at run time" beside a commit column of "—".
    nameless = copy.deepcopy(scored)
    nameless["code_identity"] = m.code_identity(None)
    assert nameless["code_identity"]["commit"] is None
    assert nameless["code_identity"]["dirty_paths"] == []
    receipt = m.build_receipt(
        scored=nameless, commit="a" * 40, cost_usd=0.0,
        commit_binding=_binding("a" * 40, nameless["corpus_hash"]))
    text = m.render_report(receipt=receipt, scored=nameless, prereg=prereg,
                           prereg_sha256="sha256:" + "0" * 64)
    assert "NOT RECORDED" in text
    assert "clean at run time" not in text


def test_untracked_files_are_seen_even_when_git_is_configured_to_hide_them(
        bound_repo):
    """FAIL-BEFORE without `--untracked-files=normal`: this read as CLEAN.

    `git status --porcelain` answers "was the tree clean?" using the READER's
    git config, so `status.showUntrackedFiles=no` — a common setting on a
    repository this size — makes an untracked, importable file invisible and the
    tree reads clean. The question must be asked of the TREE, not of the
    operator's config.
    """
    repo = bound_repo["repo"]
    assert m._git(repo, "config", "status.showUntrackedFiles", "no").returncode == 0
    try:
        (repo / "invisible_module.py").write_text("X = 1\n", encoding="utf-8")
        # The premise: with the config in force, an unpinned ask says "clean".
        unpinned = m._git(repo, "status", "--porcelain")
        assert unpinned.stdout.strip() == "", (
            "the fixture did not reproduce the hiding config")
        identity = m.code_identity(repo)
        assert "invisible_module.py" in identity["dirty_paths"], identity
    finally:
        m._git(repo, "config", "--unset", "status.showUntrackedFiles")
        (repo / "invisible_module.py").unlink()
    assert m.code_identity(repo)["dirty_paths"] == []


def test_an_end_record_that_cannot_say_whether_it_was_clean_is_refused(
        shared_run):
    """`(at_end.get('dirty_paths') or [])` folded an ABSENT key into "clean".

    The start half type-checks the field, so `None`/`""`/`{}` cannot read as
    clean there; the end half folded it, and a record that cannot say whether
    the END tree was clean is not evidence that it was.
    """
    scored = copy.deepcopy(shared_run["scored"])
    receipt = _receipt(scored)
    del receipt["code_identity"]["at_end"]["dirty_paths"]
    assert any("at_end.dirty_paths: expected a list" in issue
               for issue in m.validate_ask_receipt(receipt))

    # The same record with the field present is valid: the refusal is about the
    # missing datum, not about the record's shape in general.
    assert m.validate_ask_receipt(_receipt(copy.deepcopy(shared_run["scored"]))) == []
