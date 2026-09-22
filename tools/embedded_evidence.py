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
import contextlib
import hashlib
import json
import math
import os
import re
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
                f"emit-manifest: collect-only failed rc={p.returncode}"
                + " — child stderr:\n"
                + (p.stderr or "(empty stderr)").strip(),
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


class UsageError(Exception):
    """A usage error ⇒ exit 2 with NO record written (D8 precedence 2).

    Distinct from `RuntimeError`, which `main()` reports as an environment error:
    both exit 2, but a usage error is a property of the INVOCATION, not of the host.
    """


def _git(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> str:
    """Run git and return its stdout.

    `env=None` INHERITS the ambient environment — the production behaviour, and
    the default so no existing caller changes meaning. The parameter exists so a
    caller can pin the environment the MEASURED calls see: git reads
    `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` and the global/system config
    (`core.autocrlf`, `core.fsmonitor`, `core.untrackedCache`, `status.*`,
    `diff.*`) straight from it, so a runner's ambient config would otherwise get
    to decide what the pin measures. A test that sanitises only the FIXTURE's own
    git invocations does NOT reach these calls — which is how the first version
    of the `#4540` tests could have passed vacuously (#4203).
    """
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(cwd or REPO_ROOT),
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _git_returncode(args: list[str]) -> int:
    """Run a git predicate whose NON-ZERO rc is an ANSWER, not a failure.

    `_git` raises on rc≠0, which is right for a read whose absence is an error but
    wrong for `merge-base --is-ancestor`, where rc 1 is the "no" of a well-formed
    question. This is the rc-bearing counterpart, kept separate so the raising
    semantics of `_git` are not weakened.
    """
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    return proc.returncode


def _strict_ancestor(ancestor: str, descendant: str) -> bool:
    """True iff `ancestor` is a STRICT ancestor of `descendant` (D16/C1).

    `git merge-base --is-ancestor` is true for an EQUAL pair, which D16 does not
    accept as a pairing ref — a ref equal to the measured commit cannot be "before
    the fix" — so equality is excluded explicitly.
    """
    if ancestor == descendant:
        return False
    return _git_returncode(["merge-base", "--is-ancestor", ancestor, descendant]) == 0


def _worktree_at(ref: str, run_root: Path, name: str) -> tuple[Path, bool]:
    """Materialize `ref` for measurement, WITHOUT disturbing the invoking checkout.

    Returns `(root, added)`. When `ref` is already this checkout's HEAD the tree
    itself is measured (`added=False`); otherwise a detached worktree is created
    under `run_root`. Two callers need this: the `--ref` measurement and the
    `--pairing-ref` baseline red re-run (D16) — both must measure a ref that may
    not be checked out, and neither may touch the tree it is comparing against.
    """
    if ref == _git("rev-parse", "HEAD"):
        return REPO_ROOT, False
    wt = run_root / name
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt), ref],
        capture_output=True, text=True, cwd=str(REPO_ROOT), check=True,
    )
    return wt, True


def _porcelain_digest(
    cwd: Path, env: dict[str, str] | None = None
) -> tuple[str, bool]:
    """The measured tree's cleanliness digest, and whether it is DIRTY.

    The tool's own `--record-out` receipt is NOT excluded here, and does not need
    to be: `_build_record` REFUSES an in-tree `--record-out` as a usage error
    before any measurement is taken (#4203, owner-ruled option (a), #4572). With
    the receipt outside the tree this function performs NO path-based exclusion at
    all — and an exclusion that does not exist cannot over-match.

    That is the whole point. The exclusion this replaces was re-derived five times
    (`--record-out tools/e` substring-matched the dirty `tools/embedded_evidence`
    py; `Path.resolve()` followed a symlink and named a whole directory; the
    pathspec lacked `literal` and globbed; `:(exclude)X` also matched every `X/…`;
    a lexical-vs-kernel `--record-out link/../out` divergence), and each spelling
    traded one over-broad form for another. Every one of them could turn a
    genuinely dirty tree into a digest that reads clean (post_review_dirty False,
    exit 0 — the fail-open class of #4540), because any path-based exclusion has
    to PROVE it names the receipt and nothing else, and each proof rested on an
    assumption about git's pathspec semantics that turned out to be wrong.

    `--untracked-files=all` is load-bearing and is KEPT: without it git collapses a
    fresh untracked directory to ONE entry (`? docs/evidence/`), the permanent
    false-FAIL #4203 was raised to close. `env` pins the environment the measured
    calls see — see `_git`.
    """
    status = _git("status", "--porcelain=v2", "--untracked-files=all", "--", ".",
                  cwd=cwd, env=env)
    diff = _git("diff-index", "HEAD", "--", ".", cwd=cwd, env=env)
    blob = (status + "\n" + diff + "\n").encode()
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
        # `tree_moved` is deliberately NOT set here. A literal `False` written by the
        # runner is a value no code path can ever make `True`, so the conjunct that
        # reads it (`pin-not-airtight`'s per-run half) read as protection while
        # supplying none. `_build_record` measures it from the tree's own porcelain
        # digest between runs and writes it onto every run.
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
    # D11: the attested baseline is a RUN too, so its own tree state is part of the
    # pin — `runs` alone left the pairing worktree's move unexamined.
    ok &= conj("pin-not-airtight",
               rec["pin"]["worktree_clean"]
               and all(not r["tree_moved"]
                       for r in [*rec["runs"], *(
                           [rec["red"]["baseline_run"]]
                           if rec["red"].get("baseline_run") else [])]))
    ok &= conj("cause-unattributed",
               rec["red"]["cause"] in CAUSE_CLASSES and rec["red"]["cause"] != "unattributed")
    ok &= conj("cause-not-expected",
               rec["red"]["cause"] in rec["selection"]["expected_causes"])
    ok &= conj("red-file-list-differs", rec["red"]["same_file_list"])
    ok &= conj("load-bands-do-not-overlap", rec["load"]["overlap"])
    # `attempted` is deliberately NOT an AND-term here: `main()` rejects `--n < 2`,
    # so the producer could only ever set it True and it supplied no protection. The
    # falsifiable claim is `rate_change` (a red was demonstrated and did not appear
    # at the measured commit); `attempted` still records that a red was demonstrated
    # at all, so it can be False in a produced record.
    ok &= conj("no-rate-change",
               (rec["red"]["at_fixed_commit"]["rate_change"]
                and not rec["red"]["at_fixed_commit"]["appeared"])
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
    # A RED measured anywhere in this invocation is a red — including the attested
    # pairing-ref baseline, which is persisted under `red.baseline_run` rather than
    # in `runs`. D14/threat row 10 requires a non-overlapping load band to be exit 1
    # ("a red measured at load 80 and a green at load 3"); without the baseline in
    # this test a closing-shaped record that failed only on load returned a clean 3.
    red_runs = list(rec["runs"])
    baseline = rec.get("red", {}).get("baseline_run")
    if baseline:
        red_runs.append(baseline)
    if any(r["bucket"] in BUCKETS_RED for r in red_runs):
        return 1
    if rec["verdict"].get("environment_error"):
        return 2
    return 3


def _tool_version() -> str:
    blob = _git("hash-object", str(Path(__file__).resolve()))
    return blob


def _write_record(rec: dict, out: Path) -> None:
    """Write `rec` to `out` atomically, via a temp in `out`'s PHYSICAL parent.

    The temp must be created in the parent the KERNEL will use, not the lexical
    one. `mkstemp` normalises its `dir` with `os.path.abspath` — LEXICALLY — while
    `os.replace(tmp, out)` resolves every directory component of `out` through
    symlinks. With a symlink followed by `..` the two disagree:

        tree/lnk -> <outside>
        out = tree/lnk/../destdir

    kernel-resolves the destination to `<outside>/../destdir` (outside the tree,
    so the pre-write refusal is correctly silent), while `abspath` collapses
    `lnk/..` to `<tree>` — so the temp file, holding the COMPLETE record JSON, was
    created INSIDE the measured tree and left there when `os.replace` failed
    (#4585). `realpath` resolves `..` AFTER the symlink, exactly as the kernel
    does, so the temp and the destination share one parent.

    If the write or the replace fails, the temp is unlinked before the error is
    re-raised: a partial record must not survive as dirt in a tree the pin
    measures. `os.unlink` is best-effort — the caller (`main`) sweeps any
    `.rec-*` residue that this cleanup could not remove.
    """
    parent = Path(os.path.realpath(os.path.dirname(os.fspath(out))))
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".rec-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(_jsonable(rec), fh, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# `tempfile.mkstemp` names its file `prefix + 8 random chars + suffix`, so a temp
# THIS tool created is always `.rec-` + exactly 8 of `[a-z0-9_]` + `.tmp`. Matching
# that shape (rather than an open `.rec-*.tmp` glob) keeps the backstop sweep from
# deleting a user file that merely shares the prefix: the earlier glob removed any
# `.rec-anything.tmp` — the tool cleaning up something it never created (#4585).
_REC_RESIDUE_RE = re.compile(r"\A\.rec-[a-z0-9_]{8}\.tmp\Z")


def _sweep_record_residue(*measured_roots: Path) -> list[Path]:
    """Delete residue `_write_record` left inside a measured tree; return removals.

    A temp file holds the COMPLETE record JSON, so a survivor is exactly the
    "the tool's own record is part of the dirt it measures" condition #4203 exists
    to eliminate. `_write_record` now creates the temp in the destination's
    physical parent and unlinks it on failure, so this is the BACKSTOP for residue
    that cleanup could not remove (its `os.unlink` failed, or an earlier invocation
    crashed). Every directory a temp can be created in is swept: the measured roots
    themselves (a lexical collapse such as `tree/lnk/..` places the temp in the tree
    ROOT) and the destination's physical parent (where the temp goes when `out`
    resolves to a nested directory). The sweep is non-recursive, and it removes a
    file only when its name matches `_REC_RESIDUE_RE` — the exact shape
    `mkstemp(prefix=".rec-", suffix=".tmp")` emits — so it cannot touch anything
    else the tree contains. The residual blast radius is stated where it bites: a
    file that is ITSELF a genuine `.rec-XXXXXXXX.tmp` is indistinguishable from one
    of our temps and is removed; nothing outside that exact shape is.
    """
    removed: list[Path] = []
    for root in measured_roots:
        root_real = Path(os.path.realpath(os.fspath(root)))
        if not root_real.is_dir():
            continue
        try:
            names = sorted(os.listdir(root_real))
        except OSError:
            # Best-effort: the sweep runs inside the write-failure handler, so it
            # must not turn a refusal into a traceback.
            continue
        for name in names:
            if not _REC_RESIDUE_RE.match(name):
                continue
            candidate = root_real / name
            try:
                os.unlink(candidate)
            except OSError:
                continue
            removed.append(candidate)
    return removed


def _default_record_out() -> Path:
    """The ONLY supported destination: outside every tree the pin measures.

    Derived, never a stale literal, so the refusal message and `main()`'s fallback
    cannot drift apart. Computed per call because `TMPDIR` can change within a
    process (a test, an operator export).
    """
    return Path(tempfile.gettempdir()) / "pi-embedded-evidence" / "record.json"


def _written_location(out: Path) -> Path:
    """Where `_write_record(out)` will ACTUALLY create the file.

    `_write_record` calls `os.replace(tmp, out)`. The kernel resolves every
    DIRECTORY component of `out` through symlinks, but `rename(2)` REPLACES a
    symlink at the FINAL component rather than following it. So `realpath(out)`
    would follow that final symlink and report a location the write does not use:
    `--record-out <a symlink inside the tree that points outside>` would pass a
    `realpath`-based containment check while the receipt still lands INSIDE the
    tree — and then the tool's own record is dirt it did not exclude. Resolve the
    PARENT physically and keep the final component lexical.

    The parent is resolved with `realpath` — never `abspath`. `abspath` collapses
    `..` LEXICALLY, before any symlink is resolved, so it predicts a destination the
    kernel does not use: with `lnk -> <tree>/docs`, `abspath("…/lnk/../x.json")`
    reads "outside" while `os.replace` follows `lnk` and THEN applies `..`, landing
    the record at `<tree>/x.json` (#4203, reproduced). `realpath` resolves `..`
    AFTER the symlink, exactly as the kernel does. It also puts both sides of the
    containment comparison on one basis: a `--ref` measured root comes from
    `tempfile.mkdtemp()` and is `/var/…` while macOS's `Path.cwd()` is
    `/private/var/…` — the same directory spelled two ways.

    This is still a PREDICTION, so it is no longer the only thing standing between
    the tool and its own dirt — `_verify_record_landed_outside` observes where the
    file actually landed after the write and is the fail-closed backstop.
    """
    raw = os.fspath(out)
    return Path(os.path.realpath(os.path.dirname(raw)), os.path.basename(raw))


def _same_dir(a: Path, b: Path) -> bool:
    """Do `a` and `b` name the same directory? Asked of the KERNEL, by inode.

    `os.path.samefile` is the only comparison insensitive to BOTH case (a
    case-variant of a root component passed a string-containment test on a
    case-insensitive FS — #4203) and any lexical divergence `realpath` left behind.
    A path that does not exist raises `OSError`; "cannot stat" is not "inside".
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _inside_tree(candidate: Path, root: Path) -> bool:
    """Is `candidate` inside `root`? Answered by identity, never by string.

    `candidate` may not exist yet (the record destination usually does not), so
    walk its ancestors and ask whether any of them IS `root` — `Path.is_relative_to`
    and `root in candidate.parents` are string tests and were the half that let a
    case-variant root through.
    """
    node = candidate
    while True:
        if _same_dir(node, root):
            return True
        parent = node.parent
        if parent == node:
            return False
        node = parent


def _verify_record_landed_outside(out: Path, *measured_roots: Path) -> None:
    """Observe where the record ACTUALLY landed; delete it and refuse if in-tree.

    The pre-write refusal is a PREDICTION, and five prior cycles were defeated by
    predicting a path the kernel then resolved differently (five exclusions, then
    `abspath`'s lexical `..`). This is the fail-closed half that makes the class
    terminate: after `os.replace` the file EXISTS, so `realpath(out)` follows the
    final component too and reports the physical file the kernel created. If that
    is inside any tree the pin measures, the record is deleted and the invocation is
    a usage error — the prediction no longer has to be right in any future spelling,
    because the fact is checked.
    """
    landed = Path(os.path.realpath(os.fspath(out)))
    for root in measured_roots:
        root_real = Path(os.path.realpath(os.fspath(root)))
        if _inside_tree(landed, root_real):
            with contextlib.suppress(OSError):
                os.unlink(out)
            raise UsageError(
                f"--record-out {out} was written inside the measured tree ({root}) "
                "and has been deleted. The tool's own record must not be part of "
                "the dirt it is measuring. Write the record outside the measured "
                f"tree (the default is {_default_record_out()}) and copy or upload "
                "it afterwards."
            )


def _remove_in_tree_record(out: Path, *measured_roots: Path) -> list[Path]:
    """Best-effort removal of a record that landed inside a measured tree.

    `_verify_record_landed_outside` already unlinks it, but that unlink is
    best-effort (the `OSError` is suppressed), and #4585 measured the consequence:
    with the unlink failing, the verification still raised `UsageError` and `main`
    returned 2 while the COMPLETE record JSON survived inside the measured tree.
    The caller runs this during cleanup so the deletion is retried rather than
    abandoned after one attempt.

    Returns the paths that could NOT be removed — empty on success — so the caller
    reports the residual honestly instead of implying a clean tree. A path that is
    not inside a measured tree, or does not exist, is nothing to do: the write
    never landed it there.
    """
    landed = Path(os.path.realpath(os.fspath(out)))
    in_tree = any(
        _inside_tree(landed, Path(os.path.realpath(os.fspath(root))))
        for root in measured_roots
    )
    if not in_tree or not os.path.lexists(os.fspath(out)):
        return []
    try:
        os.unlink(out)
    except OSError:
        return [out]
    return []


def _refuse_in_tree_record_out(out: Path, *measured_roots: Path) -> None:
    """Usage error when the receipt would land inside a measured tree (#4203).

    The pin's one-directional property is that a genuinely dirty tree must never
    read clean. The tool's own record used to be excluded from the measurement BY
    PATH, and that exclusion over-matched five different ways, each hiding real
    dirt. The owner ruled (option (a), #4572) that the record must not live in the
    measured tree at all: with the receipt outside, `_porcelain_digest` performs NO
    path-based exclusion, so there is nothing left to over-match.

    Every tree the pin measures is checked. `REPO_ROOT` is always one of them —
    `pin.post_review_dirty` is measured on the INVOKING checkout even when `--ref`
    points the run at a detached worktree — so an in-repo receipt would dirty the
    pin whether or not `--ref` was given.
    """
    location = _written_location(out)
    for root in measured_roots:
        root_real = Path(os.path.realpath(os.fspath(root)))
        if _inside_tree(location, root_real):
            raise UsageError(
                f"--record-out {out} is inside the measured tree ({root}). The "
                "tool's own record would then be part of the dirt it is "
                "measuring, falsely reporting a post-review edit. Write the "
                "record outside the measured tree (the default is "
                f"{_default_record_out()}) and copy or upload it afterwards."
            )


def _build_record(args: argparse.Namespace) -> dict:
    # M52/C1: `closing` is accepted ONLY with an explicit `--pairing-ref` — without
    # it there is no ref to pair against, so a closing claim has no antecedent and
    # the role is a label with nothing behind it. Enforced BEFORE any measurement or
    # record construction: a usage error writes NO record and exits 2 (this used to
    # fall through to exit 3 and still write a record).
    if args.record_role == "closing" and not args.pairing_ref:
        raise UsageError("--record-role closing requires --pairing-ref")
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
    # #4203 owner ruling (option (a), #4572): the record must not live inside the
    # measured tree. Refuse it BEFORE any measurement — and before the `--ref`
    # worktree exists — so a usage error costs no measurement and leaves no
    # worktree behind. `REPO_ROOT` is checked first because `post_review_dirty` is
    # measured on the invoking checkout even when `--ref` measures elsewhere.
    if args.record_out is not None:
        _refuse_in_tree_record_out(args.record_out, REPO_ROOT)
    if args.ref:
        requested_ref = _git("rev-parse", f"{args.ref}^{{commit}}")
        measured_root, worktree_added = _worktree_at(requested_ref, run_root, "worktree")
        if args.record_out is not None:
            _refuse_in_tree_record_out(args.record_out, measured_root)
    commit = _git("rev-parse", "HEAD", cwd=measured_root)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=measured_root)

    ceiling = args.load_ceiling
    cur_load = load1()
    if args.environment_error:
        raise RuntimeError(args.environment_error)

    runs: list[dict] = []
    tree_states: list[tuple[str, bool]] = []
    porcelain = ""
    dirty = False
    try:
        if cur_load > ceiling:
            raise RuntimeError(f"load {cur_load} exceeds ceiling {ceiling}")
        # D11: the baseline digest is captured BEFORE the first run, so a tree that
        # moves DURING run 1 is caught. Capturing it after run 1 (the old code) made
        # run 1 compare with itself — `tree_moved` was False for run 1 by
        # construction — so a tree that moved only during run 1 left
        # `pin-not-airtight` passing while the tree moved.
        base_digest, _base_dirty = _porcelain_digest(measured_root)
        for i in range(1, args.n + 1):
            runs.append(_run_once(files, measured_root, run_root, i, args.marker,
                                  args.run_timeout))
            # The per-run tree state. The cleanliness digest MUST be taken while the
            # measured tree still EXISTS (see below) — and it is taken once per run so
            # `tree_moved` is MEASURED: a run whose tree digest differs from the
            # pre-run baseline is a moved tree, which `pin-not-airtight` refuses.
            tree_states.append(_porcelain_digest(measured_root))
        # `tree_moved` is per-run: True iff this run's tree state differs from the
        # digest captured before run 1.
        for r, (digest, _d) in zip(runs, tree_states, strict=True):
            r["tree_moved"] = digest != base_digest
        # The pin's own cleanliness is the FINAL state, read before the `finally`
        # that removes the worktree. (It used to run after the `finally`, which
        # removed the detached worktree — so a --ref measurement stat'd a path that
        # was already gone and died with FileNotFoundError, losing the field that
        # says the tree did not move.)
        porcelain, dirty = tree_states[-1] if tree_states else ("", False)
    finally:
        if worktree_added:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(measured_root)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )

    # R2/D24: the certificate binds to the REVIEWED head SHA. `commit` is the
    # MEASURED commit (which may be a detached `--ref`); `head_sha` is the invoking
    # checkout's HEAD, read independently — not the same variable copied into the
    # field it is later compared against. A `--ref` re-run after the branch moved,
    # or a checkout carrying uncommitted post-review edits, is refused by
    # `certificate-not-bound-to-review-head`. A literal (`head_sha = commit`,
    # `post_review_dirty = False`) made that conjunct unfailable.
    reviewed_head = _git("rev-parse", "HEAD")
    _, review_dirty = _porcelain_digest(REPO_ROOT)

    # D16: when a pairing ref is declared, the RED is re-run AT THE PAIRING REF
    # inside this invocation, so `at_fixed_commit` becomes a MEASURED claim (the
    # red appeared at the pairing ref; it did not appear at the measured, fixed
    # commit). The producer previously hardcoded `attempted: False` /
    # `appeared: None` / `rate_change: False`, which made `no-rate-change`
    # unreachable for EVERY record the producer could emit.
    baseline_run = None
    pair_ref = None
    pairing_is_ancestor = False
    if args.pairing_ref:
        pair_ref = _git("rev-parse", f"{args.pairing_ref}^{{commit}}")
        # C1/D16: a `--pairing-ref` must be a STRICT ancestor of the measured commit.
        # Equal, descendant or unrelated is a usage error, validated BEFORE the
        # baseline so a bad ref costs no measurement and writes no record. This is
        # load-bearing: `--pairing-ref` drives `rate_change`, so without it an
        # arbitrary ref would make `no-rate-change` satisfiable by a red measured
        # anywhere.
        pairing_is_ancestor = _strict_ancestor(pair_ref, commit)
        if not pairing_is_ancestor:
            raise UsageError(
                f"--pairing-ref {pair_ref} must be a strict ancestor of the "
                f"measured commit {commit}"
            )
        pair_root = run_root / "pairing"
        pair_root.mkdir(parents=True, exist_ok=True)
        pair_measured, pair_added = _worktree_at(pair_ref, run_root, "pairing-worktree")
        try:
            pair_base_digest, _pbd = _porcelain_digest(pair_measured)
            baseline_run = _run_once(files, pair_measured, pair_root, 1, args.marker,
                                     args.run_timeout)
            pair_post_digest, _ppd = _porcelain_digest(pair_measured)
            # The baseline is a RUN too: persist its own tree state so `pin-not-airtight`
            # and a re-evaluating verifier can see whether the pairing worktree moved.
            baseline_run["tree_moved"] = pair_post_digest != pair_base_digest
        finally:
            if pair_added:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(pair_measured)],
                    capture_output=True, text=True, cwd=str(REPO_ROOT),
                )

    measured_red_runs = [r for r in runs if r["bucket"] in BUCKETS_RED]
    measured_green_runs = [r for r in runs if r["bucket"] in BUCKETS_PASSING]
    measured_red_run = next(iter(measured_red_runs), None)

    if baseline_run is not None:
        # The red the record attests to is the pairing-ref re-run, never the
        # measured runs' own red: a closing record's runs are green by definition,
        # so its red identity can only come from the baseline.
        attested_red_runs = (
            [baseline_run] if baseline_run["bucket"] in BUCKETS_RED else []
        )
        attested_red_run = attested_red_runs[0] if attested_red_runs else None
        red_ref = pair_ref
        red_ref_tree = _git("rev-parse", f"{pair_ref}^{{tree}}")
    else:
        attested_red_runs = measured_red_runs
        attested_red_run = measured_red_run
        red_ref = requested_ref or commit
        red_ref_tree = tree

    # F17/D14: the OVERLAP set is the band of EVERY run that entered the comparison
    # — the measured runs AND the attested red (the pairing-ref baseline, which is a
    # run but is not in `runs`). Built from `runs` alone the set held only green
    # bands on the one shape that can close, so `len(bands) == 1` was trivially true
    # and the conjunct could never fire: a red measured at L-C and greens at L-A
    # recorded `overlap: true` and a CLOSING verdict.
    bands = {r["load"]["band"] for r in runs}
    if attested_red_run is not None:
        bands.add(attested_red_run["load"]["band"])

    # F17/plan schema: the red/green mix counts every run that entered the
    # comparison — the measured `runs` PLUS the attested baseline red. Derived from
    # the run objects, never the `{"red": 1, "green": 0}` literal that contradicted
    # a closing record whose own `runs` were all green and whose
    # `observed_failure_rate` was 0.0.
    contributing_runs = list(runs)
    if attested_red_run is not None and all(attested_red_run is not r for r in runs):
        contributing_runs.append(attested_red_run)
    red_green_mix = {
        "red": sum(1 for r in contributing_runs if r["bucket"] in BUCKETS_RED),
        "green": sum(1 for r in contributing_runs if r["bucket"] in BUCKETS_PASSING),
    }
    assert sum(red_green_mix.values()) == len(contributing_runs), (
        "red_green_mix must account for every contributing run: "
        f"mix={red_green_mix} runs={len(contributing_runs)}"
    )

    # F4a: derived from the attested red runs' own recorded file lists, never a
    # literal.
    same_file_list = _red_file_list_matches(attested_red_runs, files)
    # F17/D14: `red_band`/`declared_band` come from the ATTESTED red, not from
    # `runs[-1]` — on a closing shape the last measured run is GREEN, so writing its
    # band as the red's band recorded a green L-A run as the red regime while the red
    # was actually measured at L-C.
    red_band = (
        attested_red_run["load"]["band"] if attested_red_run is not None
        else (measured_red_run or runs[-1])["load"]["band"]
    )
    green_band = (measured_green_runs[0]["load"]["band"] if measured_green_runs else red_band)
    cause = attested_red_run["redis_log_cause"] if attested_red_run else None
    cause_evidence = attested_red_run["cause_evidence"] if attested_red_run else {}
    # D9 conjunct 11 (`no-rate-change`): a red was demonstrated (at the pairing ref,
    # or among the measured runs when no pairing ref is given) and did NOT appear at
    # the measured, fixed commit. `attempted` records that a red was demonstrated at
    # ALL — with no red there is no rate to compare — and is NOT a constant:
    # `bool(runs)` was one (`main()` rejects `--n < 2`, so it was True on every
    # record that could reach the conjunct). `closes_issue` no longer ANDs it, so it
    # carries no protection it cannot supply; `rate_change` is the falsifiable claim.
    attempted = bool(attested_red_run)
    appeared = bool(measured_red_runs)
    rate_change = bool(attested_red_run) and not appeared
    # DERIVED from the label it summarises, never a literal (it was `True`).
    # `attributable` is a claim ABOUT `red.cause`, so a record with `red.cause ==
    # null` (no red run) or `unattributed` was claiming an attribution it does not
    # have.
    attributable_ = attributable(cause)

    # The verdict summarises the red the record actually ATTESTED TO. Derived from
    # the measured runs it printed ALL-GREEN with `green_only: true` while
    # `red.cause` was non-null (the pairing-ref red) — the exact pair `main()` prints
    # as the human summary.
    if attested_red_run is None:
        red_status = "ALL-GREEN"
    elif baseline_run is not None:
        red_status = "RED-AT-PAIRING-REF"
    else:
        red_status = "RED-AT-PINNED-REF"

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
            "head_sha": reviewed_head,
            "head_sha_verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "post_review_dirty": review_dirty,
            "tree_object": tree,
            "requested_ref": requested_ref,
            "pairing_ref": pair_ref,
            "worktree_clean": not dirty,
            "porcelain_digest": porcelain,
            "measured_root": str(measured_root),
            "environment_pinned": False,
        },
        "n": {
            "requested": args.n,
            "mode": "explicit",
            "observed_failure_rate": (len(measured_red_runs) / len(runs)) if runs else 0.0,
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
            "ref": red_ref,
            # D16/C1: DERIVED from the ancestry RESULT, never from flag presence.
            "ref_role": (
                "per-cause" if (pairing_is_ancestor and getattr(args, "cause", None))
                else "last-before-first-family-fix" if pairing_is_ancestor
                else "pinned-head-pre-fix"
            ),
            "ref_tree_object": red_ref_tree,
            "cause": cause,
            "cause_evidence": cause_evidence,
            "red_green_mix": red_green_mix,
            # D16: the baseline run is PERSISTED, not discarded. It is the only
            # evidence from which `rate_change` can be re-derived, and `exit_code`
            # reads its bucket to classify a non-overlap as the red D14 requires.
            "baseline_run": baseline_run,
            "at_fixed_commit": {
                "attempted": attempted,
                "appeared": appeared,
                "rate_change": rate_change,
                "mutation": None,
                "mutation_operator": None,
                "mutation_target_is_fix_branch": False,
                "mutation_red_returned": False,
                # R1/D23: caller-declared, so the conjunct can be REACHED (a
                # produced record can pass) and can FAIL (an internal seam or an
                # empty assertion). Left as literals these were `None` in every
                # produced record, so the conjunct could never pass.
                "surface": getattr(args, "surface", None),
                "surface_assertion": getattr(args, "surface_assertion", None),
            },
            "same_file_list": same_file_list,
        },
        "verdict": {
            "status": red_status,
            "green_only": attested_red_run is None,
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
        "PAIRED-RED-DEMONSTRATED" if ok else red_status
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
        # R1/D23: the consumer shipping surface the mutation/rate-change proof is
        # asserted against, and the resolving test-ID that pins it. Both are
        # CALLER-declared: the tool cannot infer which test exercises
        # `tortoise_search` vs an internal helper. The conjunct
        # `certification-not-on-shipping-surface` rejects anything that is not a
        # member of SHIPPING_SURFACES with a non-empty assertion, so the free-form
        # values are the falsifiable input, not an argparse allowlist.
        p.add_argument("--surface", default=None)
        p.add_argument("--surface-assertion", default=None, dest="surface_assertion")
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
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"environment error: {exc}", file=sys.stderr)
        return 2

    out = args.record_out or _default_record_out()
    measured_roots = [REPO_ROOT, Path(rec["pin"]["measured_root"])]
    try:
        _write_record(rec, out)
        # #4203: the pre-write refusal PREDICTS the destination; this OBSERVES it.
        # A record that slipped past the prediction is deleted and refused here, so
        # no future spelling of the path can leave the tool's own record as dirt in
        # a tree it is measuring.
        _verify_record_landed_outside(out, *measured_roots)
    except BaseException as exc:
        # #4585: cleanup is UNCONDITIONAL on failure — the exception TYPE cannot
        # tell us whether residue survives, because BOTH unlinks that decide it are
        # best-effort: `_write_record` suppresses its own, and
        # `_verify_record_landed_outside` suppresses its own. A non-OSError from
        # `json.dump` (or a `UsageError` from the post-write verification) whose
        # unlink failed therefore used to escape with the temp — holding the
        # COMPLETE record — still inside the measured tree. Sweep FIRST, then map
        # the exit, so no failure path returns or raises without having cleaned up.
        swept = _sweep_record_residue(
            *measured_roots,
            # The physical parent the temp is created in, so a temp that resolved
            # to a NESTED in-tree directory (a symlink swapped after the pre-write
            # refusal) is swept too — the measured roots alone only reach the
            # top-level collapse.
            Path(os.path.realpath(os.path.dirname(os.fspath(out)))),
        )
        if isinstance(exc, UsageError):
            # #4585 (b): the record itself landed inside the tree and the
            # verification's own unlink failed. Retry the removal here; if it STILL
            # cannot be removed, say so rather than imply a clean tree.
            survivors = _remove_in_tree_record(out, *measured_roots)
            print(f"usage error: {exc}", file=sys.stderr)
            if survivors:
                print(
                    "error: the in-tree record at "
                    f"{', '.join(str(s) for s in survivors)} could NOT be removed; "
                    "the tool's own bytes are still inside the measured tree. "
                    "Delete it manually before trusting the pin.",
                    file=sys.stderr,
                )
            return 2
        if isinstance(exc, OSError):
            # #4585: a failed write is a REFUSAL, never a traceback. The environment
            # made certification impossible, so the exit code is 2 (environment
            # error) — but the operator must be told WHAT failed, WHY it matters and
            # WHAT to do, and any `.rec-*` temp left inside a measured tree has been
            # swept above: the temp holds a complete record, and leaving it would
            # make the tool's own bytes part of the dirt the pin measures.
            detail = (
                f"removed {len(swept)} temp file(s) from the measured tree"
                if swept else "no temp residue was found in the measured tree"
            )
            print(
                f"error: could not write the record to {out}: {exc}. "
                "The record was NOT written, so no certification exists for this "
                f"head ({detail}). Write the record outside the measured tree (the "
                f"default is {_default_record_out()}) and re-run; if the destination "
                "already exists it must be a FILE, not a directory.",
                file=sys.stderr,
            )
            return 2
        # Anything else (MemoryError, TypeError, KeyboardInterrupt …): the sweep
        # has already run, so the bytes are gone; re-raise rather than mislabel it
        # as a refusal.
        raise
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
