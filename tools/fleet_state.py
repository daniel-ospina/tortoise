#!/usr/bin/env python3
"""fleet_state.py — one authoritative JOIN of *who holds what* (#7750).

WHY THIS EXISTS
---------------
The fleet could not answer two questions without contradicting itself:

  * **WHO holds an issue or a PR?** Every lane authenticates to GitHub as the
    same account, so ``author:@me`` is not ownership. Ownership is only provable
    from **branch + worktree + session**, and no single tool joined those.
  * **WHAT is a lane doing?** ``lane-status.py`` derives liveness from session-file
    writes, which FREEZE during a long tool call — measured 2026-10-08, it
    reported ``IDLE`` at 0.98 confidence for three lanes whose panes were at
    CH99.7-99.9% mid-turn, and ``DEAD`` for lanes with live children. Acting on
    that bucket means ``cmux respawn-pane``, which KILLS the running turn.

This module does not invent a fifth store. It **joins artifacts that already
exist** and reports, for each of them, HOW the value was obtained:

    lane-registry.tsv          label <-> workspace
    cmux list-workspaces       workspace -> cwd / title
    cmux surface resume show   the pane's resume binding (authoritative)
    lane-sessions.json         registry OVERRIDES (disk-verified, hand-picked)
    cmux sessions list         the durable session store: running pi pid + cwd
    ~/.pi/agent/sessions/      session JSONL on disk (existence + identity)
    git worktree list          worktree -> branch -> HEAD
    gh pr / gh issue list      open work items
    tools/ci_verdict.py        the ONE per-commit CI verdict (never re-derived)
    ~/.pi/agent/reviews/       review records (head_sha / diff_sha256 binding)
    ps -axo pid,ppid,time      the WHOLE process tree (CPU-time delta)

THE SESSION RESOLUTION RULE — #7750's ROOT FIX
----------------------------------------------
A lane's session id is resolved by ONE of four named rules, in this priority:

    pane binding | registry override | disk | UNKNOWN

and NEVER by a guess. A guessed session file is how ``lane-status`` attributed
``6 Durability core`` (no resume binding) to ``4a``'s session ``01a0d5bb`` and
printed a confident wrong verdict. The four rules are:

  * ``pane binding``       ``cmux surface resume show`` names a session AND its
                           file exists on disk.
  * ``registry override``  ``lane-sessions.json`` carries an override whose
                           session file exists (a disk-verified hand-pick for a
                           lane whose pane lost its binding).
  * ``disk``               exactly ONE unclaimed session file in the lane's own
                           session directory whose first user message names the
                           lane. Ambiguity (0 or >1 candidate) is NOT a guess —
                           it is ``UNKNOWN``.
  * ``UNKNOWN``            nothing resolved. Reported first-class, the same as
                           ``UNBOUND`` is for cost — never silently substituted.

Two further invariants are enforced and reported, not papered over:

  * **one session, one lane** — a session resolvable to two lanes is a
    ``one-session-many-lanes`` violation; both lanes are marked ``shared_with``.
  * **report the rule** — ``session.source`` is one of the four names, so a
    guess can never masquerade as a measurement.

THE FREE TRAP — "genuinely idle" is subtle
------------------------------------------
An idle verdict must not be reachable while a lane works. The first attempt
called a lane free on ``0 CPU delta + no DIRECT child`` — the lane's work was in
a GRANDCHILD at CH99.8%. So ``free`` requires BOTH:

  * the CPU-time delta over the **WHOLE descendant tree** is ≤ tolerance, AND
  * the pane is **quiescent** — no spinner, and the ``CH`` reading did not move
    across the sample window.

Both are fail-closed: an unmeasured signal is ``not free``, never ``free``.

THE GHOST LANE — a lane whose workspace no longer exists (#7738)
----------------------------------------------------------------
A lane is recorded in ``lane-registry.tsv`` (label <-> workspace) and that row
outlives the cmux workspace it names. Nothing reconciled the record against
workspace EXISTENCE, so a closed or crashed workspace kept a verdict forever —
and because an unmeasured lane mapped to "live" (the anti-false-orphan default),
a ghost was reported as a live owner indefinitely.

Every lane is now reconciled against the LIVE ``cmux list-workspaces`` set. When
the list WAS read and the lane's workspace is absent, the lane is **terminal**:

  * ``RECOVERABLE`` — a session file still resolves on disk; the verdict carries
    the exact ``pi --session <sid>`` that restores it.
  * ``RETIRED``     — no session file resolves; never nudge, never dispatch,
    never route.

A terminal lane is reported and is NOT live, NOT free and NOT dispatchable.
"Reported" means never SILENTLY dropped, and it is a statement about ORPHANS: a
registry row that is not a lane record (#8845) leaves `rows`, but every dropped
row is published as an orphan of kind `registry-stale` saying which arm dropped
it — deleting the roster line is a human's call, not this module's. Three arms
drop and they are NOT one rule: a row whose (label, workspace) repeats one
already SEEN — kept, or already dropped by another arm — is a DUPLICATE,
de-duplicated even when the cmux list could not be read (both rows name one
workspace, so no verdict moves); a row whose workspace is absent from a MEASURED
list while another row for the label binds evidence is dropped as stale; and a
second UNBOUND row for a label is dropped so the label is not enumerated twice —
that one also requires a MEASURED list (it is a retirement, not a de-duplication).
Polarity is fail-OPEN with provenance: an unreadable
workspace list retires NOTHING, and ``reconciliation.workspaces_measured`` says
so — a cmux outage must never hide a real lane, and a fail-open path must not be
silent.

FRESHNESS — the cache carries its own age, and a failed read is never an answer (#7957)
--------------------------------------------------------------------------------------
This module's readers answer from a CACHE, and until now they answered from it at
ANY age: a state written in the morning answered a question asked at night, with no
age reported anywhere. That is the defect the capstone epic #7957 names at the
goal-state instrument (H1: freshness is self-reporting; H2: the verdict agrees with
reality; H5: failure is loud and honest) — the same one-line rule applies here:

  * every invocation reports ``state_age=…``, computed from the state's own
    ``generated_at`` stamp (never inferred by hand);
  * the cache is FRESH for ``STATE_FRESH_S`` (900 s — the fleet's incumbent
    ``OBJECTIVE_FRESH_S``); past it the reader **rebuilds before answering**, so the
    age served is bounded rather than merely labelled. ``--allow-stale`` reads the
    aged cache on purpose, and then it is rendered ``STALE(>900s)``;
  * an unreadable/absent stamp is **never** current — an unknown age rebuilds;
  * a FAILED ``gh`` read is not ``"no open PRs"``. ``open_prs``/``open_issues``
    carry the read's provenance (``measured`` / ``error``); ``build`` writes
    nothing when the board was unmeasured (a failed read is never cached), and
    ``who``/``free``/``conflicts``/``orphans`` REFUSE to answer from one instead of
    asserting a negative — an unread board must never free a lane into capacity.

USAGE
-----
  fleet_state.py build [--out PATH] [--sample S] [--json]
  fleet_state.py who <number> [--json]          # the lane holding it + evidence
  fleet_state.py lane <label|workspace> [--json]
  fleet_state.py free [--json]                  # genuinely idle lanes only
  fleet_state.py orphans [--json]               # work with no live owner
  fleet_state.py conflicts [--json]             # PRs asserted by >1 lane
  fleet_state.py violations [--json]            # session-partition breaks
  fleet_state.py bind [--all | -w WS -s SID] [--dry-run] [--json]

The reader commands read ``~/.pi/agent/state/fleet-state.json`` (written by
``build``); pass ``--refresh`` to rebuild first. A cache past ``STATE_FRESH_S`` is
rebuilt automatically (say ``--allow-stale`` to read it as-is and see it marked
``STALE(>900s)``); every run prints the age it answered from on stderr.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below (matching ci_verdict).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/fleet_state.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/fleet_state.py`"
    )

import argparse
import contextlib
import hashlib
import itertools
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

# --- the four session sources ----------------------------------------------

SOURCE_PANE = "pane binding"
SOURCE_OVERRIDE = "registry override"
SOURCE_DISK = "disk"
SOURCE_UNKNOWN = "UNKNOWN"
SOURCE_GUESS = "GUESS"  # reported, never used as a session id

# --- the free trap ----------------------------------------------------------

# A sub-0.1s cumulative twitch has the same signature from a parked child, a
# prompt redraw and a timer wake, so the tolerance is STATED rather than implied.
CPU_TOLERANCE_S = 0.10
DEFAULT_SAMPLE_S = 3.0

#: The #7743 stall bound. A lane whose pane is Working but which has NO descendant
#: process and has written NOTHING to its session for this long is not busy — it is
#: STALLED with an in-flight tool whose child is gone, so nothing can end the turn
#: and any message delivered into its queue can never be drained. 10 min is far
#: above any healthy model/tool latency and far below the measured wedge (35+ min);
#: the detection is REPORTING ONLY (it kills nothing), so a conservative value that
#: can occasionally flag a very slow model call is safe, while a value below the
#: latency would cry wolf on every long call.
STALLED_TURN_SECONDS = 600.0

#: How long a CACHED state may be served before a reader rebuilds instead (#7957 H1).
#: One number, matching the fleet's incumbent ``OBJECTIVE_FRESH_S = 900``
#: (``~/.pi/agent/scripts/turn-classify.py``), so every derived view the orchestrator
#: steers from ages out on the same boundary. Reported as ``state_age=…`` on every
#: run; the value is still PUBLISHED past the window (``--allow-stale``) — marked
#: stale, never presented as current.
STATE_FRESH_S = 900.0

#: A stamp this far AHEAD of now is not clock skew between the writer and this reader
#: — same box, same clock — it is a corrupt or future-dated cache, and its age is
#: UNKNOWN rather than zero (#7957 H1: "an unknown age is never current").
STATE_CLOCK_SKEW_S = 60.0

# --- paths ------------------------------------------------------------------

HOME = Path.home()
STATE_DIR = HOME / ".pi/agent/state"
SESSIONS_DIR = HOME / ".pi/agent/sessions"
REVIEWS_DIR = HOME / ".pi/agent/reviews"
REGISTRY = STATE_DIR / "lane-registry.tsv"
LANE_SESSIONS = STATE_DIR / "lane-sessions.json"
DEFAULT_OUT = STATE_DIR / "fleet-state.json"

_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z)_([0-9a-f-]{36})\.jsonl$")
_SLUG_RE = re.compile(r"(?<!\d)(\d{3,6})(?!\d)")
_HASH_RE = re.compile(r"#(\d{1,6})\b")
_SPINNER_RE = re.compile(r"[⠁⠂⠄⡀⢀⠠⠐⠈⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")


# ===========================================================================
# pure helpers
# ===========================================================================


def mangled(cwd: str) -> str:
    """cwd -> session directory name. ``/a/b.c`` -> ``--a-b-c--`` (see lane-status)."""
    return "--" + re.sub(r"[/.]", "-", cwd.strip("/")) + "--"


def refs_from_slug(slug: str) -> set[int]:
    """Issue/PR numbers named in a branch name or worktree path.

    ``fix/7735-tempdir`` -> {7735}; ``tmp/land6260-5339`` -> {6260, 5339};
    ``land1`` -> {} (a trailing single digit is a lane number, not a work item).
    """
    return {int(m) for m in _SLUG_RE.findall(slug or "")}


def refs_from_text(text: str) -> set[int]:
    """Explicit ``#NNNN`` references in a session's own first user message."""
    return {int(m) for m in _HASH_RE.findall(text or "")}


def cpu_seconds(tok: str) -> float | None:
    """``MM:SS.ss`` / ``HH:MM:SS`` / ``D-HH:MM:SS`` -> seconds (from lane-status)."""
    try:
        days = 0
        if "-" in tok:
            d, _, tok = tok.partition("-")
            days = int(d)
        secs = 0.0
        for b in tok.split(":"):
            secs = secs * 60 + float(b)
        return secs + days * 86400
    except Exception:
        return None


_FUM_CACHE: dict[str, str] = {}


def first_user_message(path: str | Path, max_bytes: int = 2_000_000, max_lines: int = 4000) -> str:
    """The first user turn's text — how this fleet identifies a lane.

    Memoized (the value is immutable) and BOUNDED: this is called over the shared
    session directory, which measured 1.6 GB / 240 files on 2026-10-08, and an
    unbounded read of every file once per lane cost 83s per lane. The first user
    message is an identity header, not deep in the transcript.
    """
    key = str(path)
    if key in _FUM_CACHE:
        return _FUM_CACHE[key]
    text = ""
    try:
        consumed = 0
        # ⛔ Iterate BYTES and count them: ``fh.tell()`` inside a text-mode
        # ``for line in fh`` raises ``OSError: telling position disabled by
        # next() call`` (verified on 3.12), which the broad except swallowed —
        # so this returned "" for EVERY file and the `disk` session source was
        # unreachable while still reporting the four-source contract.
        with open(path, "rb") as fh:
            for i, raw in enumerate(fh):
                consumed += len(raw)
                if i >= max_lines or consumed > max_bytes:
                    break
                try:
                    o = json.loads(raw.decode("utf-8", "ignore"))
                except Exception:
                    continue
                if not isinstance(o, dict):
                    continue
                if o.get("type") != "user" and o.get("role") != "user":
                    continue
                c = o.get("content")
                if c is None and isinstance(o.get("message"), dict):
                    c = o["message"].get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                if c:
                    text = str(c)
                    break
    except Exception:
        text = ""
    _FUM_CACHE[key] = text
    return text


def descendants(root: int, table: Mapping[int, int]) -> set[int]:
    """Every pid reachable from ``root`` via ``table`` = {pid: ppid}.

    THE TRAP ITSELF: work is routinely in a GRANDCHILD (pi -> bash -> pi -p), so a
    "direct child only" walk reads a busy lane as idle. This walks the whole tree.
    """
    children: dict[int, list[int]] = {}
    for pid, ppid in table.items():
        children.setdefault(ppid, []).append(pid)
    out: set[int] = set()
    stack = list(children.get(root, []))
    while stack:
        p = stack.pop()
        if p in out:
            continue
        out.add(p)
        stack.extend(children.get(p, []))
    return out


def tree_cpu(cpu: Mapping[int, float], root: int, table: Mapping[int, int]) -> float:
    """Cumulative CPU seconds over the root and its WHOLE descendant tree."""
    total = cpu.get(root, 0.0)
    for p in descendants(root, table):
        total += cpu.get(p, 0.0)
    return total


# ===========================================================================
# session resolution — #7750's root fix
# ===========================================================================


@dataclass(frozen=True)
class SessionResolution:
    """The outcome of the four-rule session resolution, with its provenance."""

    sid: str | None
    source: str
    file: str | None
    evidence: str
    guess_refused: str | None = None
    shared_with: tuple[str, ...] = ()

    @property
    def bound(self) -> bool:
        return bool(self.sid and self.source != SOURCE_UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"bound": self.bound}


def resolve_sessions(
    rows: Sequence[tuple[str, str]],
    pane_sid_by_ws: Mapping[str, str],
    override_sid_by_label: Mapping[str, str],
    disk_by_label: Mapping[str, Sequence[str]],
    file_exists: Callable[[str], Path | None],
) -> tuple[dict[str, SessionResolution], list[dict[str, Any]]]:
    """Resolve every lane's session by the four named rules, then check the partition.

    Returns ``({label: SessionResolution}, violations)``. ``file_exists(sid)``
    returns the on-disk path or ``None`` — a binding/override whose file is
    MISSING is reported as ``UNKNOWN`` (dangling), never used (#7738).
    """
    res: dict[str, SessionResolution] = {}
    claimed_by: dict[str, list[str]] = {}
    dangling: dict[str, str] = {}

    def claim(label: str, sid: str, src: str, why: str) -> None:
        path = file_exists(sid)
        res[label] = SessionResolution(
            sid, src, str(path), why, guess_refused=dangling.get(label)
        )
        claimed_by.setdefault(sid, []).append(label)

    # 1) PANE BINDING — authoritative. A DANGLING binding (file gone) is recorded
    # but does NOT block the fallthrough: a missing file is "no binding that can be
    # used", not a reason to stay blind (#7738).
    for label, ws in rows:
        sid = (pane_sid_by_ws.get(ws) or "").strip()
        if sid and file_exists(sid):
            claim(label, sid, SOURCE_PANE, "cmux surface resume show (resume_binding)")
        elif sid:
            dangling[label] = (
                f"pane binding {sid[:8]}… has NO file on disk (dangling) — refused"
            )

    # 2) REGISTRY OVERRIDE — a disk-verified hand-pick for a lane that lost its binding.
    for label, _ws in rows:
        if label in res:
            continue
        sid = (override_sid_by_label.get(label) or "").strip()
        if sid and file_exists(sid):
            claim(label, sid, SOURCE_OVERRIDE, "lane-sessions.json override (disk-verified)")
        elif sid and label not in dangling:
            dangling[label] = (
                f"registry override {sid[:8]}… has NO file on disk (dangling) — refused"
            )

    # 3) DISK — live-process record first, then an UNAMBIGUOUS identity match.
    # Never a guess: an ambiguous candidate set is ``UNKNOWN``.
    for label, _ws in rows:
        if label in res:
            continue
        cands = [c for c in disk_by_label.get(label, []) if file_exists(c)]
        unclaimed = [c for c in cands if c not in claimed_by]
        if len(unclaimed) == 1:
            claim(label, unclaimed[0], SOURCE_DISK,
                  "unique disk-verified session (live process record / identity match)")
        elif len(unclaimed) > 1:
            res[label] = SessionResolution(
                None, SOURCE_UNKNOWN, None,
                f"{len(unclaimed)} candidate session files match this lane — ambiguous, refusing to guess",
                guess_refused=f"{len(unclaimed)} disk candidates",
            )

    # 4) DEFAULT — UNKNOWN is a first-class answer, carrying the refused rule if any.
    for label, _ws in rows:
        if label in res:
            continue
        note = dangling.get(label)
        if note:
            res[label] = SessionResolution(None, SOURCE_UNKNOWN, None, note, guess_refused=note)
        else:
            why = "no pane binding, no override, no disk match — UNBOUND"
            if disk_by_label.get(label):
                why = "only disk candidate(s) already belong to another lane — UNBOUND"
            res[label] = SessionResolution(None, SOURCE_UNKNOWN, None, why)

    # 5) PARTITION — one session, one lane. A shared session is REPORTED.
    violations: list[dict[str, Any]] = []
    for sid, labels in claimed_by.items():
        if len(labels) > 1:
            violations.append({
                "kind": "one-session-many-lanes",
                "session": sid,
                "lanes": sorted(labels),
                "error": (
                    "the same session resolves to " + ", ".join(sorted(labels))
                    + " — a session belongs to at most one lane"
                ),
            })
            for lbl in labels:
                res[lbl] = replace(
                    res[lbl], shared_with=tuple(x for x in labels if x != lbl)
                )
    return res, violations


# ===========================================================================
# claim attribution — branch / worktree / session text, never comment prose
# ===========================================================================


@dataclass(frozen=True)
class Claims:
    prs: tuple[int, ...] = ()
    issues: tuple[int, ...] = ()
    evidence: Mapping[str, str] = field(default_factory=dict)


def attribute_claims(
    *,
    lane_branches: Sequence[str],
    lane_cwd: str,
    session_text: str,
    open_prs: Sequence[Mapping[str, Any]],
    open_issue_numbers: Iterable[int],
) -> Claims:
    """Attribute open PRs/issues to a lane from MECHANICAL ownership signals.

    Priority, strongest first: the PR's head branch is checked out in one of the
    lane's worktrees; the lane's worktree path / branch names the issue; the
    lane's own first user message names it. Comment prose is never consulted —
    every lane authenticates as the same GitHub account, so a claim-shaped
    comment cannot distinguish owners.
    """
    branches = {b.split("refs/heads/")[-1] for b in lane_branches if b}
    slug_refs = set()
    for b in branches:
        slug_refs |= refs_from_slug(b)
    slug_refs |= refs_from_slug(os.path.basename(lane_cwd.rstrip("/")))
    text_refs = refs_from_text(session_text)
    issues_all = set(open_issue_numbers)

    prs: list[int] = []
    ev: dict[str, str] = {}
    for pr in open_prs:
        head = (pr.get("headRefName") or "")
        num = pr.get("number")
        if head and head in branches:
            prs.append(num)
            ev[f"PR {num}"] = f"branch `{head}` checked out in lane worktree {lane_cwd}"
        elif num in text_refs:
            prs.append(num)
            ev[f"PR {num}"] = "named in the lane's own session (first user message)"

    issues: list[int] = []
    for n in sorted(slug_refs):
        if n in issues_all and n not in prs:
            issues.append(n)
            ev[f"issue {n}"] = "named in the lane's branch/worktree slug"
    for n in sorted(text_refs):
        if n in issues_all and n not in prs and n not in issues:
            issues.append(n)
            ev[f"issue {n}"] = "named in the lane's own session (first user message)"
    return Claims(tuple(sorted(set(prs))), tuple(sorted(set(issues))), ev)


# ===========================================================================
# liveness + the free predicate
# ===========================================================================


@dataclass
class Liveness:
    pi_pids: tuple[int, ...] = ()
    pid_alive: bool = False
    cpu_delta_seconds: float | None = None
    cpu_sample_seconds: float = 0.0
    descendant_count: int = 0
    pane_working: bool = False
    pane_ch_pct: float | None = None
    pane_ch_moving: bool | None = None
    transcript_age_seconds: float | None = None
    # False when the durable session store could not be read at all: ``pid_alive``
    # is then UNMEASURED, not False, and must not be reported as a dead owner.
    liveness_measured: bool = False

    @property
    def pane_quiescent(self) -> bool | None:
        if self.pane_working:
            return False
        if self.pane_ch_moving is None:
            return None  # unmeasured — never silently "quiescent"
        return not self.pane_ch_moving

    @property
    def pane_stalled_turn(self) -> bool:
        """The #7743 wedge: a LIVE, Working pane with no backing child process and
        a transcript that has not moved for ``STALLED_TURN_SECONDS``.

        This is the orphaned in-flight `task`/`subagent` tool. The child is gone, so
        `descendant_count == 0` while the spinner keeps animating; nothing will ever
        resolve the tool or end the turn, so the lane is not busy — it is stalled.
        Any ONE signal is ambiguous (a slow model call animates the spinner with no
        child; a long test run has a child and a frozen transcript); the CONJUNCTION
        is the wedge. Fail-closed: an unmeasured transcript age or an UNMEASURED
        descendant tree is never a stall — ``cpu_delta_seconds`` is non-None only
        when the process table was read, so an unreadable ``ps`` cannot be mistaken
        for "the child is gone".

        The conjunction is NECESSARY, not sufficient: a childless model call that
        outlives the bound satisfies it too. That residual is accepted because the
        detector is REPORTING ONLY — it kills nothing, so a false flag costs a look,
        not a lane — and the bound sits far above normal model latency. Recovery is
        a session resume (``cmux respawn-pane … pi --session <sid>``), which the
        lane record already carries as ``session.sid``.
        """
        return (
            self.pid_alive
            and self.pane_working
            and self.cpu_delta_seconds is not None  # the tree was MEASURED
            and self.descendant_count == 0
            and self.transcript_age_seconds is not None
            and self.transcript_age_seconds >= STALLED_TURN_SECONDS
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "pane_quiescent": self.pane_quiescent,
            "pane_stalled_turn": self.pane_stalled_turn,
        }


def free_reasons(liveness: Liveness, open_pr_count: int, held_issue_count: int) -> list[str]:
    """Every reason this lane is NOT genuinely free. Empty list => free."""
    reasons: list[str] = []
    if open_pr_count or held_issue_count:
        reasons.append(
            f"holds {open_pr_count} PR(s) and {held_issue_count} issue(s)"
        )
    if liveness.cpu_delta_seconds is None:
        reasons.append("CPU delta unmeasured (whole descendant tree)")
    elif liveness.cpu_delta_seconds > CPU_TOLERANCE_S:
        reasons.append(
            f"CPU delta {liveness.cpu_delta_seconds:.2f}s over {liveness.cpu_sample_seconds:.0f}s "
            f"across {liveness.descendant_count} descendant(s) > {CPU_TOLERANCE_S}s"
        )
    pq = liveness.pane_quiescent
    if pq is None:
        reasons.append("pane quiescence unmeasured")
    elif not pq:
        reasons.append("pane not quiescent (spinner or moving CH line)")
    if liveness.pane_stalled_turn:
        reasons.append(
            f"STALLED TURN: pane Working with no descendant process and no session "
            f"write for {liveness.transcript_age_seconds:.0f}s — the in-flight tool's "
            f"child is gone (#7743); RECOVERABLE by resuming the session"
        )
    return reasons


def is_genuinely_free(liveness: Liveness, open_pr_count: int, held_issue_count: int) -> bool:
    return not free_reasons(liveness, open_pr_count, held_issue_count)


def workspace_terminal(
    ws: str,
    live: Mapping[str, Any],
    measured: bool,
    session: SessionResolution,
) -> dict[str, Any]:
    """The terminal verdict for a lane whose cmux workspace no longer exists (#7738).

    ``measured`` is the live list's provenance. When it is False the verdict is
    NOT evaluated (``state == ""``, ``workspace_present is None``) — fail-open, so
    a cmux outage can never retire a real lane. When the list WAS read and the
    workspace is absent, the lane is terminal: ``RECOVERABLE`` if a session file
    resolves on disk (carrying the exact restore command), else ``RETIRED``.

    A deliberate close and a crash-vanish are the same observation here — the
    workspace is not in the live list — so both are caught.
    """
    if not measured:
        return {"state": "", "workspace_present": None, "reason": "", "resume_command": ""}
    present = any(str(k).upper() == ws.upper() for k in live)
    if present:
        return {"state": "", "workspace_present": True, "reason": "", "resume_command": ""}
    short = ws[:8]
    if session.file and session.sid:
        return {
            "state": "RECOVERABLE",
            "workspace_present": False,
            "reason": (
                f"cmux workspace {short} no longer exists, but its session "
                f"{session.sid[:8]}… survives on disk — recover with "
                f"`pi --session {session.sid}`; terminal until then"
            ),
            "resume_command": f"pi --session {session.sid}",
        }
    return {
        "state": "RETIRED",
        "workspace_present": False,
        "reason": (
            f"cmux workspace {short} no longer exists and no session file "
            f"resolves — RETIRED; never nudge, never dispatch, never route"
        ),
        "resume_command": "",
    }


# ===========================================================================
# review binding (AT-HEAD | diff-match | none)
# ===========================================================================


def normalize_review_diff(raw: bytes) -> bytes:
    """The SIGNED cross-repo diff normalizer (agent-infra scripts/lib/diff-normalize.py).

    Reimplemented here only as a FAIL-CLOSED fallback when the shared normalizer
    file is unavailable: an empty return means the caller must not claim a
    ``diff-match``.
    """
    idx = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+( [0-7]{6})?$")
    hunk = re.compile(r"^@@ -([0-9]+)(,([0-9]+))? \+([0-9]+)(,([0-9]+))? @@(.*)$")
    out: list[str] = []
    entry: list[str] = []
    has_hunk = False

    def flush(lines: list[str], has_hunk: bool) -> None:
        for line in lines:
            if has_hunk and idx.match(line):
                continue
            m = hunk.match(line)
            if m:
                old = m.group(3) if m.group(3) is not None else "1"
                new = m.group(6) if m.group(6) is not None else "1"
                line = "@@ -0," + old + " +0," + new + " @@" + m.group(7)
            out.append(line)

    for line in raw.decode("latin-1").split("\n"):
        if line.startswith("diff --git "):
            flush(entry, has_hunk)
            entry = []
            has_hunk = False
        entry.append(line)
        if line.startswith("@@ "):
            has_hunk = True
    flush(entry, has_hunk)
    return "\n".join(out).encode("latin-1")


def review_binding(
    pr_number: int,
    head_sha: str,
    *,
    record_path: Path,
    live_diff_sha256: str | None,
) -> str:
    """``AT-HEAD`` | ``diff-match`` | ``stale`` | ``none``.

    ``AT-HEAD`` is the binding the merge gate reads: a review record whose
    recorded ``head_sha`` IS the PR's current head. ``diff-match`` is a record
    that has moved off the head but whose reviewed diff is byte-identical (a
    base refresh). Everything else is ``stale``.
    """
    try:
        rec = json.loads(record_path.read_text())
    except Exception:
        return "none"
    if not isinstance(rec, dict):
        return "none"
    if rec.get("head_sha") == head_sha:
        return "AT-HEAD"
    reviewed = rec.get("diff_sha256")
    if reviewed and live_diff_sha256 and reviewed == live_diff_sha256:
        return "diff-match"
    return "stale"


# ===========================================================================
# orphan report
# ===========================================================================


def orphan_report(
    open_prs: Sequence[Mapping[str, Any]],
    owner_of_pr: Mapping[int, str | None],
    lane_live: Mapping[str, bool],
    over_claimed: Collection[int] = (),
    lane_terminal: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Open PRs with no live owner — the class that left #7746 unnoticed.

    ``over_claimed`` holds PRs asserted by more than one lane. Those are an
    ownership CONFLICT (reported separately) — NOT an orphan. They must be
    skipped here: a conflicted PR has had its owner cleared before this call,
    so without this guard it would resurface below as "no lane claims it",
    which is the exact opposite of the truth.
    """
    over_claimed = set(over_claimed)
    out: list[dict[str, Any]] = []
    for pr in open_prs:
        num = pr.get("number")
        if num in over_claimed:
            continue
        owner = owner_of_pr.get(num)
        if owner is None:
            out.append({
                "kind": "PR",
                "number": num,
                "title": pr.get("title"),
                "branch": pr.get("headRefName"),
                "reason": "no lane claims it (no worktree/branch, no session names it)",
            })
        elif not lane_live.get(owner, False):
            # Say what is actually TRUE. `lane_live` collapses "terminal" and
            # "measured dead" into one False, but they are different facts: a
            # terminal lane's cmux workspace is GONE, which says nothing about
            # whether a pi process outlived it — a terminal lane CAN still carry
            # `pid_alive: true`. Asserting "has no live pi process" there is an
            # unmeasured claim of exactly the #7750 class this module exists to
            # stamp out, so the terminal case names the workspace instead.
            term = (lane_terminal or {}).get(owner)
            reason = (
                f"owner lane `{owner}` is terminal ({term}): its cmux workspace no longer exists"
                if term
                else f"owner lane `{owner}` has no live pi process"
            )
            out.append({
                "kind": "PR",
                "number": num,
                "title": pr.get("title"),
                "branch": pr.get("headRefName"),
                "lane": owner,
                "reason": reason,
            })
    return out


def resolve_conflicts(
    lanes: list[dict[str, Any]],
    prs: Sequence[Mapping[str, Any]],
    owner_of_pr: dict[int, str | None],
    lane_live: Mapping[str, bool],
) -> tuple[list[int], dict[int, list[str]], list[dict[str, Any]]]:
    """Resolve contested PR claims, then report the PRs left with no live owner.

    A PR claimed by MORE THAN ONE lane is an ownership CONFLICT, not an
    assignment: a reused workspace can inherit a previous lane's worktree. A
    conflict is claimed for nobody and is reported by the conflict channel —
    never as an orphan (``orphan_report`` is told ``over_claimed``) — and its
    claimants are returned so ``who`` can NAME them instead of printing
    "no lane holds it", which for the one PR two lanes claim is the opposite of
    the truth.

    This is the single place the conflict pass and the orphan pass are wired
    together, so the ordering (conflicts computed BEFORE owners are cleared and
    ``orphan_report`` is called) is pinned by a pure test.

    Returns ``(conflicts, claimant_of_pr, orphans)``.
    """
    pr_claims: dict[int, list[str]] = {}
    for lane_ in lanes:
        for n in lane_["claim"]["prs"]:
            pr_claims.setdefault(n, []).append(lane_["identity"]["lane"])
    conflicts = sorted(n for n, who in pr_claims.items() if len(who) > 1)
    claimant_of: dict[int, list[str]] = {n: sorted(pr_claims[n]) for n in conflicts}
    if conflicts:
        for lane_ in lanes:
            lane_["claim"]["prs"] = [n for n in lane_["claim"]["prs"] if n not in conflicts]
        for n in conflicts:
            owner_of_pr.pop(n, None)
    orphans = orphan_report(
        prs, owner_of_pr, lane_live, over_claimed=conflicts, lane_terminal=_terminal_map(lanes)
    )
    return conflicts, claimant_of, orphans


def _terminal_map(lanes: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """lane label -> terminal state, for the lanes the reconciliation retired.

    Derived here rather than passed in so the orphan reason stays truthful for
    EVERY caller, not only the one that remembers to thread the extra argument.
    """
    out: dict[str, str] = {}
    for lane_ in lanes:
        try:
            state = (lane_.get("terminal") or {}).get("state")
            if state:
                out[lane_["identity"]["lane"]] = str(state)
        except Exception:
            continue
    return out


# ===========================================================================
# live collectors (thin, cached, degrade to empty — never crash)
# ===========================================================================


def _run(cmd: Sequence[str], timeout: int = 60) -> str:
    """Run ``cmd``, returning stdout — or the EMPTY STRING if it did not SUCCEED.

    #7883: a non-zero exit degrades to empty, like every other failure in this
    section ("thin, cached, degrade to empty — never crash"). Without it, a
    FAILED-but-chatty command read as a successful answer, which for
    ``cmux_workspaces`` means a partial or stale list treated as the whole fleet.
    """
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except Exception:
        return ""
    return p.stdout if p.returncode == 0 else ""


class RegistryRows(list):
    """``load_registry`` rows carrying the READ'S provenance (#7738/#7957 H5).

    A plain ``[]`` cannot distinguish "the registry lists no lanes" from "the
    registry could not be read". The first is a real (if unusual) roster; the
    second means the lane population is UNKNOWN, and every consumer that OFFERS a
    lane or asserts that a lane holds nothing would otherwise answer from a read
    that never happened. The same provenance the workspace list carries
    (``WorkspaceMap``, #7738) and the board carries (``github_measured``, #7957)
    travels here, so the caller can REFUSE rather than assert an empty fleet.
    """

    measured: bool
    error: str

    def __init__(self, *args: Any, measured: bool = False, error: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.measured = measured
        self.error = error


def load_registry(path: Path = REGISTRY) -> RegistryRows:
    """``label<TAB>workspace<TAB>issue-label`` rows (comments ignored).

    The read's PROVENANCE travels with the rows. An ``OSError`` is reported as
    ``measured=False`` with the cause — never swallowed into an empty list that
    reads as "no lanes" (the fail-open class #7738 removed from
    ``cmux_workspaces``). A readable file with no rows is ``measured=True``: an
    empty registry is an answer, a failed read is not.
    """
    try:
        text = path.read_text(errors="ignore")
    except OSError as exc:
        return RegistryRows(error=f"{type(exc).__name__}: {exc}")
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 2 and parts[1]:
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return RegistryRows(rows, measured=True)


class WorkspaceMap(dict):
    """The live cmux workspace map, carrying the READ'S provenance (#7738).

    A plain ``{}`` cannot distinguish "cmux says there are no workspaces" from
    "cmux could not be read" — and the reconciliation must fail OPEN on the
    latter (an unreadable list retires NOTHING). ``measured`` records which of the
    two happened, so the caller can SAY it failed open instead of silently
    filtering nothing. ``error`` names the failure when ``measured`` is False.

    ``measured`` means the read was COMPLETE — the command SUCCEEDED and the
    answer covers the whole fleet — not merely that the payload parsed. See
    ``cmux_workspaces`` for why a single-window answer is not the fleet.
    """

    measured: bool
    error: str

    def __init__(self, *args: Any, measured: bool = False, error: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.measured = measured
        self.error = error


def open_window_count() -> int | None:
    """How many cmux windows are open, or ``None`` when the answer is unreadable.

    WHY THIS IS NOT A LINE COUNT. ``cmux list-windows`` prints a TABLE: an ``ID …``
    header row first, then one row per window. Counting non-blank LINES therefore
    reports ``n+1``, so a healthy single-window box reads as ``saw 2`` and the
    workspace reconciliation is INERT — it fails open, every lane goes unmeasured, and
    the collision pre-flight's claim gate answers INCOMPLETE for every candidate, so
    the backlog never refills and the cell dispatches nothing. Measured 2026-10-18 on
    a live cell: ``lanes=0`` behind a 198-candidate pool, with
    ``fleet-state: workspace reconciliation is INERT (fail-open): could not confirm a
    single cmux window (saw 2)``.

    ``--json`` is preferred: it is a documented array and its LENGTH is the count, with
    no header to strip. The text form is kept as a fallback and skips the header row
    explicitly (by matching the literal ``ID`` column heading, not by "skip line 1",
    which would also swallow a real window if the format ever loses the header).
    """
    out = _run(["cmux", "list-windows", "--json"], timeout=30)
    if out.strip():
        try:
            data = json.loads(out)
        except Exception:
            data = None
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict) and isinstance(data.get("windows"), list):
            return len(data["windows"])
    text = _run(["cmux", "list-windows"], timeout=30)
    if not text.strip():
        return None
    rows = [ln for ln in text.splitlines() if ln.strip()]
    if rows and rows[0].lstrip().startswith("ID"):
        rows = rows[1:]
    return len(rows)


def cmux_workspaces() -> dict[str, dict[str, Any]]:
    out = _run(["cmux", "list-workspaces", "--json"], timeout=45)
    # SCOPE — this command lists ONE window. Its own help says "List workspaces in
    # a window", defaulting to the caller's. With more than one window open, every
    # lane in the others is simply ABSENT — and absence is exactly what makes a
    # lane terminal — so a multi-window fleet is UNMEASURABLE here. Fail open and
    # say why, rather than retiring lanes that are alive in another window. The
    # fleet is single-window today (the canonical publisher makes the same
    # assumption); this guard is what stops that assumption being load-bearing.
    n_windows = open_window_count()
    if n_windows is None:
        return WorkspaceMap(
            error=(
                "could not count the open cmux windows (unreadable answer); the workspace "
                "list is per-window, so absence is not evidence"
            )
        )
    if n_windows != 1:
        return WorkspaceMap(
            error=(
                f"could not confirm a single cmux window (saw {n_windows}); the "
                "workspace list is per-window, so absence is not evidence"
            )
        )
    try:
        data = json.loads(out)
    except Exception:
        return WorkspaceMap(error="cmux list-workspaces returned no parseable JSON")
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("workspaces") or []
    else:
        # valid JSON that is not an object/list (null, string) -> unreadable
        return WorkspaceMap(error="cmux list-workspaces returned JSON that is not an object or list")
    result: dict[str, dict[str, Any]] = {}
    for x in items:
        if not isinstance(x, dict):
            continue
        wsid = str(x.get("id") or "").strip()
        if wsid:
            result[wsid.upper()] = {
                "title": (x.get("custom_title") or "").strip(),
                "cwd": (x.get("current_directory") or "").strip(),
            }
    if not result:
        # An EMPTY answer is unreadable, not "no workspaces": the fleet always has
        # lanes, and retiring every lane on an empty set is the failure to avoid.
        return WorkspaceMap(error="cmux list-workspaces returned an empty set")
    return WorkspaceMap(result, measured=True)


def pane_bindings() -> dict[str, str]:
    """{workspace: session-id} from ``cmux surface resume show --json``."""
    out = _run(["cmux", "list-workspaces", "--json"], timeout=45)
    try:
        data = json.loads(out)
    except Exception:
        return {}
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("workspaces") or []
    else:
        return {}
    bindings: dict[str, str] = {}
    for x in items:
        if not isinstance(x, dict):
            continue
        wsid = str(x.get("id") or "").strip()
        if not wsid:
            continue
        raw = _run(
            ["cmux", "surface", "resume", "show", "--workspace", wsid, "--json"],
            timeout=30,
        )
        try:
            data = json.loads(raw)
        except Exception:
            # Text fallback: only the resume command form counts; "No resume binding" does not.
            m = re.search(r"--session'?\s*'?([0-9a-f-]{36})", raw)
            if m:
                bindings[wsid.upper()] = m.group(1)
            continue
        if not isinstance(data, dict):
            continue  # valid non-object JSON -> no usable binding
        # ⛔ THE AUTHORITATIVE FIELD IS ``resume_binding``, NOT ``restore_record``.
        # Measured 2026-10-08: workspace 01888E6B printed ``No resume binding`` from the
        # text form while ``restore_record.checkpoint_id`` still carried a STALE
        # ``01a10471…``. Reading restore_record is exactly the class of defect this
        # tool exists to remove, so the binding is read ONLY from ``resume_binding``
        # (``None`` == no binding).
        rb = data.get("resume_binding")
        if isinstance(rb, dict) and rb.get("checkpoint_id"):
            bindings[wsid.upper()] = rb["checkpoint_id"]
    return bindings


def live_sessions() -> dict[str, list[dict[str, Any]]]:
    """{workspace: [session records]} from the durable ``cmux sessions list``.

    ``--all`` is REQUIRED: the default limit is 100 and this store held 930 records
    (measured 2026-10-08), so a running lane whose record fell outside the first 100
    silently lost its pid and read ``unknown``.
    """
    out = _run(["cmux", "sessions", "list", "--json", "--all"], timeout=60)
    try:
        data = json.loads(out)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for s in data.get("sessions") or []:
        if not isinstance(s, dict) or s.get("agent") != "pi":
            continue
        ws = str(s.get("workspace_id") or "").upper()
        if ws:
            result.setdefault(ws, []).append(s)
    return result


def load_overrides(path: Path = LANE_SESSIONS) -> dict[str, str]:
    """label -> sid from ``lane-sessions.json`` entries whose source is an override."""
    out: dict[str, str] = {}
    try:
        for rec in json.loads(path.read_text()):
            if rec.get("source") in ("override", "manual") and rec.get("session"):
                out[rec["label"]] = rec["session"]
    except Exception:
        pass
    return out


def session_index() -> tuple[dict[str, str], dict[str, list[str]]]:
    """(sid -> path, mangled_dir -> [paths]) from session JSONL filenames."""
    by_sid: dict[str, str] = {}
    by_dir: dict[str, list[str]] = {}
    if not SESSIONS_DIR.is_dir():
        return by_sid, by_dir
    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            m = _TS_RE.search(f.name)
            if not m:
                continue
            by_sid[m.group(2)] = str(f)
            by_dir.setdefault(d.name, []).append(str(f))
    return by_sid, by_dir


def worktrees(repo_root: str) -> dict[str, dict[str, str]]:
    """{real path: {branch, head}} from ``git worktree list --porcelain``."""
    out = _run(["git", "-C", repo_root, "worktree", "list", "--porcelain"], timeout=45)
    result: dict[str, dict[str, str]] = {}
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
            result.setdefault(path, {})
        elif line.startswith("HEAD ") and path:
            result[path]["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch ") and path:
            result[path]["branch"] = line[len("branch "):].strip()
        elif line.startswith("detached") and path:
            result[path]["branch"] = "(detached)"
    return result


class GitHubRows(list):
    """The ``gh list`` answer carrying the READ'S provenance (#7957 H5).

    A plain ``[]`` cannot distinguish "GitHub says there are no open PRs" from "gh
    could not be read" — and the difference is the whole answer: with an unread
    board a lane that holds two PRs reads as holding none, so it is offered as free
    capacity (#7158's class), and ``who`` answers "nobody holds it" from a read that
    never happened. ``measured`` records which of the two happened; ``error`` names
    the cause when it is False. A PLAIN sequence (a hand-built fixture, an older
    caller) declares no provenance and is treated as UNMEASURED — provenance must be
    stated, never assumed, because the fail-open direction here invents capacity.
    """

    measured: bool
    error: str

    def __init__(self, rows: Iterable[Any] = (), *, measured: bool = False, error: str = "") -> None:
        super().__init__(rows)
        self.measured = measured
        self.error = error


def gh_json(args: Sequence[str], timeout: int = 60, *, what: str = "gh") -> GitHubRows:
    """Run ``gh … --json`` and say whether the READ was MEASURED (#7957).

    "Could not read it" and "the answer is empty" are different facts, and only the
    first is a reason to refuse to answer. ``measured`` is True only when the command
    SUCCEEDED (``_run`` degrades a non-zero exit to "") and the payload parsed as a
    JSON list — a chatty failure, a rate-limit notice, a ``null`` and a truncated
    body all come back UNMEASURED with the cause attached.
    """
    out = _run(["gh", *args], timeout=timeout)
    try:
        data = json.loads(out)
    except Exception:
        return GitHubRows(measured=False, error=f"{what} returned no parsable JSON")
    if not isinstance(data, list):
        return GitHubRows(
            measured=False,
            error=f"{what} returned {type(data).__name__}, not a list",
        )
    return GitHubRows(data, measured=True)


def open_prs(repo: str, limit: int = 400) -> GitHubRows:
    return gh_json([
        "pr", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
        "--json", "number,title,headRefName,headRefOid,mergeable,mergeStateStatus,url,isDraft,updatedAt",
    ], what="gh pr list")


#: The open-issue window ``build_state`` reads. ``attribute_claims`` gates EVERY
#: attribution on membership in this set, so a truncated read makes "no lane is
#: attributed" UNPROVABLE for the issues it did not see — the claim gate would
#: then answer "nobody" from a read that cannot claim coverage (#8175). The
#: window is far above any repo the fleet reads; if a repo ever exceeds it the
#: board SAYS SO (``reconciliation["open_issues_truncated"]``) and the pre-flight
#: refuses instead of answering.
OPEN_ISSUES_LIMIT = 2000


def open_issues(repo: str, limit: int = OPEN_ISSUES_LIMIT) -> GitHubRows:
    return gh_json([
        "issue", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
        "--json", "number,title,assignees,labels,updatedAt,url",
    ], what="gh issue list")


def process_table() -> tuple[dict[int, float], dict[int, int]]:
    """({pid: cumulative cpu seconds}, {pid: ppid}) for the WHOLE process table."""
    out = _run(["ps", "-axo", "pid=,ppid=,time="], timeout=30)
    cpu: dict[int, float] = {}
    parents: dict[int, int] = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) < 3:
            continue
        try:
            pid, ppid = int(f[0]), int(f[1])
        except ValueError:
            continue
        s = cpu_seconds(f[2])
        if s is not None:
            cpu[pid] = s
        parents[pid] = ppid
    return cpu, parents


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pane_screen(ws: str) -> str:
    return _run(["cmux", "read-screen", "--workspace", ws, "--lines", "24"], timeout=30)


def pane_state(ws: str) -> dict[str, Any]:
    """One pane read -> {working, ch_pct}. ``ch_pct`` is the footer's CH reading."""
    text = pane_screen(ws)
    ch = None
    for m in re.finditer(r"CH\s*([0-9]+(?:\.[0-9]+)?)%", text):
        ch = float(m.group(1))
    last = "\n".join([x for x in text.splitlines() if x.strip()][-2:])
    working = ("Working" in text) or bool(_SPINNER_RE.search(last))
    return {"working": working, "ch_pct": ch}


def transcript_age(path: str | None) -> float | None:
    if not path:
        return None
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


# ===========================================================================
# the join
# ===========================================================================


def build_state(
    *,
    repo: str,
    repo_root: str,
    orch_ws: str,
    sample_s: float = DEFAULT_SAMPLE_S,
    do_pane: bool = True,
    ci_verdict: Callable[[str, str], dict[str, Any]] | None = None,
    live_diff: Callable[[int], str | None] | None = None,
) -> dict[str, Any]:
    """Build the joined fleet state. Every live source degrades to empty."""
    registry = load_registry()
    # THE ROSTER'S OWN PROVENANCE. A registry read that failed enumerates ZERO
    # lanes, and every consumer below would then answer "no lane holds it" and "no
    # free capacity" from a read that never happened. Carry the fact (the same
    # shape as the workspace list and the board) so a reader can refuse. The
    # tolerant default keeps a legacy or monkeypatched plain list MEASURED.
    registry_measured = bool(getattr(registry, "measured", True))
    registry_error = str(getattr(registry, "error", "") or "")
    workspaces = cmux_workspaces()
    # #7738: workspace-existence reconciliation is only sound when the live list
    # was actually READ. An unreadable list (e.g. the beat's launchd context cannot
    # reach cmux) must retire NOTHING — fail open, and carry the evidence of why.
    workspaces_measured = bool(getattr(workspaces, "measured", False))
    workspaces_error = str(getattr(workspaces, "error", ""))
    bindings = pane_bindings()
    live = live_sessions()
    overrides = load_overrides()
    by_sid, by_dir = session_index()
    wts = worktrees(repo_root)
    # #7957: the board's PROVENANCE travels with the rows. An unread board is not an
    # empty board — `measured` is False and the cause is named, so a reader can refuse
    # to assert rather than assert a negative, and `build` can refuse to cache it.
    prs_read = open_prs(repo)
    issues_read = open_issues(repo)
    prs = list(prs_read)
    issues = list(issues_read)
    # #8175: the read is the FILTER `attribute_claims` applies, so hitting the cap
    # means the board cannot see every open issue. Exactly-at-limit is treated as
    # truncated (the two are indistinguishable), and the flag travels with the
    # board so a reader can refuse rather than assert a negative.
    issues_truncated = len(issues_read) >= OPEN_ISSUES_LIMIT
    github_measured = bool(getattr(prs_read, "measured", False)) and bool(
        getattr(issues_read, "measured", False)
    )
    github_error = str(
        getattr(prs_read, "error", "") or getattr(issues_read, "error", "") or ""
    )
    # An empty session store means the store could not be read (the fleet always has
    # live pi sessions); with no measured pids, ``pid_alive`` is UNMEASURED.
    liveness_measured = bool(live)
    # A worktree used by exactly ONE workspace's records clearly belongs to that lane;
    # a worktree shared by several workspaces (a reused checkout) must not be used to
    # cross-attribute, so cwd ownership is decided globally (#9).
    cwd_ws: dict[str, set[str]] = {}
    for ws_id, recs_ in live.items():
        for r in recs_:
            c = str(r.get("cwd") or "")
            if c:
                cwd_ws.setdefault(c, set()).add(ws_id)

    def file_of(sid: str) -> Path | None:
        p = by_sid.get(sid)
        return Path(p) if p else None

    # ---- which registry rows ARE lane records (#8845) ----
    # A registry row can outlive the cmux workspace behind it. When the row's
    # workspace is ABSENT from a MEASURED live list and the row binds no evidence
    # of its own — no pane binding, no session record yielding a LIVE pid — then it
    # says nothing:
    # it cannot answer whether the lane stopped, started or was never there.
    # Emitting it anyway makes ONE label enumerate TWO lane records, and the claim
    # gate reads a duplicated label as unreadable by construction
    # (`collision_preflight._holders_from_board` -> HOLDER_UNMEASURED, which is
    # INCOMPLETE/exit 2 fleet-wide). That is a stale roster row tightening a
    # fail-closed gate over a lane that is in fact perfectly readable.
    #
    # ⛔ So such a row is not a lane record — but only while the SAME label already
    # has a row that DOES bind evidence of its own. A row that is its label's ONLY
    # record is KEPT even when unbound: #7738 publishes exactly that lane as
    # RECOVERABLE/RETIRED, because a gone workspace says nothing about whether a pi
    # process outlived it. Dropping it would erase the lane #7738 exists to report.
    #
    # ⛔ And a drop requires a MEASURED live list: an unreadable cmux list says
    # nothing about whether the workspace still exists, so it retires NOTHING — the
    # same fail-open posture `workspace_terminal` takes (#7738).
    #
    # ⛔ `overrides` is deliberately NOT consulted here, and this is the trap the
    # rule has to avoid: it is keyed by LABEL while this question is per ROW, so
    # `overrides.get(label)` answers identically for every row sharing that label —
    # once ANY override existed, the stale row would "bind evidence" too, nothing
    # would be dropped, and the duplicate (and the exit-2 gate) would come straight
    # back. The override keeps its own meaning elsewhere: it re-binds a ghost's
    # session on disk, and a label whose only row is unbound is kept anyway.
    #
    # ⛔ And the session arm needs a READING, not a stored ROW. `cmux sessions list
    # --all` is a durable HISTORY: it keeps records whose session has ENDED — which
    # is exactly why the liveness read below (`running`, `alive_pids`) filters the
    # same records instead of trusting them. So the mere EXISTENCE of a record for
    # this workspace proves nothing; only a record whose pid is alive right now is
    # the "pid reading" this arm claims to be. Accepting a finished record kept the
    # duplicate — and the exit-2 gate it causes — alive for precisely the #7738
    # ghost shape this rule exists to remove (review of #8849).
    #
    # Every dropped row is REPORTED through `orphans` below (kind `registry-stale`),
    # never silently erased: deleting the roster line is a human's call, not ours.
    def usable_pid_record(rec: Any) -> bool:
        """A stored session row that yields a USABLE reading: its pid is alive NOW."""
        if not isinstance(rec, dict):
            return False
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            return False
        # `agent_lifecycle` is deliberately NOT required to say "running": a
        # workspace holds several records (a stale lifecycle field, a respawn, a
        # child) and a live lane must not read dead because of one of them (#8). The
        # alive pid IS the reading; a dead or absent one is not.
        return pid_alive(pid)

    def row_binds_evidence(ws: str) -> bool:
        """Does this registry ROW bind evidence of its own? (ROW-keyed — see above.)"""
        if ws.upper() in workspaces:
            return True                      # the workspace itself is live
        if (bindings.get(ws.upper()) or "").strip():
            return True                      # a pane resume binding exists
        # a session record -> a pid reading, but ONLY while that pid is alive
        return any(usable_pid_record(r) for r in live.get(ws.upper(), []))

    bound_rows_by_label: dict[str, int] = {}
    issue_label_by_label: dict[str, str] = {}
    for label, ws, issue_label in registry:
        if row_binds_evidence(ws):
            bound_rows_by_label[label] = bound_rows_by_label.get(label, 0) + 1
        # `issue_label` is the ONLY column two rows identical under
        # (label, workspace) can differ in, so it is the one place "last-wins" is
        # observable. The LAST non-empty value wins: a registrar that appends a
        # corrected line leaves the correction at the END, and first-wins would
        # publish the stale label and then tell the human to delete the row that
        # carries the corrected one (review of #8849, cycle 3).
        if issue_label:
            issue_label_by_label[label] = issue_label
        # Exact duplicates are the ONE place last-wins applies, and it has to be
        # applied to the KEPT tuple: a registrar that appends a corrected line
        # leaves the correction at the end, and first-wins would publish the stale
        # label and then tell the human to delete the row carrying the correction.

    kept: list[tuple[str, str, str]] = []
    stale_rows: list[dict[str, Any]] = []
    unbound_kept: set[str] = set()
    seen_rows: set[tuple[str, str]] = set()
    kept_keys: set[tuple[str, str]] = set()
    kept_pos: dict[tuple[str, str], int] = {}
    for label, ws, issue_label in registry:
        evidence = row_binds_evidence(ws)
        # (0) THE SAME (label, workspace) TWICE IS ONE LANE WRITTEN TWICE — a
        # duplicated roster line, or a registrar re-run appending instead of
        # replacing. Both copies bind identical evidence (they name the same
        # workspace), so they produce IDENTICAL records: last-wins is lossless and
        # arm (a)'s justification ("two BOUND records can disagree about
        # liveness") does not apply, because they cannot disagree. Without this,
        # arm (a) refuses to dedupe exactly the case it must, the label still
        # enumerates two lane records, and the claim gate returns
        # HOLDER_UNMEASURED -> exit 2 on a perfectly readable lane — the failure
        # #8845 exists to remove (review of #8849).
        if (label, ws.upper()) in seen_rows:
            # LAST-WINS for the one column two duplicate rows can differ in (see the
            # comment in the loop above): adopt this row's non-empty `lane:` label on
            # the copy that was kept, then report the dropped line.
            _pos = kept_pos.get((label, ws.upper()))
            if issue_label and _pos is not None:
                kept[_pos] = (label, ws, issue_label)
            stale_rows.append({
                "lane": label, "workspace": ws, "issue_label": issue_label,
                "arm": "duplicate",
                "carries": issue_label,
                # ⛔ THE TWIN MAY ALREADY HAVE BEEN DROPPED by arm (a) — `seen_rows`
                # is populated before the arm decision, so a ghost row and its
                # literal copy can take DIFFERENT arms. The sentence therefore must
                # not assert "one lane record remains" unless one actually does
                # (review of #8849, cycle 3).
                "twin_kept": (label, ws.upper()) in kept_keys,
                "why": "it repeats this label's row for the same workspace",
            })
            continue
        seen_rows.add((label, ws.upper()))
        if workspaces_measured and not evidence:
            # (a) ANOTHER row for this label binds evidence on its own, so dropping
            # this one cannot erase the lane from the board.
            if bound_rows_by_label.get(label, 0) > 0:
                stale_rows.append({
                    "lane": label, "workspace": ws, "issue_label": issue_label,
                    "arm": "other-row-binds",
                    "why": "another row for this label does bind evidence",
                })
                continue
            # (b) TWO UNBOUND ROWS FOR ONE LABEL ARE ONE LANE ENUMERATED TWICE — the
            # same duplicated-label defect by a second route. Keep the first (a lone
            # ghost row must stay published, #7738) and report the rest. Both rows are
            # empty shells by construction here (not live, no pane binding, no session
            # record), so nothing is lost by keeping one.
            #
            # ⚠️ PRECONDITION, the same one arm (a) has: this runs only when the live
            # list was READ. With an unreadable cmux list nothing is retired, so both
            # rows survive and the claim gate reports the label as duplicated — which
            # is the honest reading of a board that could not be built, not a state
            # this filter is able to improve on.
            if label in unbound_kept:
                stale_rows.append({
                    "lane": label, "workspace": ws, "issue_label": issue_label,
                    "arm": "second-unbound",
                    "why": "it is a second unbound row for the same label",
                })
                continue
            unbound_kept.add(label)
        kept.append((label, ws, issue_label))
        kept_keys.add((label, ws.upper()))
        kept_pos[(label, ws.upper())] = len(kept) - 1
    # A dropped row may be the only one carrying the label's `lane:` label; the
    # surviving row inherits it rather than losing it — but ONLY when the survivor
    # has none of its own. The survivor's own value always wins: otherwise a row the
    # board has just classified as "not a lane record", for a DIFFERENT workspace,
    # would author the surviving lane's label and then the orphan would tell the
    # human to delete that very row — following the instruction would silently
    # change the published label (review of #8849, cycle 4).
    # ⛔ CAPTURE OWNERSHIP BEFORE THE FOLD. After it, a row's `lane:` value may be an
    # INHERITED one, so a set built from the folded list answers "did this label end up
    # with any value" — not "does a surviving row OWN one". Building it after the fold
    # made the revert warning unreachable and brought back a real revert (review of
    # #8849, cycle 10): with `[(L, STALE, lane:anc), (L, LIVE, ""), (L, STALE,
    # lane:anc2)]` — or `[(L, STALE, ""), (L, LIVE, ""), (L, STALE, lane:x)]` — the
    # board published a dropped line's value while the orphan said "delete the duplicate
    # roster line", and deleting it reverted the label.
    labels_owning_own_label: set[str] = {
        label for label, _ws, issue_label in kept if issue_label
    }
    kept = [
        (label, ws, issue_label or issue_label_by_label.get(label, ""))
        for label, ws, issue_label in kept
    ]

    rows: list[tuple[str, str]] = [(lbl, ws) for lbl, ws, _ in kept]

    # ---- disk candidates: ONLY for lanes the pane/override rules did not bind.
    # The identity scan reads the first user message of every file in a lane's
    # session directory; on the shared checkout directory that is 1.6 GB/240 files,
    # so scanning per-lane for every lane is 15x the necessary work.
    disk_by_label: dict[str, list[str]] = {}
    for label, ws in rows:
        sid = (bindings.get(ws.upper()) or "").strip()
        if sid and file_of(sid):
            continue
        sid = (overrides.get(label) or "").strip()
        if sid and file_of(sid):
            continue
        cwd = (workspaces.get(ws.upper(), {}) or {}).get("cwd") or ""
        # Prefer LIVE-PROCESS evidence (Deliverable 3 #3): the running pi session
        # record for this workspace names the session directly — no identity guess.
        running = [r for r in live.get(ws.upper(), [])
                   if r.get("agent_lifecycle") == "running" and r.get("session_id")]
        live_sids = sorted({r["session_id"] for r in running if file_of(r["session_id"])})
        if len(live_sids) == 1:
            disk_by_label[label] = live_sids
            continue
        if len(live_sids) > 1:
            disk_by_label[label] = live_sids  # ambiguous — resolve_sessions refuses
            continue
        d = mangled(cwd) if cwd else ""
        cands: list[str] = []
        toks = [t for t in re.split(r"[^a-z0-9]+", label.lower())
                if len(t) > 2 and t not in {"and", "the", "of", "for", "lane"}]
        need = len(toks)
        for f in by_dir.get(d, []):
            txt = first_user_message(f).lower()
            if txt and toks and sum(1 for t in toks if t in txt) >= need:
                m = _TS_RE.search(os.path.basename(f))
                if m:
                    cands.append(m.group(2))
        disk_by_label[label] = sorted(
            set(cands), key=lambda s: os.path.getmtime(by_sid[s]), reverse=True
        )
    resolutions, violations = resolve_sessions(
        rows, bindings, overrides, disk_by_label, file_of
    )

    # ---- process table + the two-sample CPU / pane window ----
    cpu0, parents = process_table()
    pane0: dict[str, dict[str, Any]] = {}
    if do_pane:
        for label, ws in rows:
            pane0[label] = pane_state(ws)
    if sample_s > 0:
        time.sleep(sample_s)
    cpu1, parents1 = process_table()
    pane1: dict[str, dict[str, Any]] = {}
    if do_pane:
        for label, ws in rows:
            pane1[label] = pane_state(ws)

    issue_by_num = {i.get("number"): i for i in issues}
    pr_by_num = {p.get("number"): p for p in prs}
    open_issue_numbers = set(issue_by_num)

    lanes: list[dict[str, Any]] = []
    owner_of_pr: dict[int, str | None] = {}
    issue_owner: dict[int, list[str]] = {}

    for label, ws, issue_label in kept:
        wsinfo = workspaces.get(ws.upper(), {}) or {}
        cwd = wsinfo.get("cwd") or ""
        wt = wts.get(cwd, {}) or {}
        branch = wt.get("branch", "").split("refs/heads/")[-1]
        head = wt.get("head", "")
        res = resolutions.get(label, SessionResolution(None, SOURCE_UNKNOWN, None, "not in registry"))
        # #7738: a lane whose cmux workspace no longer exists cannot keep a live
        # verdict. The verdict is terminal — RECOVERABLE (a session survives on
        # disk) or RETIRED — and is reported. A row dropped as a lane record above
        # (#8845) never reaches this function at all; that is why "reported, never
        # silently dropped" is a statement about ORPHANS, not about `rows`.
        terminal = workspace_terminal(ws, workspaces, workspaces_measured, res)

        # live process: a lane is alive if ANY recorded pi pid for its workspace is
        # still running. Requiring exactly one "running" record is too strict — a
        # workspace holds several records (a stale lifecycle field, a respawn, a
        # child) and a live lane must not read dead because of that (#8).
        recs = live.get(ws.upper(), [])
        lane_measured = liveness_measured and bool(recs)
        running = [r for r in recs if r.get("agent_lifecycle") == "running"]
        alive_pids = sorted({int(r["pid"]) for r in recs if r.get("pid") and pid_alive(int(r["pid"]))})
        alive = bool(alive_pids)
        primary = next((r for r in running if r.get("session_id") == res.sid), None) \
            or (running[0] if running else None)
        pid = primary.get("pid") if primary else (alive_pids[0] if alive_pids else None)
        # A live lane whose pane binding is absent or dangling needs the D2 repair.
        binding_missing = alive and res.source != SOURCE_PANE

        pids = tuple(alive_pids)
        child_set: set[int] = set()
        delta: float | None = None
        if pids and cpu0 and cpu1:
            tree_nodes: set[int] = set()
            for root in pids:
                tree_nodes.add(root)
                # Union the parent maps from BOTH samples: a grandchild spawned after
                # sample 0 is absent from the first map, and must still count (#free).
                tree_nodes |= descendants(root, parents)
                tree_nodes |= descendants(root, parents1)
            child_set = tree_nodes - set(pids)
            # Sum over the UNION of the trees, so a parent/child pair that are both
            # recorded is not counted twice.
            delta = sum(cpu1.get(p, 0.0) - cpu0.get(p, 0.0) for p in tree_nodes)
            # A vanished pid makes the delta NEGATIVE; that is a dying lane, not a quiet
            # one, so it must read UNMEASURED (never a free pass on a negative number).
            if delta < 0:
                delta = None
        p0 = pane0.get(label, {})
        p1 = pane1.get(label, {})
        ch0, ch1 = p0.get("ch_pct"), p1.get("ch_pct")
        ch_moving = None
        if ch0 is not None and ch1 is not None:
            ch_moving = ch0 != ch1
        live_lv = Liveness(
            pi_pids=pids,
            pid_alive=alive,
            cpu_delta_seconds=delta,
            cpu_sample_seconds=sample_s,
            descendant_count=len(child_set),
            pane_working=bool(p0.get("working") or p1.get("working")),
            pane_ch_pct=ch0,
            pane_ch_moving=ch_moving,
            transcript_age_seconds=transcript_age(res.file),
            liveness_measured=lane_measured,
        )

        # The lane's worktrees are every cwd its OWN sessions ran in (plus the workspace
        # cwd): a lane checks out a fresh worktree per task. Restricted to the lane's own
        # session ids — a REUSED workspace would otherwise inherit the previous lane's
        # worktrees and, through them, its PRs (#9).
        lane_sids = {res.sid} | {r.get("session_id") for r in running}
        lane_cwds = {cwd}
        for r in recs:
            c = str(r.get("cwd") or "")
            if not c:
                continue
            if r.get("session_id") in lane_sids or cwd_ws.get(c) == {ws.upper()}:
                lane_cwds.add(c)
        lane_cwds.discard("")
        lane_cwds.discard("")
        lane_branches = sorted({
            (wts.get(c, {}) or {}).get("branch", "").split("refs/heads/")[-1]
            for c in lane_cwds
        } - {""})
        sess_text = first_user_message(res.file) if res.file else ""
        claims = attribute_claims(
            lane_branches=lane_branches,
            lane_cwd=cwd,
            session_text=sess_text,
            open_prs=prs,
            open_issue_numbers=open_issue_numbers,
        )
        for n in claims.prs:
            owner_of_pr.setdefault(n, label)
        for n in claims.issues:
            issue_owner.setdefault(n, []).append(label)

        # work state per PR: CI verdict (ci_verdict) + review binding + mergeability
        pr_work = []
        for n in claims.prs:
            pr = pr_by_num.get(n, {})
            pr_head = pr.get("headRefOid") or ""
            verdict = None
            if ci_verdict and pr_head:
                try:
                    verdict = ci_verdict(repo, pr_head)
                except Exception:
                    verdict = None
            rec_path = REVIEWS_DIR / f"{n}.json"
            if not rec_path.exists():
                alt = REVIEWS_DIR / f"{repo.replace('/', '-')}-{n}.json"
                if alt.exists():
                    rec_path = alt
            binding = review_binding(
                n,
                pr_head,
                record_path=rec_path,
                live_diff_sha256=(live_diff(n) if live_diff else None),
            )
            pr_work.append({
                "number": n,
                "title": pr.get("title"),
                "headRefName": pr.get("headRefName"),
                "head_sha": pr_head,
                "url": pr.get("url"),
                "ci": verdict,
                "review_binding": binding,
                "mergeable": pr.get("mergeable"),
                "merge_state_status": pr.get("mergeStateStatus"),
            })

        issue_work = []
        for n in claims.issues:
            it = issue_by_num.get(n, {})
            issue_work.append({
                "number": n,
                "title": it.get("title"),
                "state": it.get("state"),
                "assignees": [a.get("login") for a in it.get("assignees", [])],
                "lane_label": next(
                    (lab.get("name") for lab in it.get("labels", []) if str(lab.get("name", "")).startswith("lane:")),
                    "",
                ),
                "url": it.get("url"),
            })

        reasons = free_reasons(live_lv, len(pr_work), len(issue_work))
        if not lane_measured:
            reasons.append("no session record for this lane — liveness UNMEASURED, not dead")
        elif not alive:
            reasons.append("no live pi process — a dead lane is not dispatchable")
        if terminal["state"]:
            reasons.append(terminal["reason"])
        lanes.append({
            "identity": {
                "lane": label,
                "workspace": ws,
                "worktree": cwd,
                "branch": branch,
                "head_sha": head,
            },
            "session": res.to_dict() | {
                "live_session_id": (primary or {}).get("session_id"),
                "pi_pid": pid,
                "pid_alive": alive,
                "binding_missing": binding_missing,
            },
            "liveness": live_lv.to_dict(),
            "claim": {
                "issues": list(claims.issues),
                "prs": list(claims.prs),
                "evidence": dict(claims.evidence),
                "lane_label": issue_label,
                "attributed_from": "branch/worktree/session (never comment prose)",
            },
            "work": {"prs": pr_work, "issues": issue_work},
            "activity": {
                "last_transcript_write_age_s": live_lv.transcript_age_seconds,
                "last_head_sha": head,
            },
            "terminal": terminal,
            "free": not reasons,
            "not_free_reasons": reasons,
        })

    # orphans: open PRs with no live owner (the #7746 class)
    # When a lane has NO session record at all, its liveness is UNMEASURED — never
    # "owner lane X has no live pi" (an unmeasured liveness must not manufacture a
    # dead owner, per-lane as well as fleet-wide).
    lane_live: dict[str, bool] = {}
    for lane_ in lanes:
        lbl = lane_["identity"]["lane"]
        if lane_["terminal"]["state"]:
            # #7738: a terminal lane is DEAD, not "unmeasured". Mapping an
            # unmeasured lane to True (the anti-false-orphan default) is exactly
            # how a ghost kept a live verdict forever.
            lane_live[lbl] = False
        elif not lane_["liveness"]["liveness_measured"]:
            lane_live[lbl] = True
        else:
            lane_live[lbl] = bool(lane_["session"]["pi_pid"] and lane_["session"]["pid_alive"])
    conflicts, conflict_claimants, orphans = resolve_conflicts(lanes, prs, owner_of_pr, lane_live)
    if not liveness_measured:
        orphans.append({
            "kind": "liveness-unmeasured",
            "reason": "the live-session store could not be read — dead-owner detection suppressed",
        })
    # a second orphan class: a live lane owns no work and is not free (stranded)
    for lane_ in lanes:
        if lane_["session"]["binding_missing"]:
            orphans.append({
                "kind": "binding",
                "lane": lane_["identity"]["lane"],
                "workspace": lane_["identity"]["workspace"],
                "reason": "live pi session has NO pane resume binding — recovery destroyed attributability (#7750 D2)",
            })
    # a third orphan class (#8845): a registry row that is NOT a lane record. It can
    # be one of two things — a row whose workspace is gone for a label another row
    # already answers, or a second unbound row repeating a label — and it is still
    # VISIBLE here either way: the roster line is stale, and clearing it is a human's
    # call.
    # ⛔ THE PUBLISHED VALUE MUST COME FROM THE FINAL BOARD, not from the running
    # tuple. `kept[_pos]` is overwritten by every later duplicate that carries a
    # value, so a message that names "the published value" from the state at its own
    # point in the loop is FALSE at three or more copies — measured: `[lane:a, "",
    # lane:b]` publishes `lane:b` while the middle row's orphan said `lane:a` "is what
    # the board publishes" (review of #8849, cycle 8). `kept` is already the
    # post-rebuild list here, so this map IS the answer, for any number of copies.
    published_by_ws: dict[tuple[str, str], str] = {
        (lbl, ws.upper()): ilbl for lbl, ws, ilbl in kept
    }
    # Labels where a SURVIVING row OWNS a `lane:` value — captured BEFORE the
    # inheritance fold (see there). Inheritance is own-wins, so a dropped row's value
    # is irrelevant to such a label; warning about a revert there would tell the human
    # to keep a row the board has already classified as not a lane record.
    def sole_carrier_value(row: dict[str, Any]) -> str:
        """
        The `lane:` value this row is the SOLE CARRIER of, or ``""`` if none.

        ⛔ EVERY arm needs this test, not just `duplicate`. A dropped row can be the
        label's sole carrier of the `lane:` value: the surviving row inherits it, so the
        board PUBLISHES a value that lived only on the line the human is told to delete
        — and obeying that instruction silently reverts the label (review of #8849).

        Two conditions, and both are needed:
        * OWNERSHIP — no surviving row owns a value, so the label's value can only come
          from a dropped line (own-wins would otherwise make this row's value moot);
        * EQUALITY with the value that inheritance would publish (`issue_label_by_label`
          is last-non-empty-wins across the registry). With three or more dropped
          carriers they disagree, and naming one the board does NOT publish would send
          the human to move a value that changes the label.
        `published_by_ws` cannot answer this: in the `twin_kept=False` path that
        workspace has no kept row, so its lookup is always `""`.
        """
        own = row.get("issue_label") or ""
        if (
            own
            and row["lane"] not in labels_owning_own_label
            and own == issue_label_by_label.get(row["lane"])
        ):
            return own
        return ""

    def delete_advice(row: dict[str, Any]) -> str:
        """The per-row delete instruction, with the revert warning when it is owed.

        Used by the two non-duplicate arms; `duplicate` branches on the same predicate
        but keeps its own (longer) sentence.
        """
        value = sole_carrier_value(row)
        if value:
            return (
                f"⛔ The board publishes `{value}` for this label only because this "
                "dropped line carries it — the surviving row inherits it. Keep this "
                "line, or move that value onto the surviving row BEFORE deleting it, "
                "or the label reverts"
            )
        return "Delete the stale roster row"
    for row in stale_rows:
        ws_gone = f"workspace {row['workspace'][:8]}…" if row["workspace"] else "its workspace"
        arm = row.get("arm")
        # ⛔ THE SENTENCE MUST BE BUILT PER ARM. The three arms drop for different
        # reasons, and one template told all of them the same story: that the
        # workspace is NOT in the live cmux list and the row binds NO evidence. For
        # arm `duplicate` both clauses are FALSE — that row's workspace IS live (it
        # is the same live row, written twice), so a human was told to delete a
        # roster line because its workspace was dead while the board showed it alive
        # (review of #8849).
        if arm == "duplicate":
            _carries = row.get("carries") or ""
            _published = published_by_ws.get(
                (row["lane"], (row.get("workspace") or "").upper()), ""
            )
            if row.get("twin_kept"):
                # ⛔ BRANCH ON THE FINAL PUBLISHED VALUE, not on the reported row's
                # value alone (which said "Neither line carries a `lane:` value"
                # when the KEPT line carried it) and not on the running tuple
                # (which is stale at 3+ copies). Both faults printed a value the
                # board does not publish (review of #8849, cycles 7 and 8).
                if _carries and _published == _carries:
                    _advice = (
                        f"This line carries `{_carries}`, which IS the value the "
                        "board publishes. Keep a line carrying that value — this "
                        "one — and delete the other copy"
                    )
                elif _carries:
                    _advice = (
                        f"This line carries `{_carries}`, but the board publishes "
                        f"`{_published}` (from another line for this (label, "
                        "workspace)). Delete this line — or, if that value is the "
                        f"intended one, put `{_carries}` on the line you keep"
                    )
                elif _published:
                    _advice = (
                        "This line carries no `lane:` value; the board publishes "
                        f"`{_published}` from another line for this (label, "
                        "workspace). Delete THIS line"
                    )
                else:
                    _advice = (
                        "Neither this line nor the one the board uses carries a "
                        "`lane:` value, so either may be deleted"
                    )
                reason = (
                    f"registry row for '{row['lane']}' repeats {ws_gone} — the same "
                    "(label, workspace) written twice is ONE lane, not two; the "
                    "duplicate is not a second lane record and NO verdict changes "
                    "(only the repeated line goes away, not a lane). This is a "
                    "de-duplication, not a retirement: it holds even when the cmux "
                    "list could not be read, because both rows name one workspace. "
                    f"{_advice}"
                )
            else:
                # The twin was dropped by another arm, so this drop is NOT a plain
                # de-duplication — say so, and do not claim a record survives.
                # ⛔ AND THIS ROW MAY BE THE ONLY CARRIER of the label's `lane:`
                # value, which the board publishes via inheritance: a bare "delete
                # the duplicate roster line" would revert the label, the very
                # failure the sibling branch is written to prevent (cycle 6).
                # ⛔ AND THIS ROW MAY BE THE ONE THE PUBLISHED VALUE COMES FROM. The
                # gate is OWNERSHIP plus the published value: if no surviving row owns a
                # `lane:` value, the published one comes from a dropped line like this
                # and deleting it reverts the label. Testing the published value ALONE is
                # wrong (it matches when a survivor OWNS that value too, and
                # `published_by_ws` is always "" here — there is no kept row for this
                # workspace), and reading the owner set AFTER the inheritance fold is
                # wrong too — it then contains the inherited value and the gate never
                # fires (review of #8849, cycles 8 and 10).
                if sole_carrier_value(row):
                    reason = (
                        f"registry row for '{row['lane']}' repeats {ws_gone}, "
                        "and the copy of this row was ALREADY dropped above for its "
                        "own reason — read that entry, which is the one that explains "
                        "why this workspace is no longer a lane record. ⛔ The board "
                        f"publishes `{_carries}` only because this dropped line "
                        "carries it — keep this line, or move that value onto the "
                        "surviving row BEFORE deleting it, or the label reverts"
                    )
                else:
                    reason = (
                        f"registry row for '{row['lane']}' repeats {ws_gone}, "
                        "and the copy of this row was ALREADY dropped above for its "
                        "own reason — read that entry, which is the one that explains "
                        "why this workspace is no longer a lane record. Delete the "
                        "duplicate roster line"
                    )
        elif arm == "second-unbound":
            reason = (
                f"registry row for '{row['lane']}' names {ws_gone}, which is NOT in "
                "the live cmux list and binds no pane binding or session record with "
                "a LIVE pid, so it is not a lane record; it repeats a label that "
                "already has one such row kept, and two unbound rows for one label "
                "are one lane enumerated twice. "
                f"{delete_advice(row)}"
            )
        else:
            reason = (
                f"registry row for '{row['lane']}' names {ws_gone}, which is NOT in "
                "the live cmux list, and the row binds NO pane binding or session "
                "record with a LIVE pid of its own — it is not a lane record "
                f"({row.get('why') or 'a row for this label does bind evidence'}); "
                f"{delete_advice(row)}"
            )
        orphans.append({
            "kind": "registry-stale",
            "lane": row["lane"],
            "workspace": row["workspace"],
            "reason": reason,
        })

    # index for `who`
    index: dict[str, dict[str, Any]] = {"prs": {}, "issues": {}}
    for lane_ in lanes:
        lbl = lane_["identity"]["lane"]
        for n in lane_["claim"]["prs"]:
            index["prs"].setdefault(str(n), {"lanes": [], "evidence": {}})
            index["prs"][str(n)]["lanes"].append(lbl)
            index["prs"][str(n)]["evidence"][lbl] = lane_["claim"]["evidence"].get(f"PR {n}", "")
        for n in lane_["claim"]["issues"]:
            index["issues"].setdefault(str(n), {"lanes": [], "evidence": {}})
            index["issues"][str(n)]["lanes"].append(lbl)
            index["issues"][str(n)]["evidence"][lbl] = lane_["claim"]["evidence"].get(f"issue {n}", "")

    free_lanes = [lane_["identity"]["lane"] for lane_ in lanes if lane_["free"]]
    terminal_lanes = [
        {
            "lane": lane_["identity"]["lane"],
            "workspace": lane_["identity"]["workspace"],
            "state": lane_["terminal"]["state"],
            "reason": lane_["terminal"]["reason"],
            "resume_command": lane_["terminal"]["resume_command"],
            "session": lane_["session"]["sid"],
        }
        for lane_ in lanes
        if lane_["terminal"]["state"]
    ]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo": repo,
        "sample_seconds": sample_s,
        "lanes": lanes,
        "violations": violations,
        "orphans": orphans,
        "terminal": terminal_lanes,
        "reconciliation": {
            "workspaces_measured": workspaces_measured,
            "error": workspaces_error,
            # #7957 H5: the board's own provenance, so a cache that was built from a
            # failed read can never be served as an answer by a later reader.
            "github_measured": github_measured,
            "github_error": github_error,
            # THE LANE ROSTER'S OWN PROVENANCE (same rule, same source class): a
            # registry read that failed enumerates NO lanes and must be a NAMED
            # failure a reader can refuse on, never an empty fleet.
            "registry_measured": registry_measured,
            "registry_error": registry_error,
            # #8175: was the OPEN-ISSUE read WIDE ENOUGH to prove a negative? A
            # truncated read cannot support "no lane is attributed to issue N"
            # for an N it did not see, so the flag travels with the board and the
            # pre-flight refuses for an absent issue rather than answering.
            "open_issues_truncated": issues_truncated,
            "open_issues_read": len(issues_read),
            "open_issues_limit": OPEN_ISSUES_LIMIT,
        },
        "conflicts": conflicts,
        "conflict_claimants": {str(n): who for n, who in conflict_claimants.items()},
        "index": index,
        "summary": {
            "lanes": len(lanes),
            "bound": sum(1 for lane_ in lanes if lane_["session"]["bound"]),
            "unknown_binding": sum(1 for lane_ in lanes if not lane_["session"]["bound"]),
            "binding_missing_but_live": sum(1 for lane_ in lanes if lane_["session"]["binding_missing"]),
            "genuinely_free": len(free_lanes),
            "free_lanes": free_lanes,
            "violations": len(violations),
            "orphans": len(orphans),
            "stale_registry": len(stale_rows),
            "conflicts": len(conflicts),
            "terminal": len(terminal_lanes),
            "recoverable": sum(1 for t in terminal_lanes if t["state"] == "RECOVERABLE"),
            "retired": sum(1 for t in terminal_lanes if t["state"] == "RETIRED"),
            "workspaces_measured": workspaces_measured,
            "github_measured": github_measured,
            "registry_measured": registry_measured,
        },
    }


# ===========================================================================
# rendering / CLI
# ===========================================================================


def _load_or_build(args: argparse.Namespace) -> dict[str, Any]:
    """Serve the cached state — but only within ``STATE_FRESH_S`` (#7957 H1/H2/H5).

    The old contract was "if the file exists, answer from it", which served a
    four-hour-old board as current with no age on the answer (the epic's headline
    defect, in-repo). The contract now is:

      * fresh stamp            -> serve, annotated with its exact age;
      * stale OR unreadable    -> REBUILD before answering (the age served is bounded,
        not merely labelled), say so on stderr, and repair the cache when the fresh
        read was itself measured — otherwise the next reader pays for the same
        rebuild. ``--allow-stale`` opts into reading the aged cache on purpose, which
        is then annotated ``stale`` and rendered ``STALE(>900s)``;
      * ``--refresh``          -> rebuild as before.

    ``freshness`` is attached to whatever is returned (also for freshly built
    states), so every answer carries its own age and no reader can be fooled by
    hand-comparing timestamps.
    """
    path = Path(getattr(args, "state", DEFAULT_OUT))
    cached: Any = None
    existed = path.exists()
    if existed:
        try:
            cached = json.loads(path.read_text())
        except Exception:
            cached = None
    if not isinstance(cached, dict):
        if existed and not getattr(args, "refresh", False):
            print(
                f"fleet-state: {path} is unreadable (not a JSON object) — rebuilding",
                file=sys.stderr,
            )
        cached = None
    elif not getattr(args, "refresh", False):
        fr = state_freshness(cached)
        if not fr["stale"]:
            _report_state_age(fr, source="cache")
            return _annotate_freshness(cached, fr)
        if getattr(args, "allow_stale", False):
            print(
                f"fleet-state: {render_state_age(fr)} — serving the CACHED answer because "
                "--allow-stale was passed; it is NOT current",
                file=sys.stderr,
            )
            return _annotate_freshness(cached, fr)
        print(
            f"fleet-state: {render_state_age(fr)} — rebuilding before answering "
            "(pass --allow-stale to read the aged cache on purpose)",
            file=sys.stderr,
        )
    # A reader that rebuilds an expired cache is doing what the expiry requires: the
    # value it returns is a fresh MEASUREMENT, not a served cache. `build` owns its
    # own write path (`--out`), so the repair-write below skips it.
    state = build_state(
        repo=resolve_repo(args.repo),
        repo_root=resolve_repo_root(args.repo),
        orch_ws=os.environ.get("ORCH_WS", ""),
        sample_s=args.sample,
        do_pane=args.sample > 0,
        ci_verdict=None if getattr(args, "no_ci", False) else make_ci_verdict(),
        live_diff=None if getattr(args, "no_ci", False) else make_live_diff(),
    )
    fr = state_freshness(state)
    # Repair the expired cache — but never from an UNMEASURED read (#7957 H5: a failed
    # read is never cached), and never from `build`, which owns its own write path.
    if (
        github_provenance(state)["measured"]
        and registry_provenance(state)["measured"]
        and getattr(args, "cmd", None) != "build"
        and _write_state_json(path, state)
    ):
        print(f"fleet-state: cache repaired at {path}", file=sys.stderr)
    _report_state_age(fr, source="rebuilt")
    return _annotate_freshness(state, fr, rebuilt=True)


def state_age_seconds(state: Mapping[str, Any]) -> float | None:
    """The age of the state's OWN ``generated_at`` stamp, or None when unreadable.

    ``build_state`` writes a real measurement stamp, so the age is a property of the
    reading, not of the file's mtime (the #7159 lesson: never derive an age from a
    timestamp that means something else). ``None`` means "cannot be read" — which is
    NOT "fresh": an unknown age is never served as current.
    """
    raw = state.get("generated_at") if isinstance(state, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        ts = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if ts.tzinfo is None:
        # `build_state` always writes an offset (`%z`); a naive stamp is LOCAL.
        ts = ts.astimezone()
    age = time.time() - ts.timestamp()
    if age < -STATE_CLOCK_SKEW_S:
        # A stamp from the FUTURE is not an age. `max(0.0, ...)` reported it as the
        # FRESHEST possible state, so a future-dated or corrupt cache answered forever
        # and was never rebuilt — the exact failure this window exists to remove
        # (#7957 H1). Treated as unreadable, which `state_freshness` maps to "never
        # current", per this module's fail-closed polarity.
        return None
    return max(0.0, age)


def state_freshness(state: Mapping[str, Any]) -> dict[str, Any]:
    """``{age_seconds, stale, reason}`` for a cached state (#7957 H1)."""
    age = state_age_seconds(state)
    if age is None:
        return {
            "age_seconds": None,
            "stale": True,
            "reason": (
                "the state carries no usable `generated_at` stamp (absent, "
                "unparseable, or dated in the future), so its age is UNKNOWN — "
                "an unknown age is never current (#7957 H1)"
            ),
        }
    if age > STATE_FRESH_S:
        return {
            "age_seconds": age,
            "stale": True,
            "reason": (
                f"the state is {int(age)}s old, past the {int(STATE_FRESH_S)}s window "
                "(#7957 H1/H2)"
            ),
        }
    return {"age_seconds": age, "stale": False, "reason": ""}


def render_state_age(fr: Mapping[str, Any]) -> str:
    """``state_age=15m`` / ``state_age=298m STALE(>900s)`` / ``state_age=?``.

    Minutes for the human value (the incumbent rendering in
    ``~/.pi/agent/scripts/turn-classify.py``), while the DECISION uses exact seconds —
    so rounding never moves the boundary.
    """
    age = fr.get("age_seconds")
    if age is None:
        return "state_age=? (unreadable stamp)"
    label = f"state_age={int(float(age) // 60)}m"
    return f"{label} STALE(>{int(STATE_FRESH_S)}s)" if fr.get("stale") else label


def _annotate_freshness(
    state: dict[str, Any], fr: Mapping[str, Any], *, rebuilt: bool = False
) -> dict[str, Any]:
    if isinstance(state, dict):
        age = fr.get("age_seconds")
        state["freshness"] = {
            "age_seconds": None if age is None else round(float(age), 3),
            "stale": bool(fr.get("stale")),
            "window_seconds": STATE_FRESH_S,
            "reason": str(fr.get("reason") or ""),
            "rebuilt": bool(rebuilt),
        }
    return state


def _report_state_age(fr: Mapping[str, Any], *, source: str) -> None:
    """Every run says the age it answered from (H1: never infer it by hand)."""
    print(f"fleet-state: {render_state_age(fr)} (source={source})", file=sys.stderr)


#: Attempts to create a collision-free temp beside the cache. `O_EXCL` refuses an
#: existing path, so even a collision fails SAFE instead of clobbering another writer.
_TMP_ATTEMPTS = 8
_TMP_SEQ = itertools.count()


def _write_state_json(path: Path, state: Mapping[str, Any]) -> bool:
    """Best-effort atomic cache write. A torn cache is worse than none.

    The temp is created with mode ``0o666`` so the KERNEL applies the process umask —
    exactly what ``Path.write_text`` produced. ``tempfile.mkstemp`` was tried first here
    and creates ``0o600``, which ``os.replace`` then made the cache's FINAL mode: the
    cache silently narrowed to owner-only, locking out a non-owner reader (a container, a
    group process) with no error at write time, only a failure to read later (#7957 H2).
    """
    tmp_name = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A UNIQUE temp per writer: every reader that finds a stale cache tries to repair
        # it, so a fixed `<name>.tmp` is shared and the writers that lose the race have
        # their repair silently dropped, re-paying the full rebuild on the next read
        # (#7957 H2). pid + a per-process counter is unique without a random name, and
        # `O_EXCL` turns even a collision into a retry rather than a clobber.
        fd = -1
        for _ in range(_TMP_ATTEMPTS):
            candidate = f"{path.name}.{os.getpid()}.{next(_TMP_SEQ)}.tmp"
            tmp_name = str(path.parent / candidate)
            try:
                fd = os.open(tmp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
                break
            except FileExistsError:
                continue
        if fd < 0:
            return False
        try:
            # `with` owns the descriptor: it is closed on EVERY path through this block,
            # including a `json.dumps` failure. Nothing here closes `fd` by hand, and it
            # must not — `os.fdopen` finalises and closes the FileIO when a later stage of
            # the call fails, so a second close would hit whatever the number had been
            # reused for (#7957 H2: readers repair the same cache concurrently).
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(state, indent=2, default=str))
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        return True
    except OSError:
        return False


def github_provenance(state: Mapping[str, Any]) -> dict[str, Any]:
    """Did the BOARD this state was built from actually read (#7957 H5)?

    A cache written before this field existed carries no provenance; it is treated as
    MEASURED — the same tolerant reading ``conflict_map`` takes for a
    pre-``conflict_claimants`` cache. Only an EXPLICIT failure makes a reader refuse
    to answer, so a legacy cache cannot wedge the fleet.
    """
    rec = state.get("reconciliation") if isinstance(state, Mapping) else None
    if not isinstance(rec, Mapping) or "github_measured" not in rec:
        return {"measured": True, "error": ""}
    return {
        "measured": bool(rec.get("github_measured")),
        "error": str(rec.get("github_error") or ""),
    }


def registry_provenance(state: Mapping[str, Any]) -> dict[str, Any]:
    """Did the LANE ROSTER this state was built from actually read (#8246)?

    Same tolerant reading as ``github_provenance``: a cache written before this
    field existed carries no provenance and is treated as MEASURED, so a legacy
    cache cannot wedge the fleet — only an EXPLICIT failure makes a reader refuse.
    """
    rec = state.get("reconciliation") if isinstance(state, Mapping) else None
    if not isinstance(rec, Mapping) or "registry_measured" not in rec:
        return {"measured": True, "error": ""}
    return {
        "measured": bool(rec.get("registry_measured")),
        "error": str(rec.get("registry_error") or ""),
    }


def payload_freshness(state: Mapping[str, Any]) -> dict[str, Any]:
    """The freshness block for a JSON payload (H1 for the MACHINE channel).

    A human reads the stderr line; the orchestrator reads JSON. Both must carry the
    age, computed from the state itself rather than trusted from prose, so a consumer
    can never have to guess whether the answer it just got is current.
    """
    fr = state_freshness(state) if isinstance(state, Mapping) else {
        "age_seconds": None, "stale": True, "reason": "not a state object"
    }
    annotated = state.get("freshness") if isinstance(state, Mapping) else None
    return {
        "age_seconds": fr["age_seconds"],
        "stale": fr["stale"],
        "window_seconds": STATE_FRESH_S,
        "reason": fr["reason"],
        "rebuilt": bool(annotated.get("rebuilt")) if isinstance(annotated, Mapping) else False,
    }


def _board_readable(state: Mapping[str, Any], cmd: str, what: str) -> bool:
    """True when the command may answer. A FAILED read is never an answer (#7957 H5).

    The alternative is what the tool did until now: print the empty-board answer
    ("no lane holds it", "no ownership conflicts", a lane offered as free) from a read
    that never happened. Refusing is the honest outcome, and the exit code says so.
    """
    gp = github_provenance(state)
    if gp["measured"]:
        return True
    print(
        f"{cmd}: GitHub is UNREADABLE ({gp['error'] or 'cause unknown'}) — {what}; "
        "nothing is asserted rather than asserted empty (#7957 H5)",
        file=sys.stderr,
    )
    return False


def _registry_readable(state: Mapping[str, Any], cmd: str, what: str) -> bool:
    """True when the LANE ROSTER read may be answered from. A failed read is not.

    ``load_registry`` used to swallow an ``OSError`` into ``[]``, which made an
    unreadable registry indistinguishable from a fleet with no lanes: every command
    that asserts "no free lane", "no lane holds it", "no ownership conflicts",
    "no orphans", "no lane matching that name", "nothing to bind" or
    "no session-partition breaks" answered from a read that never happened.
    Same polarity as ``_board_readable`` (#7957 H5).
    """
    rp = registry_provenance(state)
    if rp["measured"]:
        return True
    print(
        f"{cmd}: the lane registry is UNREADABLE ({rp['error'] or 'cause unknown'}) — "
        f"{what}; nothing is asserted rather than asserted empty (#7957 H5)",
        file=sys.stderr,
    )
    return False


def resolve_repo(repo: str | None) -> str:
    if repo:
        return repo
    out = _run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], timeout=30)
    return out.strip() or "daniel-ospina/tortoise"


def resolve_repo_root(repo: str | None) -> str:
    if repo and "/" not in repo and Path(repo).is_dir():
        return str(Path(repo).resolve())
    out = _run(["git", "rev-parse", "--show-toplevel"], timeout=30).strip()
    return out or str(Path.cwd())


def make_ci_verdict() -> Callable[[str, str], dict[str, Any]] | None:
    """Bind ``tools/ci_verdict.read_verdict`` — never re-derive the rule here."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tools import ci_verdict

        def _v(repo: str, sha: str) -> dict[str, Any]:
            return ci_verdict.read_verdict(repo, sha).to_dict()

        return _v
    except Exception:
        return None


def make_live_diff() -> Callable[[int], str | None] | None:
    def _d(pr: int) -> str | None:
        try:
            raw = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.github.v3.diff",
                 f"repos/{resolve_repo(None)}/pulls/{pr}"],
                capture_output=True, timeout=60, check=False,
            ).stdout
            if not raw:
                return None
            norm = Path(os.environ.get("AGENT_INFRA_PATH", "")) / "scripts/lib/diff-normalize.py"
            if norm.is_file():
                r = subprocess.run([sys.executable, str(norm)], input=raw,
                                   capture_output=True, timeout=30, check=False)
                data = r.stdout if r.stdout else raw
            else:
                data = normalize_review_diff(raw)
            return hashlib.sha256(data).hexdigest() if data else None
        except Exception:
            return None

    return _d


def cmd_build(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    out = Path(args.out or args.state)
    # #7957 H5: a failed read is never CACHED. Writing an unread board as the new state
    # would replace the last good one with an empty fleet — and on the next read free
    # every lane into capacity. Refuse, leave the previous cache untouched, and say so
    # with a non-zero exit.
    gp = github_provenance(state)
    if not gp["measured"]:
        if args.json:
            print(json.dumps(state, indent=2, default=str))
        print(
            "⛔ NOT CACHED: the GitHub board could not be read "
            f"({gp['error'] or 'cause unknown'}) — a failed read is never written as "
            f"fact (#7957 H5); {out} is left untouched",
            file=sys.stderr,
        )
        return 2
    # And the ROSTER, for the same reason: a cached zero-lane state would be served
    # as a fresh fleet for the whole TTL, dark.
    rp = registry_provenance(state)
    if not rp["measured"]:
        if args.json:
            print(json.dumps(state, indent=2, default=str))
        print(
            "⛔ NOT CACHED: the lane registry could not be read "
            f"({rp['error'] or 'cause unknown'}) — a failed read is never written as "
            f"fact (#7957 H5); {out} is left untouched",
            file=sys.stderr,
        )
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    if not _write_state_json(out, state):
        print(f"could not write {out}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(state, indent=2, default=str))
    else:
        s = state["summary"]
        print(f"fleet-state -> {out}")
        print(f"  lanes={s['lanes']} bound={s['bound']} unknown_binding={s['unknown_binding']} "
              f"binding_missing_but_live={s['binding_missing_but_live']}")
        print(f"  genuinely_free={s['genuinely_free']} violations={s['violations']} "
              f"orphans={s['orphans']} conflicts={s.get('conflicts', 0)}")
        print(f"  terminal={s.get('terminal', 0)} "
              f"(recoverable={s.get('recoverable', 0)} retired={s.get('retired', 0)})")
        rec = state.get("reconciliation") or {}
        if "workspaces_measured" in rec and not rec.get("workspaces_measured"):
            # A fail-open path must SAY it failed open: silence here would read as
            # "no ghosts" when the truth is "ghosts cannot be detected" (#7738).
            print(
                "  ⚠️ workspace reconciliation is INERT (fail-open): "
                f"{rec.get('error') or 'cmux workspace list unreadable'} — "
                "no lane was retired; ghost lanes cannot be detected in this run",
                file=sys.stderr,
            )
    return 0


def conflict_map(state: Mapping[str, Any]) -> dict[str, list[str]]:
    """Contested PR -> claimant lanes, tolerating a pre-``conflict_claimants`` cache.

    A state file written by an older build carries ``conflicts`` but not
    ``conflict_claimants``. Degrading to "claimants unknown" is honest; reporting
    the PR as UNOWNED is the exact #7750 false negative — so the key set is the
    UNION of ``conflicts`` and ``conflict_claimants``, and a malformed value is
    never a crash (the file's contract is "degrade to empty, never crash").
    """
    nums = state.get("conflicts")
    if not isinstance(nums, (list, tuple, set)):
        nums = []
    out: dict[str, list[str]] = {str(n): [] for n in nums}
    have = state.get("conflict_claimants")
    if isinstance(have, dict):
        for k, v in have.items():
            out[str(k)] = [str(x) for x in v] if isinstance(v, (list, tuple)) else []
    return out


def lane_live_map(state: Mapping[str, Any]) -> dict[str, bool | None]:
    """lane label -> True (live pi) / False (dead) / None (liveness unmeasured).

    A lane with no pid evidence — no session record at all, or an empty one — is
    UNMEASURED here, not dead: there is nothing to call dead. ``lanes`` is checked
    for shape, so a malformed cache degrades instead of crashing.
    """
    out: dict[str, bool | None] = {}
    lanes = state.get("lanes")
    if not isinstance(lanes, (list, tuple)):
        return out
    for lane_ in lanes:
        try:
            label = lane_["identity"]["lane"]
            if (lane_.get("terminal") or {}).get("state"):
                # #7738: a terminal lane is DEAD even when its liveness is
                # unmeasured — otherwise a ghost reads as a live owner forever.
                out[label] = False
                continue
            measured = (lane_.get("liveness", {}) or {}).get("liveness_measured")
            s = lane_.get("session")
            if not measured or not isinstance(s, dict) or not s.get("pi_pid"):
                out[label] = None
            else:
                out[label] = bool(s.get("pid_alive"))
        except Exception:
            continue
    return out


def _live_tag(live: bool | None) -> str:
    if live is True:
        return " (live)"
    if live is False:
        return " (DEAD)"
    return ""


def cmd_who(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    key = str(args.number)
    for kind, label in (("prs", "PR"), ("issues", "issue")):
        entry = state["index"][kind].get(key)
        if entry:
            # Liveness must survive this branch too: an index entry outlives the
            # lane that wrote it, and "held by X (live)" vs "(terminal)" is the
            # whole question `who` is asked.
            live = lane_live_map(state)
            out = {"number": args.number, "kind": label, "lanes": entry["lanes"],
                   "live": {lbl: live.get(lbl) for lbl in entry["lanes"]},
                   "evidence": entry["evidence"],
                   "freshness": payload_freshness(state)}
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"#{args.number} ({label}) held by: "
                      + ", ".join(f"{lbl}{_live_tag(live.get(lbl))}" for lbl in entry["lanes"]))
                for lane, why in entry["evidence"].items():
                    print(f"  - {lane}: {why}")
            return 0
    # A contested PR is NOT "nobody holds it": two lanes asserted it. Name them.
    cmap = conflict_map(state)
    if key in cmap:
        who = cmap[key]
        live = lane_live_map(state)
        out = {"number": args.number, "kind": "PR", "conflict": True,
               "lanes": who, "live": {lbl: live.get(lbl) for lbl in who},
               "evidence": {}, "freshness": payload_freshness(state)}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            if who:
                print(f"#{args.number} is CONTESTED — claimed by: "
                      + ", ".join(f"{lbl}{_live_tag(live.get(lbl))}" for lbl in who))
            else:
                print(f"#{args.number} is CONTESTED — claimants unknown (stale fleet-state; re-run build)")
            print("  a reused workspace can inherit the previous lane's worktree; "
                  "resolve the worktree before assigning")
        return 0
    # #7957 H5: "no lane holds it" must never be asserted from a read that FAILED.
    # The board being unreadable is exactly when a claim looks absent.
    if not _board_readable(state, "who", f"cannot say whether a lane holds #{args.number}"):
        return 2
    # #8246 / PR #8594 review: the ROSTER is the OTHER source behind "no lane holds
    # it". `index` is built from `lanes`, and `lanes` comes from the registry, so an
    # unreadable registry yields an empty index and manufactures the same false
    # negative as an unread board — a lane picks up work another lane already holds.
    if not _registry_readable(state, "who", f"cannot say whether a lane holds #{args.number}"):
        return 2
    if args.json:
        print(json.dumps({"number": args.number, "kind": None, "lanes": [], "evidence": {},
                          "freshness": payload_freshness(state)}))
    else:
        print(f"#{args.number}: no lane holds it (no mechanical ownership evidence)")
    return 1


def _conflict_sort_key(kv: tuple[str, list[str]]) -> tuple[int, int, str]:
    """Numeric PR numbers first, in order; a malformed key sorts last, never crashes."""
    try:
        return (0, int(kv[0]), "")
    except (TypeError, ValueError):
        return (1, 0, str(kv[0]))


def cmd_conflicts(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    # An empty conflict list is an ALL-CLEAR; it must not be manufactured by a board
    # that could not be read (#7957 H5).
    if not _board_readable(state, "conflicts", "cannot rule out ownership conflicts"):
        return 2
    # ...and the ROSTER behind them: `conflict_claimants` is derived from `lanes`, so
    # an unreadable registry reports "no ownership conflicts" — an all-clear from a
    # read that never happened (#8246 / PR #8594 review).
    if not _registry_readable(state, "conflicts", "cannot rule out ownership conflicts"):
        return 2
    cmap = conflict_map(state)
    live = lane_live_map(state)
    if args.json:
        # liveness must survive --json: an automation consumer reads this, and
        # "which claimant is dead" is the decision the channel exists for.
        # ⛔ No `freshness` key in THIS payload: it is a PR-number -> claimants MAP, and
        # a non-numeric key would break any consumer reading a key as a PR number. The
        # age for this command travels on stderr (every run prints it).
        print(json.dumps({
            num: {"claimants": who, "live": {lbl: live.get(lbl) for lbl in who}}
            for num, who in cmap.items()
        }, indent=2))
        return 0
    if not cmap:
        print("no ownership conflicts")
        return 0
    for num, who in sorted(cmap.items(), key=_conflict_sort_key):
        if who:
            print(f"  PR {num}: " + ", ".join(f"{lbl}{_live_tag(live.get(lbl))}" for lbl in who))
        else:
            print(f"  PR {num}: claimants unknown (stale fleet-state; re-run build)")
    return 0


def _stale_registry_match(state: Mapping[str, Any], needle: str) -> dict[str, Any] | None:
    """The `registry-stale` orphan row matching `needle` (lane label or workspace).

    Tolerates a state written before #8845 (no such rows) and a malformed
    `orphans` value: an unreadable entry is not a match.
    """
    n = needle.lower().strip()
    orphans = state.get("orphans")
    for o in orphans if isinstance(orphans, (list, tuple)) else ():
        if not isinstance(o, dict) or o.get("kind") != "registry-stale":
            continue
        if str(o.get("lane") or "").lower() == n or str(o.get("workspace") or "").lower() == n:
            return o
    return None


def _find_lane(state: Mapping[str, Any], needle: str) -> dict[str, Any] | None:
    n = needle.lower().strip()
    for lane_ in state["lanes"]:
        if lane_["identity"]["lane"].lower() == n or lane_["identity"]["workspace"].lower() == n:
            return lane_
    for lane_ in state["lanes"]:
        if n in lane_["identity"]["lane"].lower():
            return lane_
    return None


def cmd_lane(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    # A NEGATIVE LANE LOOKUP IS ROSTER-DERIVED, so an unreadable roster must refuse
    # BEFORE the search. `state["lanes"]` is built solely from the registry rows, so an
    # unread roster enumerates zero lanes and `no lane matching ...` is an existence
    # verdict manufactured from a read that never happened (PR #8594 review P2).
    if not _registry_readable(
        state, "lane", f"cannot say whether a lane named {args.lane!r} exists"
    ):
        return 2
    lane = _find_lane(state, args.lane)
    if lane is None:
        # A stale roster row is NOT a lane (#8845) — say so, rather than manufacturing
        # the same "no lane matching" answer an unknown name gets. The two are
        # different facts: one name EXISTS in the roster and has nothing behind it.
        stale = _stale_registry_match(state, args.lane)
        if stale:
            # `workspace` is read through `.get`: `_stale_registry_match` tolerates a
            # malformed entry (a hand-edited or corrupt cached state), so its only
            # consumer must not be the one place that raises `KeyError` on one.
            stale_ws = str(stale.get("workspace") or "")
            where = f"workspace {stale_ws[:8]}…" if stale_ws else "the workspace it names"
            print(
                f"{args.lane!r} is a STALE REGISTRY ROW, not a lane: {where} "
                "is absent from the live cmux list and the row "
                "binds no pane binding or session record with a LIVE pid of its own "
                "(#8845) — delete the roster row; "
                "`python tools/fleet_state.py orphans` lists it",
                file=sys.stderr,
            )
            return 1
        print(f"no lane matching {args.lane!r}", file=sys.stderr)
        return 1
    # `lane`'s identity/session/liveness sections are lane-derived and valid whatever
    # the board says; its PR sections are not. Warn, never suppress (#7957 H5).
    gp = github_provenance(state)
    if not gp["measured"]:
        print(
            f"lane: GitHub is UNREADABLE ({gp['error'] or 'cause unknown'}) — the work/CI "
            "sections of this lane are incomplete (#7957 H5)",
            file=sys.stderr,
        )
    # Additive: `lane --json` was already an object, so the age of the answer rides
    # along without changing its shape (#7957 H1 for the machine channel).
    payload = dict(lane)
    payload["freshness"] = payload_freshness(state)
    print(json.dumps(payload, indent=2, default=str) if args.json else _render_lane(lane))
    return 0


def _render_lane(lane: Mapping[str, Any]) -> str:
    i, s, lv, c = lane["identity"], lane["session"], lane["liveness"], lane["claim"]
    lines = [
        f"lane      {i['lane']}",
        f"  workspace {i['workspace']}  worktree {i['worktree']}",
        f"  branch    {i['branch']}  HEAD {i['head_sha'][:12]}",
        f"  session   {s['sid'] or '(UNKNOWN)'}  source={s['source']}  file={s['file'] or '-'}",
        f"            pid={s['pi_pid']} alive={s['pid_alive']} binding_missing={s['binding_missing']}",
        f"  liveness  cpu_delta={lv['cpu_delta_seconds']} over {lv['cpu_sample_seconds']}s "
        f"descendants={lv['descendant_count']} pane_working={lv['pane_working']} "
        f"pane_quiescent={lv['pane_quiescent']} "
        f"pane_stalled_turn={lv.get('pane_stalled_turn')}",
        f"  claims    issues={list(c['issues'])} prs={list(c['prs'])}",
        f"  free      {lane['free']}" + (f"  (not: {'; '.join(lane['not_free_reasons'])})" if lane["not_free_reasons"] else ""),
    ]
    term = lane.get("terminal") or {}
    if term.get("state"):
        lines.append(f"  terminal  {term['state']}")
        lines.append(f"            {term.get('reason', '')}")
        if term.get("resume_command"):
            lines.append(f"            recover: {term['resume_command']}")
    for ev, why in c["evidence"].items():
        lines.append(f"      {ev}: {why}")
    for w in lane["work"]["prs"]:
        lines.append(f"  PR {w['number']}  ci={w['ci']}  review={w['review_binding']}  "
                     f"mergeable={w['mergeable']}/{w['merge_state_status']}")
    return "\n".join(lines)


def cmd_free(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    # #7957 H4/H5: `free` is an INVITATION TO DISPATCH. With the board unread, every
    # `holds N PR(s)` reason disappears and lanes that are in fact working read as idle
    # capacity — the #7158 class. Refuse instead of offering it.
    if not _board_readable(state, "free", "cannot tell a working lane from idle capacity"):
        return 2
    # SECONDLY the ROSTER. The board answers "is this lane busy?"; the registry
    # answers "which lanes EXIST?". An unreadable registry enumerates zero lanes, so
    # this command would report a fully-busy fleet — the same fail-open as an unread
    # board, one source over.
    if not _registry_readable(state, "free", "cannot enumerate the lanes that could take work"):
        return 2
    # A lane record we cannot read is NOT free (fail-closed, the same polarity as every
    # other unmeasured signal in this module) — and a malformed cache degrades rather
    # than crashing the one command whose answer is "send work here".
    lanes = state.get("lanes")
    lanes = lanes if isinstance(lanes, (list, tuple)) else []
    # The guard must cover EVERY field this command dereferences, not just the flag
    # (#7957 H4): a `{"free": true}` stub used to pass the filter and then raise
    # KeyError below, handing the caller a traceback where it needed "no genuinely
    # free lane". A lane record we cannot fully read is not an offerable lane.
    def _offerable(lane_: Any) -> bool:
        if not isinstance(lane_, Mapping) or lane_.get("free") is not True:
            return False
        ident = lane_.get("identity")
        sess = lane_.get("session")
        return (
            isinstance(ident, Mapping)
            and isinstance(ident.get("lane"), str)
            and isinstance(sess, Mapping)
        )

    free = [lane_ for lane_ in lanes if _offerable(lane_)]
    if args.json:
        print(json.dumps({"free": [lane_["identity"]["lane"] for lane_ in free],
                          "freshness": payload_freshness(state),
                          "detail": [{"lane": lane_["identity"]["lane"],
                                      "session_source": lane_["session"].get("source"),
                                      "liveness": lane_.get("liveness")} for lane_ in free]}, indent=2))
    else:
        if not free:
            print("no genuinely free lane (every lane holds work or is demonstrably active)")
        for lane_ in free:
            print(f"  {lane_['identity']['lane']}  session={lane_['session'].get('sid') or 'UNKNOWN'}")
    return 0


def cmd_orphans(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    # The "unclaimed PR" orphan class is derived from the board; "none" from an unread
    # board is a false all-clear (#7957 H5).
    if not _board_readable(state, "orphans", "cannot detect work with no live owner"):
        return 2
    # ...and the ROSTER behind the owners: `orphan_report` decides "no live owner"
    # from `lanes`, so an unreadable registry reports a claimed PR as unowned (or
    # reports `none` from a fixture state) — never an answer (#8246 / PR #8594 review).
    if not _registry_readable(state, "orphans", "cannot detect work with no live owner"):
        return 2
    print(json.dumps(state["orphans"], indent=2, default=str) if args.json else "\n".join(
        f"  [{o['kind']}] {o.get('number', o.get('lane', '?'))}: {o['reason']}" for o in state["orphans"]
    ) or "  none")
    return 0


def cmd_violations(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    # `violations` comes from `resolve_sessions(rows, ...)`, and `rows` is the rows
    # that SURVIVED the #8845 filter — NOT `load_registry()`'s output any more.
    # (It was that before #8845; a comment claiming it still is would tell a reader
    # that no roster row can be missing, which is false.) What survives is:
    # kept ⊆ registry, and EVERY arm leaves at least one row per label, so the label
    # set that `violations` keys on is preserved. The guarantee is not a
    # monotonicity claim — the duplicate arm can REMOVE a violation (two identical
    # rows for one workspace made `resolve_sessions` report a bogus
    # "one session, many lanes" against the label itself), which is the right
    # behaviour but is still a reduction, not an increase. What holds is narrower and
    # is what matters: no arm can HIDE a genuine break. The other inputs are not
    # label-keyed — `disk_by_label` is built AFTER the filter from the surviving rows
    # and is derived per WORKSPACE, `pane_sid_by_ws` is workspace-keyed too, and only
    # `override_sid_by_label` is label-keyed. An unreadable registry still yields zero
    # rows, hence zero claimed sessions, hence zero violations — the old code printed
    # `none` on rc=0, byte-identical to a measured, genuinely-clean roster (PR #8594
    # review P2).
    if not _registry_readable(state, "violations", "cannot detect session-partition breaks"):
        return 2
    print(json.dumps(state["violations"], indent=2) if args.json else "\n".join(
        f"  {v['session'][:8]}… -> {', '.join(v['lanes'])}" for v in state["violations"]
    ) or "  none")
    return 0


def session_file_for(sid: str) -> Path | None:
    """The session file for a FULL session id, or None. A partial id never matches
    (the glob requires the id to be the whole ``<sid>`` segment), so a partial
    binding cannot be written and then re-read as dangling."""
    if not sid or not SESSIONS_DIR.is_dir():
        return None
    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        for f in d.glob(f"*_{sid}.jsonl"):
            return f
    return None


def cmd_bind(args: argparse.Namespace) -> int:
    """Deliverable 2: set a lane's resume binding from its resolved session.

    Only a session whose FILE EXISTS is bound (#7738): a wrong binding is worse
    than none. ``--all`` repairs every live lane whose binding is missing.
    """
    state = _load_or_build(args)
    # `--all` repairs every live lane whose binding is missing: it is derived ENTIRELY from
    # the roster. An unread roster enumerates zero lanes, so the old code printed
    # `nothing to bind` and exited 0 -- "no lane needed repair" from a read that never
    # happened (PR #8594 review P2). A non-`--all` bind takes an explicit workspace/session
    # and does not read the roster, so it is left alone.
    if args.all and not _registry_readable(
        state, "bind", "cannot repair the lanes whose session binding is missing"
    ):
        return 2
    targets: list[tuple[str, str, str, str, Path]] = []
    results: list[dict[str, Any]] = []
    if args.all:
        for lane_ in state["lanes"]:
            s = lane_["session"]
            if not s["binding_missing"]:
                continue
            if (lane_.get("terminal") or {}).get("state"):
                # #7738: a terminal lane cannot be resumed. Binding it would
                # resurrect a ghost, so `--all` spends no cmux call on it.
                continue
            sid = s.get("live_session_id") or s.get("sid")
            # ⛔ Validate the file of the sid we will ACTUALLY bind — not the resolved
            # session's file (they differ exactly when binding_missing is true).
            path = session_file_for(sid) if sid else None
            if not path:
                results.append({
                    "lane": lane_["identity"]["lane"],
                    "workspace": lane_["identity"]["workspace"],
                    "session": sid, "ok": False, "skipped": True,
                    "out": "no session file on disk — refused (#7738)",
                })
                continue
            targets.append((lane_["identity"]["lane"], lane_["identity"]["workspace"], sid,
                            lane_["identity"]["worktree"], path))
    else:
        ws = args.workspace
        sid = args.session
        if not ws or not sid:
            print("bind needs --workspace and --session (or --all)", file=sys.stderr)
            return 2
        found = session_file_for(sid)
        if not found:
            print(f"refusing to bind {sid}: no session file exists on disk (#7738)", file=sys.stderr)
            return 2
        targets.append((args.label or "", ws, sid, args.cwd or "", found))

    pi_bin = args.pi or os.path.expanduser(
        "~/.local/share/pi-node/node-v22.23.2-darwin-arm64/bin/pi"
    )
    for label, ws, sid, cwd, _path in targets:
        argv = ["cmux", "surface", "resume", "set", "--workspace", ws,
                "--kind", "pi", "--checkpoint", sid, "--source", "fleet-state"]
        if cwd:
            argv += ["--cwd", cwd]
        argv += ["--", pi_bin, "--session", sid]
        if args.dry_run:
            results.append({"lane": label, "workspace": ws, "session": sid,
                            "action": " ".join(argv), "dry_run": True})
            continue
        out = _run(argv, timeout=30)
        # Confirmation is POSITIVE, not a substring: "NOT OK" must never read as
        # success (the "send is not delivery" rule).
        results.append({"lane": label, "workspace": ws, "session": sid,
                        "ok": out.strip().startswith("OK"), "out": out.strip()})
    print(json.dumps(results, indent=2) if args.json else "\n".join(
        f"  {'DRY ' if r.get('dry_run') else ('SKIP' if r.get('skipped') else ('OK  ' if r.get('ok') else 'FAIL'))} "
        f"{r.get('lane', '')} {str(r.get('workspace') or '')[:8]} <- {str(r.get('session') or '')[:8]}"
        for r in results
    ) or "  nothing to bind")
    # A bind that was NOT confirmed is not a success (the fleet's "send is not
    # delivery" rule): the caller must be able to tell repaired from failed.
    return 1 if any(not r.get("dry_run") and not r.get("ok") for r in results) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fleet_state.py", description=__doc__.splitlines()[0])
    p.add_argument("--state", default=str(DEFAULT_OUT), help="fleet-state.json path")
    p.add_argument("--repo", default=None, help="owner/name (default: current checkout)")
    p.add_argument("--sample", type=float, default=DEFAULT_SAMPLE_S,
                   help="CPU/pane sample window seconds (0 disables the pane read)")
    p.add_argument("--refresh", action="store_true", help="rebuild even if the state file exists")
    p.add_argument(
        "--allow-stale",
        action="store_true",
        dest="allow_stale",
        help=(
            "read a cache older than 900s as-is instead of rebuilding; the answer is "
            "still rendered STALE(>900s) (#7957)"
        ),
    )
    p.add_argument("--no-ci", action="store_true", help="skip the per-PR CI verdict fetch (offline/fast)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--out", default=None)
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_build)

    w = sub.add_parser("who")
    w.add_argument("number", type=int)
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_who)

    la = sub.add_parser("lane")
    la.add_argument("lane")
    la.add_argument("--json", action="store_true")
    la.set_defaults(func=cmd_lane)

    f = sub.add_parser("free")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_free)

    o = sub.add_parser("orphans")
    o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_orphans)

    v = sub.add_parser("violations")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_violations)

    c = sub.add_parser("conflicts")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_conflicts)

    bi = sub.add_parser("bind")
    bi.add_argument("--all", action="store_true")
    bi.add_argument("-w", "--workspace", default=None)
    bi.add_argument("-s", "--session", default=None)
    bi.add_argument("--label", default="")
    bi.add_argument("--cwd", default="")
    bi.add_argument("--pi", default=None)
    bi.add_argument("--dry-run", action="store_true")
    bi.add_argument("--json", action="store_true")
    bi.set_defaults(func=cmd_bind)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
