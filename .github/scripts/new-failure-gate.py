#!/usr/bin/env python3
"""new-failure-gate.py — the REQUIRED check's predicate: did THIS PR introduce a failure?

#9984. `python-ci-gate` is a single required check that aggregates every shard, and
its historical predicate was "does anything fail anywhere?". Because the check is an
aggregate, ONE pre-existing trunk red made the required check red for EVERY PR in the
repo: measured 2026-10-25, `main` was green in 26 of its 27 Python CI jobs, the one
red (#9979, four tests in `tests/test_cache_projection_live.py`) red'd `python-ci-gate`
for all 11 open PRs, including PRs touching nothing near that shard, and the repo-wide
merge rate went to zero for ~8 hours with the machines idle.

The rail already computes the right predicate. `scripts/admin-merge.sh` exempts a PR
failure that main itself is red for — with the SAME id, an overlapping failure
SIGNATURE and a comparable RATE (`scripts/ci-failure-set.sh` →
`scripts/ci_exemption.py decide`, #3756). The gate asked a different question about the
same tree, and the gate was the one with the wrong predicate.

So this module does NOT implement a predicate. It assembles the same two inputs the
rail feeds (`the PR's failing ids+signatures`, `main's rate+signature tables`) and
hands them to the SAME decision function, then reports its verdict. ONE implementation,
TWO consumers — the alternative (a second copy of "NEW failure" in the gate) drifts,
and the drift is discovered at 3 a.m. by a lane (#9984 constraint 7).

WHERE THE TWO SIDES COME FROM
  PR side    — the failing JOBS of THIS workflow run, and their own step logs via the
               Actions jobs API. That text is NOT byte-identical in shape to
               `gh run view --log-failed`: it carries a bare `<ts>Z ` stamp on every
               line and no `<job>\t<step>\t` head, so this module strips the stamp
               before the parser sees it (`strip_transport_stamps`; measured failure
               of the un-stripped form in #10202 review 4). Terminal escape sequences
               ARE preserved — and `gh` refuses to print them without
               `--allow-escape-sequences` (see `job_log`; #10202). `runner.temp`
               artifacts were the alternative and are rejected: only the pytest legs
               upload one, and a failure in a guard step (an orphan-count assert, a
               packaging guard) has no artifact at all.
  main side  — `ci-failure-set.sh --main-union-rates/--main-union-signatures`, the
               widened window the rail uses. NOT a per-name rollup, and NOT a single
               run: a test that fails on main only in some runs is still main's red.

FAIL CLOSED, IN BOTH DIRECTIONS (the whole safety of the change)
  Every exit is one of three codes and only the FIRST is a pass:
    0 CLEAN       — every failing id is main's own failure ($EXIT_CLEAN)
    1 NEW         — at least one failure is not main's (names it) ($EXIT_NEW)
    2 UNMEASURED  — anything we could not read ($EXIT_UNMEASURED)
  A missing check is not a green check: an unreadable baseline, an unreadable job log,
  an empty id set on a red leg, a missing shared module, a listing that names no failing
  job for a red leg — each is 2, never 0. "Unknown" is not "no new failure".

  A failure whose log yields NO identity becomes `job-unreadable::<slug>` — the same
  key `ci-failure-set.sh::run_failed_job_ids` emits (#7131), carrying no signature, so
  the shared decision BLOCKS it by construction. Attribution at JOB granularity says so
  out loud instead of pretending the leg was clean.

WHAT THIS DOES NOT DO
  * It does not decide that a red leg is green. The leg table in `python-ci-gate`
    still owns the classes it always owned, and they are NOT exemptible: a `cancelled`
    leg, a `skipped` leg the selector selected, and a leg with no result each fail
    closed BEFORE this module is consulted (#5219 — a lost shard is not a pass). Only
    `failure`/`timed_out` reach here, i.e. only "a test/step failed", never "the leg
    did not run".
  * It does not run on push-to-main. There, "no new failure relative to main" is
    vacuously true, and the required check is the one surface that still reports main's
    own redness — the baseline every PR is compared against. The workflow keeps the
    push arm on the old predicate deliberately.
  * It never mints its own verdict vocabulary: the pass/fail words are
    `ci_exemption.py decide`'s own (`CLEAN`/`BLOCK`, per-id reasons, visible
    exemptions), so an operator appealing a verdict reads the rail's language.

USAGE (the workflow's only invocation)
  python3 .github/scripts/new-failure-gate.py evaluate \
      --repo "$REPO" --run-id "$RUN_ID" --head "$HEAD" --event "$EVENT" \
      --red-legs "test test-slow" \
      --agent-infra .ci-predicate

  Test injection (no network, no `gh`): `--jobs-json FILE` (the jobs listing, JSON or
  JSONL) and `--logs-dir DIR` holding `<job_id>.log` files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_CLEAN = 0
EXIT_NEW = 1
EXIT_UNMEASURED = 2

# A matrix leg's job name carries its label: `test (d)`, `test-slow (e)`,
# `test-carve-out (b)`. The LEGS table names the JOB ID (`test`), which is the base.
_MATRIX_SUFFIX = re.compile(r"\s*\([^()]*\)\s*$")

# The conclusions that mean "something failed" — the only ones this module may ever
# exempt. `cancelled`/`startup_failure` are NOT here: a leg that never ran certified
# nothing, so it is unmeasurable (see `--red-legs` handling in evaluate()).
_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out"})

# `ci-failure-set.sh` reads these three too; a leg that ended this way has no set.
_UNCERTIFIABLE_CONCLUSIONS = frozenset({"cancelled", "startup_failure", "stale"})

# A leg that EXECUTED: its job produced a VERDICT. `skipped`, `cancelled`, `stale`,
# `neutral` and an absent job all mean the leg did not execute, so it measured nothing
# (`timed_out` is a KILLED leg whose failure set IS read, so it counts — the membership is
# the producer's `tested` arm byte for byte, which is what makes the counting argument in
# `main_leg_coverage` hold). MEASUREDNESS, not redness — `cancelled`/`stale` stay NON-RED
# (`_UNCERTIFIABLE_CONCLUSIONS` above, a recorded decision): a leg that never ran is not a
# failure, and it is not a measurement either. Two different questions, and this set only
# ever answers the second; no recorded polarity is flipped here.
_EXECUTED_CONCLUSIONS = frozenset({"success", "failure", "timed_out"})


class Unmeasured(Exception):
    """Anything we could not read. Always exit 2, never 0 (#9984 constraint 1)."""


#: The Actions jobs-log endpoint's OWN per-line stamp: a bare ISO-8601 instant and a
#: space, on EVERY line — the first one preceded by a BOM:
#:
#:     \ufeff2026-10-28T03:45:23.2665707Z Current runner version: '2.338.0'
#:     2026-10-28T04:05:52.3005076Z FAILED tests/x.py::test_y - AssertionError: …
#:
#: It is TRANSPORT, not content — the runner's own log does not carry it, and the
#: shared parser strips the OTHER transport form (the `<job>\t<step>\t<ts>Z ` head
#: `gh run view --log-failed` renders) through `ci_exemption.py::_LOG_PREFIX_RE`,
#: which REQUIRES a tab before the timestamp. A bare stamp therefore survives, so
#: `_SUMMARY_RE`'s `^FAILED` anchor never matches and `ci_exemption.py signatures`
#: exits 1 with `ids=0` on a capture whose `ids` door finds its ids. Measured
#: (#10202 review 4, job 122612288295): `ids` rc 0 ids=3, `signatures` rc 1 ids=0 →
#: this module's `Unmeasured` → the REQUIRED check red over a leg main was already
#: red for, i.e. #9984's whole defect wearing the fail-closed costume.
#:
#: Removing the stamp restores the bytes the runner wrote. It does NOT synthesize the
#: `<job>\t<step>\t` head — the scoping record rejects that (alternative 4), because
#: `_split_log_line` treats that head as evidence about WHICH STEP failed. A line
#: with no head is SKIPPED by `guard_step_failures`, so a de-stamped capture can
#: neither manufacture a step nor attribute an annotation from a passing one.
#:
#: `[ \t]` is REQUIRED, not optional: a content line that merely BEGINS with a
#: timestamp-shaped token (a test echoing one) keeps its text rather than losing a
#: character to a strip that was not a stamp.
_API_LINE_STAMP = re.compile(
    r"^\ufeff?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z[ \t]", re.MULTILINE
)


def strip_transport_stamps(text: str) -> str:
    """Drop the jobs-endpoint's bare `<ts>Z ` stamp from every line.

    The `gh run view --log-failed` 3-column form is left ALONE (its line starts with
    the job name, so the anchor cannot match): the shared parser already reads it.
    """
    return _API_LINE_STAMP.sub("", text)


def leg_base(job_name: str) -> str:
    """`test (d)` -> `test`. The LEGS table's names are job ids, not matrix legs."""
    return _MATRIX_SUFFIX.sub("", (job_name or "").strip())


def slug_job(job_name: str) -> str:
    """The `job-unreadable::` slug, byte-identical to `run_failed_job_ids` (#7131).

    That function is a `sed` chain —
    ``s/[^A-Za-z0-9_.-]/-/g; s/--*/-/g; s/^-//; s/-$//`` — and a PR and main must slug
    the same job name to the SAME key or the comparison silently misses (which blocks,
    i.e. fails closed, but reports the wrong thing). Pinned against the shell by
    `tests/test_ci_new_failure_gate.py::test_the_job_slug_matches_the_shared_producer`.
    """
    slug = re.sub(r"[^A-Za-z0-9_.-]", "-", job_name or "")
    slug = re.sub(r"--+", "-", slug)
    if slug.startswith("-"):
        slug = slug[1:]
    if slug.endswith("-"):
        slug = slug[:-1]
    return slug


# ── the two shared scripts this module drives (never reimplements) ────────────

def resolve_predicate(agent_infra: str) -> tuple[Path, Path]:
    """(ci-failure-set.sh, ci_exemption.py) from an agent-infra checkout, or refuse.

    Refusing rather than degrading is the point: the gate must never grow a second
    implementation of the predicate, so "the shared module is missing" is UNMEASURED
    (exit 2), not "carry on with a local copy".
    """
    root = Path(agent_infra) / "scripts"
    failure_set = root / "ci-failure-set.sh"
    exemption = root / "ci_exemption.py"
    missing = [str(p) for p in (failure_set, exemption) if not p.is_file()]
    if missing:
        raise Unmeasured(
            "the shared predicate is absent — the required check cannot ask whether "
            "this PR introduced a failure without a SECOND implementation of it "
            "(#9984 constraint 7). Missing: " + ", ".join(missing)
        )
    return failure_set, exemption


class Runner:
    """`gh` + `python3` invocation, with a recorded-fixture seam for the tests."""

    def __init__(self, *, gh: str, repo: str, token: str | None):
        self.gh = gh
        self.repo = repo
        self.env = dict(os.environ)
        if token:
            self.env["GH_TOKEN"] = token
        self.env.setdefault("GH_HOST", "github.com")

    def _run(self, argv: list[str], *, what: str) -> str:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, env=self.env, timeout=300
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unmeasured(f"{what}: could not run {argv[0]!r} ({exc})") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise Unmeasured(
                f"{what}: {argv[0]} exited {proc.returncode}"
                + (f" — {detail[-1]}" if detail else "")
            )
        return proc.stdout

    def jobs(self, run_id: str, jobs_json: str | None) -> list[dict]:
        """The run's job list. Injected from a recorded fixture in the tests.

        `jobs_json` may be a FILE (the PR run's listing, one `gh` call) or a DIRECTORY
        holding `<run_id>.jsonl` (the main-window listings the leg dimension reads, one
        file per run). A directory with no file for `run_id` is UNMEASURED, never an
        empty job list: "we hold no listing" is not "the run had no jobs", and the leg
        coverage a caller would derive from it would silently read as unexecuted.
        """
        if jobs_json:
            path = Path(jobs_json)
            if path.is_dir():
                path = path / f"{run_id}.jsonl"
            if not path.is_file():
                raise Unmeasured(
                    f"no recorded job listing for run {run_id} at {path} — a listing "
                    "we do not hold is not a run that had no jobs")
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise Unmeasured(
                    f"the recorded job listing for run {run_id} is unreadable "
                    f"({exc}) — never an empty listing") from exc
        else:
            raw = self._run(
                [
                    self.gh, "api",
                    f"repos/{self.repo}/actions/runs/{run_id}/jobs?per_page=100",
                    "--paginate", "--jq", ".jobs[]",
                ],
                what=f"job listing for run {run_id}",
            )
        # `--paginate --jq '.jobs[]'` prints one JSON object PER LINE across pages; a
        # recorded fixture may be either those lines or one `{"jobs": [...]}` document.
        text = raw.strip()
        if not text:
            return []
        if text.startswith("{") and '"jobs"' in text.split("\n")[0]:
            try:
                return list(json.loads(text).get("jobs") or [])
            except json.JSONDecodeError:
                pass
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise Unmeasured(
                    f"job listing for run {run_id}: unparsable jobs JSON ({exc})"
                ) from exc
        return out

    def job_log(self, job_id: int, logs_dir: str | None) -> str:
        """One job's log, de-stamped, in the shape the shared parser reads."""
        if logs_dir:
            path = Path(logs_dir) / f"{job_id}.log"
            if not path.is_file():
                raise Unmeasured(f"no recorded log at {path}")
            return strip_transport_stamps(
                path.read_text(encoding="utf-8", errors="replace"))
        # `--allow-escape-sequences` is LOAD-BEARING, not cosmetic (#10202). `gh`
        # exits 1 rather than print a response carrying terminal escape sequences
        # unless it is given this flag, and a pytest/guard step log essentially always
        # carries them (pytest colours, and `##[group]` echoes of the workflow's own
        # source). Without it EVERY failing job fell back to `job-unreadable::<slug>` —
        # which carries no signature and can never be exempted — so the gate blocked
        # every PR on main's own reds: the exact failure #9984 exists to remove,
        # reintroduced as "fail closed". Measured on run 40578637580, where all four
        # failing jobs were unreadable for this reason alone.
        #
        # Reading them is safe: the escaped text is what `gh run view --log-failed`
        # emits, and the shared parser strips ANSI SGR itself (verified against
        # `ci_exemption.py ids/signatures` on an SGR-wrapped capture). A gate that
        # could not read an escaped log would still refuse rather than pass — but a
        # gate that refuses everything is not a gate.
        return strip_transport_stamps(self._run(
            [self.gh, "api", f"repos/{self.repo}/actions/jobs/{job_id}/logs",
             "--allow-escape-sequences"],
            what=f"log for job {job_id}",
        ))

    def shell(self, argv: list[str], *, what: str) -> str:
        return self._run(argv, what=what)

    def shell_rc(self, argv: list[str], *, what: str) -> tuple[int, str]:
        """Run and return (exit code, stdout) — for a callee whose 1 means a VERDICT.

        `ci_exemption.py decide` exits 1 to mean BLOCK (a measured verdict, not a
        failure to run), so it cannot go through `_run`. Every OTHER non-zero is
        still a measurement failure and the caller must refuse on it.
        """
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, env=self.env, timeout=300
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unmeasured(f"{what}: could not run {argv[0]!r} ({exc})") from exc
        if proc.returncode not in (0, 1):
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise Unmeasured(
                f"{what}: exited {proc.returncode}"
                + (f" — {detail[-1]}" if detail else "")
            )
        return proc.returncode, proc.stdout


# ── the pipeline ─────────────────────────────────────────────────────────────

def failing_jobs(jobs: list[dict], red_legs: list[str]) -> tuple[list[dict], list[dict]]:
    """(failed jobs to attribute, red legs that produced no attributable job).

    `red_legs` is the set of legs whose aggregate-side result was
    `failure`/`timed_out`, i.e. the legs THIS run delegated here. The workflow
    derives it from `toJSON(needs)` — the same `needs` this job already waited
    on — so it is not a second copy of the aggregate's leg table and cannot
    drift from it (the objection a hand-kept list would earn). The tests pass it
    directly.

    Leaving it EMPTY is the fail-OPEN shape, not the safe default: every job in
    the run is then in scope, and a leg whose failed job is missing from the
    listing (a matrix job that never expanded, a listing gap, a conclusion this
    module does not map) leaves `failed == []`, so `evaluate` takes its "nothing
    failed" branch and the required check goes green over a red delegated leg.

    A red leg with no failing job in the listing is a leg whose failure set is
    UNOBSERVABLE: it is returned here so the caller can refuse instead of
    certifying an empty set.
    """
    scoped = ([j for j in jobs if leg_base(j.get("name")) in red_legs]
              if red_legs else list(jobs))
    failed = [j for j in scoped if j.get("conclusion") in _FAILED_CONCLUSIONS]
    uncertifiable = [j for j in scoped if j.get("conclusion") in _UNCERTIFIABLE_CONCLUSIONS]
    seen = {leg_base(j.get("name")) for j in failed + uncertifiable}
    unattributed = [leg for leg in red_legs if leg not in seen]
    if uncertifiable:
        names = ", ".join(sorted(j.get("name") or "?" for j in uncertifiable))
        raise Unmeasured(
            f"leg(s) ended without a verdict ({names}) — a cancelled or "
            "startup-failed leg certifies nothing and its failure set is unobservable"
        )
    return failed, [{"leg": leg} for leg in unattributed]


def parse_signature_rows(text: str) -> dict[str, list[str]]:
    """`<nodeid>\\t<signature>` rows -> nodeid -> signatures (the shared producer's shape)."""
    out: dict[str, list[str]] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        nodeid, _, sig = raw.partition("\t")
        if not nodeid.strip():
            continue
        out.setdefault(nodeid, []).append(sig.strip())
    return out


def pr_failure_rows(runner: Runner, exemption: Path, jobs: list[dict],
                    logs_dir: str | None, capture: Path) -> tuple[str, list[str]]:
    """Build the PR side as `<nodeid>\\t<failures>\\t<runs>\\t<signature>` rows.

    One run per head, so every row is `1/1`: the shared decision's PR-side sample
    floor (#5250) sees `k_pr = 1` and decides on ATTRIBUTION (id measured red on main,
    overlapping signature, main's own row >= min_runs) rather than on a rate that a
    single push cannot measure. That is exactly the rail's shape for a fresh head.

    PARSED PER JOB, not as one blob, for two reasons: a job whose log carries no
    identity must become its OWN `job-unreadable::` key (#7131 — the key the shared
    producer emits, carrying no signature so the decision blocks it), and a shared
    blob could not say which job contributed nothing.

    Input shape: the per-job logs endpoint returns the plain log WITH a bare
    `<ts>Z ` stamp on every line and WITHOUT the `<job>\\t<step>\\t<ts>Z ` head
    `gh run view --log` renders. Both are handled before the doors see the text:

      * the bare stamp is REMOVED (:func:`strip_transport_stamps`) because it is
        transport, not content, and the shared parser's `_LOG_PREFIX_RE` requires a
        tab before the timestamp — so a stamped `FAILED …` line never matches
        `_SUMMARY_RE`'s `^FAILED` anchor. Leaving it made `ci_exemption.py signatures`
        exit 1 with `ids=0` on a capture whose `ids` door found three ids (#10202
        review 4, measured on job 122612288295), which failed this whole run closed.
      * the `<job>\\t<step>\\t` head is deliberately NOT synthesized: `_split_log_line`
        treats a stepped line as evidence about WHICH STEP failed, and inventing a
        step label would let an annotation from a passing step be attributed as a
        failure — the fail-OPEN the guard pass exists to close. `guard_step_failures`
        SKIPS a line with no head, so de-stamping can neither manufacture a step nor
        an attribution; a failure that exists ONLY as a step annotation yields no
        identity, and that job falls back to `job-unreadable::` and BLOCKS.
    """
    rows: list[str] = []
    ids_out: list[str] = []
    excerpts: list[str] = []
    nodeid_count = 0
    for job in jobs:
        name = job.get("name") or str(job.get("id"))
        try:
            text = runner.job_log(int(job["id"]), logs_dir)
        except Unmeasured as exc:
            # Named, never swallowed: this is the #7131 fallback surface.
            print(f"job {name}: log unreadable ({exc}) — falling back to job granularity")
            rows.append(f"job-unreadable::{slug_job(name)}\t1\t1\t")
            continue
        if not text.strip():
            print(f"job {name}: log is EMPTY — falling back to job granularity")
            rows.append(f"job-unreadable::{slug_job(name)}\t1\t1\t")
            continue

        per_job = capture.with_name(f"pr-capture-{job['id']}.txt")
        per_job.write_text(text, encoding="utf-8")
        excerpts.append(f"### job {name} ({job['id']})")
        ids_text = runner.shell(
            [sys.executable, str(exemption), "ids", "--log", str(per_job)],
            what=f"the shared id extraction for job {name}",
        )
        ids = [line.strip() for line in ids_text.splitlines() if line.strip()]
        if not ids:
            print(f"job {name}: NO attributable identity in its log — falling back to "
                  "job granularity (this BLOCKS; it is not a pass)")
            rows.append(f"job-unreadable::{slug_job(name)}\t1\t1\t")
            continue
        try:
            sigs_text = runner.shell(
                [sys.executable, str(exemption), "signatures", "--log", str(per_job)],
                what=f"the shared signature extraction for job {name}",
            )
        except Unmeasured as exc:
            # ⛔ THE TWO DOORS MUST AGREE ON ONE CAPTURE (#10202 review 4). `ids` read
            # n>=1 ids out of this very text and `signatures` read none, so the text
            # is not the problem — a parser that cannot measure what its sibling door
            # measured is UNMEASURED, and the message must say so instead of quoting
            # an all-zero counter line the operator cannot act on.
            raise Unmeasured(
                f"the shared SIGNATURE door could not read the capture its ID door "
                f"read for job {name} (ids={len(ids)}) — the failure set is "
                f"unmeasurable, so it cannot be compared against main: {exc}"
            ) from exc
        signatures = parse_signature_rows(sigs_text)
        for nodeid in ids:
            nodeid_count += 1
            ids_out.append(nodeid)
            # An id with no signature lands here with an EMPTY signature field:
            # `parse_failure_rows` drops it and `decide` fails its signature rule
            # CLOSED. Never a pass by omission.
            for sig in signatures.get(nodeid) or [""]:
                rows.append(f"{nodeid}\t1\t1\t{sig}")

    capture.write_text("\n".join(excerpts) + "\n", encoding="utf-8")
    if not rows:
        raise Unmeasured(
            "the failing jobs produced NO failure identity (no pytest nodeid, no "
            "unreadable-job key) — an empty PR set is not a clean PR"
        )
    if nodeid_count == 0:
        print("NOTE: every failing job fell back to JOB granularity — the comparison "
              "below is (job)-level, not (job, test)-level, and cannot be exempted")
    return "\n".join(rows) + "\n", ids_out


def main_baseline(runner: Runner, failure_set: Path, head: str, runs: int,
                  outdir: Path) -> tuple[str, str, dict[str, str]]:
    """main's rate + signature tables over the rail's widened window, or refuse.

    The `--runs-report` file is the measurement, and its ABSENCE is a signal
    (`ci-failure-set.sh` deliberately writes none when the listing failed): a window
    nothing was read from must not become a baseline.
    """
    rates_path = outdir / "main-rates.tsv"
    sigs_path = outdir / "main-signatures.tsv"
    report_path = outdir / "main-runs-report.txt"
    common = [
        "bash", str(failure_set), "--repo", runner.repo,
        "--workflow", "python-ci.yml", "--exclude", head,
    ]
    provenance_path = outdir / "main-provenance.txt"
    rates = runner.shell(
        [*common, "--main-union-rates", str(runs), "--runs-report", str(report_path),
         "--provenance", str(provenance_path)],
        what="main's rate baseline (ci-failure-set.sh --main-union-rates)",
    )
    sigs = runner.shell(
        [*common, "--main-union-signatures", str(runs)],
        what="main's signature baseline (ci-failure-set.sh --main-union-signatures)",
    )
    rates_path.write_text(rates, encoding="utf-8")
    sigs_path.write_text(sigs, encoding="utf-8")

    report: dict[str, str] = {}
    if report_path.is_file():
        for line in report_path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            report[key.strip()] = value.strip()
    if not report:
        raise Unmeasured(
            "main's baseline was never MEASURED — the run window produced no counters "
            "(ci-failure-set.sh writes no report when the listing fails). A missing "
            "check is not a green check"
        )
    tested = counter(report, "tested")
    if tested <= 0:
        raise Unmeasured(
            "main's baseline window holds no run that finished a measurement "
            f"(tested={tested}) — nothing was compared against"
        )
    if not rates.strip():
        raise Unmeasured(
            f"main's rate table is EMPTY over a measured window (tested={tested}) — "
            "an empty baseline would exempt nothing and block everything, so it is "
            "refused rather than used"
        )
    return str(rates_path), str(sigs_path), report


def counter(report: dict[str, str], key: str) -> int:
    """A `<key>=<n>` counter from the shared producer's report, or refuse.

    An unparsable counter is UNMEASURED, never 0 and never a traceback: `main()` maps
    only `Unmeasured`/`_Blocked` to its three exits, so a `ValueError` here would leave
    the process at exit 1 — i.e. it would report "NEW FAILURE — introduced by this PR"
    for a malformed counter.
    """
    raw = (report.get(key) or "").strip()
    try:
        return int(raw or 0)
    except ValueError as exc:
        raise Unmeasured(
            f"main's runs report carries {key}={raw!r}, which is not a number — the "
            "window's counters are unreadable") from exc


def main_leg_coverage(runner: "Runner", provenance: Path, tested: int,
                      legs: list[str], main_jobs_dir: str | None
                      ) -> tuple[str, set[str]]:
    """(BASIS, covered legs) — did main's baseline window EXERCISE each leg?

    `tested` is a RUN-level counter: a `completed` run that finished a measurement
    credits it WITHOUT probing its jobs (agent-infra `ci-failure-set.sh`, "FINISHED IS
    NOT TESTED" plus the `drop_superseded_runs` limit filed as agent-infra #1401). So a
    window whose runs each had `test-slow` SKIPPED — the shape a failing `changes` job
    produces, every dependent job skipped behind it — reads `tested=10, measured` while
    that leg was never executed at all. Every PR failure from it then reads as NEW.

    This is the extension of the existing `tested <= 0` refusal from the RUN dimension to
    the LEG dimension: one instrument arriving one question deeper, not a second
    predicate (constraint 7). The RUN SET is the shared producer's, never re-listed here:
    `--provenance` emits the examined failing runs of the SAME widened window the rate
    table came from, so a second window selector cannot drift from the rail's.

    A GREEN RUN COVERS EVERY LEG, and that is measured rather than assumed: a `skipped`
    leg the selector SELECTED fails closed in `python-ci-gate`'s own leg table (#5219), so
    a `success` python-ci run is evidence that every selected leg executed and was clean.
    `tested > len(examined)` proves such a run exists without a jobs call (`tested` =
    success|failure|timed_out, `examined` = failure|timed_out|startup_failure, so the
    excess can only be `success`). Without it the window is all-red and each failing
    run's job listing is the only evidence there is — probed in order, stopping as soon
    as every leg is covered, so the usual cost is one call.
    THE BASIS IS RETURNED, not assumed: `green-run` is an INFERENCE from the aggregate's
    own fail-closed leg table, and printing it as a mere "executed" would be the exact
    misuse of that word this change exists to stop. An ABSENT provenance file is NOT the
    all-green case — it is an unreadable run set, and it refuses.
    """
    examined: list[str] = []
    if not provenance.is_file():
        raise Unmeasured(
            f"main's leg dimension: no --provenance file at {provenance} — the examined "
            "run set is unreadable, so leg coverage cannot be claimed")
    for line in provenance.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        _sha, _, run_id = line.rpartition(":")
        if not run_id.strip():
            raise Unmeasured(
                f"main's baseline provenance line {line!r} names no run id — the "
                "leg dimension cannot be read, so measuredness cannot be claimed")
        examined.append(run_id.strip())
    if tested > len(examined):
        return "green-run", set(legs)
    covered: set[str] = set()
    for run_id in examined:
        for job in runner.jobs(run_id, main_jobs_dir):
            if job.get("conclusion") in _EXECUTED_CONCLUSIONS:
                covered.add(leg_base(job.get("name")))
        if covered.issuperset(legs):
            break
    return "probed", covered


def evaluate(args: argparse.Namespace) -> int:
    red_legs = [leg for leg in args.red_legs.split() if leg]
    print("=== #9984 required-verdict: did THIS PR introduce a failure? ===")
    print(f"event={args.event} head={args.head} run={args.run_id or '-'} "
          f"red legs={red_legs or '(every failing job in this run)'}")
    if args.event != "pull_request":
        # Deliberately unreachable from the workflow (the step is PR-only), and refused
        # here too so a future caller cannot make main's own red run exempt itself.
        raise Unmeasured(
            f"event {args.event!r} does not get the introduced-failure predicate: on "
            "push, 'no new failure relative to main' is vacuous and main's redness is "
            "the baseline every PR is compared against"
        )

    runner = Runner(gh=args.gh, repo=args.repo, token=args.token)
    jobs = runner.jobs(args.run_id, args.jobs_json)
    if not jobs:
        raise Unmeasured(
            f"the job listing for run {args.run_id} is EMPTY — the failing set of "
            f"legs {red_legs} cannot be read"
        )
    failed, unattributed = failing_jobs(jobs, red_legs)
    if unattributed:
        raise Unmeasured(
            "red leg(s) with NO failing job in the listing: "
            + ", ".join(item["leg"] for item in unattributed)
            + " — the failure set is unobservable, so nothing can be certified"
        )
    # Resolved AFTER the leg analysis on purpose: "did the legs even produce a
    # measurable failure set?" is a prior question to "can it be compared?", and its
    # refusal names the leg rather than the missing module.
    failure_set, exemption = resolve_predicate(args.agent_infra)
    if not failed:
        # Nothing in this run failed: there is no failure to attribute, and on a green
        # run that is the whole answer. (A cancelled leg never reaches here — it is
        # refused below as unmeasurable, and the aggregate reds on that class itself.)
        print("VERDICT: CLEAN — no job in this run reported a failure")
        return EXIT_CLEAN
    print(f"failing jobs ({len(failed)}): " +
          ", ".join(sorted(j.get("name") or "?" for j in failed)))

    with tempfile.TemporaryDirectory(prefix="new-failure-gate.") as tmp:
        outdir = Path(tmp)
        capture = outdir / "pr-capture.txt"
        rows, ids = pr_failure_rows(runner, exemption, failed, args.logs_dir, capture)
        rows_path = outdir / "pr-failures.tsv"
        rows_path.write_text(rows, encoding="utf-8")
        print(f"PR failing set ({len(rows.splitlines())} row(s)): "
              + (" ".join(sorted(ids)) if ids
                 else "all job-granularity (see the NOTE above)"))

        rates_path, sigs_path, report = main_baseline(
            runner, failure_set, args.head, args.main_runs, outdir)
        print("main baseline: window "
              + " ".join(f"{k}={v}" for k, v in sorted(report.items())))

        # THE LEG DIMENSION (#9984/#10289 finding). `tested` says the WINDOW measured;
        # it does not say the LEG this run failed in was ever executed on main. Refused
        # per leg, so the message names the leg that was unmeasured instead of reading
        # a stale baseline as fine.
        pr_legs = sorted({leg_base(j.get("name")) for j in failed})
        basis, covered = main_leg_coverage(
            runner, outdir / "main-provenance.txt", counter(report, "tested"),
            pr_legs, args.main_jobs_dir)
        print("leg coverage: " + ", ".join(
            f"{leg}=" + (f"executed ({basis})" if leg in covered else "NOT EXECUTED")
            for leg in pr_legs))
        never_ran = [leg for leg in pr_legs if leg not in covered]
        if never_ran:
            raise Unmeasured(
                "leg(s) " + ", ".join(repr(leg) for leg in never_ran)
                + " produced this run's failure(s), but main's baseline window holds no"
                " run in which the leg EXECUTED (every examined run had it skipped,"
                " cancelled or absent) — the rate is measured over the WINDOW, not over"
                " this leg, so the failure cannot be certified as introduced here")

        blocked_path = outdir / "blocked.txt"
        verdict_path = outdir / "verdict.txt"
        argv = [
            sys.executable, str(exemption), "decide",
            "--pr-failures", str(rows_path),
            "--main-rates", rates_path,
            "--main-signatures", sigs_path,
            "--blocked-out", str(blocked_path),
            "--verdict-out", str(verdict_path),
        ]
        print("--- the shared decision (ci_exemption.py decide — the rail's own) ---")
        code, decided = runner.shell_rc(argv, what="the shared exemption decision")
        print(decided.rstrip())
        if code != 0:
            raise _Blocked(
                "the shared decision BLOCKED a failure (its per-id reasons are printed "
                "above): relative to main, this PR introduced it"
            )
        # ⛔ A MISSING VERDICT FILE IS NOT A CLEAN VERDICT (#9984 review). The rail
        # this decision mirrors refuses exactly here — `admin-merge.sh` treats an
        # absent/empty/`VERDICT`-less file as "the gate produced no decision", never
        # as CLEAN ("the VERDICT FILE is the only thing that tells 'the gate produced
        # a decision' from 'the gate crashed'"). Substituting `VERDICT\tCLEAN` made
        # the two doors disagree on the one input that means "nothing was decided".
        # `decide` writes the file before returning 0 (`ci_exemption.py`, the
        # `--verdict-out` write precedes `return 1 if gated else 0`), so this is a
        # fail-closed guard on an unreachable path today — reachable the moment the
        # rail's write moves or fails quietly, which is the class this module exists
        # to refuse rather than absorb.
        verdict = (outdir / "verdict.txt").read_text(encoding="utf-8").strip() \
            if (outdir / "verdict.txt").is_file() else ""
        if not verdict.startswith("VERDICT"):
            raise Unmeasured(
                "the shared decision exited 0 but produced no `VERDICT` line — "
                f"nothing was decided, so nothing can be certified (read {verdict!r})")
        if "BLOCK" in verdict:
            raise _Blocked(f"the shared decision reported {verdict}")
        print(f"VERDICT: CLEAN — every failing id is main's own ({verdict})")
        return EXIT_CLEAN


class _Blocked(Exception):
    """A NEW failure, named by the shared decision. Exit 1."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="new-failure-gate.py",
        description="#9984: the required check's introduced-failure predicate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate", help="does THIS PR introduce a failure relative to main?")
    ev.add_argument("--repo", required=True)
    ev.add_argument("--run-id", default="")
    ev.add_argument("--head", required=True)
    ev.add_argument("--event", required=True,
                    help="github.event_name (only pull_request is answered)")
    ev.add_argument("--red-legs", default="",
                    help="legs whose result was failure/timed_out — the only one exemptible")
    ev.add_argument("--agent-infra", required=True,
                    help="agent-infra checkout holding scripts/ci-failure-set.sh + ci_exemption.py")
    ev.add_argument("--main-runs", type=int, default=10,
                    help="main baseline window (the rail's default)")
    ev.add_argument("--gh", default="gh")
    ev.add_argument("--token", default=os.environ.get("GH_TOKEN") or None)
    ev.add_argument("--jobs-json", default=None,
                    help="test seam: a recorded jobs listing instead of the API")
    ev.add_argument("--logs-dir", default=None,
                    help="test seam: recorded <job_id>.log files instead of the API")
    ev.add_argument("--main-jobs-dir", default=None,
                    help="test seam: recorded <run_id>.jsonl main-window job listings "
                         "instead of the API (the leg dimension's only input)")
    ev.set_defaults(func=evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except _Blocked as exc:
        print(f"VERDICT: NEW FAILURE — {exc}")
        return EXIT_NEW
    except Unmeasured as exc:
        print(f"::error::#9984 the required check could not measure the failure set — "
              f"failing CLOSED: {exc}")
        print("VERDICT: UNMEASURED (fail closed — a missing check is not a green check)")
        return EXIT_UNMEASURED


if __name__ == "__main__":  # pragma: no cover — exercised via main() in tests
    raise SystemExit(main())
