"""The REQUIRED status-check set, derived from the repo — never from the protection API.

WHY THIS MODULE EXISTS
    "Is this PR ready?" begins with "what is REQUIRED here?", and the canonical
    source of that set is the branch-protection API:

        gh api repos/<owner>/<repo>/branches/main/protection

    That read needs ADMIN permission. A **fine-grained PAT** without the
    *Administration* repository permission gets

        HTTP 403 {"message":"Resource not accessible by personal access token"}

    (measured 2026-11-04 on this box, `github_pat_11ATOIH...`), so the lane
    credential cannot answer the first question a readiness gate asks — and lanes
    then improvise differently (count all checks / count only the ones that
    reported / treat `cancelled` as a pass), which is why readiness verdicts have
    not agreed with each other or with the rail (#10962).

    ⚠️ The failure is **CREDENTIAL-dependent, not repo-wide**: a user OAuth token
    with `repo` scope reads the same endpoint fine on the same box. This repo has
    no rulesets (`/rulesets` and `/rules/branches/main` both return `[]`), so the
    config below is the COMPLETE source, not merely a proxy.

THE CONTRACT THIS IMPLEMENTS — `.mergify.yml`'s own header, clause 1
    A tracked file already states the invariant:

        live required contexts == queue_conditions union merge_conditions (checks),
        each required check named in EXACTLY ONE of the two lists.

    so the required set is a pure function of a tracked file. Clause 1 also fixes
    the SPLIT, which is load-bearing and is why this module returns two lists:

        `queue_conditions` gates ENTRY   (evaluated against the PR head)
        `merge_conditions` gates the MERGE (evaluated against the queue branch)

    A `merge_conditions` name is therefore **DEFERRED** at a PR head, not missing:
    Mergify evaluates it on `mergify/merge-queue/<sha>`. Measured 2026-11-04, all
    six sampled open PR heads carried ZERO `python-ci-gate` runs — so a consumer
    that treats absence as `FAIL(missing)` reports NOT-READY for every PR forever.

    `branch_protection_injection_mode` DECIDES what the entry gate IS (`.mergify.yml`
    clause 4: *"Read the mode before reasoning about the list"*), so the mode is
    READ here, never assumed. Under `merge` (set in this repo) the required
    contexts are injected for the MERGE only, so `queue_conditions` is the entry
    gate and a name in both lists is a config ERROR. Under `queue` (Mergify's
    default) they are injected for queuing AND merging, so `merge_conditions` must
    be a SUBSET of `queue_conditions` — the overlap is LEGAL, every context is an
    entry requirement and nothing is deferred.

THE COMMITTED SNAPSHOT IS A WITNESS, AND ITS ABSENCE IS A REFUSAL
    `docs/ci/required-contexts.json` is the committed record of a LIVE
    branch-protection read (`.mergify.yml` clause 2: *"The live required set is the
    source of truth … Re-read it when it changes; a stale list is a silent
    deadlock"*). It is a flat list with no entry/merge split, so it can REPORT the
    set and can never DECIDE a gate from it. What it must do is witness that the
    derived union EQUALS the live set:

      * a context it names and the config no longer derives ⟹ `RequiredSetConfigError`
        (the unsafe direction: a name the tool KNOWS is required and would never
        evaluate — exactly the #3016 regression, since deleting one
        `check-success=` line would otherwise be a silent PASS);
      * an absent or empty record ⟹ `RequiredSetUnreadable`: the union cannot be
        cross-checked, so condition 2 would be a verdict about an UNVERIFIED set.
        Deleting the file must never be the cheapest way to a READY.

    The protection API is NOT called here — calling it would reintroduce the 403
    this module exists to remove. A caller that CAN read it (a user OAuth token) is
    welcome to cross-check; a disagreement is a loud config error, never a silent
    substitution.

The parser is `tools/mergify_config_guard.py` (`_load_mergify`, `_success_names`,
`_effective_mode`, `KNOWN_INJECTION_MODES`) — the same CI-gated code the guard
uses for its own clause checks, not a second parser that could drift from it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mergify_config_guard import (
    KNOWN_INJECTION_MODES,
    MERGIFY_REL,
    RECORD_REL,
    GuardUnreadable,
    _effective_mode,
    _load_mergify,
    _success_names,
)


class RequiredSetUnreadable(Exception):
    """Neither the config nor the committed snapshot yields a usable required set."""


class RequiredSetConfigError(Exception):
    """The config names a check in both lists — contract clause 1 violated."""


# ── Required-set resolution (no credential) ─────────────────────────────────


@dataclass(frozen=True)
class RequiredSet:
    names: tuple[str, ...]
    source: str
    queue_names: tuple[str, ...] = ()
    merge_names: tuple[str, ...] = ()
    snapshot_agrees: bool | None = None
    notes: tuple[str, ...] = ()
    # False only for a snapshot-only fallback, which cannot be evaluated: the
    # flat `required_contexts` list carries no entry/merge split.
    entry_merge_split: bool = True
    # `branch_protection_injection_mode` — LOAD-BEARING, never assumed. Under
    # `merge` (set here) the required contexts are injected for the MERGE only, so
    # `queue_conditions` IS the entry gate and a name in both lists is a config
    # error. Under `queue` (Mergify's default) they are injected for QUEUING AND
    # MERGING, so `merge_conditions` must be a SUBSET of `queue_conditions` — the
    # overlap is LEGAL and every context is an entry requirement, with nothing
    # deferred. A gate that hard-codes one mode disagrees with the guard about
    # what a legal config is; `.mergify.yml` itself says "Read the mode before
    # reasoning about the list".
    # None on the snapshot-only path: a datum must not assert a config read that
    # never happened, in a module whose own contract says the mode is never assumed.
    mode: str | None = None

    @property
    def overlap(self) -> tuple[str, ...]:
        return tuple(n for n in self.queue_names if n in set(self.merge_names))


def _snapshot_names(root: Path) -> tuple[str, ...] | None:
    """`required_contexts` from the committed record, or None if unreadable.

    Read with `json`, not the guard's YAML loader: this file is JSON, so a non-JSON
    surprise here must degrade to "no snapshot", never to an exception raised from
    the READ — a parse error that escapes would mask the primary path. The CALLER
    then decides what an unusable record MEANS, and for a readable config it is a
    refusal (`derive_from_config`), because the record is the only witness that the
    derived set equals the live required set.
    """
    path = root / RECORD_REL
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = doc.get("required_contexts") if isinstance(doc, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        return None
    return tuple(names)


def derive_from_config(root: Path) -> RequiredSet:
    """The required set as `.mergify.yml` declares it — the PRIMARY source.

    Derives the union across EVERY queue rule (not just the first): a repository
    with two rules would otherwise silently drop the second rule's checks.
    """
    doc = _load_mergify(root)  # raises GuardUnreadable
    rules = doc["queue_rules"]
    queue_names: list[str] = []
    merge_names: list[str] = []
    modes: set[str] = set()
    for rule in rules:
        queue_names.extend(_success_names(rule.get("queue_conditions") or []))
        merge_names.extend(_success_names(rule.get("merge_conditions") or []))
        rule_mode = _effective_mode(rule)
        # Type-check BEFORE hashing: a container-typed mode is unhashable, so
        # `modes.add(...)` would raise TypeError and escape every handler as a
        # traceback — and the process then exits 1, which reads to a caller as
        # "a required check is red" instead of "I could not read the config".
        if not isinstance(rule_mode, str):
            raise RequiredSetUnreadable(
                f"{MERGIFY_REL}: queue rule {rule.get('name')!r} "
                f"branch_protection_injection_mode must be a string, "
                f"got {type(rule_mode).__name__}"
            )
        modes.add(rule_mode)

    # The injection mode decides what the entry gate IS, so it is READ, never
    # assumed. Two rules in one file with different modes cannot be reasoned
    # about as a single required set — refuse rather than pick one.
    if len(modes) != 1:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: queue rules disagree on branch_protection_injection_mode "
            f"({sorted(modes)}); the entry gate is mode-dependent and cannot be mixed"
        )
    mode = modes.pop()
    if mode not in KNOWN_INJECTION_MODES:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: unrecognised branch_protection_injection_mode {mode!r} "
            f"(known: {list(KNOWN_INJECTION_MODES)})"
        )

    # De-duplicate a name repeated in the SAME list (harmless) but NEVER a name
    # present in both lists (a config error, raised below).
    def _uniq(seq: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for n in seq:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return tuple(out)

    q, m = _uniq(queue_names), _uniq(merge_names)
    overlap = tuple(n for n in q if n in set(m))
    # Only under `merge` is an overlap a CONFIGURATION ERROR (the contract's "named
    # in EXACTLY ONE list"). Under `queue` the guard REQUIRES
    # merge_conditions subset-of queue_conditions, so an overlap is the legal shape.
    if mode == "merge" and overlap:
        raise RequiredSetConfigError(
            "check(s) named in BOTH queue_conditions and merge_conditions: "
            + ", ".join(overlap)
            + " — under branch_protection_injection_mode=merge the configuration "
            "contract (clause 1) requires EXACTLY ONE list; a name in both is a "
            "configuration error, not a de-duplication problem"
        )

    union = tuple(dict.fromkeys(q + m))
    if not union:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: no positive `check-success=` condition in any queue rule"
        )

    snapshot = _snapshot_names(root)
    notes: list[str] = []
    agrees: bool | None = None
    if not snapshot:
        # `None` = absent/unreadable record, `()` = an EMPTY `required_contexts`.
        # Both are "no witness", and with the refusal below load-bearing the record
        # is the ONLY thing that can detect a config that dropped a required line —
        # so an unusable record stops being a nuance and becomes a BYPASS: delete
        # the file (or empty the list), delete one `check-success=` line, and the
        # gate reports PASS / exit 0 with a red required context in the tree.
        # Measured at `fcf9b1f6e`: both states exited 0 on `docs=completed/failure`,
        # while `tools/mergify_config_guard._clause_iv` raises "merge injection is
        # active but the gate record is absent" on the very same state. The config
        # itself is not defective here, so this is UNREADABLE ("cannot
        # cross-check"), not FAIL(config): exit 2, never a verdict built on an
        # unverified set.
        raise RequiredSetUnreadable(
            f"{RECORD_REL}: no usable committed snapshot "
            f"({'absent or unreadable' if snapshot is None else '`required_contexts` is EMPTY'}) "
            f"— {MERGIFY_REL} is readable, but its union cannot be checked against "
            "the record of the live branch-protection read (contract clause 1), so "
            "condition 2 would be a PASS/FAIL about an UNVERIFIED set. Restore the "
            "record (re-cut it) rather than deleting it: its absence is otherwise "
            "the cheapest way to make this gate report READY over a red required "
            "check"
        )
    else:
        agrees = set(snapshot) == set(union)
        # THE UNSAFE DIRECTION IS A CONFIG ERROR, NOT A NOTE. `snapshot` is the
        # committed record of a LIVE branch-protection read, so a context it names
        # and the config does not derive is a name the tool KNOWS is required and
        # then never evaluates: condition 2 would report PASS over a set it can
        # already see is not the required set (clause 1 is an EQUALITY). The note
        # alone let `--pr` exit 0 while that context was `failure` — measured at
        # 0995b1137 by deleting one `check-success=docs` line, which is exactly the
        # #3016 regression this module exists to catch.
        snapshot_only = tuple(n for n in dict.fromkeys(snapshot) if n not in set(union))
        if snapshot_only:
            raise RequiredSetConfigError(
                f"{RECORD_REL} records {', '.join(snapshot_only)} as a live-required "
                f"context, but {MERGIFY_REL} no longer derives it — the derived set "
                f"({', '.join(union)}) is therefore NOT the required set (contract "
                "clause 1), and evaluating condition 2 against it would report "
                "READY while a required check is red. Re-add the `check-success=` "
                "line, or, if the context was DELIBERATELY removed from branch "
                "protection, re-cut the record so the two agree"
            )
        if not agrees:
            notes.append(
                f"{RECORD_REL} disagrees with {MERGIFY_REL}: snapshot-only="
                f"{sorted(set(snapshot) - set(union))} config-only="
                f"{sorted(set(union) - set(snapshot))}"
            )
    return RequiredSet(
        names=union,
        source=f"{MERGIFY_REL} (queue_conditions union merge_conditions)",
        queue_names=q,
        merge_names=m,
        snapshot_agrees=agrees,
        notes=tuple(notes),
        mode=mode,
    )


def resolve_required_set(root: Path) -> RequiredSet:
    """Config first, committed snapshot second, unreadable third — never an empty pass."""
    try:
        return derive_from_config(root)
    except RequiredSetConfigError:
        raise  # a config error is a verdict, not a reason to fall back
    except (GuardUnreadable, RequiredSetUnreadable) as primary:
        snapshot = _snapshot_names(root)
        if snapshot:
            return RequiredSet(
                names=snapshot,
                source=f"{RECORD_REL} (committed snapshot fallback)",
                notes=(f"{MERGIFY_REL} unreadable ({primary}); using the snapshot",),
                entry_merge_split=False,
            )
        raise RequiredSetUnreadable(
            f"required set unreadable from BOTH sources — {MERGIFY_REL}: {primary}; "
            f"{RECORD_REL}: absent or carrying no `required_contexts`"
        ) from None
