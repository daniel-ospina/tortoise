"""#9984 — the required check's predicate: "did THIS PR introduce a failure?".

`python-ci-gate` is a single required check aggregating every shard, and its old
predicate was "does anything fail anywhere?". One pre-existing trunk red therefore
red'd the required check for EVERY PR in the repo (measured 2026-10-25: `main` green in
26 of 27 Python CI jobs, one red in `tests/test_cache_projection_live.py` — #9979 —
red'd all 11 open PRs, merge rate zero for ~8 hours). The rail already computes the
right predicate and exempts a failure main itself has; the gate asked a different
question about the same tree.

So the gate does not get a predicate of its own. It assembles the SAME two inputs the
rail feeds and hands them to the SAME decision function — `.github/scripts/
new-failure-gate.py` drives `scripts/ci-failure-set.sh` and `scripts/ci_exemption.py`
(#3756), which live in agent-infra behind the machine-local `scripts/` pointer
(#9475). Off a developer host that pointer is absent, so the decision cases below skip
with an ANNOUNCED reason (the `tests/test_pytest_attribution.py` pattern); the
fail-closed cases need no rail and always run.

WHAT THESE CASES PIN, against #9984's own acceptance list:
  * a PR whose only failures are main's pre-existing reds PASSES, naming them;
  * a PR that introduces one new failing test FAILS, naming it;
  * an unreadable baseline FAILS CLOSED (pointed at a nonexistent run / an empty
    window / an unwritten counters report);
  * a "quarantine" stops applying: the exemption IS the shared decision's own
    (main's id + an overlapping signature + main's row measured over >= min_runs), so
    when main heals the same PR failure becomes NEW and blocks;
  * the rail and the gate AGREE on the same fixture, proven by running the rail's own
    `decide` CLI and the gate over one fixture and comparing verdicts;
  * a leg that never ran is never exemptible (cancelled / no failing job in the
    listing / empty log is UNMEASURED, never CLEAN).

TWO HALVES, TWO KINDS OF EVIDENCE (#10202 review 5). The exempting half needs the real
shared decision, so those cases announce a SKIP when agent-infra is not checked out. The
BLOCKING half — `decide` says BLOCK ⇒ the required check FAILS — is pinned by cases that
need no rail at all (a stand-in CALLEE, `_decision_stub`), so it executes on every CI
runner. Which kind a case is, is stated in its own block, never inferred from a green run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / ".github" / "scripts" / "new-failure-gate.py"
EXEMPTION_PY = REPO / "scripts" / "ci_exemption.py"

#: sha256 of `.github/scripts/new-failure-gate.py` — the file that AUTHORS the required
#: check's verdict (#10202 review 5, issue 1).
#:
#: ⛔ WHY A WHOLE-FILE DIGEST. `python-ci-gate` DELEGATES every deferrable `failure` to
#: `ci-introduced-failure` and reads that job's `success` as the certification, so this
#: module now authors the required check — and it sat outside EVERY pin. The repo
#: already treats exactly that as unacceptable for the aggregate's own step
#: (`tests/test_capstone_preregistration_9075.py`, #9075: "a step in that job can export
#: `BASH_ENV` and make the required check exit 0 over a red leg"). The blocking path is
#: pinned by execution (the `_decision_stub` cases below run in CI), so this digest is
#: the second half: it makes every edit to the verdict's author DELIBERATE rather than
#: silent, which is the property the `core` leg alone cannot give (`core` runs in the
#: `test` leg, and `test` is itself a deferrable leg — the module's own regression signal
#: would be adjudicated by the module under regression).
#:
#: Re-cut with: `sha256sum .github/scripts/new-failure-gate.py` after reading the diff.
#:   #9984 — first cut, from #10202 (the module as landed, 658 lines).
#:   #10289 — re-cut: the LEG dimension of measuredness (`main_leg_coverage`) — a leg main
#:            never EXECUTED is UNMEASURED, never clean. Nothing in the decision changed.
#:   #10564 (c561c2edc) — re-cut: the leg-coverage claim is scoped to the runs the probe
#:            actually READ (`no EXAMINED run executed this leg`), and the counter
#:            shortcut is documented as sufficient-not-necessary for a green run.
#:   #10564 (cfbd25685) — re-cut: the refusal NAMES the executed set, generated from
#:            `_EXECUTED_CONCLUSIONS` (`", ".join(sorted(...))`) so the words cannot drift
#:            from the constant.
#:   #10564 (91188fcfa) — re-cut: the non-executing states are marked `e.g.` ILLUSTRATIVE
#:            (not exhaustive) and include `startup_failure` — the module's own
#:            `_UNCERTIFIABLE_CONCLUSIONS` member and the shape the probe most often reads;
#:            enumerating all six as final would re-seal the claim this line of review
#:            removed. The test pins the executed-set clause AND the markers. (The commit
#:            after this one changes this file only, so the digest stays at 91188fcfa's.)
#:            No logic change in any re-cut: no new branch, no exit-code change, no
#:            polarity change.
_GATE_MODULE_SHA256 = "f4108e2e3d4db724a2b046f4ccd18e060a8652f21676d838b4c6f1889cb1886b"

#: The shared decision, reached through the machine-local `scripts/` pointer
#: (`$AGENT_INFRA_PATH`, gitignored, NOT tracked — #9475). Missing off a developer box
#: that has never run `tools/link_agent_infra_scripts.py`, which is why the cases that
#: need it are skipped LOUDLY rather than deleted.
RAIL_AVAILABLE = EXEMPTION_PY.is_file()
RAIL_ABSENT_REASON = (
    "agent-infra rail not checked out: `scripts/ci_exemption.py` is reached through the "
    "machine-local `scripts/` pointer (#9475: gitignored, NOT tracked — materialised from "
    "$AGENT_INFRA_PATH by `tools/link_agent_infra_scripts.py`). The rail-and-gate "
    "agreement cases need the SHARED decision, and a vendored stand-in would be the "
    "second implementation #9984 constraint 7 forbids — so this skips instead of "
    "asserting against a copy. ⛔ AND NO CI JOB RUNS THEM: `python-ci.yml` is "
    "self-contained BY DECISION (#555 — no `scripts`/`agent-infra` symlink resolves on "
    "the runner), and the only job that checks agent-infra out (`ci-introduced-failure`, "
    "at `.ci-predicate`) runs no pytest at all. So on CI these cases SKIP BY NAME and a "
    "green leg means 'the structural pins held', never 'the decision path ran' — the "
    "wording `tests/test_pytest_attribution.py` uses for the same layer. This file used "
    "to claim the opposite ('the gate itself is exercised with the real module in CI'), "
    "which no job makes true (#10202 review 4)."
)


# ── fixtures: a realistic capture, not a synthetic one ──────────────────────
#
# ⛔ THE SHAPE IS THE PRODUCTION SHAPE (#10202 review 4). The gate reads its logs from
# `gh api repos/<r>/actions/jobs/<id>/logs`, which stamps EVERY line with a bare
# ISO-8601 instant (BOM on the first) — so a fixture of bare `FAILED` lines is text the
# endpoint never produces, and a defect in the fetch/de-stamp path stays invisible to
# every case built on it. That is exactly how the P0 below shipped green.
_ENDPOINT_STAMP = "2026-10-28T04:05:52.3005076Z"


def _api_capture(bare: str, *, stamp: str = _ENDPOINT_STAMP) -> str:
    """`bare` as the jobs endpoint delivers it: a `<ts>Z ` stamp on every line.

    Byte-for-byte the shape captured from job `122612288295` (#10202):
    `\ufeff2026-10-28T03:45:23.2665707Z Current runner version: '2.338.0'`.
    """
    return "".join(("\ufeff" if index == 0 else "") + stamp + " " + line
                   for index, line in enumerate(bare.splitlines(keepends=True)))


# The shape matters. `ci_exemption.py signatures` derives an id's SIGNATURE from
# pytest's failure block (`E   <ExceptionType>: …`) and the `FAILED <nodeid> - <detail>`
# short summary — a capture carrying only a bare `FAILED` line yields an UNSIGNED id,
# which the decision blocks. A fixture of bare `FAILED` lines would therefore have
# "proved" only the block path and never the exemption. Verified against the real
# module: both doors report ids=1 signed=1 on this text.
_BARE_MAIN_RED_LOG = """\
tests/test_cache_projection_live.py::test_a FAILED [ 10%]

=================================== FAILURES ===================================
_______________________________ test_a ________________________________

    def test_a():
>       assert row["id"] == expected
E       KeyError: 'id'

tests/test_cache_projection_live.py:42: KeyError
=========================== short test summary info ============================
FAILED tests/test_cache_projection_live.py::test_a - KeyError: 'id'
========================= 1 failed, 99 passed in 12.34s ========================
"""

_BARE_NEW_LOG = """\
tests/test_other.py::test_brand_new FAILED [ 20%]

=================================== FAILURES ===================================
_______________________________ test_brand_new ________________________________

    def test_brand_new():
>       assert 1 == 2
E       AssertionError: this PR broke it

tests/test_other.py:11: AssertionError
=========================== short test summary info ============================
FAILED tests/test_other.py::test_brand_new - AssertionError: this PR broke it
========================= 1 failed, 50 passed in 5.00s ========================
"""

#: The decision fixtures are the TEXT THE ENDPOINT DELIVERS, not the text the runner
#: wrote: every line stamped, first line BOM-prefixed (#10202 review 4). A fixture
#: without the stamp cannot see a defect in the fetch/de-stamp path — which is how the
#: `signatures` door defect below shipped green.
MAIN_RED_LOG = _api_capture(_BARE_MAIN_RED_LOG)
NEW_LOG = _api_capture(_BARE_NEW_LOG)

#: main's baseline tables, in the shared producers' own wire formats
#: (`--main-union-rates` 3 columns, `--main-union-signatures` 2 columns). main is red
#: for `test_a` at 8/10 over a 10-run window, with the same signature the PR's log
#: yields — exactly the #9979 shape.
MAIN_RATES = "tests/test_cache_projection_live.py::test_a\t8\t10\n"
MAIN_SIGS = "tests/test_cache_projection_live.py::test_a\tKeyError: 'id'\n"


def _jobs(*rows: tuple[int, str, str]) -> str:
    """A recorded jobs listing, in the `gh api --paginate --jq '.jobs[]'` JSONL shape."""
    return "".join(json.dumps({"id": i, "name": n, "conclusion": c}) + "\n"
                   for i, n, c in rows)


_STUB = """#!/usr/bin/env bash
# A recorded stand-in for `ci-failure-set.sh` (the rail's main-side producer). The
# REAL producer calls the GitHub API; the gate's comparison logic is what is under
# test here, and its wire formats are pinned by the shared module's own parsers.
set -u
mode=""; report=""; prov=""
while [ $# -gt 0 ]; do
  case "$1" in
    --main-union-rates) mode=rates; shift 2 ;;
    --main-union-signatures) mode=sigs; shift 2 ;;
    --runs-report) report="${2:-}"; shift 2 ;;
    --provenance) prov="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$report" ] && [ "${STUB_NO_REPORT:-0}" != "1" ]; then
  printf 'examined=12\\nextracted=3\\ncompleted=12\\ntested=%s\\npending=0\\n' \\
    "${STUB_TESTED:-10}" > "$report"
fi
# The examined FAILING runs of the same window (#10289) — the leg dimension's run set.
[ -n "$prov" ] && printf '%s' "${STUB_PROV:-}" > "$prov"
[ "${STUB_RC:-0}" = "0" ] || { echo "ci-failure-set: stub failure" >&2; exit "${STUB_RC}"; }
if [ "$mode" = "rates" ]; then printf '%s' "${STUB_RATES:-}"; else printf '%s' "${STUB_SIGS:-}"; fi
"""


class Sandbox:
    """A predicate dir + recorded fixtures, so the gate runs with no network at all."""

    def __init__(self, root: Path, *, rates: str = MAIN_RATES, sigs: str = MAIN_SIGS,
                 copy_exemption: bool = True):
        self.root = root
        self.agent_infra = root / "agent-infra"
        (self.agent_infra / "scripts").mkdir(parents=True)
        self.logs = root / "logs"
        self.logs.mkdir()
        stub = (self.agent_infra / "scripts" / "ci-failure-set.sh")
        stub.write_text(_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.rates, self.sigs = rates, sigs
        if copy_exemption:
            shutil.copy2(EXEMPTION_PY, self.agent_infra / "scripts" / "ci_exemption.py")

    def env(self, **extra: str) -> dict[str, str]:
        import os
        env = dict(os.environ)
        env.update({"STUB_RATES": self.rates, "STUB_SIGS": self.sigs})
        env.update(extra)
        return env

    def run(self, *, jobs: str, logs: dict[int, str], red_legs: str = "test",
            event: str = "pull_request", main_jobs: dict[str, str] | None = None,
            **extra: str) -> subprocess.CompletedProcess:
        jobs_path = self.root / "jobs.jsonl"
        jobs_path.write_text(jobs, encoding="utf-8")
        for job_id, text in logs.items():
            (self.logs / f"{job_id}.log").write_text(text, encoding="utf-8")
        # The main window's per-run job listings (the leg dimension). Always passed, so
        # a case that reaches the probe with no fixture records fails CLOSED instead of
        # calling the live API from a test.
        main_dir = self.root / "main-jobs"
        main_dir.mkdir(exist_ok=True)
        for run_id, listing in (main_jobs or {}).items():
            (main_dir / f"{run_id}.jsonl").write_text(listing, encoding="utf-8")
        argv = [
            sys.executable, str(GATE), "evaluate",
            "--repo", "daniel-ospina/tortoise",
            "--run-id", "4242",
            "--head", "deadbeef" * 5,
            "--event", event,
            "--red-legs", red_legs,
            "--agent-infra", str(self.agent_infra),
            "--jobs-json", str(jobs_path),
            "--logs-dir", str(self.logs),
            "--main-jobs-dir", str(main_dir),
        ]
        return subprocess.run(argv, capture_output=True, text=True, timeout=120,
                              env=self.env(**extra))


# ── the gate's own structural guarantees (no rail needed) ───────────────────

def test_the_module_is_tracked_and_has_no_third_party_imports():
    """The gate job has no install step: stdlib only, or it cannot run at all."""
    source = GATE.read_text(encoding="utf-8")
    assert "import yaml" not in source and "import requests" not in source
    assert GATE.stat().st_mode & 0o111 or source.startswith("#!/usr/bin/env python3")


def test_the_verdicts_author_is_pinned_by_content():
    """#10202 review 5, issue 1: the module that AUTHORS the required check is pinned.

    A benign-looking early `return EXIT_CLEAN` in `evaluate` is invisible to every
    other guard: the workflow is unchanged, `gate_digest` projects workflows and
    settings only (`tools/mergify_config_guard.py::_gate_projection`), and the
    capstone allow-list covers the aggregate's step, not this file. Re-cut the
    constant DELIBERATELY when the module changes, and say why in the re-cut history
    above it — a stale digest must be indistinguishable from a silencing edit.
    """
    import hashlib

    assert hashlib.sha256(GATE.read_bytes()).hexdigest() == _GATE_MODULE_SHA256, (
        "`.github/scripts/new-failure-gate.py` changed without re-cutting "
        "_GATE_MODULE_SHA256 in this file. That module authors the required check's "
        "verdict (python-ci-gate delegates every deferrable failure to it), so the edit "
        "is re-cut DELIBERATELY, with a line in the re-cut history, or not at all.")


def test_the_job_slug_matches_the_shared_producer():
    """`job-unreadable::` keys must slug identically on the PR and main sides.

    `ci-failure-set.sh::run_failed_job_ids` produces main's keys with a `sed` chain
    (`s/[^A-Za-z0-9_.-]/-/g; s/--*/-/g; s/^-//; s/-$//`). If the gate slugged a name
    differently, a (job)-level failure would silently miss main's row — a fail-closed
    miss, but a wrong REPORT. So the two are pinned to each other by execution, not by
    a comment.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("new_failure_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    names = ["test (d)", "test-slow (e)", "test-carve-out (b)", "manifest-integrity",
             "test--d-", "  weird  name !! ", "a", ""]
    shell = subprocess.run(
        ["bash", "-c",
         'for one in "$@"; do printf "%s" "$one" | '
         'sed \'s/[^A-Za-z0-9_.-]/-/g; s/--*/-/g; s/^-//; s/-$//\'; echo; done', "_",
         *names],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert [module.slug_job(n) for n in names] == shell


def test_a_leg_that_never_ran_is_never_exemptible(tmp_path):
    """`cancelled` and an empty listing are UNMEASURED (exit 2), never CLEAN."""
    box = Sandbox(tmp_path, copy_exemption=False)
    cancelled = box.run(
        jobs=_jobs((1, "test (d)", "cancelled")), logs={}, red_legs="test")
    assert cancelled.returncode == 2, cancelled.stdout + cancelled.stderr
    assert "without a verdict" in cancelled.stdout

    missing_job = box.run(
        jobs=_jobs((1, "test (g)", "success")), logs={}, red_legs="test")
    assert missing_job.returncode == 2, missing_job.stdout
    assert "NO failing job in the listing" in missing_job.stdout


def test_push_runs_do_not_get_the_predicate(tmp_path):
    """On push, "no new failure relative to main" is vacuous — and main's redness is
    the baseline every PR is compared against. Refused, not answered."""
    box = Sandbox(tmp_path, copy_exemption=False)
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")),
                   logs={1: MAIN_RED_LOG}, event="push")
    assert proc.returncode == 2, proc.stdout
    assert "vacuous" in proc.stdout


def test_an_absent_shared_predicate_fails_closed(tmp_path):
    """No second implementation, ever: a missing rail is exit 2, not a local copy."""
    box = Sandbox(tmp_path, copy_exemption=False)
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    assert proc.returncode == 2, proc.stdout
    assert "shared predicate is absent" in proc.stdout


# ── the BLOCKING direction, executable with no rail at all (runs in CI) ─────
#
# ⛔ WHY THIS BLOCK EXISTS (#10202 review 5, issue 1). Every case below that reaches
# `EXIT_NEW` needs `scripts/ci_exemption.py` — the SHARED decision — and `scripts/` is a
# machine-local, untracked pointer (#9475), so on a runner those cases SKIP by name and
# the code that BLOCKS a PR is executed by NOTHING on the merge path. #9984's whole
# safety claim is "a PR-caused failure still fails the gate exactly as today", and it
# was asserted only where CI cannot see it.
#
# The remedy stubs the CALLEE, never the predicate: `_decision_stub` answers the three
# subcommands the module invokes (`ids`, `signatures`, `decide`) with FIXED answers, so
# what is under test is the module's own reading of them — its exit code and its output —
# which is the half that blocks. That is not the second implementation #9984
# constraint 7 forbids (no parsing, no signature rule, no rate comparison); it is a
# recorded stand-in in the shape `test_a_decision_that_writes_no_verdict_is_never_clean`
# already uses. These cases run UNCONDITIONALLY, so a weakening of the blocking path
# reds the `core` leg instead of going green everywhere.

def _decision_stub(*, rc: int, verdict: str | None) -> str:
    """A stand-in `ci_exemption.py`: fixed ids/signature, and a fixed `decide` answer.

    `verdict=None` means "exits `rc` without writing `--verdict-out`" — the
    "decided nothing" shape; otherwise the text is written to the flag's path.
    """
    return (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv[1:]\n"
        "sub = argv[0] if argv else ''\n"
        "def opt(n):\n"
        "    return argv[argv.index(n) + 1] if n in argv else ''\n"
        "if sub == 'ids':\n"
        "    print('tests/test_other.py::test_brand_new')\n"
        "    raise SystemExit(0)\n"
        "if sub == 'signatures':\n"
        "    print('tests/test_other.py::test_brand_new\\tAssertionError: this PR broke it')\n"
        "    raise SystemExit(0)\n"
        "if sub == 'decide':\n"
        + ("    open(opt('--verdict-out'), 'w').write(" + repr(verdict) + ")\n"
           if verdict is not None else "")
        + f"    raise SystemExit({rc})\n"
        "raise SystemExit(2)\n"
    )


def _run_against_stub(tmp_path: Path, *, rc: int, verdict: str | None
                      ) -> subprocess.CompletedProcess:
    box = Sandbox(tmp_path, copy_exemption=False)
    (box.agent_infra / "scripts" / "ci_exemption.py").write_text(
        _decision_stub(rc=rc, verdict=verdict), encoding="utf-8")
    return box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: NEW_LOG})


def test_a_shared_decision_that_blocks_makes_the_required_check_FAIL(tmp_path):
    """#9984 acceptance 2, on the merge path: `decide` rc 1 ⇒ exit 1, naming it.

    This is the one statement that makes "a PR-caused failure still fails" true
    (`new-failure-gate.py`'s `if code != 0: raise _Blocked`), and until this case
    existed a one-line `return EXIT_CLEAN` there left every CI-run test green.
    """
    proc = _run_against_stub(
        tmp_path, rc=1,
        verdict="VERDICT\tBLOCK\ttests/test_other.py::test_brand_new introduced it\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "VERDICT: NEW FAILURE" in proc.stdout
    # The decision's own words are echoed, so an appellant reads the rail's language.
    assert "BLOCK" in proc.stdout
    assert "introduced it" in proc.stdout


def test_a_blocking_verdict_is_not_undone_by_a_zero_exit_code(tmp_path):
    """Defence in depth: `decide` exiting 0 with a `BLOCK` verdict still fails.

    The module checks the exit code AND the verdict text, so weakening one of the two
    is not enough to open the hole — which is why both are pinned by execution.
    """
    proc = _run_against_stub(
        tmp_path, rc=0,
        verdict="VERDICT\tBLOCK\ttests/test_other.py::test_brand_new introduced it\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "VERDICT: NEW FAILURE" in proc.stdout


def test_a_stand_in_decision_that_writes_no_verdict_is_never_clean(tmp_path):
    """The rail's own rule, with no rail present: no `VERDICT` line ⇒ never CLEAN.

    Same stand-in, exits 0, writes nothing. The real `decide` always writes before
    returning (`--verdict-out` precedes `return 1 if gated else 0`), so this guards a
    path a future edit reaches silently rather than one reachable today.
    """
    proc = _run_against_stub(tmp_path, rc=0, verdict=None)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "VERDICT: CLEAN" not in proc.stdout
    assert "nothing was decided" in proc.stdout


# ── the decision cases: fail-closed baseline, exemption, new failure ────────

@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_a_pr_carrying_only_mains_red_PASSES_and_names_it(tmp_path):
    """#9984 acceptance 1: main's pre-existing red is not every PR's problem."""
    box = Sandbox(tmp_path)
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")),
                   logs={1: MAIN_RED_LOG})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXEMPT: tests/test_cache_projection_live.py::test_a" in proc.stdout
    assert "VERDICT: CLEAN" in proc.stdout


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_a_pr_that_introduces_one_new_failing_test_FAILS_and_names_it(tmp_path):
    """#9984 acceptance 2: a PR-caused failure still fails, exactly as today."""
    box = Sandbox(tmp_path)
    proc = box.run(jobs=_jobs((1, "test (d)", "failure"), (2, "test (g)", "failure")),
                   logs={1: MAIN_RED_LOG, 2: NEW_LOG})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BLOCK : tests/test_other.py::test_brand_new" in proc.stdout
    assert "VERDICT: NEW FAILURE" in proc.stdout


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_a_decision_that_writes_no_verdict_is_never_clean(tmp_path):
    """#9984 review: mirror the rail — no `VERDICT` line is "nothing was decided".

    `admin-merge.sh` refuses when the verdict file is absent, empty, or lacks a
    leading `VERDICT` ("the VERDICT FILE is the only thing that tells 'the gate
    produced a decision' from 'the gate crashed'"). The module used to substitute
    `VERDICT\\tCLEAN` in exactly that case, so the two doors disagreed on the one
    input that means "nothing was decided" — the gate would certify a tree the merge
    path refuses to read.

    Fail-injection: a stand-in `ci_exemption.py` that exits 0 and writes nothing. The
    real one always writes (`--verdict-out` precedes `return 1 if gated else 0`), so
    this is a guard on an unreachable-today path — which is the point: it is the path
    a future edit reaches silently.
    """
    box = Sandbox(tmp_path)
    stub = box.agent_infra / "scripts" / "ci_exemption.py"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "# exits 0 WITHOUT writing the --verdict-out file: 'decided' with no verdict\n"
        "sys.exit(0)\n",
        encoding="utf-8")
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    assert proc.returncode != 0, (
        "a shared decision that produced no VERDICT line must never be read as CLEAN — "
        f"the gate would certify a tree the rail refuses; got {proc.stdout + proc.stderr!r}")
    assert "VERDICT: CLEAN" not in proc.stdout, (
        "the gate printed CLEAN without a decision to read; "
        f"got {proc.stdout!r}")


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_the_verdict_is_auditable(tmp_path):
    """#9984 design constraint 6: print the PR set, the baseline, and every exemption.

    A gate you cannot read is a gate nobody can appeal, so the three facts an
    appellant needs — which ids failed here, what main's window measured, and which
    ids were exempted with what reason — are asserted as OUTPUT, not inferred.
    """
    box = Sandbox(tmp_path)
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    out = proc.stdout
    assert "PR failing set" in out and "tests/test_cache_projection_live.py::test_a" in out
    assert "main baseline: window" in out and "tested=10" in out
    assert "EXEMPT:" in out and "main 8/10 (80%)" in out
    assert "rejected=0" in out and "unsigned=0" in out


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_an_unreadable_baseline_fails_closed(tmp_path):
    """#9984 acceptance 3, all three unreadable shapes.

    The rail's producer writes NO `--runs-report` when its listing fails, `tested=0`
    when nothing in the window measured, and an empty table when main was never red.
    Each must exit 2 — a missing check is not a green check, and an empty baseline is
    not "no new failure either".
    """
    no_report = Sandbox(tmp_path / "a")
    proc = no_report.run(jobs=_jobs((1, "test (d)", "failure")),
                         logs={1: MAIN_RED_LOG}, STUB_NO_REPORT="1")
    assert proc.returncode == 2, proc.stdout
    assert "never MEASURED" in proc.stdout

    not_tested = Sandbox(tmp_path / "b")
    proc = not_tested.run(jobs=_jobs((1, "test (d)", "failure")),
                          logs={1: MAIN_RED_LOG}, STUB_TESTED="0")
    assert proc.returncode == 2, proc.stdout
    assert "no run that finished a measurement" in proc.stdout

    empty_table = Sandbox(tmp_path / "c", rates="", sigs="")
    proc = empty_table.run(jobs=_jobs((1, "test (d)", "failure")),
                           logs={1: MAIN_RED_LOG})
    assert proc.returncode == 2, proc.stdout
    assert "rate table is EMPTY" in proc.stdout

    producer_error = Sandbox(tmp_path / "d")
    proc = producer_error.run(jobs=_jobs((1, "test (d)", "failure")),
                              logs={1: MAIN_RED_LOG}, STUB_RC="3")
    assert proc.returncode == 2, proc.stdout
    assert "main's rate baseline" in proc.stdout


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_a_healed_main_stops_exempting(tmp_path):
    """#9984 acceptance 4 — "a quarantine entry with a closed issue stops applying".

    The exemption is the shared decision's own: main's id, an overlapping signature,
    and main's row measured over `>= min_runs` runs. So the moment main heals — the
    owning issue is fixed and main's window rolls past it — the SAME PR failure is
    absent from main and BLOCKS. Nothing has to remember to expire an entry, and
    nothing can forget to.
    """
    healed = Sandbox(tmp_path, sigs="", rates="tests/test_unrelated.py::test_c\t1\t10\n")
    proc = healed.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    assert proc.returncode == 1, proc.stdout
    assert "absent from main's failure set" in proc.stdout
    assert "VERDICT: NEW FAILURE" in proc.stdout


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_a_same_id_different_signature_is_not_exempt(tmp_path):
    """Same id on main, DIFFERENT failure: not exempt (the design's constraint 3/5).

    A quarantined red must not be able to hide a new one — and "same test name" is not
    "same failure". Here main fails `test_a` with a KeyError and the PR fails it with
    an AssertionError.
    """
    box = Sandbox(tmp_path)
    box.sigs = "tests/test_cache_projection_live.py::test_a\tAssertionError: other\n"
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    assert proc.returncode == 1, proc.stdout
    assert "signature differs from main's" in proc.stdout


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_an_unreadable_job_log_blocks_at_job_granularity(tmp_path):
    """The #7131 fallback: a leg we cannot attribute is NAMED and BLOCKED, never clean.

    Its key carries no signature, so the shared decision cannot exempt it however red
    main is — the fallback can only convert an unobservable refusal into a named block.
    """
    box = Sandbox(tmp_path)
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={})
    assert proc.returncode == 1, proc.stdout
    assert "job-unreadable::test-d" in proc.stdout
    assert "job granularity" in proc.stdout


#: A `gh` stand-in that reproduces the REAL step-log endpoint's refusal (#10202): exit
#: 1 with that exact message rather than print a log carrying terminal escape
#: sequences, unless `--allow-escape-sequences` is passed. Every other call is the job
#: listing. A recorded fixture cannot stand in for this — `--logs-dir` bypasses `gh`
#: entirely, which is exactly why the defect shipped green.
_STUB_GH = """#!/usr/bin/env bash
set -u
case "$*" in
  *--allow-escape-sequences*) cat "${STUB_GH_LOG:?}" ;;
  *"/logs") echo "the response contains terminal escape sequences; pass --allow-escape-sequences to output it anyway" >&2; exit 1 ;;
  *) printf '{"id": 1, "name": "test (d)", "conclusion": "failure"}\\n' ;;
esac
"""

#: `MAIN_RED_LOG` as `gh` actually returns it: the endpoint's `<ts>Z ` line stamp PLUS
#: the escape sequences this case is about (pytest colours). Verified against the
#: shared parser — `ci_exemption.py ids/signatures` still yield `test_a` +
#: `KeyError: 'id'` on it once the stamp is stripped — so what is under test is the
#: FETCH, not a degraded parse.
ESCAPED_RED_LOG = _api_capture(_BARE_MAIN_RED_LOG.replace(
    "FAILED tests/test_cache_projection_live.py::test_a - KeyError: 'id'",
    "\x1b[31mFAILED\x1b[0m tests/test_cache_projection_live.py::test_a - "
    "KeyError: 'id'\x1b[0m",
))


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_an_escaped_log_is_read_not_degraded_to_job_granularity(tmp_path):
    """#10202: `gh` refuses an escaped log unless asked, and that refusal is quiet.

    Measured on run 40578637580: all four failing jobs were unreadable "the response
    contains terminal escape sequences", so every one of them fell back to
    `job-unreadable::<slug>`. Those keys carry no signature and can never be exempted,
    so main's own reds blocked EVERY PR — #9984's defect wearing the fail-closed
    costume, and invisible to the fixture seam because that seam never runs `gh`.

    The gate must therefore ask for the escaped text (`--allow-escape-sequences`) and
    reach the same EXEMPT-bearing verdict a plain log does.
    """
    assert "\x1b" in ESCAPED_RED_LOG, "the fixture must carry real escape sequences"
    box = Sandbox(tmp_path)
    log = tmp_path / "gh-stdout.log"
    log.write_text(ESCAPED_RED_LOG, encoding="utf-8")
    stub = tmp_path / "gh"
    stub.write_text(_STUB_GH, encoding="utf-8")
    stub.chmod(0o755)
    argv = [
        sys.executable, str(GATE), "evaluate",
        "--repo", "daniel-ospina/tortoise",
        "--run-id", "4242",
        "--head", "deadbeef" * 5,
        "--event", "pull_request",
        "--agent-infra", str(box.agent_infra),
        "--gh", str(stub),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                          env=box.env(STUB_GH_LOG=str(log)))
    assert "log unreadable" not in proc.stdout, proc.stdout
    assert "job granularity" not in proc.stdout, proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXEMPT: tests/test_cache_projection_live.py::test_a" in proc.stdout


#: A `gh run view --log-failed` capture: the OTHER transport shape, with the
#: `<job>\t<step>\t<ts>Z ` head the shared parser attributes a failing STEP from.
_STEPPED_CAPTURE = (
    "test (d)\tRun pytest\t2026-10-28T04:05:52.3005076Z "
    "FAILED tests/test_cache_projection_live.py::test_a - KeyError: 'id'\n")


def _gate_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("new_failure_gate_stamp", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_endpoint_line_stamp_is_transport_never_content(tmp_path):
    """#10202 review 4 (P0): the jobs endpoint stamps EVERY line, and the shared
    `signatures` door cannot read a stamped record.

    Measured at head `06bde46d5`, run `40621960682`, job `122612288295`: the gate
    printed `VERDICT: UNMEASURED` and the REQUIRED check went red over three nodeids
    main was ALREADY red for (`ci-failure-set.sh --main-union-signatures` carries all
    three with identical signatures, verified). Root cause, reproduced with the pinned
    module on the bytes the job fetched: `ids` rc 0 ids=3, `signatures` rc 1 ids=0.
    The id door has a raw-log mode (`parse_failed_ids(raw_log=True)`) and mounts
    `_strip_log_prefix`, whose `_LOG_PREFIX_RE` REQUIRES a tab before the timestamp —
    so a bare `<ts>Z ` survives and `_SUMMARY_RE`'s `^FAILED` anchor never matches.

    This pins the de-stamp itself: it must restore the runner's own bytes exactly, and
    it must NOT touch the `<job>\t<step>\t` form (that head is evidence about WHICH
    STEP failed, and a strip that ate it would silently drop step attribution).
    """
    stamped = _api_capture(_BARE_MAIN_RED_LOG)
    assert stamped != _BARE_MAIN_RED_LOG, "the fixture must carry the endpoint shape"
    assert stamped.startswith("\ufeff"), "the endpoint BOMs the first line"
    unstamped_lines = [line for line in stamped.splitlines()[1:] if line.strip()]
    assert unstamped_lines and all(line.startswith(_ENDPOINT_STAMP)
                                  for line in unstamped_lines), (
        "every line — not just the first — carries the endpoint's stamp")

    module = _gate_module()
    assert module.strip_transport_stamps(stamped) == _BARE_MAIN_RED_LOG, (
        "de-stamping must restore the runner's own bytes exactly")
    assert module.strip_transport_stamps(_STEPPED_CAPTURE) == _STEPPED_CAPTURE, (
        "a `gh run view --log-failed` capture keeps its step head — the shared parser "
        "attributes the failing STEP from it")
    # And the strip is NOT a licence to keep a timestamp-shaped line: content that
    # merely begins with one (no separating space) keeps every character.
    near_miss = "2026-10-28T04:05:52.3005076Zsuffix"
    assert module.strip_transport_stamps(near_miss) == near_miss


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_a_stamped_capture_is_measurable_end_to_end(tmp_path):
    """#10202 review 4 (P0), both halves — the door refuses it, the GATE measures it.

    Half 1 is the root cause itself: the shared `signatures` door really does refuse a
    capture carrying the endpoint's stamp, so deleting the strip would put the
    required check straight back into `UNMEASURED`. Half 2 is the delivery #9984
    claims: the gate measures that SAME text and exempts main's own red.

    If half 1 ever starts passing, agent-infra has learned to read the stamp upstream
    — re-measure before removing the strip, and make this assertion the record of it.
    """
    raw = tmp_path / "stamped.log"
    raw.write_text(MAIN_RED_LOG, encoding="utf-8")
    door = subprocess.run(
        [sys.executable, str(EXEMPTION_PY), "signatures", "--log", str(raw)],
        capture_output=True, text=True, timeout=120)
    assert door.returncode != 0, (
        "the shared signature door now reads the endpoint's line stamp — the de-stamp "
        "in new-failure-gate.py may be removable, but re-measure first: "
        f"{door.stdout!r}")

    box = Sandbox(tmp_path / "box")
    proc = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXEMPT: tests/test_cache_projection_live.py::test_a" in proc.stdout
    assert "VERDICT: CLEAN" in proc.stdout


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_the_rail_and_the_gate_agree_on_the_same_fixture(tmp_path):
    """#9984 acceptance 5: ONE predicate, proven by running both over one fixture.

    The rail's own door is `ci_exemption.py decide` over `--pr-failures` / `--main-rates`
    / `--main-signatures`. The gate's door is `.github/scripts/new-failure-gate.py`.
    Both are driven here with the SAME tree — main red on `test_a`, the PR red on
    `test_a` and on a new `test_brand_new` — and their verdicts must match, case by
    case, for the exempt-only tree and for the tree with a new failure.
    """
    box = Sandbox(tmp_path)
    pr_rows = tmp_path / "pr-rows.tsv"
    pr_rows.write_text(
        "tests/test_cache_projection_live.py::test_a\t1\t1\tKeyError: 'id'\n", encoding="utf-8")
    rates = tmp_path / "rates.tsv"
    rates.write_text(MAIN_RATES, encoding="utf-8")
    sigs = tmp_path / "sigs.tsv"
    sigs.write_text(MAIN_SIGS, encoding="utf-8")
    rail = subprocess.run(
        [sys.executable, str(EXEMPTION_PY), "decide",
         "--pr-failures", str(pr_rows), "--main-rates", str(rates),
         "--main-signatures", str(sigs)],
        capture_output=True, text=True, timeout=120,
    )
    gate = box.run(jobs=_jobs((1, "test (d)", "failure")), logs={1: MAIN_RED_LOG})
    assert rail.returncode == 0 and gate.returncode == 0, (rail.stdout, gate.stdout)
    assert "VERDICT\tCLEAN" in rail.stdout and "VERDICT: CLEAN" in gate.stdout

    pr_rows.write_text(
        pr_rows.read_text(encoding="utf-8")
        + "tests/test_other.py::test_brand_new\t1\t1\tAssertionError: this PR broke it\n",
        encoding="utf-8")
    rail = subprocess.run(
        [sys.executable, str(EXEMPTION_PY), "decide",
         "--pr-failures", str(pr_rows), "--main-rates", str(rates),
         "--main-signatures", str(sigs)],
        capture_output=True, text=True, timeout=120,
    )
    gate = box.run(jobs=_jobs((1, "test (d)", "failure"), (2, "test (g)", "failure")),
                   logs={1: MAIN_RED_LOG, 2: NEW_LOG})
    assert rail.returncode == 1 and gate.returncode == 1, (rail.stdout, gate.stdout)
    assert "tests/test_other.py::test_brand_new" in rail.stdout
    assert "tests/test_other.py::test_brand_new" in gate.stdout

# ── the wiring contract: the verdict job and the aggregate cannot drift ─────

def test_the_deferrable_arms_are_exactly_the_verdict_jobs_needs():
    """One list, two readers — pinned against each other.

    The aggregate defers a failed arm to `ci-introduced-failure`; the verdict job only
    adjudicates arms it WAITED for. If the two lists drift:
      * a leg in the aggregate's set but not in the job's `needs:` is excused by a
        verdict that never looked at it — a FALSE PASS on the required check;
      * a leg in the job's `needs:` but not in the aggregate's set is waited for and
        never excused — the verdict becomes a no-op.
    Both are silent, so the equality is asserted here rather than trusted.
    """
    import re

    import yaml

    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "python-ci.yml")
                              .read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    gate = jobs["python-ci-gate"]
    verdict = jobs["ci-introduced-failure"]

    assert "ci-introduced-failure" in (gate.get("needs") or []), (
        "the required check must consume the verdict as a need, or the predicate is "
        "asked by nothing")
    script = "\n".join(str(s.get("run") or "") for s in gate["steps"])
    found = re.search(r'^\s*DEFERRABLE_LEGS="([^"]+)"', script, re.MULTILINE)
    assert found, "the aggregate must declare DEFERRABLE_LEGS (#9984)"
    deferrable = set(found.group(1).split())
    assert deferrable == set(verdict["needs"]), (
        "the arms the aggregate excuses must be exactly the arms the verdict job waits "
        f"for; aggregate={sorted(deferrable)} verdict-needs={sorted(verdict['needs'])}")
    assert deferrable <= set(gate.get("needs") or []), (
        "a deferrable arm must also be a leg of the required check — otherwise the "
        "aggregate excuses something it does not observe")
    # The drift gate is deliberately NOT on the verdict job's path (#2656): a drift
    # failing must SKIP nothing on the way to the test matrix.
    assert "manifest-integrity" not in (verdict.get("needs") or []), (
        "the verdict job must not need `manifest-integrity`, or a drift would skip it "
        "instead of failing it (#2656)")
    # A `cancelled` verdict certifies nothing; the aggregate must never treat it as a
    # pass (the harness in tests/test_ci_selection.py asserts the rendered behaviour).
    assert verdict.get("if") == "always()", (
        "the verdict job must be `if: always()`, or it is SKIPPED on exactly the red "
        "runs it exists for")


def test_the_workflow_hands_the_predicate_the_red_legs():
    """#9984 review: the fail-closed net is only real if the caller turns it on.

    `--red-legs` is the ONE argument that enables `failing_jobs()`'s "a red leg with
    no failing job in the listing is unobservable" refusal, and it is the ONE flag
    with a default (`""`) instead of `required=True` — so its omission is silent and
    fail-OPEN, unlike every other argument. With it empty, every job in the run is in
    scope and a delegated leg whose failed job is missing from the listing (or ends
    on a conclusion this module does not map) leaves `failed == []`, so `evaluate`
    takes its "nothing failed → CLEAN" branch and the required check certifies a red
    delegated leg.

    The set is DERIVED from `toJSON(needs)` — the same `needs` this job already
    waited on — so it is not a second copy of the aggregate's leg table and cannot
    drift from it. A literal leg list here would be the drift the module's own
    docstring warns about, so the test refuses that shape too.
    """
    import yaml

    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "python-ci.yml")
                              .read_text(encoding="utf-8"))
    verdict = workflow["jobs"]["ci-introduced-failure"]
    steps = [s for s in (verdict.get("steps") or [])
             if "new-failure-gate.py" in str(s.get("run") or "")]
    assert len(steps) == 1, f"expected ONE predicate step, found {len(steps)}"
    step = steps[0]
    run = str(step.get("run") or "")
    assert "--red-legs" in run, (
        "the predicate must be invoked with `--red-legs`; without it the module's "
        "unobservable-red-leg refusal never runs and a delegated red leg can be "
        "certified CLEAN (#9984 review)")
    needs_env = str((step.get("env") or {}).get("NEEDS_JSON") or "")
    assert "toJSON(needs)" in needs_env.replace(" ", ""), (
        "`--red-legs` must be DERIVED from `toJSON(needs)`; a hand-kept leg list is a "
        f"second copy of the aggregate's table and will drift; got {needs_env!r}")
    # No literal leg list smuggled into the step: every leg the aggregate declares
    # must come from `needs`, not from a hard-coded word.
    assert "DEFERRABLE_LEGS" not in run, (
        "the verdict step must not carry its own leg list — derive it from needs")


def test_the_verdict_step_is_gated_on_every_class_the_aggregate_delegates():
    """#9984 review: a delegated class with NO adjudicator is a false PASS.

    The aggregate delegates `failure` AND `timed_out` for a deferrable leg, and it
    reads the verdict JOB's result as the certification. `contains()` over
    `needs.*.result` is exact-element membership, so a step gate naming only `failure`
    leaves a `timed_out` delegation with a job that still RUNS (`always()`), SKIPS its
    predicate step, and concludes `success` because every remaining step passed. The
    aggregate then prints "certified every failing id" and the required check goes
    GREEN on a PR whose shard timed out — with nothing compared against main.

    Measured pre-fix on the rendered aggregate (`test=timed_out`,
    `ci-introduced-failure=success`): rc=0, leg reported as delegated. The module was
    already correct (`_FAILED_CONCLUSIONS` holds `timed_out`); the hole was purely the
    step gate, which is why the two halves are pinned to each other here.
    """
    import yaml

    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "python-ci.yml")
                              .read_text(encoding="utf-8"))
    aggregate = "\n".join(str(s.get("run") or "")
                          for s in workflow["jobs"]["python-ci-gate"]["steps"])
    verdict = workflow["jobs"]["ci-introduced-failure"]

    # The classes the aggregate delegates, read from its own `case` arm: that arm is the
    # authority, and it is ONE arm so the two halves cannot drift apart silently.
    assert "failure|timed_out)" in aggregate, (
        "the aggregate must delegate `failure|timed_out` test arms in one case arm; if "
        "that set changed, this pin and the verdict step's `if:` must be re-cut "
        "together — a class delegated with no adjudicating step is a false PASS")
    classes = ["failure", "timed_out"]

    steps = [s for s in (verdict.get("steps") or [])
             if "new-failure-gate.py" in str(s.get("run") or "")]
    assert len(steps) == 1, (
        f"the verdict job must run the predicate in exactly ONE step, found {len(steps)}"
        " — a second step makes the one that actually adjudicates unidentifiable")
    gate_if = str(steps[0].get("if") or "")
    assert "always()" in gate_if, (
        "the predicate step must be `always()`-guarded or the default `success()` "
        "skips it on exactly the red runs it exists for; got "
        f"{gate_if!r}")
    missing = [c for c in classes if f"'{c}'" not in gate_if]
    assert not missing, (
        f"the predicate step's `if:` must name EVERY class the aggregate delegates; "
        f"missing {missing} — a `{missing[0]}` leg would be delegated to a step that "
        "never runs, and this job's `success` would certify it (#9984 review); got "
        f"{gate_if!r}")


def test_the_verdict_job_is_pinned_and_bounded():
    """The two properties that keep this job from becoming a new single point of red."""
    import yaml

    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "python-ci.yml")
                              .read_text(encoding="utf-8"))
    job = workflow["jobs"]["ci-introduced-failure"]
    pinned = [s for s in job["steps"]
              if (s.get("with") or {}).get("repository") == "daniel-ospina/agent-infra"]
    assert len(pinned) == 1, "the shared predicate must be checked out exactly once"
    ref = (pinned[0]["with"] or {}).get("ref") or ""
    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
        f"the agent-infra checkout must be pinned to a full commit sha (got {ref!r}): "
        "unpinned, this job would track another repo's main on the surface every PR's "
        "merge depends on")
    assert isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] <= 15, (
        "the verdict job feeds the REQUIRED aggregate: an unbounded job would hold "
        "python-ci-gate pending on every PR")
    assert set(job.get("permissions") or {}) == {"actions", "contents", "pull-requests"}, (
        "the job's token must be the three reads it needs, stated explicitly")


# ── the LEG dimension: did main ever EXECUTE the leg this run failed in? (#10289) ──
#
# `tested` is a RUN-level counter (`ci-failure-set.sh`: "FINISHED IS NOT TESTED"). A
# window whose runs each had `test-slow` SKIPPED — the shape a failing `changes` job
# produces, every dependent job skipped behind it — therefore reads `tested=3,
# measured`, and a PR failure from that leg was reported as NEW: "introduced by this
# PR" about a failure nothing on main ever measured. These cases assert the GATE'S
# VERDICT (its exit code and its words), never an internal helper.

#: main's window: three examined FAILING runs, each with `changes` red and every test
#: leg SKIPPED behind it. The suite never ran in this window at all.
_MAIN_WINDOW_LEG_SKIPPED = {
    run_id: _jobs((1, "changes", "failure"), (2, "test-slow (d)", "skipped"))
    for run_id in ("1001", "1002", "1003")
}

#: The same window with the leg `neutral` on main — one of the non-executing
#: conclusions. It exists so the refusal's own wording is pinned to the code: the EXECUTED
#: set it names is generated from `_EXECUTED_CONCLUSIONS`, and the non-executing states it
#: lists are ILLUSTRATIVE. A message that enumerates a few states as if that were the
#: complement states a fact narrower than what the code did (#10564 review).
_MAIN_WINDOW_LEG_NEUTRAL = {
    run_id: _jobs((1, "changes", "success"), (2, "test-slow (d)", "neutral"))
    for run_id in ("1001", "1002", "1003")
}

#: The same window with the leg EXECUTED (and clean) — the control: a leg that main
#: measured must still let a genuinely new PR failure block.
_MAIN_WINDOW_LEG_EXECUTED = {
    run_id: _jobs((1, "changes", "success"), (2, "test-slow (d)", "success"))
    for run_id in ("1001", "1002", "1003")
}

#: The window's counters and its examined failing runs. `tested=3` EQUALS the examined
#: count on purpose: `tested > len(examined)` is the proof that a GREEN run exists, and a
#: green run covers every leg — so an all-red window is the only shape that probes.
_STUB_PROV = {"STUB_PROV": "aaa:1001\nbbb:1002\nccc:1003\n",
              "STUB_TESTED": "3", "STUB_EXAMINED": "3"}


def _leg_case(tmp_path: Path, *, main_jobs: dict[str, str]):
    box = Sandbox(tmp_path, copy_exemption=False)
    (box.agent_infra / "scripts" / "ci_exemption.py").write_text(
        _decision_stub(rc=1, verdict="VERDICT\tBLOCK\ttest_brand_new introduced it\n"),
        encoding="utf-8")
    return box.run(jobs=_jobs((900, "test-slow (d)", "failure")), logs={900: NEW_LOG},
                   red_legs="test-slow", main_jobs=main_jobs, **_STUB_PROV)


def test_a_leg_main_never_executed_is_Unmeasured_not_introduced(tmp_path):
    """The verdict that was WRONG before this change: it read `introduced` (exit 1).

    ⚠️ Read the pre-change delta exactly: the old module has no `--main-jobs-dir` flag, so
    re-running THIS test against it exits 2 from argparse, not from the leg dimension. The
    behavioural delta was reproduced by running the pre-change blob WITHOUT the seam flag
    on the same fixture — `rc=1, VERDICT: NEW FAILURE … this PR introduced it` — where
    this fixture now yields exit 2 naming the leg. The assertion below therefore pins the
    POST-change verdict; the pre-change one is reproduced out of band and in
    `/tmp/repro-unmeasured.py`'s transcript, not claimed by this test.

    The window is measured (`tested=3`) and its rate table is non-empty, so every
    pre-existing refusal passed — and the shared decision, correctly, found no main row
    for the PR's id. What was missing is the question this asserts: did the leg the
    failure came from ever EXECUTE on main? It did not. The honest verdict is
    UNMEASURED, which blocks too, but says "nothing compared against this leg" instead
    of emitting a confident, wrong attribution.
    """
    proc = _leg_case(tmp_path, main_jobs=_MAIN_WINDOW_LEG_SKIPPED)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "VERDICT: UNMEASURED" in proc.stdout
    assert "leg coverage: test-slow=NOT EXECUTED" in proc.stdout
    assert "NEW FAILURE" not in proc.stdout
    assert "'test-slow'" in proc.stdout, "the refusal must NAME the unmeasured leg"
    # ⛔ THE REFUSAL STATES ONLY WHAT IT READ (#10564 review, ISSUE 2). The probe scans
    # the EXAMINED (failing) runs; `tested <= len(examined)` is sufficient-but-not-
    # necessary for a green run, so a `success` run may exist in this window unprobed.
    # The words must therefore scope to the run set that was actually read — the
    # unscoped form ("the window holds no run in which the leg EXECUTED") is a claim
    # about runs nothing measured, the same class as the absent-provenance defect.
    assert "no EXAMINED run of main's" in proc.stdout, proc.stdout
    assert "window holds no" not in proc.stdout, (
        "the refusal must not claim the whole window; only the examined runs were read")


def test_the_refusal_names_the_conclusions_it_actually_treats_as_not_executed(tmp_path):
    """The refusal names the EXECUTED set (generated), and marks its examples as examples.

    A `neutral` (or `stale`, `startup_failure`) leg on main is `NOT EXECUTED` — the set
    the code uses is `{success, failure, timed_out}` — so a refusal that enumerates only
    some of the others describes a narrower thing than it did. The generated clause is
    the fix for drift; the `e.g.`-marked list is illustrative on purpose, because
    GitHub can add a conclusion and that must not make the message wrong.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("new_failure_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    proc = _leg_case(tmp_path, main_jobs=_MAIN_WINDOW_LEG_NEUTRAL)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "leg coverage: test-slow=NOT EXECUTED" in proc.stdout
    # The EXECUTED set is GENERATED from the constant, so the words cannot drift from
    # the code: a message naming a stale subset fails here the moment the constant moves.
    executed = ", ".join(sorted(module._EXECUTED_CONCLUSIONS))
    assert f"something other than {executed}" in proc.stdout, (
        "the refusal must name the conclusions that DO count as executed, generated "
        f"from `_EXECUTED_CONCLUSIONS` ({executed}); got: {proc.stdout!r}")
    # The named states are EXAMPLES of that complement, not a completeness claim — so
    # asserting them cannot certify a list as exhaustive the way the previous wording
    # did (#10564 review, F1). `action_required` is a real conclusion in this repo
    # (`tools/ci_verdict.py:NEGATIVE_CONCLUSIONS`).
    for state in ("skipped", "cancelled", "neutral", "stale", "startup_failure",
                  "action_required"):
        assert state in proc.stdout, f"{state!r} missing from the refusal: {proc.stdout!r}"
    # ...and the list must be MARKED as illustrative, or it reads as the exhaustive
    # enumeration F1 was raised about (a mutation that drops the marker passed this test
    # until this assertion existed — the docstring claimed the marker was checked).
    assert "ILLUSTRATIVE, not exhaustive" in proc.stdout, (
        f"the example list must be marked illustrative, not exhaustive: {proc.stdout!r}")
    assert "e.g. " in proc.stdout, (
        f"the state list must be introduced as examples: {proc.stdout!r}")
    assert "the complement of" not in proc.stdout, (
        "the message must not CERTIFY its example list as complete: the gate reads only "
        "the conclusions it observes, and `absent` is not a conclusion at all")


def test_a_leg_main_did_execute_still_blocks_a_genuinely_new_failure(tmp_path):
    """The control that keeps the change from becoming a blanket refusal.

    Same fixture, same BLOCK from the shared decision — only main's window differs: the
    leg ran. A gate that answered UNMEASURED here would be the over-block the module's
    own docstring refuses ("a gate that refuses everything is not a gate").
    """
    proc = _leg_case(tmp_path, main_jobs=_MAIN_WINDOW_LEG_EXECUTED)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "VERDICT: NEW FAILURE" in proc.stdout
    assert "leg coverage: test-slow=executed (probed)" in proc.stdout


def test_a_green_run_in_the_window_covers_the_leg_without_a_jobs_call(tmp_path):
    """A `success` python-ci run IS leg evidence — no extra API call, and no probe.

    A `skipped` leg the selector SELECTED fails closed in `python-ci-gate`'s own leg
    table (#5219), so a green python-ci run means every selected leg executed. `tested >
    len(examined)` proves one exists in the window (`tested` = success|failure|timed_out,
    `examined` = failure|timed_out|startup_failure), so the no-fixture `main-jobs` dir
    must never be consulted — asserted by the run NOT failing closed on a missing
    listing while `tested` exceeds the examined count.
    """
    box = Sandbox(tmp_path, copy_exemption=False)
    (box.agent_infra / "scripts" / "ci_exemption.py").write_text(
        _decision_stub(rc=1, verdict="VERDICT\tBLOCK\ttest_brand_new introduced it\n"),
        encoding="utf-8")
    proc = box.run(jobs=_jobs((900, "test-slow (d)", "failure")), logs={900: NEW_LOG},
                   red_legs="test-slow", main_jobs={},
                   STUB_TESTED="4", STUB_EXAMINED="3",
                   STUB_PROV=_STUB_PROV["STUB_PROV"])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "leg coverage: test-slow=executed (green-run)" in proc.stdout


def test_a_missing_main_job_listing_is_unmeasured_never_unexecuted(tmp_path):
    """A missing listing is not "the run had no jobs" — it refuses (exit 2).

    Without this, the leg dimension could read a fixture gap as "the leg never ran" and
    manufacture a refusal from a recording bug rather than from a measurement.
    """
    proc = _leg_case(tmp_path, main_jobs={})          # provenance names runs, no listings
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "no recorded job listing for run" in proc.stdout
