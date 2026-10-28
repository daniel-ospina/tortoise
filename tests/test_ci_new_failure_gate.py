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
    "asserting against a copy. The gate itself is exercised with the real module in CI, "
    "where the pinned agent-infra checkout is present."
)

# ── fixtures: a realistic capture, not a synthetic one ──────────────────────
#
# The shape matters. `ci_exemption.py signatures` derives an id's SIGNATURE from
# pytest's failure block (`E   <ExceptionType>: …`) and the `FAILED <nodeid> - <detail>`
# short summary — a capture carrying only a bare `FAILED` line yields an UNSIGNED id,
# which the decision blocks. A fixture of bare `FAILED` lines would therefore have
# "proved" only the block path and never the exemption. Verified against the real
# module: both doors report ids=1 signed=1 on this text.

MAIN_RED_LOG = """\
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

NEW_LOG = """\
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
mode=""; report=""
while [ $# -gt 0 ]; do
  case "$1" in
    --main-union-rates) mode=rates; shift 2 ;;
    --main-union-signatures) mode=sigs; shift 2 ;;
    --runs-report) report="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$report" ] && [ "${STUB_NO_REPORT:-0}" != "1" ]; then
  printf 'examined=12\\nextracted=3\\ncompleted=12\\ntested=%s\\npending=0\\n' \\
    "${STUB_TESTED:-10}" > "$report"
fi
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
            event: str = "pull_request", **extra: str) -> subprocess.CompletedProcess:
        jobs_path = self.root / "jobs.jsonl"
        jobs_path.write_text(jobs, encoding="utf-8")
        for job_id, text in logs.items():
            (self.logs / f"{job_id}.log").write_text(text, encoding="utf-8")
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
        ]
        return subprocess.run(argv, capture_output=True, text=True, timeout=120,
                              env=self.env(**extra))


# ── the gate's own structural guarantees (no rail needed) ───────────────────

def test_the_module_is_tracked_and_has_no_third_party_imports():
    """The gate job has no install step: stdlib only, or it cannot run at all."""
    source = GATE.read_text(encoding="utf-8")
    assert "import yaml" not in source and "import requests" not in source
    assert GATE.stat().st_mode & 0o111 or source.startswith("#!/usr/bin/env python3")


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

#: `MAIN_RED_LOG` as `gh` actually returns it: the step log carries the escape
#: sequences this case is about (pytest colours). Verified against the shared parser —
#: `ci_exemption.py ids/signatures` still yield `test_a` + `KeyError: 'id'` on it — so
#: what is under test is the FETCH, not a degraded parse.
ESCAPED_RED_LOG = MAIN_RED_LOG.replace(
    "FAILED tests/test_cache_projection_live.py::test_a - KeyError: 'id'",
    "\x1b[31mFAILED\x1b[0m tests/test_cache_projection_live.py::test_a - "
    "KeyError: 'id'\x1b[0m",
)


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
