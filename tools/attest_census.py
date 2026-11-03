#!/usr/bin/env python3
"""attest_census.py — one read answers "does this PR's code-review proof exist, and
does it describe the code that is actually on the PR right now?"

THE QUESTION THIS EXISTS FOR
----------------------------
The `ai-review-gate` required check reds with "No AI review evidence found in PR
#N body or comments." That message has two very different causes and they must
never be confused:

  * REPAIR — a review really happened, a record really exists on a box
    (`~/.pi/agent/reviews/<owner>-<repo>-<pr>.json`) bound to the PR's CURRENT
    head, and only its published copy is missing (a 403 refused the post). The
    proof can be re-posted verbatim.
  * FABRICATION — no record, or a record bound to a head the code has since
    moved past. There is nothing to re-post: the honest answer is a fresh review
    (re-review the DELTA), never a copied verdict.

This tool decides which, mechanically, from three facts only:

  1. record exists?                       (canonical path, then the legacy `<pr>.json`)
  2. record.head_sha == PR headRefOid?    (byte-for-byte, 40 lowercase hex)
  3. record carries a REAL verdict with ARTIFACT-SHAPED evidence — `verdict` in the clean
     allow-list (`clean` / `clean-micro` / `clean-low`), `evidence` matching `_EVIDENCE_REF`
     (`<kind>:<digits>` or a GitHub `#issuecomment-`/`#discussion_r`/`#pullrequestreview-` URL),
     and `mint` not carried.

     ⛔ Not "a non-empty string": that weaker rule is exactly what cycle 5 measured as fail-open
     (`"TODO: verify"`, `"evidence: none"`, `" : "` all licensed `RESTORE` for a record with no
     artifact at all). Weakening `real_evidence` back to a non-empty check re-opens fabrication.

BINDING IS BY HEAD, DELIBERATELY NARROWER THAN THE GATE
-------------------------------------------------------
`ai-review-gate` also accepts a marker bound to the current **diff** digest (#1351), so a record at
an older head whose diff still matches can still satisfy the gate. This census does not: its job is
"does a verdict describe the code that is live now", and the safe direction is `RE-REVIEW` (pay for
a review) rather than `RESTORE` (re-publish an unmatched verdict). Rows may therefore UNDER-report
restorable rows — never over-report them.

⛔ It never mints, never writes a record, and never posts anything. It is a
READER. A `RESTORE` row means "the artifact you would re-post already exists and
describes the live head" — a human or lane still has to do the posting, and must
quote the record's own verdict verbatim.

WHY THE GATE STATE IS READ PER-COMMIT, NOT FROM THE ROLLUP
----------------------------------------------------------
`gh pr list --json statusCheckRollup` looks cheaper (one call) but is the wrong
instrument twice over: a re-run ADDS a red attempt rather than clearing one, so
the rollup of a green commit can carry old `FAILURE` contexts; and the newest
attempt per check NAME is only resolvable by check-run `id`. So the gate state is
read the way AGENTS.md §"Reading verification state" says to read it — newest
attempt by id, over `filter=all` — scoped with `check_name=` so the payload stays
small enough for a rate-limited box. `--no-gate` drops only the PER-PR check-run reads (the
expensive half); `gh pr list` is still required, so a fully offline run produces no report.

USAGE
-----
    uv run python tools/attest_census.py                    # tortoise open PRs → operations/attest-census.md
    uv run python tools/attest_census.py --no-gate          # registry only (no API calls but `gh pr list`)
    uv run python tools/attest_census.py --json             # machine-readable rows on stdout
    uv run python tools/attest_census.py --repo daniel-ospina/agent-infra

Exit code is 0 for a completed census (this is a report, not a gate) and 2 when a
surface could not be read — never 1, so a caller cannot mistake "unreadable" for
"found a problem".
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/attest_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/attest_census.py`"
    )

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GATE_CHECK = "ai-review-gate"

# Verdicts that are a *clean* claim. Kept as a set rather than a substring test:
# "NOT CLEAN" contains "CLEAN", and this file exists to refuse exactly this kind
# of vocabulary accident.
CLEAN_VERDICTS = {"clean", "clean-micro", "clean-low"}


def _is_carried(mint: str) -> bool:
    """Normalised, because this guard is FAIL-OPEN if it ever misses.

    Measured by review cycle 3: `mint` = `"Carried"` / `"CARRIED"` / `" carried"` / `"carried "`
    all reached `RESTORE` — a restorable proof manufactured from a pointer to ANOTHER review
    (#10177). Everything else in this file normalises; this did not.
    """
    return mint.strip().lower() == "carried"


# An evidence reference is an ARTIFACT POINTER, not prose: `<kind>:<numeric id>` or a GitHub URL
# carrying one. Cycle 5 measured that the previous test (`":" in evidence`) accepted
# `"TODO: verify"` / `"evidence: none"` / `" : "` as evidence and licensed `RESTORE` for a record
# with no artifact at all — the docstring's rule, unimplemented.
_EVIDENCE_REF = re.compile(
    r"^(?:comment|issuecomment|review|review-comment|discussion):\d+$"
    # ⛔ github.com ONLY: the docstring promises a GitHub artifact, and cycle 9 measured
    # `https://example.com/#issuecomment-9` (and bitbucket/localhost) licensing RESTORE — a pointer
    # whose SHAPE was checked but which resolves to no artifact on any repo.
    r"|^https?://(?:www\.)?github\.com/[^#\s]+/[^#\s]+#(?:issuecomment-|pullrequestreview-|discussion_r)\d+$"
)

RESTORE = "RESTORE"
RE_REVIEW = "RE-REVIEW"
NOTHING = "NOTHING-TO-RESTORE"
OK = "OK"
IN_FLIGHT = "IN-FLIGHT"
UNREADABLE = "UNREADABLE"


class RegistryUnreadable(RuntimeError):
    """The registry DIRECTORY could not be listed.

    ⛔ This must never be folded into "no record": a missing or unreadable
    `--reviews-dir` makes every PR look unrestorable, which is the same
    absence-as-verdict error the census exists to prevent, at directory scale.
    """



@dataclass
class Record:
    path: Path
    head_sha: str = ""
    mint: str = ""
    verdict: str = ""
    evidence: object = None
    diff_sha256: str = ""
    reviewed_at: str = ""
    error: str = ""

    @property
    def real_evidence(self) -> bool:
        """The record attests that a reviewer actually reported something.

        Requires ALL of: a verdict in the clean allow-list, an evidence reference that is an
        ARTIFACT SHAPE (`<kind>:<id>` or a GitHub URL carrying one), and `mint` not carried. The
        mint exclusion is deliberate: #10177 measured a carry re-binding an older `clean` onto a
        head whose own review found a P1 — a carried record is a *pointer to another review*, and
        re-posting it as proof of this head is the laundering this tool exists to refuse.

        The match is `_EVIDENCE_REF`, not a bare colon test: cycle 5 measured `"TODO: verify"`,
        `"evidence: none"` and `" : "` passing a colon check and licensing RESTORE for a record
        with no artifact at all (a stringified empty like `"null"`/`"n/a"` is colon-free, so this
        rule also subsumes the null-ish cases without a separate list that can never fire).
        """
        return (
            bool(self.verdict)
            and self.verdict in CLEAN_VERDICTS
            and isinstance(self.evidence, str)
            and _EVIDENCE_REF.match(self.evidence.strip()) is not None
            and not _is_carried(self.mint)
        )


@dataclass
class Row:
    pr: int
    head: str
    branch: str
    title: str
    record: Record | None
    gate_status: str = ""
    gate_conclusion: str = ""
    gate_checked: bool = False
    outcome: str = ""
    why: str = ""
    notes: list[str] = field(default_factory=list)


# ── pure classifier (unit-tested) ────────────────────────────────────────────


def classify(
    head: str, record: Record | None, gate_status: str, gate_conclusion: str, gate_known: bool = True
) -> tuple[str, str]:
    """Return (outcome, why) for one PR. No I/O — this is the whole rule.

    Precedence, and why: a record we could not READ or could not TIE to a head is
    `UNREADABLE`, never `NOTHING-TO-RESTORE` — the latter asserts "there is no
    artifact", and a failed read is not evidence of absence.
    """
    gate_green = gate_known and gate_conclusion in ("success", "neutral", "skipped")
    gate_in_flight = gate_known and gate_status not in ("", "completed")

    if record is not None and record.error:
        return UNREADABLE, f"record {record.path.name} could not be used: {record.error}"

    if record is None:
        if gate_green:
            return OK, "gate green; no local record (evidence lives on GitHub only)"
        if gate_in_flight:
            return IN_FLIGHT, "gate has not concluded"
        # The marker half of the story depends on what was ACTUALLY read, so `main` appends it.
        return NOTHING, "no record on this box at any head"

    rec_head = record.head_sha
    if not SHA_RE.match(rec_head or ""):
        return UNREADABLE, (
            f"record {record.path.name} carries no usable head_sha ({rec_head!r}) — it cannot be "
            "bound to this head, so it is neither restorable nor provably absent"
        )
    if rec_head == head and record.real_evidence:
        if gate_green:
            return OK, f"record binds live head and carries {record.verdict}/{record.evidence}"
        if gate_in_flight:
            return IN_FLIGHT, "record binds live head; gate has not concluded"
        return RESTORE, (
            f"record binds the live head with {record.verdict} + evidence {record.evidence} — "
            "the artifact exists; re-post its own verdict verbatim"
        )

    if rec_head and rec_head != head:
        if gate_green:
            return OK, f"gate green (record is stale at {rec_head[:12]}…, irrelevant)"
        return RE_REVIEW, f"record binds {rec_head[:12]}…, PR head is {head[:12]}… — the code moved"

    # Record exists, head matches (or is unreadable), but the verdict/evidence
    # is not a review of this code.
    if gate_green:
        return OK, "gate green"
    if gate_in_flight:
        return IN_FLIGHT, "gate has not concluded"
    if _is_carried(record.mint):
        return RE_REVIEW, "record is mint:\"carried\" — a pointer to an older review, not evidence of this head (#10177)"
    if record.evidence in (None, "", False):
        return NOTHING, "record carries no evidence (evidence: null) — nothing to restore"
    # ⛔ Name the ACTUAL defect: a clean verdict with an unusable pointer is not "a non-clean
    # verdict" (cycle 6: the row asserted the opposite of its own `verdict` column, so a lane
    # concluded the review had found issues and re-reviewed for the wrong reason).
    if not isinstance(record.evidence, str) or _EVIDENCE_REF.match(record.evidence.strip()) is None:
        # ⛔ Do NOT assert a verdict here: this branch fires on the EVIDENCE test, and cycle 7
        # measured the claim "is clean" printed beside `verdict=ISSUES` — which sends a lane to
        # repair a pointer on a review that actually found issues, without reading the findings.
        return NOTHING, (
            f"record verdict {record.verdict!r} at the live head, but its evidence "
            f"({record.evidence!r}) is not an artifact pointer — it cannot be quoted as proof"
        )
    return NOTHING, f"record verdict {record.verdict!r} is not a clean verdict — nothing to restore"


# ── readers ──────────────────────────────────────────────────────────────────


def load_record(
    reviews_dir: Path, owner: str, repo: str, pr: int
) -> Record | None:
    """Find this PR's record, or None when the registry genuinely has none.

    ⛔ Three things this must never do, each a measured defect:
      * fold an UNREADABLE registry into "no record" — raises `RegistryUnreadable`;
      * fold a CORRUPT record into "no record" — returns a Record carrying `.error`
        (which `classify` maps to `UNREADABLE`, never `NOTHING-TO-RESTORE`);
      * accept another repo's/PR's record — a record whose own `repo`/`pr` fields
        disagree is returned with `.error`, so it can never become evidence.
    Filenames are matched CASE-INSENSITIVELY: `gh` treats `DMeer` and `dmeer` as the
    same repo while the registry path is case-sensitive, so an exact-case lookup
    reports a false absence (measured on `daniel-ospina-DMeer-*.json`).
    """
    try:
        entries = [e for e in reviews_dir.iterdir() if e.name.endswith(".json")]
    except FileNotFoundError as exc:
        raise RegistryUnreadable(f"registry directory does not exist: {reviews_dir}") from exc
    except OSError as exc:
        raise RegistryUnreadable(f"registry directory cannot be listed: {reviews_dir} ({exc})") from exc

    wanted = [f"{owner}-{repo}-{pr}.json".lower(), f"{pr}.json"]
    chosen: Path | None = None
    canonical = False
    for idx, want in enumerate(wanted):
        for entry in entries:
            if entry.name.lower() == want:
                chosen = entry
                canonical = idx == 0
                break
        if chosen is not None:
            break
    if chosen is None:
        return None

    try:
        raw = chosen.read_text()
    except (OSError, ValueError) as exc:
        # OSError: permission denied, dangling link, a DIRECTORY named *.json.
        # ValueError: invalid UTF-8 (`UnicodeDecodeError`) — cycle 3 measured it escaping as rc 1.
        return Record(path=chosen, error=f"unreadable: {exc}")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return Record(path=chosen, error=f"unreadable: not JSON ({exc})")
    if not isinstance(data, dict):
        return Record(path=chosen, error=f"unreadable: top-level JSON is {type(data).__name__}, not an object")

    rec = Record(
        path=chosen,
        head_sha=str(data.get("head_sha") or ""),
        mint=str(data.get("mint") or ""),
        verdict=str(data.get("verdict") or data.get("outcome") or ""),
        evidence=data.get("evidence"),
        diff_sha256=str(data.get("diff_sha256") or ""),
        reviewed_at=str(data.get("reviewed_at") or ""),
    )
    # Identity: a record names its own PR and repo; one that disagrees is another
    # artifact entirely (mirrors/forks/lineage repos collide on small PR numbers).
    # ⛔ The LEGACY `<pr>.json` name carries no repo at all, so a legacy record that does not
    # DECLARE one cannot be attributed to this repo — and an unattributable artifact that reaches
    # RESTORE would have a lane re-post `comment:<id>` from another repo's PR. Under-report
    # (UNREADABLE), never over-report (cycle 4, finding 5).
    rec_repo = data.get("repo")
    declared_repo = isinstance(rec_repo, str) and rec_repo.strip() != ""
    if declared_repo and rec_repo.strip().lower() != f"{owner}/{repo}".lower():
        rec.error = f"foreign record: repo={rec_repo!r}, census repo is {owner}/{repo}"
    elif not canonical and not declared_repo:
        rec.error = (
            f"legacy record {chosen.name} declares no repo — it cannot be attributed to "
            f"{owner}/{repo}, so it is not evidence"
        )
    rec_pr = data.get("pr")
    if rec_pr is not None and str(rec_pr).strip() != str(pr):
        # NOT `isinstance(rec_pr, int)`: cycle 5 measured a record declaring `"pr":"8"` escaping
        # the check and reaching RESTORE for PR 7 — a re-post of PR 8's `comment:<id>` as PR 7's
        # proof. Any declared pr that is not this pr is foreign, whatever its JSON type.
        rec.error = f"foreign record: pr={rec_pr!r}, census pr is {pr}"
    return rec


def gh_json(args: list[str]) -> object:
    """Run `gh` and parse JSON. Every failure mode — gh missing, non-zero rc, non-JSON body —
    becomes a RuntimeError, never an uncaught OSError/ValueError: the exit-code contract
    (0/2, never 1) depends on it (cycle 3 measured `FileNotFoundError` escaping as rc 1)."""
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True)
    except OSError as exc:  # gh not installed / not executable
        raise RuntimeError(f"could not run gh ({' '.join(args[:2])}…): {exc}") from exc
    except ValueError as exc:  # a non-UTF-8 stdout body — UnicodeDecodeError is a ValueError
        raise RuntimeError(f"gh {' '.join(args[:2])}… returned undecodable output: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}… failed rc={out.returncode}: {out.stderr.strip()[:300]}")
    try:
        return json.loads(out.stdout)
    except ValueError as exc:
        raise RuntimeError(f"gh {' '.join(args[:3])}… returned non-JSON ({exc}): {out.stdout[:200]!r}") from exc


def read_gate(repo: str, head: str) -> tuple[str, str]:
    """Newest `ai-review-gate` check-run on the exact head, by id (never the rollup)."""
    payload = gh_json(
        [
            "api",
            "-X",
            "GET",
            f"repos/{repo}/commits/{head}/check-runs",
            "-f",
            f"check_name={GATE_CHECK}",
            "-f",
            "per_page=100",
            "-f",
            "filter=all",
        ]
    )
    # Shape-validate: a valid-JSON-but-wrong-payload must be a TRANSPORT verdict (RuntimeError →
    # UNREADABLE → rc 2), never an AttributeError escaping as rc 1 (cycle 3, measured five shapes).
    runs_raw = payload.get("check_runs", []) if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(runs_raw, list)
        or not all(isinstance(r, dict) for r in runs_raw)
    ):
        raise RuntimeError(f"check-runs payload for {head[:12]}… has an unexpected shape: {type(payload).__name__}")
    runs = [r for r in runs_raw if r.get("name") == GATE_CHECK]
    if not runs:
        return "", ""
    # ⛔ NEWEST BY `id`, never by list position: the endpoint answers newest-first, so
    # `runs[-1]` would read the OLDEST attempt — the rollup error requirement (c) forbids
    # (a re-run ADDS an attempt; the oldest may be a stale `cancelled`/`failure`).
    # An id that is not an integer is a payload we cannot order, so it is a TRANSPORT verdict.
    ordered = [r for r in runs if isinstance(r.get("id"), int)]
    if runs and not ordered:
        raise RuntimeError(f"check-runs payload for {head[:12]}… carries no integer id — cannot order attempts")
    newest = max(ordered, key=lambda r: r["id"])
    return str(newest.get("status") or ""), str(newest.get("conclusion") or "")


# ── render ───────────────────────────────────────────────────────────────────


def render(rows: list[Row], repo: str, generated_at: str, truncated: bool = False, limit: int = 100) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    order = [RESTORE, RE_REVIEW, NOTHING, IN_FLIGHT, OK, UNREADABLE]
    totals = " · ".join(f"{k}={counts[k]}" for k in order if counts.get(k))

    lines = [
        "# Attestation census — open PRs",
        "",
    ]
    if truncated:
        lines += [
            f"> ⛔ **TRUNCATED: the first {limit} of MORE than {limit} open PRs.** This report does NOT",
            "> cover the whole population — PRs outside this window have no row here, and their absence",
            "> is not a statement about them. Re-run with a larger `--limit`.",
            "",
        ]
    lines += [
        f"Repo `{repo}` · generated {generated_at} by `tools/attest_census.py`.",
        "",
        "> ⚠️ **SNAPSHOT, NOT A LIVE VERDICT.** This file is machine-generated from *one box's*",
        "> registry at the timestamp above, and every push, review and gate re-run invalidates it.",
        "> Re-run `uv run python tools/attest_census.py` before acting on any row — in particular,",
        "> an `OK` or `NOTHING-TO-RESTORE` row from this snapshot is **not** evidence about the",
        "> PR's current head, and `NOTHING-TO-RESTORE` never means \"nobody reviewed this\"",
        "> fleet-wide: `~/.pi/agent/reviews/` is per-machine.",
        "",
        "One row per open PR: its head, whether a review record exists on **this box**, whether that",
        "record's head equals the PR's head, what the record claims, and what the `ai-review-gate`",
        "check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the",
        "verdict is quoted from the record, never synthesised. A record at any other head is not"
        " evidence of the live code: it is `RE-REVIEW` when the gate has no green verdict, and `OK`",
        " only because the gate itself already accepted the proof (so no re-post is owed).",
        "",
        "## Census totals",
        "",
        totals,
        "",
        "| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        rec = r.record
        if rec is None:
            rec_cell = "—"
        elif rec.error == "":
            rec_cell = rec.path.name
        elif "foreign" in rec.error:
            rec_cell = f"{rec.path.name} (foreign)"
        elif "legacy" in rec.error:
            rec_cell = f"{rec.path.name} (unattributable)"
        else:
            # Only a record that could NOT be read is corrupt; the label's remedy is destructive,
            # so it must never cover an intact record that merely names another PR/repo (cycle 9).
            rec_cell = f"{rec.path.name} (corrupt)"
        rhead = "—"
        match = "—"
        if rec is not None:
            rhead = (rec.head_sha[:12] + "…") if rec.head_sha else "—"
            match = "yes" if rec.head_sha == r.head else ("no" if rec.head_sha else "—")
        mint = "—" if rec is None else (rec.mint or "—")
        verdict = "—" if rec is None else (rec.verdict or "—")
        ev = "—" if rec is None else ("null" if rec.evidence in (None, "", False) else str(rec.evidence))
        gate = "not-read" if not r.gate_checked else (r.gate_conclusion or r.gate_status or "absent")
        lines.append(
            f"| #{r.pr} | `{r.head[:12]}…` | {rec_cell} | {rhead} | {match} | {mint} | {verdict} | {ev} | {gate} | **{r.outcome}** |"
        )

    flagged = [r for r in rows if r.outcome in (RESTORE, RE_REVIEW, NOTHING, UNREADABLE, IN_FLIGHT)]
    if flagged:
        lines += ["", "## What each flagged row needs", ""]
        for r in flagged:
            lines.append(f"- **#{r.pr}** `{r.branch}` — {r.outcome}: {r.why}")
            for n in r.notes:
                lines.append(f"  - {n}")
    lines += [
        "",
        "## Reading rules",
        "",
        "- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.",
        "- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.",
        "- `mint:\"carried\"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`",
        "  onto a head whose own review had found a P1).",
        "- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are",
        "  this box's view, and the census says so rather than implying a fleet-wide clean bill.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="daniel-ospina/tortoise")
    ap.add_argument("--reviews-dir", default=str(Path.home() / ".pi/agent/reviews"))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--no-gate", action="store_true", help="skip the per-PR check-run read (gh pr list is still required)")
    ap.add_argument("--json", action="store_true", help="machine-readable rows on stdout instead of markdown")
    ap.add_argument("--out", default="operations/attest-census.md")
    args = ap.parse_args(argv)

    owner, _, name = args.repo.partition("/")
    try:
        reviews_dir = Path(args.reviews_dir).expanduser()
    except RuntimeError as exc:  # `~nosuchuser` — expanduser raises, and that must not be rc 1
        print(f"attest_census: cannot resolve --reviews-dir {args.reviews_dir!r}: {exc}", file=sys.stderr)
        return 2
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        prs = gh_json(
            ["pr", "list", "--repo", args.repo, "--state", "open", "--limit", str(args.limit + 1),
             "--json", "number,headRefOid,headRefName,title"]
        )
    except RuntimeError as exc:
        print(f"attest_census: could not list PRs: {exc}", file=sys.stderr)
        return 2
    if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
        print("attest_census: `gh pr list` returned an unexpected payload — refusing to census it", file=sys.stderr)
        return 2

    # ⛔ Assert the REGISTRY is readable BEFORE classifying anything: an unlistable
    # `--reviews-dir` would otherwise render every PR as "no record", i.e. report a
    # whole-registry read failure as "nothing to restore" for the entire fleet.
    try:
        load_record(reviews_dir, owner, name, 0)
    except RegistryUnreadable as exc:
        print(f"attest_census: {exc} — refusing to report absence from an unreadable registry", file=sys.stderr)
        return 2

    truncated = len(prs) > args.limit
    if truncated:
        prs = prs[: args.limit]

    rows: list[Row] = []
    unreadable = 0
    for pr in prs:
        if not isinstance(pr.get("number"), int):
            print(f"attest_census: `gh pr list` row has no integer number ({pr!r}) — refusing", file=sys.stderr)
            return 2
        head = str(pr.get("headRefOid") or "")
        try:
            rec = load_record(reviews_dir, owner, name, int(pr["number"]))
        except RegistryUnreadable as exc:  # registry became unlistable mid-run (race)
            print(f"attest_census: {exc} — refusing to report absence", file=sys.stderr)
            return 2
        row = Row(
            pr=int(pr["number"]),
            head=head,
            branch=str(pr.get("headRefName") or ""),
            title=str(pr.get("title") or ""),
            record=rec,
        )
        if not SHA_RE.match(head):
            row.outcome, row.why = UNREADABLE, f"PR head {head!r} is not a 40-char sha"
        elif args.no_gate:
            row.outcome, row.why = classify(head, rec, "", "", gate_known=False)
            # ⛔ Only the GITHUB half of the sentence may be replaced, and only when there is no
            # record at all: a record-present NOTHING row has its own reason (evidence-less /
            # non-clean verdict) and overwriting it would contradict the same row's own table
            # columns (measured by review cycle 3).
            if row.outcome == NOTHING and rec is None:
                row.why = (
                    "no record on this box at any head; the published-marker surface was NOT "
                    "read (--no-gate), so this is not a statement about GitHub"
                )
            elif row.outcome != NOTHING:
                row.why += " (gate NOT read: --no-gate)"
        else:
            try:
                row.gate_status, row.gate_conclusion = read_gate(args.repo, head)
                row.gate_checked = True
            except RuntimeError as exc:
                row.outcome, row.why = UNREADABLE, f"check-run read failed: {exc}"
        if row.gate_checked:
            row.outcome, row.why = classify(head, rec, row.gate_status, row.gate_conclusion)
            # ⛔ The marker clause is a claim about a surface the GATE reads. Only a gate that
            # actually RAN AND REPORTED can support it: an absent check-run, or a non-red,
            # non-green attempt (`cancelled`/`stale`), is not a read of the marker surface.
            if row.outcome == NOTHING and rec is None:
                # ⛔ A RED CONCLUSION DOES NOT PROVE THE MARKER SURFACE WAS READ. The gate fails
                # before reading any marker when its key is unset, when every candidate marker
                # fails HMAC, when the comment fetch 403s, and when the diff hash cannot be
                # computed (each says so in its own annotation). So state what the run knows:
                # the gate is red, and its own annotation is the thing to read next (cycle 6).
                gate_red = row.gate_conclusion in ("failure", "timed_out", "action_required")
                row.why += (
                    f"; the gate concluded {row.gate_conclusion} — whether a published marker exists is "
                    "answered by the gate's own annotation, not by this census"
                    if gate_red
                    else f"; the gate reported nothing readable on this head ({row.gate_conclusion or 'no run'}), "
                    "so the published-marker surface was not judged"
                )
        # ⛔ ONE increment site. A row the tool itself calls UNREADABLE is a surface it could
        # not read, so the run must not report 0 ("complete census") while displaying one.
        # (A second increment in the bad-head branch made the rc-2 test pass even when this
        # line was deleted — measured by review cycle 2.)
        if row.outcome == UNREADABLE:
            unreadable += 1
        if rec is not None and rec.head_sha and len(rec.head_sha) != 40:
            row.notes.append(f"record head_sha {rec.head_sha!r} is not a 40-char sha — not bindable")
        rows.append(row)

    rows.sort(key=lambda r: r.pr, reverse=True)

    if args.json:
        print(json.dumps({
            "repo": args.repo,
            "generated_at": generated_at,
            "reviews_dir": str(reviews_dir),
            "truncated": truncated,
            "limit": args.limit,
            "rows": [
                {
                    "pr": r.pr, "head": r.head, "branch": r.branch, "outcome": r.outcome, "why": r.why,
                    "gate_status": r.gate_status, "gate_conclusion": r.gate_conclusion, "gate_checked": r.gate_checked,
                    "record": None if r.record is None else {
                        "path": str(r.record.path), "head_sha": r.record.head_sha, "mint": r.record.mint,
                        "verdict": r.record.verdict, "evidence": r.record.evidence,
                        "real_evidence": r.record.real_evidence,
                    },
                }
                for r in rows
            ],
        }, indent=2))
        return 0 if unreadable == 0 else 2

    text = render(rows, args.repo, generated_at, truncated=truncated, limit=args.limit)
    try:
        out = Path(args.out).expanduser()
    except RuntimeError as exc:  # symmetric with --reviews-dir: an unresolvable ~ is rc 2, not rc 1
        print(f"attest_census: cannot resolve --out {args.out!r}: {exc}", file=sys.stderr)
        return 2
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    except OSError as exc:  # unwritable --out is a surface we could not write, not a finding
        print(f"attest_census: could not write {out}: {exc}", file=sys.stderr)
        return 2
    print(text)
    print(f"attest_census: wrote {out}", file=sys.stderr)
    return 0 if unreadable == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
