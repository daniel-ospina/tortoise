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
0. THE TARGET IS ESTABLISHED, NOT ASSUMED. The tool resolves an explicit
   ``owner/name`` (``--repo owner/name``, or derived from ``--repo PATH``/
   cwd) and sends it on every REPOSITORY-SCOPED ``gh`` call, and it PRINTS the
   resolved ``owner/name`` and the issue's FULL TITLE in the verdict. Two
   calls are deliberately NOT repository-scoped because they cannot be: ``gh
   repo view`` (a fallback that DISCOVERS the slug — there is nothing to send
   yet) and ``gh api user`` (the GitHub login that identifies this lane's
   account, not a repository). A verdict that
   does not name what it measured cannot be trusted: on 2026-09-18 the tool
   resolved TORTOISE #1178 from a tortoise worktree while the target was
   AGENT-INFRA #1178, printed no repository and no title, and returned
   ``CLEAN`` for work it never looked at (#4027). If the issue is ABSENT from
   the target repo the run is INCOMPLETE (exit 2) — "not found here" is not
   "no in-flight work". If ``--repo`` is omitted and the number resolves in
   more than one candidate repo, the tool REFUSES rather than guess.
1. EVERY surface is always evaluated. There is no ``--only`` flag, no partial
   mode, no early exit. If a surface cannot be queried the run is INCOMPLETE,
   never CLEAN.
2. Worktree enumeration is UNTRUNCATED, and the GitHub PR surfaces are
   enumerated to COMPLETENESS. This module never pipes, heads or tails git
   output — the reported count is the full count — and a PR list is fetched
   with ``--limit N+1`` (open PRs) or ``--paginate`` over the REST API (closed
   PRs) so a list longer than its cap is detectable and is reported
   TRUNCATED → INCOMPLETE, never silently partial. A capped list that
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
  recently-closed PRs       gh api --paginate REST    (title / headRef;
                            /repos/<owner>/<repo>/pulls   body only as a
                                                         closing reference)
                                                         (#3587)
  local branches            git for-each-ref refs/heads
  remote branches           git for-each-ref refs/remotes   (all remotes)
  local worktrees           git worktree list --porcelain   (UNTRUNCATED)
  issue assignee/comments   gh issue view N (assignee + claim comments)
  issue keywords            gh issue view title, or --keywords (completeness)

NOT PRESENT: a fleet-session surface. #1233 asked for one wired to
`map-sessions.py`, and it was built and measured for this change — then left
out, because the prescribed discipline (issue number + >= 2 distinctive title
keywords) does not identify a session that HOLDS an issue; it identifies every
session that has READ it. On #3827 the surface attributed 13 sessions, 11 of
them sessions holding a pasted copy of the fleet board (a mailer-daemon
session, a marketing session, ...). Every one of them is fail-closed noise, and
blocking dispatch on 13 phantom holders would reproduce the exact
"a gate that always fires is a gate that gets worked around" corrosion this
change fixes for claim comments. Shipping it needs a mechanism that can tell a
holder from a reader (a lane registry, or the board's lane->issue mapping) —
see the #1233 note in the change report.

Claim comments and lane identity
--------------------------------
Lanes share ONE GitHub account, so an author login cannot tell this lane's own
claim comment from another lane's. A ``claim``-shaped comment is attributed to
this lane only on POSITIVE evidence — a session UUID equal to this session's
(``$PI_SESSION_ID`` / ``--session``) or a lane marker equal to ours
(``$COLLISION_PREFLIGHT_LANE`` / ``--lane``). Anything else, including a
same-account comment with no marker, is NOT ours: "we cannot tell whose it is"
must never be read as "it is ours". The claim regex itself is deliberately
narrow — it matches intent assertions ("claiming this", "/claim", "working on
this") and NOT the ordinary English noun/verb ("a coverage claim", "claiming
that X", "a green on it"), which is what made a lane's own long scoping /
verdict comment fire a false COLLISION on #3827.

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

Keywords are the issue title's DISTINCTIVE tokens (length >= 5, minus two
excluded vocabularies: `_GENERIC` process words and `_COMMON_DOMAIN`
cross-cutting engineering/product words); pass ``--keywords`` to override when
``gh`` cannot supply the title. Excluding the cross-cutting tier is what keeps
``graph`` + ``delete`` in two unrelated branch slugs from reading as shared work
(#3325) while distinctive-term pairs still collide.

Usage
-----
    python3 tools/collision_preflight.py <issue-number>
        [--repo OWNER/NAME | --repo PATH] [--lane ID] [--session UUID]
        [--keywords a,b,c] [--min-keywords N] [--gh PATH] [--git PATH]
        [--timeout SECS] [--pr-limit N] [--closed-pr-limit N]
        [--closed-pr-timeout SECS]

``--repo`` accepts EITHER ``owner/name`` (the GitHub target; a local clone is
located for the git surfaces) OR a path to a worktree of the target repo (the
existing behaviour; ``owner/name`` is then derived from its remote). Omitted,
the current directory is used and the number is checked for ambiguity across
sibling repos before any verdict is issued.

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
    COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT closed-PR REST   (default: 600)
    COLLISION_PREFLIGHT_LANE              this lane's id  (e.g. W0)
    COLLISION_PREFLIGHT_REPO_ROOTS        ':'-separated roots scanned for
                                          sibling repos (default: parent of the
                                          current repo's main worktree)
    PI_SESSION_ID / PI_SESSION_FILE       this session's id (claim attribution)
"""
from __future__ import annotations

import argparse
import json
import math
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
# PR caps are completeness bounds, not sampling windows: OPEN PRs are fetched
# with `--limit cap+1`, CLOSED PRs are fetched to exhaustion with
# `gh api --paginate` (#3587) and the cap is applied to the full result — either
# way a list longer than its cap is reported TRUNCATED -> INCOMPLETE.
# `gh pr list --search` is deliberately never used: the search API silently
# caps at 1000 results (observed on this repo's closed surface), which is the
# exact partial-query failure mode this tool exists to prevent.
PR_LIMIT = 1000
CLOSED_PR_LIMIT = 5000

# The closed-PR surface is enumerated over the GitHub REST API with
# ``gh api --paginate``, NOT the ``gh pr list`` GraphQL path, which resets
# deterministically on this host (``read: connection reset by peer``) while
# REST works (#3587). REST enumeration is inherently MULTI-REQUEST, so this
# surface gets its own wall-clock budget: a single GraphQL call's 60 s budget
# would falsely report a large repo's COMPLETE enumeration as INCOMPLETE. This
# is a budget, not a completeness relaxation — exceeding it is still
# INCOMPLETE (exit 2), never CLEAN. The cap and the budget bound different
# things: `--closed-pr-limit` truncates the SCAN of the fully-fetched list,
# while this budget bounds the FETCH — lowering the cap cannot shorten (or
# fail fast) the enumeration.
CLOSED_PR_TIMEOUT = 600.0
# REST page size. A page is one HTTP response; the issue's suggested
# ``per_page=100`` resets on this host (3/3 runs, ~40 s) while ``per_page=20``
# completes a full 356-PR enumeration under the same transport. Completeness
# comes from ``--paginate`` following the ``Link: rel="next"`` chain, never
# from this number.
REST_PAGE_SIZE = 20

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

# ── target-repo resolution (#4027) ───────────────────────────────────────────
# `--repo` accepts `owner/name` OR a directory path. The slug is what every
# `gh` call is sent; the path is where the git surfaces run. When a slug is
# given without a path (or vice versa) the other half is derived, and a half
# that cannot be derived leaves its surfaces INCOMPLETE — never silently
# reading a different repository.
REPO_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
REPO_ROOTS_ENV = "COLLISION_PREFLIGHT_REPO_ROOTS"
# Sibling repos probed for the omitted-`--repo` ambiguity refusal. Exceeding
# the cap is INCOMPLETE (fail closed), never a silent partial scan.
CANDIDATE_REPO_CAP = 40
AMBIGUITY_TIMEOUT = 30.0

# ── lane / session identity (defect 3) ──────────────────────────────────────
# Lanes are not GitHub accounts: every lane on this fleet shares one login, so
# a claim comment's AUTHOR cannot distinguish this lane from another. Ownership
# of a claim comment is therefore established by SESSION UUID or LANE marker,
# never by authorship alone.
LANE_ENV = "COLLISION_PREFLIGHT_LANE"
SESSION_ID_ENV = "PI_SESSION_ID"
SESSION_FILE_ENV = "PI_SESSION_FILE"
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# `lane X` identities. The grammar must parse the ids this fleet actually
# uses: a trailing letter is a REAL id (`lane B2c`, this repo's own AGENTS.md)
# and the colon form (`Lane: W0`) is common. An id the regex cannot parse makes
# the self-footprint escape hatch inert — with OUR id unparsed, our own claim
# reads as another lane's and blocks our dispatch. Only case is normalised.
LANE_MARKER_RE = re.compile(
    r"(?i)\blane[\s_:-]*([A-Za-z]{1,4}[-_]?\d{1,3}[A-Za-z]?)\b"
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

# Cross-cutting ENGINEERING / PRODUCT vocabulary, excluded from title-derived
# keywords alongside `_GENERIC` (#3325). `_GENERIC` covers process words
# ("test", "fix", "update"); this set covers engineering/product words that
# recur across UNRELATED workstreams. Two of them coinciding in a branch slug is
# not evidence of shared work: the live bug was issue #3214 ("…graph minted …
# the delete"), whose title cleared the >=2 gate against the unrelated
# `feat/2701-graphs-rename-delete` branch purely because "graph" + "delete" are
# common in this repo. A false COLLISION blocks legitimate dispatch, so the gate
# must count DISTINCTIVE terms only.
#
# Measured document frequency over the repo's 3,375 issue+PR titles when this
# set was curated (#3325): graph 7.3%, session 6.3%, hosted 6.3%, dashboard
# 4.9%, signup 4.9%, welcome 4.4%, onboarding 4.0%, backup 3.6%, stale 3.4%,
# monitor 3.2%, source 2.7%, deploy 2.7%, search 2.4%, error 1.9%, … .
#
# Curation rule — a token belongs here iff it is (a) cross-cutting across
# unrelated workstreams AND (b) NOT the name of a subsystem/concept whose
# identity the match is meant to reveal. Frequent but IDENTIFYING domain nouns
# (battery, manifest, retrieval, operator, ontology, projection, parity, dedup,
# falkordb, …) are deliberately absent: for those a keyword match IS the
# sensitivity this dial exists to preserve. The mechanism is a static stoplist
# rather than a corpus-derived IDF score precisely because this is a gate: the
# same repo state must yield the same verdict, and an IDF threshold would make
# the dial's strictness drift with unrelated PR traffic and with gh
# availability. What this trades away is recall on stoplisted terms — a
# distinctively-named branch whose only shared terms are generic is no longer a
# keyword hit. Number matching (`<type>/<issue#>-<slug>`) and an explicit
# `--keywords` override remain the escape hatches.
#
# Reproduce / re-curate with:
#   gh pr list --state all --limit 5000 --json title > /tmp/prs.json
#   gh issue list --state all --limit 5000 --json title > /tmp/issues.json
#   python3 - <<'PY'
#   import json, re, collections
#   from tools.collision_preflight import _singular
#   titles = [o["title"] for f in ("/tmp/prs.json", "/tmp/issues.json")
#             for o in json.load(open(f))]
#   df = collections.Counter()
#   for t in titles:
#       for s in {_singular(w) for w in re.findall(r"[a-z0-9]+", t.lower())}:
#           df[s] += 1
#   n = len(titles)
#   for w, c in df.most_common(120):
#       print(f"{w:16s} {c:5d} {c / n:6.2%}")
#   PY
_COMMON_DOMAIN = {
    # storage/substrate work — the #3325 false-positive class
    "graph", "graphs", "delete", "deletes", "deleted", "deleting", "deletion",
    # web-product surfaces shared by unrelated features
    "session", "sessions", "dashboard", "dashboards", "onboarding",
    "signup", "signups", "welcome", "hosted", "stale",
    # generic software-work verbs/nouns
    "source", "sources", "server", "servers", "search", "searches",
    "suite", "suites", "default", "defaults", "deploy", "deploys",
    "deployed", "deploying", "deployment", "deployments", "merge", "merges",
    "merged", "merging", "write", "writes", "wrote", "written", "writing",
    "audit", "audits", "audited", "auditing", "event", "events",
    "context", "contexts", "product", "products", "object", "objects",
    "error", "errors", "fail", "fails", "failed", "failing", "failure",
    "failures", "monitor", "monitors", "monitoring", "migrate", "migrates",
    "migrated", "migrating", "migration", "migrations", "cache", "caches",
    "cached", "caching", "backup", "backups",
}

# The full set of terms that may never count toward a keyword-only hit.
# Explicit --keywords bypass this (explicit intent wins).
_STOPLIST = _GENERIC | _COMMON_DOMAIN

# Branch-type prefixes / structural path tokens never treated as keywords.
_STRUCTURAL = {
    "feat", "feature", "fix", "fixes", "bugfix", "chore", "hotfix", "release",
    "refactor", "test", "tests", "docs", "ci", "build", "perf", "style",
    "revert", "wip", "main", "master", "dev", "develop", "head", "origin",
    "upstream", "worktree", "worktrees", "detached", "bare",
}

# Claim-shaped comments. This is a GATE, so BOTH failure directions are
# defects and the corpus below is asserted in both: prose that must NOT hit
# and genuine claims that MUST hit.
#
# FALSE POSITIVE (the #3827 live bug): a lane's own long scoping /
# research-verdict comment matched the old regex on "the config comment
# claiming a carve_out", "was a claim about the hour", "a green on it would
# be a vacuous certificate" and "A coverage claim must be stated PER ITEM" —
# every one prose, none a claim. The old `\bclaim(?:ing)?\b` also matched the
# ordinary English `"claiming that X"` the docstring said it excluded. Fixed
# by requiring `claim` to take a deictic object (`this`/`it`/`#N`) and by NOT
# matching third-person present (`"the PR claims it ..."`); bare mid-sentence
# `on it` is prose and is only matched anchored.
#
# FALSE NEGATIVE (the over-narrowing that followed): the fix had dropped
# genuine claim forms the old regex caught (`"I'm on it"`, `"Handling this"`,
# `"working on the fix"`, `"dispatching a lane for #N"`, `"will fix this"`,
# `"assigned to …"`), so the gate stopped seeing real claims. They are
# restored in an ANCHORED form below.
_CLAIM_RE = re.compile(
    r"(?im)(?:"
    r"(?:^|\s)/claim\b|"
    r"\bclaim(?:ing|ed)?\s+(?:this|it|#\d+)\b|"
    r"\bi(?:'ll| will| am|'m|m)\s+(?:take|do|handle|fix|implement|work on|pick|own|get on|jump on)\b|"
    r"\bworking on\b|"
    r"\bwork(?:ing)?\s+this\b|"
    r"\bi(?:'m| am)\s+on it\b|"
    r"^\s*on it\b|"
    r"\bpick(?:ed|ing)?\s+(?:this|it)\s+up\b|"
    r"\btaking\s+(?:this|it)\b|"
    r"\bhandling\s+this\b|"
    r"\bdispatch(?:ing)?\s+(?:(?:a|the)\s+)?(?:this|it|#\d+|lane|sub-?agent|workstream|session)\b|"
    r"\bstarted\s+(?:on\s+)?this\b|"
    r"\balready\s+(?:fixing|working|implementing)\b|"
    r"\bassigned\s+to\b|"
    r"\bwill\s+(?:fix|implement|handle|take|do)\b|"
    r"\bin\s+progress\b"
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
class Identity:
    """Who is running this pre-flight. `login` is the GitHub account; `lane`
    and `session_id` are what actually distinguish one lane from another on a
    fleet where every lane shares the account."""

    login: str | None = None
    lane: str | None = None
    session_id: str | None = None


@dataclass
class RepoTarget:
    """The repo this verdict is ABOUT. `slug` (owner/name) is sent on every
    `gh` call; `path` is where the git surfaces run. Either may be None when it
    could not be derived — its surfaces then report INCOMPLETE, never a scan of
    some other repository."""

    slug: str | None = None
    path: str | None = None
    source: str = "unresolved"
    requested: bool = False


@dataclass
class Surface:
    name: str
    status: str = STATUS_CLEAN
    hits: list[Hit] = field(default_factory=list)
    note: str = ""
    truncated: bool = False
    truncation_note: str = ""
    own_ignored: list[str] = field(default_factory=list)
    # The surface was QUERIED but has no signal to offer (e.g. a title whose
    # every term is generic, so the keyword dimension is empty). Not INCOMPLETE
    # — number matching still works — but the verdict must say the surface is
    # BLIND rather than advertising "7/7 surfaces queried" as complete.
    blind: bool = False

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


# C0/C1 control characters (ESC/CSI/OSC/BEL/DEL included) are stripped from
# every UNTRUSTED field before it reaches the report. The report IS the
# artifact a human reads to decide "do NOT dispatch", and GitHub-sourced text
# (issue title, comment bodies, PR titles) plus a git-remote-derived slug is
# attacker-controlled: an ESC sequence can blank or overwrite the VERDICT line
# on the reading terminal, and OSC 52 can rewrite the clipboard — a fail-open
# class. \t\n\r are kept here and collapsed to spaces by `_one_line`.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _sanitize(text: str | None) -> str:
    """Strip terminal control characters from an untrusted string."""
    return _CONTROL_RE.sub("", text or "")


def _one_line(text: str, limit: int = 200) -> str:
    flat = " ".join(_sanitize(text).split())
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


def _classify_title(title: str) -> tuple[list[str], list[str]]:
    """Split a title's candidate tokens into (distinctive, suppressed-generic).

    "Distinctive" = length >= 5 after plural folding and NOT in `_STOPLIST`.
    The suppressed list is informational only (it makes the precision dial
    legible in the report); the distinctive list is what matching uses."""
    kept: list[str] = []
    dropped: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9]+", title):
        low = tok.lower()
        if low.isdigit() or len(low) < 5:
            continue
        sing = _singular(low)
        if len(sing) < 5:
            continue
        generic = sing if sing in _STOPLIST else (low if low in _STOPLIST else None)
        if generic is not None:
            if generic not in dropped:
                dropped.append(generic)
            continue
        if sing not in kept:
            kept.append(sing)
    return kept, dropped


def derive_keywords(title: str | None, explicit: str | None = None) -> list[str]:
    """Issue keywords: DISTINCTIVE title tokens, or explicit --keywords.

    Generic/process vocabulary (`_STOPLIST`) is dropped so the `--min-keywords`
    gate counts distinctive terms rather than common engineering nouns/verbs
    (#3325). Explicit --keywords bypass that filter (explicit intent wins)."""
    out: list[str] = []
    if explicit:
        for raw in explicit.split(","):
            tok = raw.strip().lower()
            if tok and tok not in out:
                out.append(tok)
        return out
    if not title:
        return out
    kept, _ = _classify_title(title)
    return kept


def suppressed_keywords(title: str | None) -> list[str]:
    """Title tokens dropped as generic/cross-cutting vocabulary (#3325).
    Purely informational — surfaced in the report so a suppressed match is
    never silent. Explicit --keywords are never suppressed, so this is only
    meaningful for the gh-title path."""
    if not title:
        return []
    _, dropped = _classify_title(title)
    return dropped


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


def _gh_json_stream(gh_bin: str, args: list[str], repo: str, timeout: float) -> list:
    """Like `_gh_json`, but for a stream of CONCATENATED JSON values.

    `gh api --paginate --jq …` emits one JSON value (a page array) per page.
    Parsing is deliberately strict, and partial output is NEVER salvaged: a
    non-zero exit is a failure even when earlier pages were printed, and a
    truncated value raises rather than yielding a silently-shorter list. That
    is the difference between a loud INCOMPLETE and a false CLEAN — the exact
    failure class this tool exists to prevent.
    """
    rc, out, err, timed_out = _run([gh_bin, *args], repo, timeout)
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        raise SurfaceError(f"gh {' '.join(args)} failed ({why}): {_one_line(err or out)}")
    values: list = []
    decoder = json.JSONDecoder()
    idx, end = 0, len(out)
    while True:
        while idx < end and out[idx] in " \t\r\n":
            idx += 1
        if idx >= end:
            break
        try:
            value, idx = decoder.raw_decode(out, idx)
        except json.JSONDecodeError as exc:
            raise SurfaceError(
                f"gh {' '.join(args)} returned a truncated/malformed JSON stream "
                f"at offset {idx}: {exc}"
            ) from exc
        values.append(value)
    if not values:
        # An rc-0 transport that prints nothing must never read as a complete,
        # EMPTY enumeration: `_gh_json` rejects empty output (`json.loads("")`
        # raises), and this path must not be weaker than the one it parallels.
        # A genuinely exhausted list still emits one `[]` page, so this cannot
        # reject a legitimate empty result.
        raise SurfaceError(
            f"gh {' '.join(args)} returned no JSON values (empty output) — "
            "refusing to read an empty stream as a complete enumeration"
        )
    return values


def _closed_pr_list_rest(gh_bin: str, slug: str | None, cwd: str,
                         timeout: float) -> list[dict]:
    """Enumerate ALL closed PRs over the REST API (#3587).

    `gh pr list --state closed` (GraphQL) resets on this host while the REST
    endpoint works, so this surface is fetched as
    `GET /repos/{owner}/{repo}/pulls?state=closed&per_page=N` with
    `--paginate`. Completeness lives in `--paginate`: gh follows the
    `Link: rel="next"` chain to exhaustion, and without it only the first page
    would be returned — a short enumeration wearing a complete face. `--jq`
    projects exactly the fields the surface consumes; REST nests the branch
    under `head.ref`, so it is re-keyed to `headRefName` to keep
    `scan_pr_surface` transport-agnostic.

    The path is built from the RESOLVED `owner/name` literally. It deliberately
    does NOT use gh's `{owner}/{repo}` placeholders: those resolve from the
    CURRENT DIRECTORY, which is exactly how the cross-repo false CLEAN of
    #4027 happened (`gh api` has no `--repo` flag).
    """
    if not slug:
        raise SurfaceError(
            "target-repo-unresolved: no owner/name for the target repo — "
            "refusing to query the closed-PR surface against an unknown repo"
        )
    args = [
        "api", "--paginate",
        f"repos/{slug}/pulls?state=closed&per_page={REST_PAGE_SIZE}",
        "--jq",
        "map({number, title, body, state, url, headRefName: .head.ref})",
    ]
    prs: list[dict] = []
    for page in _gh_json_stream(gh_bin, args, cwd, timeout):
        if not isinstance(page, list):
            raise SurfaceError(
                f"gh {' '.join(args)} returned a non-list page: "
                f"{_one_line(json.dumps(page))}"
            )
        prs.extend(page)
    return prs


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


def _identity_markers(body: str) -> tuple[set[str], set[str]]:
    """Session UUIDs and `lane X` markers named in a comment body."""
    sessions = {m.group(0).lower() for m in UUID_RE.finditer(body or "")}
    lanes = {m.group(1).upper() for m in LANE_MARKER_RE.finditer(body or "")}
    return sessions, lanes


# Text allowed BETWEEN an identity marker and a claim for the marker to count
# as TIED to it: whitespace/punctuation, plus identity keywords. A real word
# ("lane W0 IS DONE HERE — I'll take this") means the marker is a REFERENCE to
# another lane, not this lane identifying itself, so it must not suppress the
# collision. This is the reader-vs-holder confusion the #1233 session surface
# was rejected for; it must not be reintroduced here.
_IDENTITY_LINK_RE = re.compile(
    r"(?:[\s\-–—:()\[\]{},.;'\"`|*]|\b(?:session|id|uuid|lane|owner|as|by)\b)*"
)


def _same_line(body: str, a: int, b: int) -> bool:
    return body.rfind("\n", 0, a) == body.rfind("\n", 0, b)


def _marker_tied_to_claim(body: str, m_start: int, m_end: int,
                          claim_spans: list[tuple[int, int]]) -> bool:
    """True when an identity marker at [m_start, m_end) sits on the SAME LINE
    as a claim match and is joined to it only by punctuation/identity words."""
    for c_start, c_end in claim_spans:
        if not _same_line(body, m_start, c_start):
            continue
        gap = (body[m_end:c_start] if m_end <= c_start
               else body[c_end:m_start])
        if _IDENTITY_LINK_RE.fullmatch(gap):
            return True
    return False


def claim_attribution(body: str, login: str | None, identity: Identity) -> str:
    """Attribute a claim-shaped comment: ``self`` | ``other`` | ``unknown``.

    Lanes share ONE GitHub account, so the AUTHOR LOGIN ALONE cannot tell this
    lane's own claim from another lane's — ownership on this tracker is named
    by LANE / SESSION, never by author. A comment is therefore attributed to
    this lane only on POSITIVE evidence: a session UUID equal to ours, or a
    lane marker equal to ours, TIED TO THE CLAIM (same line, punctuation-only
    gap). MERELY NAMING our marker is not enough: another lane's comment that
    quotes the fleet board — which contains our lane id and session UUID — is
    a READER, not a holder, and must still collide. Every other outcome is
    NOT-OURS, including a same-account comment with no marker at all: "we
    cannot tell whose it is" must never be read as "it is ours" (#4027).
    """
    if not login or login.strip().lower() in ("", "unknown", "ghost", "none"):
        return "unknown"
    if not identity.login:
        # We do not even know who WE are: nothing can be attributed to us.
        return "unknown"
    if login.strip().lower() != identity.login.strip().lower():
        return "other"
    sessions, lanes = _identity_markers(body)
    claim_spans = [(m.start(), m.end()) for m in _CLAIM_RE.finditer(body or "")]
    if identity.session_id:
        sid = identity.session_id.strip().lower()
        if sid in sessions and any(
            m.group(0).lower() == sid
            and _marker_tied_to_claim(body, m.start(), m.end(), claim_spans)
            for m in UUID_RE.finditer(body or "")
        ):
            return "self"
    if identity.lane:
        lane = identity.lane.strip().upper()
        if lane in lanes and any(
            m.group(1).upper() == lane
            and _marker_tied_to_claim(body, m.start(), m.end(), claim_spans)
            for m in LANE_MARKER_RE.finditer(body or "")
        ):
            return "self"
    if sessions or lanes:
        # A marker is present, but it is not ours tied to this claim.
        return "other"
    return "unknown"


def assignee_attribution(login: str | None, identity: Identity) -> str:
    """Attribute an issue assignee: ``other`` | ``unknown``.

    GitHub's assignee is a LOGIN only — unlike a comment it carries no lane or
    session marker. On this fleet every lane shares ONE account, so an assignee
    equal to our own login CANNOT be attributed to THIS lane: it could be any
    lane (or a human) that assigned the shared account. Treating login
    equality as "self" would make the assignee surface blind to every other
    lane on the fleet — a false negative, the worse of the two failure modes —
    so it is deliberately NOT treated as positive evidence. A different login
    is affirmatively ``other``; everything else is ``unknown`` and is kept as
    a hit by the caller (fail closed).
    """
    if not login or login.strip().lower() in ("", "unknown", "ghost", "none"):
        return "unknown"
    if not identity.login:
        return "unknown"
    if login.strip().lower() != identity.login.strip().lower():
        return "other"
    return "unknown"


def scan_issue_surface(
    surface: Surface, issue_data: dict, identity: Identity,
) -> None:
    """Assignee + claim comments.

    A claim comment is a hit only when it is NOT attributable to this lane. A
    comment we can positively attribute to ourselves is recorded as own
    footprint (printed, non-blocking) rather than as a collision — otherwise a
    lane could never dispatch the issue it had already claimed.

    An ASSIGNEE is handled the same way, but the identity machinery cannot do
    for it what it does for a comment: GitHub gives only a login, with no lane
    or session marker. On this shared-account fleet an assignee equal to our
    own login is therefore NOT attributable to this lane (any lane could have
    set it), so it is reported as un-attributable and kept as a hit — fail
    closed. Only a DIFFERENT login is affirmatively another party's. The
    consequence is deliberate: a lane's own self-assignment still blocks its
    own dispatch, because suppressing same-account assignments would blind the
    surface to every other lane on the fleet (a false negative).
    """
    for assignee in issue_data.get("assignees") or []:
        login = assignee.get("login") if isinstance(assignee, dict) else str(assignee)
        who = assignee_attribution(login, identity)
        if who == "other":
            surface.add(
                f"assignee:{login}",
                "issue is assigned to another account (not this lane's) — claimed",
                "strong",
            )
        else:
            surface.add(
                f"assignee:{login}",
                "issue is assigned to the shared account; no lane/session marker "
                "can attribute it to THIS lane, so it counts as a hit (fail "
                "closed)",
                "strong",
            )
    for comment in issue_data.get("comments") or []:
        body = comment.get("body") or ""
        if not _CLAIM_RE.search(body):
            continue
        author = comment.get("author") or {}
        login = author.get("login") if isinstance(author, dict) else (
            str(author) if author else None
        )
        who = claim_attribution(body, login, identity)
        if who == "self":
            surface.own_ignored.append(
                f"claim comment by {login or 'unknown'} attributed to this lane "
                f"(session/lane marker): {_one_line(body, 90)}"
            )
            continue
        surface.add(
            f"comment by {login or 'unknown'}",
            f"claim-style comment ({who} attribution — not this lane): "
            + _one_line(body, 90),
            "strong",
        )
    if not surface.hits:
        state = issue_data.get("state")
        if state and state.upper() != "OPEN":
            surface.note = f"issue state={state} (closed issues are warn-only, not a hit)"


# ── target-repo resolution (#4027) ──────────────────────────────────────────

def _valid_slug(slug: str | None) -> str | None:
    """`owner/name` or None. Remote-derived slugs are UNTRUSTED: a git remote
    URL (or gh output) can carry terminal control sequences or path junk, and
    the slug is printed in the report. A slug that is not EXACTLY the GitHub
    slug shape is rejected, leaving its surfaces INCOMPLETE — never printed and
    never used to reach gh."""
    if slug and REPO_SLUG_RE.fullmatch(slug):
        return slug
    return None


def _remote_slug(git_bin: str, path: str, timeout: float) -> str | None:
    """`owner/name` from the repo's git remote, OFFLINE.

    Preferred over `gh repo view` because it is deterministic, needs no network,
    and works identically in a linked worktree. Only gitlab/github-style
    `host:owner/name` and `host/owner/name` URLs are understood; anything else
    falls through to the gh fallback. The result is validated with
    `REPO_SLUG_RE` (`_valid_slug`) because it is printed and sent to gh.
    """
    rc, out, _err, _to = _run([git_bin, "remote", "-v"], path, timeout)
    if rc != 0:
        return None
    urls: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            urls.append((parts[0], parts[1]))
    for name, url in urls:  # prefer `origin`
        if name != "origin":
            continue
        m = re.search(r"[/:]((?:[^/]+))/([^/\s]+?)(?:\.git)?$", url)
        if m and m.group(2):
            return _valid_slug(f"{m.group(1)}/{m.group(2)}")
    for _name, url in urls:
        m = re.search(r"[/:]((?:[^/]+))/([^/\s]+?)(?:\.git)?$", url)
        if m and m.group(2):
            return _valid_slug(f"{m.group(1)}/{m.group(2)}")
    return None


def _gh_slug(gh_bin: str, path: str, timeout: float) -> str | None:
    """`owner/name` from gh itself (fallback when there is no usable remote).
    Validated like every other remote-derived slug before it is printed."""
    rc, out, _err, _to = _run(
        [gh_bin, "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        path, timeout,
    )
    if rc == 0 and out.strip():
        return _valid_slug(out.strip())
    return None


def resolve_slug(gh_bin: str, git_bin: str, path: str, timeout: float,
                 explicit: str | None = None) -> tuple[str | None, str]:
    """Resolve the GitHub `owner/name` for a local checkout."""
    if explicit:
        return explicit, "--repo"
    slug = _remote_slug(git_bin, path, timeout)
    if slug:
        return slug, "git remote"
    slug = _gh_slug(gh_bin, path, timeout)
    if slug:
        return slug, "gh repo view"
    return None, "unresolved"


def _main_worktree_root(git_bin: str, path: str, timeout: float) -> str | None:
    """The MAIN worktree's root for a (possibly linked) worktree.

    `--git-common-dir` points at the shared `.git`, so its parent is the main
    checkout even when `path` is a linked worktree under `.worktrees/`. This is
    what makes sibling-repo discovery work from a worktree, where the mere
    parent directory would list the repo's OWN worktrees.
    """
    rc, out, _err, _to = _run(
        [git_bin, "rev-parse", "--path-format=absolute", "--git-common-dir"],
        path, timeout,
    )
    if rc != 0 or not out.strip():
        return None
    common = Path(out.strip())
    return str(common.parent) if common.name == ".git" else str(common)


def repo_roots(git_bin: str, path: str, timeout: float) -> list[str]:
    """Directories scanned for sibling repos (for clone lookup + ambiguity)."""
    override = os.environ.get(REPO_ROOTS_ENV, "").strip()
    if override:
        return [p for p in override.split(os.pathsep) if p]
    root = _main_worktree_root(git_bin, path, timeout) or path
    parent = Path(root).parent
    return [str(parent)] if parent.is_dir() else []


def _git_repo_dirs(roots: list[str]) -> list[str]:
    dirs: list[str] = []
    for root in roots:
        try:
            entries = sorted(Path(root).iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and (entry / ".git").exists():
                dirs.append(str(entry))
    return dirs


def candidate_slugs(git_bin: str, timeout: float, roots: list[str]) -> list[str]:
    """Distinct `owner/name` slugs of the local sibling repos, in stable order."""
    slugs: list[str] = []
    for directory in _git_repo_dirs(roots):
        slug = _remote_slug(git_bin, directory, timeout)
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def find_local_clone(selector: str, git_bin: str, timeout: float,
                     roots: list[str]) -> str | None:
    """A local checkout of `selector`, so the git surfaces describe the TARGET
    repo rather than whatever directory happens to be the cwd. The slug
    comparison is CASE-INSENSITIVE: GitHub and `gh --repo` accept
    `Owner/Name`, so a case-different selector must find the clone rather than
    leaving every git surface INCOMPLETE (a false exit 2)."""
    want = selector.strip().lower()
    for directory in _git_repo_dirs(roots):
        slug = _remote_slug(git_bin, directory, timeout)
        if slug and slug.lower() == want:
            return directory
    return None


def probe_issue_in_repo(gh_bin: str, slug: str, issue: int, cwd: str,
                        timeout: float) -> bool | None:
    """True if #N resolves in `slug`; False if definitively absent; None if the
    repo could not be queried at all (transport/auth) — never treated as
    absent, because an unqueryable repo may be exactly where the issue lives."""
    rc, out, err, _to = _run(
        [gh_bin, "issue", "view", str(issue), "--repo", slug, "--json", "number"],
        cwd, timeout,
    )
    if rc == 0:
        return True
    blob = f"{err}\n{out}".lower()
    if "could not resolve to an issue" in blob or "could not resolve to a pull request" in blob:
        return False
    if "could not resolve to a repository" in blob:
        # A local remote pointing at a repo GitHub does not have (deleted,
        # renamed, private-to-someone-else): not a candidate at all.
        return False
    return None


# ── orchestration ────────────────────────────────────────────────────────────

def _classify_issue_error(exc: SurfaceError) -> str:
    blob = str(exc).lower()
    if "could not resolve to an issue" in blob or "could not resolve to a pull request" in blob:
        return "issue-absent"
    if "could not resolve to a repository" in blob:
        return "repo-unresolved"
    if "not found" in blob:
        return "issue-absent"
    return "transport"


def run_preflight(
    issue: int,
    target: RepoTarget,
    gh_bin: str,
    git_bin: str,
    timeout: float,
    explicit_keywords: str | None = None,
    min_keywords: int = DEFAULT_MIN_KEYWORDS,
    open_pr_limit: int = PR_LIMIT,
    closed_pr_limit: int = CLOSED_PR_LIMIT,
    closed_pr_timeout: float = CLOSED_PR_TIMEOUT,
    identity: Identity | None = None,
) -> tuple[list[Surface], str | None, list[str], int, bool]:
    identity = identity or Identity()
    surfaces: dict[str, Surface] = {name: Surface(name) for name in ALL_SURFACES}
    cwd = target.path or os.getcwd()
    slug = target.slug

    # 1. Issue metadata FIRST — it is both a surface (assignee/comments) and the
    #    keyword source for the name-like surfaces. A failure here leaves the
    #    keyword dimension INCOMPLETE (never silently number-only).
    #
    #    The target repo (`slug`) is sent EXPLICITLY on every gh call. If it
    #    cannot be resolved the run is INCOMPLETE rather than inferred from the
    #    cwd: querying "whatever repo this directory happens to be" is exactly
    #    the cross-repo false CLEAN of #4027.
    issue_data: dict | None = None
    title: str | None = None
    if slug is None:
        surfaces[SURFACE_ISSUE].incomplete(
            "target-repo-unresolved: no owner/name could be derived for the "
            f"target repo (source={target.source}) — refusing to query gh against "
            "an unknown repository (NOT clean)"
        )
    else:
        try:
            data = _gh_json(
                gh_bin,
                ["issue", "view", str(issue), "--repo", slug, "--json",
                 "number,title,state,assignees,comments,url"],
                cwd, timeout,
            )
            if isinstance(data, dict):
                issue_data = data
                title = data.get("title") or None
            else:
                raise SurfaceError("gh issue view returned non-object JSON")
        except SurfaceError as exc:
            kind = _classify_issue_error(exc)
            if kind == "issue-absent":
                msg = (
                    f"issue-absent: #{issue} does not exist in {slug} — "
                    "\"not found here\" is NOT \"no in-flight work\" (fail closed)"
                )
            elif kind == "repo-unresolved":
                msg = f"repo-unresolved: {slug} could not be resolved"
            else:
                msg = f"gh-unavailable: {exc}"
            surfaces[SURFACE_ISSUE].incomplete(msg)
    if isinstance(issue_data, dict):
        scan_issue_surface(surfaces[SURFACE_ISSUE], issue_data, identity)

    keywords = derive_keywords(title, explicit_keywords)
    suppressed = [] if explicit_keywords else suppressed_keywords(title)
    if keywords:
        note = (
            "source: " + ("--keywords" if explicit_keywords else "gh issue title")
            + f"; {len(keywords)} distinctive keyword(s)"
        )
        if suppressed:
            note += (
                f"; {len(suppressed)} generic term(s) excluded from the gate "
                f"({', '.join(suppressed)})"
            )
        surfaces[SURFACE_KEYWORDS].note = note
    elif explicit_keywords:
        surfaces[SURFACE_KEYWORDS].note = (
            "source: --keywords; 0 usable keyword(s) after parsing"
        )
        surfaces[SURFACE_KEYWORDS].blind = True
    elif title is not None:
        # The title WAS fetched — it simply contains no distinctive term. That
        # is an evaluated, empty keyword dimension (number matching still runs),
        # NOT an unqueryable surface. Conflating the two turned a title like
        # "fix graph delete" into a spurious INCOMPLETE (exit 2) once the
        # cross-cutting stoplist was widened (#3325). It is still BLIND for
        # keyword matching, and the verdict now says so (#3378 P2-1) instead
        # of advertising a complete 7/7-surface scan.
        surfaces[SURFACE_KEYWORDS].note = (
            "source: gh issue title; 0 distinctive keyword(s) — every title term "
            "is generic/cross-cutting, so keyword-only matching has no signal "
            "(number matching is unaffected)"
        )
        if suppressed:
            surfaces[SURFACE_KEYWORDS].note += f"; excluded: {', '.join(suppressed)}"
        surfaces[SURFACE_KEYWORDS].blind = True
    else:
        surfaces[SURFACE_KEYWORDS].incomplete(
            "keyword-source-unavailable: gh issue title could not be fetched and "
            "--keywords was not supplied"
        )

    # 2. PR surfaces — enumerated to COMPLETENESS, each over the transport that
    #    actually works for its state. Open PRs use `gh pr list` (one GraphQL
    #    request, fetched as `--limit cap+1`); closed PRs use the REST API with
    #    `--paginate` (#3587 — the GraphQL path resets on this host). Either
    #    way, hitting the cap marks the surface TRUNCATED, which keeps the run
    #    out of CLEAN. The `--search` filter is deliberately NOT used (the
    #    search API silently caps at 1000 results — a partial query wearing a
    #    complete face).
    for surface_name, state, limit in (
        (SURFACE_OPEN_PRS, "open", open_pr_limit),
        (SURFACE_CLOSED_PRS, "closed", closed_pr_limit),
    ):
        surface = surfaces[surface_name]
        if slug is None:
            surface.incomplete(
                "target-repo-unresolved: no owner/name for the target repo — "
                "refusing to enumerate PRs against an unknown repository "
                "(NOT clean)"
            )
            continue
        try:
            if state == "closed":
                prs = _closed_pr_list_rest(gh_bin, slug, cwd, closed_pr_timeout)
            else:
                args = ["pr", "list", "--state", state, "--repo", slug,
                        "--limit", str(limit + 1),
                        "--json", "number,title,body,headRefName,state,url"]
                prs = _gh_json(gh_bin, args, cwd, timeout)
            if not isinstance(prs, list):
                raise SurfaceError(f"gh {state}-PR enumeration returned non-list JSON")
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
        if target.path is None:
            surface.incomplete(
                f"no-local-clone: no local checkout of {slug or '(unknown repo)'} "
                "was found — this surface cannot describe the TARGET repo and is "
                "left INCOMPLETE rather than scanning a different one (NOT clean)"
            )
            continue
        try:
            refs = _git_refs(git_bin, cwd, namespace, timeout)
            scan_branch_surface(surface, refs, issue, keywords, min_keywords, allow_kw)
            surface.note = f"{len(refs)} ref(s) enumerated"
            if not allow_kw and keywords:
                surface.note += "; number-only (remote refs are stale/numerous)"
        except SurfaceError as exc:
            surface.incomplete(f"git-unavailable: {exc}")

    # 4. Worktree surface — UNTRUNCATED by construction; the count is proof.
    surface = surfaces[SURFACE_WORKTREES]
    if target.path is None:
        surface.incomplete(
            f"no-local-clone: no local checkout of {slug or '(unknown repo)'} was "
            "found — the worktree surface cannot describe the TARGET repo and is "
            "left INCOMPLETE rather than scanning a different one (NOT clean)"
        )
    else:
        try:
            rc, out, err, timed_out = _run(
                [git_bin, "worktree", "list", "--porcelain"], cwd, timeout
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
    target: RepoTarget,
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
    slug = _sanitize(target.slug) or "(unresolved)"
    lines.append(f"collision-preflight: issue #{issue}")
    lines.append(f"repo: {slug}   [resolved from: {target.source}]")
    lines.append(
        f"local checkout: {target.path or '(none — git surfaces INCOMPLETE)'}"
    )
    # The FULL title, never truncated: this line is what makes a wrong-target
    # read visible at the point of use (#4027). When it is unavailable the
    # report says so, but that is a VISIBILITY aid, not by itself a fail-closed
    # gate: `--keywords` can make the keyword dimension complete without a
    # title. The fail-closed signals are the keyword surface's own status (a
    # missing title with no --keywords is INCOMPLETE) and the BLIND annotation
    # on the verdict when no distinctive keyword exists at all.
    lines.append(
        f"title: {' '.join(_sanitize(title).split()) if title else '(unavailable — target not established)'}"
    )
    lines.append(
        f"keywords: {', '.join(_sanitize(k) for k in keywords) if keywords else '(none)'}"
    )
    lines.append(f"keyword gate: >= {max(1, min_keywords)} distinct DISTINCTIVE keyword(s) for a keyword-only hit")
    lines.append("")
    lines.append(f"{'SURFACE':<24} {'STATUS':<11} {'HITS':<5} NOTE")
    blind = [s for s in ordered if s.blind and s.status == STATUS_CLEAN]
    for surface in ordered:
        note = surface.note or ""
        if surface.truncated:
            note = (note + " " if note else "") + "⚠ TRUNCATED — list is partial"
        status = ("BLIND" if surface in blind else surface.status)
        lines.append(f"{surface.name:<24} {status:<11} {len(surface.hits):<5} {note}")
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
                lines.append(
                    f"  [{hit.surface}] {_sanitize(hit.ref)} — "
                    f"{_sanitize(hit.detail)} ({tag})"
                )
            if len(surface_hits) > MAX_HITS_SHOWN:
                # The COUNT is complete and visible; only the detail sample is
                # capped (a completeness check must never hide a count).
                lines.append(
                    f"  [{surface_name}] … +{len(surface_hits) - MAX_HITS_SHOWN} "
                    f"more hit(s) on this surface (total {len(surface_hits)})"
                )
    own_ignored = [(s.name, note) for s in ordered for note in s.own_ignored]
    if own_ignored:
        lines.append("")
        lines.append(
            "OWN FOOTPRINT (non-blocking — attributed to THIS lane by session/"
            "lane marker, so it is not a collision)"
        )
        for name, note in own_ignored:
            lines.append(f"  [{name}] {note}")
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
            f"{len({h.surface for h in hits})} surface(s) for #{issue} in {slug}; "
            "do NOT dispatch"
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
            f"hit(s) across {len({h.surface for h in hits})} surface(s) for #{issue} in "
            f"{slug}; do NOT dispatch"
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
            f"this is NOT clean for #{issue} in {slug} — fix gh auth/network and re-run"
        )
        return "\n".join(lines) + "\n", EXIT_INCOMPLETE
    if weak:
        lines.append(
            f"NOTE: {len(weak)} weak prose signal(s) ignored (cross-reference prose "
            "is not work; non-blocking)"
        )
    lines.append(
        f"VERDICT: CLEAN (exit {EXIT_CLEAN}) — {len(ordered)}/{len(ALL_SURFACES)} surfaces "
        f"queried, no in-flight work found for #{issue} in {slug}"
    )
    if blind:
        lines.append(
            f"  BLIND: {', '.join(s.name for s in blind)} yielded no distinctive "
            "keyword(s), so a keyword-only collision could be missed. Number "
            "matching and every other surface are unaffected; supply --keywords "
            "to restore the keyword dimension."
        )
    return "\n".join(lines) + "\n", EXIT_CLEAN


def _session_id_from_file(session_file: str | None) -> str | None:
    """The session UUID that is the recovery key, from `PI_SESSION_FILE`."""
    if not session_file:
        return None
    match = UUID_RE.search(Path(session_file).name)
    return match.group(0).lower() if match else None


def _resolve_target(args, timeout: float) -> tuple[RepoTarget | None, int]:
    """Resolve `--repo` (owner/name OR path OR omitted) into a RepoTarget.

    Returns (target, exit_code); exit_code != 0 means the CLI must abort.
    """
    repo_arg = args.repo
    requested = repo_arg is not None
    slug_explicit: str | None = None
    # An EMPTY/whitespace `--repo` is a usage error, never the cwd: `Path("")`
    # is a directory, so it used to take the PATH branch with path="" and run
    # the git surfaces against os.getcwd() while the report claimed "local
    # checkout: (none — git surfaces INCOMPLETE)". That is exactly the
    # unset-shell-variable accident this rejects up front.
    if repo_arg is not None and not repo_arg.strip():
        print(
            "collision-preflight: --repo must be `owner/name` or an existing "
            "directory; got an empty/whitespace value (an unset shell variable "
            "must not be reinterpreted as the cwd)",
            file=sys.stderr,
        )
        return None, EXIT_USAGE
    if repo_arg is None:
        path = os.getcwd()
        source = "cwd"
    elif Path(repo_arg).is_dir():
        path = repo_arg
        source = "--repo PATH"
    elif REPO_SLUG_RE.fullmatch((repo_arg or "").strip()):
        path = os.getcwd()
        slug_explicit = repo_arg.strip()
        source = "--repo owner/name"
    else:
        print(
            "collision-preflight: --repo must be `owner/name` or an existing "
            f"directory, got: {repo_arg!r}",
            file=sys.stderr,
        )
        return None, EXIT_USAGE

    target = RepoTarget(requested=requested)
    roots = repo_roots(args.git, path, timeout)
    if slug_explicit:
        current, _how = resolve_slug(args.gh, args.git, path, timeout)
        target.slug = slug_explicit
        target.source = source
        # GitHub and `gh --repo` are case-insensitive; compare that way so a
        # case-different selector still finds the local clone (and the canonical
        # resolved slug is what is sent to gh).
        if current and current.lower() == slug_explicit.lower():
            target.path = path
        else:
            target.path = find_local_clone(slug_explicit, args.git, timeout, roots)
    else:
        slug, how = resolve_slug(args.gh, args.git, path, timeout)
        target.slug = slug
        target.path = path
        target.source = f"{source} ({how})"
    return target, 0


def _ambiguity_refusal(args, target: RepoTarget, timeout: float) -> int | None:
    """When `--repo` was omitted, refuse rather than guess which repo #N is in.

    Returns an exit code to abort with, or None to proceed. A number that
    resolves in more than one candidate repo (or that resolves elsewhere but
    not here) must NEVER be silently resolved to whichever repo the cwd happens
    to be — that is the cross-repo false CLEAN of #4027.
    """
    if target.requested or not target.slug:
        return None
    cwd = target.path or os.getcwd()
    probe_timeout = min(timeout, AMBIGUITY_TIMEOUT)
    roots = repo_roots(args.git, cwd, timeout)
    try:
        slugs = candidate_slugs(args.git, timeout, roots)
    except Exception:  # pragma: no cover - defensive
        slugs = []
    if len(slugs) > CANDIDATE_REPO_CAP:
        print(
            f"collision-preflight: {len(slugs)} candidate repos exceed the "
            f"{CANDIDATE_REPO_CAP} cap — refusing to guess which one #{args.issue} "
            "belongs to; pass --repo owner/name",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    others = [s for s in slugs if s != target.slug]
    holds_others: list[str] = []
    unqueried: list[str] = []
    for other in others:
        verdict = probe_issue_in_repo(args.gh, other, args.issue, cwd, probe_timeout)
        if verdict is True:
            holds_others.append(other)
        elif verdict is None:
            unqueried.append(other)
    current_holds = probe_issue_in_repo(args.gh, target.slug, args.issue, cwd, probe_timeout)
    if current_holds is None:
        unqueried.insert(0, target.slug)
    holders = ([target.slug] if current_holds else []) + holds_others
    if len(holders) > 1:
        print(
            f"collision-preflight: AMBIGUOUS target — #{args.issue} resolves in "
            f"{', '.join(holders)}; refusing to guess. Re-run with --repo owner/name.",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    if current_holds is False and len(holds_others) == 1:
        print(
            f"collision-preflight: #{args.issue} does NOT exist in {target.slug} "
            f"(the current repo) but resolves in {holds_others[0]} — "
            f"re-run with --repo {holds_others[0]}",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    if unqueried:
        print(
            f"collision-preflight: cannot rule out a second repo for #{args.issue} "
            f"— these candidates could not be probed: {', '.join(unqueried)}. "
            "A partial check is never CLEAN; pass --repo owner/name to disambiguate.",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="collision_preflight",
        description="Fail-loud, all-surface in-flight-work pre-flight for a GitHub issue (#3061).",
    )
    parser.add_argument("issue", type=int, help="issue number to check, e.g. 3061")
    parser.add_argument(
        "--repo", default=None,
        help="target repo: `owner/name` (a local clone is located for the git "
             "surfaces) OR a path to a worktree of the target repo. Omitted: the "
             "current directory, with a cross-repo ambiguity refusal rather than a "
             "guess (#4027)",
    )
    parser.add_argument("--lane", default=os.environ.get(LANE_ENV) or None,
                        help="this lane's id (e.g. W0). Used to recognise the lane's "
                             f"OWN claim comments (env {LANE_ENV})")
    parser.add_argument("--session", default=os.environ.get(SESSION_ID_ENV) or None,
                        help="this session's UUID; also read from PI_SESSION_ID / "
                             "PI_SESSION_FILE")
    parser.add_argument("--keywords", default=None,
                        help="comma-separated keyword override when gh cannot supply the title")
    parser.add_argument("--min-keywords", type=int, default=DEFAULT_MIN_KEYWORDS,
                        help="distinct DISTINCTIVE keywords required for a keyword-only hit "
                             f"(default {DEFAULT_MIN_KEYWORDS}; 1 disables the count gate)")
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
                        help="closed-PR completeness cap applied to the full REST enumeration; a "
                             "longer list is TRUNCATED/INCOMPLETE. It truncates the SCAN, not the "
                             "fetch — the enumeration itself is bounded only by --closed-pr-timeout "
                             f"(default {CLOSED_PR_LIMIT}; env COLLISION_PREFLIGHT_CLOSED_PR_LIMIT)")
    # Deliberately NO ``type=float`` here. A bad value must be EXIT_USAGE, and
    # neither argparse's own error path (exits 2 == EXIT_INCOMPLETE) nor an
    # eagerly-converted env default (uncaught ValueError -> exit 1 ==
    # EXIT_COLLISION) reports a misconfiguration as itself. Validated below.
    parser.add_argument("--closed-pr-timeout",
        default=os.environ.get("COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT", CLOSED_PR_TIMEOUT),
        metavar="SECS",
        help="wall-clock budget (secs) for the closed-PR REST enumeration, which is "
             f"multi-request (default {CLOSED_PR_TIMEOUT:g}; env "
             "COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT)")
    args = parser.parse_args(argv)

    if args.issue <= 0:
        print("collision-preflight: issue number must be positive", file=sys.stderr)
        return EXIT_USAGE
    if args.timeout <= 0 or not math.isfinite(args.timeout):
        print("collision-preflight: --timeout must be finite and > 0", file=sys.stderr)
        return EXIT_USAGE
    if args.min_keywords < 1:
        print("collision-preflight: --min-keywords must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.pr_limit < 1 or args.closed_pr_limit < 1:
        print("collision-preflight: --pr-limit / --closed-pr-limit must be >= 1",
              file=sys.stderr)
        return EXIT_USAGE

    # `nan`/`inf` are the trap: `nan <= 0` and `inf <= 0` are both False, so a
    # bare positivity check passes them to subprocess.run(timeout=…), where they
    # raise ValueError/OverflowError out of `_run` — no VERDICT line, exit 1 (the
    # COLLISION code). Reject non-numeric and non-finite explicitly.
    try:
        closed_pr_timeout = float(args.closed_pr_timeout)
    except (TypeError, ValueError):
        print("collision-preflight: --closed-pr-timeout must be a number > 0",
              file=sys.stderr)
        return EXIT_USAGE
    if not math.isfinite(closed_pr_timeout) or closed_pr_timeout <= 0:
        print("collision-preflight: --closed-pr-timeout must be > 0", file=sys.stderr)
        return EXIT_USAGE

    target, code = _resolve_target(args, args.timeout)
    if target is None:
        return code

    # Ambiguity refusal only when `--repo` was omitted: an explicit `--repo`
    # (either form) is the operator's disambiguation and must not be second-guessed.
    refusal = _ambiguity_refusal(args, target, args.timeout)
    if refusal is not None:
        return refusal

    # Identity: the GitHub login plus the lane/session that actually distinguish
    # one lane from another on an account shared by every lane.
    session_id = (args.session or os.environ.get(SESSION_ID_ENV)
                  or _session_id_from_file(os.environ.get(SESSION_FILE_ENV)))
    identity = Identity(login=None, lane=args.lane, session_id=session_id)
    rc, out, _err, _to = _run(
        [args.gh, "api", "user", "-q", ".login"],
        target.path or os.getcwd(), args.timeout,
    )
    if rc == 0 and out.strip():
        identity.login = out.strip()

    try:
        ordered, title, keywords, issue, _ = run_preflight(
            args.issue, target, args.gh, args.git, args.timeout, args.keywords,
            args.min_keywords, args.pr_limit, args.closed_pr_limit,
            closed_pr_timeout, identity,
        )
        report, code = format_report(
            ordered, issue, target, title, keywords, args.min_keywords
        )
    except RuntimeError as exc:  # partial-run guard
        print(f"collision-preflight: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sys.stdout.write(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
