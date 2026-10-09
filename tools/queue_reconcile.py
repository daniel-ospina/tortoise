#!/usr/bin/env python3
"""queue_reconcile — re-check the fleet's PR queue against real GitHub state.

WHY THIS EXISTS (MEASURED, not inferred)
----------------------------------------
`~/.pi/agent/state/queues/CLAIMS.tsv` is an append-only verdict log. A merge is a
CODE event: nothing in the fleet observes it and writes the row back. Measured
2026-10-08: row `7678` read `BLOCKED` for HOURS after its PR **#7686 had actually
merged** (merge commit `11bd9d9d71e42c8b063e182d4870fd78a68eb7bc`, merged_at
2026-10-08T17:22:27Z). The lane only discovered it by manually re-reading GitHub.

A queue that under-reports what has landed:
  * makes the PR backlog look bigger than it is;
  * can send a lane to REDO work that already merged (the duplicate-ownership
    failure this fleet has repeatedly been burned by);
  * is fleet-wide — the same staleness applies to every row whose PR has since
    merged or whose issue has since closed.

THE TAXONOMY TRAP
-----------------
The queue's terminal set is **{LANDED, FIXED, BLOCKED}**. `SKIPPED` is NOT
terminal — it means "not now", and a prior lane treating it as terminal silently
killed 43 queued items (documented in `claim.py`). `REFUSED` is deliberately NOT
in this tool's terminal set either: a merged PR contradicts a `REFUSED` row just
as much as it contradicts a `BLOCKED` one, and both are measurements this tool
must report.

Terminality is a property of the row HISTORY, not of the last row (#7329): a
later `CLAIMED`/`SKIPPED` row must not un-terminate an adjudicated item.
`effective_verdict()` below mirrors `claim.py`'s collapse so this tool and the
dispatcher agree on what the queue currently believes.

CLASSIFICATION (the tool's whole contract)
------------------------------------------
For each distinct identifier in the queue, resolve it against GitHub and compare
the queue's effective verdict to the measured state:

  MERGED_WHILE_NOT_LANDED          PR merged, effective verdict != LANDED.
                                   CONTRADICTION — APPLYABLE (append LANDED).
  CLOSED_ISSUE_WHILE_NON_TERMINAL  issue closed, effective verdict non-terminal.
                                   CONTRADICTION — REPORT-ONLY (the measured
                                   state does not say WHICH terminal verdict is
                                   right: it could be LANDED, FIXED, or
                                   not-planned, so the tool must not guess).
  BLOCKED_ON_CLOSED_ISSUE          issue closed, effective verdict BLOCKED.
                                   CONTRADICTION — REPORT-ONLY. BLOCKED is
                                   terminal for re-dispatch, but a CLOSED issue
                                   is evidence the block is resolved. This is
                                   exactly the motivating shape: the #7678 row
                                   keys the ISSUE while its PR #7686 merged, so
                                   a PR-only check would miss it.
  LANDED_BUT_PR_NOT_MERGED         PR open / closed-unmerged, verdict LANDED
                                   (the #7541 defect-#2 direction). REPORT-ONLY.
  UNKNOWN                          the number could not be resolved (404 or a
                                   failed `gh` call), OR the identifier is not a
                                   GitHub number at all. NEVER inferred green.
  OK                               verdict agrees with the measured state.

SAFETY CONTRACT
---------------
  1. DRY-RUN BY DEFAULT. Corrections are appended only under `--apply`.
  2. APPEND ONLY. The tool never rewrites or truncates the queue; it appends one
     row through the same `fcntl` lock `claim.py` uses, and takes a timestamped
     backup before the first write of a run.
  3. IDEMPOTENT. After a correction the effective verdict is terminal `LANDED`,
     so a re-run cannot flag it again; `apply` re-reads the queue first and
     skips any number already terminal.
  4. UNKNOWN IS NOT GREEN. A number that cannot be resolved is reported and
     forces exit 2; it is never folded into OK.
  5. IT DOES NOT TOUCH THE REAL QUEUE FROM TESTS. The default path is only read
     when the tool is run; tests inject their own `tmp_path` queue and resolver.

USAGE
    uv run python tools/queue_reconcile.py                  # dry run, report
    uv run python tools/queue_reconcile.py --apply          # append corrections
    uv run python tools/queue_reconcile.py --json           # machine output
    uv run python tools/queue_reconcile.py --only 7678 7686
    uv run python tools/queue_reconcile.py --queue PATH --repo owner/name

EXIT CONTRACT
    0  every number resolved, no contradiction
    1  contradiction(s) found (dry run) / remaining after a failed apply
    2  UNKNOWN present (a partial read is never clean)

The queue's own header/format is read first (``# pr<TAB>lane<TAB>verdict<TAB>
reason``); nothing about the columns is assumed beyond that.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── queue taxonomy (mirrors claim.py's effective_verdict) ────────────────────

# The task-visible terminal set. SKIPPED is deliberately absent: "not now" is
# not "done", and treating it as terminal is a documented trap. REFUSED is also
# absent on purpose — a merged PR contradicts REFUSED exactly as it contradicts
# BLOCKED, and that is the measurement this tool reports.
TERMINAL = frozenset({"LANDED", "FIXED", "BLOCKED"})
CLEARS = frozenset({"REVIVED"})

# Sanctioned verdict vocabulary, used only for a malformed-row advisory. A row
# whose verdict is outside this set is still parsed; it is not the tool's job to
# police spelling, only to report what it cannot classify.
KNOWN_VERDICTS = frozenset(
    {
        "CLAIMED", "FREE", "LANDED", "FIXED", "BLOCKED", "REFUSED", "SKIPPED",
        "REVIVED", "CLOSED", "MERGED", "SHOULD-BE-CLOSED", "RAIL-REFUSED",
        "HELD", "HELD-BACK", "NEEDS-REBASE", "QUESTION-SUBMITTED",
        "REVIEWED-NOT-CLEAN", "SEMANTIC-REPORT", "REFUSED(UPSTREAM-MOVED)",
        "reviewed-findings",
    }
)

# ── measured GitHub states ───────────────────────────────────────────────────

PR_MERGED = "PR_MERGED"
PR_CLOSED_UNMERGED = "PR_CLOSED_UNMERGED"
PR_OPEN = "PR_OPEN"
ISSUE_CLOSED = "ISSUE_CLOSED"
ISSUE_OPEN = "ISSUE_OPEN"
UNKNOWN = "UNKNOWN"

# ── findings ─────────────────────────────────────────────────────────────────

MERGED_WHILE_NOT_LANDED = "MERGED_WHILE_NOT_LANDED"
CLOSED_ISSUE_WHILE_NON_TERMINAL = "CLOSED_ISSUE_WHILE_NON_TERMINAL"
BLOCKED_ON_CLOSED_ISSUE = "BLOCKED_ON_CLOSED_ISSUE"
LANDED_BUT_PR_NOT_MERGED = "LANDED_BUT_PR_NOT_MERGED"
MALFORMED = "MALFORMED"
OK = "OK"

# Findings this tool will append a correction for. Only MERGED_WHILE_NOT_LANDED
# is applyable: the merge commit is a proof that the correct verdict is LANDED.
# The others are reported, not rewritten, because the measured state does not
# determine which terminal verdict is correct.
APPLYABLE = frozenset({MERGED_WHILE_NOT_LANDED})

DEFAULT_QUEUE = Path(
    os.environ.get(
        "CLAIMS_QUEUE", "~/.pi/agent/state/queues/CLAIMS.tsv"
    )
).expanduser()
DEFAULT_REPO = os.environ.get("QUEUE_RECONCILE_REPO", "daniel-ospina/tortoise")
CORRECTION_LANE = "queue-reconcile"
NUMERIC = re.compile(r"\A\d+\Z")


@dataclass
class QueueRow:
    number: str
    lane: str
    verdict: str
    reason: str
    line_no: int


@dataclass
class History:
    """Every row for one queue key, in file order."""

    number: str
    rows: list[QueueRow] = field(default_factory=list)

    @property
    def effective_verdict(self) -> str:
        return effective_verdict(self.rows)

    @property
    def last_lane(self) -> str:
        return self.rows[-1].lane if self.rows else ""

    @property
    def last_reason(self) -> str:
        return self.rows[-1].reason if self.rows else ""


@dataclass(frozen=True)
class Resolution:
    state: str
    merge_commit: str | None = None
    merged_at: str | None = None
    detail: str = ""


@dataclass
class Finding:
    number: str
    verdict: str
    state: str
    kind: str
    lane: str
    detail: str = ""
    resolution: Resolution | None = None


# ── pure queue logic (unit-tested without any file or network) ───────────────


def effective_verdict(rows, terminal=TERMINAL, clears=CLEARS) -> str:
    """Collapse one key's full row history to the verdict the queue acts on.

    Mirrors ``claim.py`` (#7329): once an item has been adjudicated TERMINAL, a
    later NON-terminal row does not re-open it; only an explicit re-opening verb
    (REVIVED) does. Returns "" for an empty history.
    """
    state = ""
    for row in rows:
        verdict = row.verdict
        if verdict in clears or verdict in terminal:
            state = verdict
        elif not state:
            state = verdict
    return state


def parse_queue(text: str) -> tuple[list[QueueRow], list[str]]:
    """Parse CLAIMS.tsv text. Returns (rows, malformed_lines).

    The format is ``# pr<TAB>lane<TAB>verdict<TAB>reason``. Comment/blank lines
    are skipped; a data line with fewer than three tab-separated fields is
    malformed and reported, never guessed at.
    """
    rows: list[QueueRow] = []
    malformed: list[str] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            malformed.append(f"line {i}: {line!r}")
            continue
        rows.append(
            QueueRow(
                number=parts[0].strip(),
                lane=parts[1].strip(),
                verdict=parts[2].strip(),
                reason=parts[3].strip() if len(parts) > 3 else "",
                line_no=i,
            )
        )
    return rows, malformed


def histories(rows: list[QueueRow]) -> dict[str, History]:
    """Group rows by queue key, preserving file order."""
    out: dict[str, History] = {}
    for row in rows:
        out.setdefault(row.number, History(number=row.number)).rows.append(row)
    return out


def classify(verdict: str, state: str) -> str:
    """Compare the queue's effective verdict to the measured GitHub state.

    Pure. The four required cases:
      * ``("BLOCKED", PR_MERGED)``                       -> MERGED_WHILE_NOT_LANDED
      * ``("CLAIMED", ISSUE_CLOSED)``                    -> CLOSED_ISSUE_WHILE_NON_TERMINAL
      * ``("BLOCKED", PR_OPEN)``                         -> OK
      * ``(anything, UNKNOWN)``                          -> UNKNOWN
    """
    if state == UNKNOWN:
        return UNKNOWN
    if state == PR_MERGED:
        return OK if verdict == "LANDED" else MERGED_WHILE_NOT_LANDED
    if state == ISSUE_CLOSED:
        if verdict == "BLOCKED":
            # The #7678 shape: the row keys the ISSUE while its PR merged. BLOCKED
            # is terminal for re-dispatch, so the spec's non-terminal rule alone
            # would miss it; a closed issue is the evidence the block is over.
            return BLOCKED_ON_CLOSED_ISSUE
        return OK if verdict in TERMINAL else CLOSED_ISSUE_WHILE_NON_TERMINAL
    if state in (PR_OPEN, PR_CLOSED_UNMERGED):
        # A LANDED row can only be contradicted by a PR, never by an issue: many
        # rows legitimately record an ISSUE number and name the PR in the reason.
        return LANDED_BUT_PR_NOT_MERGED if verdict == "LANDED" else OK
    if state == ISSUE_OPEN:
        return OK
    return UNKNOWN


def correction_reason(finding: Finding) -> str:
    """The reason appended with a LANDED correction — carries the proof."""
    res = finding.resolution
    commit = (res.merge_commit if res else None) or "unknown"
    merged_at = (res.merged_at if res else None) or "unknown"
    return (
        f"reconcile: measured state contradicts recorded verdict {finding.verdict!r} "
        f"(queue-reconcile {time.strftime('%Y-%m-%d')}) — PR #{finding.number} "
        f"merged_at {merged_at}, merge commit {commit}"
    )


# ── live GitHub resolution ───────────────────────────────────────────────────


class GitHubResolver:
    """Resolve a queue number to measured state via ``gh api``.

    One ``/issues/<N>`` call answers "PR or issue?" for both; a PR then gets one
    ``/pulls/<N>`` call for the merge commit. A number GitHub does not know is
    UNKNOWN — never inferred.
    """

    def __init__(self, repo: str, runner=None):
        self.repo = repo
        self._runner = runner or self._gh_api
        self._cache: dict[str, Resolution] = {}

    def _gh_api(self, path: str) -> tuple[object | None, str]:
        proc = subprocess.run(
            ["gh", "api", f"repos/{self.repo}/{path}"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout or "").strip()
        try:
            return json.loads(proc.stdout), ""
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            return None, f"unparsable gh output: {exc}"

    def resolve(self, number: str) -> Resolution:
        if number in self._cache:
            return self._cache[number]
        resolution = self._resolve(number)
        self._cache[number] = resolution
        return resolution

    def _resolve(self, number: str) -> Resolution:
        if not NUMERIC.match(number):
            return Resolution(UNKNOWN, detail=f"not a GitHub number: {number!r}")
        issue, err = self._runner(f"issues/{number}")
        if issue is None:
            detail = "not found (HTTP 404)" if "404" in err else (err or "gh api failed")
            return Resolution(UNKNOWN, detail=detail)
        if not issue.get("pull_request"):
            state = ISSUE_CLOSED if issue.get("state") == "closed" else ISSUE_OPEN
            return Resolution(state, detail=issue.get("state_reason") or "")
        pull, perr = self._runner(f"pulls/{number}")
        if pull is None:
            # /issues already carried the merged_at signal; use it rather than
            # pretend the PR is unmeasured.
            pr = issue.get("pull_request") or {}
            if pr.get("merged_at"):
                return Resolution(
                    PR_MERGED,
                    merged_at=pr.get("merged_at"),
                    detail=f"merge commit unread: {perr or 'pulls lookup failed'}",
                )
            state = PR_CLOSED_UNMERGED if issue.get("state") == "closed" else PR_OPEN
            return Resolution(state, detail=f"pulls lookup failed: {perr}")
        if pull.get("merged"):
            return Resolution(
                PR_MERGED,
                merge_commit=pull.get("merge_commit_sha"),
                merged_at=pull.get("merged_at"),
                detail=pull.get("state") or "",
            )
        state = PR_CLOSED_UNMERGED if pull.get("state") == "closed" else PR_OPEN
        return Resolution(state, detail=pull.get("state") or "")


# ── findings over a queue ────────────────────────────────────────────────────


def _finding(number: str, history: History, resolution: Resolution) -> Finding:
    verdict = history.effective_verdict
    if not NUMERIC.match(number):
        # A row whose identifier is not a GitHub number (a garbled row, #6915) is
        # reported as MALFORMED data-quality, NOT as a failed GitHub lookup: it
        # does not force the UNKNOWN exit, but it is never folded into OK either.
        return Finding(
            number=number or "(empty)",
            verdict=verdict or "(empty)",
            state=UNKNOWN,
            kind=MALFORMED,
            lane=history.last_lane,
            detail="identifier is not a GitHub number",
            resolution=Resolution(UNKNOWN, detail="not a GitHub number"),
        )
    return Finding(
        number=number,
        verdict=verdict or "(empty)",
        state=resolution.state,
        kind=classify(verdict, resolution.state),
        lane=history.last_lane,
        detail=resolution.detail,
        resolution=resolution,
    )


def build_findings(
    hist: dict[str, History],
    resolver,
    only: set[str] | None = None,
) -> list[Finding]:
    """Resolve every distinct number and classify it. Sorted by number."""
    numbers = sorted(hist)
    if only is not None:
        numbers = [n for n in numbers if n in only]
    findings: list[Finding] = []
    for number in numbers:
        history = hist[number]
        resolution = resolver.resolve(number)
        findings.append(_finding(number, history, resolution))
    return findings


def build_findings_parallel(
    hist: dict[str, History],
    resolver,
    only: set[str] | None = None,
    workers: int = 8,
) -> list[Finding]:
    """Same as build_findings but resolves numbers concurrently (network-bound)."""
    numbers = sorted(hist)
    if only is not None:
        numbers = [n for n in numbers if n in only]
    if not numbers:
        return []
    resolved: dict[str, Resolution] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(resolver.resolve, n): n for n in numbers}
        for fut in concurrent.futures.as_completed(futures):
            number = futures[fut]
            try:
                resolved[number] = fut.result()
            except Exception as exc:  # noqa: BLE001 - a failure is UNKNOWN, not a crash
                resolved[number] = Resolution(UNKNOWN, detail=f"resolution error: {exc}")
    return [_finding(n, hist[n], resolved[n]) for n in numbers]


# ── apply (append-only, locked, backed up) ───────────────────────────────────


def _append_locked(queue_path: Path, line: str) -> None:
    """Append one row under the same fcntl lock claim.py uses."""
    lock_path = queue_path.with_suffix(queue_path.suffix + ".lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(queue_path, "a") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def apply_corrections(
    queue_path: Path,
    findings: list[Finding],
    *,
    recheck: bool = True,
    backup: bool = True,
) -> list[dict]:
    """Append a LANDED row for every applyable finding whose PR really merged.

    Returns a list of ``{number, verdict, applied, skipped, reason}`` records.
    Re-reads the queue first so a number already terminal (another lane fixed
    it, or a previous run did) is skipped — this is the idempotency guard.
    """
    results: list[dict] = []
    applyable = [f for f in findings if f.kind in APPLYABLE]
    if not applyable:
        return results

    fresh_effective: dict[str, str] = {}
    if recheck:
        rows, _ = parse_queue(queue_path.read_text())
        fresh_effective = {n: h.effective_verdict for n, h in histories(rows).items()}

    if backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(queue_path, queue_path.with_name(f"{queue_path.name}.bak-reconcile-{stamp}"))

    for finding in applyable:
        current = fresh_effective.get(finding.number)
        if recheck and current == "LANDED":
            results.append(
                {"number": finding.number, "verdict": finding.verdict,
                 "applied": False, "skipped": True, "reason": "already LANDED on re-read"}
            )
            continue
        reason = correction_reason(finding)
        _append_locked(queue_path, f"{finding.number}\t{CORRECTION_LANE}\tLANDED\t{reason}")
        results.append(
            {"number": finding.number, "verdict": finding.verdict,
             "applied": True, "skipped": False, "reason": reason}
        )
    return results


# ── reporting ────────────────────────────────────────────────────────────────


def summarize(findings: list[Finding]) -> dict:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    return counts


def render_report(
    findings: list[Finding],
    applied: list[dict] | None,
    dry_run: bool,
    scanned: int | None = None,
) -> str:
    counts = summarize(findings)
    lines = ["", "queue_reconcile — CLAIMS.tsv vs measured GitHub state", "=" * 62]
    lines.append(f"distinct identifiers resolved : {scanned if scanned is not None else len(findings)}")
    for kind in (
        MERGED_WHILE_NOT_LANDED,
        CLOSED_ISSUE_WHILE_NON_TERMINAL,
        BLOCKED_ON_CLOSED_ISSUE,
        LANDED_BUT_PR_NOT_MERGED,
        UNKNOWN,
        MALFORMED,
        OK,
    ):
        lines.append(f"  {kind:<32} {counts.get(kind, 0)}")
    contradictions = [
        f for f in findings
        if f.kind in (
            MERGED_WHILE_NOT_LANDED,
            CLOSED_ISSUE_WHILE_NON_TERMINAL,
            BLOCKED_ON_CLOSED_ISSUE,
            LANDED_BUT_PR_NOT_MERGED,
        )
    ]
    if contradictions:
        lines.append("")
        lines.append("CONTRADICTIONS")
        lines.append("-" * 62)
        for f in contradictions:
            res = f.resolution
            commit = (res.merge_commit if res else None) or "-"
            lines.append(
                f"  #{f.number:<7} verdict={f.verdict:<14} measured={f.state:<18} "
                f"kind={f.kind}"
            )
            lines.append(f"          lane={f.lane!r} merge_commit={commit}")
    unknowns = [f for f in findings if f.kind == UNKNOWN]
    if unknowns:
        lines.append("")
        lines.append("UNKNOWN (NOT verified — never inferred green)")
        lines.append("-" * 62)
        for f in unknowns:
            lines.append(f"  #{f.number:<7} verdict={f.verdict:<14} detail={f.detail!r}")
    malformed = [f for f in findings if f.kind == MALFORMED]
    if malformed:
        lines.append("")
        lines.append("MALFORMED identifiers (data quality, not a GitHub answer)")
        lines.append("-" * 62)
        for f in malformed:
            lines.append(f"  {f.number!r:<26} verdict={f.verdict!r} lane={f.lane!r}")
    if applied:
        lines.append("")
        lines.append("APPLIED CORRECTIONS")
        lines.append("-" * 62)
        for rec in applied:
            status = "append LANDED" if rec["applied"] else f"skip ({rec['reason']})"
            lines.append(f"  #{rec['number']:<7} prior={rec['verdict']:<14} -> {status}")
    elif dry_run and contradictions:
        lines.append("")
        lines.append("DRY RUN — no rows written. Re-run with --apply to correct the applyable rows.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile CLAIMS.tsv against measured GitHub state.")
    ap.add_argument("--queue", type=Path, default=DEFAULT_QUEUE,
                    help=f"queue path (default: {DEFAULT_QUEUE})")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="GitHub owner/name")
    ap.add_argument("--apply", action="store_true",
                    help="append LANDED corrections for merged-while-not-LANDED rows")
    ap.add_argument("--no-backup", action="store_true", help="do not take a .bak before applying")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to these queue identifiers")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--workers", type=int, default=8, help="concurrent gh lookups")
    args = ap.parse_args(argv)

    if not args.queue.exists():
        print(f"queue_reconcile: queue not found: {args.queue}", file=sys.stderr)
        return 3

    rows, malformed = parse_queue(args.queue.read_text())
    hist = histories(rows)
    only = set(args.only) if args.only else None

    resolver = GitHubResolver(args.repo)
    if args.workers > 1:
        findings = build_findings_parallel(hist, resolver, only=only, workers=args.workers)
    else:
        findings = build_findings(hist, resolver, only=only)

    applied = None
    scanned = len(findings)
    if args.apply:
        applied = apply_corrections(args.queue, findings, backup=not args.no_backup)
        # A correction makes a number terminal; recompute what remains. The
        # resolution population (`scanned`) is captured above so the report
        # still names what it actually measured.
        remaining_numbers = {r["number"] for r in applied if r["applied"]}
        findings = [
            f for f in findings if not (f.number in remaining_numbers and f.kind in APPLYABLE)
        ]

    if args.json:
        payload = {
            "queue": str(args.queue),
            "repo": args.repo,
            "dry_run": not args.apply,
            "resolved": scanned,
            "malformed_lines": malformed,
            "counts": summarize(findings),
            "findings": [
                {
                    "number": f.number,
                    "verdict": f.verdict,
                    "measured_state": f.state,
                    "kind": f.kind,
                    "lane": f.lane,
                    "detail": f.detail,
                    "merge_commit": f.resolution.merge_commit if f.resolution else None,
                }
                for f in findings
            ],
            "applied": applied or [],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(findings, applied, dry_run=not args.apply, scanned=scanned))

    counts = summarize(findings)
    if counts.get(UNKNOWN, 0):
        return 2
    if any(counts.get(k, 0) for k in APPLYABLE) or counts.get(CLOSED_ISSUE_WHILE_NON_TERMINAL) \
            or counts.get(BLOCKED_ON_CLOSED_ISSUE) or counts.get(LANDED_BUT_PR_NOT_MERGED):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
