#!/usr/bin/env python3
"""issue_process_lint — two ADVISORY checks that make an issue's own process visible.

The defect this exists for (measured 2026-11-05)
------------------------------------------------
A lane's process lived in its **dispatch brief**. A brief is not attached to the issue and the next
lane never reads it, so an issue picked up by a lane whose dispatch forgot a step is worked without
that step — and the issue cannot say so.

The sharper half: **an issue states a PROBLEM, and its completion criteria do not exist at creation.**
They are produced by scoping, which happens *after* the issue is filed (owner ruling, 2026-11-05).
So the design is two-stage — the **body** carries the process, and scoping posts a **`## Satisfies`
comment** that is the finish line. See `docs/ops/ISSUE-WORKING-PROCESS.md`.

Which makes exactly two shapes defects, and this tool flags only those:

=========================  =========================================================================
arm                        flagged when
=========================  =========================================================================
``pr-unclaimed-issue``     a PR **claims** an issue (``Refs`` / ``Related`` / ``Part of`` / a bare
                           ``#N`` on that line) and that issue is resolved by **neither**
                           ``Satisfies #N`` **nor** a closing keyword (``Closes``/``Fixes``/
                           ``Resolves``). Measured (#10534): of 18 PRs naming a P0/P1, 18 used
                           ``Refs`` and 5 of 29 used a closing keyword — so delivered work never
                           closed and the count never moved.
``wip-no-satisfies``       an issue has WIP but **no ``## Satisfies`` checklist** anywhere in its
                           comments — work started before the completion criteria existed.
=========================  =========================================================================

A CLAIM, NOT A MENTION — and why that distinction is the whole arm
------------------------------------------------------------------
The first version of this arm counted every ``#N`` in the body and flagged 19 of 28 live open PRs
(68 %), because a PR body is full of references that are not claims: a criterion id in prose
("reds only #10"), a PR's own number quoted from a review, an existing guard cited as precedent, a
command inside inline code, a tracking note inside an HTML comment. Each of those is a *mention*.
A claim is a reference the author is using to say **"this PR serves this issue"**, so the ref must sit
on a line that carries a claiming word, and this module reads only those — plus it excludes the PR's
own number, inline code spans, HTML comments and fenced blocks.

⛔ Two populations are DELIBERATELY NOT FLAGGED, and the tests pin them as negative controls:

* a **fresh issue with no criteria** — normal, the scoping has not run yet;
* a **PR that claims no issue** — not every change serves an issue.

A false positive in either direction points bureaucracy at legitimate work, which is worse than the
defect. That is the whole reason this tool is advisory.

ADVISORY, NEVER A GATE — and the exit codes say so honestly
------------------------------------------------------------
``0`` nothing flagged · ``3`` findings · ``2`` unmeasurable. There is no ``--fail`` mode; nothing
mounts it; it is not in ``python-ci-gate.needs`` and not in ``.mergify.yml``. Any workflow step that
ever mounts it must carry ``continue-on-error: true`` — the shape of ``tools/closes_landing.py`` +
``.github/workflows/closes-coherence.yml`` (#10597: "not a new gate"), and
``tests/test_issue_process_lint.py`` parses the workflow YAML to enforce it per-step rather than
trusting the string to appear somewhere in the file.

WHAT THIS TOOL DOES **NOT** CONSULT — declared, so its silence is not read as health
---------------------------------------------------------------------------------
The ``wip-no-satisfies`` arm reads the **durable textual marker** the process block mandates — a
non-empty ``IN FLIGHT: <ids> …`` line in a comment — and nothing else. It does **not** enumerate
branches, worktrees or ref-claims. That population is answered by ``tools/who_is_on.py <N> --repo .``
(the supported "who is on #N" verb, which sees local-only branches and uncommitted worktrees that a
remote-only query cannot — #4256); a caller that has already run it injects its verdict with
``--wip`` / ``--no-wip``.

Building a second branch census here would be a second source of truth that can disagree with the
first, which this repo has already paid for (AGENTS.md: *"a second census path that can disagree"*).
So the arm is explicit about its bound in the OUTPUT, not only here: every run prints how many
subjects carried a marker and how many carried none, because **``0 markers read`` and ``0 issues with
WIP`` are different claims** and a bare "NOTHING FLAGGED" would launder one into the other.

Why the fence/inline-code scan
------------------------------
``tools/closes_landing.py`` already paid for the naive version of this: a fence opened on a
list-marker line was read as prose and manufactured a ``lands``. The same naive read here would
manufacture a *flag* about a line the author quoted rather than claimed.

That grammar is therefore **not re-derived here**. The first version of this module did re-derive it
— it recognised a fence only when the first non-space character of the line was a backtick/tilde —
and a quoted example written as ``- ```markdown`` (a fence ON the list-marker line) or ``> ```markdown``
(inside a blockquote) was read as prose, so ``Refs #1234`` inside the quotation became a CLAIM and arm 1
flagged a line the author quoted rather than claimed (review P2). A second copy of a grammar is how
that drift happens; the hardened copy is ``tools/closes_landing.py::strip_code``, which carries the
container rules (a fence may open ON the marker line, at the item's content column; a fence belongs
to its container and ends when that container does; containers compare by DEPTH), so this module
imports it instead of carrying a weaker third copy.

An **unterminated** fence hides the rest of the body from the scan, which is a MISS in the silent
direction, so that case is reported in the output — in EVERY mode that reads a body, including the
sweeps, rather than only in the offline ones.

Usage
-----
    uv run python tools/issue_process_lint.py --pr 11027
    uv run python tools/issue_process_lint.py --issue 4282
    uv run python tools/issue_process_lint.py --pr 11027 --wip        # (issue arms only)
    uv run python tools/issue_process_lint.py --sweep                  # open PRs
    uv run python tools/issue_process_lint.py --sweep-issues --label P0-blocker --limit 20
    uv run python tools/issue_process_lint.py --body-file /tmp/body.md
    uv run python tools/issue_process_lint.py --issue-body-file /tmp/i.md \
        --comments-file /tmp/c.json --wip
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level 3.11+-only import would
# fail first (D9 shape). Must be the FIRST statement after the docstring and `__future__`, and
# `tests/test_entry_point_python_guard.py` derives its corpus from `tools/*.py`, so a new tool
# without this guard reds a test in `main` the moment it is staged.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/issue_process_lint.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/issue_process_lint.py`"
    )

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# #5128-adjacent: the fence grammar is NOT duplicated here. `tools/closes_landing.py::strip_code`
# carries the container rules this module needs (a fence may open ON a list-marker line, at the
# item's content column; a fence belongs to its container and ends with it; containers compare by
# DEPTH), and it is the copy a real CommonMark renderer is checked against in
# `tests/test_closes_landing.py`. A second copy here was the review's P2 defect: a quoted example
# inside `- ``` … ` or `> ``` … ` was read as prose, so the quote's `Refs #N` became a claim. The
# repo-root insert is the pattern `tools/ask_spotcheck.py` uses for the same reason (this file runs
# BOTH as a loose script, where `sys.path[0]` is `tools/`, and as `tools.issue_process_lint` from a
# test, where it is the repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Both grammars are IMPORTED rather than re-derived, for the same measured reason: a weaker copy
# here is a false positive on legitimate work. `strip_code` owns the fence grammar, and
# `closes_landing`'s own history records the inline one — a QUOTED `Closes #N` written between TWO
# backticks on each side counted as a claim (#10634 review P3-2). A single-backtick regex re-derived
# in this module reproduced exactly that: ``Refs #1234`` was left as prose and flagged. CommonMark
# closes a code span with a run of the SAME length, so run-length is the grammar's question.
from tools.closes_landing import _strip_inline_code as _strip_inline_code_spans  # noqa: E402
from tools.closes_landing import strip_code as _strip_fenced_blocks  # noqa: E402

# --- exit codes (the caller's contract; see ADVISORY above) -------------------------------
RC_CLEAN = 0
RC_UNMEASURABLE = 2
RC_FINDINGS = 3

CLOSING_WORDS = ("close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved")
SATISFIES_WORDS = ("satisfy", "satisfies", "satisfied")

# A claim is a reference the author attaches a **claiming word** to. Anything else is a mention.
# The window between the word and the `#` is short on purpose: "See #12 for background, and this
# closes nothing" must not read as a closing claim, and a long window is how that happens.
CLAIM_RE = re.compile(
    r"\b(?P<kw>refs?|related|part\s+of|see\s+also|satisf(?:y|ies|ied)|"
    r"close[sd]?|fix(?:e[sd])?|resolve[sd]?|serves?|addresses)\b"
    r"(?P<mid>[^\n#]{0,16}?)(?:(?P<qual>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<num>\d{1,7})\b"
    # A COMMA-CONTINUED list is claimed in full: `Part of #10085, #10063` claims BOTH, and
    # reading only the first dropped every subject after it (F3) — measured on three open PRs
    # (#11038, #11065, #11093). The list shape is unambiguous in a way a bare `#N` elsewhere on
    # the line is not, so only the comma-continued form is taken.
    r"(?P<more>(?:\s*,\s*(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d{1,7}\b)*)",
    re.IGNORECASE,
)
# One entry per continued ref; a named group inside a repeated group keeps only the LAST match, so
# the continuation is re-scanned rather than captured in place.
CONTINUATION_RE = re.compile(
    r"(?:(?P<qual>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<num>\d{1,7})\b",
    re.IGNORECASE,
)
# An author may say, in the body, that the reference is deliberately NOT a closing one — a
# measurement PR that serves an issue without fixing it. That sentence is the author's answer, so
# reading it as a defect would punish exactly the honesty the block asks for.
# Markdown emphasis sits *between* the words in real bodies — measured: "deliberately **not**
# `Closes`" defeats a plain `\s+` separator, so the separator admits emphasis characters.
_EMPH = r"[\s*_`]+"
DECLINE_RE = re.compile(
    rf"deliberately{_EMPH}not|does{_EMPH}not{_EMPH}clos|not{_EMPH}(?:a{_EMPH})?clos"
    rf"|non-closing|no{_EMPH}closing",
    re.IGNORECASE,
)
SATISFIES_HEADING_RE = re.compile(r"^[ \t]{0,3}#{2,3}[ \t]*Satisfies\b", re.IGNORECASE | re.MULTILINE)
CHECKBOX_RE = re.compile(r"^[ \t]*[-*][ \t]+\[[ xX]\]", re.MULTILINE)
IN_FLIGHT_RE = re.compile(r"IN[ -]FLIGHT\s*:\s*(?P<rest>.*)$", re.IGNORECASE)
# A marker whose remainder is only a placeholder (`<ids> — branch <b> …`) is the CONVENTION being
# quoted, not a claim; a remainder that says the issue was released is a release, not a claim.
PLACEHOLDER_RE = re.compile(r"^[\s<>a-zA-Z0-9_./—-]*$")
RELEASE_WORDS = ("none", "released", "unclaimed", "free", "—", "-")
# The unambiguous release words, but only in an ANNOTATION POSITION (F6). An unanchored anywhere-
# scan is a silent MISS in the flagging direction: the marker the process block mandates carries
# author text after the ids (`IN FLIGHT: 4282 — branch fix/unclaimed-issue-arm2 — box cell-3`), and
# `unclaimed` is this tool's OWN vocabulary, so a lane named after the arm it is working on would
# clear its own WIP marker. A release is a delimited annotation — `— released …`, `(released)`,
# `; none` — so that is what is matched. `free` is excluded entirely: it heads a release, but also
# appears in ordinary work (`free tier`), so it stays a FIRST-TOKEN-only word.
RELEASE_ANYWHERE_RE = re.compile(
    r"(?:^|[—–\-(;,])\s*(?:none|released|unclaimed)\b", re.IGNORECASE)

ARM_PR = "pr-unclaimed-issue"
ARM_ISSUE = "wip-no-satisfies"


@dataclass
class Finding:
    arm: str
    subject: str
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"arm": self.arm, "subject": self.subject, "detail": self.detail,
                "evidence": self.evidence}

    def annotate(self) -> str:
        return f"::warning title=issue-process-lint {self.arm}::{self.subject} — {self.detail}"


class Unmeasurable(Exception):
    """The question was not answered (gh missing, rate limited, no body read). Never a pass."""


# --- normalisation --------------------------------------------------------------------------
def strip_code(body: str) -> tuple[str, int]:
    """Return (text with fenced blocks, inline-code spans and HTML comments removed, hidden_lines).

    `hidden_lines` is the number of lines an UNTERMINATED fence hid from the scan: that is a miss in
    the silent direction, so the caller reports it instead of passing over it.

    The FENCE grammar is `tools/closes_landing.py::strip_code`'s — imported, not copied (see the
    module docstring): it knows that a fence may open ON a list-marker line or inside a blockquote,
    and that a fence ends with its container. This wrapper adds the two things that are this
    module's own concern: HTML comments (which may span lines and contain fences) are removed
    FIRST, and inline code spans are blanked AFTER, because `gh pr view #1234` is a command being
    quoted, not a claim.
    """
    # HTML comments first — they may span lines and may contain fences.
    text = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    stats: dict[str, int] = {}
    text = _strip_fenced_blocks(text, stats=stats)
    # Inline code spans: `gh pr view #1234` is a command being quoted, not a claim. Imported, not
    # re-derived — a single-backtick regex here read a ``Refs #1234`` span as prose (F1).
    text = _strip_inline_code_spans(text)
    return text, stats.get("hidden_lines", 0)


def claims(body: str, own_number: int | None = None,
           repo: str | None = None) -> dict[str, list[int]]:
    """The refs this body CLAIMS, grouped by what the claim does.

    `closing` / `satisfies` resolve an issue; `weak` (`Refs`, `Related`, `Part of`, `See also`)
    links nothing and closes nothing — that is the #10534 hole.
    """
    return _claims_in(strip_code(body)[0], own_number, repo)


def _claims_in(text: str, own_number: int | None = None,
               repo: str | None = None) -> dict[str, list[int]]:
    """`claims` over ALREADY-normalised text, so a caller that needs `hidden_lines` from the same
    pass does not strip the body twice (two passes of a pure function cannot disagree, but a caller
    that reported a count from one pass and classified from another would be reading two bodies)."""
    found: dict[str, list[int]] = {"closing": [], "satisfies": [], "weak": [], "declined": []}
    declined_lines = {i for i, line in enumerate(text.splitlines()) if DECLINE_RE.search(line)}
    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    for m in CLAIM_RE.finditer(text):
        line_no = sum(1 for st in line_starts if st <= m.start()) - 1
        into = found["declined"] if line_no in declined_lines else None
        word = re.sub(r"\s+", " ", m.group("kw").lower())
        # The claiming word governs its WHOLE same-line list (F3).
        refs = [(m.group("qual"), int(m.group("num")))]
        refs += [(c.group("qual"), int(c.group("num")))
                 for c in CONTINUATION_RE.finditer(m.group("more") or "")]
        for qual, num in refs:
            if own_number is not None and num == own_number:
                continue  # a PR quoting its own number from a review is not claiming an issue
            if qual and (repo is None or qual.lower() != repo.lower()):
                continue  # `owner/repo#N` for another repo is not this PR's business
            if into is not None:
                into.append(num)
            elif word in CLOSING_WORDS:
                found["closing"].append(num)
            elif word in SATISFIES_WORDS:
                found["satisfies"].append(num)
            else:
                found["weak"].append(num)
    for key in found:
        found[key] = sorted(set(found[key]))
    return found


def claim_issues(body: str, own_number: int | None = None) -> list[int]:
    """Every issue this body claims, whatever the strength of the claim."""
    c = claims(body, own_number)
    return sorted((set(c["closing"]) | set(c["satisfies"]) | set(c["weak"])) - set(c["declined"]))


def has_satisfies_checklist(comment_bodies: list[str]) -> bool:
    """A `## Satisfies` heading in any comment, plus at least one checkbox in ANY comment.

    Deliberately not "both in the same comment": criteria are often ticked in a follow-up comment
    once they are proved, and requiring co-location would re-flag an issue the moment it finishes.
    """
    # Read through `strip_code`, exactly as `in_flight_markers` does. A comment that SHOWS the
    # format inside a fence is an example, not a checklist: matched against the raw bodies it
    # silently cleared the arm it exists to raise, on an issue with WIP and no real criteria (F2).
    headings = [SATISFIES_HEADING_RE.search(strip_code(b)[0]) for b in comment_bodies]
    boxes = [CHECKBOX_RE.search(strip_code(b)[0]) for b in comment_bodies]
    return any(headings) and any(boxes)


def _marker_rest(line: str) -> str:
    """The text after `IN FLIGHT:`, with markdown emphasis/bullets stripped."""
    rest = line.split(":", 1)[1] if ":" in line else ""
    rest = rest.strip()
    rest = rest.strip("*_`").strip()          # **IN FLIGHT:** / _IN FLIGHT:_
    rest = rest.strip("-–— ").strip()
    return rest


def in_flight_markers(comment_bodies: list[str]) -> list[str]:
    """Non-empty `IN FLIGHT: <ids>` markers — the durable WIP signal the process block mandates.

    Three shapes are NOT markers, because each is the line surviving into a state that is not WIP:
    an empty remainder, a remainder that is only the convention's placeholders (`<ids> — branch <b>
    …`, present whenever a comment *quotes* the block), and a released/none remainder.
    """
    markers: list[str] = []
    for body in comment_bodies:
        text, _ = strip_code(body)
        for line in text.splitlines():
            if not IN_FLIGHT_RE.search(line):
                continue
            rest = _marker_rest(line)
            if not rest:
                continue
            if rest.lower().split()[0].strip(":.,;") in RELEASE_WORDS:
                continue
            # The marker is often ANNOTATED rather than replaced: `IN FLIGHT: 3.2c — released
            # 2026-11-05` / `IN FLIGHT: 3.2c (released)`. Reading only the FIRST token left both
            # as live WIP and flagged finished work (F6). Matched in an ANNOTATION POSITION only,
            # so a release word inside a branch slug cannot clear a real marker.
            if RELEASE_ANYWHERE_RE.search(rest):
                continue
            if "<" in rest and ">" in rest and PLACEHOLDER_RE.match(rest):
                continue  # the unsubstituted template
            markers.append(rest)
    return markers


# --- the two arms ---------------------------------------------------------------------------
def classify_pr(body: str, number: int | str, repo: str | None = None,
                labels: list[str] | None = None,
                notes: list[str] | None = None) -> list[Finding]:
    """ARM 1 — a PR that claims an issue which no keyword resolves.

    `notes`, when supplied, receives the UNTERMINATED-FENCE note for this body. It lives here rather
    than in each CLI branch because only two of the four body-reading modes collected it: a
    `--sweep` silently swallowed the one thing that says the scan could not see the rest of a body —
    and a sweep is the one mode that reads a POPULATION (review P2, adjacent).
    """
    text, hidden = strip_code(body)
    if hidden and notes is not None:
        notes.append(f"an unterminated fence hid {hidden} line(s) from the scan in #{number}")
    own = number if isinstance(number, int) and number > 0 else None
    c = _claims_in(text, own, repo)
    claimed = sorted((set(c["closing"]) | set(c["satisfies"]) | set(c["weak"]))
                     - set(c["declined"]))
    if not claimed:
        return []  # negative control: a PR that claims no issue is legitimate
    resolved = set(c["closing"]) | set(c["satisfies"])
    unresolved = [n for n in claimed if n not in resolved]
    if not unresolved:
        return []
    detail = (f"claims #{', #'.join(str(n) for n in unresolved)} with only a linking word "
              f"(`Refs`/`Related`/`Part of`) — neither `Satisfies #N: <ids>` nor a closing keyword "
              f"(Closes/Fixes/Resolves) resolves it, so delivered work cannot close what it serves")
    labels = labels or []
    if "owner-decision" in labels:
        # An owner-authored BODY is a record; the PR body is still the author's own, so the label
        # changes nothing. Stated so nobody "fixes" this by exempting it.
        detail += " (an owner-authored body is not exempt: the PR body is the author's)"
    return [Finding(arm=ARM_PR, subject=f"PR #{number}", detail=detail,
                    evidence={"claimed": claimed, "unresolved": unresolved,
                              "resolved_by_closing": c["closing"],
                              "resolved_by_satisfies": c["satisfies"]})]


def classify_issue(comment_bodies: list[str], number: int | str,
                   wip: bool | None = None,
                   notes: list[str] | None = None) -> tuple[list[Finding], dict]:
    """ARM 2 — WIP with no `## Satisfies` checklist.

    Returns (findings, measured) where `measured` carries the marker count, because "0 markers read"
    and "0 issues with WIP" are different claims and the caller must print the first.

    `notes`, when supplied, receives the UNTERMINATED-FENCE note for this issue's bodies. Arm 1 got
    this first (a `--sweep` swallowed it); the issue modes kept swallowing it, so the one thing that
    says "the scan could not see the rest of this body" was reported for a PR and silently dropped
    for the same content read as an issue — the same one-population-two-measurements shape as the
    discarded issue body below.
    """
    hidden = sum(strip_code(body)[1] for body in comment_bodies)
    if hidden and notes is not None:
        notes.append(f"an unterminated fence hid {hidden} line(s) from the scan in #{number}")
    markers = in_flight_markers(comment_bodies)
    if wip is None:
        is_wip = bool(markers)
        source = "comment-marker"
    else:
        is_wip = wip
        source = "injected"
    measured = {"wip_source": source, "markers": markers[:3], "marker_count": len(markers),
                "had_satisfies": has_satisfies_checklist(comment_bodies)}
    if not is_wip:
        return [], measured  # negative control: a fresh issue has no criteria yet, by design
    if measured["had_satisfies"]:
        return [], measured
    detail = ("has WIP but no `## Satisfies` checklist in its comments — work started before the "
              "completion criteria existed, so nothing can tell this issue it is finished "
              "(SCOPING posts that comment)")
    populations = ([] if source == "injected"
                   else ["branches", "worktrees", "ref-claims"])
    return [Finding(arm=ARM_ISSUE, subject=f"issue #{number}", detail=detail,
                    evidence={**measured, "populations_not_consulted": populations})], measured


# --- measurement (gh) ---------------------------------------------------------------------
def _gh(args: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise Unmeasurable(f"gh not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unmeasurable(f"gh {' '.join(args)} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise Unmeasurable(f"gh {' '.join(args)} -> rc={proc.returncode}: {err[0] if err else ''}")
    return proc.stdout


def resolve_repo(repo: str | None) -> str | None:
    """The ambient `owner/name` — without it every self-qualified `owner/repo#N` claim is dropped."""
    if repo:
        return repo
    try:
        return json.loads(_gh(["repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
    except (Unmeasurable, KeyError, json.JSONDecodeError):
        return None


def fetch_pr(number: int, repo: str | None) -> tuple[str, list[str]]:
    args = ["pr", "view", str(number), "--json", "body,labels"]
    if repo:
        args += ["--repo", repo]
    data = json.loads(_gh(args))
    return data.get("body") or "", [lb.get("name", "") for lb in data.get("labels") or []]


def fetch_issue(number: int, repo: str | None) -> tuple[str, list[str]]:
    args = ["issue", "view", str(number), "--json", "body,comments"]
    if repo:
        args += ["--repo", repo]
    data = json.loads(_gh(args))
    return data.get("body") or "", [(c.get("body") or "") for c in data.get("comments") or []]


def sweep_prs(repo: str | None, limit: int) -> list[tuple[int, str, list[str]]]:
    args = ["pr", "list", "--state", "open", "--json", "number,body,labels", "--limit", str(limit)]
    if repo:
        args += ["--repo", repo]
    return [(p["number"], p.get("body") or "", [lb.get("name", "") for lb in p.get("labels") or []])
            for p in json.loads(_gh(args))]


def sweep_issues(repo: str | None, limit: int, label: str | None) -> list[tuple[int, list[str]]]:
    args = ["issue", "list", "--state", "open", "--json", "number", "--limit", str(limit)]
    if label:
        args += ["--label", label]
    if repo:
        args += ["--repo", repo]
    out = []
    for row in json.loads(_gh(args)):
        # The issue's own BODY is a comment-shaped input, exactly as the offline
        # `--issue-body-file` branch says: a checklist or a claim may live there. Discarding it here
        # measured a strictly smaller population than the offline path for the SAME content, so an
        # issue whose `## Satisfies` checklist sits in its body read as *WIP with no checklist*
        # through `--sweep-issues` and *clean* through `--issue-body-file` (review P2). One
        # population, one measurement.
        body, bodies = fetch_issue(row["number"], repo)
        out.append((row["number"], [body, *bodies]))
    return out


# --- CLI ----------------------------------------------------------------------------------
def _comments_from_file(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]  # a bare markdown file is one comment body
    if isinstance(data, list):
        return [c if isinstance(c, str) else (c.get("body") or "") for c in data]
    if isinstance(data, dict):
        return [(c.get("body") or "") for c in data.get("comments") or []]
    return [raw]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Advisory: flag a PR claiming an issue that nothing resolves, and an issue with "
                    "WIP but no `## Satisfies` checklist. Never a gate (rc 0 clean / 3 findings / "
                    "2 unmeasured).")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--pr", type=int, help="classify one PR by number")
    which.add_argument("--issue", type=int, help="classify one issue by number")
    which.add_argument("--sweep", action="store_true", help="classify every open PR")
    which.add_argument("--sweep-issues", action="store_true", help="classify every open issue")
    which.add_argument("--body-file", type=Path, help="offline: a PR body")
    which.add_argument("--issue-body-file", type=Path, help="offline: an issue body")
    ap.add_argument("--repo", default=None, help="owner/name (default: resolved from gh)")
    ap.add_argument("--comments-file", type=Path, default=None,
                    help="offline issue mode: comment bodies (JSON list, gh-shaped, or markdown)")
    ap.add_argument("--wip", action="store_true",
                    help="inject a WIP verdict (e.g. tools/who_is_on.py found a branch/worktree) — "
                         "single-issue modes only")
    ap.add_argument("--no-wip", action="store_true", help="inject the opposite verdict")
    ap.add_argument("--label", default=None, help="--sweep-issues only: restrict the population")
    ap.add_argument("--limit", type=int, default=50, help="--sweep/--sweep-issues page size")
    ap.add_argument("--annotate", action="store_true",
                    help="emit GitHub Actions ::warning lines (for a warn-only workflow step)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    wip: bool | None = None
    if args.wip and args.no_wip:
        ap.error("--wip and --no-wip are mutually exclusive")
    if args.wip:
        wip = True
    if args.no_wip:
        wip = False
    if wip is not None and (args.sweep or args.sweep_issues):
        # One issue's verdict cannot speak for a population — that would flag (or clear) every
        # subject on a state measured for one of them.
        ap.error("--wip/--no-wip answer ONE issue; they cannot be applied to --sweep/--sweep-issues")
    if args.label and args.sweep:
        # `--label` filters the ISSUE population (`sweep_issues`); `--sweep` reads PRs and has no
        # label filter — so the combination silently swept 50 unfiltered PRs while the doc's own
        # example implied a filtered population (F4). Refuse it, as `--wip` is refused above.
        ap.error("--label applies to --sweep-issues, not --sweep")

    findings: list[Finding] = []
    measured_rows: list[dict] = []
    notes: list[str] = []
    try:
        # Resolve the repo through `gh` ONLY in the modes that reach GitHub. The `--body-file` and
        # `--issue-body-file` modes are documented as OFFLINE, and making an unannounced network
        # call from them falsified that (and, on a box without `gh`, silently dropped every
        # self-qualified `owner/repo#N` claim — a smaller population measured without refusing).
        # An explicit `--repo` is still honoured offline; it just is not looked up.
        live_mode = bool(args.pr or args.issue or args.sweep or args.sweep_issues)
        repo = resolve_repo(args.repo) if live_mode else args.repo
        if args.body_file is not None:
            body = args.body_file.read_text(encoding="utf-8")
            findings += classify_pr(body, "body-file", repo, notes=notes)
            population = "1 body (offline)"
        elif args.issue_body_file is not None:
            bodies = _comments_from_file(args.comments_file) if args.comments_file else []
            body = args.issue_body_file.read_text(encoding="utf-8")
            # The issue's own body is a comment-shaped input: a checklist or a claim may live there.
            found, measured = classify_issue([body, *bodies], "issue-body-file", wip, notes)
            findings += found
            measured_rows.append(measured)
            population = f"1 issue body + {len(bodies)} comment(s) (offline)"
        elif args.pr is not None:
            body, labels = fetch_pr(args.pr, repo)
            findings += classify_pr(body, args.pr, repo, labels, notes=notes)
            population = "1 PR"
        elif args.issue is not None:
            # The body is a comment-shaped input on the LIVE path too — the offline sibling twelve
            # lines above has always said so; this one discarded it, so identical content got two
            # verdicts depending on whether it was read from gh or from a file (review P2).
            body, bodies = fetch_issue(args.issue, repo)
            found, measured = classify_issue([body, *bodies], args.issue, wip, notes)
            findings += found
            measured_rows.append(measured)
            population = f"1 issue ({len(bodies)} comment(s) + body)"
        elif args.sweep:
            rows = sweep_prs(repo, args.limit)
            for number, body, labels in rows:
                findings += classify_pr(body, number, repo, labels, notes=notes)
            population = f"{len(rows)} open PR(s)"
        else:  # --sweep-issues
            rows = sweep_issues(repo, args.limit, args.label)
            for number, bodies in rows:
                found, measured = classify_issue(bodies, number, wip, notes)
                findings += found
                measured_rows.append(measured)
            population = f"{len(rows)} open issue(s)"
    except Unmeasurable as exc:
        print(f"issue_process_lint: UNMEASURED — {exc}", file=sys.stderr)
        return RC_UNMEASURABLE

    marker_reads = sum(1 for m in measured_rows if m["marker_count"])
    markerless = sum(1 for m in measured_rows if not m["marker_count"] and m["wip_source"] == "comment-marker")

    if args.json:
        print(json.dumps({"population": population, "notes": notes,
                          "wip_read": {"subjects": len(measured_rows),
                                       "with_marker": marker_reads,
                                       "markerless": markerless,
                                       "populations_not_consulted":
                                           # An INJECTED verdict CAME from that population, so
                                           # claiming it was not consulted is false (F7) — derive
                                           # it, do not hard-code it.
                                           ([] if measured_rows and all(
                                               m["wip_source"] == "injected" for m in measured_rows)
                                            else ["branches", "worktrees", "ref-claims"])},
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    elif args.annotate:
        for note in notes:
            print(f"::notice title=issue-process-lint::{note}")
        for f in findings:
            print(f.annotate())
        if measured_rows:
            # The annotation note must describe THIS run, exactly as the human/`--json` modes do —
            # an injected verdict CAME from the population, so calling it "not consulted" made one
            # run describe itself two incompatible ways (F7).
            if all(m["wip_source"] == "injected" for m in measured_rows):
                flag = "--wip" if wip else "--no-wip"
                print(f"::notice title=issue-process-lint::WIP verdict INJECTED ({flag}) from the "
                      f"branches/worktrees/ref-claims population tools/who_is_on.py owns")
            else:
                print(f"::notice title=issue-process-lint::WIP arm read {marker_reads} subject(s) "
                      f"carrying an `IN FLIGHT:` marker; {markerless} carried none "
                      f"(branches/worktrees NOT consulted — tools/who_is_on.py owns that population)")
        if not findings:
            print(f"::notice title=issue-process-lint::nothing flagged over {population}")
    else:
        print(f"issue_process_lint (advisory) — population: {population}")
        for note in notes:
            print(f"  ! {note}")
        if measured_rows:
            print(f"  WIP arm: {len(measured_rows)} subject(s) read — {marker_reads} carried an "
                  f"`IN FLIGHT:` marker, {markerless} carried none.")
            # The disclaimer must describe THIS run: under an INJECTED `--wip` verdict the
            # population WAS consulted and the verdict came from it (F7).
            if measured_rows and all(m["wip_source"] == "injected" for m in measured_rows):
                flag = "--wip" if wip else "--no-wip"
                print(f"           WIP verdict INJECTED ({flag}) from the branches/worktrees/"
                      "ref-claims population tools/who_is_on.py owns — that is where this "
                      "verdict came from, not the marker count above.")
            else:
                print("           branches/worktrees/ref-claims NOT consulted "
                      "(tools/who_is_on.py owns that population) — so 'no marker' is not 'no WIP'.")
        if not findings:
            print("  NOTHING FLAGGED")
        for f in findings:
            print(f"  ⚠️  {f.subject} [{f.arm}] — {f.detail}")
        print(f"  advisory only (rc {RC_CLEAN}/{RC_FINDINGS}/{RC_UNMEASURABLE}); "
              f"never a required check.")
    return RC_FINDINGS if findings else RC_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
