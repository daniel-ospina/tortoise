#!/usr/bin/env python3
"""The extractor-v4 capstone's closing gate (#9080 / epic #7949).

The epic states its own closing rule verbatim: *"Close this epic only when
E1–E5 all have evidence attached to this issue — not when the children close.
A child closing is a claim; this epic is where the claim is checked against the
pipeline running end to end."* A rule in prose is not a gate: nothing fails
when a row is marked green on a closed child, on an evidence file that does not
exist, or on a denominator inherited from a different corpus. This tool is the
mechanical form of that rule, and it is the second half of the issue's own
acceptance command:

    python docs/evidence/7949-capstone/probes/capstone_run.py \\
        --prereg <frozen pre-registration commit> --corpus <pre-registered corpus> \\
        | tee docs/evidence/7949-capstone/capstone.log
    python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone.log

The runner EMITS the verdict table (``capstone-verdict-v1``, between the two
marker lines); this tool CHECKS it. Splitting them is the point: the run is
expensive and privileged (a real LLM + a real graph), the check is pure and
runs in CI, so a hand-edited table cannot pass without the artifacts behind it.

⚠️ What this tool can and cannot tell you. It CAN establish that every row
carries an artifact that exists, a sha that resolves, a denominator that agrees
with its sibling, and a named blocker when it is not green. It CANNOT establish
that the measurement itself was performed honestly — a fabricated evidence
file with a plausible sha passes. That limit is stated rather than hidden
because the epic's failure mode is *inference*, not forgery: five rows marked
green off closed children, with no artifact at all. This closes that.

## The rule set (each finding carries a stable code)

    V1  root schema is ``capstone-verdict-v1``
    V2  exactly five rows, ids E1..E5, one each — no missing, no duplicate
    V3  per-row required fields; verdict/evidence_kind in vocabulary; sha shape
    V4  every row's evidence path exists (repo-relative, no escape)
    V5  GREEN requires: evidence_kind=measurement, 0 < numerator <= denominator,
        blocked_by null, sha resolvable AND not predating the pre-registration,
        and run.status=COMPLETED with ALL eight steps observed in one pass
    V6  non-green requires a named blocker (``#NNN``) — *name the blocking issue*;
        a ``refusal`` row must cite a capstone log that carries that row
    V7  a measurement row's ``corpus_sha256`` equals the log's corpus sha —
        "every green row cites an evidence file whose before/after denominators
        are the same corpus"
    V8  a row naming a ``sibling`` must match that sibling's numerator AND
        denominator — the denominators are carried, not restated
    V9  --require-green (the EPIC's closing rule): 5/5 GREEN

Exit codes: 0 = clean, 1 = findings, 2 = usage / unreadable log. Without
``--require-green`` an honest all-blocked table is CLEAN (exit 0): the epic is
not closeable, but the table is not lying, and that is the distinction the
verdict vocabulary exists to make.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "capstone-verdict-v1"
CRITERIA = ("E1", "E2", "E3", "E4", "E5")
STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")
VERDICTS = ("GREEN", "RED", "BLOCKED", "UNPREREGISTERED")
EVIDENCE_KINDS = ("measurement", "refusal")

BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
END = "=== CAPSTONE-VERDICT-END ==="

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_BLOCKER_RE = re.compile(r"^#\d+$")


def extract_block(text: str) -> dict:
    """Pull the verdict JSON out of a capstone log.

    The log is a human-readable run record with the machine block embedded, so
    the operator reads the same file the gate parses — one artifact, not two
    that can disagree. A missing/malformed block is a usage error (exit 2), not
    a finding: a log with no table is not a red row, it is not a table.
    """
    if BEGIN not in text or END not in text:
        raise ValueError(
            f"no verdict block: expected the markers {BEGIN!r} / {END!r} "
            "(capstone_run.py emits them)")
    start = text.index(BEGIN) + len(BEGIN)
    end = text.index(END)
    if end < start:
        raise ValueError("verdict block markers are out of order")
    return json.loads(text[start:end].strip())


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, code: str, where: str, msg: str) -> None:
        self.items.append((code, where, msg))

    def __bool__(self) -> bool:
        return bool(self.items)

    def render(self) -> str:
        return "\n".join(f"  [{c}] {w}: {m}" for c, w, m in self.items)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=False)


def _is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree").returncode == 0


def _resolves(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _is_ancestor(repo: Path, maybe_ancestor: str, of: str) -> bool:
    """True when ``maybe_ancestor`` is ``of`` or precedes it.

    This is the mechanical form of *"0 rows green where the evidence artifact
    predates the pre-registration"*: evidence measured BEFORE the thresholds
    were frozen cannot be evidence for them — the threshold would have been
    chosen to fit the number.
    """
    if maybe_ancestor == of:
        return True
    return _git(repo, "merge-base", "--is-ancestor",
                maybe_ancestor, of).returncode == 0


def check(record: dict, *, log_path: Path, repo: Path,
          require_green: bool) -> Findings:
    f = Findings()

    # ── V1 root schema ────────────────────────────────────────────────────
    if record.get("schema") != SCHEMA:
        f.add("V1", "root", f"schema is {record.get('schema')!r}, expected "
                            f"{SCHEMA!r} — refuse rather than guess a shape")

    # ── V2 the five-criterion contract ────────────────────────────────────
    rows = record.get("rows")
    if not isinstance(rows, list):
        f.add("V2", "root.rows", "missing or not a list — the table is 5 rows")
        return f
    seen = [r.get("criterion") for r in rows if isinstance(r, dict)]
    for criterion in CRITERIA:
        n = seen.count(criterion)
        if n == 0:
            f.add("V2", f"rows[{criterion}]", "row is MISSING — the epic closes "
                                              "on 5/5, so an absent row can "
                                              "never be inferred from the rest")
        elif n > 1:
            f.add("V2", f"rows[{criterion}]", f"{n} rows — a criterion is "
                                              "decided once, not voted on")
    for criterion in seen:
        if criterion not in CRITERIA:
            f.add("V2", f"rows[{criterion}]", "not an E1–E5 criterion")

    run = record.get("run") or {}
    prereg = record.get("prereg") or {}
    corpus = record.get("corpus") or {}
    prereg_sha = prereg.get("sha")
    if not (isinstance(prereg_sha, str) and _SHA_RE.match(prereg_sha or "")):
        # A table with no frozen pre-registration can hold no green row; the
        # rows themselves carry the honest verdict, so this is a note unless a
        # green row tries to lean on it (V5).
        prereg_sha = None

    by_id = {r.get("criterion"): r for r in rows if isinstance(r, dict)}

    for row in rows:
        if not isinstance(row, dict):
            f.add("V3", "rows[]", f"row is not an object: {row!r}")
            continue
        cid = row.get("criterion")
        where = f"rows[{cid}]"

        # ── V3 vocabulary + shape ─────────────────────────────────────────
        verdict = row.get("verdict")
        if verdict not in VERDICTS:
            f.add("V3", where, f"verdict {verdict!r} not in {VERDICTS} — "
                               "'unknown' is not a verdict")
        kind = row.get("evidence_kind")
        if kind not in EVIDENCE_KINDS:
            f.add("V3", where, f"evidence_kind {kind!r} not in "
                               f"{EVIDENCE_KINDS}")
        sha = row.get("sha")
        if not (isinstance(sha, str) and _SHA_RE.match(sha)):
            f.add("V3", where, f"sha {sha!r} is not a commit sha — every row "
                               "is measured AT a sha")

        # ── V4 the artifact must exist ────────────────────────────────────
        evidence = row.get("evidence")
        ev_path = None
        if not (isinstance(evidence, str) and evidence.strip()):
            f.add("V4", where, "no evidence path — a row with no artifact is "
                               "an inference, not evidence")
        else:
            ev_path = Path(evidence)
            if ev_path.is_absolute() or ".." in ev_path.parts:
                f.add("V4", where, f"evidence path {evidence!r} must be "
                                   "repo-relative with no escape")
                ev_path = None
            elif not (repo / ev_path).is_file():
                f.add("V4", where, f"evidence path {evidence!r} does not exist "
                                   "— a green row on a missing file is the "
                                   "defect this gate exists for")

        num, den = row.get("numerator"), row.get("denominator")

        # ── V5 green means measured ───────────────────────────────────────
        if verdict == "GREEN":
            if kind != "measurement":
                f.add("V5", where, "GREEN requires evidence_kind='measurement' "
                                   "— a refusal record cannot be green")
            if not isinstance(num, int) or not isinstance(den, int):
                f.add("V5", where, "GREEN requires integer numerator and "
                                   "denominator")
            elif den <= 0:
                f.add("V5", where, f"denominator is {den} — a green row with an "
                                   "empty denominator measures nothing")
            elif not 0 <= num <= den:
                f.add("V5", where, f"numerator {num} outside 0..{den}")
            if row.get("blocked_by"):
                f.add("V5", where, f"GREEN carries a blocker "
                                   f"({row.get('blocked_by')!r}) — green and "
                                   "blocked are mutually exclusive")
            if run.get("status") != "COMPLETED":
                f.add("V5", where, f"GREEN with run.status="
                                   f"{run.get('status')!r} — every row comes "
                                   "from ONE completed pass")
            observed = tuple(run.get("steps_observed") or ())
            missing = [s for s in STEPS if s not in observed]
            if missing:
                f.add("V5", where, "GREEN with steps not observed in the run: "
                                   f"{', '.join(missing)} — the epic measures "
                                   "the pipeline end to end, not a step probe")
            if sha and isinstance(sha, str) and _SHA_RE.match(sha):
                if not _is_git_repo(repo):
                    f.add("V5", where, "cannot verify the evidence sha: not a "
                                       "git work tree")
                elif not _resolves(repo, sha):
                    f.add("V5", where, f"evidence sha {sha} does not resolve "
                                       "in this repo")
                elif prereg_sha is None:
                    f.add("V5", where, "GREEN with no frozen pre-registration "
                                       "sha — the verdict would be measured "
                                       "against a threshold nobody fixed "
                                       "first (#9075)")
                elif not _is_ancestor(repo, prereg_sha, sha):
                    f.add("V5", where, "evidence PREDATES the pre-registration "
                                       f"({sha} is not a descendant of "
                                       f"{prereg_sha}) — evidence cannot "
                                       "validate thresholds it was measured "
                                       "before")

        # ── V6 non-green names its blocker ────────────────────────────────
        else:
            blocker = row.get("blocked_by")
            if not (isinstance(blocker, str) and _BLOCKER_RE.match(blocker)):
                f.add("V6", where, f"verdict {verdict!r} must name the blocking "
                                   f"issue as '#NNN' (got {blocker!r}) — the "
                                   "epic asks for a red row WITH its cause")
            if kind == "refusal" and ev_path is not None:
                # A refusal row cites the RUN LOG that records the refusal.
                # Checked by CONTENT rather than by path equality so the log
                # stays relocatable (the acceptance command pipes it through
                # `tee`): the cited file must itself be a capstone log that
                # carries THIS row. A prose file, a README, or another lane's
                # artifact is not a refusal record, and citing one would put a
                # measurement's clothes on a refusal.
                try:
                    cited = extract_block((repo / ev_path).read_text())
                    cited_ids = {r.get("criterion") for r in cited.get("rows") or []
                                 if isinstance(r, dict)}
                except (OSError, ValueError, json.JSONDecodeError):
                    f.add("V6", where, f"evidence_kind='refusal' must cite a "
                                       f"capstone log; {evidence!r} is not one")
                else:
                    if cid not in cited_ids:
                        f.add("V6", where, f"the cited log {evidence!r} does "
                                           f"not carry a {cid} row")

        # ── V7 one corpus ─────────────────────────────────────────────────
        row_corpus = row.get("corpus_sha256")
        if verdict in ("GREEN", "RED"):
            if not row_corpus:
                f.add("V7", where, "a measured row must carry corpus_sha256 — "
                                   "the before/after denominators have to be "
                                   "the same corpus")
            elif corpus.get("sha256") and row_corpus != corpus.get("sha256"):
                f.add("V7", where, f"corpus_sha256 {row_corpus!r} != the log's "
                                   f"{corpus.get('sha256')!r} — two corpora "
                                   "cannot share one verdict")
        elif row_corpus and corpus.get("sha256") and \
                row_corpus != corpus.get("sha256"):
            f.add("V7", where, "corpus_sha256 disagrees with the log's corpus")

    # ── V8 denominators carried, not restated ────────────────────────────
    for cid, row in by_id.items():
        sib = row.get("sibling")
        if not sib:
            continue
        other = by_id.get(sib)
        if other is None:
            f.add("V8", f"rows[{cid}]", f"sibling {sib!r} is not a row")
            continue
        if (row.get("numerator"), row.get("denominator")) != \
                (other.get("numerator"), other.get("denominator")):
            f.add("V8", f"rows[{cid}]",
                  f"denominators disagree with sibling {sib}: "
                  f"{row.get('numerator')}/{row.get('denominator')} vs "
                  f"{other.get('numerator')}/{other.get('denominator')}")

    # ── V9 the epic's closing rule ───────────────────────────────────────
    if require_green:
        red = [c for c in CRITERIA
               if (by_id.get(c) or {}).get("verdict") != "GREEN"]
        if red:
            f.add("V9", "epic #7949", f"--require-green: {len(red)}/5 rows are "
                                      f"not green ({', '.join(red)}) — the "
                                      "epic is NOT closeable")

    return f


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check the extractor-v4 capstone verdict table (#9080).")
    ap.add_argument("log", help="the capstone log (capstone_run.py output)")
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    ap.add_argument("--require-green", action="store_true",
                    help="the EPIC's closing rule: exit 0 only when 5/5 rows "
                         "are green (without this, an honest all-blocked table "
                         "is clean)")
    args = ap.parse_args(argv)

    log_path = Path(args.log).resolve()
    try:
        text = log_path.read_text()
    except OSError as e:
        print(f"capstone-verdict-check: cannot read {args.log}: {e}",
              file=sys.stderr)
        return 2
    try:
        record = extract_block(text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"capstone-verdict-check: {args.log}: {e}", file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve()
    findings = check(record, log_path=log_path, repo=repo,
                     require_green=args.require_green)
    rows = record.get("rows") or []
    tally = {v: sum(1 for r in rows if isinstance(r, dict)
                    and r.get("verdict") == v) for v in VERDICTS}
    print(f"capstone-verdict-check: {args.log} — "
          + ", ".join(f"{v}={tally[v]}" for v in VERDICTS))
    if findings:
        print(f"❌ {len(findings.items)} finding(s):", file=sys.stderr)
        print(findings.render(), file=sys.stderr)
        return 1
    print("✅ verdict table is structurally sound: 5/5 rows carry an evidence "
          "artifact + sha, denominators agree, no row is green without a file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
