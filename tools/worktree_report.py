#!/usr/bin/env python3
"""#10220 — a lane's outcome must survive a reply that never arrives.

THE PROBLEM THIS TOOL EXISTS FOR
--------------------------------
A dispatched lane whose reply is truncated, killed at a silence threshold, or
queued behind a rate limit leaves *nothing* behind: the work is real, green and
on disk, but the parent sees only silence, and "no report" cannot be told apart
from "still working" or "failed". Two measured incidents (#8933 stage 1, #10122)
had complete, green work recovered only because a human guessed to run
`git status` inside `.worktrees/`.

The owner ruling relayed on #10220 (2026-10-28) makes the fix an ARTIFACT rather
than a message, in the ruling's own words:

    every lane writes `WORKTREE-REPORT.md` before it exits: worktree + branch,
    files changed, each suite run with pass/fail/skip counts, the commands used,
    and anything it could not do … include the suite commands verbatim (the
    verifier has no other way to tell "ran 37 tests" from "wrote 37").

WHY A TOOL AND NOT A TEMPLATE
-----------------------------
A template makes "37 passed" and "37 passed" identical text, so the verifier the
ruling asks for (#2977) would have nothing to read. This tool therefore closes
the three ways a report can lie, mechanically:

1. **There is no input path for a count.** `run` executes the command, streams the
   child's stdout/stderr straight to `.worktree-report/logs/NN-<slug>.log`, and
   derives the numbers BY PARSING THAT LOG. No `--passed`/`--counts` flag exists
   (`tests/test_worktree_report.py` pins its absence), so a number cannot be typed.
2. **The log must exist and be unmodified.** `check` re-hashes the cited log;
   deleting it fails `suite-log-missing`, editing it fails `suite-log-hash-mismatch`.
3. **The numbers are re-derived, not trusted.** `check` re-parses the log and
   compares it with the recorded counts (`suite-counts-differ`), so a count edited
   in the record is caught by the log it cites.

4. **The human-readable half is sealed.** Every render ends with a digest of the
   markdown it produced, and `check` recomputes it (`report-digest-mismatch`), so
   the numbers a REVIEWER reads cannot be edited away from the numbers the logs
   support.

   Stated limit, so nobody reads more into this than it does: a lane that rewrites
   the log, then its sha256, then the recorded counts produces an internally
   consistent artifact that nothing on this disk can tell from a real run. No
   on-disk scheme closes that — the merge path's fresh-context reviewer
   (`agent-infra#2977`) is the remaining guarantee, and the point of this artifact
   is that a reviewer now has something checkable to read

A red suite is *valid evidence* and is recorded honestly — `check` exits `3` for
it, never `0`, so a caller can never read red evidence as a pass.

WHAT LIVES WHERE (all inside the worktree, none of it product source)
--------------------------------------------------------------------
    WORKTREE-REPORT.md                      the human- and parent-readable artifact
    .worktree-report/state.json             the machine record the checker reads
    .worktree-report/logs/NN-<slug>.log     verbatim child output, one file per run

`init` writes the skeleton BEFORE the first test run, so a lane killed mid-run
still leaves its worktree, branch and file list behind — that is indicator (2) of
#10220, and it is why the report is rendered again before each command starts.

SCOPE — the split the issue body demands (no duplicate implementation)
---------------------------------------------------------------------
This tool is the PRODUCER + FORMAT AUTHORITY. The merge-path consumer that
refuses an unevidenced report is `agent-infra#2977` (`merge-one.sh`); it calls
`worktree_report.py check` instead of writing a second parser for a format it
does not own. Nothing here gates a merge on its own.

USAGE
-----
    uv run python tools/worktree_report.py init --issue 10220
    uv run python tools/worktree_report.py run --cmd 'uv run pytest tests/x.py -q'
    uv run python tools/worktree_report.py run --name lint --cmd 'uv run ruff check tools/x.py'
    uv run python tools/worktree_report.py note --not-done 'docker lane unavailable'
    uv run python tools/worktree_report.py write        # re-render (refreshes files changed)
    uv run python tools/worktree_report.py check [--json]

EXIT CODES
----------
`run`   exits with the first non-zero suite code it recorded (0 = every command
        exited 0) — the report is written either way, so a red suite is recorded,
        not hidden.
`check` 0 = report is valid evidence AND every recorded suite exited 0 ·
        3 = report is valid evidence but records a failing suite (the honest red;
               never conflate this with 0) ·
        1 = the report is missing, empty, incomplete or fabricated (rules named) ·
        2 = usage/environment error.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/worktree_report.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/worktree_report.py`"
    )

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_NAME = "WORKTREE-REPORT.md"
STATE_DIR = ".worktree-report"
STATE_NAME = "state.json"
LOGS_DIR = "logs"
STATE_VERSION = 1
TAIL_LINES = 15
# The sections a reader (and the #2977 verifier) is promised. A report missing one
# of these is not the artifact the ruling describes, however long it is.
REQUIRED_SECTIONS = ("## Files changed", "## Suites run", "## Could not do")

# pytest's canonical summary counter. Deliberately the SAME pattern as
# `tools/ci_timing.py::COUNT_RE` (the fleet's one authority for reading counts out
# of pytest output) — pinned by
# `tests/test_worktree_report.py::test_count_regex_stays_the_single_authority`
# rather than imported, so this tool pays no import cost for a 5k-line module.
COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|skipped|xfailed|xpassed)")
# Only pytest's SUMMARY line is a count, never a line of a test's own output: a
# test that prints "3 passed" must not become the lane's evidence. The shape
# pytest emits is `<counters> in <D>.<D>s`, optionally wrapped in `=` rules.
# pytest appends ` (0:07:33)` once a run passes a minute, so the tail is optional —
# dropping it made every suite longer than 60 s record "no counts parsed" (measured on
# tests/test_ci_selection.py: `346 passed, 1 skipped, 4 warnings in 453.21s (0:07:33)`).
PYTEST_SUMMARY_RE = re.compile(
    r"^(?P<body>(?:\d+\s+[a-z]+(?:,\s*)?)+)\s+in\s+[\d.]+s(?:\s*\([^)]*\))?$"
)
_TOTAL_KEYS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
# The render's own tamper seal (`state_digest`), verified by `check`.
DIGEST_RE = re.compile(r"<!-- report-digest: ([0-9a-f]{64}) -->\n?$")


# ── small helpers ────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(wt: Path, *args: str) -> str | None:
    """Run git in `wt`; None on any failure (a non-git dir is a usage error, not a traceback)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(wt), *args], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _target(args: argparse.Namespace) -> Path:
    """The worktree an invocation acts on: `--worktree`, else the current directory."""
    return Path(args.worktree) if args.worktree else Path.cwd()


def worktree_root(path: Path) -> Path:
    """The git worktree containing `path` (a linked worktree resolves to ITSELF, not the hub)."""
    resolved = Path(path).expanduser().resolve()
    top = _git(resolved, "rev-parse", "--show-toplevel")
    if top:
        return Path(top)
    if not resolved.is_dir():
        raise SystemExit(f"worktree_report: {resolved} is not a directory")
    return resolved


def _branch(wt: Path) -> str:
    name = _git(wt, "rev-parse", "--abbrev-ref", "HEAD") or ""
    if name and name != "HEAD":
        return name
    sha = _git(wt, "rev-parse", "--short", "HEAD") or "unknown"
    return f"DETACHED@{sha}"


def _head(wt: Path) -> str:
    return _git(wt, "rev-parse", "HEAD") or ""


def _base_sha(wt: Path) -> str:
    """What "files changed" is measured against: merge-base with origin/main, else the root commit.

    The fallback matters: a worktree whose remote-tracking ref is missing (a fresh
    clone of a fork, a detached repro) must still produce a file list rather than
    an empty one that reads as "nothing changed".
    """
    for ref in ("origin/main", "origin/master", "origin/HEAD"):
        base = _git(wt, "merge-base", ref, "HEAD")
        if base:
            return base
    return _git(wt, "rev-list", "--max-parents=0", "HEAD") or ""


def files_changed(wt: Path, base: str) -> list[tuple[str, str]]:
    """Tracked changes against `base` PLUS untracked files — a lane's new test file is untracked.

    This report's own artifacts are excluded: a report that lists itself as one of the
    lane's changed files is noise, and it would also read as a change in a repo that
    has not gitignored them (#10220 ships that gitignore entry, the exclusion does not
    depend on it).
    """
    rows: list[tuple[str, str]] = []
    if base:
        diff = _git(wt, "diff", "--name-status", base) or ""
        for line in diff.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                rows.append((parts[0], parts[-1]))
    untracked = _git(wt, "ls-files", "--others", "--exclude-standard") or ""
    rows.extend(("??", line) for line in untracked.splitlines() if line)
    return [row for row in rows if not _is_own_artifact(row[1])]


def _is_own_artifact(path: str) -> bool:
    return path in (REPORT_NAME, STATE_DIR) or path.startswith(f"{STATE_DIR}/")



def parse_counts(text: str) -> dict[str, int] | None:
    """Counts from the LAST pytest summary line in `text`, or None when the command is not pytest.

    None is a legitimate answer (a lint or markdown run has no counts): the proof
    for such a command is its exit code and the verbatim log, not a number.
    """
    for raw in reversed(text.splitlines()):
        line = raw.strip().strip("=").strip()
        if not line:
            continue
        match = PYTEST_SUMMARY_RE.match(line)
        if not match:
            continue
        pairs = COUNT_RE.findall(match.group("body"))
        if not pairs:
            continue
        counts: dict[str, int] = {}
        for number, word in pairs:
            key = "errors" if word == "error" else word
            counts[key] = counts.get(key, 0) + int(number)
        return counts
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(command: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")
    return (slug[:40].rstrip("-")) or "suite"


def _log_tail(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-TAIL_LINES:]


# ── state ────────────────────────────────────────────────────────────────────


def _state_path(wt: Path) -> Path:
    return wt / STATE_DIR / STATE_NAME


def load_state(wt: Path) -> dict[str, Any] | None:
    path = _state_path(wt)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return None
    state.setdefault("suites", [])
    state.setdefault("not_done", [])
    return state


def save_state(wt: Path, state: dict[str, Any]) -> None:
    path = _state_path(wt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_state(wt: Path, issue: str | None) -> dict[str, Any]:
    """Load the recorded state, or start one — never silently discarding a READABLE state.

    A missing state is the normal first run. An UNREADABLE one (truncated JSON, a
    version this tool does not know) is replaced, but LOUDLY: the suites it held are
    gone, and a lane must not be able to believe it re-ran everything when it did not.
    """
    state = load_state(wt)
    if state is not None:
        if issue and not state.get("issue"):
            state["issue"] = issue
        return state
    if _state_path(wt).exists():
        print(
            f"worktree_report: ⚠ {_state_path(wt)} is unreadable (not version "
            f"{STATE_VERSION}) — starting a fresh record; any suite it held is gone",
            file=sys.stderr,
        )
    return new_state(wt, issue)


def new_state(wt: Path, issue: str | None) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "issue": issue or "",
        "worktree": str(wt),
        "branch": _branch(wt),
        "base_sha": _base_sha(wt),
        "started_at": _now(),
        "suites": [],
        "not_done": [],
    }


# ── render ───────────────────────────────────────────────────────────────────


def _cell(text: str) -> str:
    """Escape a value for a markdown table cell (a command may contain `|`, or a newline)."""
    return " ".join(text.splitlines()).replace("|", "\\|")


def _counts_cell(counts: dict[str, int] | None, key: str) -> str:
    if counts is None:
        return "—"
    return str(counts.get(key, 0))


def _verdict_line(state: dict[str, Any]) -> str:
    suites = state["suites"]
    failed = [s for s in suites if s.get("exit") not in (0, None)]
    running = [s for s in suites if s.get("exit") is None]
    parts = [f"**{len(suites)} suite(s) recorded**"]
    if running:
        parts.append(f"**{len(running)} still running/incomplete**")
    parts.append(f"**{len(failed)} failed**")
    if state["not_done"]:
        parts.append(f"**{len(state['not_done'])} noted as not done**")
    return " · ".join(parts)


def render(wt: Path, state: dict[str, Any]) -> str:
    head = _head(wt)
    out: list[str] = []
    out.append(f"# WORKTREE-REPORT — `{state['branch']}`")
    out.append("")
    out.append(
        "<!-- #10220: generated by `tools/worktree_report.py`. The counts below are parsed from the"
    )
    out.append(
        "     cited logs and are re-derived by `check`, so a hand-written number cannot pass."
    )
    out.append("     Do not hand-edit: run `write` (or another `run`) instead. -->")
    out.append("")
    if state.get("issue"):
        out.append(f"- **Issue:** #{state['issue']}")
    out.append(f"- **Worktree:** `{state['worktree']}`")
    out.append(f"- **Branch:** `{state['branch']}`")
    base = (state.get("base_sha") or "")[:12]
    out.append(f"- **Base:** `{base or 'unknown'}` → **head at write:** `{head[:12]}`")
    out.append(f"- **Started:** {state['started_at']} · **Written:** {_now()}")
    out.append(f"- {_verdict_line(state)}")
    out.append("")
    out.append("## Files changed")
    out.append("")
    rows = files_changed(wt, state.get("base_sha") or "")
    if rows:
        out.append("| status | path |")
        out.append("|---|---|")
        out.extend(f"| `{_cell(status)}` | `{_cell(path)}` |" for status, path in rows)
    else:
        out.append(f"_No files changed against `{base or 'HEAD'}`._")
    out.append("")
    out.append("## Suites run")
    out.append("")
    if not state["suites"]:
        out.append(
            "_None recorded yet. A report with no suite is NOT evidence of a green change — "
            "`check` refuses it (`no-suites`)._"
        )
    else:
        out.append(
            "| # | exit | passed | failed | skipped | other | duration | command | log (sha256) |"
        )
        out.append("|---|---|---|---|---|---|---|---|---|")
        for suite in state["suites"]:
            counts = suite.get("counts")
            other = "—"
            if counts is not None:
                other = ", ".join(
                    f"{key}={counts[key]}" for key in ("errors", "xfailed", "xpassed") if counts.get(key)
                ) or "—"
            exit_code = "…running" if suite.get("exit") is None else str(suite["exit"])
            duration = (
                "—" if suite.get("duration_s") is None else f"{suite['duration_s']:.1f}s"
            )
            sha = (suite.get("log_sha256") or "")[:12] or "—"
            out.append(
                f"| {suite['n']} | {exit_code} | {_counts_cell(counts, 'passed')} | "
                f"{_counts_cell(counts, 'failed')} | {_counts_cell(counts, 'skipped')} | "
                f"{other} | {duration} | {_cell(suite['command'])} | "
                f"`{_cell(suite['log'])}` ({sha}) |"
            )
        out.append("")
        for suite in state["suites"]:
            out.append(f"### Suite {suite['n']} — {suite.get('label') or suite['n']}")
            out.append("")
            exit_code = "not finished" if suite.get("exit") is None else f"exit {suite['exit']}"
            out.append(f"- **{exit_code}** · log `{_cell(suite['log'])}`")
            out.append("")
            out.append("Command, verbatim as executed:")
            out.append("")
            out.append("````text")
            out.append(str(suite["command"]))
            out.append("````")
            tail = _log_tail(wt / suite["log"]) if suite.get("log") else []
            if tail:
                out.append("")
                out.append(f"Output, last {TAIL_LINES} lines:")
                out.append("")
                out.append("````text")
                out.extend(tail)
                out.append("````")
            out.append("")
    out.append("## Could not do")
    out.append("")
    if state["not_done"]:
        out.extend(f"- {item}" for item in state["not_done"])
    else:
        out.append("_Nothing recorded._")
    out.append("")
    out.append("## How to verify this report")
    out.append("")
    out.append("```bash")
    out.append("uv run python tools/worktree_report.py check")
    out.append("```")
    out.append("")
    return "\n".join(out)


def report_digest(body: str) -> str:
    """The seal over the rendered markdown, so a hand-edited report is detectable.

    The md is the half a human (and a reviewer) reads. Without a seal, a lane could leave
    the log and the state truthful and edit only the markdown it is judged on — which is
    exactly the "wrote 37 vs ran 37" failure at the human-facing layer. The digest covers
    every byte render() produced, and `check` recomputes it from the file it was given.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_report(wt: Path, state: dict[str, Any]) -> Path:
    """Render the report and record which head it describes (the checker's staleness signal)."""
    state["head_at_write"] = _head(wt)
    body = render(wt, state)
    path = wt / REPORT_NAME
    path.write_text(f"{body}<!-- report-digest: {report_digest(body)} -->\n", encoding="utf-8")
    save_state(wt, state)
    return path


# ── subcommands ──────────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    wt = worktree_root(_target(args))
    existing = load_state(wt)
    if existing and not args.force:
        # Idempotent: re-running `init` must never discard a suite already recorded
        # (a lane re-entering after a context compaction is the common case).
        state = existing
        if args.issue and not state.get("issue"):
            state["issue"] = args.issue
            save_state(wt, state)
        path = write_report(wt, state)
        print(f"worktree_report: kept existing state, re-rendered {path}")
        print(f"worktree_report: {len(state['suites'])} suite(s) already recorded")
        return 0
    state = new_state(wt, args.issue)
    save_state(wt, state)
    path = write_report(wt, state)
    print(f"worktree_report: wrote skeleton {path}")
    print(
        "worktree_report: run each suite THROUGH this tool "
        "(`run --cmd '<verbatim command>'`) so the counts come from real output"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    wt = worktree_root(_target(args))
    state = resolve_state(wt, args.issue)
    save_state(wt, state)
    first_nonzero = 0
    for command in args.cmd:
        number = len(state["suites"]) + 1
        slug = _slug(command)
        log_rel = f"{STATE_DIR}/{LOGS_DIR}/{number:02d}-{slug}.log"
        log_path = wt / log_rel
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "n": number,
            "label": (args.name if args.name and len(args.cmd) == 1 else slug),
            "command": command,
            "exit": None,
            "counts": None,
            "duration_s": None,
            "log": log_rel,
            "log_sha256": None,
            "started_at": _now(),
            "finished_at": None,
        }
        state["suites"].append(entry)
        save_state(wt, state)
        # ⛔ Rendered BEFORE the child starts: this is the line that makes a lane
        # killed at the silence threshold recoverable — the command and the worktree
        # are on disk even though the suite (and the reply) never finished.
        write_report(wt, state)
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8") as handle:
            try:
                # `shell=True` on purpose: the command IS the artifact, and it is the
                # lane's own verbatim invocation — re-parsing it into argv would
                # stop the report from showing what the lane actually ran.
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(wt),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    timeout=args.timeout,
                    check=False,
                )
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                handle.write(f"\n[worktree_report] TIMEOUT after {args.timeout}s\n")
                exit_code = 124
        text = log_path.read_text(encoding="utf-8", errors="replace")
        entry.update(
            {
                "exit": exit_code,
                "counts": parse_counts(text),
                "duration_s": round(time.monotonic() - started, 1),
                "log_sha256": _sha256(log_path),
                "finished_at": _now(),
            }
        )
        save_state(wt, state)
        write_report(wt, state)
        counts = entry["counts"]
        summary = "no counts parsed (not a pytest-shaped suite)" if counts is None else ", ".join(
            f"{counts[key]} {key}" for key in _TOTAL_KEYS if counts.get(key)
        ) or "no tests"
        print(f"worktree_report: suite {number} exit {exit_code} — {summary} — log {log_rel}")
        if exit_code != 0 and first_nonzero == 0:
            first_nonzero = exit_code
    return first_nonzero


def cmd_note(args: argparse.Namespace) -> int:
    wt = worktree_root(_target(args))
    state = resolve_state(wt, args.issue)
    for item in args.not_done:
        state["not_done"].append(item)
    save_state(wt, state)
    path = write_report(wt, state)
    print(f"worktree_report: recorded {len(args.not_done)} note(s) in {path}")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    wt = worktree_root(_target(args))
    state = load_state(wt)
    if state is None:
        print(
            "worktree_report: no state at "
            f"{_state_path(wt)} — run `init` first (a hand-written report is refused by `check`)"
        )
        return 2
    path = write_report(wt, state)
    print(f"worktree_report: re-rendered {path}")
    return 0


def _check_report(wt: Path, report: Path) -> list[str]:
    rules: list[str] = []
    if not report.exists():
        return ["report-missing"]
    text = report.read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < 80:
        return ["report-empty"]
    missing = [section for section in REQUIRED_SECTIONS if section not in text]
    if missing:
        rules.append("report-sections:" + ",".join(section.lstrip("# ").strip() for section in missing))
    return rules


def _check_digest(text: str) -> list[str]:
    """The markdown must be exactly what was rendered — a hand-edited report is refused."""
    match = DIGEST_RE.search(text)
    if not match:
        return ["report-digest-missing"]
    if match.group(1) != report_digest(text[: match.start()]):
        return ["report-digest-mismatch"]
    return []


def _check_suites(wt: Path, state: dict[str, Any]) -> tuple[list[str], int]:
    rules: list[str] = []
    red = 0
    if not state["suites"]:
        rules.append("no-suites")
    for suite in state["suites"]:
        tag = f"suite-{suite.get('n', '?')}"
        if not str(suite.get("command") or "").strip():
            rules.append(f"suite-command-missing:{tag}")
        if suite.get("exit") is None:
            rules.append(f"suite-incomplete:{tag}")
            continue
        if suite["exit"] != 0:
            red += 1
        counts = suite.get("counts")
        if suite["exit"] == 0 and counts and (counts.get("failed") or counts.get("errors")):
            # A summary that reports failures on a zero exit is contradictory evidence,
            # and the safe reading of a contradiction is never "green".
            rules.append(f"suite-exit-contradicts-counts:{tag}")
        log_rel = suite.get("log") or ""
        if not log_rel:
            rules.append(f"suite-log-missing:{tag}")
            continue
        log_path = wt / log_rel
        if not log_path.exists():
            rules.append(f"suite-log-missing:{tag}")
            continue
        recorded = suite.get("log_sha256") or ""
        if not recorded:
            rules.append(f"suite-log-hash-missing:{tag}")
            continue
        if _sha256(log_path) != recorded:
            rules.append(f"suite-log-hash-mismatch:{tag}")
            continue
        derived = parse_counts(log_path.read_text(encoding="utf-8", errors="replace"))
        if derived != counts:
            rules.append(f"suite-counts-differ:{tag}")
    return rules, red


def cmd_check(args: argparse.Namespace) -> int:
    wt = worktree_root(_target(args))
    report = Path(args.report).expanduser().resolve() if args.report else wt / REPORT_NAME
    rules = _check_report(wt, report)
    state = load_state(wt)
    if state is None:
        rules.append("state-missing" if not _state_path(wt).exists() else "state-malformed")
        red = 0
    else:
        suite_rules, red = _check_suites(wt, state)
        rules.extend(suite_rules)
        if report.exists():
            rules.extend(_check_digest(report.read_text(encoding="utf-8", errors="replace")))
    stale = False
    if state is not None:
        current = _head(wt)
        recorded = state.get("head_at_write") or ""
        stale = bool(current and recorded and current != recorded)
        if not state.get("branch"):
            rules.append("report-branch-missing")
    code = 1 if rules else (3 if red else 0)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": code == 0,
                    "report": str(report),
                    "rules": rules,
                    "suites": len(state["suites"]) if state else 0,
                    "failing_suites": red,
                    "head": _head(wt),
                    "head_moved_since_write": stale,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return code
    print(f"worktree_report: {report}")
    if rules:
        print(f"VERDICT: INVALID ({len(rules)} rule(s) failed)")
        for rule in rules:
            print(f"  - {rule}")
    elif red:
        print(f"VERDICT: VALID EVIDENCE, {red} FAILING SUITE(S) RECORDED (exit 3 — not a pass)")
    else:
        print(f"VERDICT: VALID ({len(state['suites']) if state else 0} suite(s), all exit 0)")
    if stale:
        print(
            "  ⚠ head moved since the report was written — re-run the affected suites "
            "so the report describes the tree you are actually shipping"
        )
    return code


# ── cli ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--worktree",
        default=None,
        help="worktree to report on (default: the current directory's git worktree)",
    )
    common.add_argument("--issue", default=None, help="issue number this lane addresses")
    parser = argparse.ArgumentParser(
        prog="worktree_report.py",
        description="Write/verify the durable lane report #10220 mandates (WORKTREE-REPORT.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", parents=[common], help="write the skeleton report")
    init.add_argument("--force", action="store_true", help="discard recorded suites and start over")
    init.set_defaults(func=cmd_init)

    run = sub.add_parser(
        "run",
        parents=[common],
        help="execute a suite, capture its verbatim output, parse its counts, record it",
    )
    # ⛔ There is deliberately NO `--passed` / `--failed` / `--counts` option: the
    # numbers are parsed from the log this command produces (see module docstring).
    run.add_argument("--cmd", action="append", required=True, help="the exact command (repeatable)")
    run.add_argument("--name", default=None, help="label for this suite (default: a slug of --cmd)")
    run.add_argument("--timeout", type=int, default=None, help="kill the command after N seconds")
    run.set_defaults(func=cmd_run)

    note = sub.add_parser("note", parents=[common], help="record something the lane could not do")
    note.add_argument("--not-done", action="append", required=True, help="repeatable")
    note.set_defaults(func=cmd_note)

    write = sub.add_parser("write", parents=[common], help="re-render the report from state")
    write.set_defaults(func=cmd_write)

    check = sub.add_parser(
        "check",
        parents=[common],
        help="verify a report is un-fabricated evidence (exit 0 green, 3 red, 1 invalid)",
    )
    check.add_argument(
        "--report",
        default=None,
        help=f"path to the report (default: <worktree>/{REPORT_NAME})",
    )
    check.add_argument("--json", action="store_true", help="machine-readable verdict")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
