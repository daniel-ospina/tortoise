#!/usr/bin/env python3
"""closes_landing — a `Closes #N` on a PR whose base is NOT the default branch is
INERT: it closes nothing and lands nothing, while every surface says it did
(#10597).

Why this exists
---------------
GitHub interprets a closing keyword (`Closes`/`Fixes`/`Resolves #N`) **only when
the pull request targets the repository's default branch**
(https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue).
When the base is another lane's branch — a stacked PR, which is the *correct* way
to keep a reviewable, non-overlapping diff — the keyword is ignored. Two things
then go wrong at once, and neither is visible from outside:

1. the issue is never closed. It keeps a `Closes` in its timeline and stays
   `open`, so the next reader sees it as unstarted work;
2. the work is not on the default branch. It lives on the sibling branch and
   only lands when *that* branch's own PR merges — and that PR may carry no
   closing keyword at all.

Measured on 2026-11-02 (#10597, re-measured at `origin/main`
`63981d1529e1c2dbea0db3ffe22f675bfd9e32d7`): `PR #10568` (base `act-10465`,
body `Closes #10541`) merged at 14:07:51Z, `merge_commit_sha`
`ebea1fe84f193a78009d8faa6bf4bead345499db` — which is **not** an ancestor of
main — while `#10541` is still `open` with `closed_at: null`. `act-10465` is the
head of `PR #10519`, whose base IS `main` and whose body has **no** `Closes
#10541`. So `#10541`'s work rides `#10519` to main and the issue stays open
anyway: two lanes of work, and the issue looks unstarted.

What this tool answers
----------------------
The question no existing surface asked: **for every closing keyword in this PR's
body, which PR will actually carry it to the default branch, and does THAT PR
carry the same keyword?**

* base is the default branch            -> `lands`     (GitHub fires on merge)
* base is a sibling branch, and the PR that terminates the stack carries the
  same closing ref                      -> `lands`     (it rides in and closes)
* base is a sibling branch, and the terminating PR does NOT carry it
                                        -> `inert`     (nothing will close it)
* the base branch has no PR at all      -> `unresolved` (cannot be measured)

`unresolved` is deliberately NOT `lands` and NOT `inert`: a base branch with no
PR is a stack whose carrier has not been opened yet, and calling that a pass is
how the silent case was manufactured in the first place.

The check walks the WHOLE stack (`a <- b <- c`), not one hop: the keyword only
fires on the PR whose base is the default branch, so a two-hop chain with the
keyword on the middle PR is just as inert as a one-hop one.

Scope — what this is NOT
------------------------
It is a **warning**, not a gate (#10597: "Not a new gate"). Stacking is
legitimate and blocking a legitimately-stacked PR would be worse than the defect;
so the caller decides what to do with exit 3. `--annotate` emits plain GitHub
Actions `::warning` lines for a warn-only workflow step, and the JSON output is
for anything that wants to reason instead of read prose.

It never writes: no comments, no labels, no closures. Reading is the whole
surface.

Usage
-----
    uv run python tools/closes_landing.py --pr 10568                # one PR
    uv run python tools/closes_landing.py --sweep                   # every open PR on a non-default base
    uv run python tools/closes_landing.py --pr 10568 --annotate     # ::warning:: lines
    uv run python tools/closes_landing.py --body-file body.md --base act-10465 \
        --carrier-body-file carrier.md          # offline: no network at all

Exit codes: 0 = nothing inert · 3 = at least one inert OR unresolved closing
ref · 2 = the question could not be measured at all (gh/API failed).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.12-only construct would fail first, with an error that blames the import
# rather than the interpreter (the D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/closes_landing.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/closes_landing.py`"
    )

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

# ── core: pure, network-free, and the part the tests drive ────────────────────

# GitHub's closing keywords, in the spelling that actually fires:
#   close/closes/closed · fix/fixes/fixed (and the common "fixe"? no) ·
#   resolve/resolves/resolved
# The optional `:`, one space, and the optional `owner/repo` prefix are GitHub's
# own accepted forms.
_CLOSING_RE = re.compile(
    r"(?i)\b(?P<keyword>close[sd]?|fix(?:es|ed)?|resolve[sd]?)"
    r"\s*:?\s*"
    r"(?:(?P<slug>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?"
    r"#(?P<number>\d+)\b"
)

_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

MAX_STACK_DEPTH = 20


@dataclass(frozen=True)
class PullRequest:
    """The four fields the question needs. Anything else is not read."""

    number: int
    base: str
    body: str = ""
    head: str = ""
    state: str = "OPEN"
    merged_at: str | None = None

    @property
    def merged(self) -> bool:
        return bool(self.merged_at) or self.state.upper() == "MERGED"

    @property
    def closed_unmerged(self) -> bool:
        """Closed WITHOUT ever merging. Such a PR can never carry anything, so
        it must not be confused with an open one — see `_pick_carrier`."""
        return self.state.upper() == "CLOSED" and not self.merged


@dataclass(frozen=True)
class Finding:
    issue: int
    pr: int
    base: str
    verdict: str  # lands | inert | unresolved
    carrier: int | None
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)

    def annotation(self) -> str:
        """A GitHub Actions `::warning` line. Newlines are percent-escaped
        because an annotation is ONE line — a raw newline truncates it."""
        msg = self.detail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        # The TITLE states the actual verdict: `unresolved` is not `inert` (it is
        # "could not be measured"), and the title is the only text a reader sees
        # before opening the log.
        return (f"::warning title={self.verdict.capitalize()} Closes "
                f"#{self.issue}::{msg}")


_CONTAINER_PREFIX_RE = re.compile(r"^(?:\s*>\s?)*")


def _fence_run(line: str) -> tuple[str, int] | None:
    """The (character, length) of a leading ```/`~~~` fence run, if any.

    Blockquote prefixes (`> `, `> > `) are seen through: a fenced block inside a
    quote is still a fence, and `_fence_run` not seeing it made the block's
    contents look like prose (review c4-5, second shape).
    """
    body = _CONTAINER_PREFIX_RE.sub("", line)
    m = re.match(r"(`{3,}|~{3,})", body.lstrip())
    return (m.group(1)[0], len(m.group(1))) if m else None


def strip_code(body: str, strict: bool = False) -> str:
    """Blank out the parts of a PR body that are CODE, not prose.

    ONLY fenced blocks are stripped. An INDENTED block is deliberately NOT: where
    a 4-space line is code and where it is a list item's continuation or a
    multi-paragraph bullet depends on the enclosing container's content column,
    and getting that wrong in the stripping direction loses a REAL claim — a
    false pass, the worst class here (two review cycles found it: first every
    indented line, then every indented line after a blank line). A warn-only
    instrument loses nothing by over-reporting a quoted keyword; it loses the
    reader by under-reporting a real one.

    Fences: a block opens with ``` or `~~~` and closes with the SAME character
    and a run at least as long.

    `strict` is for the CARRIER side, and for the arm that would otherwise report
    `lands`. There the cost of leniency is asymmetric: a keyword that appears
    only inside an indented block would buy a PASS, which is the one outcome this
    module's doctrine forbids. Strict mode therefore also blanks INDENTED lines
    and sees through blockquote prefixes when looking for a fence.

    What it does NOT do, deliberately: a keyword in blockquote PROSE (`> Closes
    #N`) stays a claim. GitHub acts on a closing keyword in a quoted paragraph —
    only CODE is ignored — so blanking it would be the under-reporting direction
    that loses real closures.

    The concrete cost of strict mode: a carrier that restates the keyword in a
    4-space bullet (`This PR:\n\n    - Closes #N`) reads `inert`. That is the
    safe direction (an extra warning, never a pass) and is accepted.
    """
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in body.splitlines():
        run = _fence_run(line)
        if strict and (line.startswith("    ") or line.startswith("\t")):
            out.append("")
            continue
        if fence is not None:
            if run and run[0] == fence[0] and run[1] >= fence[1]:
                fence = None
            out.append("")
            continue
        if run:
            fence = run
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def parse_closing_refs(body: str, repo: str | None = None,
                       strict: bool = False) -> list[int]:
    """Issue numbers this body claims to close, in body order, deduped.

    Code spans and fenced blocks (``` and `~~~`) are stripped first, because a
    keyword inside one is a *quotation* of a keyword, not a claim (GitHub does
    not act on it either, so treating it as a claim would manufacture a finding).
    Indented blocks are stripped ONLY when `strict` — see `strip_code`. Two
    callers use it, and both for the same reason (a lenient-only match there
    would be a PASS): the CARRIER body when asking whether it restates the
    keyword, and the subject's OWN `lands` arm in `classify` (which refuses to
    pass on a ref that survives only the lenient parse). Finding arms stay
    lenient, where the cost of leniency is a false warning, not a pass.

    `repo` (``owner/name``) decides what an `owner/repo#N` reference means. A
    slug naming a DIFFERENT repo is a closure elsewhere, not a claim about an
    issue here — but a slug naming THIS repo is exactly as much a claim as the
    bare `#N` form, and dropping it would be a false pass by non-detection
    (#10597 review P2-3). With no `repo`, a slug is treated as foreign: the
    function cannot tell, and inventing a finding is worse than missing one.
    """
    text = _INLINE_CODE_RE.sub(" ", strip_code(body or "", strict))
    seen: list[int] = []
    for m in _CLOSING_RE.finditer(text):
        slug = m.group("slug")
        if slug and (repo is None or slug.lower() != repo.lower()):
            continue
        n = int(m.group("number"))
        if n not in seen:
            seen.append(n)
    return seen


def resolve_carrier(pr: PullRequest, by_head: dict[str, PullRequest],
                    default_branch: str) -> PullRequest | None:
    """The PR that will actually land this stack on the default branch.

    Returns `pr` itself when it already targets the default branch. Returns
    None when the stack cannot be followed to a default-branch PR (a base
    branch with no PR, a cycle, or a stack deeper than MAX_STACK_DEPTH) — the
    caller reports that as `unresolved`, never as a pass.
    """
    seen: set[int] = {id(pr)}
    cur = pr
    for _ in range(MAX_STACK_DEPTH):
        if cur.base == default_branch:
            return cur
        nxt = by_head.get(cur.base)
        if nxt is None:
            return None
        # IDENTITY, not the PR number: the offline mode's records carry no
        # number (they are all 0), so a number comparison would report a cycle
        # on the very first hop and turn every offline judgement into
        # `unresolved`. Identity is also the correct cycle test in the live
        # mode, where every record comes from `by_head` and is therefore stable.
        if id(nxt) in seen:
            return None  # a cycle: no PR in it targets the default branch
        seen.add(id(nxt))
        cur = nxt
    return None


def classify(pr: PullRequest, by_head: dict[str, PullRequest],
             default_branch: str, repo: str | None = None) -> list[Finding]:
    """Every closing ref in `pr`'s body, judged against where it will land."""
    refs = parse_closing_refs(pr.body, repo)
    if not refs:
        return []
    carrier = resolve_carrier(pr, by_head, default_branch)
    # Only the `lands` arm needs this, and it is the SAME body for every ref, so
    # it is computed once (review c6).
    strict_refs = set(parse_closing_refs(pr.body, repo, strict=True))
    findings: list[Finding] = []
    for issue in refs:
        if carrier is None:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=None,
                detail=(f"PR #{pr.number} says `Closes #{issue}` but targets "
                        f"`{pr.base}`, and no PR with that head branch exists — "
                        f"nothing can be shown to carry #{issue} to "
                        f"`{default_branch}` yet."),
            ))
            continue
        # A SUBJECT that was CLOSED WITHOUT MERGING never fired the keyword — but
        # ONLY when its base IS the default branch: on a sibling base the question
        # is whether the CARRIER carries the keyword, which the arms below answer,
        # and claiming the subject "targets main" would be false (review c3-1).
        if pr.closed_unmerged and pr.base == default_branch:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=pr.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` and targets "
                        f"`{default_branch}`, but it was CLOSED WITHOUT "
                        f"MERGING — GitHub never fired the keyword, so "
                        f"#{issue} stays open and its work is not on "
                        f"`{default_branch}`. Re-open it, or carry the change "
                        f"(and the keyword) on the PR that does land."),
            ))
            continue
        # IDENTITY, not the PR number: the offline mode has no numbers, and a
        # number comparison would make 0 == 0 read as "this PR lands" — a false
        # pass on exactly the shape this tool exists to catch.
        if carrier is pr:
            # A PASS may not rest on a lenient-only match: `strict_refs` is the
            # same body parsed strictly, so a claim at column 0 passes and an
            # ambiguous one downgrades to a warning (review c5-2).
            if issue not in strict_refs:
                # The message must not assert WHAT GitHub thinks: the strict parse
                # differs from the lenient one only by ignoring four-space-indented
                # lines, and such a line is a CODE BLOCK in one context and a list
                # item's continuation — where GitHub DOES fire — in another (see
                # `strip_code`, and the list-item cases pinned below). So the
                # finding states what THIS tool can show, not a fact it cannot.
                findings.append(Finding(
                    issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                    carrier=pr.number,
                    detail=(f"PR #{pr.number} targets `{default_branch}`, but its "
                            f"`Closes #{issue}` survives only the LENIENT parse: a "
                            f"four-space-indented line is a code block in one "
                            f"context and a list item's continuation in another, "
                            f"so a key word on one cannot be shown to fire and is "
                            f"never counted as a pass. State `Closes #{issue}` at "
                            f"the start of a line, in plain prose."),
                ))
                continue
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="lands",
                carrier=pr.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` and targets "
                        f"`{default_branch}` — GitHub fires the keyword on merge."),
            ))
            continue
        # A CARRIER that was CLOSED WITHOUT MERGING can never carry anything.
        # Reporting `lands` here would be the worst failure this tool has: the
        # remedy it itself printed ("re-state the keyword on PR #X") produces
        # exactly this state when #X is later closed or superseded, so the tool
        # would then bless every stacked PR on that base for ever.
        if carrier.closed_unmerged:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                        f"`{pr.base}`; PR #{carrier.number} targets "
                        f"`{default_branch}` but was CLOSED WITHOUT MERGING, so "
                        f"nothing will carry #{issue} to `{default_branch}`. "
                        f"Re-state the keyword on whatever PR now targets "
                        f"`{default_branch}` for this work."),
            ))
            continue
        carried = issue in parse_closing_refs(carrier.body, repo, strict=True)
        if carried:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="lands",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                        f"`{pr.base}`; PR #{carrier.number} targets "
                        f"`{default_branch}` and carries the same keyword, so it "
                        f"closes #{issue} when it lands."),
            ))
        else:
            # The remedy depends on a state the reader cannot see from the
            # finding alone: an OPEN carrier can still be amended, a MERGED one
            # cannot — telling a lane to "re-state the keyword on" an already
            # merged PR prescribes an impossible action and hides the real one.
            if carrier.merged:
                remedy = (f"PR #{carrier.number} has ALREADY merged, so the "
                          f"work did land — only the closure did not fire. "
                          f"Close #{issue} by hand, citing PR "
                          f"#{carrier.number}.")
                lands = (f"#{issue}'s work already landed with PR "
                         f"#{carrier.number}")
            else:
                remedy = (f"Re-state the keyword on PR #{carrier.number}.")
                lands = (f"#{issue}'s work only lands when PR #{carrier.number} "
                         f"merges")
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="inert",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` but its base is "
                        f"`{pr.base}`, NOT `{default_branch}` — GitHub will NOT "
                        f"close #{issue}, and {lands}, which carries no `Closes "
                        f"#{issue}`. {remedy}"),
            ))
    return findings


def worst_verdict(findings: list[Finding]) -> str:
    """The single verdict for a set of findings, order worst-first."""
    for verdict in ("inert", "unresolved", "lands"):
        if any(f.verdict == verdict for f in findings):
            return verdict
    return "none"


# ── live layer: `gh`, no writes, each call self-describing ────────────────────

class Unmeasurable(RuntimeError):
    """The question could not be answered. Never degraded into a pass."""


_GH_FIELDS = "number,baseRefName,body,headRefName,state,mergedAt"


def _gh(args: list[str]) -> str:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             check=False)
    except FileNotFoundError as exc:
        raise Unmeasurable(f"`gh` is not installed: {exc}") from exc
    if out.returncode != 0:
        raise Unmeasurable(
            f"`gh {' '.join(args)}` exited {out.returncode}: "
            f"{(out.stderr or out.stdout).strip()[:400]}")
    return out.stdout


def _to_pr(raw: dict) -> PullRequest:
    return PullRequest(
        number=int(raw["number"]),
        base=raw.get("baseRefName") or "",
        body=raw.get("body") or "",
        head=raw.get("headRefName") or "",
        state=raw.get("state") or "OPEN",
        merged_at=raw.get("mergedAt"),
    )


def fetch_identity(repo: str | None) -> tuple[str, str]:
    """(default branch, ``owner/name``) in ONE read.

    The second half is not decoration: when `--repo` is omitted the caller has no
    repo string, and `parse_closing_refs` treats every `owner/repo#N` as foreign
    without one — so a same-repo QUALIFIED claim was silently dropped and the
    sweep reported "nothing to report" over a real offender (review cycle 2,
    finding 5). `gh repo view` already knows the name; this keeps it.

    `gh repo view` takes the repo POSITIONALLY (it has no `--repo` flag), which
    also stops the read answering for a selector it was not asked about.
    """
    raw = _gh(["repo", "view", *([repo] if repo else []), "--json",
               "defaultBranchRef,nameWithOwner"])
    try:
        payload = json.loads(raw)
        name = payload["defaultBranchRef"]["name"]
        slug = payload.get("nameWithOwner") or ""
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(f"the repo identity could not be read: {raw[:200]}") from exc
    if not name:
        raise Unmeasurable("the repo reported an empty default branch name")
    # An EMPTY slug must not degrade quietly into `None`: that would drop every
    # same-repo qualified claim as foreign, with no signal — the exact false pass
    # this read exists to remove, and the module's doctrine is Unmeasurable, never
    # a quiet pass (review c3-5).
    if not slug:
        raise Unmeasurable("the repo reported no nameWithOwner")
    return name, slug


def fetch_pr(number: int, repo: str | None) -> PullRequest:
    repo_args = ["--repo", repo] if repo else []
    raw = _gh(["pr", "view", str(number), *repo_args, "--json", _GH_FIELDS])
    try:
        return _to_pr(json.loads(raw))
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(f"PR #{number} could not be read: {raw[:200]}") from exc


def fetch_prs_by_head(branch: str, repo: str | None) -> list[PullRequest]:
    """Every PR whose HEAD is `branch` — the candidates for a stack carrier."""
    repo_args = ["--repo", repo] if repo else []
    raw = _gh(["pr", "list", *repo_args, "--state", "all", "--head", branch,
               "--json", _GH_FIELDS, "--limit", "50"])
    try:
        return [_to_pr(r) for r in json.loads(raw)]
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(
            f"the PRs with head `{branch}` could not be read: {raw[:200]}") from exc


def _pick_carrier(cands: list[PullRequest]) -> PullRequest | None:
    """The carrier candidate for one base branch.

    OPEN outranks MERGED — an open carrier can still be amended with the
    keyword and is the one that will land next — and a CLOSED-WITHOUT-MERGING
    PR ranks LAST, not first. `not pr.merged` was the original test and it was
    wrong: `merged` is False for a CLOSED PR, so a closed-and-abandoned PR was
    picked ahead of a real carrier and `classify` then reported `lands` over an
    issue nothing would ever close (#10597 review P0-1).
    """
    if not cands:
        return None
    for pr in cands:
        if pr.state.upper() == "OPEN":
            return pr
    for pr in cands:
        if pr.merged:
            return pr
    return cands[0]  # closed-unmerged: `classify` reports it as `unresolved`


def collect_stack(roots: list[PullRequest], repo: str | None,
                  default_branch: str) -> dict[str, PullRequest]:
    """Resolve the stack above each root, fetching each base branch's PR once."""
    by_head: dict[str, PullRequest] = {}
    pending: list[str] = []
    for pr in roots:
        if pr.head:
            by_head.setdefault(pr.head, pr)
        if pr.base and pr.base != default_branch:
            pending.append(pr.base)
    resolved: set[str] = set()
    while pending:
        branch = pending.pop()
        if branch in resolved or branch in by_head:
            continue
        resolved.add(branch)
        pr = _pick_carrier(fetch_prs_by_head(branch, repo))
        if pr is None:
            continue
        by_head[branch] = pr
        if pr.base and pr.base != default_branch and pr.base not in resolved:
            pending.append(pr.base)
    return by_head


def sweep(repo: str | None, default_branch: str) -> list[Finding]:
    """Every OPEN PR whose base is not the default branch, classified.

    This is the population #10597 was measured in: the offending PR is visible
    here for as long as it is open — which is the whole window in which the
    keyword can still be fixed.
    """
    repo_args = ["--repo", repo] if repo else []
    raw = _gh(["pr", "list", *repo_args, "--state", "open", "--limit", "1000",
               "--json", _GH_FIELDS])
    try:
        prs = [_to_pr(r) for r in json.loads(raw)]
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(f"the open PRs could not be read: {raw[:200]}") from exc
    roots = [p for p in prs if p.base and p.base != default_branch
             and parse_closing_refs(p.body, repo)]
    if not roots:
        return []
    by_head = collect_stack(roots, repo, default_branch)
    findings: list[Finding] = []
    for pr in roots:
        findings.extend(classify(pr, by_head, default_branch, repo))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report `Closes #N` refs that will never fire because the PR's "
                    "base is not the default branch (#10597).")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--pr", type=int, help="classify one PR by number")
    which.add_argument("--sweep", action="store_true",
                       help="classify every open PR on a non-default base")
    which.add_argument("--body-file", type=Path,
                       help="offline: read the PR body from a file")
    ap.add_argument("--repo", default=None, help="owner/name (default: ambient)")
    ap.add_argument("--base", default=None,
                    help="offline/--body-file only: the PR's base branch")
    ap.add_argument("--head", default=None, help="offline/--body-file only: the PR's head branch")
    ap.add_argument("--default-branch", default=None,
                    help="the default branch (measured when omitted)")
    ap.add_argument("--carrier-body-file", type=Path, default=None,
                    help="offline/--body-file only: the body of the PR at the top of the stack")
    ap.add_argument("--annotate", action="store_true",
                    help="emit GitHub Actions ::warning lines instead of prose")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    try:
        if args.body_file is not None:
            if not args.base:
                ap.error("--body-file needs --base (the question is meaningless without it)")
            default_branch = args.default_branch or "main"
            pr = PullRequest(number=0, base=args.base,
                             body=args.body_file.read_text(encoding="utf-8"),
                             head=args.head or "")
            by_head: dict[str, PullRequest] = {}
            # No `--carrier-body-file` means the caller knows of NO PR at the top
            # of the stack. That is `unresolved`, not `inert`: inventing an empty
            # carrier would report a fact about a PR that was never described.
            if args.base != default_branch and args.carrier_body_file:
                carrier = PullRequest(
                    number=0, base=default_branch,
                    body=args.carrier_body_file.read_text(encoding="utf-8"))
                by_head[args.base] = carrier
            findings = classify(pr, by_head, default_branch, args.repo)
        else:
            measured_default, slug = fetch_identity(args.repo)
            default_branch = args.default_branch or measured_default
            # Fall back to the repo gh just named: with no `--repo` the caller had
            # no string, and a same-repo `owner/repo#N` claim would be dropped as
            # foreign — a false pass by non-detection (review cycle 2, finding 5).
            classify_repo = args.repo or slug or None
            if args.sweep:
                findings = sweep(classify_repo, default_branch)
            else:
                pr = fetch_pr(args.pr, args.repo)
                by_head = collect_stack([pr], args.repo, default_branch)
                findings = classify(pr, by_head, default_branch, classify_repo)
    except Unmeasurable as exc:
        print(f"closes_landing: UNMEASURED — {exc}", file=sys.stderr)
        return 2

    verdict = worst_verdict(findings)
    if args.json:
        print(json.dumps({"default_branch": default_branch, "verdict": verdict,
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    elif args.annotate:
        for f in findings:
            if f.verdict != "lands":
                print(f.annotation())
    else:
        if not findings:
            # NOT "on a non-default base": this branch is also reached by `--pr` on
            # a PR whose base IS the default branch, and claiming otherwise puts a
            # false statement about the PR in front of the reader.
            print(f"closes_landing: no closing refs to judge — nothing to report "
                  f"(default branch: {default_branch})")
        for f in findings:
            print(f"[{f.verdict:10s}] #{f.issue} via PR #{f.pr} (base {f.base}) "
                  f"-> carrier #{f.carrier}")
            print(f"             {f.detail}")

    return 3 if verdict in ("inert", "unresolved") else 0


if __name__ == "__main__":
    sys.exit(main())
