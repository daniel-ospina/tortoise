#!/usr/bin/env python3
"""next_work — the queue CONSUMER: pick the next unit of work, and name the command that does it.

WHY THIS EXISTS
---------------
Measured 2026-11-04 across four boxes, counting every lane artifact:
``DONE 53 · PARTIAL 45 · BLOCKED 41`` — 38% finish, 29% blocked. Every blocker that
mattered that day was a *harness* defect no lane detected on its own (an
``ubuntu-latest`` label with no runner, a stale ``mergeable`` flag, a lane's
read-only token, push-churn) and **nothing picked its own next unit of work**.
The orchestrator was the queue: the fleet advanced when it dispatched, and
stalled when it did not. This tool moves that judgement out of an agent's
context and into a mechanical answer, so a lane that finishes can ask the
repository what to do next instead of idling until a human session reads it.

WHAT IS A WORK UNIT (mechanically, from records that already exist)
------------------------------------------------------------------
    priority   the issue's own ``P0-blocker`` / ``P1-severe`` LABEL, read from
               ``search/issues`` and paginated to completeness — never a
               page-capped list whose truncation reads as "nothing left".
               Owner ruling: *"beta ships when the p0 and p1 are merged"*, so
               P0 is searched first and never ranked below a P1.
    state      whether an open PR carries the work, and whether that PR could
               land: the repo's OWN queue gate (``.mergify.yml``'s
               ``queue_conditions``, via ``tools/queue_resweep.py`` — not a
               second copy of the gate), plus a head-bound review attestation
               (``record-review.sh --check-carry``) and a real merge test
               (``git merge-tree --write-tree``, never the ``mergeable`` flag).
    claimable  ``refs/claims/*`` — the holder-authored cross-box registry that
               #8175 established as the answer to "who holds this?" — plus the
               LOCAL branches and worktrees whose slug names the issue, which
               are work that exists only on this checkout (#4256). Never
               comment prose, which is a probability only its author can read.
    fold       a constituent of an open fold is NOT a unit; the fold is
               (#10902). Detected from the LABEL the gate itself reads
               (``superseded-by-fold`` on the constituent, ``batch-fold`` on
               the fold) and, for a fold that has not been labelled yet, from
               ``batch_open.py``'s own machine-readable ``fold-constituents:``
               line — never from the ``batch(...): land #1 #2 #3`` title, which
               ``tools/batch_open.py`` refuses for exactly this reason.

WHAT IT EMITS, AND WHY THAT IS THE POINT
----------------------------------------
A ranked list is a REPORT. This tool prints, for the top row, the exact command
a runner would execute: which rail, which PR, which issue. That is the
difference between a consumer and an actuator, and it is why the output has a
``NEXT ACTION:`` block rather than a table alone.

⛔ IT DOES NOT INSTALL A TIMER, AND THE ``--help`` SAYS SO TOO.
cmux authorises by ancestry, so a launchd timer cannot act on a pane. Proposing
one would be the actuator-that-cannot-deliver defect this fleet keeps
re-learning. This tool's output is consumed by whoever holds an allowed
channel; it decides, it never acts.

⛔ READ-ONLY AGAINST GITHUB. No writes, no merges, no comments — ``gh api`` /
``gh pr list`` reads only, and ``record-review.sh --check-carry``, which is
read-only by its own contract.

DETERMINISM — WHAT STOPS TWO LANES TAKING THE SAME PR
----------------------------------------------------
The ordering key is total: ``(priority_rank, position_rank, unit_key)`` where
``unit_key`` is the issue number (or ``fold:<pr>``). Given the same snapshot,
two consumers pick the SAME row — that is the idempotence the brief asks for,
and it is why ``--snapshot-out`` / ``--from`` exist: a decision that cannot be
replayed cannot be proven idempotent. The snapshot itself is the measurement, so
nothing outside it (and no clock) can move the pick.

⛔ NO CLOCK ENTERS THE DECISION AT ALL. A claim lease's polarity — live, expired,
unparseable — is resolved by `claims.sh` when the snapshot is COLLECTED, and the
snapshot records its verdict. So a replay needs no clock seam, and that is
strictly stronger than one: the same snapshot always yields the same decision,
and there is no `--now` flag to be forgotten or to mask a nondeterminism.

⛔ THE ONE DELIBERATE DEPARTURE FROM THE QUEUE LITERATURE: NO AGING.
The external research (Postgres ``FOR UPDATE SKIP LOCKED`` queues, lease/fence
designs, OS priority scheduling) converges on *aging* — raising a starving
job's effective priority — to stop strict priority from starving low-priority
work forever. We do not do it.
**OVERRIDES:** aging/anti-starvation reordering — the owner's ruling is that
beta ships when the P0 and P1 set is merged, so a P1 that is ready must never
outrank a P0 that is not. Starvation of the P2 backlog is the *intended* shape
of a burn-down, not a defect to correct. A lane that "improves" this by letting
readiness outrank priority has reversed a product decision, silently.

Usage
    uv run python tools/next_work.py                     # collect live, rank, name the next command
    uv run python tools/next_work.py --json              # the same decision, as data
    uv run python tools/next_work.py --from snap.json    # replay a snapshot (no network)
    uv run python tools/next_work.py --snapshot-out s.json --top 5

Exit codes
    0  the queue was fully read and a next action was decided
    1  the queue was read but NO unit is pickable (every one is held, is a fold
       constituent, or the queue is empty) — there is no action, and that is an
       answer. Held and superseded rows are filtered out of the pick, so when the
       RANK LEADER is held the next pickable row is simply the `TOP` — and the
       `RUNNABLE` line appears only when the top row itself has nothing to run
       (its head is in a runner queue), naming the first unit that does
    2  INCOMPLETE: a surface the decision depends on could not be read — the
       queue gate (``.mergify.yml``, at collection OR missing from a replayed
       snapshot), the claim registry (``claims.sh`` absent, its snapshot header
       failed, or ANY key answering ``claim-unreadable`` — a per-key abstention
       is INCOMPLETE, not a note beside a pick), the attestation TOOL (``record-review.sh`` absent — not
       a per-PR ``norecord``/``unreadable`` verdict, which is a real answer and
       lands the row at ``NEEDS-REVIEW``), the issue search, or the PR list.
       "Could not check" is never reported as "free" — the same posture
       ``collision_preflight.py`` (exit 2) and ``who-is-on`` (exit 2) take.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/next_work.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/next_work.py`"
    )

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The repo root, prepended to sys.path so `tools.queue_resweep` is importable when
#: this file is run BY PATH (`python tools/next_work.py`) as a lane will run it —
#: not only as `python -m tools.next_work`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── the repo's own vocabulary, imported rather than restated ─────────────────
# `queue_resweep` owns the ENTRY GATE: it parses `.mergify.yml`, resolves the
# newest attempt per check name, and names every unmet condition. A second
# implementation here would be a second definition of "ready", which is the
# defect class this fleet pays for repeatedly (two readers of one gate drifting
# apart). So the gate is imported, not copied.
from tools.queue_resweep import (  # noqa: E402 — the sys.path insert above must run first
    ConfigError,
    GhCli,
    GhError,
    QueueConfig,
    load_config_text,
    parse_config,
    unmet_conditions,
)

#: Priority order. The owner's ruling is that the P0/P1 set is what ships, so a
#: P0 is never ranked below a P1 — see the OVERRIDES note in the module docstring.
PRIORITIES = ("P0-blocker", "P1-severe")
PRIORITY_RANK = {label: i for i, label in enumerate(PRIORITIES)}
#: A fold with no constituent issue is still a unit, but it is an aggregate: it
#: sorts after every P0/P1 issue unit rather than inventing a priority for it.
FOLD_PRIORITY = "fold"

#: Positions, cheapest path to a merge first. `READY-TO-LAND` then
#: `NEEDS-REVIEW` (a review cycle, then land), `NEEDS-TEST` (wait for CI),
#: `NEEDS-FIX` (someone must change code), `NO-PR` (someone must start).
POSITIONS = ("READY-TO-LAND", "NEEDS-REVIEW", "NEEDS-TEST", "NEEDS-FIX", "NO-PR")
POSITION_RANK = {name: i for i, name in enumerate(POSITIONS)}
#: Rendered reasons that are NOT positions: a held unit and a superseded one are
#: answers about the unit, not stages of it.
REASON_CLAIMED = "CLAIMED"
REASON_SUPERSEDED = "SUPERSEDED-BY-FOLD"

#: The constituent label `.mergify.yml` names in `-label=superseded-by-fold`, and
#: the fold label `priority_rules` reads. Both are the config's own vocabulary —
#: `tests/test_next_work.py` asserts they still match `.mergify.yml`, so a rename
#: in one place cannot silently make this reader blind.
CONSTITUENT_LABEL = "superseded-by-fold"
FOLD_LABEL = "batch-fold"

#: `tools/batch_open.py`'s explicit machine-readable constituent line. It is DATA
#: (a declared list), not prose: that tool states why the fold's TITLE must never
#: be parsed for it ("Deriving the close list from that prose would close a PR the
#: fold never carried").
FOLD_LINE_RE = re.compile(r"^\s*fold-constituents:\s*([0-9][0-9,\s]*)", re.MULTILINE | re.IGNORECASE)

#: `claims.sh`'s own default (`CLAIM_NS`). A claim ref is `<CLAIM_NS>/<key>`; the
#: key alone addresses nothing.
CLAIM_NS = os.environ.get("CLAIM_NS") or "refs/claims"

#: A date is not an issue number. `batch-2026-11-03` must not claim issue 2026.
_DATE_SPAN_RE = re.compile(r"(?<![0-9])(?:19|20)[0-9]{2}-[0-9]{1,2}-[0-9]{1,2}(?![0-9])")
#: A hash fragment is not an issue number: `fix-10704-bc80c635d` must not claim 80c635d.
_HEX_SPAN_RE = re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{7,}(?![0-9a-zA-Z])")

GH_TIMEOUT = 90.0
GIT_TIMEOUT = 60.0


# ── the snapshot: everything the decision is allowed to read ─────────────────
@dataclass
class Snapshot:
    """The measured input. The decision is a pure function of THIS — no clock."""

    repo: str = ""
    base: str = "main"
    config_source: str = ""
    #: THE PARSED GATE, not just its file name. A replay that re-read
    #: `origin/main:.mergify.yml` from the process cwd is not a replay: measured
    #: 2026-11-04 by a verifier — the same snapshot picked #10602 from the repo
    #: root and #10017 from `/tmp`, because `git show` had no checkout to read.
    #: Two consumers on one snapshot, two different rows: the duplicate pick this
    #: tool exists to prevent. `None` means the gate could not be read at
    #: collection; a replay of such a snapshot must EXIT 2, never fall open.
    gate: dict[str, Any] | None = None
    collected_at: int = 0
    #: open P0/P1 issues: {number, title, priority}
    issues: list[dict[str, Any]] = field(default_factory=list)
    #: open PRs: {number, head, draft, labels, closing_issues, title, fold_line, refs_issues}
    pulls: list[dict[str, Any]] = field(default_factory=list)
    #: head sha -> newest-per-name check runs are derived from the raw list
    check_runs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: pr number (as str) -> {"verdict": ..., "rc": ...}
    attestation: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: claim ref -> {"verdict": ..., "owner": ...}
    claims: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: pr number (as str) -> {"clean": bool|None, "why": str}
    merge_tree: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: local branches/worktrees whose slug names an issue: "issue:<n>" -> [descriptions]
    local_holders: dict[str, list[str]] = field(default_factory=dict)
    #: surfaces that could not be read. A non-empty list makes the run INCOMPLETE
    #: (exit 2) unless the caller explicitly accepts an advisory degradation.
    unreadable: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "base": self.base,
            "config_source": self.config_source,
            "gate": self.gate,
            "collected_at": self.collected_at,
            "issues": self.issues,
            "pulls": self.pulls,
            "check_runs": self.check_runs,
            "attestation": self.attestation,
            "claims": self.claims,
            "merge_tree": self.merge_tree,
            "local_holders": self.local_holders,
            "unreadable": self.unreadable,
        }

    def normalize(self) -> Snapshot:
        """Fold every abstention into `unreadable`, so ONE field decides refusal.

        ⛔ A SNAPSHOT CAN CARRY THE ABSTENTION WITHOUT THE SENTENCE. `refused` was
        derived only from `unreadable`, and `_claim_verdicts` is what appends the
        companion line — so a replayed or hand-written snapshot whose claims map
        held `claim-unreadable` rendered a row saying "claim registry UNREADABLE"
        while `NEXT ACTION` said "every measured unit is held or superseded" and
        the exit code was 1, not 2. Measured by a verifier on this change. The
        verdict map is the fact; this makes the prose follow it, everywhere.
        """
        abstained = sorted(
            ref for ref, entry in (self.claims or {}).items() if _abstention_reason(entry)
        )
        if abstained and not any("claim-unreadable" in u for u in self.unreadable):
            self.unreadable.append(
                f"claims: {len(abstained)} key(s) answered claim-unreadable — an abstention, "
                "never freedom"
            )
        return self

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Snapshot:
        known = {
            "repo", "base", "config_source", "gate", "collected_at", "issues", "pulls",
            "check_runs", "attestation", "claims", "merge_tree", "local_holders",
            "unreadable",
        }
        snap = cls(**{k: v for k, v in raw.items() if k in known})
        # JSON object keys are strings; the decision code looks PRs up by str(pr)
        # anyway, but normalise so a hand-written snapshot behaves identically.
        snap.attestation = {str(k): v for k, v in (snap.attestation or {}).items()}
        snap.merge_tree = {str(k): v for k, v in (snap.merge_tree or {}).items()}
        return snap.normalize()


# ── the decision ────────────────────────────────────────────────────────────
@dataclass
class Unit:
    """One row: a candidate, its measured position, and why it is (not) pickable."""

    key: str                     # "10897" for an issue, "fold:10894" for a fold
    kind: str                    # "issue" | "fold"
    issue: int | None
    fold_pr: int | None
    priority: str
    title: str
    pr: int | None
    head: str | None
    position: str
    reason: str                  # position, CLAIMED, or SUPERSEDED-BY-FOLD
    detail: list[str] = field(default_factory=list)
    holder: str | None = None

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """TOTAL and deterministic: priority, then position, then a stable key.

        The third element exists so two rows can never compare equal — a partial
        order would let two consumers, or two runs, disagree about the top row,
        which is precisely the duplicate-pick this tool prevents.
        """
        p = PRIORITY_RANK.get(self.priority, len(PRIORITY_RANK))
        pos = POSITION_RANK.get(self.position, len(POSITIONS))
        return (p, pos, self.key)

    @property
    def pickable(self) -> bool:
        return self.reason not in (REASON_CLAIMED, REASON_SUPERSEDED)

    @property
    def executable(self) -> bool:
        """Pickable AND there is something to run now.

        `NEEDS-TEST` is a real position but not an action: the head is waiting on
        a runner, and no dispatch changes that. Reporting it as the next action
        would be the actuator-that-cannot-deliver defect — measured 2026-11-04,
        when the CI queue itself was the blocker and every P0 sat in it.
        """
        return self.pickable and self.position != "NEEDS-TEST"


def _labels(pr: dict[str, Any]) -> set[str]:
    return {str((lb or {}).get("name") or "") for lb in (pr.get("labels") or [])}


def _fold_constituents(pr: dict[str, Any]) -> set[int]:
    """The constituents a fold DECLARES, from its machine-readable line only."""
    out: set[int] = set()
    for block in FOLD_LINE_RE.findall(str(pr.get("fold_line") or "")):
        for token in re.split(r"[,\s]+", block.strip()):
            if token.isdigit():
                out.add(int(token))
    return out


def _open_pulls_for_issue(snap: Snapshot, issue: int) -> list[dict[str, Any]]:
    """Every open PR that the repository itself records as naming this issue.

    Two mechanical sources, both written by GitHub, neither parsed from prose
    here: `closingIssuesReferences` (what the PR would CLOSE on merge) and the
    issue's own `cross-referenced` timeline (what it REFERENCES). The gap
    between them is real and measured — #10534 found 18 of 18 PRs naming a P0/P1
    used `Refs`, so a consumer that read only closing keywords would call every
    P0 `NO-PR` and be confidently wrong. The distinction is carried into the
    reason so a row whose only link is a `Refs` says so.
    """
    hits: list[dict[str, Any]] = []
    for pr in snap.pulls:
        if issue in {int(n) for n in (pr.get("closing_issues") or [])}:
            hits.append({**pr, "_link": "closes"})
        elif issue in {int(n) for n in (pr.get("refs_issues") or [])}:
            hits.append({**pr, "_link": "refs"})
    # Newest PR first: the highest number is the most recent attempt, and an
    # older abandoned PR is not the thing to hand a lane.
    return sorted(hits, key=lambda p: int(p["number"]), reverse=True)


def _fold_index(snap: Snapshot) -> dict[int, list[int]]:
    """constituent PR -> the open fold PRs that carry it."""
    fold_of: dict[int, list[int]] = {}
    for pr in snap.pulls:
        labels = _labels(pr)
        nums = _fold_constituents(pr)
        is_fold = FOLD_LABEL in labels or bool(nums)
        if is_fold:
            for c in nums:
                fold_of.setdefault(c, []).append(int(pr["number"]))
        # The label is authoritative on the CONSTITUENT side and works even when
        # the fold has not been labelled yet (#10902 applies it at fold-open).
        if CONSTITUENT_LABEL in labels:
            fold_of.setdefault(int(pr["number"]), [])
    return fold_of


def _abstention_reason(entry: object) -> str | None:
    """Why this claim entry CANNOT BE READ as free, or ``None``. ONE definition.

    ⛔ TWO READERS, ONE RULE — THAT IS THE POINT. `normalize()` decides whether the
    RUN is incomplete and `_claim_of()` decides whether the ROW is free; when they
    disagreed about what "unreadable" means, the same snapshot produced a row
    saying "claim registry UNREADABLE" beside a verdict saying "every measured unit
    is held or superseded", and could degrade to a merge command at exit 0 with a
    second pickable unit present. Both defects were measured on this change, both
    on the `--from` path (a hand-edited or older snapshot), and both came from the
    two readers drifting. A present key with NO verdict is not `claim-absent`:
    `claim-absent` is the one value that means freedom, and silence is not it.
    """
    if not isinstance(entry, dict):
        return "the entry is not a verdict object"
    verdict = str(entry.get("verdict") or "")
    if not verdict:
        return "the entry carries no verdict"
    if verdict == "claim-unreadable":
        return "claim-unreadable"
    return None


def _claim_of(snap: Snapshot, *, issue: int | None, pr: int | None, head: str | None) -> tuple[str | None, bool]:
    """(holder description or None, registry_readable).

    Keys are the registry's own: `issue-<N>` (agent-infra#1850), `pr-<N>`
    (agent-infra#1952) and `<pr>-<sha12>` (content-addressed, rec 1). A verdict
    that is not explicitly `claim-absent` is an ABSTENTION — it does not prove
    freedom, so it must never be read as one.
    """
    # ⛔ THE FULL REF, NOT THE BARE KEY. `claims.sh` indexes
    # `refs/claims/<key>` (claim_snapshot writes `${CLAIM_NS}/${key}`) and its
    # reader matches field 1 EXACTLY, so asking for `pr-10852` answered
    # `claim-absent` for a key that is HELD — and `claim-absent` is the one word
    # this module reads as freedom. Measured by a verifier on this change: PRs
    # #10852 and #10889 were `claim-held` by a live box at that moment and both
    # came out `READY-TO-LAND` with an executable command. The miss is silent,
    # which is exactly why the addressing is spelled out here.
    refs: list[str] = []
    if issue is not None:
        refs.append(f"{CLAIM_NS}/issue-{issue}")
    if pr is not None:
        refs.append(f"{CLAIM_NS}/pr-{pr}")
        if head:
            refs.append(f"{CLAIM_NS}/{pr}-{head[:12]}")
    readable = True
    #: `claims.sh`'s OWN vocabulary (claim_snapshot_verdict), mapped once:
    #:   claim-absent    rc 0  no entry in a read snapshot          -> FREE
    #:   claim-expired   rc 0  the holder's window closed           -> FREE
    #:   claim-orphaned  rc 0  the holder cannot be a live lane     -> FREE
    #:   claim-held      rc 1  a live holder                       -> HOLDER
    #:   claim-unreadable rc 2 the registry could not answer       -> ABSTENTION
    #: An abstention is NOT freedom (the doctrine this module states) and it is
    #: not a silent note either: it blocks the row AND makes the run incomplete.
    FREE = ("claim-absent", "claim-expired", "claim-orphaned")
    for ref in refs:
        if ref not in snap.claims:
            # ABSENT from the snapshot: the registry was read and does not hold
            # this key. That is the ONE case that is genuinely free. (`ref not
            # in`, not `.get() is None` — a PRESENT key whose value is null is a
            # registry that could not answer, not a registry that said "free".)
            continue
        entry = snap.claims[ref]
        verdict = str((entry or {}).get("verdict") or "") if isinstance(entry, dict) else ""
        why = _abstention_reason(entry)
        if why:
            readable = False
            return (f"<claim-unreadable: {ref}> ({why})", readable)
        if verdict in FREE:
            continue
        owner = str(entry.get("owner") or "")
        # `claim-held`, or a word this reader does not know. Unknown is a HOLDER:
        # the fail-closed direction, because the other one duplicates the work.
        return (owner or verdict or ref, readable)
    for desc in snap.local_holders.get(f"issue:{issue}" if issue is not None else "", []):
        return (f"local:{desc}", readable)
    return (None, readable)


def _position_for_pr(
    snap: Snapshot,
    cfg: QueueConfig | None,
    pr: dict[str, Any],
) -> tuple[str, list[str]]:
    """The position of one PR — the repo's own gate first, then attestation, then merge."""
    detail: list[str] = []
    head = str(pr.get("head") or "")
    if cfg is None:
        # No gate read means no position can be claimed. Everything downstream
        # would be a guess, so it is reported as an unmet surface, not a state.
        return ("NEEDS-TEST", ["queue gate unreadable — position cannot be asserted"])

    runs = snap.check_runs.get(head) or []
    unmet = unmet_conditions(pr, runs, cfg)
    for cond in unmet:
        if cond.startswith(f"-label={CONSTITUENT_LABEL}"):
            detail.append(f"constituent: {cond}")
            continue
        detail.append(cond)

    blocking = [c for c in unmet if not c.startswith(f"-label={CONSTITUENT_LABEL}")]
    # ⛔ THREE BUCKETS, NOT TWO. `unmet_conditions` also emits NON-CHECK refusals —
    # `draft`, `base=main (actual release/1.x)`, `label=X: absent`. The first
    # version sorted only `completed/…` and `in flight`, so everything else fell
    # through to READY-TO-LAND: a DRAFT PR, or one based on another branch, was
    # the tool's BEST position and was handed an executable command — a PR the
    # queue will refuse, advertised as the closest to done. Anything structural
    # the gate names is work on the PR, so it is NEEDS-FIX with the reason kept.
    fails = [c for c in blocking if "completed/" in c and "completed/success" not in c]
    in_flight = [c for c in blocking if "in flight" in c or "no check-run" in c]
    structural = [c for c in blocking if c not in fails and c not in in_flight]

    if fails or structural:
        pos = "NEEDS-FIX"
    elif in_flight:
        pos = "NEEDS-TEST"
    else:
        pos = "READY-TO-LAND"

    mt = snap.merge_tree.get(str(pr["number"])) or {}
    clean = mt.get("clean")
    if clean is False:
        pos = "NEEDS-FIX"
        detail.append(f"merge-tree: {mt.get('why') or 'conflict against base'}")
    elif clean is None:
        # ⛔ ABSTENTION, HANDLED LIKE EVERY OTHER ABSTENTION. This used to append
        # a note and leave the position alone, so an unmeasured tree could still
        # be READY-TO-LAND with a command — while the neighbouring unreadable
        # attestation was correctly downgraded. Two abstentions, opposite
        # handling: a merge that was never tested read as the safest row.
        if pos == "READY-TO-LAND":
            pos = "NEEDS-TEST"
        detail.append(
            f"merge-tree: UNMEASURED ({mt.get('why') or 'head not available locally'}) "
            "— cannot be shown to merge, so it is not landable"
        )

    if pos == "READY-TO-LAND":
        att = snap.attestation.get(str(pr["number"])) or {}
        verdict = str(att.get("verdict") or "")
        if verdict in ("head", "carry"):
            detail.append(f"attestation: {verdict}")
        elif verdict == "carry-patch":
            pos = "NEEDS-REVIEW"
            detail.append("attestation: carry-patch — re-record at this head (no review cycle owed)")
        elif verdict in ("fresh", "norecord", "nodigest"):
            pos = "NEEDS-REVIEW"
            detail.append(f"attestation: {verdict} — a head-bound review is owed")
        else:
            # unreadable, or the recorder is absent. Neither authorises a merge.
            pos = "NEEDS-REVIEW"
            detail.append(
                "attestation: UNREADABLE "
                f"({att.get('why') or verdict or 'record-review.sh --check-carry gave no verdict'}) "
                "— abstention, not a clean record"
            )
    return (pos, detail)


def _priorities_of_prs(snap: Snapshot, prs: Iterable[int]) -> list[str]:
    """The priorities of the issues those PRs carry, via the repository's own links.

    Constituent numbers are PRs (`batch_open.py`'s vocabulary), so a fold's
    priority cannot be read off them directly: it inherits from the ISSUES those
    PRs close or reference. A fold whose constituents name no P0/P1 issue
    inherits nothing and sorts after them, rather than inventing a priority.
    """
    wanted = set(prs)
    issues_in: set[int] = set()
    for pr in snap.pulls:
        if int(pr["number"]) not in wanted:
            continue
        issues_in |= {int(n) for n in (pr.get("closing_issues") or [])}
        issues_in |= {int(n) for n in (pr.get("refs_issues") or [])}
    return [str(i.get("priority") or "") for i in snap.issues if int(i["number"]) in issues_in]


def build_units(snap: Snapshot, cfg: QueueConfig | None) -> list[Unit]:
    """Turn the snapshot into ranked rows. Pure: the same snapshot always ranks the same."""
    fold_of = _fold_index(snap)
    units: list[Unit] = []

    # ── issue units ────────────────────────────────────────────────────────
    for iss in snap.issues:
        number = int(iss["number"])
        priority = str(iss.get("priority") or "")
        pulls = _open_pulls_for_issue(snap, number)
        pr = pulls[0] if pulls else None
        if pr is None:
            holder, readable = _claim_of(snap, issue=number, pr=None, head=None)
            u = Unit(
                key=str(number), kind="issue", issue=number, fold_pr=None,
                priority=priority, title=str(iss.get("title") or ""),
                pr=None, head=None, position="NO-PR",
                reason=REASON_CLAIMED if holder else "NO-PR",
                detail=[f"no open PR closes or references #{number}"],
                holder=holder,
            )
            if not readable:
                u.detail.append("claim registry UNREADABLE for this unit")
            units.append(u)
            continue

        pr_number = int(pr["number"])
        folds = fold_of.get(pr_number) or []
        position, detail = _position_for_pr(snap, cfg, pr)
        if pr.get("_link") == "refs":
            detail.append("link: REFS only — merging this PR closes nothing (#10534)")
        # One PR lands several issues (#10726 closes 7 of them). The PR is the
        # atomic merge artifact, so the rows are not separate work — they are
        # separate ISSUES sharing one unit. Naming the sharer keeps a reader from
        # dispatching twice for what is one action.
        sharers = [
            int(other["number"])
            for other in snap.issues
            if int(other["number"]) != number
            and pr_number
            in {int(o["number"]) for o in _open_pulls_for_issue(snap, int(other["number"]))}
        ]
        if sharers:
            # The MINIMUM sharer, not the first one the input happened to list:
            # a scan that depends on iteration order makes the rendered output
            # differ between two runs on the same data (measured: five rows
            # differed under a shuffled snapshot while the PICK never moved).
            detail.append(
                f"shares PR #{pr_number} with #{min(sharers)} — one unit, not two"
            )
        holder, readable = _claim_of(snap, issue=number, pr=pr_number, head=str(pr.get("head") or ""))
        superseded = bool(folds) or CONSTITUENT_LABEL in _labels(pr)
        if superseded:
            reason = REASON_SUPERSEDED
            if folds:
                detail.append(f"constituent of open fold #{folds[0]} — the fold is the unit")
            else:
                detail.append(f"carries `{CONSTITUENT_LABEL}` with no open fold — the fold is missing")
        elif holder:
            reason = REASON_CLAIMED
        else:
            reason = position
        u = Unit(
            key=str(number), kind="issue", issue=number, fold_pr=folds[0] if folds else None,
            priority=priority, title=str(iss.get("title") or ""),
            pr=pr_number, head=str(pr.get("head") or "") or None,
            position=position, reason=reason, detail=detail, holder=holder,
        )
        if not readable:
            u.detail.append("claim registry UNREADABLE — this row's freedom is not provable")
        units.append(u)

    # ── fold units ─────────────────────────────────────────────────────────
    for pr in snap.pulls:
        labels = _labels(pr)
        nums = _fold_constituents(pr)
        if FOLD_LABEL not in labels and not nums:
            continue
        pr_number = int(pr["number"])
        inherited = [
            PRIORITY_RANK[p] for p in _priorities_of_prs(snap, nums) if p in PRIORITY_RANK
        ]
        priority = PRIORITIES[min(inherited)] if inherited else FOLD_PRIORITY
        position, detail = _position_for_pr(snap, cfg, pr)
        holder, readable = _claim_of(snap, issue=None, pr=pr_number, head=str(pr.get("head") or ""))
        if FOLD_LABEL not in labels:
            detail.append(
                f"fold is UNLABELLED (`{FOLD_LABEL}` absent) — #10909's CI priority and the "
                "queue's own fold ordering do not see it"
            )
        u = Unit(
            key=f"fold:{pr_number}", kind="fold", issue=None, fold_pr=pr_number,
            priority=priority, title=str(pr.get("title") or ""),
            pr=pr_number, head=str(pr.get("head") or "") or None,
            position=position, reason=REASON_CLAIMED if holder else position,
            detail=detail, holder=holder,
        )
        if not readable:
            u.detail.append("claim registry UNREADABLE — this row's freedom is not provable")
        units.append(u)

    units.sort(key=lambda u: u.sort_key)
    return units


def _title_looks_like_fold_without_evidence(snap: Snapshot) -> list[int]:
    """Folds that only their TITLE says are folds — a FLAG, never a decision.

    Measured 2026-11-04 on `daniel-ospina/tortoise`: #10790 (`batch(10046,10487,
    10605,10626): fold 7 reviewed PRs…`), #10726 (`batch: fold 32 open tortoise
    PRs…`), #10894, #10702 are all folds with NO `batch-fold` label and NO
    `fold-constituents:` line — so #10902's entry refusal, which reads the label,
    cannot see any of them. That is a defect in the QUEUE's bookkeeping, not in
    this consumer, and the right response is to report it: promoting a title into
    membership is exactly what `tools/batch_open.py` refuses to do ("Deriving the
    close list from that prose would close a PR the fold never carried").

    So this list is used for ONE thing — telling a human which PRs need the label
    — and it never enters `build_units`. `tests/test_next_work.py` pins that.
    """
    out: list[int] = []
    for pr in snap.pulls:
        labels = _labels(pr)
        if FOLD_LABEL in labels or _fold_constituents(pr):
            continue
        if re.match(r"^\s*(batch|fold)\b", str(pr.get("title") or ""), re.IGNORECASE):
            out.append(int(pr["number"]))
    return sorted(out)


# ── the next command: a consumer that ranks is a report; this makes it an actuator ──
def next_command(unit: Unit, repo: str) -> str | None:
    """The exact command a runner would execute for this row. None when there is none."""
    if not unit.pickable:
        return None
    if unit.kind == "fold":
        target = f"PR #{unit.pr}"
        if unit.position == "READY-TO-LAND":
            return f"bash scripts/orchestrator-harness/merge-one.sh . {repo} {unit.pr} <branch>"
        if unit.position == "NEEDS-REVIEW":
            return (
                f"# review {target} at {unit.head}, then: "
                f"~/.pi/agent/scripts/record-review.sh {unit.pr} {unit.head} clean {repo} "
                "--evidence comment:<review-comment-id>"
            )
        if unit.position == "NEEDS-TEST":
            return f"# {target} is awaiting checks — nothing to dispatch until its head reports"
        return f"bash scripts/orchestrator-harness/fix-one.sh . {repo} {unit.pr} <branch>"
    if unit.position == "NO-PR":
        return f"bash scripts/orchestrator-harness/lane.sh --repo {repo} {unit.issue} lane/{unit.issue}"
    if unit.position == "READY-TO-LAND":
        return f"bash scripts/orchestrator-harness/merge-one.sh . {repo} {unit.pr} <branch>"
    if unit.position == "NEEDS-REVIEW":
        return (
            f"# review PR #{unit.pr} at {unit.head}, then: "
            f"~/.pi/agent/scripts/record-review.sh {unit.pr} {unit.head} clean {repo} "
            "--evidence comment:<review-comment-id>"
        )
    if unit.position == "NEEDS-TEST":
        return f"# PR #{unit.pr} is awaiting checks — nothing to dispatch until its head reports"
    return f"bash scripts/orchestrator-harness/fix-one.sh . {repo} {unit.pr} <branch>"


#: Seconds between GitHub calls. The API throttles per-call and a queue sweep
#: makes ~50 of them, so the sweep is PACED rather than fanned out.
PACE_SECONDS = float(os.environ.get("NEXT_WORK_PACE", "0.2"))


def _pace() -> None:
    if PACE_SECONDS > 0:
        time.sleep(PACE_SECONDS)


def _unreadable(snap: Snapshot, what: str, detail: object) -> None:
    """Record a failed read, CLASSIFIED — the brief asks for the words, not the wall.

    `GH-RATE-LIMITED(<endpoint>)` on a 403, `GH-TIMEOUT(<endpoint>)` when the call
    was cut off, the raw text otherwise. An operator reading `exit 2` needs to know
    which of those it was: a rate limit clears itself in a minute, a wrong token
    does not, and a timeout may be the box. Recording all three as "unreadable"
    makes them indistinguishable at the only moment anyone looks.
    """
    text = str(detail or "")
    low = text.lower()
    spoke = what.split("?")[0]
    if "403" in text or "rate limit" in low or "secondary rate" in low:
        snap.unreadable.append(f"GH-RATE-LIMITED({spoke})")
    elif "timed out" in low or "timeout" in low or "killed" in low or "128+" in low:
        snap.unreadable.append(f"GH-TIMEOUT({spoke})")
    else:
        snap.unreadable.append(f"{what} ({text[:120]})")


# ── collection (the only part that touches the network) ─────────────────────
def _run(
    args: Sequence[str], *, timeout: float = GH_TIMEOUT, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Every git call is pinned to the checkout being judged, never the process cwd."""
    try:
        return subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - environment
        return subprocess.CompletedProcess(list(args), 127, "", str(exc))


def _repo_slug(explicit: str | None) -> str:
    if explicit:
        return explicit
    proc = _run(["git", "remote", "get-url", "origin"], timeout=GIT_TIMEOUT)
    if proc.returncode == 0:
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?\s*$", proc.stdout.strip())
        if m:
            return m.group(1)
    raise SystemExit("next_work: could not resolve owner/name — pass --repo owner/name")


def _search_issues(client: GhCli, repo: str, label: str, snap: Snapshot) -> list[dict[str, Any]]:
    """EVERY open issue with this label — paginated to completeness, never capped.

    GitHub's search API caps a result set at 1000 rows and tells you so with
    `total_count`. A page-capped read of that surface is how a P0 outside the
    window becomes invisible, so the total is compared against what arrived and
    a shortfall is UNREADABLE, never "nothing left".
    """
    rows: list[dict[str, Any]] = []
    page = 1
    total = None
    while True:
        arg = f"search/issues?q=repo:{repo}+is:issue+is:open+label:{label}&per_page=100&page={page}"
        try:
            payload = client._json([arg])
        except GhError as exc:
            _unreadable(snap, f"search:{label}", exc)
            return []
        if total is None:
            if "total_count" not in payload:
                # ⛔ WITHOUT `total_count` THE RECONCILIATION BELOW CANNOT FIRE.
                # The first version defaulted it to 0, broke after page 1, and
                # `len(rows) < total` was then False — a page-capped read
                # reporting "nothing left", the exact failure the docstring forbids.
                snap.unreadable.append(
                    f"search:{label} carried no total_count — a page cannot be reconciled "
                    "against the population, so truncation would be invisible"
                )
            total = int(payload.get("total_count") or 0)
        items = payload.get("items") or []
        for it in items:
            rows.append(
                {
                    "number": int(it["number"]),
                    "title": str(it.get("title") or ""),
                    "priority": label,
                }
            )
        if not items or len(rows) >= total or page >= 10:
            break
        page += 1
    if total is not None and len(rows) < total:
        snap.unreadable.append(f"search:{label} returned {len(rows)} of {total} — page-capped")
    return rows


def _issue_links(client: GhCli, repo: str, issue: int, snap: Snapshot) -> set[int]:
    """PRs GitHub itself records as referencing this issue (`cross-referenced`)."""
    try:
        events = client._get_list(f"repos/{repo}/issues/{issue}/timeline")
    except GhError as exc:
        _unreadable(snap, f"timeline:{issue}", exc)
        return set()
    out: set[int] = set()
    for ev in events:
        if ev.get("event") != "cross-referenced":
            continue
        src = (ev.get("source") or {}).get("issue") or {}
        if src.get("pull_request") and src.get("number"):
            out.add(int(src["number"]))
    return out


def _closing_refs(repo: str, snap: Snapshot) -> dict[int, list[int]]:
    """PR -> the issues it would CLOSE, from GitHub's own parse of the body.

    ⛔ THIS IS NOT AVAILABLE FROM THE REST PR OBJECT (a verifier's finding on this
    change): `repos/{repo}/pulls` carries no `closingIssuesReferences`, so the
    first version's `closing_issues` was empty for **every** pull on live data —
    the `closes` link was dead code and every row printed "this PR closes
    nothing", a consequence asserted from an empty list. `gh pr list --json` is
    GraphQL underneath and DOES carry it, so it is read here once for all PRs.
    A list at the cap is reported, because a truncated list of closures would
    read as "this PR closes nothing".
    """
    limit = 1000
    proc = _run(
        [
            "gh", "pr", "list", "--repo", repo, "--state", "open",
            "--limit", str(limit), "--json", "number,closingIssuesReferences",
        ],
        timeout=GH_TIMEOUT * 2,
    )
    if proc.returncode != 0:
        _unreadable(snap, "closing references", proc.stderr.strip())
        return {}
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        snap.unreadable.append(f"closing references (non-JSON: {exc})")
        return {}
    if len(rows) >= limit:
        snap.unreadable.append(
            f"closing references hit the {limit}-PR window — a closure outside it is invisible"
        )
    out: dict[int, list[int]] = {}
    for row in rows:
        nums = [
            int(ref["number"])
            for ref in (row.get("closingIssuesReferences") or [])
            if isinstance(ref, dict) and ref.get("number")
        ]
        out[int(row["number"])] = nums
    return out


def _issue_numbers_in(slug: str) -> set[int]:
    r"""The issue numbers a branch name or worktree path MENTIONS — and no others.

    ⛔ A BARE DIGIT RUN IS NOT A MENTION. A boundary-exact `[0-9]{3,6}` match
    over the whole string harvested **hex-SHA and date fragments** out of real
    names on this hub: `backup/fix-10704-bc80c635d` claimed issue **635**, and
    `batch-2026-11-03` claimed issue **2026**. Both then read CLAIMED forever —
    the #8175 "an abandoned claim blocks its issue forever" class, re-entered
    through name matching. `collision_preflight.py`'s rule is anchored
    (`fix/#?(\d{3,7})(?![0-9])(?:-|$)`); this keeps the boundary half AND drops
    the two things that are provably not issue numbers: hash runs and dates.
    """
    cleaned = _DATE_SPAN_RE.sub(" ", slug)
    cleaned = _HEX_SPAN_RE.sub(" ", cleaned)
    out: set[int] = set()
    for m in re.finditer(r"(?<![0-9a-zA-Z])([0-9]{3,6})(?![0-9a-zA-Z])", cleaned):
        out.add(int(m.group(1)))
    return out


def _local_holders(repo_root: Path, snap: Snapshot | None = None) -> dict[str, list[str]]:
    """Work that exists only on THIS checkout — branches and worktrees carrying commits.

    `who-is-on --inventory`'s lesson (#4256): a remote-only read reports live
    local work as free. This is the cheap half of that enumeration — refs and the
    worktree list, one call each, no per-worktree `git status` — matched
    boundary-exactly on the issue number, the same rule
    `collision_preflight.py` uses, so `10897` never matches `108970`.

    ⛔ AHEAD-BY ZERO IS NOT A HOLDER. That rule is #8175's finding, restated as a
    filter: a branch left behind with 0 commits ahead and a clean worktree
    produced nothing, and reading its name as a claim would block the issue
    forever — the defect the claim gate itself was fixed for in 2026-10. So a
    branch counts only when it carries commits ahead of the base, which is the
    same annotation `who-is-on` prints beside every holder.
    """
    holders: dict[str, list[str]] = {}
    proc = _run(
        ["git", "for-each-ref", "--format=%(refname:short)\t%(ahead-behind:origin/main)", "refs/heads"],
        timeout=GIT_TIMEOUT,
        cwd=repo_root,
    )
    if proc.returncode != 0 and snap is not None:
        # A git that cannot answer is NOT an empty holder list. The local half of
        # the claim surface would otherwise report "could not check" as "free" —
        # the posture this module forbids everywhere else.
        why = f"local holders: git for-each-ref failed ({(proc.stderr or '').strip()[:80]})"
        if why not in snap.unreadable:
            snap.unreadable.append(why)
    ahead_of: dict[str, int] = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            try:
                ahead_of[parts[0]] = int(parts[1].split()[0])
            except (ValueError, IndexError):
                ahead_of[parts[0]] = -1  # unreadable ahead count: assume it carries work
    proc = _run(["git", "worktree", "list", "--porcelain"], timeout=GIT_TIMEOUT, cwd=repo_root)
    if proc.returncode != 0 and snap is not None:
        why = f"local holders: git worktree list failed ({(proc.stderr or '').strip()[:80]})"
        if why not in snap.unreadable:
            snap.unreadable.append(why)
    worktrees: list[tuple[str, str]] = []
    if proc.returncode == 0:
        path = ""
        branch = ""
        for line in [*proc.stdout.splitlines(), ""]:
            if line.startswith("worktree "):
                path, branch = line[len("worktree "):], ""
            elif line.startswith("branch "):
                branch = line[len("branch "):].replace("refs/heads/", "")
            elif line == "":
                if path:
                    worktrees.append((path, branch))
                path, branch = "", ""

    def note(slug: str, ahead: int) -> None:
        if ahead == 0:
            return  # abandoned: it produced nothing and must not block (#8175)
        for number in _issue_numbers_in(slug):
            holders.setdefault(f"issue:{number}", []).append(slug)

    for ref, _ahead in ahead_of.items():
        note(ref, 1 if _ahead < 0 else _ahead)
    for path, branch in worktrees:
        note(path, ahead_of.get(branch, 1 if branch else -1))
    return {k: sorted(set(v)) for k, v in holders.items()}


def _claim_verdicts(repo_root: Path, refs: Iterable[str], snap: Snapshot) -> None:
    """Ask `claims.sh` — the ONE implementation of the lease/abstention polarity."""
    script = os.environ.get("NEXT_WORK_CLAIMS_SH") or os.environ.get("CLAIMS_SH")
    if not script:
        for cand in (
            repo_root / "scripts" / "orchestrator-harness" / "claims.sh",
            Path(os.environ.get("AGENT_INFRA_PATH", "")) / "scripts" / "orchestrator-harness" / "claims.sh",
        ):
            if cand.is_file():
                script = str(cand)
                break
    if not script or not Path(script).is_file():
        snap.unreadable.append("claims.sh not found — the claim registry cannot be read")
        return
    ref_list = sorted({r for r in refs if r})
    if not ref_list:
        return
    # ONE snapshot fetch, then one verdict per key. `claims.sh` publishes a FAILED
    # header rather than an empty body when the fetch fails, and its reader
    # refuses that header at ANY age — so an unreadable registry can never be
    # mistaken for an empty one here either.
    # ⛔ NOT under `.git`: in a LINKED worktree `.git` is a FILE pointing at the
    # gitdir, so writing there fails, `claim_snapshot` publishes nothing, and the
    # reader then correctly answers "unreadable" for every key — which is how all
    # 96 keys read here on the first live run. `$TMPDIR` is also what the
    # main-worktree-guard freezes for unhatched sessions, so this is the only
    # legal home anyway.
    snap_file = str(Path(tempfile.mkdtemp(prefix="next-work-claims-")) / "snapshot")
    # ⛔ THE VERDICT COMES FROM THE GLOBAL, NEVER FROM `$( )`. `claims.sh`
    # returns rc 1 for `claim-held` and rc 2 for `claim-unreadable`, so the
    # obvious `v=$(claim_snapshot_verdict …) || v=unreadable` OVERWROTE every
    # held verdict with `unreadable` the moment it was correct — and the owner
    # was always empty too, because the globals die with the subshell. Measured
    # by a verifier on this change: a PR held by `box-a:4242` came out
    # `unreadable`, then read as free, and the tool printed a `merge-one.sh`
    # command for it at exit 0 — the exact duplicate dispatch it exists to
    # prevent, in the fail-open direction. The function is called WITHOUT a
    # subshell so `CLAIM_VERDICT` / `CLAIM_SNAPSHOT_OWNER` survive.
    script_body = (
        'set -u; '
        f'. "{script}" >/dev/null 2>&1 || {{ echo "SOURCE-FAILED"; exit 9; }}; '
        f'claim_snapshot "{snap_file}" >/dev/null 2>&1; '
        'while IFS= read -r ref; do '
        f'  claim_snapshot_verdict "{snap_file}" "$ref" >/dev/null 2>&1; '
        '  v="${CLAIM_VERDICT:-}"; [ -n "$v" ] || v="claim-unreadable"; '
        '  printf "%s\\t%s\\t%s\\n" "$ref" "$v" "${CLAIM_SNAPSHOT_OWNER:-}"; '
        'done'
    )
    proc = subprocess.run(
        ["bash", "-c", script_body],
        input="\n".join(ref_list) + "\n",
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=GH_TIMEOUT * 2,
    )
    shutil.rmtree(Path(snap_file).parent, ignore_errors=True)
    if proc.returncode != 0:
        snap.unreadable.append(f"claims.sh verdicts ({proc.stdout.strip()[:120] or proc.returncode})")
        return
    abstained = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] in ("", "SOURCE-FAILED"):
            continue
        ref, verdict = parts[0], parts[1]
        owner = parts[2] if len(parts) > 2 else ""
        snap.claims[ref] = {"verdict": verdict, "owner": owner, "why": verdict}
        if verdict == "claim-unreadable":
            abstained += 1
    if abstained:
        # A registry that answered "I cannot tell" for N keys has NOT cleared
        # them. That is a surface failure, so the run is INCOMPLETE (exit 2) —
        # not a note beside a confident pick.
        snap.unreadable.append(
            f"claims: {abstained} key(s) answered claim-unreadable — an abstention, never freedom"
        )


def _attestation(repo_root: Path, repo: str, pr: int, snap: Snapshot) -> None:
    script = os.environ.get("RECORD_REVIEW_SH")
    if not script:
        for cand in (
            repo_root / "scripts" / "record-review.sh",
            Path(os.environ.get("AGENT_INFRA_PATH", "")) / "scripts" / "record-review.sh",
        ):
            if cand.is_file():
                script = str(cand)
                break
    if not script or not Path(script).is_file():
        snap.attestation[str(pr)] = {"verdict": "unreadable", "rc": None, "why": "record-review.sh not found"}
        why = "record-review.sh not found — the attestation surface cannot be queried"
        if why not in snap.unreadable:
            snap.unreadable.append(why)
        return
    proc = _run(["bash", script, "--check-carry", str(pr), repo], timeout=GH_TIMEOUT)
    verdict = (proc.stdout.split("\t")[0] if proc.stdout.strip() else "").strip() or "unreadable"
    snap.attestation[str(pr)] = {
        "verdict": verdict,
        "rc": proc.returncode,
        "why": (proc.stderr or proc.stdout).strip()[:160],
    }


def _merge_tree(repo_root: Path, base: str, pr: dict[str, Any], snap: Snapshot) -> None:
    """`git merge-tree --write-tree` — a real merge, never the `mergeable` flag.

    The API's `mergeable` is a cached flag that lies for minutes after a push;
    measured in the lane brief as one of the day's real blockers. This asks git
    to build the tree instead. A head that is not present locally is fetched
    into a private namespace, and if that fails the answer is UNMEASURED — an
    abstention, never "clean".
    """
    number = int(pr["number"])
    head = str(pr.get("head") or "")
    if not head:
        snap.merge_tree[str(number)] = {"clean": None, "why": "no head sha"}
        return
    have = _run(["git", "cat-file", "-e", f"{head}^{{commit}}"], timeout=GIT_TIMEOUT, cwd=repo_root)
    if have.returncode != 0:
        fetched = _run(
            ["git", "fetch", "--no-tags", "origin", f"+refs/pull/{number}/head:refs/pi-next-work/pr-{number}"],
            timeout=GIT_TIMEOUT,
            cwd=repo_root,
        )
        if fetched.returncode != 0:
            snap.merge_tree[str(number)] = {
                "clean": None,
                "why": f"head {head[:8]} not available locally and the fetch failed",
            }
            return
        head = f"refs/pi-next-work/pr-{number}"
    merged = _run(["git", "merge-tree", "--write-tree", base, head], timeout=GIT_TIMEOUT, cwd=repo_root)
    if merged.returncode == 0:
        snap.merge_tree[str(number)] = {"clean": True, "why": "merge-tree clean"}
        return
    if merged.returncode == 1:
        conflicts = [
            ln.split("\t", 1)[-1]
            for ln in merged.stdout.splitlines()
            if ln.startswith("CONFLICT") or "\t" in ln
        ]
        snap.merge_tree[str(number)] = {
            "clean": False,
            "why": "conflict: " + (", ".join(c.strip() for c in conflicts[:3]) or "see merge-tree"),
        }
        return
    snap.merge_tree[str(number)] = {
        "clean": None,
        "why": f"merge-tree exited {merged.returncode}: {(merged.stderr or '').strip()[:80]}",
    }


def collect(repo: str, repo_root: Path, *, base: str = "main") -> tuple[Snapshot, QueueConfig | None, str]:
    """Read every surface the decision needs, and record what could not be read."""
    snap = Snapshot(repo=repo, base=base)
    # Provenance only — the decision never reads it. Bounded like every other
    # subprocess here: the first version ran `date` with NO timeout and no error
    # handling, so a host without it raised FileNotFoundError and exited 1 with a
    # traceback instead of the documented 2.
    stamp = _run(["date", "+%s"], timeout=5)
    snap.collected_at = int(stamp.stdout.strip() or 0) if stamp.returncode == 0 and stamp.stdout.strip().isdigit() else 0

    try:
        text, source = load_config_text(None)
        cfg = parse_config(text)
        snap.config_source = source
        snap.base = cfg.base
        snap.gate = {
            "queue_name": cfg.queue_name,
            "base": cfg.base,
            "required_checks": list(cfg.required_checks),
            "required_labels": list(cfg.required_labels),
            "forbidden_labels": list(cfg.forbidden_labels),
        }
    except (ConfigError, OSError) as exc:
        cfg = None
        snap.unreadable.append(f"queue config ({str(exc)[:120]})")

    client = GhCli()

    issues: list[dict[str, Any]] = []
    for label in PRIORITIES:
        issues.extend(_search_issues(client, repo, label, snap))
    snap.issues = sorted(issues, key=lambda i: i["number"])

    try:
        pulls = client.list_pulls(repo, snap.base)
    except GhError as exc:
        pulls = []
        _unreadable(snap, "open pulls", exc)
    snap.pulls = [
        {
            "number": int(p["number"]),
            "head": str(((p.get("head") or {}).get("sha")) or ""),
            "draft": bool(p.get("draft")),
            "base": p.get("base") or {},
            "labels": p.get("labels") or [],
            "closing_issues": [int(x["number"]) for x in (p.get("closing_issues") or []) if x.get("number")],
            "title": str(p.get("title") or ""),
            "fold_line": str(p.get("body") or ""),
            "refs_issues": [],
        }
        for p in pulls
    ]

    # The REFERENCE side of the link, per issue: the PR list's own GraphQL
    # `closingIssuesReferences` sees only closing keywords (#10534), so the
    # timeline is the surface that knows about `Refs`.
    for iss in snap.issues:
        _pace()
        linked = _issue_links(client, repo, int(iss["number"]), snap)
        for pr in snap.pulls:
            if int(pr["number"]) in linked:
                pr["refs_issues"].append(int(iss["number"]))

    heads = {str(pr["head"]) for pr in snap.pulls if pr.get("head")}
    for sha in sorted(heads):
        _pace()
        try:
            snap.check_runs[sha] = client.list_check_runs(repo, sha)
        except GhError as exc:
            _unreadable(snap, f"check-runs:{sha[:8]}", exc)

    closes = _closing_refs(repo, snap)
    for pr in snap.pulls:
        pr["closing_issues"] = closes.get(int(pr["number"]), [])

    snap.local_holders = _local_holders(repo_root, snap)

    # Claims: every key the decision could ask about, in one snapshot fetch.
    refs: list[str] = []
    for iss in snap.issues:
        refs.append(f"{CLAIM_NS}/issue-{iss['number']}")
    for pr in snap.pulls:
        refs.append(f"{CLAIM_NS}/pr-{pr['number']}")
        if pr.get("head"):
            refs.append(f"{CLAIM_NS}/{pr['number']}-{str(pr['head'])[:12]}")
    _claim_verdicts(repo_root, refs, snap)

    # Attestation + merge test only for PRs that could become a unit at all:
    # a PR linked to a P0/P1 issue, or a fold.
    linked_prs = {
        int(pr["number"])
        for pr in snap.pulls
        if (pr.get("closing_issues") or pr.get("refs_issues")) or FOLD_LABEL in _labels(pr)
    }
    for pr in snap.pulls:
        if int(pr["number"]) not in linked_prs:
            continue
        _pace()
        _attestation(repo_root, repo, int(pr["number"]), snap)
        _merge_tree(repo_root, f"origin/{snap.base}", pr, snap)

    return snap.normalize(), cfg, snap.config_source


# ── rendering ───────────────────────────────────────────────────────────────
def render(
    units: list[Unit],
    cfg: QueueConfig | None,
    snap: Snapshot,
    *,
    repo: str,
    top: int,
) -> tuple[str, Unit | None]:
    out: list[str] = []
    out.append(f"next-work — repo {repo} · base {snap.base} · gate {snap.config_source or 'UNREADABLE'}")
    out.append(
        f"read: {len(snap.issues)} open P0/P1 issue(s) · {len(snap.pulls)} open PR(s) · "
        f"{len(snap.check_runs)} head(s) with check-runs · {len(snap.claims)} claim key(s)"
    )
    if snap.unreadable:
        for u in snap.unreadable:
            out.append(f"  ⚠️ UNREADABLE: {u}")
    out.append("")

    header = f"{'#':<6} {'PRI':<11} {'REASON':<19} {'PR':<7} {'POSITION':<14} TITLE"
    out.append(header)
    out.append("-" * len(header))
    for u in units:
        label = u.issue if u.kind == "issue" else f"fold {u.fold_pr}"
        pr_col = str(u.pr) if u.pr else "-"
        out.append(
            f"{label!s:<6} {u.priority:<11} {u.reason:<19} {pr_col:<7} {u.position:<14} {u.title[:58]}"
        )
        for d in u.detail:
            out.append(f"{'':<6}   · {d}")
    out.append("")

    pick = next((u for u in units if u.pickable), None)
    runnable = next((u for u in units if u.executable), None)
    out.append("NEXT ACTION")
    if snap.unreadable:
        out.append(
            "  REFUSED — INCOMPLETE: "
            + "; ".join(snap.unreadable[:3])
            + "\n  A surface the decision depends on could not be read, so no unit's freedom is provable."
        )
    elif pick is None:
        out.append("  NONE — every measured unit is held or superseded. That is an answer, not an error.")
    else:
        out.append(f"  TOP       {_label(pick)}  ({pick.priority}, {pick.reason}, position {pick.position})")
        out.append(f"            {next_command(pick, repo)}")
        if runnable is not None and runnable.key != pick.key:
            out.append(f"  RUNNABLE  {_label(runnable)}  ({runnable.priority}, {runnable.reason})")
            out.append(f"            {next_command(runnable, repo)}")
            out.append(
                "            ^ the rank leader has nothing to run (its head is waiting on a "
                "runner), so this is the first unit a lane can act on NOW."
            )
        elif runnable is None:
            out.append("  RUNNABLE  none — every pickable unit is waiting on its own checks.")
        out.append("  CONSUME   The output is consumed by whoever holds an allowed channel.")
        out.append("            This tool DECIDES; it never acts, and it installs no timer —")
        out.append("            cmux authorises by ancestry, so a launchd timer cannot act on a pane.")
    out.append("")
    unlabelled_folds = _title_looks_like_fold_without_evidence(snap)
    if unlabelled_folds:
        out.append("QUEUE NOTE — the fold label path is INERT on this queue")
        out.append(
            f"  {len(unlabelled_folds)} open PR(s) batch by TITLE ALONE "
            f"({', '.join('#' + str(n) for n in unlabelled_folds[:8])}"
            f"{' …' if len(unlabelled_folds) > 8 else ''}) and carry neither `{FOLD_LABEL}` nor a "
            "`fold-constituents:` line."
        )
        remedy = (
            "run `uv run python tools/batch_open.py --fold <N> --label-only` to make the "
            "relationship mechanical"
        )
        if snap.unreadable:
            # A refused run must not carry a command — not even an advisory one
            # naming another tool. The remedy is stated, not spelled as a command.
            remedy = "`batch_open.py --fold <N> --label-only` makes the relationship mechanical"
        out.append(
            "  Consequence: #10902's entry refusal reads only the label, so nothing stops a "
            f"constituent landing ahead of its fold. This tool will NOT guess membership from a "
            f"title — {remedy}."
        )
        out.append("")
    pickable = [u for u in units if u.pickable]
    shown = pickable[: max(top, 0)]
    out.append(f"TOP {len(shown)} PICKABLE of {len(units)} unit(s) (rank order, held/superseded excluded)")
    # ⛔ A REFUSED RUN DELIVERS NO COMMAND IN EITHER FORMAT. The `--json` path
    # nulled every `next_command` while this footer still printed them, so a text
    # consumer grepping for a command could find one on a run the tool itself
    # calls REFUSED — the same fail-open, one format over.
    for i, u in enumerate(shown, 1):
        cmd = "" if snap.unreadable else (next_command(u, repo) or "")
        out.append(f"  {i}. {_label(u):<12} {u.priority:<11} {u.reason:<19} {cmd[:96]}".rstrip())
    out.append(
        "ordering key = (priority, position, number) — total, and no clock enters the decision, "
        "so a re-run picks the same row."
    )
    return "\n".join(out), pick


def _label(u: Unit) -> str:
    return f"#{u.issue}" if u.kind == "issue" else f"fold PR #{u.fold_pr}"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="next_work.py",
        description=(
            "Pick the next unit of work from the repository's own records and name the "
            "command that does it. READ-ONLY: no writes, no merges, no comments."
        ),
        epilog=(
            "WHAT CONSUMES THIS: whoever holds an allowed channel — a controller session, a lane "
            "that just finished, a human. Deliberately NOT a timer: cmux authorises by ancestry, so "
            "a launchd job cannot act on a pane, and an actuator that cannot deliver is the defect "
            "this fleet keeps re-learning. The 'NEXT ACTION' block is the actuator half: the exact "
            "command for the top row.\n\n"
            "IDEMPOTENT BY CONSTRUCTION: the ordering key is total — (priority, position, number) — "
            "so two runs on one snapshot pick the same row; that is what stops two lanes taking the "
            "same PR. NO clock enters the decision: a claim lease's polarity is decided by claims.sh "
            "when the snapshot is collected and recorded in it, so a replay is exact with no clock "
            "seam to forget.\n\n"
            "Reads: label listing via search/issues (paginated, never page-capped), the repo's own "
            "queue gate from .mergify.yml (via tools/queue_resweep.py), refs/claims (via "
            "scripts/orchestrator-harness/claims.sh), record-review.sh --check-carry, and "
            "git merge-tree --write-tree. Never the `mergeable` flag; never comment prose."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", help="owner/name (default: derived from origin)")
    p.add_argument("--from", dest="from_file", help="replay a snapshot JSON — no network")
    p.add_argument("--snapshot-out", help="write the collected snapshot here (replayable)")
    p.add_argument("--json", action="store_true", help="emit the decision as JSON")
    p.add_argument("--top", type=int, default=5, help="how many rows the footer summarises")
    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent

    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        snap = Snapshot.from_json(raw)
        # ⛔ THE GATE COMES FROM THE SNAPSHOT. Re-reading it here (the first
        # version ran `load_config_text(None)`, which shells
        # `git show origin/main:.mergify.yml` in the PROCESS CWD) made the replay
        # a different decision depending on where it was run — measured: #10602
        # from the repo root, #10017 from `/tmp` — and it swallowed the failure
        # (exit 0) where the live path fails closed (exit 2). A snapshot with no
        # gate is an INCOMPLETE read, full stop.
        if snap.gate is None:
            cfg = None
            msg = (
                "queue gate: the snapshot carries none — the collection that "
                "produced it could not read .mergify.yml, so no position is provable"
            )
            if msg not in snap.unreadable:
                snap.unreadable.append(msg)
        else:
            cfg = QueueConfig(
                queue_name=str(snap.gate.get("queue_name") or "default"),
                base=str(snap.gate.get("base") or "main"),
                required_checks=tuple(snap.gate.get("required_checks") or ()),
                required_labels=tuple(snap.gate.get("required_labels") or ()),
                forbidden_labels=tuple(snap.gate.get("forbidden_labels") or ()),
            )
            snap.config_source = f"{snap.config_source} (from snapshot)"
        repo = args.repo or snap.repo
    else:
        repo = _repo_slug(args.repo)
        snap, cfg, _ = collect(repo, repo_root, base="main")
        if args.snapshot_out:
            Path(args.snapshot_out).write_text(json.dumps(snap.to_json(), indent=2), encoding="utf-8")
            print(f"snapshot written to {args.snapshot_out}", file=sys.stderr)
    if args.from_file and args.snapshot_out:
        print(
            f"next_work: --snapshot-out ignored on a replay (nothing was collected; "
            f"--from {args.from_file} is already the input)",
            file=sys.stderr,
        )

    units = build_units(snap, cfg)
    text, pick = render(units, cfg, snap, repo=repo, top=args.top)
    # ⛔ AN INCOMPLETE RUN DELIVERS NO PICK — in EITHER format. The text path was
    # refused while the machine path shipped `pick` + `next_command` anyway, so a
    # consumer keyed on `--json` was handed a command for a run the tool itself
    # calls REFUSED. The dangerous instance: `claims.sh` missing leaves the
    # registry empty, which `_claim_of` reads as free for every key.
    refused = bool(snap.unreadable)
    if args.json:
        print(
            json.dumps(
                {
                    "repo": repo,
                    "base": snap.base,
                    "gate": snap.config_source,
                    "unreadable": snap.unreadable,
                    "refused": refused,
                    "units": [
                        {
                            "key": u.key,
                            "kind": u.kind,
                            "issue": u.issue,
                            "fold_pr": u.fold_pr,
                            "priority": u.priority,
                            "pr": u.pr,
                            "head": u.head,
                            "position": u.position,
                            "reason": u.reason,
                            "holder": u.holder,
                            "detail": u.detail,
                            "pickable": u.pickable,
                            "next_command": None if refused else next_command(u, repo),
                        }
                        for u in units
                    ],
                    "pick": None if refused else (pick.key if pick else None),
                    "next_command": None if refused else (next_command(pick, repo) if pick else None),
                },
                indent=2,
            )
        )
    else:
        print(text)

    if snap.unreadable:
        return 2
    if pick is None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
