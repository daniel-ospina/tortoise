#!/usr/bin/env python3
"""embedded_evidence.py — the embedded-lane evidence producer (#3827, RED half).

One command runs a named, recorded selection N times in fresh subprocesses at a
pinned commit and **classifies every run (never counts it)**, demonstrates a RED
for the same selection at a named pre-fix ref in the same lane, and **enforces**
`closes_issue` as an exit code — so the embedded family's exit evidence becomes a
receipt a reviewer can falsify.

This is the RED half (PR #1). The GREEN half (N consecutive green at the fixed
commit) has no referent while the family lane is red; see the plan doc
`docs/plans/2026-09-17-3827-embedded-lane-evidence-producer.md`.

It is a COMPOSITION, not a rebuild: it calls the existing primitives
(`tools/skip-guard.py::emit_manifest`, `tools/ci_selection.py::select` +
`load_manifest` + `carve_out_files`, `tools/embedded_orphans.py::census`,
`tools/testdb_canary_classify.py`'s vocabulary) and adds the pin, the paired red,
the cause label and the closing rule.

⛔ Certification rules (verdict 2026-09-18):
  R1 (D23) — certification binds to the SHIPPING surface (`tortoise_search` /
             `tortoise_recall`), never an internal helper.
  R2 (D24) — the certificate is bound to the reviewed head SHA and re-run after
             any post-review edit; the required mutation operator is statement
             deletion of the fix's own population/edit branch.
  Context: issue #3888 shipped 17 tests + a clean review + VGATE PASS, yet
  deleting the population branch at `sdk.py:13202`/`:13239-13245` left the suite
  GREEN while `tortoise_search` and `tortoise_recall` both returned
  `sessionId:''`. The manual proof covered the READ path only.

Usage:
  python3 tools/embedded_evidence.py run  --selection family [--n 3] [--ref <sha>]
  python3 tools/embedded_evidence.py red  --selection family [--ref <sha>]
  python3 tools/embedded_evidence.py classify --redis-log <path>

Exit codes: 0 = all conjuncts hold (closing); 1 = violation (a red / moved tree /
unattributable); 2 = environment error (measurement impossible); 3 = NOT-CLOSING.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# The DECLARED LOCAL N (D3827-b / D13). No source makes any N canonical: the
# four practitioner sources describe detect -> quarantine -> fix and do NOT fix
# N. N=10 is OURS, used to CERTIFY where the field uses N to DENY.
# ---------------------------------------------------------------------------
DEFAULT_N = 10
DEFAULT_MAX_RUNS = 50
DEFAULT_RUN_TIMEOUT_S = 900

# ---------------------------------------------------------------------------
# Selection (D5) — the family's evidence selection.
# ---------------------------------------------------------------------------
FAMILY_REPRODUCERS = (
    "tests/test_dr_endpoints.py",
    "tests/test_hosted_backup.py",
    "tests/test_backup_e2e.py",
)
# DERIVED from FAMILY_REPRODUCERS — never a second literal of its first entry.
# Re-typing the path is how the two could drift (one list edited, the other not).
MANDATORY_REPRODUCER = FAMILY_REPRODUCERS[0]
# The assert that stood here (`MANDATORY_REPRODUCER in FAMILY_REPRODUCERS`) was a
# TAUTOLOGY: `MANDATORY_REPRODUCER` IS `FAMILY_REPRODUCERS[0]`, so membership held
# for ANY value of the tuple and the assert could never fire. It read as protection
# and supplied none. The invariant that CAN fail is that the selection declares each
# reproducer once. A duplicate cannot be caught downstream: pytest de-duplicates a
# repeated path, and `_manifest_receipt` counts the nodeids of whatever pytest
# collected — so a duplicated file list yields the SAME count as the unduplicated
# one and neither guard can see the duplicate. This assert is therefore the only
# place a duplicate declaration is caught, and the defect it prevents is a recorded
# `selection.files` that names one reproducer twice while claiming to name a set of
# them.
assert len(set(FAMILY_REPRODUCERS)) == len(FAMILY_REPRODUCERS), (
    "FAMILY_REPRODUCERS contains a duplicate entry, so the recorded selection "
    f"names one file twice: {FAMILY_REPRODUCERS}"
)
DEFAULT_MARKER = "not track_b and not live"

# ---------------------------------------------------------------------------
# Run buckets (#3827, P1-B) — the CLOSED vocabulary the runner can emit and every
# consumer classifies with. ONE canonical definition; the passing set and the red
# set are DERIVED from it, never re-spelled at each call site.
#
# The defect this replaces: `slow-run` appeared in four consumer sites but NO
# producer could emit it — a bucket treated as a valid pass yet never produced is
# a silent hole. It was REMOVED rather than wired to a producer: the RED half
# records no run-duration baseline, so "a passing run that ran long" is not a
# condition this harness can distinguish from `green`, and inventing a producer
# would assert a distinction that does not exist here.
# ---------------------------------------------------------------------------
BUCKET_IS_PASSING: dict[str, bool] = {
    "green": True,
    "unexpected-divergence": False,
    "timeout-red": False,
    "selection-red": False,
}
BUCKET_NAMES: tuple[str, ...] = tuple(BUCKET_IS_PASSING)
BUCKETS_PASSING: frozenset[str] = frozenset(
    b for b, passing in BUCKET_IS_PASSING.items() if passing
)
BUCKETS_RED: frozenset[str] = frozenset(BUCKET_NAMES) - BUCKETS_PASSING
# The two asserts that stood here ("BUCKETS_PASSING and BUCKETS_RED partition
# BUCKET_NAMES" and "they are disjoint") were TAUTOLOGIES: BUCKETS_RED is
# `BUCKET_NAMES - BUCKETS_PASSING` by construction, so both conditions hold for
# ANY input and neither could ever fire. Something that cannot fail is not
# protection — it reads as protection. Replaced with the invariant that CAN fail
# and that actually carries weight: both sides must be non-empty, because
# `closes_issue` (all runs passing) and `exit_code` (any run red) each branch on
# one of these sets, so an all-passing or all-red vocabulary silently changes what
# a record means.
assert BUCKETS_PASSING and BUCKETS_RED, (
    "the bucket vocabulary must declare at least one passing and at least one red "
    f"bucket: passing={sorted(BUCKETS_PASSING)} red={sorted(BUCKETS_RED)}"
)

# ---------------------------------------------------------------------------
# Load bands (D14) — half-open, deterministic at the boundaries.
#
# `LOAD_BAND_UNMEASURED` is NOT a band: it is the state `load1()` returns when the
# host could not be read. Before it existed the sentinel `-1.0` fell through
# `load_band`'s `return LOAD_BANDS[-1][0]` and was reported as the TOP band (L-C),
# so a run whose load could not be measured satisfied `load-bands-do-not-overlap`
# and was recorded as an L-C regime — a proxy silent in exactly the failure case it
# exists for. The overlap conjunct now requires every run to be in one DECLARED
# band, so an unmeasured run can never satisfy it.
# ---------------------------------------------------------------------------
LOAD_BANDS: tuple[tuple[str, float, float], ...] = (
    ("L-A", 0.0, 12.0),
    ("L-B", 12.0, 24.0),
    ("L-C", 24.0, float("inf")),
)
LOAD_UNMEASURED = -1.0
LOAD_BAND_UNMEASURED = "unmeasured"
DEFAULT_LOAD_CEILING = 60.0


def load_band(value: float) -> str:
    """The declared half-open band for a load1 value (12.0 -> L-B, 24.0 -> L-C).

    A negative sentinel or a non-finite value is `LOAD_BAND_UNMEASURED`, never a
    band: an unmeasurable load cannot be asserted to lie in a regime.
    """
    if not math.isfinite(value) or value < 0.0:
        return LOAD_BAND_UNMEASURED
    for name, lo, hi in LOAD_BANDS:
        if lo <= value < hi:
            return name
    return LOAD_BAND_UNMEASURED


def load1() -> float:
    """The host's 1-minute load average, or `LOAD_UNMEASURED` if it cannot be read."""
    try:
        return float(os.getloadavg()[0])
    except (OSError, AttributeError):
        return LOAD_UNMEASURED


# ---------------------------------------------------------------------------
# Cause classes for the red (D10 / F15(i)).
#
# `GRAPH.COPY failed, could not fork` is emitted by TWO mechanically distinct
# causes, and the module-fork refusal line is BYTE-IDENTICAL between them:
#   - save/child-slot: a background RDB save (or AOF rewrite) child occupies the
#     child slot, so RedisModule_Fork refuses with "File exists".
#   - module-fork-hang (#3845): a PREVIOUS module fork child never exited, so
#     the next RM_Fork refuses with the same "File exists".
# The discriminator is the presence of the save/rewrite lines AND the ABSENCE of
# a matching `Module fork exited pid:` for every `Module fork started pid:`
# (for the module-fork-hang class).
# ---------------------------------------------------------------------------
# The patterns every cause classifier matches. Each literal is declared ONCE and
# referenced by CAUSE_CLASSES below — a second, string-form copy of the same
# regex is how a classifier and its own evidence drift (`BGSAVE_RE` /
# `AOF_REWRITE_RE` were declared here and then re-typed as strings in the specs,
# so neither compiled object was ever matched against anything).
_FORK_REFUSAL = r"Can't fork for module:"
# RM_Fork's refusal is `Can't fork for module: <strerror(errno)>`. The bare prefix is
# shared by EVERY errno, so it cannot support a class whose NAME asserts a specific
# one: an EAGAIN refusal ("Resource temporarily unavailable") was labelled
# `module-fork-eexist` — asserting `File exists` about a log that never says it.
# This matcher requires the EEXIST text, and only the EEXIST-named class uses it.
_FORK_REFUSAL_EEXIST = r"Can't fork for module:\s*File exists"
_AOF_START = r"Starting BGREWRITEAOF"
FORK_REFUSAL_RE = re.compile(_FORK_REFUSAL)
FORK_REFUSAL_EEXIST_RE = re.compile(_FORK_REFUSAL_EEXIST)
MODULE_FORK_STARTED_RE = re.compile(r"Module fork started pid:\s*(\d+)")
MODULE_FORK_EXITED_RE = re.compile(r"Module fork exited pid:\s*(\d+)")
# The daemon's OWN shutdown assertion that a module fork child is STILL ALIVE
# (`moduleForkChildPid != -1` at shutdown) — the plan doc's declared second
# `requires_lines` entry for `module-fork-hang` (line 1139). It is the ONLY witness
# of an unexited module fork child that appears in captured `redis.log`: measured
# over the 1120 real `redis.log`s collected under `/tmp/pi3827-*`,
# `Module fork started pid:` occurs in **0** of them, so a class keyed solely on the
# started/exited counter is unreachable on real evidence.
MODULE_FORK_CHILD_KILLED_RE = re.compile(r"There is a module fork child\. Killing it!")
BGSAVE_ANY_RE = re.compile(r"Background saving")
BGSAVE_RE = re.compile(r"Background saving (started|terminated)")
AOF_START_RE = re.compile(_AOF_START)
AOF_REWRITE_RE = re.compile(rf"({_AOF_START}|Background AOF rewrite (started|finished))")

CAUSE_PRECEDENCE = ("module-fork-hang", "module-fork-eexist", "aof-rewrite-fork", "save-child-slot")

CAUSE_CLASSES: dict[str, dict] = {
    # #3845: a previous RM_Fork child never exited, so the next refusal is EEXIST.
    # The unexited-child property has TWO INDEPENDENT witnesses, and the class is
    # reachable iff either holds:
    #   (a) the ordered started/exited counter in `_module_fork_lifecycle`, read AT
    #       the EEXIST refusal line — sound, pid-reuse aware, and the only witness a
    #       fixture WITHOUT the daemon's shutdown line can exercise;
    #
    # Witness (a) is TEMPORAL for exactly the reason (b) is weak. A fork that starts
    # AFTER the refusal cannot have held the slot the refusal is about, so the
    # counter is read at the refusal's own line, not at end-of-log: the reviewer's
    # `save-started / refusal / save-terminated / fork-started` and
    # `fork-started / fork-exited / refusal / fork-restarted` logs each end with an
    # unexited pid, and only the temporal read distinguishes ``outstanding when
    # RM_Fork checked`` from ``outstanding later``.
    #   (b) the daemon's own `There is a module fork child. Killing it!` — the plan
    #       doc's declared `requires_lines` entry (line 1139), and the ONLY witness
    #       that occurs in real output. Keying on (a) alone made this class
    #       UNREACHABLE on real evidence while a genuine hang red was labelled
    #       `module-fork-eexist`, the label reserved for a PRIOR instance's child —
    #       a proxy silent in exactly the case it exists to cover.
    #
    # The plan doc's `requires_absent: [r"Module fork exited pid:"]` is NOT copied
    # for the counter witness: it is the same property in a cruder form ("no fork
    # ever exited"), and the blunt form contradicts that witness — the pid-reuse case
    # `started 123 / exited 123 / started 123` DOES contain an exited line and IS a
    # hang. The ordered counter is a strict refinement, so it governs there.
    #
    # The same absence rule IS correct for the OTHER witness — the daemon's killing
    # line. That witness is WEAK: it proves a child was outstanding at SHUTDOWN, not
    # at the refusal, and says nothing about what caused the refusal. It therefore
    # supports the class only when nothing else explains the EEXIST:
    #   * no save/AOF child is present, so the refusal is not better explained by a
    #     save-child-slot / aof-rewrite-fork refusal; and
    #   * no `Module fork exited pid:` appears, so the child killed at shutdown is the
    #     same instance that held the slot at the refusal (had any instance exited,
    #     the shutdown child could be a later, unrelated one).
    # Without these constraints the witness was a co-occurrence test: a log whose
    # EEXIST came from a save child was relabelled a hang because some unrelated
    # module child lingered to shutdown (measured: /tmp/revsyn/06_bgsave_kill.log
    # -> module-fork-hang under the unconstrained rule, save-child-slot under this
    # one). The counter witness carries no absence constraints — a fork outstanding
    # at the refusal line IS direct evidence the slot was held at that moment.
    "module-fork-hang": {
        "requires_lines": [FORK_REFUSAL_RE],
        "requires_absent": [],
        "unexited_fork_witnesses": [MODULE_FORK_CHILD_KILLED_RE],
        "weak_witness_requires_absent": [
            BGSAVE_ANY_RE, AOF_START_RE, MODULE_FORK_EXITED_RE,
        ],
        "requires_fork_refusal": True,
        # The class requires a refusal AND an unexited child, so its payload is
        # that the unexited child CAUSED the refusal — and RM_Fork's child-slot
        # check sets EEXIST (`moduleForkChildPid != -1`); the plan doc's own
        # declaration requires `File exists`. A bare `Can't fork for module:` can
        # also be EAGAIN (the fork() call hit a resource limit), which is a
        # different mechanism the hang does not evidence. The unexited child is
        # still reported in `module_forks_unexited`; only the CAUSATION claim is
        # withheld, so the refusal falls to `unattributed` (which cannot close).
        "requires_eexist_refusal": True,
    },
    # EEXIST refusal with NO save/AOF discriminator present.
    #
    # Observed on the family reproducer: `Can't fork for module: File exists`
    # appears with NO `Background saving` and NO BGREWRITEAOF line, so both
    # discard-candidates above are excluded and this fell through to
    # `unattributed` — a cause the log DOES state, thrown away.
    #
    # It is deliberately NOT folded into `module-fork-hang`: that class's
    # discriminator is the ABSENCE of a matching `Module fork exited pid:`, and
    # the reviewer's Devil's-Advocate finding is precisely that the two
    # mechanically distinct causes emit a BYTE-IDENTICAL refusal. Folding this in
    # would re-conflate what the discriminator exists to separate. A refusal with
    # no `Module fork started` in THIS log means the slot was held by a child of a
    # PRIOR instance — a different run — so its started line is not in this file.
    # That is a distinct, honestly-labellable fact, so it gets its own name rather
    # than being forced into a neighbour or lost.
    "module-fork-eexist": {
        "requires_lines": [FORK_REFUSAL_RE],
        "requires_absent": [BGSAVE_ANY_RE, AOF_START_RE],
        "requires_fork_refusal": True,
        # The NAME asserts EEXIST, so the refusal must state EEXIST — the shared
        # `Can't fork for module:` prefix also fronts EAGAIN, which is a different
        # failure and a different label (`unattributed`).
        "requires_eexist_refusal": True,
    },
    # appendonly yes -> a background AOF rewrite child occupies the slot.
    "aof-rewrite-fork": {
        "requires_lines": [AOF_REWRITE_RE],
        "requires_absent": [],
        "requires_fork_refusal": True,
        # "a child occupies the slot" means RM_Fork failed on the slot check with
        # EEXIST. An EAGAIN refusal is a resource-limit failure, a different
        # mechanism the AOF rewrite child does not evidence.
        "requires_eexist_refusal": True,
    },
    # The RDB save child (`--save ''` asymmetry) occupies the slot; the refusal
    # line is BYTE-IDENTICAL to module-fork-hang, so the save lines are the
    # discriminator. No stale module fork is required (and none may be present
    # with an unexited pid, else precedence labels it module-fork-hang).
    "save-child-slot": {
        "requires_lines": [BGSAVE_RE],
        "requires_absent": [AOF_START_RE],
        "requires_fork_refusal": True,
        # Same mechanism as aof-rewrite-fork: the save child "occupies the slot",
        # which is precisely the EEXIST child-slot check. An EAGAIN refusal does
        # not evidence the save child holding it.
        "requires_eexist_refusal": True,
    },
    "unattributed": {
        "requires_lines": [], "requires_absent": [],
        "requires_fork_refusal": False,
    },
}

# The set of causes a RED may be labelled with — DERIVED from CAUSE_CLASSES, never a
# hand-written copy. The copy is how `module-fork-eexist` (added to DETECT a real
# cause) reached CAUSE_CLASSES while `expected_causes` kept the old three: the class
# that exists to detect the cause became the one that invalidated the record
# (`cause-not-expected`) the moment it fired, so that cause could never produce a
# valid RED. Deriving makes the omission unrepresentable — a class added to detect a
# cause IS, by construction, a cause the record expects. `unattributed` is excluded:
# it is the absence of a cause, and closes_issue() rejects it separately
# (`cause-unattributed`).
EXPECTED_CAUSES: tuple[str, ...] = tuple(c for c in CAUSE_CLASSES if c != "unattributed")

# The sibling of the same bug, one line away: a class in CAUSE_CLASSES but absent from
# CAUSE_PRECEDENCE is UNREACHABLE — label_cause() only visits CAUSE_PRECEDENCE, so the
# class would be declared, documented and never emitted. Fail closed here rather than
# ship a class that can never fire.
assert set(CAUSE_PRECEDENCE) | {"unattributed"} == set(CAUSE_CLASSES), (
    "CAUSE_CLASSES and CAUSE_PRECEDENCE disagree — declared but unreachable: "
    f"{sorted(set(CAUSE_CLASSES) - set(CAUSE_PRECEDENCE) - {'unattributed'})}"
)

def _module_fork_lifecycle(
    lines: list[str],
) -> tuple[set[str], set[str], list[str], list[str]]:
    """Ordered, pid-reuse-aware view of the module-fork lifecycle.

    `started - exited` over the WHOLE log is a SET difference, and a set cannot see
    sequence: `Module fork started pid: 123` -> `Module fork exited pid: 123` ->
    `Module fork started pid: 123` (the second one hanging) leaves BOTH sets holding
    123, so the set-diff is EMPTY and a genuine `module-fork-hang` is reported as
    `module-fork-eexist`. A `redis.log` is append-ordered, so the sound view is a
    running count: add on every `started`, subtract on every `exited` (never below
    zero). Leftovers at end-of-log are the unexited instances — pid reuse, and the
    same pid started twice, both included.

    The FOURTH return value is the same counter sampled at every EEXIST refusal
    line: the pids outstanding at the moment RM_Fork refused. This is the witness
    `module-fork-hang` needs, and end-of-log is not a substitute — a fork that
    starts after the refusal (or a pid that exits before it and restarts after)
    leaves an end-of-log leftover while being no cause of the refusal at all.
    """
    outstanding: Counter[str] = Counter()
    started: set[str] = set()
    exited: set[str] = set()
    outstanding_at_refusal: set[str] = set()
    for ln in lines:
        # Sample BEFORE processing the line: a refusal holds the counter's value at
        # its own moment, and no refusal line carries a started/exited marker.
        if FORK_REFUSAL_EEXIST_RE.search(ln):
            outstanding_at_refusal.update(
                pid for pid, n in outstanding.items() if n > 0
            )
        m = MODULE_FORK_STARTED_RE.search(ln)
        if m is not None:
            outstanding[m.group(1)] += 1
            started.add(m.group(1))
            continue
        m = MODULE_FORK_EXITED_RE.search(ln)
        if m is not None:
            pid = m.group(1)
            exited.add(pid)
            if outstanding[pid] > 0:
                outstanding[pid] -= 1
    unexited = sorted(pid for pid, n in outstanding.items() if n > 0)
    return started, exited, unexited, sorted(outstanding_at_refusal)


def _cause_evidence(
    text: str,
    hits: list[str],
    started: set[str],
    exited: set[str],
    unexited: list[str],
    outstanding_at_refusal: list[str],
) -> dict:
    """The evidence payload every label_cause() return shares (one spelling)."""
    return {
        "matched_lines": hits[:20],
        "fork_refusal": bool(FORK_REFUSAL_RE.search(text)),
        "eexist_refusal": bool(FORK_REFUSAL_EEXIST_RE.search(text)),
        "module_fork_child_killed": bool(MODULE_FORK_CHILD_KILLED_RE.search(text)),
        "module_forks_started": sorted(started),
        "module_forks_exited": sorted(exited),
        "module_forks_unexited": unexited,
        "module_forks_outstanding_at_refusal": outstanding_at_refusal,
    }


def label_cause(lines: list[str]) -> tuple[str, dict]:
    """Label the red's cause from server-side `redis.log` lines (D10).

    Returns `(cause, evidence)`. `evidence` carries the matched lines, whether a
    fork refusal appeared, whether that refusal states EEXIST, which module-fork
    instances were outstanding AT the EEXIST refusal (the `module-fork-hang`
    discriminator, computed as a running count so pid reuse cannot hide a hang), and
    which were still outstanding at end-of-log.

    NOTE (GAP-5): the regexes are pinned against a captured real redis.log; a log
    matching none of the declared patterns is `unattributed` and can never close.
    """
    text = "\n".join(lines)
    started, exited, unexited, outstanding_at_refusal = _module_fork_lifecycle(lines)

    for cause in CAUSE_PRECEDENCE:
        spec = CAUSE_CLASSES[cause]
        hits = [
            ln for ln in lines
            if any(re.search(p, ln) for p in spec["requires_lines"])
        ]
        if not hits:
            continue
        if spec.get("requires_fork_refusal") and not FORK_REFUSAL_RE.search(text):
            # The distinguishing refusal is absent. A save/AOF line on its own is
            # not evidence that the module fork was refused (P2-F): without this
            # guard a log containing only `Background saving started` was labelled
            # `save-child-slot`, attributing a fork cause to evidence that never
            # mentions a fork.
            continue
        if spec.get("requires_eexist_refusal") and not FORK_REFUSAL_EEXIST_RE.search(text):
            # The refusal does not state EEXIST. A class whose NAME asserts
            # `File exists` must not be applied to an EAGAIN refusal — the two
            # share the `Can't fork for module:` prefix and nothing else.
            continue
        witnesses = spec.get("unexited_fork_witnesses")
        if witnesses is not None:
            # Two independent witnesses of ONE property. The ordered counter,
            # sampled at the refusal (`outstanding_at_refusal`), is direct: a module
            # fork was outstanding when RM_Fork checked the slot, so the EEXIST check
            # would have failed. An end-of-log leftover (`unexited`) is NOT that
            # proof — the fork could have started after the refusal — so it is
            # reported in evidence but does not witness the class. The daemon's
            # shutdown assertion (`weak`) only says a child was outstanding LATER, so
            # it supports the class only when no save/AOF child and no exited instance
            # give the refusal a different explanation (see the class comment).
            weak = any(re.search(p, text) for p in witnesses) and not any(
                re.search(p, text)
                for p in spec.get("weak_witness_requires_absent", [])
            )
            if not (outstanding_at_refusal or weak):
                # The refusal came from a save/AOF child, from no stale module child,
                # or from a module fork that was not yet outstanding at the refusal.
                continue
        if any(re.search(p, text) for p in spec["requires_absent"]):
            continue
        return cause, _cause_evidence(
            text, hits, started, exited, unexited, outstanding_at_refusal
        )

    return "unattributed", _cause_evidence(
        text, [], started, exited, unexited, outstanding_at_refusal
    )


def attributable(cause: str | None) -> bool:
    """Whether a red's cause is an ATTRIBUTED one — the DERIVATION behind
    `verdict.attributable` (it was the literal `True`, so a record with
    `red.cause == null` claimed an attribution it did not have).

    `None` = no red run at all; `"unattributed"` = the label_cause() fallback, i.e.
    the log stated no cause this tool recognises. Neither is attributable.
    """
    return bool(cause) and cause != "unattributed"


# ---------------------------------------------------------------------------
# Shipping surfaces (D23 / R1) — where a consumer actually looks.
# ---------------------------------------------------------------------------
SHIPPING_SURFACES = ("tortoise_search", "tortoise_recall")


# ---------------------------------------------------------------------------
# Selection + manifest.
# ---------------------------------------------------------------------------
def _resolve_selection(name: str, manifest: dict) -> list[str]:
    if name == "family":
        return list(FAMILY_REPRODUCERS)
    if name == "carve-out":
        from tools.ci_selection import carve_out_files
        return sorted(f"tests/{f}" for f in carve_out_files(manifest))
    if name == "whole-suite":
        from tools.ci_selection import load_manifest
        m = load_manifest()
        core = set(m.get("surfaces", {}).get("core", []))
        return sorted(f"tests/{f}" for f in core)
    raise ValueError(f"unknown selection: {name!r}")


def _manifest_receipt(files: list[str], marker: str, out_dir: Path) -> dict:
    """Reuse skip_guard.emit_manifest for the resolved nodeid manifest (D5.4)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "skip_guard", str(REPO_ROOT / "tools" / "skip-guard.py")
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    out = out_dir / "manifest.txt"

    def _runner(cmd: list[str]):
        # The manifest collect-only MUST run under the same interpreter the runs
        # do, or a 3.9 child fails in conftest and reports it as rc=4.
        if cmd and cmd[0] != _python():
            cmd = [_python(), *cmd[1:]]
        env = _child_env(out_dir)
        (out_dir / "pi3827_capture.py").write_text(_CAPTURE_PLUGIN)
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
        # A fail-closed guard must not discard its own diagnostic. `rc` alone says
        # "pytest usage error" and nothing about WHY — and rc=4 is also what a
        # missing path yields, so the cause is unrecoverable without the stream.
        # The first version of this dropped p.stderr, which made a deterministic
        # manifest failure take a dozen probes to localize. Persist the child's
        # argv, cwd and stderr so the failure is self-describing.
        if p.returncode != 0:
            (out_dir / "collect-diagnostic.txt").write_text(
                "argv: " + " ".join(cmd) + "\n"
                + "cwd: " + str(REPO_ROOT) + "\n"
                + "TMPDIR: " + str(env.get("TMPDIR")) + "\n"
                + "PYTHONPATH: " + str(env.get("PYTHONPATH")) + "\n"
                + "--- child stderr ---\n" + (p.stderr or "(empty)")
                + "--- child stdout (tail) ---\n" + (p.stdout or "")[-2000:]
            )
            print(
                "emit-manifest: collect-only failed rc=%d — child stderr:\n%s"
                % (p.returncode, (p.stderr or "(empty stderr)").strip()),
                file=sys.stderr,
            )
        return p.returncode, p.stdout

    rc = mod.emit_manifest(files, marker, out, runner=_runner)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"emit_manifest failed rc={rc}")
    nodeids = [
        ln for ln in out.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    ]
    digest = "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "path": str(out),
        "digest": digest,
        "count": len(nodeids),
        "unique_count": len(set(nodeids)),
        "marker": marker,
    }


# ---------------------------------------------------------------------------
# The capture plugin — captures the real redis.log BEFORE fixture teardown.
#
# The embedded daemon's `redis_dir` is rmtree'd by redislite `_cleanup` when the
# test's client fixture tears down — i.e. milliseconds after the fork refusal is
# logged. An out-of-process poller (even at 30ms) misses it. This plugin runs
# IN the pytest process and snapshots every `redis.log` at the moment a test's
# call phase reports failure, which is BEFORE its fixtures tear down.
# ---------------------------------------------------------------------------
_CAPTURE_PLUGIN = '''\
"""pi3827_capture — snapshot redis.log at failure time (#3827)."""
import os
from pathlib import Path

_EVID = Path(os.environ.get("PI3827_EVIDENCE_DIR", "."))


def _snap():
    import tempfile
    base = Path(tempfile.gettempdir())
    for p in base.glob("tmp*/redis.log"):
        try:
            data = p.read_bytes()
        except Exception:
            continue
        try:
            (_EVID / (p.parent.name + ".redis.log")).write_bytes(data)
        except Exception:
            pass


def pytest_runtest_makereport(item, call):
    if call.when == "call" and call.excinfo is not None:
        _snap()


def pytest_sessionfinish(session, exitstatus):
    _snap()
'''


def _python() -> str:
    """The interpreter the CHILDREN must run under.

    `sys.executable` is whatever launched this tool — and on this box `python3` is
    3.9 from the Command Line Tools while the repo's .venv is 3.12. The suite
    imports `enum.StrEnum` (3.11+), so a 3.9 child dies inside tests/conftest.py
    with `ImportError: cannot import name 'StrEnum'`, and pytest reports that as a
    USAGE-class rc=4 — which reads like a bad path and is not. Prefer the repo
    venv so the child matches the environment the suite is actually installed
    into; a launch flag must not decide whether the evidence run works.
    """
    for cand in (REPO_ROOT / ".venv" / "bin" / "python",):
        if cand.exists():
            return str(cand)
    return sys.executable


def _short_tmp_root(run_root: Path) -> Path:
    """A SHORT, stable TMPDIR for the child — deliberately NOT under run_root.

    The embedded Redis binds a Unix socket below its own working dir, and macOS caps
    that path at 104 bytes. Nesting the child's TMPDIR under the harness's run root
    (`$TMPDIR/pi-embedded-evidence-XXXX/tmp/tmpYYY/` = 14 dir chars + the socket
    name) produced a 107-byte path, so Redis NEVER STARTED:

        # Failed opening Unix socket: unix socket path too long (107), must be under 104

    The run then hung at fixture setup with executed=0, and the harness reported that
    as `timeout-red` — an ENVIRONMENT failure presented as the family RED. A temp
    root is a budget, and the harness was spending it on directory names.
    """
    tag = hashlib.sha256(str(run_root).encode()).hexdigest()[:8]
    return Path("/tmp") / f"pi3827-{tag}"


def _child_env(run_root: Path) -> dict:
    env = dict(os.environ)
    # Pop every lane variable so a dev shell cannot flip the child's lane.
    for var in (
        "TORTOISE_DB_URI", "TORTOISE_TEST_EXPECT_URI", "TORTOISE_TEST_ALLOW_REMOTE",
        "TORTOISE_TEST_NO_REDIRECT", "TORTOISE_TEST_JOURNAL_FILE", "TORTOISE_DB_PATH",
        "TORTOISE_EMBEDDED_AOF", "TORTOISE_ALLOW_NONSTANDARD_PATH",
    ):
        env.pop(var, None)
    tmp = _short_tmp_root(run_root) / "t"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmp)
    env["TORTOISE_TEST_CARVE_OUT"] = "1"
    env["PI3827_EVIDENCE_DIR"] = str(run_root / "evidence")
    (run_root / "evidence").mkdir(parents=True, exist_ok=True)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(run_root), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    return env


def _read_junit_counts(path: Path) -> dict:
    import xml.etree.ElementTree as ET
    empty = {
        "executed": 0, "skipped": 0, "failed": 0, "observed": 0,
        "junit_parse_error": None,
    }
    if not path.exists():
        return empty
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        # A child killed mid-flush leaves a TRUNCATED junit. Its sibling
        # `_junit_test_files` already tolerated that; this reader raised, so on the
        # TIMEOUT path — the case that exists precisely when things are going wrong
        # — `_run_once` raised out of `_build_record`, and `main` catches only
        # RuntimeError, so the whole harness died with a traceback instead of
        # recording `timeout-red`. Both readers now degrade the same way, and the
        # reason is recorded (`junit_parse_error`) rather than swallowed.
        return {**empty, "junit_parse_error": f"{type(exc).__name__}: {exc}"}
    cases = root.iter("testcase")
    executed = skipped = failed = observed = 0
    for c in cases:
        observed += 1
        if c.find("skipped") is not None:
            skipped += 1
        else:
            executed += 1
        if c.find("failure") is not None or c.find("error") is not None:
            failed += 1
    return {"executed": executed, "skipped": skipped, "failed": failed,
            "observed": observed, "junit_parse_error": None}


def _norm_test_file(raw: str) -> str:
    """Normalise a junit `file` attribute to the repo-relative, POSIX form used by
    the selection (e.g. `tests/test_dr_endpoints.py`)."""
    p = Path(raw)
    if p.is_absolute():
        try:
            return p.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return p.name
    s = str(raw).replace(os.sep, "/")
    while s.startswith("./"):
        s = s[2:]
    return s


class _JunitObservation(NamedTuple):
    """The test FILES a run's OWN junit recorded — the independent side of
    `same_file_list`.

    The type IS the guard. `_red_file_list_matches` accepts an observation only if
    it is an instance of this class, and the only producer is `_junit_test_files`
    (the junit reader). `_run_once`'s `files` — a `list[str]` copy of the selection —
    is therefore not an observation and cannot be substituted for one, which is the
    hole this closes: `same_file_list` was `True` in every reachable state because
    both sides were the selection, and a one-line fallback (`observed = list(files)`)
    restored the vacuity without failing a single test.

    `source` is the junit path the observation was read from: the property the check
    asserts is that the observed files came from the run's OWN evidence, so the
    evidence is NAMED, not merely shaped like a list.

    An EMPTY observation (`_junit_test_files` on a missing or truncated junit) is
    valid and expected — it is the fail-closed state: no evidence, so
    `same_file_list` is False and `red-file-list-differs` is reported, loudly.
    """

    observed: tuple[str, ...]
    failing: tuple[str, ...]
    source: str


def _observation_to_json(obs: _JunitObservation) -> dict:
    """The PERSISTED form of an observation: a LABELLED object, never an anonymous
    tuple. `_JunitObservation` is a NamedTuple, so `json.dumps` used to write a bare
    `[observed, failing, source]` triple; reloaded it is a `list`, `isinstance` is
    False, and `_red_file_list_matches` failed closed on EVERY persisted record — a
    future verifier re-evaluating a record would reject a red that genuinely ran the
    selection.
    """
    return {
        "observed": list(obs.observed),
        "failing": list(obs.failing),
        "source": obs.source,
    }


def _is_str_list(value) -> bool:
    """Whether `value` is a `list` whose every element is a `str`.

    A bare `isinstance(value, list)` is not enough: `str` IS iterable, so on the
    pre-fix code a `failing` value of `"tests/a.py"` rehydrated to
    `('t','e','s','t','s','/','a','.','p','y')`, and a record whose `observed` matched
    the selection with `failing` of `"X"` measured `True` from
    `_red_file_list_matches` — a one-character value certifying a red.
    """
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _observation_from_json(value) -> _JunitObservation | None:
    """Rehydrate the labelled persisted shape, or `None` for anything else.

    Strict on VALUES, not just key NAMES. `_observation_to_json` is the only
    producer, so the accepted shape is exactly its output: a dict with those three
    keys and no others, `observed`/`failing` as `list[str]`, `source` as `str`. A
    `source` of `None` or `123`, a `failing` string, or any other near-miss is
    rejected — this function is the fail-CLOSED gate `_red_file_list_matches` reads,
    and a permissive rehydrator is the guard failing open. Plain `list`s (the
    selection copy the type exists to refuse), the anonymous triple a pre-fix record
    holds, and records with extra keys all stay rejected.
    """
    if isinstance(value, _JunitObservation):
        return value
    if not isinstance(value, dict) or set(value) != {"observed", "failing", "source"}:
        return None
    observed, failing, source = value["observed"], value["failing"], value["source"]
    if not isinstance(source, str):
        return None
    if not _is_str_list(observed) or not _is_str_list(failing):
        return None
    return _JunitObservation(tuple(observed), tuple(failing), source)


def _jsonable(value):
    """Recursively convert the record's in-memory types to their JSON form.

    `json.dumps` cannot be told about a NamedTuple through `default=` — a tuple is
    serialised before the hook is consulted — so the conversion happens here, at the
    persistence seam, while the in-memory record keeps the typed observation.
    """
    if isinstance(value, _JunitObservation):
        return _observation_to_json(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _junit_test_files(path: Path) -> _JunitObservation:
    """The run's OWN observed / failing test FILES, read from its junit XML.

    This is the independent record of what the child actually ran — as opposed to
    `_run_once`'s `files` argument, which is a copy of the selection, so comparing
    it to the selection compares a value to itself in every reachable state (the
    vacuity this replaces).

    Returns a `_JunitObservation`, NOT a bare `(list, list)` pair: the TYPE is the
    independence guard. An earlier revision returned bare lists and left the consumer
    to notice they were empty; a one-line fallback at either seam then restored the
    vacuous `same_file_list = True` — including in the production state the check
    exists for (a red run whose junit was never written).

    Requires `-o junit_family=xunit1` on the run command (see `_pytest_cmd`):
    xunit2 — pytest's default, and what the run used before — emits NO `file`
    attribute, so the observed set would be silently empty in every state.
    """
    import xml.etree.ElementTree as ET

    if not path.exists():
        return _JunitObservation((), (), str(path))
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return _JunitObservation((), (), str(path))
    observed: list[str] = []
    failing: list[str] = []
    for case in root.iter("testcase"):
        raw = case.get("file")
        if raw is None:
            continue
        f = _norm_test_file(raw)
        if f not in observed:
            observed.append(f)
        if (
            case.find("failure") is not None or case.find("error") is not None
        ) and f not in failing:
            failing.append(f)
    return _JunitObservation(tuple(sorted(observed)), tuple(sorted(failing)), str(path))


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(cwd or REPO_ROOT)
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _porcelain_digest(cwd: Path, exclude: Path | None = None) -> tuple[str, bool]:
    status = _git("status", "--porcelain=v2", cwd=cwd)
    diff = _git("diff-index", "HEAD", cwd=cwd)
    blob = (status + "\n" + diff + "\n").encode()
    if exclude is not None:
        # The record-out path is excluded from the pin (M5): strip its line.
        ex = str(exclude.relative_to(cwd)) if exclude.is_relative_to(cwd) else str(exclude)
        blob = b"\n".join(
            ln for ln in blob.splitlines() if ex.encode() not in ln
        ) + b"\n"
    return "sha256:" + hashlib.sha256(blob).hexdigest(), bool(status.strip())


def _snapshot_redis_logs(run_root: Path) -> list[Path]:
    """Copy every redis.log the child's TMPDIR holds into evidence/.

    Mirrors the capture plugin's `_snap`, but callable from the HARNESS. The plugin
    can only fire from a pytest hook (`pytest_runtest_makereport` / sessionfinish),
    and the case that needs this most is the one where no hook ever runs — a hang
    before any test executes. Both measured runs hit the bound with executed=0, so
    the plugin was silent and `red_cause` was null by construction, not by absence
    of a cause. Same class as the rest of this lane: an observer waiting on a proxy
    that is silent in exactly the case it exists to cover.
    """
    evid = run_root / "evidence"
    evid.mkdir(parents=True, exist_ok=True)
    found: list[Path] = []
    # The child's TMPDIR is the SHORT root, not run_root/tmp — see _short_tmp_root.
    roots = [_short_tmp_root(run_root), run_root / "tmp"]
    for root in roots:
        for p in sorted(root.glob("**/redis.log")):
            try:
                (evid / (p.parent.name + ".redis.log")).write_bytes(p.read_bytes())
                found.append(p)
            except OSError:
                continue
    return found


def _pytest_cmd(files: list[str], junit: Path, marker: str, timeout: int) -> list[str]:
    """The child's pytest argv — spelled ONCE, and carrying the load-bearing flag.

    `-o junit_family=xunit1` is not decoration: pytest's default xunit2 emits NO
    `file` attribute on `<testcase>`, so the per-run observed/failing FILE data the
    red-file-list conjunct compares would be absent in every state. A test asserts
    the flag is present, so dropping it fails a test rather than silently making
    the tool unclosable.
    """
    return [
        _python(), "-m", "pytest", *files,
        "-q", "-p", "no:cacheprovider",
        f"--timeout={max(30, timeout // 3)}",
        f"--junitxml={junit}",
        "-m", marker,
        "-p", "pi3827_capture",
        "-o", "junit_family=xunit1",
    ]


def _run_once(
    files: list[str],
    measured_root: Path,
    run_root: Path,
    run_id: int,
    marker: str,
    timeout: int,
) -> dict:
    junit = run_root / f"junit-{run_id}.xml"
    cmd = _pytest_cmd(files, junit, marker, timeout)
    env = _child_env(run_root)
    (run_root / "pi3827_capture.py").write_text(_CAPTURE_PLUGIN)
    before = load1()
    started = time.time()
    timed_out = False
    child_out = ""
    # Popen + communicate, NOT subprocess.run(timeout=...): run() re-raises
    # TimeoutExpired and NEVER retrieves the captured pipes, so the child's output —
    # the only diagnostic when a run is killed — is destroyed by the exact path that
    # needs it. Both measured runs died this way with no output kept.
    proc = subprocess.Popen(
        cmd, cwd=str(measured_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        child_out, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        rc = 124
        proc.kill()
        try:
            child_out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            child_out = ""
        _snapshot_redis_logs(run_root)
        (run_root / f"child-output-{run_id}.txt").write_text(child_out or "")
    wall = time.time() - started
    after = load1()
    counts = _read_junit_counts(junit)
    # NO fallback. A missing / truncated / xunit2 junit yields an EMPTY observation,
    # which fails `_red_file_list_matches` closed — it never yields the selection.
    observed = _junit_test_files(junit)
    bucket = "green" if rc == 0 and counts["failed"] == 0 else "unexpected-divergence"
    if timed_out:
        bucket = "timeout-red"
    elif not junit.exists():
        bucket = "selection-red"
    # The producer names buckets by literal; fail closed if one is not declared in
    # BUCKET_IS_PASSING above, so a consumer's set cannot silently omit it (the
    # `slow-run` hole, inverted).
    assert bucket in BUCKET_NAMES, f"undeclared run bucket: {bucket!r}"
    # Cause label from any captured redis.log (best-effort per run).
    cause = None
    evidence: dict = {}
    for log in sorted((run_root / "evidence").glob("*.redis.log")):
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            continue
        c, ev = label_cause(lines)
        if c != "unattributed" or ev.get("fork_refusal"):
            cause, evidence = c, {"redis_log": str(log), **ev}
            if c != "unattributed":
                break
    return {
        "run_id": run_id,
        "bucket": bucket,
        "files": list(files),
        "returncode": rc,
        "step_wall_s": round(wall, 2),
        "observed": counts["observed"],
        "executed": counts["executed"],
        "skipped": counts["skipped"],
        "load": {"before": before, "after": after, "band": load_band(after)},
        "tree_moved": False,
        "redis_log_cause": cause,
        "cause_evidence": evidence,
        "timed_out": timed_out,
        "junit_parse_error": counts["junit_parse_error"],
        "observed_files": observed,
    }


def _red_file_list_matches(red_runs: list[dict], files: list[str]) -> bool:
    """Whether every red run ACTUALLY RAN the selection's file list (F4a).

    Each side is independent data: the selection, and the red run's OWN
    junit-observed test-file set (what the child actually executed). It is NOT a
    comparison against `_run_once`'s `files` field — that field is a copy of the
    selection, so comparing it to the selection is a comparison of a value with
    itself and is `True` in every reachable state. That was the vacuous check this
    replaces.

    A red whose junit observed a different file set — a vanished or renamed
    selection file, a collection error that aborted the rest of the selection, a
    narrowed invocation — did not run this selection, so its cause cannot certify
    it. A red that observed the selection but recorded no failing FILE is likewise
    not a demonstrated red on it.

    What this is deliberately NOT: a red-failing-set vs green-failing-set
    comparison. A green run's failing set is EMPTY by definition, so that
    comparison would be false in exactly the state the paired red must reach. The
    file-level claim a paired red can honestly carry is that the red executed — and
    failed inside — the selection the record names.
    """
    selection = {_norm_test_file(f) for f in files}
    if not red_runs:
        return False
    for r in red_runs:
        obs = _observation_from_json(r.get("observed_files"))
        # The observed side must be an OBSERVATION — junit-derived evidence — never
        # the selection. `list(files)` is a `list`; it is not the labelled object, so a
        # fallback that substitutes the selection for the run's own evidence is
        # structurally unable to reach the comparison below. This guards the PROPERTY,
        # not one mutation: an observation carries the junit it was read from
        # (`source`), the files it observed, and the files it failed in, so both sides
        # of both checks below come from the SAME run's own evidence or the check
        # fails closed. The `dict` branch is the PERSISTED record's shape
        # (`_observation_to_json`), so a record written to disk and reloaded by a
        # verifier rehydrates instead of failing closed on every run.
        if obs is None:
            return False
        observed = {_norm_test_file(f) for f in obs.observed}
        failing = {_norm_test_file(f) for f in obs.failing}
        if not observed or observed != selection:
            return False
        if not failing:
            return False
    return True


# ---------------------------------------------------------------------------
# Closing rule (D9) — the subset that the RED half can evaluate honestly.
# ---------------------------------------------------------------------------
def closes_issue(rec: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    def conj(name: str, value: bool) -> bool:
        if not value:
            reasons.append(name)
        return value

    ok = True
    ok &= conj("runs-empty", bool(rec["runs"]))
    ok &= conj("non-green-bucket",
               all(r["bucket"] in BUCKETS_PASSING for r in rec["runs"]))
    ok &= conj("no-test-executed", all(r["executed"] >= 1 for r in rec["runs"]))
    ok &= conj("selection-not-family", rec["selection"]["name"] == "family")
    ok &= conj("reproducer-absent",
               any(MANDATORY_REPRODUCER.endswith(f) or MANDATORY_REPRODUCER in f
                   for f in rec["selection"]["files"]))
    ok &= conj("pin-not-airtight",
               rec["pin"]["worktree_clean"] and all(not r["tree_moved"] for r in rec["runs"]))
    ok &= conj("cause-unattributed",
               rec["red"]["cause"] in CAUSE_CLASSES and rec["red"]["cause"] != "unattributed")
    ok &= conj("cause-not-expected",
               rec["red"]["cause"] in rec["selection"]["expected_causes"])
    ok &= conj("red-file-list-differs", rec["red"]["same_file_list"])
    ok &= conj("load-bands-do-not-overlap", rec["load"]["overlap"])
    ok &= conj("no-rate-change",
               (rec["red"]["at_fixed_commit"]["attempted"]
                and not rec["red"]["at_fixed_commit"]["appeared"]
                and rec["red"]["at_fixed_commit"]["rate_change"])
               or (bool(rec["red"]["at_fixed_commit"]["mutation"])
                   and str(rec["red"]["at_fixed_commit"]["mutation_operator"]).startswith("statement-deletion:")
                   and rec["red"]["at_fixed_commit"]["mutation_target_is_fix_branch"]
                   and not rec["red"]["at_fixed_commit"]["appeared"]
                   and rec["red"]["at_fixed_commit"]["mutation_red_returned"]))
    ok &= conj("record-role-not-closing", rec["record_role"] == "closing")
    # R1 (D23): certification binds to the shipping surface.
    ok &= conj("certification-not-on-shipping-surface",
               rec["red"]["at_fixed_commit"]["surface"] in SHIPPING_SURFACES
               and bool(rec["red"]["at_fixed_commit"]["surface_assertion"]))
    # R2 (D24): certificate bound to the reviewed head SHA.
    ok &= conj("certificate-not-bound-to-review-head",
               rec["pin"]["head_sha"] == rec["pin"]["commit"]
               and not rec["pin"]["post_review_dirty"])
    return ok, reasons


def exit_code(rec: dict) -> int:
    ok, _ = closes_issue(rec)
    if ok:
        return 0
    if any(r["bucket"] in BUCKETS_RED for r in rec["runs"]):
        return 1
    if rec["verdict"].get("environment_error"):
        return 2
    return 3


def _tool_version() -> str:
    blob = _git("hash-object", str(Path(__file__).resolve()))
    return blob


def _write_record(rec: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".rec-", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(_jsonable(rec), fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, out)


def _build_record(args: argparse.Namespace) -> dict:
    from tools.ci_selection import load_manifest

    run_root = Path(tempfile.mkdtemp(prefix="pi-embedded-evidence-"))
    evidence = run_root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (run_root / "pi3827_capture.py").write_text(_CAPTURE_PLUGIN)

    manifest = load_manifest()
    files = _resolve_selection(args.selection, manifest)
    mrec = _manifest_receipt(files, args.marker, run_root)

    requested_ref = None
    measured_root = REPO_ROOT
    worktree_added = False
    if args.ref:
        requested_ref = _git("rev-parse", f"{args.ref}^{{commit}}")
        head = _git("rev-parse", "HEAD")
        if requested_ref != head:
            wt = run_root / "worktree"
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt), requested_ref],
                capture_output=True, text=True, cwd=str(REPO_ROOT), check=True,
            )
            measured_root = wt
            worktree_added = True
    commit = _git("rev-parse", "HEAD", cwd=measured_root)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=measured_root)

    ceiling = args.load_ceiling
    cur_load = load1()
    if args.environment_error:
        raise RuntimeError(args.environment_error)

    runs: list[dict] = []
    porcelain = ""
    dirty = False
    try:
        if cur_load > ceiling:
            raise RuntimeError(f"load {cur_load} exceeds ceiling {ceiling}")
        for i in range(1, args.n + 1):
            runs.append(_run_once(files, measured_root, run_root, i, args.marker,
                                  args.run_timeout))
        # The cleanliness digest MUST be taken while the measured tree still
        # EXISTS. It used to run after this `finally`, which removes the detached
        # worktree — so a --ref measurement stat'd a path that was already gone
        # and died with FileNotFoundError, losing the one field that says the tree
        # did not move. Read state before the code that deletes it.
        porcelain, dirty = _porcelain_digest(measured_root, exclude=args.record_out)
    finally:
        if worktree_added:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(measured_root)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )

    red_run = next((r for r in runs if r["bucket"] in BUCKETS_RED), None)
    bands = {r["load"]["band"] for r in runs}
    green_runs = [r for r in runs if r["bucket"] in BUCKETS_PASSING]
    red_runs = [r for r in runs if r["bucket"] in BUCKETS_RED]
    # F4a: derived from the red runs' own recorded file lists, never a literal.
    same_file_list = _red_file_list_matches(red_runs, files)
    red_band = (red_run or runs[-1])["load"]["band"]
    green_band = (green_runs[0]["load"]["band"] if green_runs else red_band)
    cause = red_run["redis_log_cause"] if red_run else None
    cause_evidence = red_run["cause_evidence"] if red_run else {}
    # DERIVED from the label it summarises, never a literal (it was `True`).
    # `attributable` is a claim ABOUT `red.cause`, so a record with `red.cause ==
    # null` (no red run) or `unattributed` was claiming an attribution it does not
    # have.
    attributable_ = attributable(cause)

    rec = {
        "schema": "embedded-evidence/1",
        "attestation": "self-declared",
        "record_role": args.record_role,
        "tool_version": _tool_version(),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection": {
            "name": args.selection,
            "files": files,
            "expected_causes": list(EXPECTED_CAUSES),
        },
        "manifest": mrec,
        "pin": {
            "commit": commit,
            "head_sha": commit,
            "head_sha_verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "post_review_dirty": False,
            "tree_object": tree,
            "requested_ref": requested_ref,
            "pairing_ref": None,
            "worktree_clean": not dirty,
            "porcelain_digest": porcelain,
            "record_out_excluded": str(args.record_out) if args.record_out else None,
            "measured_root": str(measured_root),
            "environment_pinned": False,
        },
        "n": {
            "requested": args.n,
            "mode": "explicit",
            "observed_failure_rate": (len(red_runs) / len(runs)) if runs else 0.0,
            "max_runs": DEFAULT_MAX_RUNS,
            "declared_local": True,
            "note": "N=10 is DECLARED LOCAL — no source makes any N canonical.",
        },
        "runs": runs,
        "load": {
            "bands": {n: f"{lo} <= x < {hi if hi != float('inf') else 'inf'}"
                      for n, lo, hi in LOAD_BANDS},
            "ceiling": ceiling,
            "ceiling_source": "DEFAULT_LOAD_CEILING / operator --load-ceiling",
            "declared_band": red_band,
            "green_band": green_band,
            "red_band": red_band,
            # An UNMEASURED run is not in a band, so it can never satisfy the overlap
            # claim: the comparison is meaningful only if every run's load was read.
            "overlap": bool(bands) and LOAD_BAND_UNMEASURED not in bands and len(bands) == 1,
        },
        "environment": {
            "lane": {"uri_unset": True, "carve_out": "1", "expect_uri": False},
            "invocation_id": uuid.uuid4().hex,
            "tmpdir": str(run_root / "tmp"),
            "ledger_root": str(run_root),
        },
        "red": {
            "ref": requested_ref or commit,
            "ref_role": "pinned-head-pre-fix" if not args.pairing_ref else "last-before-first-family-fix",
            "ref_tree_object": tree,
            "cause": cause,
            "cause_evidence": cause_evidence,
            "red_green_mix": {"red": len(red_runs), "green": len(green_runs)},
            "at_fixed_commit": {
                "attempted": False,
                "appeared": None,
                "rate_change": False,
                "mutation": None,
                "mutation_operator": None,
                "mutation_target_is_fix_branch": False,
                "mutation_red_returned": False,
                "surface": None,
                "surface_assertion": None,
            },
            "same_file_list": same_file_list,
        },
        "verdict": {
            "status": "RED-AT-PINNED-REF" if red_runs else "ALL-GREEN",
            "green_only": not red_runs,
            "attributable": attributable_,
            "environment_error": False,
            "closes_issue": False,
            "violations": [],
            "reasons": [],
        },
        "reproduce": (
            f"python3 tools/embedded_evidence.py {args.cmd} --selection {args.selection} "
            f"--n {args.n} --ref {commit} --marker \"{args.marker}\""
        ),
    }
    ok, reasons = closes_issue(rec)
    rec["verdict"]["closes_issue"] = ok
    rec["verdict"]["violations"] = reasons
    rec["verdict"]["status"] = (
        "PAIRED-RED-DEMONSTRATED" if ok else
        ("RED-AT-PINNED-REF" if red_runs else "ALL-GREEN")
    )
    rec["exit_code"] = exit_code(rec)
    return rec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="embedded_evidence")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "red"):
        p = sub.add_parser(name)
        p.add_argument("--selection", default="family",
                       choices=["family", "carve-out", "whole-suite"])
        p.add_argument("--n", type=int, default=DEFAULT_N)
        p.add_argument("--ref", default=None)
        p.add_argument("--pairing-ref", default=None)
        p.add_argument("--marker", default=DEFAULT_MARKER)
        p.add_argument("--load-ceiling", type=float, default=DEFAULT_LOAD_CEILING)
        p.add_argument("--run-timeout", type=int, default=DEFAULT_RUN_TIMEOUT_S)
        p.add_argument("--record-role", default="historical-attestation",
                       choices=["historical-attestation", "closing"])
        p.add_argument("--record-out", default=None)
    c = sub.add_parser("classify")
    c.add_argument("--redis-log", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "classify":
        lines = Path(args.redis_log).read_text(errors="replace").splitlines()
        cause, ev = label_cause(lines)
        print(json.dumps({"cause": cause, "evidence": ev}, indent=2))
        return 0

    args.record_out = Path(args.record_out).expanduser() if args.record_out else None
    args.environment_error = None
    if args.n < 2:
        print("error: --n must be >= 2 (a single run is not N consecutive)", file=sys.stderr)
        return 2
    if args.n > DEFAULT_MAX_RUNS:
        print(f"error: --n {args.n} exceeds --max-runs {DEFAULT_MAX_RUNS}", file=sys.stderr)
        return 2

    try:
        rec = _build_record(args)
    except RuntimeError as exc:
        print(f"environment error: {exc}", file=sys.stderr)
        return 2

    out = args.record_out or Path(tempfile.gettempdir()) / "pi-embedded-evidence" / "record.json"
    _write_record(rec, out)
    print(json.dumps({
        "record": str(out),
        "status": rec["verdict"]["status"],
        "closes_issue": rec["verdict"]["closes_issue"],
        "red_cause": rec["red"]["cause"],
        "red_band": rec["load"]["red_band"],
        "reasons": rec["verdict"]["violations"],
        "exit_code": rec["exit_code"],
    }, indent=2))
    return rec["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
