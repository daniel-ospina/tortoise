#!/usr/bin/env python3
"""Owner-readiness CONDITION 2: every REQUIRED check is green at the head.

WHY THIS FILE EXISTS
--------------------
The readiness verdicts the fleet posted tonight did not agree with each other or
with the rail (#10962 read "READY" and then merged as `CONFLICTING` with
`ai-review-gate: CANCELLED`). Every lane that tried to answer "are all the
REQUIRED checks green?" improvised:

  * one lane called `gh api repos/…/branches/main/protection` and got
    **403 `Resource not accessible by personal access token`** — a fine-grained
    PAT needs `Administration: read` for that endpoint, so the lane credential
    cannot answer at all;
  * one counted EVERY check-run, required or not;
  * one counted only the checks that happened to report;
  * one treated `cancelled` as a pass.

This tool is the ONE answer, and it takes its required set from the REPO:

  1. **`.mergify.yml`** — the merge queue's own configuration contract says the
     live required set is `queue_conditions ∪ merge_conditions` (checks), "each
     required check named in EXACTLY ONE of the two lists". The parse is
     `.github/scripts/check-required-set.py::load_mergify()` — IMPORTED, never
     reimplemented, so there is no second parser to drift (its own header, and
     the contract's ⚠️, is that classification is by the `check-success=`
     prefix only: `base=main`, `-draft` and `-label=superseded-by-fold` are NOT
     part of the invariant, and a negated `-check-success=X` is the INVERSE of
     a requirement).
  2. **`docs/ci/required-contexts.json`** — the committed record of live
     protection, used as the FALLBACK only when `.mergify.yml` cannot be read,
     and always COMPARED when it can (a disagreement between the enforced config
     and the recorded snapshot is reported, never silently resolved in favour of
     the one that reads green). An EMPTY record is refused as a fallback.
  3. **the guard's `injected` bucket** — `.mergify.yml` contract point 1 says a
     check required on `main` but named in NEITHER list is "still known to
     Mergify" and enforced by branch-protection injection. `queue ∪ merge` is
     therefore NOT the whole required set: the names are read from
     `check-required-set.py::injected_names()` (the only record of that bucket)
     and folded into the required set, so a red INJECTED check cannot read PASS.
  4. **live branch protection** — only under `--live`, because it needs
     `Administration: read`; under that credential the derived set must AGREE
     with live or this tool refuses to pass. GraphQL is not a way around the
     scope: `repository.branchProtectionRules` returns the same
     `FORBIDDEN / Resource not accessible by personal access token`. The read is
     `check-required-set.py::read_live_protection()` — the same imported module,
     so there is ONE reader of branch protection, not two.

AN UNREADABLE REQUIRED SET IS A FAIL, NEVER AN ASSUMED PASS
-----------------------------------------------------------
If neither the config nor the snapshot can be read, the verdict is
`FAIL(unreadable)` and the exit status is 2. "Nothing was compared" is never a
pass — the same rule `check-required-set.py` states for itself.

WHAT "GREEN" MEANS HERE — AND WHY IT IS NOT `ci_verdict.aggregate`
------------------------------------------------------------------
This gate answers the MERGIFY question ("would `check-success=<name>` be
satisfied?"), not the merge-rail's health question. For a REQUIRED check that
means **`conclusion == "success"`**, and nothing else:

    success                                  PASS
    neutral, skipped                         FAIL — not a `check-success` pass
                                              (.mergify.yml contract point 3)
    cancelled, stale                         FAIL — never green (the #10962 shape)
    failure, timed_out, action_required,
      startup_failure, any unknown conclusion FAIL — fail-closed polarity
    completed with a null/absent conclusion  FAIL — undocumented is not green
    not completed (queued/in_progress/…)     FAIL — in-flight is not yet ready
    no such check-run on the head            FAIL — missing is not green

⚠️ That is deliberately STRICTER than `tools/ci_verdict.py`'s aggregate, where
`cancelled`/`stale` are ABSENT (non-red) and `skipped`/`neutral` are GREEN, so a
commit whose required check was cancelled can read `green` there. The per-group
reading is still `ci_verdict`'s — grouping by `(app, workflow, job)` and the
NEWEST ATTEMPT per group — this module only re-decides the polarity for the
required names. Do not "fix" one to match the other: they answer different
questions, and the merge rail is the one that must not over-block.

CONDITION 2, AND WHAT IS NOT HERE
---------------------------------
When the config parses, the committed snapshot is a CROSS-CHECK, and the direction
of a disagreement decides the outcome: a name the RECORD requires but the config
does not derive is refused (`FAIL(config-error)`) — the record is the live picture
and a config edit cannot remove a live requirement, so grading would certify a head
against a smaller set than the queue blocks on. The opposite direction only means
the record is stale, which is inert. `--live` SETTLES which side is current: if live
protection agrees with the config, the disagreeing record is provably stale and the
drift refusal lifts (`source` then says `drift cleared by --live`); if live names
something else, the run refuses.

The owner-readiness gate has several conditions; this tool implements ONLY
condition 2 ("all required checks green at the head") and the CLI marks the
others as not-implemented rather than guessing them — an honest stub beats a
wrong pass. `--required-only` prints the derived set alone (the answer to "what
IS required", available with any credential).

EXIT CODES
----------
0  the REQUESTED work succeeded, and nothing more:
     · with `--pr N`  — every required check is `success` at that head (PASS);
     · with `--required-only` (and no `--pr`) — the required set was derived and
       printed. Its JSON `state` is `required-set-only`, NOT `PASS`: exit 0 here
       does **not** mean a head is ready, because no head was read.
1  FAIL — a required check is not green, or a configuration error: a name in
   BOTH condition lists (the entry gate and the merge gate cannot both be right),
   or a committed record of live protection naming a check the config does not
   derive (that name would go UNGRADED). A MEASURED fail outranks an unreadable
   sibling in a multi-`--pr` run: exit 2 means "retry", and retrying a PR that
   already answered FAIL never terminates.
2  cannot measure — the required set or the check surface could not be read, and
   ALSO a bare invocation with neither `--pr` nor `--required-only`: nothing was
   measured, and "nothing compared" is never a pass

Multi-`--pr` runs report EVERY PR in the JSON record (a failing one is never
silently omitted) and take the WORST outcome, with exit 1 dominating exit 2.

⚠️ A caller-supplied `--mergify`/`--snapshot` is a substitution, not the repo's own
config: the record's `source` names the path (`mergify:/tmp/x.yml`) so a downstream
reader can tell where the certification came from.

USAGE
-----
    uv run python tools/ready_for_owner.py --required-only
    uv run python tools/ready_for_owner.py --pr 10702
    uv run python tools/ready_for_owner.py --pr 10702 --pr 10852 --json
    uv run python tools/ready_for_owner.py --pr 10702 --live   # needs Administration: read
"""

from __future__ import annotations

import sys

# ⛔ THE #5128 GUARD IS THE FIRST STATEMENT AFTER THE DOCSTRING / `__future__` /
# `import sys`, and that position is a REPO CONTRACT, not a style choice:
# `tests/test_entry_point_python_guard.py::_guard_index` requires exactly
# `[docstring] [__future__ imports…] import sys <guard>` and reds otherwise. A
# module-level 3.12-only syntax/import raises before a later guard could run, so a
# guard placed after the import block protects nothing. This is the same shape as
# `tools/ci_verdict.py:104-113`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ready_for_owner.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ready_for_owner.py`"
    )

import argparse
import importlib.util
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / ".github" / "scripts" / "check-required-set.py"
MERGIFY_PATH = REPO_ROOT / ".mergify.yml"
SNAPSHOT_PATH = REPO_ROOT / "docs" / "ci" / "required-contexts.json"
DEFAULT_REPO = "daniel-ospina/tortoise"

# Per-required-check states. Only PASS is green; every other spelling is a FAIL
# with its own reason, so a reader never has to infer which failure it was.
PASS = "pass"
FAIL = "fail"

# Gate states.
GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_CONFIG_ERROR = "FAIL(config-error)"
GATE_UNREADABLE = "FAIL(unreadable)"
# NOT a gate verdict: `--required-only` answers "what IS required", never "is this
# head ready". Reporting PASS there would claim a measurement that never happened —
# the exact "nothing compared is not a pass" shape this tool removes.
STATE_REQUIRED_SET_ONLY = "required-set-only"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_UNREADABLE = 2


class Unreadable(Exception):
    """The required set could not be derived. Never treated as an empty set."""


@dataclass(frozen=True)
class RequiredSet:
    """The required check names, and where they came from.

    THREE buckets, not two. `queue`/`merge` come from `.mergify.yml`; `injected`
    comes from the guard's own enumeration (`.github/scripts/check-required-set.py`
    `REQUIRED_SET[…][0] == "injected"`), which is how a check that is REQUIRED ON
    MAIN but named in NEITHER list is recorded — `.mergify.yml`'s contract point 1
    says such a check "is still known to Mergify" and is enforced by
    branch-protection injection. Omitting that bucket would let a red INJECTED
    required check read PASS, so it is folded into `names` (today it is empty, and
    the note says so rather than leaving the reader to assume).
    """

    queue: frozenset[str]
    merge: frozenset[str]
    source: str
    notes: tuple[str, ...] = ()
    injected: frozenset[str] = frozenset()
    # Names the COMMITTED RECORD of live protection requires but the config does
    # not derive. Directional on purpose: the record is the live picture, and a
    # config edit cannot remove a live requirement (only a branch-protection edit
    # can), so this direction means "these would go UNGRADED" — unsafe. The
    # opposite direction only means the record is stale, which is inert.
    unsafe_drift: frozenset[str] = frozenset()

    @property
    def names(self) -> frozenset[str]:
        """Every required name: both config lists AND the injection bucket."""
        return self.queue | self.merge | self.injected

    @property
    def overlap(self) -> frozenset[str]:
        """Names in BOTH lists. The contract requires EXACTLY ONE — never empty-by-luck."""
        return self.queue & self.merge


# ── the derived set ───────────────────────────────────────────────────────────


def load_checker(path: Path | None = None) -> ModuleType:
    """Import `check-required-set.py` (a dashed filename) as a module.

    Imported rather than copied: it is the repo's ONE parser of `check-success=`
    conditions, and it is what the CI guard reads. A second parser here would be
    a second thing to keep right — the class of defect this lane exists to close.
    """
    path = path or CHECKER_PATH
    spec = importlib.util.spec_from_file_location("tortoise_check_required_set", path)
    if spec is None or spec.loader is None:
        raise Unreadable(f"cannot load the required-set parser at {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError, MemoryError) as exc:
        raise Unreadable(
            f"cannot load the required-set parser at {path} ({type(exc).__name__}: {exc})"
        ) from exc
    return module


def read_snapshot(path: Path | None = None) -> frozenset[str] | None:
    """The committed record of live protection (`required_contexts`), or None.

    PRESENT-but-unusable is an error, not an absence: a corrupt snapshot must not
    read as "no fallback needed", because the caller would then trust the config
    alone while claiming it had a cross-check.
    """
    path = path or SNAPSHOT_PATH
    try:
        # `Path.exists()` re-raises EACCES on 3.12 (it swallows only
        # ENOENT/ENOTDIR/EBADF/ELOOP). Unguarded it escaped as a TRACEBACK — exit 1,
        # which the rail reads as "a required check is red", and zero bytes on
        # `--json` stdout. The guard this module imports guards the same call for
        # the same reason.
        exists = path.exists()
    except OSError as exc:
        raise Unreadable(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    if not exists:
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError, MemoryError) as exc:
        raise Unreadable(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    if not isinstance(doc, dict):
        raise Unreadable(f"{path}: top level is not a mapping — cannot measure")
    contexts = doc.get("required_contexts")
    if contexts is None:
        # PRESENT but declaring nothing is NOT an absence: returning None made the
        # cross-check vanish silently and left the derivation's failure message
        # claiming "no committed snapshot" about a file that is committed.
        raise Unreadable(
            f"{path}: present but declares no `required_contexts` — unusable as a "
            "record of live protection"
        )
    if not isinstance(contexts, list) or any(not isinstance(n, str) for n in contexts):
        raise Unreadable(f"{path}: required_contexts must be a list of strings")
    return frozenset(contexts)


def derive_required_set(
    mergify_path: Path | None = None,
    snapshot_path: Path | None = None,
    checker: ModuleType | None = None,
) -> RequiredSet:
    """The required set: `.mergify.yml` first, the committed snapshot as fallback.

    Raises `Unreadable` when BOTH are unavailable — the caller must then FAIL,
    never assume. The snapshot is compared whenever the config DID parse, and a
    disagreement is reported in `notes` (the config is what Mergify enforces, so
    it wins; the snapshot is inert and cannot enforce anything).
    """
    mergify_path = mergify_path or MERGIFY_PATH
    notes: list[str] = []

    snapshot: frozenset[str] | None
    try:
        snapshot = read_snapshot(snapshot_path)
    except Unreadable as exc:
        # The snapshot is only a fallback + cross-check; a broken one is recorded
        # and does not by itself make the set unreadable. It is NOT folded into
        # the pass: the note is printed on every run.
        notes.append(f"snapshot unreadable ({exc}) — config is the only source")
        snapshot = None
    if snapshot is not None and not snapshot:
        # An EMPTY record is not a fallback — it is the "nothing was compared"
        # shape, and falling back to it would print "contract satisfied" over a
        # set of zero names.
        notes.append("snapshot records an EMPTY required set — refusing it as a fallback")
        snapshot = None

    module = checker or load_checker()

    # A check that is required on `main` but named in NEITHER list is still
    # enforced — by branch-protection injection (.mergify.yml contract point 1).
    # The guard's enumeration is the only record of that bucket, so it is read from
    # the SAME imported module rather than assumed empty. An ABSENT reader is NOT
    # an empty bucket: a renamed/removed `injected_names()` would otherwise make a
    # red injected check read PASS, silently.
    #
    # ⛔ READ BEFORE the config parse, deliberately: `.mergify.yml` is unreadable
    # exactly WHILE IT IS BEING EDITED, which is when the snapshot fallback runs —
    # and a fallback that skipped this bucket would return a set missing an injected
    # required check, i.e. a false PASS on the one path where nobody cross-checks.
    injected_names = getattr(module, "injected_names", None)
    if not callable(injected_names):
        raise Unreadable(
            "the guard exposes no injected_names() — the injection bucket cannot be "
            "accounted for, and assuming it empty would let a red injected required "
            "check read PASS"
        )
    try:
        injected = frozenset(injected_names())
    except Exception as exc:  # a broken enumeration must not silently drop names
        raise Unreadable(
            f"the guard's injected bucket could not be read ({type(exc).__name__}: {exc})"
        ) from exc

    try:
        parsed = module.load_mergify(mergify_path)
        queue = frozenset(parsed["queue"])
        merge = frozenset(parsed["merge"])
    except Unreadable:
        raise
    except Exception as exc:  # CannotMeasure and anything else the parser raises
        if snapshot is None:
            raise Unreadable(
                f"required set unreadable: .mergify.yml ({mergify_path}: "
                f"{type(exc).__name__}: {exc}) and no usable committed snapshot to fall back on"
            ) from exc
        notes.append(
            f".mergify.yml unreadable ({type(exc).__name__}: {exc}) — fell back to the "
            f"committed snapshot, which is NOT the enforced config"
        )
        notes.append(
            f"fallback keeps the injection bucket ({sorted(injected)})"
            if injected else "fallback: injection bucket empty"
        )
        # The snapshot is the live record, so it may already name an injected check;
        # `names` is a union, so passing both is exact either way.
        return RequiredSet(queue=snapshot, merge=frozenset(),
                           source="snapshot" + (
                               "" if snapshot_path is None else f":{snapshot_path}"),
                           notes=tuple(notes), injected=injected)

    notes.append(
        f"injection bucket: {sorted(injected)}" if injected
        else "injection bucket: empty — every required name is named in a condition list"
    )
    if not (queue | merge | injected):
        raise Unreadable(
            "required set is EMPTY after derivation — nothing to compare is not a pass"
        )

    unsafe_drift: frozenset[str] = frozenset()
    # The record's LABEL, so a caller-supplied file is never reported as the
    # committed one (the cross-check must not go vacuous while the note claims
    # `docs/ci/required-contexts.json` agreed).
    record = ("docs/ci/required-contexts.json"
              if snapshot_path is None or snapshot_path == SNAPSHOT_PATH
              else str(snapshot_path))
    if snapshot is not None:
        derived = queue | merge | injected
        unsafe_drift = snapshot - derived
        stale = derived - snapshot
        if unsafe_drift:
            # The record is the LIVE picture and a config edit cannot remove a live
            # requirement — so the config is missing names that are still enforced,
            # and grading without them would certify a head against a smaller set
            # than the queue actually blocks on. Refused by `evaluate()`.
            notes.append(
                f"⚠️ CONFIG/record disagreement: {record} (the record of live "
                f"protection that was read) requires {sorted(unsafe_drift)}, "
                "which the config does NOT derive — those names would go UNGRADED. "
                "Fix the config, or re-record protection if the requirement is gone; "
                "`--live` resolves which side is current"
            )
        if stale:
            notes.append(
                f"{record} does not list {sorted(stale)} — it is stale, and inert "
                "(the config is enforced)"
            )
        if not unsafe_drift and not stale:
            notes.append(f"{record} agrees with the derived set")
    # A credential-free substitution THE CALLER supplied must not be
    # indistinguishable from the repo's own config in the machine record.
    source = f"mergify:{mergify_path}" if mergify_path != MERGIFY_PATH else "mergify"
    if record != "docs/ci/required-contexts.json":
        source = f"{source} (record: {record})"
    return RequiredSet(queue=queue, merge=merge, source=source, notes=tuple(notes),
                       injected=injected, unsafe_drift=unsafe_drift)


def _required_doc(required: RequiredSet) -> dict[str, Any]:
    """The machine-readable view of the required set (ONE builder, so a rebuilt
    set cannot emit a stale or differently-keyed document)."""
    return {
        "queue": sorted(required.queue), "merge": sorted(required.merge),
        "injected": sorted(required.injected), "union": sorted(required.names),
        "overlap": sorted(required.overlap),
        "unsafe_drift": sorted(required.unsafe_drift),
        "source": required.source, "notes": list(required.notes),
    }


def read_live_contexts(repo: str = DEFAULT_REPO) -> frozenset[str]:
    """Live branch protection contexts. Needs `Administration: read`.

    The READ is `check-required-set.py::read_live_protection()` — the file this
    module already imports for its parser — so there is ONE reader of branch
    protection, not two that can drift apart (that reader also validates
    `strict`). It is pinned to `daniel-ospina/tortoise`; asking for another repo
    is refused rather than answered from the wrong repository.
    """
    if repo != DEFAULT_REPO:
        raise Unreadable(
            f"--live is only supported for {DEFAULT_REPO} (the guard's reader is pinned "
            f"to it); {repo} was refused rather than read from the wrong repo"
        )
    checker = load_checker()
    try:
        contexts, _strict = checker.read_live_protection()
    except Exception as exc:  # its CannotMeasure, and anything the shell raises
        message = str(exc)
        # ⚠️ ORDER MATTERS, and the guard's own wording is why: `read_live_protection`
        # prefixes EVERY failure with "could not read branch protection (needs
        # admin-scoped credentials): " and then appends gh's stderr. Testing for that
        # prefix first made a rate-limited read report a CREDENTIAL failure — sending
        # the lane to drop the live cross-check instead of waiting on the bucket.
        # The bucket is matched FIRST; the scope markers are matched after it.
        if "rate limit" in message.lower():
            raise Unreadable(
                f"GH-RATE-LIMITED({repo}/branches/main/protection,{message[:200]})") from exc
        if "personal access token" in message:
            raise Unreadable(
                f"PROTECTION-UNREADABLE({repo}: scope, not rate limit) — the credential "
                "lacks Administration: read; derive from .mergify.yml instead "
                f"[{message[:200]}]"
            ) from exc
        raise Unreadable(f"PROTECTION-UNREADABLE({repo}: {message[:200]})") from exc
    return frozenset(contexts)


# ── condition 2 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Row:
    """One required check, at one head."""

    name: str
    state: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    state: str
    rows: tuple[Row, ...] = ()
    reasons: tuple[str, ...] = ()
    required: RequiredSet | None = None
    sha: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == GATE_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "sha": self.sha,
            "reasons": list(self.reasons),
            "rows": [{"name": r.name, "state": r.state, "detail": r.detail} for r in self.rows],
            # ONE builder: a second literal here is how the `--required-only`
            # document and every graded `report["required"]` drift apart.
            "required": None if self.required is None else _required_doc(self.required),
        }


def _groups_for(verdict: Any, name: str) -> list[Any]:
    """Every check-run GROUP whose job name is exactly `name`."""
    return [g for g in verdict.groups if g.job == name]


def _cv() -> Any:
    """`tools.ci_verdict`, imported on demand (pure callers need no sys.path work)."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools import ci_verdict

    return ci_verdict


def _state_of(name: str, group: Any) -> Row:
    """One check-run GROUP's row, fail-closed on every non-`success` reading.

    The PRECEDENCE is `ci_verdict`'s own per-group state, not a second guess:
    a non-completed status carrying a negative conclusion is RED there (a run that
    died mid-flight is not "waiting"), and reporting it as in-flight would tell a
    lane to keep waiting on a check that will never resolve.
    """
    cv = _cv()
    if getattr(group, "voided_red", False):
        return Row(name, FAIL, "red (a red attempt is not cleared by a red-less re-run)")
    state = getattr(group, "state", None)
    conclusion = group.conclusion
    detail = f"{group.status or 'unknown'}/{conclusion}" if conclusion else str(
        group.status or "unknown")
    if group.status == "completed" and conclusion is None:
        # A distinct reason, not just a distinct state: ci_verdict folds this into
        # RED (right verdict), and "red (completed)" would hide WHY.
        return Row(name, FAIL, "completed with no conclusion: undocumented is not green")
    if state == cv.GROUP_RED:
        return Row(name, FAIL, f"red ({detail})")
    if state == cv.GROUP_ABSENT:
        return Row(name, FAIL, f"{conclusion or 'absent'}: never green")
    if state == cv.GROUP_IN_FLIGHT:
        return Row(name, FAIL, f"in-flight ({group.status or 'unknown'}) — not yet ready")
    # A GREEN group is green for the RAIL, which accepts skipped/neutral; this gate
    # answers Mergify's `check-success` question, where only `success` passes.
    if conclusion == "success":
        return Row(name, PASS, "success")
    if conclusion in ("neutral", "skipped"):
        return Row(name, FAIL, f"{conclusion}: not a check-success pass")
    if conclusion is None:
        return Row(name, FAIL, "no conclusion: undocumented is not green")
    return Row(name, FAIL, f"{conclusion}")


def _row_for(name: str, groups: list[Any], surface_in_flight: bool = False) -> Row:
    """One required check's state, taken as the WORST of its groups.

    Several groups may share a name when two workflows emit it; ALL must be
    success. Taking the best would let one green workflow mask a cancelled one —
    the #10962 shape.

    `surface_in_flight` distinguishes the two ways a name can be ABSENT, and the
    distinction is measured, not cosmetic: a job with a `needs:` list gets NO
    check-run until its dependencies finish (measured on #10962: `python-ci-gate`
    absent while 22 test shards were still `queued`). Calling that "never
    produced" would report a deadlock that is not there. Both FAIL — only the
    reason differs.

    ⚠️ The claim is deliberately WEAKER than "its dependencies are running": the
    surface tells us that other checks on this head are still running, not that
    they are the ones this name waits on. A stronger claim would be a guess, and
    a guess in this direction tells a lane to keep waiting on a deadlock.
    """
    if not groups:
        if surface_in_flight:
            return Row(name, FAIL, "not yet reported, and other checks on this head are "
                                  "still running — not yet ready")
        return Row(name, FAIL, "missing: no check-run with this name on the head")
    rows = [_state_of(name, group) for group in groups]
    failing = [row for row in rows if row.state != PASS]
    return failing[0] if failing else rows[0]


def evaluate(
    required: RequiredSet,
    verdict: Any,
    sha: str | None = None,
) -> GateResult:
    """Condition 2's verdict for one head, given a `ci_verdict.Verdict`."""

    def _result(state: str, rows: tuple[Row, ...], reasons: Iterable[str]) -> GateResult:
        return GateResult(state=state, rows=rows, reasons=tuple(reasons),
                          required=required, sha=sha or getattr(verdict, "sha", None))

    # A name in BOTH lists means the entry gate and the merge gate disagree about
    # it; the config cannot be both, so its answer about GREEN cannot be trusted.
    if required.overlap:
        return _result(GATE_CONFIG_ERROR, (), (
            f"configuration error: {sorted(required.overlap)} appear in BOTH "
            "queue_conditions and merge_conditions — the contract requires EXACTLY ONE "
            "of the two lists; fix .mergify.yml (never by deleting a check line)",
        ))
    if not required.names:
        return _result(GATE_UNREADABLE, (), ("required set is EMPTY — nothing was compared",))
    if required.unsafe_drift:
        return _result(GATE_CONFIG_ERROR, (), (
            f"the committed record of live protection requires "
            f"{sorted(required.unsafe_drift)}, which the config does not derive — "
            "grading this head would certify it against a SMALLER set than the queue "
            "blocks on; fix the config or re-record protection (never widen by deleting "
            "a check line from both lists)",
        ))

    # Only genuinely RUNNING groups count: a non-completed status with a negative
    # conclusion is RED (ci_verdict's reading), so counting it here would describe a
    # dead run as a wait.
    surface_in_flight = any(
        getattr(group, "state", None) == _cv().GROUP_IN_FLIGHT for group in verdict.groups
    )
    rows = tuple(
        _row_for(name, _groups_for(verdict, name), surface_in_flight)
        for name in sorted(required.names)
    )
    failing = [r for r in rows if r.state != PASS]
    if failing:
        return _result(GATE_FAIL, rows,
                       (f"{len(failing)} of {len(rows)} required check(s) not green: "
                        + ", ".join(f"{r.name}={r.detail}" for r in failing),))
    return _result(GATE_PASS, rows, (f"all {len(rows)} required check(s) are success",))


def evaluate_head(
    required: RequiredSet,
    repo: str,
    sha: str,
) -> GateResult:
    """Fetch the head's check surface and evaluate. Raises `Unreadable` on a read failure."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools import ci_verdict

    try:
        verdict = ci_verdict.read_verdict(repo, sha)
    except ci_verdict.SurfaceReadError as exc:
        message = str(exc)
        if "rate limit" in message.lower():
            raise Unreadable(
                f"GH-RATE-LIMITED(repos/{repo}/commits/{sha}/check-runs,{message[:160]})"
            ) from exc
        raise Unreadable(f"check surface unreadable for {sha}: {message}") from exc
    return evaluate(required, verdict, sha)


def read_pr_head(repo: str, number: int) -> tuple[str, str]:
    """`(head sha, mergeable_state)` for a PR. Read-only gh calls, one attempt each."""
    import subprocess

    cmd = ["gh", "api", f"repos/{repo}/pulls/{number}",
           "--jq", "{sha: .head.sha, mergeable_state: .mergeable_state, pr_state: .state}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Unreadable(f"could not run gh: {type(exc).__name__}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        if "rate limit" in detail.lower():
            # The brief's marker: say WHICH endpoint and which bucket, so a reader
            # waits on the right thing instead of re-diagnosing the instrument.
            raise Unreadable(
                f"GH-RATE-LIMITED(repos/{repo}/pulls/{number},{detail[:160]})")
        raise Unreadable(f"could not read PR #{number}: {detail[:200]}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise Unreadable(f"PR #{number} returned non-JSON: {proc.stdout[:200]!r}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
        raise Unreadable(f"PR #{number} payload has no head sha")
    return payload["sha"], str(payload.get("mergeable_state") or "unknown") + (
        "" if payload.get("pr_state") == "open" else f" (pr_state={payload.get('pr_state')})"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _print_report(number: int, mergeable: str, result: GateResult) -> None:
    required = result.required
    assert required is not None
    print(f"\nPR #{number}  head={result.sha}  mergeable_state={mergeable}")
    print(f"  required set ({required.source}): {len(required.names)} name(s) "
          f"— queue={sorted(required.queue)} merge={sorted(required.merge)}")
    if required.overlap:
        print(f"  ⛔ CONFIGURATION ERROR — in BOTH lists: {sorted(required.overlap)}")
    for note in required.notes:
        print(f"  note: {note}")
    width = max([len(r.name) for r in result.rows] + [4])
    for row in result.rows:
        mark = "✅" if row.state == PASS else "⛔"
        print(f"  {mark} {row.name:<{width}}  {row.detail}")
    print(f"  → {result.state}" + ("" if result.ok else " — " + "; ".join(result.reasons)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pr", type=int, action="append", default=[],
                    help="pull request number (repeatable)")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--live", action="store_true",
                    help="also read live branch protection (needs Administration: read)")
    ap.add_argument("--required-only", action="store_true",
                    help="print the derived required set and exit")
    ap.add_argument("--json", action="store_true",
                    help="emit ONE JSON document on stdout (annotations go to stderr)")
    ap.add_argument("--mergify", type=Path, default=None, help="path to .mergify.yml")
    ap.add_argument("--snapshot", type=Path, default=None,
                    help="path to the committed required-contexts snapshot")
    # `--json` must be one document on EVERY path — including a malformed argv,
    # which argparse answers with SystemExit(2) and an EMPTY stdout. The flag is
    # detected from the RAW argv because parsing is what just failed.
    json_mode = "--json" in (sys.argv[1:] if argv is None else argv)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exit_request:
        code = exit_request.code if isinstance(exit_request.code, int) else EXIT_UNREADABLE
        if code == EXIT_PASS:
            return EXIT_PASS          # `--help`: argparse already printed it
        if json_mode:
            print(json.dumps({
                "repo": None, "required": None, "state": GATE_UNREADABLE,
                "reasons": ["usage error: the arguments could not be parsed — nothing "
                            "was measured (see stderr)"],
                "reports": [],
            }, indent=2))
        return code

    def note(text: str) -> None:
        """Human prose — to STDERR under `--json`, so stdout stays parseable."""
        print(text, file=sys.stderr if args.json else sys.stdout)

    def error(text: str) -> None:
        print(f"::error::{text}", file=sys.stderr if args.json else sys.stdout)

    def emit(document: dict[str, Any], exit_code: int) -> int:
        """The ONLY stdout writer under `--json`: exactly one document, ever.

        EVERY measurement exit path routes through here — including the pre-loop
        refusals; an unparseable argv is answered separately (it never reaches
        here, and it emits its own document under `--json`). A
        `--json` consumer must never have to special-case "this failure printed
        nothing": an empty stdout is indistinguishable from a truncated run.
        """
        if args.json:
            print(json.dumps(document, indent=2))
        return exit_code

    try:
        required = derive_required_set(args.mergify, args.snapshot)
    except Unreadable as exc:
        error(f"FAIL(unreadable): {exc}")
        return emit({"repo": args.repo, "required": None, "state": GATE_UNREADABLE,
                     "reasons": [str(exc)], "reports": []}, EXIT_UNREADABLE)

    required_doc = _required_doc(required)

    # The REQUIRED SET is derived from THIS checkout's repo, so grading another
    # repo's PR against it would answer a question nobody asked (a false block, or
    # a coincidental PASS). Refused rather than answered.
    if args.repo != DEFAULT_REPO:
        reason = (
            f"--repo {args.repo} refused — the required set is derived from this "
            f"checkout, which describes {DEFAULT_REPO}; a verdict for another repo would "
            "be measured against the wrong required set"
        )
        error(f"{GATE_UNREADABLE}: {reason}")
        return emit({"repo": args.repo, "required": required_doc, "state": GATE_UNREADABLE,
                     "reasons": [reason], "reports": []}, EXIT_UNREADABLE)

    # `--live` is checked BEFORE any early return: a disagreement between the
    # enforced config and live protection makes the whole answer ambiguous, and a
    # `--required-only` run that reported "contract satisfied" without it would be
    # the exact silent pass this tool exists to remove.
    if args.live:
        try:
            live = read_live_contexts(args.repo)
        except Unreadable as exc:
            error(f"{GATE_UNREADABLE}: live protection: {exc}")
            return emit({"repo": args.repo, "required": required_doc,
                         "state": GATE_UNREADABLE, "reasons": [str(exc)],
                         "reports": []}, EXIT_UNREADABLE)
        if live != required.names:
            error(
                f"{GATE_CONFIG_ERROR}: live protection requires {sorted(live)} but the "
                f"repo derives {sorted(required.names)} — refusing to pass on an ambiguous set"
            )
            return emit({"repo": args.repo, "required": required_doc,
                         "state": GATE_CONFIG_ERROR,
                         "reasons": [f"live protection requires {sorted(live)}; the repo "
                                     f"derives {sorted(required.names)}"],
                         "reports": []}, EXIT_FAIL)
        note(f"  live protection agrees: {len(live)} context(s) required on main")
        if required.unsafe_drift:
            # LIVE is the authority on live protection: if it agrees with the
            # derived set, the record that disagreed is provably STALE, so the
            # drift refusal (which exists to avoid certifying against a SMALLER
            # set than the queue blocks on) no longer applies.
            note(f"  live protection clears the record drift: {sorted(required.unsafe_drift)} "
                 "is not required on main — the record was stale")
            # Drop the disagreement note the live read just DISPROVED: leaving it
            # would emit "these names would go UNGRADED" next to a PASS.
            surviving = tuple(
                text for text in required.notes
                if not text.startswith("⚠️ CONFIG/record disagreement")
            )
            required = replace(
                required, unsafe_drift=frozenset(),
                notes=(*surviving,
                       "live protection agrees with the config, so the record naming "
                       f"{sorted(required.unsafe_drift)} was stale — drift cleared"),
                source=f"{required.source} (drift cleared by --live)",
            )
            required_doc = _required_doc(required)

    if not args.pr and not args.required_only:
        # NOTHING was measured. Exit 0 here would be a false PASS on condition 2 —
        # the shape this whole tool exists to remove — so it is refused, and the
        # caller is told which flag asks which question.
        reason = (
            "nothing was measured: pass `--pr <N>` to grade condition 2, or "
            "`--required-only` to print the derived required set"
        )
        error(f"{GATE_UNREADABLE}: {reason}")
        return emit({"repo": args.repo, "required": required_doc, "state": GATE_UNREADABLE,
                     "reasons": [reason], "reports": []}, EXIT_UNREADABLE)

    if args.required_only or not args.pr:
        note(f"required set ({required.source}): {len(required.names)} name(s)")
        note(f"  queue           : {sorted(required.queue)}")
        note(f"  merge           : {sorted(required.merge)}")
        note(f"  injected        : {sorted(required.injected)}")
        note(f"  union (required): {sorted(required.names)}")
        if required.source.startswith("snapshot"):
            # The config was NOT read: "the contract" was never parsed, so claiming
            # it is satisfied would be a measurement that did not happen.
            note("  overlap         : not measured — the config could not be read; "
                 "this answer is the snapshot record")
        else:
            note(f"  overlap         : "
                 f"{sorted(required.overlap) or 'none — contract satisfied'}")
        for note_text in required.notes:
            note(f"  note: {note_text}")
        if required.unsafe_drift:
            error(f"{GATE_CONFIG_ERROR}: the record requires "
                  f"{sorted(required.unsafe_drift)}, which the config does not derive")
            return emit({"repo": args.repo, "required": required_doc,
                         "state": GATE_CONFIG_ERROR,
                         "reasons": [f"{sorted(required.unsafe_drift)} are required by the "
                                     "record but not derived from the config — they would go "
                                     "UNGRADED; fix the config, re-record protection, or "
                                     "pass --live to settle which side is current"],
                         "reports": []}, EXIT_FAIL)
        if required.overlap:
            error(f"{GATE_CONFIG_ERROR}: {sorted(required.overlap)} in BOTH lists")
            return emit({"repo": args.repo, "required": required_doc,
                         "state": GATE_CONFIG_ERROR,
                         "reasons": [f"{sorted(required.overlap)} appear in BOTH "
                                     "queue_conditions and merge_conditions"],
                         "reports": []}, EXIT_FAIL)
        if not args.pr:
            # A SET answer, not a gate verdict: `required-set-only`, never PASS.
            return emit({"repo": args.repo, "required": required_doc,
                         "state": STATE_REQUIRED_SET_ONLY,
                         "reasons": ["the required set was derived and printed; no head "
                                     "was read, so this is NOT a condition-2 verdict"],
                         "reports": []}, EXIT_PASS)

    worst = EXIT_PASS
    reports: list[dict[str, Any]] = []
    for number in args.pr:
        try:
            sha, mergeable = read_pr_head(args.repo, number)
            result = evaluate_head(required, args.repo, sha)
        except Unreadable as exc:
            error(f"{GATE_UNREADABLE}: PR #{number}: {exc}")
            # A failing PR must still APPEAR in the machine-readable record: a
            # report that silently omits it reads as "no problem with this PR".
            reports.append({"pr": number, "state": GATE_UNREADABLE,
                            "reasons": [str(exc)], "rows": [], "sha": None})
            # A MEASURED red outranks an unreadable sibling: `max()` would let 2
            # dominate 1, so a caller whose documented rule is "exit 2 → retry"
            # would retry forever over a PR that already answered FAIL.
            if worst != EXIT_FAIL:
                worst = EXIT_UNREADABLE
            continue
        if not args.json:
            _print_report(number, mergeable, result)
        reports.append({"pr": number, "mergeable_state": mergeable, **result.to_dict()})
        if result.state in (GATE_FAIL, GATE_CONFIG_ERROR):
            worst = EXIT_FAIL
        elif result.state == GATE_UNREADABLE and worst != EXIT_FAIL:
            worst = EXIT_UNREADABLE

    note("\nCONDITION 2 (required checks green at head) — the other conditions of the "
         "owner-readiness gate are NOT implemented here (FAIL(not-implemented) by design).")
    # A static {exit: state} map collapsed `FAIL(config-error)` into plain `FAIL`,
    # so a consumer could not tell "a required check is red here" (re-run it) from
    # "the required-SET is broken" (re-running never helps).
    states = {report.get("state") for report in reports}
    if GATE_FAIL in states:
        overall = GATE_FAIL
    elif GATE_CONFIG_ERROR in states:
        overall = GATE_CONFIG_ERROR
    else:
        overall = {EXIT_PASS: GATE_PASS, EXIT_FAIL: GATE_FAIL,
                   EXIT_UNREADABLE: GATE_UNREADABLE}[worst]
    reasons = [reason for report in reports for reason in report.get("reasons", [])]
    if not reasons:
        reasons = [f"all {len(required.names)} required check(s) are success"
                   if overall == GATE_PASS else overall]
    return emit({"repo": args.repo, "required": required_doc,
                 "reports": reports, "state": overall, "reasons": reasons}, worst)


if __name__ == "__main__":
    sys.exit(main())
