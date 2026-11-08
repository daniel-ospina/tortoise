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
    population  the fleet's ONE work-unit rule: the ``POOL_JQ`` assignment in
               agent-infra's ``scripts/orchestrator-harness/refresh-candidates.sh``
               (#10951). It is EXTRACTED and DRIVEN with ``jq``, never restated —
               a second copy of this predicate is the defect the issue removes. A
               row it withholds is not a unit of work whatever else is true of it:
               assigned to a human, DECLARING ``standing-constraint``, or a
               CONTAINER by title kind (an epic/decision/design/coord/measure/…
               is where work is TRACKED). An unreadable rule withholds EVERY row
               and refuses the run — an unmeasured eligibility is not a pass.
    claimable  ``refs/claims/*`` — the holder-authored cross-box registry that
               #8175 established as the answer to "who holds this?" — plus the
               LOCAL branches and worktrees whose slug names the issue, which
               are work that exists only on this checkout (#4256). Never
               comment prose, which is a probability only its author can read.
    fold       a constituent of an open fold is NOT a PICKABLE unit; the fold is
               (#10902). The constituent keeps its row — that is how a reader sees
               why it is not offered — and carries ``SUPERSEDED-BY-FOLD`` plus no
               command; the fold is the row that can be picked. Detected from the
               LABEL the gate itself reads (``superseded-by-fold`` on the
               constituent, ``batch-fold`` on the fold) and, for a fold that has
               not been labelled yet, from ``batch_open.py``'s own
               machine-readable ``fold-constituents:`` line — never from the
               ``batch(...): land #1 #2 #3`` title, which ``tools/batch_open.py``
               refuses for exactly this reason.

WHAT IT EMITS, AND WHY THAT IS THE POINT
----------------------------------------
A ranked list is a REPORT. This tool prints, for the top row, the exact command
a runner would execute — which rail, which PR, which issue — or, when the top row
needs a HUMAN (a review, or a gate refusal: another base, a missing required label,
an unreadable base), is waiting on a runner, has an UNMEASURED merge tree, has a
required check that never REPORTED, or is a DRAFT, the ACTION and then the first
lower-ranked unit that DOES have a command.
That is the difference
between a consumer and an actuator, and it is why the output has a
``NEXT ACTION`` block rather than a table alone.

⛔ IT DOES NOT INSTALL A TIMER, AND THE ``--help`` SAYS SO TOO.
cmux authorises by ancestry, so a launchd timer cannot act on a pane. Proposing
one would be the actuator-that-cannot-deliver defect this fleet keeps
re-learning. This tool's output is consumed by whoever holds an allowed
channel; it decides, it never acts.

⛔ READ-ONLY AGAINST GITHUB. No writes, no merges, no comments — ``gh api`` /
``gh pr list`` reads only, and ``record-review.sh --check-carry``, which is
read-only by its own contract.

DETERMINISM — WHAT MAKES A SECOND LANE'S PICK CHECKABLE
-------------------------------------------------------
The ordering key is total: ``(priority_rank, position_rank, unit_key)`` where
``unit_key`` is an ORDER tuple — ``(0, issue)`` for an issue, ``(1, pr)`` for a fold
— so issues sort before folds at equal priority and position, and the comparison is
NUMERIC (a text key would put 100 before 99). Given the same snapshot,
two consumers pick the SAME row — that is the idempotence the brief asks for.
⛔ IT IS NOT MUTUAL EXCLUSION, and saying it was would be a lie: determinism means
two concurrent lanes would take the SAME PR, and this tool writes no claim (it is
read-only by design). What excludes the second lane is `refs/claims`, written by
the lane that acts — which is why the claim surface is read here, why a held row
is never offered, and why an unreadable registry refuses the pick outright — and
it is why ``--snapshot-out`` / ``--from`` exist: a decision that cannot be
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

Environment overrides. The two POPULATION levers are PRINTED in the run's
provenance, so a reader can always tell which one was pulled; the other two are
resolved paths/pacing and are not rendered
    NEXT_WORK_POPULATION=0    DISARMS the fleet's work-unit rule (#10951). No
                              population rule is consulted at all: every row is
                              admitted on its title and labels exactly as before
                              that issue, so an epic, a `design(…)` decision, a
                              `measure(…)` and tortoise#10175 all get a runnable
                              command. Reported as `OFF — …`; never passed for a
                              measurement.
    NEXT_WORK_POOL_JQ_SH=…    the script carrying the `POOL_JQ` assignment, when it
                              is not this checkout's `scripts/` or $AGENT_INFRA_PATH.
    NEXT_WORK_CLAIMS_SH=…     `claims.sh`, when it is not resolvable in-tree.
    NEXT_WORK_PACE=…          the pacing gap between `gh` calls.

Exit codes
    0  the queue was fully read and a next action was decided. When the top row has
       nothing to run — NEEDS-TEST (a runner, a merge tree that could not be measured,
       or a required check that never REPORTED), NEEDS-REVIEW (a human: a review, a
       gate refusal, or a review gate that is red with a bound record), or a DRAFT
       (the author's) — the block prints that row's ``ACTION`` and then the first
       lower-ranked unit that DOES have a command; when NOTHING is runnable it says
       so and names no unit
    1  the queue was read but NO unit is pickable — every one is held, is a fold
       constituent, is a duplicate of a row already offered (a blocked row owns its
       action target, so a duplicate can be the rank leader), is not a unit of work
       under the fleet's population rule (a CONTAINER, a declared
       ``standing-constraint``, already assigned, or unattributable because the
       shipped program's arm shape changed — #10951), or the queue is
       empty. The block prints ``NONE`` and that is an answer, not an error
    2  INCOMPLETE — one of the things the decision depends on could not be read,
       OR the input contradicts itself:
         · a surface — the queue gate (``.mergify.yml``, at collection or missing
           from a replayed snapshot), the claim registry (``claims.sh`` absent, its
           snapshot header failed, or ANY key answering ``claim-unreadable`` — a
           per-key abstention, not a note beside a pick), the attestation TOOL
           (``record-review.sh`` absent — not a per-PR ``norecord``/``unreadable``
           verdict, which is a real answer and lands the row at ``NEEDS-REVIEW``),
           the fleet's work-unit rule (``refresh-candidates.sh`` absent, carrying no
           usable ``POOL_JQ``, or that program refused by ``jq`` — #10951; on a box
           with neither ``scripts/`` nor ``$AGENT_INFRA_PATH`` this fires on every
           run that has at least one candidate row — a zero-row run reports
           ``UNREADABLE — …(no rows)`` and exits 1, because there is nothing to
           withhold — unless ``NEXT_WORK_POPULATION=0`` disarms the rule, by design,
           because a queue whose eligibility was never measured is not a queue that
           passed. ⛔ THAT SWITCH IS THE ONE DOCUMENTED LEVER THAT RESTORES THE
           PRE-#10951 BEHAVIOUR; it is listed under ``Usage`` above),
           the issue search, or the PR list;
         · a SHAPE the door refuses — a decision input, a pull field, an issue
           element, an element of a list, or a value that is the wrong type for
           what it states (see ``_normalise_shapes``);
         · a contradicted TARGET — a replayed snapshot whose ``repo`` disagrees
           with ``--repo``, or no target repo at all.
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
import contextlib
import hashlib
import json
import math
import os
import re
import shlex
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
from tools.batch_open import (  # noqa: E402 — the sys.path insert above must run first
    declared_constituents_line,
    parse_constituents,
)
from tools.queue_resweep import (  # noqa: E402 — the sys.path insert above must run first
    ConfigError,
    GhCli,
    GhError,
    QueueConfig,
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

#: ── THE POPULATION VERDICT (#10951) ─────────────────────────────────────────
#: A row the fleet's own population rule (``POOL_JQ`` in
#: ``refresh-candidates.sh``, in agent-infra) does not count as a unit of work.
#: The three arm names are the SHIPPED program's three top-level ``select``
#: stages, in order; ``POPULATION_ARMS`` is that mapping and nothing else — the
#: PREDICATE is never restated here (see ``_population_verdicts``).
REASON_ASSIGNED = "ASSIGNED"
REASON_DECLARED = "DECLARED-CONSTRAINT"
REASON_CONTAINER = "CONTAINER"
#: The rule withheld the row but its arms could not be ATTRIBUTED (the shipped
#: program's shape changed under us). Withheld, because an unattributable arm is
#: still a withheld row; named, so an internal change cannot pass silently.
REASON_NOT_A_UNIT = "NOT-A-UNIT"
#: The rule could not be READ at all. Every row carries it, because a population
#: that was never measured is not a population that passed — the same
#: ``UNMEASURED`` polarity the merge tree uses, pointed at eligibility.
REASON_POP_UNMEASURED = "POP-UNMEASURED"

#: The reasons `pickable` refuses. ONE tuple, so a new withheld class cannot be
#: added to the picker while its reason is left out of the refusal.
REASONS_WITHHELD = (
    REASON_CLAIMED,
    REASON_SUPERSEDED,
    REASON_ASSIGNED,
    REASON_DECLARED,
    REASON_CONTAINER,
    REASON_NOT_A_UNIT,
    REASON_POP_UNMEASURED,
)

#: The constituent label `.mergify.yml` names in `-label=superseded-by-fold`, and
#: the fold label `priority_rules` reads. Both are the config's own vocabulary —
#: `tests/test_next_work.py` asserts they still match `.mergify.yml`, so a rename
#: in one place cannot silently make this reader blind.
CONSTITUENT_LABEL = "superseded-by-fold"
FOLD_LABEL = "batch-fold"

#: The REQUIRED CHECK whose red means "a review is owed", not "code must change".
#: `.mergify.yml`: "`ai-review-gate` is red by design until a review is recorded,
#: which IS the enforcement". `tests/test_next_work.py` asserts this constant is
#: still named by the live gate, so a rename cannot silently reclassify it.
REVIEW_GATE_CHECKS = frozenset({"ai-review-gate"})

#: ⛔ THE FOLD-LINE GRAMMAR IS NOT DEFINED HERE, ON PURPOSE. Three rounds of
#: hand-matching `tools/batch_open.py` produced three divergences (a `\n` crossed by
#: `\s*`, a `fold-constituents :` that tool accepts and a local regex missed, `\d`
#: vs `[0-9]`), each unsafe in the same direction: being STRICTER than the tool
#: that ACTS leaves a constituent pickable ahead of its fold (#10902), because the
#: acting tool wins. `_fold_constituents` calls that tool's own parser, so there is
#: nothing here to drift — and no regex left for a reader to re-derive one from.

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
    #: The gate's own TEXT. The parsed fields alone cannot be re-validated: a
    #: hand-written snapshot with `required_checks: []` produced "no unmet
    #: conditions", so every PR became READY-TO-LAND and got a command while the
    #: LIVE path exits 2 on the same config (`parse_config` refuses a rule that
    #: names no check). Carrying the text means one parser on both paths.
    gate_text: str = ""
    #: The claim namespace the KEYS were built with. `claims.sh` honours
    #: `CLAIM_NS`, so a process with a different value constructed DIFFERENT refs
    #: than the snapshot holds — every lookup missed, and a miss is the one branch
    #: that reads as freedom. Measured by a reviewer on this change: one snapshot,
    #: one held claim, `CLAIM_NS=refs/claims-other` → CLAIMED became
    #: READY-TO-LAND with an executable command at exit 0. The namespace travels
    #: with the data; the environment cannot move a replay.
    claim_ns: str = ""
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
    #: issue number (as str) -> the population reason that withholds it (#10951).
    #: ABSENT means the shipped rule KEPT the row; a replay carries the verdicts so
    #: a decision is still a pure function of its snapshot.
    population: dict[str, str] = field(default_factory=dict)
    #: Where that verdict came from — the script path and the ``POOL_JQ`` digest,
    #: or the off-switch. Printed so a reader can tell a DEPLOYED copy from a stale
    #: one; the issue's own closure note asks for exactly that before closing.
    population_source: str = ""
    #: TRUE only on a REPLAY of a snapshot that carries no eligibility verdicts at
    #: all (#10951). Distinct from an empty `population` map, which is a real
    #: measurement that withheld nothing. Directly-constructed snapshots (the test
    #: fixtures) leave it False: their author chose the verdicts, and an old
    #: snapshot FILE is the only input that was measured by a version that had no
    #: rule to measure with.
    population_absent: bool = False
    #: surfaces that could not be read. A non-empty list makes the run INCOMPLETE
    #: (exit 2). There is no override: an unreadable surface is never a pick.
    unreadable: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "base": self.base,
            "config_source": self.config_source,
            "gate": self.gate,
            "gate_text": self.gate_text,
            "claim_ns": self.claim_ns,
            "collected_at": self.collected_at,
            "issues": self.issues,
            "pulls": self.pulls,
            "check_runs": self.check_runs,
            "attestation": self.attestation,
            "claims": self.claims,
            "merge_tree": self.merge_tree,
            "local_holders": self.local_holders,
            "population": self.population,
            "population_source": self.population_source,
            "unreadable": self.unreadable,
        }

    def normalize(self) -> Snapshot:
        """Fold the CLAIM-MAP abstentions into `unreadable`, so one field decides refusal.

        Scope: claim-map abstentions only. An unmeasured merge tree and an unreadable
        attestation are POSITIONS (NEEDS-TEST / NEEDS-REVIEW at exit 0/1) by the
        Exit-codes contract, not run-level refusals.

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
            "repo", "base", "config_source", "gate", "gate_text", "claim_ns", "collected_at",
            "issues", "pulls",
            "check_runs", "attestation", "claims", "merge_tree", "local_holders",
            "population", "population_source",
            "unreadable",
        }
        snap = cls(**{k: v for k, v in raw.items() if k in known})
        # ⛔ THE SHAPE CHECK RUNS FIRST — INCLUDING FOR `claim_ns`. It was coerced
        # here first (`str(raw.get(...) or "refs/claims")`), so a falsy WRONG type
        # (`[]`, `{}`, `0`, `false`) was erased to the default before the door could
        # judge it: measured, `claim_ns: []` exited 0 with a runnable merge command.
        # `_normalise_shapes` now owns that field too. The key-coercions below used
        # `(x or {}).items()`, which silently turned a falsy WRONG type (`[]`, `""`)
        # into `{}` — erasing the very evidence the check needs. Measured: with the
        # check after them, `attestation: []` and `merge_tree: []` exited 0.
        _normalise_shapes(raw, snap)
        # JSON object keys are strings; the decision code looks PRs up by str(pr)
        # anyway, but normalise so a hand-written snapshot behaves identically.
        snap.attestation = {str(k): v for k, v in snap.attestation.items()}
        snap.merge_tree = {str(k): v for k, v in snap.merge_tree.items()}
        # ⛔ JUDGED AT THE DOOR, LIKE EVERY OTHER FIELD (#10951 review, follow-up).
        # `"population": null` used to reach `snap.population.items()` and surface as
        # a raw AttributeError inside INCOMPLETE — fail-closed, but as the diagnostic
        # class this door exists to turn into a NAMED refusal.
        raw_population = raw.get("population")
        if "population" not in raw:
            # ABSENT is the separate INCOMPLETE case below — not a wrong type.
            snap.population = {}
        elif not isinstance(raw_population, dict):
            snap.unreadable.append(
                f"population: the snapshot's population is "
                f"{type(raw_population).__name__}, not a map — no row's eligibility is provable"
            )
            snap.population = {}
        else:
            snap.population = {str(k): str(v) for k, v in raw_population.items()}
        # ⛔ A REPLAY MAY NOT FALL OPEN ON A FIELD IT CANNOT PROVE (#10951). The
        # population verdicts are DERIVED data (`collect` fills them by driving the
        # shipped `POOL_JQ`), so a snapshot written before this change carries none
        # — and an empty map would then read as "the rule measured every row and
        # kept all of them", which is the fail-OPEN resolution of an UNMEASURED
        # surface. That is the same defect one level up: an absent measurement
        # granted as a pass. ABSENT is INCOMPLETE (exit 2); an EMPTY map is a real
        # answer from a real measurement and is honoured.
        # …and the field beside it, so `population_source: 5` is a NAMED refusal
        # rather than an AttributeError raised from inside INCOMPLETE.
        if not isinstance(snap.population_source, str):
            snap.unreadable.append(
                f"population_source: the snapshot's population_source is "
                f"{type(snap.population_source).__name__}, not a string"
            )
            snap.population_source = ""
        # ⛔ THE REPLAY SOURCE BELONGS TO A MAP THAT WAS ACTUALLY USABLE (#10951
        # review, F4). Set unconditionally it claimed "verdicts from the snapshot's
        # own map" about a map the door had just REFUSED, and the render's ⚠️ marker
        # only fires for OFF/UNREADABLE/ABSENT — so the text path printed an
        # unwarned provenance line asserting a measurement that did not happen.
        usable_map = "population" in raw and isinstance(raw_population, dict)
        if usable_map and not (snap.population_source or "").strip():
            snap.population_source = "REPLAY — verdicts from the snapshot's own map"
        if "population" not in raw:
            snap.population_absent = True
            snap.population_source = (
                "ABSENT — this snapshot carries no eligibility verdicts; it predates #10951"
            )
            snap.unreadable.append(
                "population: this snapshot carries no eligibility verdicts — a replay cannot "
                "prove any row IS a work unit, so none is offered (re-collect to measure it)"
            )
        # Labels arrive as `[{"name": "x"}]` from GitHub and as `["x"]` from a hand
        # written snapshot. The imported gate (`queue_resweep.unmet_conditions`)
        # reads `.get("name")`, so the coercion has to happen HERE, at the one point
        # every snapshot passes through — not in each reader.
        for pr in snap.pulls or []:
            if not isinstance(pr, dict):
                continue
            pr["labels"] = [
                {"name": lb} if isinstance(lb, str) else lb
                for lb in (pr.get("labels") or [])
                if isinstance(lb, (str, dict))
            ]
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
    #: The PR's head branch — the rails take it as their 4th argument.
    head_ref: str | None = None
    #: A draft is OUTSIDE the queue's reach (`-draft` is a live entry condition) and
    #: is normally a human mid-authoring. It gets an ACTION, never a command: a
    #: fixer pushed at a draft is the duplicate dispatch this tool prevents.
    draft: bool = False
    #: Set when a BLOCKED row (claimed / superseded — it registers first and always)
    #: or an earlier-ranked pickable row already carries this row's action target
    #: (the same PR, or the same issue when there is no PR). Two rows for one
    #: mergeable artifact meant two identical commands: dispatch both and the same
    #: PR is worked twice — the duplicate pick this tool exists to prevent, one
    #: level below `pick`. The row stays, with the reason, and carries no command.
    duplicate_of: str | None = None
    #: TRUE when the fleet's population rule WITHHELD this row (#10951). The
    #: REFUSAL IS BY PRESENCE, not by looking the reason string up in a tuple: the
    #: tuple can only refuse the literals the version that wrote it knew, so a
    #: snapshot carrying a verdict from a NEWER vocabulary (`STANDING-CONSTRAINT-V2`)
    #: would be labelled withheld and handed a runnable command at the same time —
    #: measured. The reason is kept for the reader; the flag is what decides.
    withheld_by_population: bool = False

    @property
    def sort_key(self) -> tuple[int, int, tuple[int, int]]:
        """TOTAL and deterministic: priority, then position, then a stable key.

        The third element exists so two rows can never compare equal — a partial
        order would let two consumers, or two runs, disagree about the top row,
        which is precisely the duplicate-pick this tool prevents.
        """
        p = PRIORITY_RANK.get(self.priority, len(PRIORITY_RANK))
        pos = POSITION_RANK.get(self.position, len(POSITIONS))
        # ⛔ `key` IS TEXT. `str(100) < str(99)`, so a text tie-break picked issue
        # 100 ahead of 99 while the docstring, the epilog and the footer all said
        # the tie-break is the NUMBER. Numeric here; folds sort after issues on
        # the same (priority, position) because their id is a PR, not an issue.
        order = (0, int(self.issue)) if self.kind == "issue" and self.issue else (
            1,
            int(self.fold_pr or 0),
        )
        return (p, pos, order)

    @property
    def action_target(self) -> str:
        """What a command would act on. Two rows with the same target are one action."""
        return f"pr:{self.pr}" if self.pr else f"issue:{self.issue}"

    @property
    def pickable(self) -> bool:
        if self.duplicate_of:
            return False
        # ⛔ A WITHHELD ROW IS REFUSED BY PRESENCE, NOT BY ITS SPELLING (#10951). A
        # withheld row that `pickable` still admits is a row `render` offers a
        # COMMAND for while its own REASON column says it is not a unit — the
        # command-XOR-action invariant this file keeps breaking. And the refusal
        # cannot be a lookup in `REASONS_WITHHELD`: that tuple only knows the
        # literals this version writes, so a replay carrying a verdict from a newer
        # vocabulary was labelled withheld AND handed a command (measured). The
        # tuple stays for the reasons this file itself writes.
        if self.withheld_by_population:
            return False
        return self.reason not in REASONS_WITHHELD

    @property
    def executable(self) -> bool:
        """Pickable AND there is something to run now.

        `NEEDS-TEST` is a real position but not a RUNNABLE action (it still gets an
        ACTION line, never a command): the head is waiting on a
        runner (checks in flight), or the merge tree could not be MEASURED, or a
        required check has never REPORTED (a harness defect), and no dispatch
        changes any of them. Reporting it as the next action
        would be the actuator-that-cannot-deliver defect — measured 2026-11-04,
        when the CI queue itself was the blocker and every P0 sat in it.
        """
        # The positions that have a RUNNABLE rail. NEEDS-REVIEW (a reviewer + an
        # evidence id) and NEEDS-TEST (a runner queue, an unmeasured merge tree, or
        # a check that never reported) have an ACTION, not a command — see
        # `next_action`.
        # ⛔ THE CLASSIFICATION MUST AGREE WITH THE EMITTER. `next_command` also
        # refuses a gate-refused row, so leaving this predicate without that guard
        # made `executable=True` sit beside `next_command is None` — and `render`
        # chose the RUNNABLE slot by `executable`, printing a literal `None` where
        # the command belongs under "this is the first unit a lane can act on NOW".
        return (
            self.pickable
            and not self.draft
            and not any(d.startswith("gate-refusal:") for d in self.detail)
            and self.position in ("READY-TO-LAND", "NEEDS-FIX", "NO-PR")
        )



#: Every decision input that must be a list, and what it means when it is not.
_LIST_FIELDS = ("unreadable", "issues", "pulls")
#: …and every one that must be a map. `gate` is allowed to be None (the collector
#: writes None precisely when it could not read the gate) — see the special case.
_MAP_FIELDS = ("claims", "attestation", "merge_tree", "check_runs", "local_holders")


def _normalise_shapes(raw: dict[str, Any], snap: Snapshot) -> None:
    """Reject a wrong-typed decision input ONCE, for every field, at the one door.

    ⛔ ONE TABLE, NOT A GUARD PER FIELD. Every TOP-LEVEL input and every PULL field
    is answered by `(x or {})` or `(x or [])` somewhere, so a PRESENT-but-wrong-typed
    value that is FALSY (`[]`, `""`, `0`, `null`) is swallowed and the surface reads
    as EMPTY — "nothing is held", "the queue is empty", "nothing was unreadable".
    Six of them were found one review round at a time (`claims`, `unreadable`,
    `pulls`, `issues`, `claim_ns`, then the pull fields and their elements), each
    measured as exit 0 WITH a runnable command from an input on which that surface
    was never consulted. A per-field guard is how that repeats; this is the single
    place those shapes are judged, and a wrong type is INCOMPLETE — "could not check"
    is never "free".

    SCOPE, stated so the claim is exact. Judged here, with a refusal that NAMES the
    field: the fields in `_LIST_FIELDS` / `_MAP_FIELDS`; `gate`; the ENTRY VALUES of
    `local_holders`; `claim_ns`; `repo`/`base`/`config_source`/`collected_at`; each
    pull's `number`, `labels`, `closing_issues`, `refs_issues`, `draft`, `base`,
    `head`, `head_ref`, `title`, `fold_line`; the ELEMENTS of a pull's three list
    fields; each ISSUE element — its `number` (a non-bool positive int),
    `priority` and `title` — and, since #10951, its `labels` and `assignees` plus
    their ELEMENTS (a nameless label is normalised to the empty name and RECORDED,
    so one malformed element cannot kill the rule but does refuse the run).
    `population` and `population_source` are judged in `from_json`, not here: they
    exist only on a REPLAY. Judged by their READERS instead: `gate_text`, and the
    entry VALUES of `attestation`/`merge_tree`/`check_runs` — one that reads as an
    abstention becomes a POSITION (NEEDS-REVIEW / NEEDS-TEST at exit 0/1, never a
    command), and one the reader cannot handle at all exits 2 through the outer frame
    without naming the field.

    A value of the RIGHT type is left exactly as it was: an empty list or map is a
    legitimate measurement ("read, and it held nothing"). ABSENT is tolerated for a
    pull's optional fields (a hand-written snapshot need not carry them) with ONE
    exception: a pull with no usable `number` is refused, because nothing can address
    it. Elements are judged too — a judged container is not a judged element.
    """
    # ⛔ ONE LEVEL DOWN, THE SAME ERASURE. Each pull's own fields are read through
    # `(pr.get(k) or …)`, so a falsy WRONG type is swallowed just like the
    # top-level ones: measured, `labels: 0` on a PR that holds
    # `superseded-by-fold` read as no labels and handed out `merge-one.sh` (#10902);
    # `closing_issues: 0` read as none and produced a NO-PR `dispatch-merge.sh` for
    # work an existing PR carries; `draft: 0` read as not-a-draft; `base: ""`
    # produced a fixer. ABSENT is a legitimate omission (a hand-written snapshot
    # may not carry a field) — PRESENT-and-wrong-typed is not.
    bad_pull_fields: list[str] = []

    def _is_int(value: object) -> bool:
        # ⛔ A BOOLEAN IS AN int IN PYTHON. `isinstance(True, int)` is True, so
        # `number: true` passed the door and the row addressed PR **#1001** while the
        # command merged **#1** — a runnable command aimed at the wrong artifact.
        return isinstance(value, int) and not isinstance(value, bool)

    # The refusal channel first: if IT is not a list the run cannot report anything
    # else, and the falsy cases would silence every refusal appended afterwards.
    if not isinstance(snap.unreadable, list):
        snap.unreadable = [
            f"unreadable: the snapshot's refusal list is {type(snap.unreadable).__name__}, "
            "not a list — nothing it carries can be trusted"
        ]

    # ⛔ THE ISSUE LIST HAS THE SAME ELEMENT RULE. `_is_int` guards a pull's number,
    # but an issue's was delegated to "the outer frame" — and the outer frame raises
    # NOTHING for `true` or `100.9`: `int(True) == 1` and `int(100.9) == 100` both
    # succeed, so `issues=[{"number": true, …}]` produced `dispatch-merge.sh … 1` at
    # exit 0 for an issue the snapshot never named. Same class, one level over.
    # ⛔ THE CONTAINER IS JUDGED BEFORE ITS ELEMENTS ARE ITERATED. `for row in snap.issues`
    # ran ahead of the `_LIST_FIELDS` check far below, so a PRESENT-BUT-WRONG-TYPED `issues`
    # (`5`, `null`, `true`, `1.5`) raised `TypeError: … is not iterable` out of the outer
    # frame instead of the field-named refusal this function's SCOPE block promises. Safe
    # polarity (exit 2, no command emitted) but the message was the one thing the door
    # exists to replace. Measured before the fix: `issues: 5` ->
    # "INCOMPLETE — the snapshot could not be decided (TypeError: 'int' object is not iterable)".
    kept_issues: list[dict[str, Any]] = []
    bad_issue_fields: list[str] = []
    issue_rows = snap.issues if isinstance(snap.issues, list) else []
    for row in issue_rows:
        if not isinstance(row, dict):
            bad_issue_fields.append(f"issue element {row!r}")
            continue
        number = row.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            bad_issue_fields.append(f"issue number {number!r}")
            continue
        if not isinstance(row.get("priority"), str):
            bad_issue_fields.append(f"#{number}.priority")
            row["priority"] = ""
        if not isinstance(row.get("title"), str):
            bad_issue_fields.append(f"#{number}.title")
            row["title"] = ""
        # ⛔ THE POPULATION RULE READS `labels` AND `assignees`, SO THEIR SHAPE IS A
        # DECISION INPUT, NOT DECORATION (#10951). Two ways this bites, both
        # measured against the shipped predicate:
        #   · `labels: ["standing-constraint"]` (the natural hand-written form this
        #     file ALREADY tolerates for PRs, see `_labels`) makes the shipped jq
        #     arm die with `Cannot index string with string "name"` — so it is
        #     coerced here to GitHub's `[{"name": …}]`, at the one point every
        #     snapshot passes through, exactly as the PR labels are;
        #   · a present-but-wrong TYPE (`labels: "x"`, `assignees: 3`) is an
        #     element that cannot be read, and a field that cannot be read is not
        #     an absent one — it is recorded and emptied, never silently trusted.
        for key in ("labels", "assignees"):
            if key not in row:
                continue
            if not isinstance(row[key], list):
                bad_issue_fields.append(f"#{number}.{key}")
                row[key] = []
            elif not all(isinstance(v, (str, dict)) for v in row[key]):
                bad_issue_fields.append(f"#{number}.{key} elements")
                row[key] = []
            else:
                # GitHub's shapes: labels are `{name}`, assignees are `{login}`.
                # Using the LABEL shape for both was inert (the shipped arm reads
                # only `.assignees|length`) but it is the wrong object, and an
                # inert wrong shape is exactly what a later reader trusts.
                shape = "name" if key == "labels" else "login"
                row[key] = [{shape: v} if isinstance(v, str) else v for v in row[key]]
        # ⛔ AND A LABEL DICT WITH NO USABLE `name` KILLS THE WHOLE RULE, not just
        # its own row: measured, `{"labels": [{"id": 1}]}` makes the shipped jq
        # arm die with `explode input must be a string`, which would surface as an
        # UNREADABLE population and withhold the ENTIRE queue for one malformed
        # element — a false block. A nameless label declares nothing, so it
        # normalises to the empty name (matched by no arm) and is RECORDED: the rule
        # survives, but the run is INCOMPLETE (exit 2), because a field that cannot
        # be read is not an absent one. Two different promises — "the rule does not
        # die" and "the run is clean" — and this makes the first without the second.
        if isinstance(row.get("labels"), list):
            names: list[dict[str, Any]] = []
            for label in row["labels"]:
                if isinstance(label.get("name"), str):
                    names.append(label)
                else:
                    bad_issue_fields.append(f"#{number}.labels element has no name")
                    names.append({"name": ""})
            row["labels"] = names
        kept_issues.append(row)
    if bad_issue_fields:
        snap.unreadable.append(
            f"issues: {len(bad_issue_fields)} field(s) are not the type this reader needs "
            f"({', '.join(bad_issue_fields[:4])}) — an element that cannot be read is not absent"
        )
        snap.issues = kept_issues
    dropped_pulls: list[Any] = []
    pull_rows = snap.pulls if isinstance(snap.pulls, list) else []
    for pr in pull_rows:
        if not isinstance(pr, dict):
            # ⛔ AN ELEMENT THAT IS NOT A PULL IS DROPPED AND NAMED, not left to the
            # outer frame: it would raise in `build_units` before `render`, so the
            # refusal never printed.
            bad_pull_fields.append(f"pull element {pr!r}")
            dropped_pulls.append(pr)
            continue
        tag = pr.get("number", "?")
        # ADDRESSABILITY IS NOT OPTIONAL: a pull with no usable number cannot be
        # operated on, so ABSENT is refused here (the one exception to "absent is a
        # legitimate omission") — otherwise it surfaced as a KeyError traceback
        # through the outer frame instead of a refusal.
        if not _is_int(pr.get("number")) or pr.get("number") <= 0:
            bad_pull_fields.append(f"pull number {pr.get('number')!r}")
            # ⛔ AND IT MUST LEAVE THE POPULATION. Appending the refusal is not
            # enough: `build_units` does `int(pr["number"])` and raised BEFORE
            # `render`, so the field-named message was discarded and the operator got
            # a KeyError/TypeError from the outer frame instead — the exact outcome
            # the door exists to prevent.
            dropped_pulls.append(pr)
        for key in ("labels", "closing_issues", "refs_issues"):
            if key in pr and not isinstance(pr[key], list):
                bad_pull_fields.append(f"#{tag}.{key}")
                pr[key] = []
            # …and its ELEMENTS. A judged container is not a judged element: a
            # boolean in `closing_issues` coerces to 1 and silently re-points the
            # link, and `closing_issues: [true]` reproduced the NO-PR dispatch for
            # work an existing PR carries.
            elif key in pr:
                ok_el = (
                    (lambda v: isinstance(v, (str, dict)))
                    if key == "labels"
                    else _is_int
                )
                if not all(ok_el(v) for v in pr[key]):
                    bad_pull_fields.append(f"#{tag}.{key} elements")
                    pr[key] = []
        for key in ("draft",):
            if key in pr and not isinstance(pr[key], bool):
                bad_pull_fields.append(f"#{tag}.{key}")
                pr[key] = False
        if "base" in pr and not isinstance(pr["base"], dict):
            bad_pull_fields.append(f"#{tag}.base")
            pr["base"] = {}
        for key in ("head", "head_ref", "title", "fold_line"):
            if key in pr and not isinstance(pr[key], str):
                bad_pull_fields.append(f"#{tag}.{key}")
                pr[key] = ""
    if dropped_pulls:
        snap.pulls = [pr for pr in snap.pulls if pr not in dropped_pulls]
    if bad_pull_fields:
        snap.unreadable.append(
            f"pulls: {len(bad_pull_fields)} field(s) are not the type this reader needs "
            f"({', '.join(bad_pull_fields[:4])}) — a field that cannot be read is not absent"
        )
    # `repo` reaches every emitted command; `base`/`config_source` the header.
    # ⛔ `if value is not None` EXEMPTED A PRESENT `null` — the exact
    # present-but-wrong-typed case this door exists for, and the same class as
    # F1's `number: true`. ABSENT needs no exemption: the dataclass defaults are
    # already strings, so an omitted field arrives here as one.
    for key in ("repo", "base", "config_source"):
        value = getattr(snap, key)
        if not isinstance(value, str):
            snap.unreadable.append(
                f"{key}: the snapshot's {key} is {type(value).__name__}, not a string"
            )
            setattr(snap, key, "")
    # Provenance only — but the sentence says EVERY field, so it is judged too.
    if not isinstance(snap.collected_at, int) or isinstance(snap.collected_at, bool):
        snap.unreadable.append(
            f"collected_at: the snapshot's collected_at is "
            f"{type(snap.collected_at).__name__}, not an int"
        )
        snap.collected_at = 0
    # `claim_ns` is a decision input like any other: a snapshot from before it
    # existed carries its keys under the registry's own default, but a PRESENT value
    # that is not a string is a surface this reader cannot address keys with.
    if "claim_ns" in raw and not isinstance(raw.get("claim_ns"), str):
        snap.unreadable.append(
            f"claim_ns: the snapshot's namespace is {type(raw.get('claim_ns')).__name__}, "
            "not a string — the claim keys cannot be addressed"
        )
    snap.claim_ns = str(raw.get("claim_ns") or "refs/claims")
    for name in _LIST_FIELDS[1:]:
        value = getattr(snap, name)
        if not isinstance(value, list):
            snap.unreadable.append(
                f"{name}: the snapshot's {name} is {type(value).__name__}, not a list "
                "— that surface was never read"
            )
            setattr(snap, name, [])
    for name in _MAP_FIELDS:
        value = getattr(snap, name)
        if not isinstance(value, dict):
            snap.unreadable.append(
                f"{name}: the snapshot's {name} is {type(value).__name__}, not a map "
                "— that surface was never read"
            )
            setattr(snap, name, {})
    # ⛔ A JUDGED CONTAINER IS NOT A JUDGED ELEMENT. `local_holders` is a map of
    # lists; a falsy wrong VALUE (`{"issue:101": ""}`) iterates empty in
    # `_claim_of`, so the unit reads FREE and gets a runnable command at exit 0 for
    # work the snapshot's own key says is held. Same erasure, one level in.
    if isinstance(snap.local_holders, dict):
        bad_values = [k for k, v in snap.local_holders.items() if not isinstance(v, list)]
        if bad_values:
            snap.unreadable.append(
                f"local_holders: {len(bad_values)} entr(y/ies) are not lists "
                f"(e.g. {bad_values[0]!r}) — a holder that cannot be read is not nobody"
            )
            snap.local_holders = {
                k: v for k, v in snap.local_holders.items() if isinstance(v, list)
            }
    if "local_holders" not in raw:
        # ⛔ ABSENT IS NOT EMPTY, for this surface too. `claims` already refuses an
        # absent map ("freedom is not provable"); `local_holders` fell back to the
        # dataclass default `{}`, which `_claim_of` reads as "nothing local holds it"
        # — measured: a snapshot that simply OMITS the field, with a local branch
        # holding the unit, exited 0 WITH a merge command. The local half has no
        # registry entry by construction, so no rail can catch it downstream.
        snap.unreadable.append(
            "local_holders: the snapshot carries no local-holder map — whether this box "
            "already holds the work is not provable"
        )
    if "claims" not in raw:
        # ABSENT is not the same as EMPTY: `_claim_of`'s `ref not in snap.claims`
        # branch is documented as "the registry was read and does not hold this key".
        snap.unreadable.append("claims: the snapshot carries no claim map — freedom is not provable")
    elif isinstance(snap.claims, dict) and snap.claims:
        # The namespace travels with the data (see `claim_ns`), so a map whose keys
        # do not sit under it was built for a different registry — a held claim would
        # simply not be found.
        prefix = f"{snap.claim_ns}/"
        if not all(isinstance(k, str) and k.startswith(prefix) for k in snap.claims):
            snap.unreadable.append(
                f"claims: the snapshot's keys are not under claim_ns={snap.claim_ns!r} — "
                "the registry they were read from is not the one this replay would consult"
            )
    if snap.gate is not None and not isinstance(snap.gate, dict):
        snap.unreadable.append(
            f"gate: the snapshot's gate is {type(snap.gate).__name__}, not a map — "
            "no position is provable"
        )
        snap.gate = None


def _labels(pr: dict[str, Any]) -> set[str]:
    """Label names, tolerating the plain-string form a hand-written snapshot uses.

    `[{"name": "x"}]` is GitHub's shape; `["x"]` is the natural hand-written one.
    The first version assumed the dict and raised AttributeError — a traceback and
    exit 1, which this tool documents as "the queue was read and answered".
    """
    out: set[str] = set()
    for label in pr.get("labels") or []:
        if isinstance(label, str):
            out.add(label)
        elif isinstance(label, dict):
            out.add(str(label.get("name") or ""))
    out.discard("")
    return out


def _fold_constituents(pr: dict[str, Any]) -> set[int]:
    r"""The constituents a fold DECLARES — from `batch_open.py`'s OWN parser.

    ⛔ THE GRAMMAR IS THE ACTING TOOL'S, NOT A COPY OF IT. Three rounds of "match
    batch_open exactly" produced three divergences (a `\n` crossed by `\s*`, a
    `fold-constituents :` the owner tool accepts and this one missed, `\d` vs
    `[0-9]`), every one of them unsafe in the same direction: being STRICTER than
    the tool that acts means a constituent stays pickable and can land ahead of its
    fold (#10902), because the acting tool wins. So the parser is imported — the
    same rule this module already applies to the queue gate.

    Two behaviours come with it and both are wanted:
      * the fold is never its OWN constituent (`batch_open`: "the fold's own number
        is never a constituent" — otherwise the row supersedes itself and the
        work-carrying fold vanishes);
      * the FIRST matching line decides; a malformed later line is not a refusal,
        because it is not the line the owner tool reads.
    """
    body = str(pr.get("fold_line") or "")
    own = pr.get("number")
    return set(parse_constituents(body, int(own) if own is not None else None))


def _fold_line_refused(pr: dict[str, Any]) -> bool:
    """True when the body carries a `fold-constituents:` line `batch_open` refuses.

    `declared_constituents_line` says whether a line EXISTS; the parser says whether
    it yielded anything. A line that does not parse AND one that parses yet yields
    nothing (an empty value, or a line naming only the fold itself) both count as a
    refusal — the same distinction `batch_open`'s own CLI draws.
    """
    body = str(pr.get("fold_line") or "")
    if declared_constituents_line(body) is None:
        return False
    own = pr.get("number")
    return not parse_constituents(body, int(own) if own is not None else None)


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


def _open_pulls_for_issue(snap: Snapshot, issue: int) -> list[dict[str, Any]]:
    """Every open PR that the repository itself records as naming this issue.

    Two mechanical sources, both written by GitHub, neither parsed from prose
    here: `closingIssuesReferences` (what the PR would CLOSE on merge) and the
    issue's own `cross-referenced` timeline (what it REFERENCES). The gap between
    them is real and measured — #10534 found 18 of 18 PRs naming a P0/P1 used
    `Refs`, so a consumer that read only closing keywords would call every P0
    `NO-PR` and be confidently wrong. The distinction is carried into the reason
    so a row whose only link is a `Refs` says so.
    """
    hits: list[dict[str, Any]] = []
    for pr in snap.pulls:
        if issue in {int(n) for n in (pr.get("closing_issues") or [])}:
            hits.append({**pr, "_link": "closes"})
        elif issue in {int(n) for n in (pr.get("refs_issues") or [])}:
            hits.append({**pr, "_link": "refs"})
    # Newest PR first: the highest number is the most recent attempt, and an older
    # abandoned PR is not the thing to hand a lane.
    return sorted(hits, key=lambda p: int(p["number"]), reverse=True)


def _abstention_reason(entry: object) -> str | None:
    """Why this claim entry CANNOT BE READ as free, or ``None``. ONE definition.

    ⛔ TWO READERS, ONE RULE — THAT IS THE POINT. `normalize()` decides whether the
    RUN is incomplete and `_claim_of()` decides whether the ROW is free; when they
    disagreed about what "unreadable" means, the same snapshot produced a row
    saying "claim registry UNREADABLE" beside a verdict saying "every measured unit
    is held or superseded", and could degrade to a merge command at exit 0 with a
    second pickable unit present. Both defects were measured on this change, both
    on the `--from` path (a hand-edited or older snapshot), and both came from the
    two readers drifting. A present key with NO verdict is not `claim-absent`: the
    three rc-0 verdicts (`claim-absent`, `claim-expired`, `claim-orphaned`) mean
    freedom, and silence is none of them.
    """
    if not isinstance(entry, dict):
        return "the entry is not a verdict object"
    verdict = str(entry.get("verdict") or "")
    if not verdict:
        return "the entry carries no verdict"
    if verdict == "claim-unreadable":
        return "claim-unreadable"
    return None


def _claim_of(
    snap: Snapshot, *, issue: int | None, pr: int | None, head: str | None
) -> tuple[str | None, bool]:
    """(holder description or None, registry_readable).

    Keys are the registry's own: `issue-<N>` (agent-infra#1850), `pr-<N>`
    (agent-infra#1952) and `<pr>-<sha12>` (content-addressed, rec 1). A verdict
    that is not one of the three rc-0 verdicts (`claim-absent`, `claim-expired`,
    `claim-orphaned`) is NOT freedom: `claim-unreadable` is an ABSTENTION (it blocks
    the row AND makes the run incomplete), and an unknown word — `claim-held` or
    anything this reader has never seen — is a HOLDER. Neither proves freedom.

    ⛔ THE FULL REF, NOT THE BARE KEY. `claims.sh` indexes `refs/claims/<key>`
    (claim_snapshot writes `${CLAIM_NS}/${key}`) and its reader matches field 1
    EXACTLY, so asking for `pr-10852` answered `claim-absent` for a key that is
    HELD — and `claim-absent` is a word this module reads as freedom. Measured by a
    verifier: PRs #10852 and #10889 were `claim-held` by a live box and both came
    out `READY-TO-LAND` with an executable command.
    """
    # NO ENV FALLBACK: the namespace is data. `refs/claims` is claims.sh's own default.
    ns = snap.claim_ns or "refs/claims"
    refs: list[str] = []
    if issue is not None:
        refs.append(f"{ns}/issue-{issue}")
    if pr is not None:
        refs.append(f"{ns}/pr-{pr}")
        if head:
            refs.append(f"{ns}/{pr}-{head[:12]}")
    readable = True
    #: `claims.sh`'s OWN vocabulary (claim_snapshot_verdict), mapped once:
    #:   claim-absent    rc 0  no entry in a read snapshot          -> FREE
    #:   claim-expired   rc 0  the holder's window closed           -> FREE
    #:   claim-orphaned  rc 0  the holder cannot be a live lane     -> FREE
    #:   claim-held      rc 1  a live holder                       -> HOLDER
    #:   claim-unreadable rc 2 the registry could not answer       -> ABSTENTION
    #: THREE values mean freedom (every rc-0 verdict of `claims.sh`), one means a
    #: live holder, and one is an abstention. An abstention is NOT freedom and it
    #: is not a silent note either: it blocks the row AND makes the run incomplete.
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
    local_keys = []
    if issue is not None:
        local_keys.append(f"issue:{issue}")
    if pr is not None:
        # A fold has NO issue, so `issue:` keys cannot cover it — a fold held only
        # by a local branch read free. `_local_holders` records both keyings.
        local_keys.append(f"pr:{pr}")
    for key in local_keys:
        for desc in snap.local_holders.get(key, []):
            return (f"local:{desc}", readable)
    return (None, readable)


def _gate_refusal_edit(conditions: list[str]) -> str:
    """The edit each gate refusal actually needs — they are NOT all the same.

    The first version emitted "a human retargets the base or adds the label" for
    EVERY structural condition, which is the wrong prescription for a DRAFT (the
    author undrafts it) and for `base=main (actual unknown)` (the base may be
    correct — it could not be READ).
    """
    tips: list[str] = []
    for cond in conditions:
        if cond.startswith("draft"):
            tips.append("the author must undraft it")
        elif "actual unknown" in cond:
            tips.append("the PR's base could not be READ — re-read the PR")
        elif cond.startswith("base="):
            tips.append(f"retarget the PR ({cond})")
        elif cond.startswith("label="):
            tips.append(f"add the required label {cond.split(':', 1)[0].split('=', 1)[-1]}")
        else:
            tips.append(cond)
    return "; ".join(dict.fromkeys(tips))


def _check_state(cond: str) -> str:
    """The STATE half of a `check-success=` condition, read from the LAST `": "`.

    ⛔ The LAST separator, not the first: a check RUN may legitimately be named
    `docs: build`, and a check whose name contains `": "` must not have its NAME
    read as its state — that is the same `completed/` hole one level in.
    """
    return cond.rsplit(": ", 1)[1] if ": " in cond else ""


def _position_for_pr(
    snap: Snapshot,
    cfg: QueueConfig | None,
    pr: dict[str, Any],
) -> tuple[str, list[str]]:
    """The position of one PR — gate first, then attestation, then merge, in that order."""
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
    # queue will refuse, advertised as the closest to done.
    # ⛔ BUCKET BY PROVENANCE, NOT BY SUBSTRING. `unmet_conditions` emits five
    # forms, and TWO of them embed text the PR itself chooses — its base ref in
    # `base=<cfg.base> (actual <ref>)` and a label name in `label=<name>: absent`.
    # The first version substring-matched `completed/`, `in flight` and
    # `no check-run` over the WHOLE string, so a PR based on `completed/spike` —
    # a legal ref (`git-check-ref-format`: no slash-separated component may begin
    # with a dot or end `.lock`, and nothing forbids `completed/`) — was read as a
    # RED CHECK: `fails` took it, `structural` came out empty, the `gate-refusal:`
    # detail was never written, and `next_command` handed back `fix-one.sh` for a
    # PR the queue refuses at entry, at exit 0, with `next_action` printing
    # "nothing is dispatched" beside it. Measured on that tree: `unmet ->
    # ['base=main (actual completed/spike)', 'label=completed/x: absent']` bucketed
    # BOTH into `fails`. The same construction mis-graded a base ref or label
    # containing `in flight` into NEEDS-TEST ("wait") — a wrong prescription.
    # So: the PREFIX decides which condition this is, `_check_state` reads the
    # state from the last `": "`, and the text is display-only. Fail-closed on
    # top — every `check-success=` condition that is not POSITIVELY pending (no
    # run yet, or a run still in flight) counts as a failing check, so no
    # condition, well-formed or not, can fall out of all three buckets into
    # READY-TO-LAND.
    check_conds = [c for c in blocking if c.startswith("check-success=")]
    in_flight = [
        c
        for c in check_conds
        if _check_state(c) == "no check-run" or _check_state(c).endswith("(in flight)")
    ]
    fails = [c for c in check_conds if c not in in_flight]
    structural = [c for c in blocking if not c.startswith("check-success=")]

    # ⛔ A RED REVIEW GATE IS NOT A CODE PROBLEM. `.mergify.yml` says
    # `check-success=ai-review-gate` "is red by design until a review is recorded,
    # which IS the enforcement" — so a red review gate means A REVIEW (or a
    # re-record) IS OWED, not that code must change. Bucketing it with the failing
    # checks made the tool dispatch a FIXER at a PR whose reviewer left no findings,
    # and made its own NEEDS-REVIEW ladder unreachable on the live gate (gate-green
    # ⇒ attestation bound ⇒ READY-TO-LAND; gate-red ⇒ NEEDS-FIX). Measured by a
    # reviewer on the live queue: #10858 (`norecord`), #10833 and #10575 (`fresh`)
    # were all handed `fix-one.sh`.
    review_red = [c for c in fails if any(f"check-success={n}:" in c for n in REVIEW_GATE_CHECKS)]
    other_fails = [c for c in fails if c not in review_red]

    att = snap.attestation.get(str(pr["number"])) or {}
    verdict = str(att.get("verdict") or "")

    # ⛔ THE REFUSAL IS RECORDED WHATEVER THE POSITION. It used to be appended only
    # in the `elif structural` branch, so a structural refusal OUT-VOTED by a red
    # check left no `gate-refusal:` detail — and the command guard, which keys on
    # that detail, then handed out `fix-one.sh` for a gate-refused PR.
    if structural:
        detail.append(
            f"gate-refusal: {'; '.join(structural)} — no code change and no rail fixes this; "
            f"{_gate_refusal_edit(structural)}"
        )
    if other_fails:
        pos = "NEEDS-FIX"
    elif structural:
        # ⛔ A BASE MISMATCH OR A MISSING REQUIRED LABEL IS NOT A CODE FIX. These
        # landed on NEEDS-FIX, whose rail is `fix-one.sh` — a fixer dispatched at a
        # PR whose only defect is that it targets another branch or lacks a label
        # finds no findings, pushes nothing, and re-fails the chain. Same conflation
        # round 14 fixed for the review gate; a human retargets or relabels.
        pos = "NEEDS-REVIEW"
    elif review_red and verdict not in ("head", "carry"):
        # Red by design: the record does not bind this head. The attestation block
        # below decides whether a review or a re-record is owed.
        pos = "NEEDS-REVIEW"
    elif review_red:
        # The record binds the head yet the gate is red. That is NOT simply "a real
        # failure": `record-review.sh`'s own header (#10677) documents the case where
        # a record's SIGNED body marker never reached the PR, so the gate correctly
        # reports "No AI review evidence found" and a properly reviewed PR is blocked
        # by a BOOKKEEPING failure — remedy `--flush-pending`, not a code change. The
        # tool cannot tell the two apart, so it asks a human rather than dispatching a
        # fixer at code a reviewer already approved.
        pos = "NEEDS-REVIEW"
        manual = (
            "review-gate-red: a head-bound record exists yet the review gate is red — either "
            "the record's "
            "evidence never reached the PR (record-review.sh --flush-pending) or the check is "
            "genuinely failing. A human decides; do not dispatch a fixer at approved code"
        )
        detail.append(manual)
    elif in_flight:
        pos = "NEEDS-TEST"
    else:
        pos = "READY-TO-LAND"

    if pos in ("READY-TO-LAND", "NEEDS-REVIEW"):
        if verdict in ("head", "carry"):
            detail.append(f"attestation: {verdict}")
        elif verdict == "carry-patch":
            pos = "NEEDS-REVIEW"
            detail.append("attestation: carry-patch — re-record at this head (no review cycle owed)")
        elif verdict == "nodigest":
            # `record-review.sh` exit 5: "the record has no usable digest — re-run
            # the recorder at the head and let its carry arms decide (a CARRY, not
            # a review)". Prescribing a review burned 20-40 minutes per occurrence.
            pos = "NEEDS-REVIEW"
            detail.append(
                "attestation: nodigest — the record has no usable digest, so RE-RECORD "
                "at this head (a carry, not a review)"
            )
        elif verdict in ("fresh", "norecord"):
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

    # ⛔ THE MERGE ABSTENTION RUNS LAST, AND ONLY DOWNGRADES. It used to run FIRST,
    # so a PR with an unmeasured tree AND a missing review reported NEEDS-TEST
    # ("waiting on a runner") and the review that was actually owed was never
    # surfaced — the opposite of the documented order (gate → attestation → merge).
    mt = snap.merge_tree.get(str(pr["number"])) or {}
    clean = mt.get("clean")
    # ⛔ THE POLARITY IS `is True`, NOT `is False`. Written as `is False` / `is None`,
    # every OTHER value — `0`, `"false"`, `1`, `{}`, `[]`, `"yes"` — fell through
    # with no downgrade at all, i.e. a tree that was never shown to merge read as
    # MEASURED CLEAN and got the merge rail at exit 0. That is the exact inversion
    # the comment below forbids, and it was the only field in this module where
    # "not recognised" defaulted permissive: an unknown attestation verdict, an
    # unknown claim verdict and a missing gate all fail closed. `0` and `"false"`
    # are the values a hand-written snapshot most plausibly carries for "conflicted".
    if clean is True:
        pass  # measured clean; nothing to record
    elif clean is False:
        pos = "NEEDS-FIX"
        detail.append(f"merge-tree: {mt.get('why') or 'conflict against base'}")
    else:
        if pos == "READY-TO-LAND":
            pos = "NEEDS-TEST"
        detail.append(
            f"merge-tree: UNMEASURED ({mt.get('why') or 'head not available locally'})"
            + ("" if clean is None else f" — clean={clean!r} is not a boolean verdict")
            + " — cannot be shown to merge, so it is not landable"
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
        # ⛔ THE POPULATION VERDICT IS A PROPERTY OF THE ISSUE, NOT OF ITS PR STATE
        # (#10951). It is read once, from the snapshot the collector filled by
        # DRIVING the fleet's own rule, and it is applied on BOTH branches below:
        # the rule withholds a container whether or not something is attached to
        # it, which is exactly what the board does. It sits AFTER `superseded`
        # (a fold constituent's reason is the relationship, not the container) and
        # BEFORE `claimed` (a lane holding a non-unit is the less useful fact; the
        # holder is kept in `detail` either way).
        population = (
            REASON_POP_UNMEASURED if snap.population_absent
            else str((snap.population or {}).get(str(number)) or "")
        )
        pulls = _open_pulls_for_issue(snap, number)
        pr = pulls[0] if pulls else None
        if pr is None:
            holder, readable = _claim_of(snap, issue=number, pr=None, head=None)
            u = Unit(
                key=str(number), kind="issue", issue=number, fold_pr=None,
                priority=priority, title=str(iss.get("title") or ""),
                pr=None, head=None, position="NO-PR",
                reason=population or (REASON_CLAIMED if holder else "NO-PR"),
                withheld_by_population=bool(population),
                detail=[f"no open PR closes or references #{number}"],
                holder=holder,
            )
            if population:
                u.detail.append(_population_note(population, snap))
                if holder:
                    u.detail.append(
                        f"…and a lane holds it ({holder}) — the population verdict is the "
                        "stronger fact: this is not a unit of work to dispatch at all"
                    )
            if not readable:
                u.detail.append("claim registry UNREADABLE for this unit")
            units.append(u)
            continue

        pr_number = int(pr["number"])
        folds = sorted(fold_of.get(pr_number) or [])
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
        # The flag mirrors the REASON that actually won (`superseded` outranks the
        # population verdict), so `--json` never reports `withheld_by_population:
        # true` beside `reason: SUPERSEDED-BY-FOLD` — and a superseded row keeps
        # registering its action target, which is the pre-existing behaviour.
        withheld_by_population = bool(population) and not superseded
        if superseded:
            reason = REASON_SUPERSEDED
            if folds:
                detail.append(f"constituent of open fold #{folds[0]} — the fold is the unit")
            else:
                detail.append(f"carries `{CONSTITUENT_LABEL}` with no open fold — the fold is missing")
        elif population:
            reason = population
            detail.append(_population_note(population, snap))
            if holder:
                detail.append(
                    f"…and a lane holds it ({holder}) — the population verdict is the "
                    "stronger fact: this is not a unit of work to dispatch at all"
                )
        elif holder:
            reason = REASON_CLAIMED
        else:
            reason = position
        u = Unit(
            key=str(number), kind="issue", issue=number, fold_pr=folds[0] if folds else None,
            priority=priority, title=str(iss.get("title") or ""),
            pr=pr_number, head=str(pr.get("head") or "") or None,
            position=position, reason=reason, detail=detail, holder=holder,
            head_ref=str(pr.get("head_ref") or "") or None,
            draft=bool(pr.get("draft")),
            withheld_by_population=withheld_by_population,
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
        if _fold_line_refused(pr):
            detail.append(
                "declared `fold-constituents:` line does not parse — refused WHOLE (the rule "
                "`batch_open.py` applies), so this fold declares no constituents"
            )
        if FOLD_LABEL not in labels:
            detail.append(
                f"fold is UNLABELLED (`{FOLD_LABEL}` absent) — #10909's CI priority and the "
                "queue's own fold ordering do not see it"
            )
        # ⛔ A FOLD CAN ITSELF BE A CONSTITUENT of a bigger fold. The issue path
        # applies this rule and the fold path did not, so a `batch-fold` PR carrying
        # `superseded-by-fold` — or declared in an outer fold's line — computed
        # READY-TO-LAND and was handed the command that lands it SEPARATELY and
        # ahead of the fold that carries it: the #10902 failure this tool exists to
        # prevent, for a PR the repo's own entry gate refuses. `_position_for_pr`
        # strips the constituent condition from `blocking` (the superseded row still
        # has a position), so the rule must be applied HERE.
        outer = sorted(fold_of.get(pr_number) or [])
        is_constituent = bool(outer) or CONSTITUENT_LABEL in labels
        if is_constituent:
            detail.append(
                f"constituent of open fold #{outer[0]} — the OUTER fold is the unit"
                if outer
                else f"carries `{CONSTITUENT_LABEL}` with no open fold named — it is not a unit"
            )
        u = Unit(
            key=f"fold:{pr_number}", kind="fold", issue=None, fold_pr=pr_number,
            priority=priority, title=str(pr.get("title") or ""),
            pr=pr_number, head=str(pr.get("head") or "") or None,
            position=position,
            reason=REASON_SUPERSEDED if is_constituent
            else (REASON_CLAIMED if holder else position),
            detail=detail, holder=holder,
            head_ref=str(pr.get("head_ref") or "") or None,
            draft=bool(pr.get("draft")),
        )
        if not readable:
            u.detail.append("claim registry UNREADABLE — this row's freedom is not provable")
        units.append(u)

    units.sort(key=lambda u: u.sort_key)
    # ⛔ A HELD ROW STILL OWNS ITS TARGET. Marking duplicates only among PICKABLE
    # rows let a claimed row be skipped before it registered its PR, so a second
    # issue closed by the same PR was offered `merge-one.sh` for an artifact whose
    # work is held — refused only at merge time, by the pr-<N> claim, instead of
    # here. Blocked rows are registered FIRST and they block unconditionally: a
    # CLAIMED or SUPERSEDED row's target is not free just because another row
    # closing it looks ready.
    # ⛔ …BUT A POPULATION-WITHHELD ROW OWNS NOTHING (#10951 review, P2). It is not
    # a unit of work, so the artifact it happens to point at is not "held" by it —
    # and registering its target made a fully merge-ready REAL unit unofferable:
    # measured, one PR closing both `design(extractor):` #5333 and `feat(…)` #8488
    # produced `8488 … duplicate of 5333` with NO command anywhere, while the footer
    # said "every measured one is held, superseded, or a duplicate" — none of which
    # the target owner was. Containers commonly share a PR with a closing issue.
    seen_targets: dict[str, str] = {}
    for u in units:
        if u.pickable or u.withheld_by_population:
            continue
        seen_targets.setdefault(u.action_target, u.key)
    for u in units:
        if not u.pickable:
            continue
        first = seen_targets.get(u.action_target)
        if first is None:
            seen_targets[u.action_target] = u.key
        else:
            u.duplicate_of = first
            u.detail.append(
                f"not offered: duplicate of #{first} — the same action target is already "
                "carried by that row, so this one has no command"
            )
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
    """A shell command a runner can EXECUTE. ``None`` when there is nothing to run.

    ⛔ A COMMENT IS NOT A COMMAND. `NEEDS-REVIEW` used to return
    ``# review PR #N …, then: record-review.sh … --evidence comment:<id>`` — a
    fully commented-out line that exits 0 having done nothing. A runner that
    executes `next_command` saw success and no work, indistinguishable from having
    done it. Anything that needs a human decision (a reviewer, an evidence comment
    id) is an ACTION, not a command: see `next_action`.
    """
    if not repo:
        # A command with an empty slug is not "the exact command a runner would
        # execute" — a hand-written snapshot with no `repo` produced `'' 1001`.
        return None
    # ⛔ A GATE-REFUSED ROW NEVER REACHES A RAIL — the same guard the DRAFT fix
    # added, for the same reason. A structural refusal (`base=` / `label=`) can be
    # out-voted on POSITION by a red check or a merge conflict (`other_fails` wins,
    # and the merge block downgrades unconditionally), so the row is NEEDS-FIX and
    # used to be handed `fix-one.sh` — while `next_action` printed "nothing is
    # dispatched" beside it. Measured: wrong base + conflict → COMMAND fix-one.sh,
    # and `--json` carried a command AND an action for one unit, breaking this
    # file's own `command XOR action` invariant.
    if not unit.pickable or unit.draft or any(
        d.startswith("gate-refusal:") for d in unit.detail
    ):
        # A draft is the AUTHOR's; the queue refuses it at entry and a fixer would
        # be a second lane on a human's branch.
        return None
    # The rails take the branch as their 4th argument. When the snapshot does not
    # carry it, resolve it IN THE COMMAND — a `<branch …>` placeholder parses as a
    # shell input redirection, so the "command" would fail with "No such file".
    # ⛔ THE BRANCH IS UNTRUSTED INPUT. `git check-ref-format` ALLOWS `$(`, backticks,
    # `"` and `;` in a ref name, and double quotes EXECUTE `$( )` — measured: a head
    # ref of `fix/100-$(tar${IFS}czf${IFS}/tmp/x${IFS}~/.pi/agent)` was interpolated
    # raw into the one string this tool exists to hand a runner. `merge-one.sh`
    # documents this exact threat and solved it with `--argv`; the emitter must not
    # put the value back in the shell. `shlex.quote` is the one-line form.
    # ⛔ EVERY VALUE IN THIS STRING IS UNTRUSTED, NOT JUST THE BRANCH. `repo` comes
    # from `--repo`, from a remote URL (`_repo_slug`'s regex accepts `;`, `$(` and
    # spaces), or from a `--from` snapshot; the first fix quoted the branch and left
    # the five rail lines interpolating `{repo}` raw — measured: `evil/repo;touch
    # /tmp/PWNED` split into two argv words and the second command RAN. One rule,
    # applied to every value: `shlex.quote`, then build.
    qrepo = shlex.quote(repo)
    branch = unit.head_ref
    if branch:
        branch_shell = shlex.quote(branch)
    else:
        branch_shell = (
            f'"$(gh pr view {unit.pr} --repo {qrepo} --json headRefName -q .headRefName)\"'
        )
    if unit.kind == "fold" and unit.position == "READY-TO-LAND":
        return f"bash scripts/orchestrator-harness/merge-one.sh . {qrepo} {unit.pr} {branch_shell}"
    if unit.kind == "fold" and unit.position not in ("READY-TO-LAND", "NEEDS-REVIEW", "NEEDS-TEST"):
        return f"bash scripts/orchestrator-harness/fix-one.sh . {qrepo} {unit.pr} {branch_shell}"
    if unit.kind == "fold":
        return None
    if unit.position == "NO-PR":
        # ⛔ `lane.sh` DOES NOT PROVISION. Its own header: "Runs ONE lane's pi inside
        # its own worktree (cwd must already be the worktree)", it sets LANE_WT="$PWD"
        # and REFUSES unless refs/heads/<branch> exists AND $PWD is checked out on it
        # — measured: `lane.sh --repo … 10955 lane/10955` → exit 72,
        # `reason=branch-missing`. `lane/<N>` is also a branch no fleet tool creates
        # (the real dispatch uses `feat/<N>-tm`). The rail that creates the worktree
        # and then calls lane.sh FROM it is `dispatch-merge.sh`.
        return (
            f"bash scripts/orchestrator-harness/dispatch-merge.sh . {qrepo} {unit.issue}"
        )
    if unit.position == "READY-TO-LAND":
        return f"bash scripts/orchestrator-harness/merge-one.sh . {qrepo} {unit.pr} {branch_shell}"
    if unit.position == "NEEDS-FIX":
        return f"bash scripts/orchestrator-harness/fix-one.sh . {qrepo} {unit.pr} {branch_shell}"
    return None


def next_action(unit: Unit, repo: str) -> str | None:
    """What to DO for a pickable row, when it is not a shell command.

    A review, a wait, or a re-record are real work with no runnable invocation — an
    evidence comment id does not exist until the review has happened. Saying so is
    honest; emitting a comment-as-command is not.
    """
    if not unit.pickable:
        return None
    target = f"#{unit.issue}" if unit.kind == "issue" else f"fold PR #{unit.fold_pr}"
    if unit.draft:
        return (
            f"the author must UNDRAFT {target} — the queue will not take it and a fixer must "
            "not be dispatched at the author's branch"
        )
    if any(d.startswith("gate-refusal:") for d in unit.detail):
        # ⛔ BEFORE `nodigest`: a gate refusal keeps the PR out of the queue whatever
        # the record says, so it must be the line a lane reads. Measured: a PR with a
        # wrong base AND `nodigest` got "re-record … needs NO new review" as its only
        # ACTION, and the base mismatch appeared nowhere but the row detail.
        why = next(d.split(":", 1)[1].strip() for d in unit.detail if d.startswith("gate-refusal:"))
        return (
            f"a HUMAN fixes the gate refusal on {target}: {why}. No code change and no rail does "
            "this, so nothing is dispatched"
        )
    if unit.position == "NEEDS-REVIEW" and any("nodigest" in d for d in unit.detail):
        # The record carries no usable digest: re-mint at the head, no review owed.
        return (
            f"re-record {target} at head {unit.head} — the record has no usable digest, so "
            f"record-review.sh {unit.pr} <current-head> clean {shlex.quote(repo)} needs NO new "
            "review"
        )
    if any(d.startswith("review-gate-red:") for d in unit.detail):
        return (
            f"a HUMAN decides for {target}: a head-bound record exists yet the review gate is "
            "red — either its evidence never reached the PR (record-review.sh --flush-pending) "
            "or the check is genuinely failing. Do NOT dispatch a fixer at approved code"
        )
    if unit.position == "NEEDS-REVIEW" and any("UNREADABLE" in d for d in unit.detail):
        # `--check-carry` exit 3: "the live diff could not be READ — RETRY, do NOT
        # re-review". The row printed "abstention, not a clean record" and then
        # asked for a review; a retry is what is owed.
        return (
            f"retry the attestation read for {target} at head {unit.head} "
            f"(record-review.sh --check-carry {unit.pr} {shlex.quote(repo)}) — an UNREADABLE diff "
            "is a RETRY, not a review"
        )
    if unit.position == "NEEDS-REVIEW" and any("carry-patch" in d for d in unit.detail):
        # ⛔ `carry-patch` IS NOT A REVIEW CYCLE. `record-review.sh` exit 6 says
        # "RE-RECORD at the current head and do NOT run a review cycle" — the
        # reviewed PATCH is byte-identical, only the rendered diff moved. Offering
        # "dispatch a reviewer … --evidence comment:<id>" wasted a review AND
        # nudged toward the fabricated-evidence shape the recorder warns about.
        return (
            f"re-record at {target} head {unit.head} — the reviewed PATCH is unchanged, so "
            f"record-review.sh {unit.pr} <current-head> clean {shlex.quote(repo)} needs NO new "
            "review"
        )
    if unit.position == "NEEDS-REVIEW":
        return (
            f"dispatch a reviewer at {target} head {unit.head} ({shlex.quote(repo)}), then "
            "record it with "
            f"record-review.sh {unit.pr} <current-head> clean {shlex.quote(repo)} "
            "--evidence comment:<id>"
        )
    if unit.position == "NEEDS-TEST" and any("merge-tree: UNMEASURED" in d for d in unit.detail):
        # Nothing is in a runner queue here; the tree could not be measured (head
        # unavailable, the PR moved mid-sweep, the base is missing). Say THAT.
        return (
            f"retry the sweep for {target} — the merge tree could not be measured "
            f"({next(d for d in unit.detail if 'UNMEASURED' in d)}), so nothing hosts a wait"
        )
    if unit.position == "NEEDS-TEST" and any("no check-run" in d for d in unit.detail):
        # ⛔ NOTHING IS IN A RUNNER QUEUE when a required check has never REPORTED:
        # a check name nothing emits, or a paths-ignore skip (a CANCELLED run does
        # report — `completed/cancelled` — and lands in `fails`). Waiting is not the
        # remedy — that is a harness defect.
        which = next(d for d in unit.detail if "no check-run" in d)
        return (
            f"a required check has NEVER REPORTED for {target} ({which}) — nothing is queued and "
            "waiting will not fix it; treat it as a harness defect"
        )
    if unit.position == "NEEDS-TEST":
        return f"wait — {target} is in a runner queue; nothing to dispatch until its head reports"
    return None


#: Seconds between GitHub calls. The API throttles per-call and a queue sweep
#: makes ~50 of them, so the sweep is PACED rather than fanned out.
#:
#: ⛔ Parsed at USE time, not at import. As `float(os.environ.get(...))` at module level this
#: raised `ValueError` before `main()`, and the process then exited **1** — the code this tool
#: defines as "the queue was read but NO unit is pickable: that is an answer" (`:112`,
#: `:2289-2305`, `:1735-1748`). A consumer keying on the exit code read "nothing to do" from a
#: malformed knob. Measured:
#:
#:     NEXT_WORK_PACE=0.2s uv run python tools/next_work.py
#:
#: Environment overrides. The two POPULATION levers are PRINTED in the run's
#: provenance (see the module docstring's ``Usage`` block); the other two are a
#: resolved path and a pacing number and are NOT rendered:
#:     NEXT_WORK_POPULATION=0    DISARMS the fleet's work-unit rule (#10951). No
#:                               population rule is consulted at all: every row is
#:                               admitted on its title and labels, exactly as before
#:                               that issue — an epic, a `design(…)` decision, a
#:                               `measure(…)` and #10175 all get a runnable command.
#:                               Reported as `OFF — …`; never passed for a measurement.
#:     NEXT_WORK_POOL_JQ_SH=…    the script carrying the `POOL_JQ` assignment, when it
#:                               is not this checkout's `scripts/` or $AGENT_INFRA_PATH.
#:     NEXT_WORK_CLAIMS_SH=…     `claims.sh`, when it is not resolvable in-tree.
#:     NEXT_WORK_PACE=…          the pacing gap between `gh` calls.
#:       -> ValueError: could not convert string to float: '0.2s'
#:       -> exit 1 with no snapshot read at all
#:
#: So a bad value falls back to the documented default with a named warning on stderr, and can
#: never decide an exit code.
#:
#: ⛔ AND "NOT A NUMBER" WAS ONLY HALF THE HOLE (fold review, P2). `float("inf")` (or `1e400`)
#: parsed FINE and then `time.sleep(inf)` raised `OverflowError` — an `ArithmeticError`, which
#: `main`'s catch tuple does not name — so a typo'd knob still exited **1**, the "queue
#: exhausted" code; and a huge finite value would have slept for years. The seam is therefore
#: graded on the VALUE, not only on the parse: finite, non-negative, capped.
PACE_SECONDS_DEFAULT = 0.2
#: The documented ceiling. A pacing knob is a rate-limit courtesy; a value that can outlast a
#: lane is not a pace, it is a hang.
PACE_SECONDS_MAX = 5.0
_PACE_WARNED = False


def pace_seconds() -> float:
    """The pacing gap, read from `NEXT_WORK_PACE` on every use (see the block above)."""
    global _PACE_WARNED
    raw = os.environ.get("NEXT_WORK_PACE")
    if raw is None or raw == "":
        return PACE_SECONDS_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value) or value < 0:
        if not _PACE_WARNED:
            _PACE_WARNED = True
            print(
                f"next_work: ignoring NEXT_WORK_PACE={raw!r} (not a usable number); "
                f"using {PACE_SECONDS_DEFAULT}s",
                file=sys.stderr,
            )
        return PACE_SECONDS_DEFAULT
    return min(value, PACE_SECONDS_MAX)


#: A snapshot of the same seam, read once at import, for callers that report the pace rather
#: than use it (and for `--help`-shaped runs that never pace). It is NOT what `_pace()` reads:
#: the pacing gap itself is resolved at USE time, so a test can move it.
PACE_SECONDS = pace_seconds()


def _pace() -> None:
    gap = pace_seconds()
    if gap > 0:
        time.sleep(gap)


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
    args: Sequence[str],
    *,
    timeout: float = GH_TIMEOUT,
    cwd: Path | None = None,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Every git call is pinned to the checkout being judged, never the process cwd."""
    try:
        return subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None,
            input=stdin_text,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - environment
        return subprocess.CompletedProcess(list(args), 127, "", str(exc))


class _Incomplete(SystemExit):
    """Raised to end the run with INCOMPLETE (2) from deep inside a collector."""

    def __init__(self) -> None:
        super().__init__(2)


def _repo_slug(explicit: str | None, repo_root: Path | None = None) -> str:
    if explicit:
        return explicit
    proc = _run(["git", "remote", "get-url", "origin"], timeout=GIT_TIMEOUT, cwd=repo_root)
    if proc.returncode == 0:
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?\s*$", proc.stdout.strip())
        if m:
            return m.group(1)
    # ⛔ exit 2, NOT SystemExit(1). The interpreter's 1 means "the queue was read and
    # no unit is pickable"; this run never read the queue. `main` cannot catch
    # SystemExit, so raising it was the one remaining mis-grade of the same family
    # as the missing and mis-shaped snapshot.
    print(
        "next_work: INCOMPLETE — could not resolve owner/name from origin; pass --repo "
        "owner/name. exit 2 is 'could not check'.",
        file=sys.stderr,
    )
    raise _Incomplete()


def dedupe_issues(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per ISSUE, highest priority winning, ordered by number.

    Two searches, one per label, and an issue carrying both appeared twice with
    the same `key`: the population was overcounted and `--json` emitted two units
    a machine consumer keys as one. It is a function so the fix is testable — the
    earlier test re-implemented the dedup instead of calling it.
    """
    by_number: dict[int, dict[str, Any]] = {}
    for row in rows:
        number = int(row["number"])
        seen = by_number.get(number)
        if seen is None or PRIORITY_RANK.get(str(row.get("priority") or ""), 99) < PRIORITY_RANK.get(
            str(seen.get("priority") or ""), 99
        ):
            by_number[number] = row
    return [by_number[n] for n in sorted(by_number)]


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
                    # ⛔ CARRIED, NOT DISCARDED (#10951). This projection used to
                    # keep three keys, so the population rule's two INPUT FIELDS
                    # never reached the reader that must consult it — the field
                    # erasure #10902 records for PRs, on the issue side. The data
                    # was always in `it` (24/24 live rows carried both); only the
                    # projection threw it away.
                    "labels": it.get("labels") or [],
                    "assignees": it.get("assignees") or [],
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
    (`fix/#?(\d{3,7})(?![0-9])(?:-|$)`); this keeps the boundary half for the
    range ISSUE numbers actually occupy ({3,6} — one digit tighter than that
    anchored rule, which is anchored to a branch prefix this reader does not have) AND drops
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
    branch counts when it carries commits ahead of the base, which is the same
    annotation `who-is-on` prints beside every holder.

    TWO DELIBERATE EXCEPTIONS, and both fail CLOSED (they may over-block, never
    free something): a DETACHED worktree has no branch to measure, and an
    unreadable ahead count cannot be shown to be zero. Both are counted.
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
            # Both keyings: a slug's number may be an ISSUE or a PR (a fold has no
            # issue at all), and recording only one leaves half the surface blind.
            holders.setdefault(f"issue:{number}", []).append(slug)
            holders.setdefault(f"pr:{number}", []).append(slug)

    for ref, _ahead in ahead_of.items():
        note(ref, 1 if _ahead < 0 else _ahead)
    for path, branch in worktrees:
        note(path, ahead_of.get(branch, 1 if branch else -1))
    return {k: sorted(set(v)) for k, v in holders.items()}


# ── the population rule (#10951) ─────────────────────────────────────────────
#: THE POPULATION RULE IS NOT DEFINED IN THIS FILE. It is the fleet's own
#: `POOL_JQ` in agent-infra's `refresh-candidates.sh`, EXTRACTED and EXECUTED by
#: `jq` — the same bytes the controller drives over the same population.
#:
#: ⛔ WHY EXECUTED AND NOT RESTATED. #10951 is what happens to a second copy: the
#: rule existed four times inline with two meanings, the P1 tier's copy excluded
#: two container kinds and the P0 tier's six, and the tier that decided a lane's
#: next unit was the WIDE one. A Python restatement here would mint a third
#: definition — and it would drift the moment either side is edited, which is the
#: defect, not a risk of it. The issue's own acceptance clause is *"used by both
#: surfaces"*, and "both surfaces run one program" is the only mechanical reading
#: of that.
#:
#: ⛔ AND IT IS FAIL-CLOSED BY OMISSION. If the script, the assignment or `jq`
#: cannot be read, every row is withheld as POP-UNMEASURED and the reason is named
#: in `unreadable` (exit 2). A rule that cannot be read must never resolve to
#: "every row is a unit": that resolution IS the defect being fixed.
POPULATION_SCRIPT_ENVS = ("NEXT_WORK_POOL_JQ_SH",)
#: `POOL_JQ='…'` is a SINGLE-QUOTED assignment on ONE line — the shipped script
#: says so in its own comment, because `pool-filter.test.sh` extracts it the same
#: way. `[^']*`, not `.*`: a line that ever gains a second quote yields NOTHING (a
#: named failure) instead of a silently truncated program, which would withhold
#: or admit rows for reasons nobody can see.
_POOL_JQ_RE = re.compile(r"^POOL_JQ='([^']*)'[ \t]*$", re.M)
#: The shipped program's three top-level `select` stages, IN ORDER. This is an
#: IDENTITY mapping — which stage means what for the reader — never a predicate:
#: the predicate is the bytes. If the program stops being exactly these three
#: stages, the attribution is dropped and withheld rows are reported as
#: NOT-A-UNIT (named, still withheld) rather than mislabelled.
POPULATION_ARMS = (REASON_ASSIGNED, REASON_DECLARED, REASON_CONTAINER)

#: What each arm must MENTION for the name to be assigned to it. Positional
#: attribution was measured to be unsafe: joining the same three stages in another
#: order keeps `len == 3` and the rejoin identity, so a reordered upstream program
#: made the CONTAINER arm read "its labels DECLARE `standing-constraint`" and the
#: ASSIGNED arm read "its title kind is a CONTAINER" — confidently, with a normal
#: provenance line, and `test_the_fixture_rule_mirrors_the_shipped_one` cannot see
#: it on a runner (that tie is the one test that skips when `scripts/` is absent).
#: The withhold is unaffected either way; the LABEL must not lie.
POPULATION_ARM_MARKERS = (".assignees", ".labels", ".title")


def _population_script(repo_root: Path) -> str:
    """Where the shipped rule lives — env override, then this checkout, then the tree.

    Mirrors `_claim_verdicts`, the file's established resolution order, with one
    correction: the `AGENT_INFRA_PATH` candidate is only consulted when that
    variable is NON-EMPTY. `Path("") / "scripts" / …` is a RELATIVE path, so on a
    box that does not export it the lookup would silently resolve against the
    process CWD — some other checkout's copy, or none, and either way a different
    answer than the caller intended.
    """
    for env in POPULATION_SCRIPT_ENVS:
        explicit = os.environ.get(env)
        if explicit:
            return explicit
    candidates = [repo_root / "scripts" / "orchestrator-harness" / "refresh-candidates.sh"]
    infra = os.environ.get("AGENT_INFRA_PATH")
    if infra:
        candidates.append(
            Path(infra) / "scripts" / "orchestrator-harness" / "refresh-candidates.sh"
        )
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return ""


def _label_objects(values: Any) -> list[dict[str, Any]]:
    """Labels in GitHub's `[{"name": …}]` shape — the ONLY shape the shipped arm reads.

    `["x"]` is the natural hand-written spelling (this file already tolerates it for
    PRs, see `_labels`) and it is FATAL to the rule: measured, the shipped label arm
    dies with `Cannot index string with string "name"`, which takes the WHOLE
    population with it rather than one row. A nameless element is normalised to the
    empty name — matched by no arm, so it can never withhold work — instead of
    killing the run.
    """
    out: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, str):
            out.append({"name": value})
        elif isinstance(value, dict):
            out.append(value if isinstance(value.get("name"), str) else {"name": ""})
    return out


def _pool_jq(script: str) -> str:
    """The `POOL_JQ` assignment's VALUE, byte for byte (it is single-quoted)."""
    try:
        text = Path(script).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = _POOL_JQ_RE.search(text)
    return match.group(1) if match else ""


def _top_level_stages(prog: str) -> list[str]:
    """Split a jq program at top-level `|` — never inside a string, `(…)` or `[…]`.

    ⛔ A PLAIN `prog.split("|")` IS WRONG HERE, measured: the shipped label arm
    contains a NESTED pipe inside `[$L[]|. as $n|select(…)]`, so a naive split
    yields four fragments and mis-attributes every row. The scanner tracks string
    literals (with backslash escapes) and bracket depth; the caller RE-CHECKS the
    split by rejoining it, so a scanner bug cannot silently mislabel — it can only
    lose the attribution.
    """
    stages: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    for char in prog:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            current.append(char)
            continue
        if char == '"':
            in_string = True
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "|" and depth == 0:
            stages.append("".join(current))
            current = []
            continue
        current.append(char)
    stages.append("".join(current))
    return stages


def _arm_indexes(stages: list[str]) -> dict[str, int] | None:
    """Map each REASON to the stage index that PROVES it is that arm — by content.

    Positional attribution was measured to be unsafe: joining the same three stages
    in another order keeps `len == 3` and the rejoin identity, so a reordered
    upstream program made the CONTAINER arm read "its labels DECLARE
    `standing-constraint`" and the ASSIGNED arm read "its title kind is a
    CONTAINER" — confidently, with a normal provenance line. Each marker must
    appear in exactly one stage; two stages mentioning `.labels` (or none) is not an
    attribution but a guess, so it returns None and the rows fall back to
    NOT-A-UNIT. The withhold is unaffected either way; the LABEL must not lie.
    """
    found: dict[str, int] = {}
    for reason, marker in zip(POPULATION_ARMS, POPULATION_ARM_MARKERS, strict=True):
        owners = [i for i, stage in enumerate(stages) if marker in stage]
        if len(owners) != 1:
            return None
        found[reason] = owners[0]
    if len(set(found.values())) != len(POPULATION_ARMS):
        return None
    return found


def _population_program(prog: str) -> tuple[str, bool]:
    """One jq invocation for every row: what the rule KEEPS, and which arm withheld."""
    stages = _top_level_stages(prog)
    attributable = (
        len(stages) == len(POPULATION_ARMS)
        and "|".join(stages) == prog
        and _arm_indexes(stages) is not None
    )
    body = "reduce .[] as $r ([]; . + [{n: $r.number, kept: ([$r | " + prog + "]|length)"
    if attributable:
        body += "".join(
            f", a{index}: ([$r | {stage}]|length)" for index, stage in enumerate(stages)
        )
    body += "}])"
    return body, attributable


def _withhold_all(snap: Snapshot) -> None:
    """A rule that could not be read withholds EVERY row — never none of them.

    The polarity is the whole point: an unmeasured eligibility that resolves to
    "every row is a unit" is exactly the state #10951 was filed about, arrived at
    by accident instead of by omission. Rows keep their place in the report so the
    queue is not silently empty; they carry POP-UNMEASURED and no command.
    """
    for row in snap.issues:
        snap.population[str(row["number"])] = REASON_POP_UNMEASURED


def _population_verdicts(repo_root: Path, snap: Snapshot) -> None:
    """Fill `snap.population` by DRIVING the fleet's own predicate over our own rows.

    One `jq` call for the whole population (not one per row), because the arm is a
    property of the ROW and not of the run.
    """
    # ⛔ THE SWITCH IS DECIDED BEFORE ANYTHING ELSE (#10951 review, P3). It used to
    # sit AFTER the zero-row early return, so an empty queue with the rule DISARMED
    # printed the rule's path and digest — no ⚠️ marker, because the marker keys on
    # `OFF` — i.e. the one artifact a closer audits said the deployed rule had
    # decided the population, which is the opposite of the switch state. (Same
    # defect class as the refused-map provenance line.)
    # ⛔ THE OFF-SWITCH IS NAMED AND LOUD (#10951). When it is off, this tool
    # consults NO population rule at all: every collected row is admitted on its
    # title and labels exactly as it was before this change — the pre-#10951
    # behaviour, in which an epic, a `design(…)` decision, a `measure(…)` and
    # tortoise#10175 ("the cap is HELD deliberately — never override it") all
    # receive a runnable dispatch command. It exists so the change can be reverted
    # at runtime by a box that cannot carry it, and it is REPORTED, never silent.
    if os.environ.get("NEXT_WORK_POPULATION", "1").strip().lower() in (
        "0", "off", "no", "false",
    ):
        snap.population_source = "OFF — NEXT_WORK_POPULATION disabled; no row was checked"
        return

    if not snap.issues:
        # Even an EMPTY queue names the rule it would have used: "the source is
        # printed, ALWAYS" is a property of the render, not of a non-empty run.
        script = _population_script(repo_root)
        prog = _pool_jq(script) if script else ""
        if prog:
            digest = hashlib.sha256(prog.encode()).hexdigest()[:12]
            snap.population_source = f"{script} · POOL_JQ sha256:{digest} (no rows)"
        else:
            # ⛔ THE CLAIM IS "THE SOURCE IS PRINTED", NOT "THE SOURCE IS FOUND"
            # (#10951 review, round 6). With no rule and no rows the old branch
            # printed NOTHING, so "ALWAYS" was not what the code did. Zero rows
            # cannot be withheld either way, so this is a report — but a silent
            # report is the one thing this seam must never be.
            snap.population_source = "UNREADABLE — no work-unit rule resolvable (no rows)"
        return

    script = _population_script(repo_root)
    if not script:
        _withhold_all(snap)
        snap.unreadable.append(
            "population: refresh-candidates.sh was not found (tried this checkout's "
            "scripts/orchestrator-harness/, then $AGENT_INFRA_PATH) — the fleet's own "
            "work-unit rule cannot be driven, so no row's eligibility is provable"
        )
        snap.population_source = "UNREADABLE — no refresh-candidates.sh"
        return
    prog = _pool_jq(script)
    if not prog:
        _withhold_all(snap)
        snap.unreadable.append(
            f"population: {script} carries no usable `POOL_JQ='…'` assignment — the "
            "work-unit rule cannot be driven"
        )
        snap.population_source = f"UNREADABLE — no POOL_JQ in {script}"
        return

    program, attributable = _population_program(prog)
    payload = [
        {
            "number": int(row["number"]),
            "title": str(row.get("title") or ""),
            "labels": _label_objects(row.get("labels")),
            "assignees": row.get("assignees") or [],
        }
        for row in snap.issues
    ]
    proc = _run(["jq", "-c", program], stdin_text=json.dumps(payload), timeout=GIT_TIMEOUT)
    if proc.returncode != 0:
        _withhold_all(snap)
        # A `jq` that refuses is not a population that passed. `pool-filter.test.sh`
        # runs a MUTATION arm for the same reason: a predicate that fails OPEN while
        # looking stricter is the failure mode this whole rule is exposed to.
        snap.unreadable.append(
            "population: the shipped POOL_JQ could not be driven over "
            f"{len(payload)} row(s) ({proc.stderr.strip()[:160] or 'no stderr'}) — "
            "eligibility is UNMEASURED, not granted"
        )
        snap.population_source = f"UNREADABLE — jq refused {Path(script).name}"
        return
    try:
        verdicts = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        _withhold_all(snap)
        snap.unreadable.append(f"population: jq emitted non-JSON ({exc})")
        snap.population_source = "UNREADABLE — jq emitted non-JSON"
        return

    stages = _top_level_stages(prog)
    by_reason = _arm_indexes(stages) if attributable else None
    for row in verdicts:
        if int(row.get("kept") or 0):
            continue
        reason = REASON_NOT_A_UNIT
        if by_reason:
            # The row's FIRST failing stage identifies the arm — but the NAME comes
            # from that stage's content (`by_reason`), never from its position.
            for index in range(len(stages)):
                if int(row.get(f"a{index}") or 0):
                    continue
                reason = next(
                    (r for r, i in by_reason.items() if i == index), REASON_NOT_A_UNIT
                )
                break
        snap.population[str(row.get("n"))] = reason
    digest = hashlib.sha256(prog.encode()).hexdigest()[:12]
    snap.population_source = f"{script} · POOL_JQ sha256:{digest}"


def _population_note(reason: str, snap: Snapshot) -> str:
    """One sentence a reader can act on, for a row the population rule withheld."""
    why = {
        REASON_ASSIGNED: "it is already assigned to a human",
        REASON_DECLARED: (
            "its labels DECLARE `standing-constraint` — a standing constraint is not work, "
            "and the declaration is the only honest marker (the title cannot carry it)"
        ),
        REASON_CONTAINER: (
            "its title kind is a CONTAINER — the kinds are enumerated in the shipped rule "
            "itself, never here, so this note cannot drift from the verdict"
        ),
        REASON_POP_UNMEASURED: (
            "the population rule could not be READ, so its eligibility is UNMEASURED — "
            "not granted"
        ),
    }.get(reason, "the fleet's work-unit rule withholds it")
    source = f" [{snap.population_source}]" if snap.population_source else ""
    return f"withheld by the fleet's population rule ({reason}): {why}{source}"


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
    try:
        proc = subprocess.run(
            ["bash", "-c", script_body],
            input="\n".join(ref_list) + "\n",
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=GH_TIMEOUT * 2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # ⛔ A HUNG `git fetch` INSIDE claims.sh MUST EXIT 2, NOT CRASH. This was
        # the only subprocess in the module without error handling, and
        # `claim_snapshot` does a network fetch — so a slow one escaped as
        # TimeoutExpired, produced a traceback and exit 1 with `unreadable`
        # EMPTY, which is the one shape that reads as "the queue was read and
        # answered". A timeout is "could not check".
        snap.unreadable.append(f"GH-TIMEOUT(claims.sh): {str(exc)[:120]}")
        return
    finally:
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
        target = head
        # ⛔ THE MEASUREMENT MUST BE BOUND TO THE HEAD IT IS FILED UNDER. The fetch
        # used to take `refs/pull/N/head` and merge THAT ref, never comparing it to
        # `pr["head"]` — so a PR pushed between `list_pulls` and this call was
        # measured at a DIFFERENT commit than the one whose check-runs and
        # attestation were read, and `merge_tree` is keyed by PR number only.
        # Measured: snapshot head 6aaaf759 conflicted with main while
        # refs/pull/7/head (782737d0) merged clean → recorded `clean: True`.
        fetched = _run(["git", "fetch", "--no-tags", "origin", head], timeout=GIT_TIMEOUT, cwd=repo_root)
        if fetched.returncode != 0:
            fetched = _run(
                ["git", "fetch", "--no-tags", "origin",
                 f"+refs/pull/{number}/head:refs/pi-next-work/pr-{number}"],
                timeout=GIT_TIMEOUT,
                cwd=repo_root,
            )
            if fetched.returncode == 0:
                tip = _run(
                    ["git", "rev-parse", f"refs/pi-next-work/pr-{number}"],
                    timeout=GIT_TIMEOUT,
                    cwd=repo_root,
                )
                got = (tip.stdout or "").strip()
                if got != head:  # an EMPTY rev-parse is a mismatch, not a match
                    snap.merge_tree[str(number)] = {
                        "clean": None,
                        "why": (
                            f"the PR head moved during the sweep — snapshot says {head[:8]}, "
                            f"the remote has {got[:8]} — the tree that would merge was not measured"
                        ),
                    }
                    return
                target = f"refs/pi-next-work/pr-{number}"
        if fetched.returncode != 0:
            snap.merge_tree[str(number)] = {
                "clean": None,
                "why": f"head {head[:8]} not available locally and the fetch failed",
            }
            return
        head = target
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
    snap = Snapshot(repo=repo, base=base, claim_ns=CLAIM_NS)
    # Provenance only — the decision never reads it. Bounded like every other
    # subprocess here: the first version ran `date` with NO timeout and no error
    # handling, so a host without it raised FileNotFoundError and exited 1 with a
    # traceback instead of the documented 2.
    stamp = _run(["date", "+%s"], timeout=5)
    snap.collected_at = int(stamp.stdout.strip() or 0) if stamp.returncode == 0 and stamp.stdout.strip().isdigit() else 0

    try:
        # ⛔ PINNED TO THE CHECKOUT, NOT THE PROCESS CWD. `load_config_text(None)`
        # shells `git show origin/main:.mergify.yml` with no cwd, so the gate came
        # from whatever repository the process was standing in — and was labelled
        # `origin/main:.mergify.yml` either way, indistinguishable from the right
        # one. Measured by a reviewer: from another Mergify repo it returned THAT
        # repo's conditions silently, so positions were computed against a foreign
        # gate. The read is pinned; `parse_config` (the shared parser) is unchanged.
        shown = _run(
            ["git", "show", f"origin/{base}:.mergify.yml"],
            timeout=GIT_TIMEOUT,
            cwd=repo_root,
        )
        if shown.returncode == 0 and shown.stdout.strip():
            text, source = shown.stdout, f"origin/{base}:.mergify.yml ({repo})"
        else:
            on_disk = repo_root / ".mergify.yml"
            text = on_disk.read_text(encoding="utf-8")
            source = f"{on_disk} (FALLBACK — origin/{base} unreadable)"
            # ⛔ A FAILED READ IS NOT A MEASURED STATE. Reading the checkout's own
            # copy may pick up an uncommitted edit — a removed `check-success`, a
            # dropped `-label=superseded-by-fold` — and then "no unmet conditions"
            # means READY-TO-LAND with a merge command, while this module's own
            # exit-2 list says an unreadable gate is INCOMPLETE. The positions are
            # still computed (from the fallback, labelled), but the run refuses.
            snap.unreadable.append(
                f"queue gate: origin/{base}:.mergify.yml could not be read — fell back to "
                f"{on_disk}, which may be an uncommitted local edit"
            )
        cfg = parse_config(text)
        snap.gate_text = text
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

    # ⛔ ONE ROW PER ISSUE. Two searches, one per label — an issue carrying BOTH
    # labels appeared twice with the same `key`, so the population was
    # overcounted and `--json` emitted two units a consumer keys as one. The
    # higher priority wins, because the ordering is priority-first anyway.
    found: list[dict[str, Any]] = []
    for label in PRIORITIES:
        found.extend(_search_issues(client, repo, label, snap))
    snap.issues = dedupe_issues(found)

    try:
        # ⛔ NO BASE FILTER. `queue_resweep.list_pulls` takes `base=main`, so a PR
        # targeting another branch was absent from this list — the issue whose only
        # open PR is `release/1.x` read NO-PR and got a `lane.sh` dispatch, while
        # `_position_for_pr` has a `base=main (actual …)` branch that could never
        # fire. Every open PR is enumerated; the gate decides the base.
        pulls = client._get_list(f"repos/{repo}/pulls", "state=open")
    except GhError as exc:
        pulls = []
        _unreadable(snap, "open pulls", exc)
    snap.pulls = [
        {
            "number": int(p["number"]),
            "head": str(((p.get("head") or {}).get("sha")) or ""),
            # ⛔ THE BRANCH IS PART OF THE RECORD. Without this the emitted rail
            # command could only ever say "branch unknown" on a LIVE run — the
            # field existed on `Unit`, nothing wrote it, and the only test planted
            # it on a synthetic snapshot, so the fix was inert where it mattered.
            "head_ref": str(((p.get("head") or {}).get("ref")) or ""),
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

    # The population rule, DRIVEN (not restated) over the rows just collected. It
    # runs before `build_units`, which is a pure function of the snapshot, and it
    # is what withholds a row on BOTH branches of `build_units`.
    _population_verdicts(repo_root, snap)

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
    # ⛔ A FOLD THE LINE DECLARES IS A UNIT — SO MEASURE IT. `build_units` accepts
    # `bool(fold-constituents)` as fold evidence, but this filter only looked at
    # issue links and the LABEL, so a line-only fold was never attested and never
    # merge-tested: `merge_tree` stayed empty, the abstention forced NEEDS-TEST
    # forever, and the row asserted "awaiting checks … until its head reports" over
    # green checks and "head not available locally" when no fetch was attempted.
    # That is the COMMON live shape — the label is not applied automatically yet
    # (`docs/ops/batch-integration.md`), which the tool's own QUEUE NOTE reports.
    linked_prs = {
        int(pr["number"])
        for pr in snap.pulls
        if (pr.get("closing_issues") or pr.get("refs_issues"))
        or FOLD_LABEL in _labels(pr)
        or _fold_constituents(pr)
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
    gate_label = snap.config_source or (
        "(parsed, source unnamed)" if cfg is not None else "UNREADABLE"
    )
    out.append(f"next-work — repo {repo} · base {snap.base} · gate {gate_label}")
    out.append(
        f"read: {len(snap.issues)} open P0/P1 issue(s) · {len(snap.pulls)} open PR(s) · "
        f"{len(snap.check_runs)} head(s) with check-runs · {len(snap.claims)} claim key(s)"
    )
    if snap.unreadable:
        for u in snap.unreadable:
            out.append(f"  ⚠️ UNREADABLE: {u}")
    # ⛔ THE POPULATION SOURCE IS PRINTED, ALWAYS (#10951). The issue's own closure
    # note asks a closer to verify that the DEPLOYED copy carries the merged rule
    # before recording anything — and a run that cannot name WHICH bytes decided
    # its population (or that no bytes did) is a run whose refusal/consent cannot
    # be audited. `OFF` is called out separately because it is a deliberate return
    # to the pre-#10951 behaviour, not a measurement.
    if snap.population_source:
        marker = "⚠️ " if snap.population_source.startswith(("OFF", "UNREADABLE", "ABSENT")) else ""
        out.append(f"  {marker}population: {snap.population_source}")
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
        # ⛔ NAME THE CLASS THAT ACTUALLY WITHHELD (#10951 review, follow-up). The
        # sentence listed "held, superseded, or a duplicate" — so a queue whose whole
        # population was withheld by the work-unit rule was described by three causes,
        # none of which applied, which is the same misdescription the stolen-target
        # finding was filed for.
        # ⛔ BY PRESENCE, LIKE THE REFUSAL ITSELF (#10951 review, P3). Selecting on
        # the tuple re-created the misdescription on exactly the input the presence
        # flag exists for: a replay carrying a NEWER verdict string was refused
        # correctly and then reported as "held, superseded, or a duplicate".
        withholds = sorted(
            {u.reason for u in units if u.withheld_by_population or u.reason in REASONS_WITHHELD}
        )
        # ⛔ TWO VOCABULARIES, TWO SENTENCES (#10951 review, F3). A CLAIMED or
        # SUPERSEDED row is described by the sentence that belongs to it; putting it
        # in the population sentence made a fold-only queue read "a queue whose rows
        # are containers, declared constraints, assigned…", which is not why IT is
        # not pickable. Only the population reasons get the population parenthetical.
        population_reasons = {u.reason for u in units if u.withheld_by_population}
        if withholds and population_reasons:
            out.append(
                "  NONE — no unit is pickable: every measured one is withheld as "
                + ", ".join(withholds)
                + " (a queue whose rows are containers, declared constraints or otherwise "
                "not units is an ANSWER, not an error)."
            )
        elif withholds:
            out.append(
                "  NONE — no unit is pickable: every measured one is withheld as "
                + ", ".join(withholds)
                + ". That is an answer, not an error."
            )
        else:
            out.append(
                "  NONE — no unit is pickable: every measured one is held, superseded, or a "
                "duplicate of a row already offered (or the queue is empty). That is an "
                "answer, not an error."
            )
    else:
        out.append(f"  TOP       {_label(pick)}  ({pick.priority}, {pick.reason}, position {pick.position})")
        cmd = next_command(pick, repo)
        if cmd:
            out.append(f"  COMMAND   {cmd}")
        else:
            out.append(f"  ACTION    {next_action(pick, repo) or 'none'}")
        if runnable is not None and runnable.key != pick.key:
            out.append(f"  RUNNABLE  {_label(runnable)}  ({runnable.priority}, {runnable.reason})")
            out.append(f"            {next_command(runnable, repo)}")
            # ⛔ NAME THE ACTUAL REASON. This said "its head is waiting on a"
            # "runner" for EVERY top row without a command — so a NEEDS-REVIEW top
            # row printed "dispatch a reviewer at #N" and, one line later, told the
            # operator to wait for a runner that would never move it.
            if pick.draft:
                why = "the rank leader is a DRAFT — the author's, see ACTION"
            elif any(d.startswith("gate-refusal:") for d in pick.detail):
                why = "the rank leader is GATE-REFUSED (see ACTION) — a human edits the PR"
            elif any(d.startswith("review-gate-red:") for d in pick.detail):
                why = "the rank leader's review gate is RED with a bound record — a human decides"
            elif pick.position == "NEEDS-REVIEW":
                why = "the rank leader needs a REVIEW (see ACTION), so nothing runs until it has one"
            elif pick.position == "NEEDS-TEST" and any(
                "merge-tree: UNMEASURED" in d for d in pick.detail
            ):
                why = (
                    "the rank leader's merge tree could not be MEASURED (see ACTION) — "
                    "nothing is in a runner queue"
                )
            elif pick.position == "NEEDS-TEST" and any("no check-run" in d for d in pick.detail):
                why = "a required check has NEVER REPORTED for the rank leader (see ACTION)"
            elif pick.position == "NEEDS-TEST":
                why = "the rank leader's head is waiting on a runner"
            else:
                why = f"the rank leader has no runnable command (position {pick.position})"
            out.append(f"            ^ {why}; this is the first unit a lane can act on NOW.")
        elif runnable is None:
            out.append("  RUNNABLE  none — no pickable unit has a runnable command right now.")
        out.append("  CONSUME   The output is consumed by whoever holds an allowed channel.")
        out.append("            This tool DECIDES; it never acts, and it installs no timer —")
        out.append("            cmux authorises by ancestry, so a launchd timer cannot act on a pane.")
    out.append("")
    unlabelled_folds = _title_looks_like_fold_without_evidence(snap)
    # ⛔ TWO POPULATIONS, NEVER ONE COUNT OVER BOTH. "of them" means the TITLE-ONLY
    # folds; the refused-line set is a different population (a LABELLED fold can
    # carry an unparseable line too). The first version counted every pull, so the
    # note printed "1 open PR(s) batch by TITLE ALONE (#912)" and then "2 of them …
    # (#911, #912)" — a count larger than the population it referred to, naming a
    # PR that was not in it.
    refused_all = sorted(int(pr["number"]) for pr in snap.pulls if _fold_line_refused(pr))
    refused_unlabelled = [n for n in refused_all if n in unlabelled_folds]
    labelled_refused = [n for n in refused_all if n not in unlabelled_folds]
    if unlabelled_folds or refused_all:
        out.append("QUEUE NOTE — the fold label path is INERT on this queue")
    if unlabelled_folds:
        out.append(
            f"  {len(unlabelled_folds)} open PR(s) batch by TITLE ALONE "
            f"({', '.join('#' + str(n) for n in unlabelled_folds[:8])}"
            f"{' …' if len(unlabelled_folds) > 8 else ''}) and carry neither `{FOLD_LABEL}` nor a "
            "usable `fold-constituents:` line."
        )
        if refused_unlabelled:
            # ⛔ A LINE THAT IS PRESENT BUT DOES NOT PARSE IS NOT "NO LINE". The note
            # used to say these PRs carry no line at all, sending a reader to add
            # one that is already there in a form `batch_open.py` REFUSES (all
            # tokens must be `#?\d+`, or the whole line is refused — "a partial
            # parse is how the wrong PR gets closed"). Name it.
            out.append(
                f"  ⚠️ {len(refused_unlabelled)} of them DO carry a `fold-constituents:` line that "
                f"does not parse ({', '.join('#' + str(n) for n in refused_unlabelled[:5])}) — that "
                "line is refused whole, exactly as `batch_open.py` refuses it, so it declares no fold."
            )
    if labelled_refused:
        # A LABELLED fold whose declared line is refused declares NOTHING, and its
        # row would otherwise carry no trace: the label says "fold", the unit has no
        # constituents, and the reason is nowhere.
        out.append(
            f"  ⚠️ {len(labelled_refused)} other open PR(s) carry a non-parsing "
            f"`fold-constituents:` line ({', '.join('#' + str(n) for n in labelled_refused[:5])}) — "
            "declared membership is refused whole, so nothing is superseded by them."
        )
    if unlabelled_folds or refused_all:
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
    if snap.unreadable:
        # ⛔ NOTHING IS "PICKABLE" ON A REFUSED RUN — `--json` already says
        # `pickable: false` for every row, and the footer contradicted it.
        out.append(f"TOP 0 PICKABLE of {len(units)} unit(s) — this run is REFUSED")
    else:
        out.append(
            f"TOP {len(shown)} PICKABLE of {len(units)} unit(s) "
            "(rank order; held, superseded and duplicate rows excluded)"
        )
    # ⛔ A REFUSED RUN DELIVERS NO COMMAND IN EITHER FORMAT. The `--json` path
    # nulled every `next_command` while this footer still printed them, so a text
    # consumer grepping for a command could find one on a run the tool itself
    # calls REFUSED — the same fail-open, one format over.
    # ⛔ THE COMMAND IS PRINTED WHOLE. This row used to carry `cmd[:96]`, which cut a real
    # command MID-TOKEN with no marker: measured with a live-shaped branch, the footer said
    # `merge-one.sh . daniel-ospina/tortoise 1001 fix/10955-queue-con` — a branch that does not
    # exist, in the block `tests/` itself treats as a command surface. A truncated command is
    # worse than no command: it is runnable, plausible and wrong, and the reader has no signal
    # that a character is missing. An earlier fix moved only the `NEXT ACTION` `COMMAND` line
    # to the whole string and left this footer site behind, so the defect survived a review
    # that recorded it as closed.
    for i, u in enumerate(shown, 1):
        cmd = "" if snap.unreadable else (next_command(u, repo) or "")
        out.append(f"  {i}. {_label(u):<12} {u.priority:<11} {u.reason:<19} {cmd}".rstrip())
    out.append(
        "ordering key = (priority, position, (kind, number)) — total, and no clock enters the decision, "
        "so a re-run picks the same row."
    )
    return "\n".join(out), pick


def _label(u: Unit) -> str:
    return f"#{u.issue}" if u.kind == "issue" else f"fold PR #{u.fold_pr}"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. ANY structural failure inside is INCOMPLETE (exit 2).

    ⛔ THE GUARD IS THE OUTERMOST FRAME, NOT A BLOCK INSIDE IT. Wrapping only
    `build_units`/`render` left `Snapshot.from_json` (which iterates `claims` and
    `attestation`) and the `--from` gate branch escaping as a traceback with the
    interpreter's exit 1 — which this tool documents as "the queue was read and
    answered: nothing is pickable". Measured by a verifier on this change:
    `claims: ["x"]`, `claims: "x"`, `attestation: [1, 2]`, `gate: "q"`, `gate: []`
    and `gate_text: 7` each parsed as JSON, each exited 1. A mis-shaped snapshot is
    "could not check", never "nothing to do".
    """
    try:
        rc = _main(argv)
        # ⛔ THE FLUSH IS INSIDE THE TRY, BECAUSE THE FLUSH IS WHERE IT BREAKS.
        # `print` only fills the buffer; the closed-pipe error surfaces when the
        # buffer is written — i.e. at the interpreter's SHUTDOWN flush, which
        # CPython turns into exit **120** (measured: 8,489 B and 58,653 B both
        # 120, <8 KB 0, the same data as `--json` 2). Catching around `_main`
        # alone never sees it.
        sys.stdout.flush()
        return rc
    except BrokenPipeError:
        # `next_work.py | head -1` closes the pipe. The interpreter's own exit 1
        # would read as "the queue was read and nothing is pickable" on a run that
        # was never written — the same mis-grade as the mis-shaped snapshot.
        # ⛔ CATCHING IT IS NOT ENOUGH: the interpreter's SHUTDOWN FLUSH re-raises
        # over the caught error and CPython turns that into exit 120 — measured
        # (8,489 B → 120, 58,653 B → 120, <8 KB → 0), an undocumented code on the
        # same command family. The canonical epilogue points stdout at devnull so
        # the flush has somewhere to go.
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 2
    except SystemExit as exc:
        # A deep `_Incomplete()` is already a code; anything else that exits here is
        # still "could not check" rather than the interpreter's 1.
        code = exc.code if isinstance(exc.code, int) else 2
        return code if code != 1 else 2
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError) as exc:
        print(
            f"next_work: INCOMPLETE — the snapshot could not be decided "
            f"({type(exc).__name__}: {exc}). exit 2 is 'could not check'.",
            file=sys.stderr,
        )
        return 2


def _main(argv: Sequence[str] | None = None) -> int:
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
            "command for the top row — or, when the top row needs a HUMAN (a review, or a gate "
            "refusal: another base, a missing required label, an unreadable base), is waiting on "
            "a runner, has an UNMEASURED merge tree, a required check that never REPORTED, or is a "
            "DRAFT, the ACTION for it plus the first unit below it that DOES have a command.\n\n"
            "IDEMPOTENT BY CONSTRUCTION: the ordering key is total — (priority, position, "
            "(kind, number)), where an issue is (0, n) and a fold is (1, pr) — "
            "so two runs on one snapshot agree on the row. That is AGREEMENT, not exclusion: two "
            "concurrent lanes would pick the same unit, and this tool writes no claim — what stops "
            "a second lane is `refs/claims`, written by the one that acts. NO clock enters the "
            "decision: a claim lease's polarity is decided by claims.sh "
            "when the snapshot is collected and recorded in it, so a replay is exact with no clock "
            "seam to forget.\n\n"
            "Reads: label listing via search/issues (paginated, never page-capped), the fleet's "
            "work-unit rule (the POOL_JQ assignment in agent-infra's "
            "scripts/orchestrator-harness/refresh-candidates.sh, driven through `jq` — the "
            "`jq` binary is a RUNTIME DEPENDENCY of this tool), the repo's own "
            "queue gate from .mergify.yml (via tools/queue_resweep.py), refs/claims (via "
            "scripts/orchestrator-harness/claims.sh), record-review.sh --check-carry, and "
            "git merge-tree --write-tree (which fetches a PR head it does not have locally into "
            "refs/pi-next-work/pr-<n> after trying `git fetch origin <sha>`; `claims.sh` also "
            "maintains refs/claims-cache/*). Never the `mergeable` "
            "flag; never comment prose."
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
        # ⛔ A MISSING OR MALFORMED SNAPSHOT IS exit 2, NOT exit 1. Unhandled, a
        # typo'd path or a truncated file raised FileNotFoundError/JSONDecodeError
        # — a traceback and exit 1, which this tool documents as "the queue was
        # read and NO unit is pickable: that is an answer". A consumer keying on
        # the exit code read "nothing to do" from an input that was never read.
        try:
            raw = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"next_work: INCOMPLETE — the snapshot {args.from_file} could not be read "
                f"({exc}). exit 2 is 'could not check'; exit 1 would mean the queue was read "
                "and answered.",
                file=sys.stderr,
            )
            return 2
        if not isinstance(raw, dict):
            print(
                f"next_work: INCOMPLETE — {args.from_file} is not a snapshot object",
                file=sys.stderr,
            )
            return 2
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
            # ⛔ THE SAME PARSER ON BOTH PATHS. Rebuilding a `QueueConfig` by hand
            # from the parsed fields skipped every invariant `parse_config`
            # enforces — a snapshot whose gate named NO required checks produced
            # "no unmet conditions", so every PR was READY-TO-LAND with a command
            # while the live path exits 2 on that very config. Replay the TEXT
            # through the parser; if the snapshot has no text (an older writer),
            # apply the one invariant that matters here: a gate with no checks is
            # not a gate.
            if snap.gate_text.strip():
                try:
                    cfg = parse_config(snap.gate_text)
                except ConfigError as exc:
                    cfg = None
                    snap.unreadable.append(f"queue gate: the snapshot's text does not parse ({exc})")
            elif not (snap.gate.get("required_checks") or ()):
                cfg = None
                snap.unreadable.append(
                    "queue gate: the snapshot carries no gate text and names no required check — "
                    "a gate that requires nothing makes every PR landable"
                )
            else:
                cfg = QueueConfig(
                    queue_name=str(snap.gate.get("queue_name") or "default"),
                    base=str(snap.gate.get("base") or "main"),
                    required_checks=tuple(snap.gate.get("required_checks") or ()),
                    required_labels=tuple(snap.gate.get("required_labels") or ()),
                    forbidden_labels=tuple(snap.gate.get("forbidden_labels") or ()),
                )
            if cfg is not None:
                snap.config_source = f"{snap.config_source} (from snapshot)"
        # ⛔ THE TARGET REPO TRAVELS WITH THE DATA. `repo = args.repo or snap.repo`
        # let a replay of a snapshot collected for repo A emit
        # `merge-one.sh . <repo B> 1001 …` at exit 0 — every row, head, attestation
        # and claim read from A, the command naming B, and `merge-one.sh` does not
        # re-assert the slug against the checkout's origin. This is the same hazard
        # `claim_ns` already closes ("the namespace travels with the data"), and the
        # one `dispatch-merge.sh` exists to stop (a lane built for the wrong repo).
        if snap.repo and args.repo and args.repo.strip() != snap.repo.strip():
            print(
                f"next_work: INCOMPLETE — the snapshot was collected for {snap.repo!r} but "
                f"--repo says {args.repo!r}. A command aimed at the other repo is a wrong-repo "
                "dispatch, so nothing is emitted.",
                file=sys.stderr,
            )
            return 2
        repo = args.repo or snap.repo
        if not repo:
            # No slug = no addressable command, and every emit site depends on it.
            snap.unreadable.append(
                "repo: no target repo could be resolved — every emitted command needs one"
            )
    else:
        repo = _repo_slug(args.repo, repo_root)
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
                    # ⛔ THE MACHINE CONSUMER NEEDS THE PROVENANCE TOO (#10951 review,
                    # follow-up). Without it `--json` could not distinguish a real
                    # measurement from the OFF switch, an UNREADABLE rule or an ABSENT
                    # replay — the refusal/consent decision was readable only in the
                    # human render.
                    "population_source": snap.population_source,
                    "population": snap.population,
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
                            "withheld_by_population": u.withheld_by_population,
                            "holder": u.holder,
                            "detail": u.detail,
                            "pickable": False if refused else u.pickable,
                            "next_command": None if refused else next_command(u, repo),
                            "next_action": None if refused else next_action(u, repo),
                            "duplicate_of": u.duplicate_of,
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
