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
               Actions jobs API. That text is byte-identical in shape to
               `gh run view --log-failed` (it is where that command gets it from), so
               the shared parser reads it unchanged. `runner.temp` artifacts were the
               alternative and are rejected: only the pytest legs upload one, and a
               failure in a guard step (an orphan-count assert, a packaging guard) has
               no artifact at all.
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


class Unmeasured(Exception):
    """Anything we could not read. Always exit 2, never 0 (#9984 constraint 1)."""


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
        """The run's job list. Injected from a recorded fixture in the tests."""
        if jobs_json:
            raw = Path(jobs_json).read_text(encoding="utf-8", errors="replace")
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
        """One job's log, in the `job\tstep\t<line>` shape the shared parser reads."""
        if logs_dir:
            path = Path(logs_dir) / f"{job_id}.log"
            if not path.is_file():
                raise Unmeasured(f"no recorded log at {path}")
            return path.read_text(encoding="utf-8", errors="replace")
        return self._run(
            [self.gh, "api", f"repos/{self.repo}/actions/jobs/{job_id}/logs"],
            what=f"log for job {job_id}",
        )

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

    `red_legs` is normally EMPTY: the run's own job list is the authority, so the
    predicate job needs no second copy of the aggregate's leg table (two lists would
    drift, and a leg the predicate did not wait for is a leg it cannot adjudicate).
    It is accepted for a caller that already knows the set, and the tests use it.

    A red leg with no failing job in the listing is a leg whose failure set is
    UNOBSERVABLE (a matrix job that never expanded, a listing gap): the second list
    carries it so the caller can refuse instead of certifying an empty set.
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

    Input shape: the per-job logs endpoint returns the plain log, WITHOUT the
    `<job>\\t<step>\\t<ts>Z ` prefix `gh run view --log` renders. That prefix is
    deliberately NOT synthesized here: `_split_log_line` treats a stepped line as
    evidence about WHICH STEP failed, and inventing a step label would let an
    annotation from a passing step be attributed as a failure — the fail-OPEN the
    guard pass exists to close. Bare text is still fully readable by both doors for
    pytest nodeids; a failure that exists ONLY as a step annotation therefore yields
    no identity, and that job falls back to `job-unreadable::` and BLOCKS.
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
        sigs_text = runner.shell(
            [sys.executable, str(exemption), "signatures", "--log", str(per_job)],
            what=f"the shared signature extraction for job {name}",
        )
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
    rates = runner.shell(
        common + ["--main-union-rates", str(runs), "--runs-report", str(report_path)],
        what="main's rate baseline (ci-failure-set.sh --main-union-rates)",
    )
    sigs = runner.shell(
        common + ["--main-union-signatures", str(runs)],
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
    tested = int(report.get("tested") or 0)
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
        verdict = (outdir / "verdict.txt").read_text(encoding="utf-8").strip() \
            if (outdir / "verdict.txt").is_file() else "VERDICT\tCLEAN"
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
