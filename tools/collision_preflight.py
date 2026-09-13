#!/usr/bin/env python3
"""collision_preflight — fail-loud, all-surface in-flight-work pre-flight (#3061).

Why this exists
---------------
Before dispatching a workstream for an issue, agents are supposed to check
whether another worktree / branch / PR already covers it. The check that was
actually run looked only at REMOTE surfaces (open PRs, remote branches,
assignees, claim comments) and piped the ONE local check
(``git worktree list | grep <N> | tail``) through a truncating window — so on a
297-worktree hub a live collision read as "no collision". Two dispatches
(#2985, #2952) duplicated in-flight work that already had open PRs (#3005,
#3018).

Design contract
---------------
1. EVERY surface is always evaluated. There is no ``--only`` flag, no partial
   mode, no early exit. If a surface cannot be queried the run is INCOMPLETE,
   never CLEAN.
2. Worktree enumeration is UNTRUNCATED, and the GitHub PR surfaces are
   enumerated to COMPLETENESS. This module never pipes, heads or tails git
   output — the reported count is the full count — and a PR list is fetched
   with ``--limit N+1`` so a list longer than its cap is detectable and is
   reported TRUNCATED → INCOMPLETE, never silently partial. A capped list that
   quietly queried a subset of PRs is the same fail-open class as the bug this
   tool exists to fix.
3. A hit exits non-zero and names the surface. An unqueryable surface exits
   non-zero as INCOMPLETE. "No collision" (0) and "could not check" (2) are
   different outcomes by construction.
4. Matching is boundary-exact for the issue number — the regex
   ``(?<![0-9])N(?![0-9])`` means ``3061`` never matches ``30610`` — so the
   tool cannot manufacture a collision out of an unrelated number.

Surfaces (7 rows; 6 are hit-capable, the 7th is the keyword source)
-------------------------------------------------------------------
  open PRs                  gh pr list --state open   (title / headRef;
                                                         body only as a closing
                                                         reference)
  recently-closed PRs       gh pr list --state closed (title / headRef;
                                                         body only as a closing
                                                         reference)
  local branches            git for-each-ref refs/heads
  remote branches           git for-each-ref refs/remotes   (all remotes)
  local worktrees           git worktree list --porcelain   (UNTRUNCATED)
  issue assignee/comments   gh issue view N (assignee + claim comments)
  issue keywords            gh issue view title, or --keywords (completeness)

Number matching is applied to full refs/paths/PR text; keyword matching is
applied only to NAME-LIKE fields (branch refs, worktree basenames, PR head
refs) and requires at least `--min-keywords` (default 2) DISTINCT keywords to
coincide. Three precision guards are deliberate, each learned from a real run:

  * Keyword matching is NOT applied to PR bodies (prose), to worktree parent
directories (the literal `.worktrees/` component is structural, and a title
containing "worktree" must not collide with every worktree on the hub), or to
REMOTE branches — the remote-tracking namespace here holds hundreds of stale,
abandoned refs, and keyword-scanning it produced 878 false hits for one
issue. Remote branches are number-matched only (the convention is
`<type>/<issue#>-<slug>`), which is what the check actually needs.
  * A single keyword is too weak: "remote" matches hundreds of branches, so a
keyword-only hit requires `--min-keywords` distinct keywords. Number hits are
always hits.
  * PR-BODY PROSE IS NOT A COLLISION. A closed PR cannot be in-flight, and a
body that merely cross-references an issue ("restored in #2745", "triaged and
filed as #2751") is not work for it. On the body of a PR only a CLOSING
REFERENCE (`closes` / `fixes` / `resolves #N`, case-insensitive) is a strong
hit; a bare number mention is recorded as a WEAK, non-blocking signal — it is
printed for transparency but can never by itself produce a "do NOT dispatch"
verdict. This is a live-bug fix: those two exact bodies produced a false
COLLISION for #2745 and #2751 because every `#N` in prose was treated as work.

Keywords are the issue title's distinctive tokens (length >= 5, minus a
generic/process vocabulary); pass ``--keywords`` to override when ``gh`` cannot
supply the title.

Usage
-----
    python3 tools/collision_preflight.py <issue-number> [--repo PATH]
        [--keywords a,b,c] [--min-keywords N] [--gh PATH] [--git PATH]
        [--timeout SECS] [--pr-limit N] [--closed-pr-limit N]

Exit codes
----------
    0  CLEAN        every surface queried and no hit (weak prose-only
                    cross-references may be listed; they are non-blocking)
    1  COLLISION    >= 1 hit on >= 1 surface (do NOT dispatch)
    2  INCOMPLETE   >= 1 surface could not be queried (NOT clean)
    3  usage / internal error

Env seams (tests point these at stubs; production defaults are the real tools)
    COLLISION_PREFLIGHT_GH                gh binary        (default: gh)
    COLLISION_PREFLIGHT_GIT               git binary       (default: git)
    COLLISION_PREFLIGHT_TIMEOUT           per-command secs (default: 60)
    COLLISION_PREFLIGHT_PR_LIMIT          open-PR cap     (default: 1000)
    COLLISION_PREFLIGHT_CLOSED_PR_LIMIT   closed-PR cap    (default: 5000)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

EXIT_CLEAN = 0
EXIT_COLLISION = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3

DEFAULT_TIMEOUT = 60.0
# PR caps are completeness bounds, not sampling windows: the lists are fetched
# with `--limit cap+1` and a longer list is reported TRUNCATED -> INCOMPLETE.
# `gh pr list --search` is deliberately never used: the search API silently
# caps at 1000 results (observed on this repo's closed surface), which is the
# exact partial-query failure mode this tool exists to prevent.
PR_LIMIT = 1000
CLOSED_PR_LIMIT = 5000
DEFAULT_MIN_KEYWORDS = 2
MAX_HITS_SHOWN = 20

SURFACE_OPEN_PRS = "open PRs"
SURFACE_CLOSED_PRS = "recently-closed PRs"
SURFACE_LOCAL_BRANCHES = "local branches"
SURFACE_REMOTE_BRANCHES = "remote branches"
SURFACE_WORKTREES = "local worktrees"
SURFACE_ISSUE = "issue assignee/comments"
SURFACE_KEYWORDS = "issue keywords"

# The complete, ordered surface list. `_assert_all_surfaces` proves the run
# evaluated every row — a partial run is an internal error (exit 3), never a
# silently-narrower CLEAN.
ALL_SURFACES = (
    SURFACE_OPEN_PRS,
    SURFACE_CLOSED_PRS,
    SURFACE_LOCAL_BRANCHES,
    SURFACE_REMOTE_BRANCHES,
    SURFACE_WORKTREES,
    SURFACE_ISSUE,
    SURFACE_KEYWORDS,
)

STATUS_CLEAN = "CLEAN"
STATUS_HIT = "HIT"
STATUS_INCOMPLETE = "INCOMPLETE"

# Generic / process vocabulary excluded from title-derived keywords: these words
# are in a large fraction of issue titles and would match unrelated branches and
# worktrees, i.e. fabricate collisions. User-supplied --keywords bypass this
# filter (explicit intent wins).
_GENERIC = {
    "issue", "issues", "process", "check", "checks", "checking", "work",
    "works", "working", "update", "updates", "fix", "fixes", "fixed",
    "test", "tests", "testing", "add", "adds", "adding", "remove", "removes",
    "removing", "support", "supports", "improve", "improves", "review",
    "reviews", "task", "tasks", "code", "docs", "documentation", "feature",
    "features", "bug", "bugs", "bugfix", "cleanup", "refactor", "implement",
    "implements", "implementation", "change", "changes", "create", "creates",
    "enable", "enables", "disable", "disables", "allow", "allows", "make",
    "makes", "use", "uses", "using", "need", "needs", "needed", "should",
    "would", "could", "must", "when", "what", "which", "where", "there",
    "their", "about", "after", "before", "into", "from", "with", "without",
    "only", "also", "than", "then", "this", "that", "these", "those", "have",
    "has", "had", "been", "being", "were", "was", "are", "not", "and", "for",
    "the", "its", "it's", "each", "every", "some", "any", "all", "more",
    "most", "less", "least", "other", "another", "same", "both", "two",
    "three", "first", "second", "next", "last", "new", "old", "via", "per",
    "pre", "post", "non", "sub", "re", "run", "runs", "running", "state",
    "states", "data", "file", "files", "line", "lines", "path", "paths",
    "time", "times", "case", "cases", "value", "values", "type", "types",
    "name", "names", "todo", "note", "notes", "info", "misc", "miscellaneous",
}

# Branch-type prefixes / structural path tokens never treated as keywords.
_STRUCTURAL = {
    "feat", "feature", "fix", "fixes", "bugfix", "chore", "hotfix", "release",
    "refactor", "test", "tests", "docs", "ci", "build", "perf", "style",
    "revert", "wip", "main", "master", "dev", "develop", "head", "origin",
    "upstream", "worktree", "worktrees", "detached", "bare",
}

_CLAIM_RE = re.compile(
    r"(?i)(?:"
    r"/claim\b|"
    r"\bworking on\b|\bwork(?:ing)? this\b|\bon it\b|\bin progress\b|"
    r"\btaking (?:this|it)\b|\bi'?ll (?:take|do|handle|fix)\b|\bclaim(?:ing)?\b|"
    r"\bassigned to\b|\bdispatching\b|\bpicked (?:this|it) up\b|"
    r"\bhandling this\b|\bwill (?:fix|implement|handle)\b|"
    r"\bstarted (?:on )?this\b|\balready (?:fixing|working|implementing)\b"
    r")"
)


class SurfaceError(Exception):
    """A surface could not be queried (INCOMPLETE — never CLEAN)."""


@dataclass
class Hit:
    surface: str
    ref: str
    detail: str
    strength: str  # "strong" (issue number / claim) | "keyword" | "weak"


@dataclass
class Surface:
    name: str
    status: str = STATUS_CLEAN
    hits: list[Hit] = field(default_factory=list)
    note: str = ""
    truncated: bool = False
    truncation_note: str = ""

    def add(self, ref: str, detail: str, strength: str) -> None:
        self.hits.append(Hit(self.name, ref, detail, strength))
        self.status = STATUS_HIT

    def incomplete(self, note: str) -> None:
        self.status = STATUS_INCOMPLETE
        self.note = note

    def mark_truncated(self, note: str) -> None:
        """The surface was queried but the list hit its cap, so it is PARTIAL.
        Tracked separately from `status` so a hit found in the partial list is
        still reported while the truncation keeps the run out of CLEAN."""
        self.truncated = True
        self.truncation_note = note


# ── process helpers ──────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str, timeout: float, env: dict | None = None):
    """Run a command and capture output. Never raises for non-zero exit;
    returns (rc, stdout, stderr, timed_out)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: command not found", False
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout:g}s", True
    except OSError as exc:  # pragma: no cover - defensive
        return 126, "", f"{cmd[0]}: {exc}", False


def _one_line(text: str, limit: int = 200) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


# ── matching ─────────────────────────────────────────────────────────────────

def number_present(text: str, issue: int) -> bool:
    """Boundary-exact issue-number match: 3061 matches '#3061', 'w3061',
    'fix/3061-x' but NEVER '30610'."""
    return re.search(rf"(?<![0-9]){issue}(?![0-9])", text or "") is not None


def closing_reference(text: str, issue: int) -> bool:
    """True only for a GitHub CLOSING KEYWORD bound to the issue number
    (`closes` / `fixes` / `resolves #N`, all inflections, optional colon).

    This is a statement that the PR *is* the work for #N. It is deliberately
    narrower than `number_present`: a body saying "restored in #2745" or
    "triaged and filed as #2751" only cross-references the issue and is NOT
    work for it, so it must never block a dispatch for #2745 / #2751.
    """
    if not text:
        return False
    pattern = (
        rf"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s*:?\s*#{issue}(?![0-9])"
    )
    return re.search(pattern, text) is not None


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _singular(token: str) -> str:
    if len(token) <= 4 or not token.endswith("s") or token.endswith("ss"):
        return token
    if token.endswith(("ches", "shes", "sses", "xes", "zes")):
        return token[:-2]  # dispatches -> dispatch, boxes -> box
    return token[:-1]      # surfaces -> surface, checks -> check


def derive_keywords(title: str | None, explicit: str | None = None) -> list[str]:
    """Issue keywords: distinctive title tokens, or explicit --keywords."""
    out: list[str] = []
    if explicit:
        for raw in explicit.split(","):
            tok = raw.strip().lower()
            if tok and tok not in out:
                out.append(tok)
        return out
    if not title:
        return out
    for tok in re.findall(r"[A-Za-z0-9]+", title):
        low = tok.lower()
        if low.isdigit() or len(low) < 5 or low in _GENERIC:
            continue
        sing = _singular(low)
        if len(sing) < 5 or sing in _GENERIC:
            continue
        if sing not in out:
            out.append(sing)
    return out


def keyword_matches(text: str, keywords: list[str]) -> list[str]:
    """Whole-token (plural-folded) keyword match against a name-like field.
    Structural branch/path vocabulary is excluded so 'worktree' in a title
    cannot match the literal '.worktrees/' directory component."""
    if not keywords:
        return []
    cand = {_singular(t) for t in _tokens(text)} - _STRUCTURAL
    return [k for k in keywords if _singular(k) in cand]


def keyword_hit(text: str, keywords: list[str], minimum: int) -> list[str]:
    """Keyword hits gated on `minimum` DISTINCT keywords. The gate is what
    keeps a single common word ('remote') from fabricating hundreds of
    collisions; number hits are unaffected."""
    kws = keyword_matches(text, keywords)
    return kws if len(kws) >= max(1, minimum) else []


# ── git surfaces ─────────────────────────────────────────────────────────────

def _git_refs(git_bin: str, repo: str, namespace: str, timeout: float) -> list[str]:
    rc, out, err, timed_out = _run(
        [git_bin, "for-each-ref", "--format=%(refname)", namespace],
        repo, timeout,
    )
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        raise SurfaceError(f"git for-each-ref {namespace} failed ({why}): {_one_line(err)}")
    refs = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # Remote HEAD symrefs are not work.
    return [r for r in refs if not r.endswith("/HEAD")]


def scan_branch_surface(
    surface: Surface, refs: list[str], issue: int, keywords: list[str],
    min_keywords: int, allow_keywords: bool,
) -> None:
    """Number matching always; keyword matching only where it is precise
    enough to be useful (`allow_keywords` is False for the remote namespace)."""
    for ref in refs:
        if number_present(ref, issue):
            surface.add(ref, f"matched issue-number ({issue})", "strong")
            continue
        if not allow_keywords:
            continue
        kws = keyword_hit(ref, keywords, min_keywords)
        if kws:
            surface.add(ref, "keyword(s): " + ", ".join(kws), "keyword")


def _worktree_blocks(porcelain: str) -> list[dict]:
    blocks: list[dict] = []
    cur: dict | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if cur is not None:
                blocks.append(cur)
            cur = {"path": line[len("worktree "):].strip()}
        elif cur is not None and line.startswith("branch "):
            cur["branch"] = line[len("branch "):].strip()
        elif cur is not None and line.startswith("detached"):
            cur.setdefault("branch", "(detached)")
    if cur is not None:
        blocks.append(cur)
    return blocks


def scan_worktree_surface(
    surface: Surface, blocks: list[dict], issue: int, keywords: list[str],
    min_keywords: int,
) -> None:
    """Untruncated worktree scan. Number match on the full path and branch;
    keyword match on the basename and branch only (never the parent dir)."""
    for block in blocks:
        path = block.get("path", "")
        branch = block.get("branch", "")
        basename = Path(path).name
        if number_present(path, issue) or number_present(branch, issue):
            surface.add(f"{path} [{branch or 'detached'}]",
                        f"matched issue-number ({issue})", "strong")
            continue
        kws = keyword_hit(basename, keywords, min_keywords) \
            or keyword_hit(branch, keywords, min_keywords)
        if kws:
            surface.add(f"{path} [{branch or 'detached'}]",
                        "keyword(s): " + ", ".join(kws), "keyword")


# ── GitHub surfaces ──────────────────────────────────────────────────────────

def _gh_json(gh_bin: str, args: list[str], repo: str, timeout: float):
    rc, out, err, timed_out = _run([gh_bin, *args], repo, timeout)
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        raise SurfaceError(f"gh {' '.join(args)} failed ({why}): {_one_line(err or out)}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SurfaceError(
            f"gh {' '.join(args)} returned non-JSON: {exc}: {_one_line(out)}"
        ) from exc


def _pr_ref(pr: dict) -> str:
    return (
        f"PR #{pr.get('number', '?')} "
        f"{_one_line(pr.get('title', ''), 90)} "
        f"[{pr.get('headRefName', '')}]"
    )


def scan_pr_surface(
    surface: Surface, prs: list[dict], issue: int, keywords: list[str],
    min_keywords: int,
) -> None:
    """PR surface matching.

    STRONG hits are name-like or contractual fields: the title (this repo's
    convention is `type(scope): #N ...`), the head ref (`<type>/<N>-<slug>`),
    or a BODY CLOSING REFERENCE (`Closes #N`). The body is otherwise PROSE:
    a bare `#N` mention there is recorded as a WEAK, non-blocking signal —
    cross-reference prose is not work (live bug: "restored in #2745" on closed
    PR #2926 and "filed as #2751" on PR #2754 read as strong COLLISIONs).
    Keyword matching applies to the head ref only (name-like), never prose.
    """
    for pr in prs:
        title = pr.get("title") or ""
        body = pr.get("body") or ""
        head = pr.get("headRefName") or ""
        if number_present(title, issue):
            surface.add(_pr_ref(pr), f"matched issue-number ({issue}) in title", "strong")
            continue
        if number_present(head, issue):
            surface.add(_pr_ref(pr), f"matched issue-number ({issue}) in branch", "strong")
            continue
        if closing_reference(body, issue):
            surface.add(_pr_ref(pr),
                        f"closing reference to #{issue} in body", "strong")
            continue
        kws = keyword_hit(head, keywords, min_keywords)
        if kws:
            surface.add(_pr_ref(pr),
                        "keyword(s): " + ", ".join(kws), "keyword")
            continue
        if str(pr.get("number")) == str(issue):
            surface.add(_pr_ref(pr),
                        f"PR number == issue ({issue}): this PR *is* the issue, "
                        "not separate in-flight work (non-blocking)", "weak")
            continue
        if number_present(body, issue):
            surface.add(_pr_ref(pr),
                        f"prose mention of #{issue} in body — not a closing "
                        "reference (non-blocking)", "weak")


def scan_issue_surface(surface: Surface, issue_data: dict) -> None:
    for assignee in issue_data.get("assignees") or []:
        login = assignee.get("login") if isinstance(assignee, dict) else str(assignee)
        surface.add(f"assignee:{login}", "issue is assigned (claimed)", "strong")
    for comment in issue_data.get("comments") or []:
        body = comment.get("body") or ""
        author = (comment.get("author") or {}).get("login", "unknown")
        if _CLAIM_RE.search(body):
            surface.add(f"comment by {author}", "claim-style comment: "
                        + _one_line(body, 90), "strong")
    if not surface.hits:
        state = issue_data.get("state")
        if state and state.upper() != "OPEN":
            surface.note = f"issue state={state} (closed issues are warn-only, not a hit)"


# ── orchestration ────────────────────────────────────────────────────────────

def run_preflight(
    issue: int,
    repo: str,
    gh_bin: str,
    git_bin: str,
    timeout: float,
    explicit_keywords: str | None = None,
    min_keywords: int = DEFAULT_MIN_KEYWORDS,
    open_pr_limit: int = PR_LIMIT,
    closed_pr_limit: int = CLOSED_PR_LIMIT,
) -> tuple[list[Surface], str | None, list[str], int, bool]:
    surfaces: dict[str, Surface] = {name: Surface(name) for name in ALL_SURFACES}

    # 1. Issue metadata FIRST — it is both a surface (assignee/comments) and the
    #    keyword source for the name-like surfaces. A failure here leaves the
    #    keyword dimension INCOMPLETE (never silently number-only).
    issue_data: dict | None = None
    title: str | None = None
    try:
        data = _gh_json(
            gh_bin,
            ["issue", "view", str(issue), "--json",
             "number,title,state,assignees,comments,url"],
            repo, timeout,
        )
        if isinstance(data, dict):
            issue_data = data
            title = data.get("title") or None
        else:
            raise SurfaceError("gh issue view returned non-object JSON")
    except SurfaceError as exc:
        surfaces[SURFACE_ISSUE].incomplete(f"gh-unavailable: {exc}")
    if isinstance(issue_data, dict):
        scan_issue_surface(surfaces[SURFACE_ISSUE], issue_data)

    keywords = derive_keywords(title, explicit_keywords)
    if keywords:
        surfaces[SURFACE_KEYWORDS].note = (
            "source: " + ("--keywords" if explicit_keywords else "gh issue title")
            + f"; {len(keywords)} distinctive keyword(s)"
        )
    else:
        surfaces[SURFACE_KEYWORDS].incomplete(
            "keyword-source-unavailable: gh issue title could not be fetched and "
            "--keywords was not supplied"
        )

    # 2. PR surfaces — enumerated to COMPLETENESS. Each list is fetched with an
    #    extra row (`--limit cap+1`) so a longer list is detectable: hitting the
    #    cap marks the surface TRUNCATED, which keeps the run out of CLEAN. The
    #    `--search` filter is deliberately NOT used (the search API silently
    #    caps at 1000 results — a partial query wearing a complete face).
    for surface_name, state, limit in (
        (SURFACE_OPEN_PRS, "open", open_pr_limit),
        (SURFACE_CLOSED_PRS, "closed", closed_pr_limit),
    ):
        surface = surfaces[surface_name]
        try:
            args = ["pr", "list", "--state", state, "--limit", str(limit + 1),
                    "--json", "number,title,body,headRefName,state,url"]
            prs = _gh_json(gh_bin, args, repo, timeout)
            if not isinstance(prs, list):
                raise SurfaceError("gh pr list returned non-list JSON")
            if len(prs) > limit:
                surface.mark_truncated(
                    f"truncated at the {limit} cap — more than {limit} {state} "
                    f"PR(s) exist and were NOT scanned; this surface is "
                    "INCOMPLETE (never CLEAN). Widen with --pr-limit / "
                    "--closed-pr-limit (or COLLISION_PREFLIGHT_*_PR_LIMIT)."
                )
                prs = prs[:limit]
            scan_pr_surface(surface, prs, issue, keywords, min_keywords)
            if not surface.truncated:
                surface.note = f"{len(prs)} PR(s) enumerated (complete, cap {limit})"
        except SurfaceError as exc:
            surface.incomplete(f"gh-unavailable: {exc}")

    # 3. Branch surfaces. Remote refs are number-matched ONLY: the
    #    remote-tracking namespace carries hundreds of stale branches and
    #    keyword-scanning it produced 878 false hits on a real run.
    for surface_name, namespace, allow_kw in (
        (SURFACE_LOCAL_BRANCHES, "refs/heads", True),
        (SURFACE_REMOTE_BRANCHES, "refs/remotes", False),
    ):
        surface = surfaces[surface_name]
        try:
            refs = _git_refs(git_bin, repo, namespace, timeout)
            scan_branch_surface(surface, refs, issue, keywords, min_keywords, allow_kw)
            surface.note = f"{len(refs)} ref(s) enumerated"
            if not allow_kw and keywords:
                surface.note += "; number-only (remote refs are stale/numerous)"
        except SurfaceError as exc:
            surface.incomplete(f"git-unavailable: {exc}")

    # 4. Worktree surface — UNTRUNCATED by construction; the count is proof.
    surface = surfaces[SURFACE_WORKTREES]
    try:
        rc, out, err, timed_out = _run(
            [git_bin, "worktree", "list", "--porcelain"], repo, timeout
        )
        if rc != 0:
            why = "timeout" if timed_out else f"exit {rc}"
            raise SurfaceError(f"git worktree list failed ({why}): {_one_line(err)}")
        blocks = _worktree_blocks(out)
        if out.strip() and len(blocks) != sum(
            1 for ln in out.splitlines() if ln.startswith("worktree ")
        ):
            raise SurfaceError("worktree porcelain parse lost an entry (refusing partial scan)")
        scan_worktree_surface(surface, blocks, issue, keywords, min_keywords)
        surface.note = f"{len(blocks)} worktree(s) enumerated (untruncated)"
    except SurfaceError as exc:
        surface.incomplete(f"git-unavailable: {exc}")

    ordered = [surfaces[name] for name in ALL_SURFACES]
    return ordered, title, keywords, issue, bool(explicit_keywords)


def _assert_all_surfaces(ordered: list[Surface]) -> None:
    names = [s.name for s in ordered]
    if names != list(ALL_SURFACES):
        raise RuntimeError(
            f"internal error: surface set is incomplete/partial: {names!r} != {list(ALL_SURFACES)!r}"
        )


def format_report(
    ordered: list[Surface],
    issue: int,
    repo: str,
    title: str | None,
    keywords: list[str],
    min_keywords: int,
) -> tuple[str, int]:
    _assert_all_surfaces(ordered)

    hits = [h for s in ordered for h in s.hits]
    incomplete = [s for s in ordered if s.status == STATUS_INCOMPLETE or s.truncated]
    strong = [h for h in hits if h.strength == "strong"]
    keyword_hits = [h for h in hits if h.strength == "keyword"]
    weak = [h for h in hits if h.strength == "weak"]

    lines: list[str] = []
    lines.append(f"collision-preflight: issue #{issue}")
    lines.append(f"repo: {repo}")
    lines.append(f"title: {title or '(unavailable)'}")
    lines.append(f"keywords: {', '.join(keywords) if keywords else '(none)'}")
    lines.append(f"keyword gate: >= {max(1, min_keywords)} distinct keyword(s) for a keyword-only hit")
    lines.append("")
    lines.append(f"{'SURFACE':<24} {'STATUS':<11} {'HITS':<5} NOTE")
    for surface in ordered:
        note = surface.note or ""
        if surface.truncated:
            note = (note + " " if note else "") + "⚠ TRUNCATED — list is partial"
        lines.append(f"{surface.name:<24} {surface.status:<11} {len(surface.hits):<5} {note}")
    if hits:
        lines.append("")
        lines.append("HITS")
        by_surface: dict[str, list[Hit]] = {}
        for hit in hits:
            by_surface.setdefault(hit.surface, []).append(hit)
        for surface_name, surface_hits in by_surface.items():
            for hit in surface_hits[:MAX_HITS_SHOWN]:
                tag = {"strong": "number", "keyword": "keyword",
                       "weak": "weak"}.get(hit.strength, hit.strength)
                lines.append(f"  [{hit.surface}] {hit.ref} — {hit.detail} ({tag})")
            if len(surface_hits) > MAX_HITS_SHOWN:
                # The COUNT is complete and visible; only the detail sample is
                # capped (a completeness check must never hide a count).
                lines.append(
                    f"  [{surface_name}] … +{len(surface_hits) - MAX_HITS_SHOWN} "
                    f"more hit(s) on this surface (total {len(surface_hits)})"
                )
    if weak and not strong and not keyword_hits:
        lines.append("")
        lines.append("WEAK SIGNALS (non-blocking — prose is not work)")
        lines.append(
            f"  {len(weak)} prose-only cross-reference(s) of #{issue}; no title / "
            "branch / worktree / closing-reference match. These do NOT block."
        )
    if incomplete:
        lines.append("")
        lines.append("INCOMPLETE SURFACES")
        for surface in incomplete:
            lines.append(f"  [{surface.name}] {surface.truncation_note or surface.note}")
    lines.append("")
    if strong:
        lines.append(
            f"VERDICT: COLLISION (exit {EXIT_COLLISION}) — {len(hits)} hit(s) across "
            f"{len({h.surface for h in hits})} surface(s); do NOT dispatch work for #{issue}"
        )
        if incomplete:
            lines.append(
                f"  ALSO INCOMPLETE: {len(incomplete)} surface(s) could not be queried "
                f"({', '.join(s.name for s in incomplete)}) — fix gh auth/network and re-run."
            )
        return "\n".join(lines) + "\n", EXIT_COLLISION
    if keyword_hits:
        lines.append(
            f"VERDICT: COLLISION (keyword-only) (exit {EXIT_COLLISION}) — {len(hits)} "
            f"hit(s) across {len({h.surface for h in hits})} surface(s); do NOT dispatch "
            f"work for #{issue}"
        )
        if incomplete:
            lines.append(
                f"  ALSO INCOMPLETE: {len(incomplete)} surface(s) could not be queried "
                f"({', '.join(s.name for s in incomplete)}) — fix gh auth/network and re-run."
            )
        return "\n".join(lines) + "\n", EXIT_COLLISION
    if incomplete:
        lines.append(
            f"VERDICT: INCOMPLETE (exit {EXIT_INCOMPLETE}) — {len(incomplete)} surface(s) "
            f"could not be queried ({', '.join(s.name for s in incomplete)}); "
            "this is NOT clean — fix gh auth/network and re-run"
        )
        return "\n".join(lines) + "\n", EXIT_INCOMPLETE
    if weak:
        lines.append(
            f"NOTE: {len(weak)} weak prose signal(s) ignored (cross-reference prose "
            "is not work; non-blocking)"
        )
    lines.append(
        f"VERDICT: CLEAN (exit {EXIT_CLEAN}) — {len(ordered)}/{len(ALL_SURFACES)} surfaces "
        f"queried, no in-flight work found for #{issue}"
    )
    return "\n".join(lines) + "\n", EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="collision_preflight",
        description="Fail-loud, all-surface in-flight-work pre-flight for a GitHub issue (#3061).",
    )
    parser.add_argument("issue", type=int, help="issue number to check, e.g. 3061")
    parser.add_argument("--repo", default=os.getcwd(),
                        help="path to any worktree of the target repo (default: cwd)")
    parser.add_argument("--keywords", default=None,
                        help="comma-separated keyword override when gh cannot supply the title")
    parser.add_argument("--min-keywords", type=int, default=DEFAULT_MIN_KEYWORDS,
                        help="distinct keywords required for a keyword-only hit "
                             f"(default {DEFAULT_MIN_KEYWORDS}; 1 disables the precision gate)")
    parser.add_argument("--gh", default=os.environ.get("COLLISION_PREFLIGHT_GH", "gh"),
                        help="gh binary (env COLLISION_PREFLIGHT_GH)")
    parser.add_argument("--git", default=os.environ.get("COLLISION_PREFLIGHT_GIT", "git"),
                        help="git binary (env COLLISION_PREFLIGHT_GIT)")
    parser.add_argument("--timeout", type=float,
                        default=float(os.environ.get("COLLISION_PREFLIGHT_TIMEOUT", DEFAULT_TIMEOUT)),
                        help="per-command timeout in seconds (env COLLISION_PREFLIGHT_TIMEOUT)")
    parser.add_argument("--pr-limit", type=int,
                        default=int(os.environ.get("COLLISION_PREFLIGHT_PR_LIMIT", PR_LIMIT)),
                        help="open-PR completeness cap; a longer list is TRUNCATED/INCOMPLETE "
                             f"(default {PR_LIMIT}; env COLLISION_PREFLIGHT_PR_LIMIT)")
    parser.add_argument("--closed-pr-limit", type=int,
                        default=int(os.environ.get("COLLISION_PREFLIGHT_CLOSED_PR_LIMIT", CLOSED_PR_LIMIT)),
                        help="closed-PR completeness cap; a longer list is TRUNCATED/INCOMPLETE "
                             f"(default {CLOSED_PR_LIMIT}; env COLLISION_PREFLIGHT_CLOSED_PR_LIMIT)")
    args = parser.parse_args(argv)

    if args.issue <= 0:
        print("collision-preflight: issue number must be positive", file=sys.stderr)
        return EXIT_USAGE
    if not Path(args.repo).is_dir():
        print(f"collision-preflight: --repo not a directory: {args.repo}", file=sys.stderr)
        return EXIT_USAGE
    if args.timeout <= 0:
        print("collision-preflight: --timeout must be > 0", file=sys.stderr)
        return EXIT_USAGE
    if args.min_keywords < 1:
        print("collision-preflight: --min-keywords must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.pr_limit < 1 or args.closed_pr_limit < 1:
        print("collision-preflight: --pr-limit / --closed-pr-limit must be >= 1",
              file=sys.stderr)
        return EXIT_USAGE

    try:
        ordered, title, keywords, issue, _ = run_preflight(
            args.issue, args.repo, args.gh, args.git, args.timeout, args.keywords,
            args.min_keywords, args.pr_limit, args.closed_pr_limit,
        )
        report, code = format_report(
            ordered, issue, args.repo, title, keywords, args.min_keywords
        )
    except RuntimeError as exc:  # partial-run guard
        print(f"collision-preflight: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sys.stdout.write(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
