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

USAGE
-----
  fleet_state.py build [--out PATH] [--sample S] [--json]
  fleet_state.py who <number> [--json]          # the lane holding it + evidence
  fleet_state.py lane <label|workspace> [--json]
  fleet_state.py free [--json]                  # genuinely idle lanes only
  fleet_state.py orphans [--json]               # work with no live owner
  fleet_state.py violations [--json]            # session-partition breaks
  fleet_state.py bind [--all | -w WS -s SID] [--dry-run] [--json]

The reader commands read ``~/.pi/agent/state/fleet-state.json`` (written by
``build``); pass ``--refresh`` to rebuild first.
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
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"pane_quiescent": self.pane_quiescent}


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
    return reasons


def is_genuinely_free(liveness: Liveness, open_pr_count: int, held_issue_count: int) -> bool:
    return not free_reasons(liveness, open_pr_count, held_issue_count)


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
) -> list[dict[str, Any]]:
    """Open PRs with no live owner — the class that left #7746 unnoticed."""
    out: list[dict[str, Any]] = []
    for pr in open_prs:
        num = pr.get("number")
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
            out.append({
                "kind": "PR",
                "number": num,
                "title": pr.get("title"),
                "branch": pr.get("headRefName"),
                "lane": owner,
                "reason": f"owner lane `{owner}` has no live pi process",
            })
    return out


# ===========================================================================
# live collectors (thin, cached, degrade to empty — never crash)
# ===========================================================================


def _run(cmd: Sequence[str], timeout: int = 60) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        ).stdout
    except Exception:
        return ""


def load_registry(path: Path = REGISTRY) -> list[tuple[str, str, str]]:
    """``label<TAB>workspace<TAB>issue-label`` rows (comments ignored)."""
    rows: list[tuple[str, str, str]] = []
    try:
        for line in path.read_text(errors="ignore").splitlines():
            if not line or line.startswith("#") or "\t" not in line:
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 2 and parts[1]:
                rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    except OSError:
        pass
    return rows


def cmux_workspaces() -> dict[str, dict[str, Any]]:
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
        return {}  # valid JSON that is not an object/list (null, string) -> empty
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
    return result


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


def gh_json(args: Sequence[str], timeout: int = 60) -> list[dict[str, Any]]:
    out = _run(["gh", *args], timeout=timeout)
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def open_prs(repo: str, limit: int = 400) -> list[dict[str, Any]]:
    return gh_json([
        "pr", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
        "--json", "number,title,headRefName,headRefOid,mergeable,mergeStateStatus,url,isDraft,updatedAt",
    ])


def open_issues(repo: str, limit: int = 800) -> list[dict[str, Any]]:
    return gh_json([
        "issue", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
        "--json", "number,title,assignees,labels,updatedAt,url",
    ])


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
    workspaces = cmux_workspaces()
    bindings = pane_bindings()
    live = live_sessions()
    overrides = load_overrides()
    by_sid, by_dir = session_index()
    wts = worktrees(repo_root)
    prs = open_prs(repo)
    issues = open_issues(repo)
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

    rows: list[tuple[str, str]] = [(lbl, ws) for lbl, ws, _ in registry]

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

    for label, ws, issue_label in registry:
        wsinfo = workspaces.get(ws.upper(), {}) or {}
        cwd = wsinfo.get("cwd") or ""
        wt = wts.get(cwd, {}) or {}
        branch = wt.get("branch", "").split("refs/heads/")[-1]
        head = wt.get("head", "")
        res = resolutions.get(label, SessionResolution(None, SOURCE_UNKNOWN, None, "not in registry"))

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
            "free": not reasons,
            "not_free_reasons": reasons,
        })

    # A PR claimed by MORE THAN ONE lane is an ownership CONFLICT, not an assignment:
    # a reused workspace can inherit a previous lane's worktree. Report it and claim it
    # for nobody rather than naming whichever lane happened to come first.
    pr_claims: dict[int, int] = {}
    for lane_ in lanes:
        for n in lane_["claim"]["prs"]:
            pr_claims[n] = pr_claims.get(n, 0) + 1
    conflicts = sorted(n for n, c in pr_claims.items() if c > 1)
    if conflicts:
        for lane_ in lanes:
            lane_["claim"]["prs"] = [n for n in lane_["claim"]["prs"] if n not in conflicts]
        for n in conflicts:
            owner_of_pr.pop(n, None)

    # orphans: open PRs with no live owner (the #7746 class)
    # When a lane has NO session record at all, its liveness is UNMEASURED — never
    # "owner lane X has no live pi" (an unmeasured liveness must not manufacture a
    # dead owner, per-lane as well as fleet-wide).
    lane_live = {
        lane_["identity"]["lane"]: (
            True if not lane_["liveness"]["liveness_measured"]
            else bool(lane_["session"]["pi_pid"] and lane_["session"]["pid_alive"])
        )
        for lane_ in lanes
    }
    orphans = orphan_report(prs, owner_of_pr, lane_live)
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
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo": repo,
        "sample_seconds": sample_s,
        "lanes": lanes,
        "violations": violations,
        "orphans": orphans,
        "conflicts": conflicts,
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
        },
    }


# ===========================================================================
# rendering / CLI
# ===========================================================================


def _load_or_build(args: argparse.Namespace) -> dict[str, Any]:
    if not args.refresh and Path(args.state).exists():
        try:
            return json.loads(Path(args.state).read_text())
        except Exception:
            pass
    state = build_state(
        repo=resolve_repo(args.repo),
        repo_root=resolve_repo_root(args.repo),
        orch_ws=os.environ.get("ORCH_WS", ""),
        sample_s=args.sample,
        do_pane=args.sample > 0,
        ci_verdict=None if getattr(args, "no_ci", False) else make_ci_verdict(),
        live_diff=None if getattr(args, "no_ci", False) else make_live_diff(),
    )
    return state


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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2, default=str))
    if args.json:
        print(json.dumps(state, indent=2, default=str))
    else:
        s = state["summary"]
        print(f"fleet-state -> {out}")
        print(f"  lanes={s['lanes']} bound={s['bound']} unknown_binding={s['unknown_binding']} "
              f"binding_missing_but_live={s['binding_missing_but_live']}")
        print(f"  genuinely_free={s['genuinely_free']} violations={s['violations']} orphans={s['orphans']}")
    return 0


def cmd_who(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    key = str(args.number)
    for kind, label in (("prs", "PR"), ("issues", "issue")):
        entry = state["index"][kind].get(key)
        if entry:
            out = {"number": args.number, "kind": label, "lanes": entry["lanes"], "evidence": entry["evidence"]}
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"#{args.number} ({label}) held by: {', '.join(entry['lanes'])}")
                for lane, why in entry["evidence"].items():
                    print(f"  - {lane}: {why}")
            return 0
    if args.json:
        print(json.dumps({"number": args.number, "kind": None, "lanes": [], "evidence": {}}))
    else:
        print(f"#{args.number}: no lane holds it (no mechanical ownership evidence)")
    return 1


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
    lane = _find_lane(state, args.lane)
    if lane is None:
        print(f"no lane matching {args.lane!r}", file=sys.stderr)
        return 1
    print(json.dumps(lane, indent=2, default=str) if args.json else _render_lane(lane))
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
        f"pane_quiescent={lv['pane_quiescent']}",
        f"  claims    issues={list(c['issues'])} prs={list(c['prs'])}",
        f"  free      {lane['free']}" + (f"  (not: {'; '.join(lane['not_free_reasons'])})" if lane["not_free_reasons"] else ""),
    ]
    for ev, why in c["evidence"].items():
        lines.append(f"      {ev}: {why}")
    for w in lane["work"]["prs"]:
        lines.append(f"  PR {w['number']}  ci={w['ci']}  review={w['review_binding']}  "
                     f"mergeable={w['mergeable']}/{w['merge_state_status']}")
    return "\n".join(lines)


def cmd_free(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    free = [lane_ for lane_ in state["lanes"] if lane_["free"]]
    if args.json:
        print(json.dumps({"free": [lane_["identity"]["lane"] for lane_ in free],
                          "detail": [{"lane": lane_["identity"]["lane"],
                                      "session_source": lane_["session"]["source"],
                                      "liveness": lane_["liveness"]} for lane_ in free]}, indent=2))
    else:
        if not free:
            print("no genuinely free lane (every lane holds work or is demonstrably active)")
        for lane_ in free:
            print(f"  {lane_['identity']['lane']}  session={lane_['session']['sid'] or 'UNKNOWN'}")
    return 0


def cmd_orphans(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
    print(json.dumps(state["orphans"], indent=2, default=str) if args.json else "\n".join(
        f"  [{o['kind']}] {o.get('number', o.get('lane', '?'))}: {o['reason']}" for o in state["orphans"]
    ) or "  none")
    return 0


def cmd_violations(args: argparse.Namespace) -> int:
    state = _load_or_build(args)
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
    targets: list[tuple[str, str, str, str, Path]] = []
    results: list[dict[str, Any]] = []
    if args.all:
        for lane_ in state["lanes"]:
            s = lane_["session"]
            if not s["binding_missing"]:
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
