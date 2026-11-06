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

    A `merge_conditions` name is therefore **DEFERRED** at a PR head, not missing —
    *but only for a consumer that can ATTRIBUTE the absence*: one that knows which
    workflow emits the context (``emit_files``) AND has read that head's run listing.
    Mergify evaluates the context on `mergify/merge-queue/<sha>`, so a consumer that
    treats absence as `FAIL(missing)` reports NOT-READY for every PR forever, while one
    that defers on bare absence grades a PENDING aggregate PASS (see the paragraph below).
    With no attribution the answer is `FAIL(unattributable)`, never `DEFERRED`.

    ⛔ BUT "DEFERRED" IS NOT "ABSENT", AND THE FIRST MEASUREMENT SAID THE WRONG THING.
    This docstring used to conclude, from "all six sampled open PR heads carried ZERO
    `python-ci-gate` runs" (2026-11-04), that the aggregate NEVER reports at an author
    head. That sample was taken while the matrix was still running, and it cannot tell
    "never runs at a head" from "not scheduled/created YET". Re-measured 2026-11-05,
    `python-ci-gate` DOES report on author PR heads — as `queued` (PR 11097) and
    `completed` with BOTH `success` (PR 11100) and `failure` (PR 11096). An Actions job
    with unmet `needs` (and this one has `if: always()` + a 13-way `needs:`) has NO
    check-run at all, so absence is also exactly what a PENDING aggregate looks like.

    The two states need different verdicts — a pending aggregate is IN FLIGHT (the gate's
    own rule is "in flight is a FAIL here, not a wait"), while a workflow that never ran
    on this head really is deferred — and only the EMITTING WORKFLOW separates them. That
    is why `RequiredSet.emit_files` exists: it carries the record's `emitters`
    projection so a consumer can ask "did a PR-triggered run of that workflow happen on
    this head?". A consumer with no attribution must NOT read `DEFERRED` — that is a PASS
    arm over evidence nobody read.

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

    ⛔ AND THE RECORD'S OWN LIVE VERDICT IS PART OF THE WITNESS. The record states TWO
    facts: `required_contexts` (the PAYLOAD a live read observed) and `live_result` /
    `live_observed_contexts` (the RESULT — whether that read agreed with the config at
    all). `mergify_config_guard._record_live_attempt` re-attests `required_contexts`
    ONLY on a `SATISFIED` read, so a `DIVERGED` or `UNAVAILABLE` record keeps a STALE
    payload while saying, in the same file, that live protection enforces something
    else. Checking only `set(derived) == set(required_contexts)` therefore prints
    `snapshot_agrees: True` over a set the record itself calls unverified. So a
    non-clean verdict is a REFUSAL (`_live_attestation`), absence is tolerated (a
    record predating the live read still carries the payload), and the attestation's
    AGE is disclosed in `RequiredSet.source` rather than enforced — a gate that can
    only pass under an admin token is the gate this module exists to replace.

    The protection API is NOT called here — calling it would reintroduce the 403
    this module exists to remove. A caller that CAN read it (a user OAuth token) is
    welcome to cross-check; a disagreement is a loud config error, never a silent
    substitution.

READ FROM THE PROTECTED REF, NEVER FROM THE TREE BEING JUDGED
    Both inputs are tracked files, so a PR can edit `.mergify.yml` and
    `docs/ci/required-contexts.json` in the SAME diff — and the two then agree with
    each other, letting the witness certify the shrunken set it was just changed to.
    That is why consumers derive through `derive_from_ref` (`origin/main` by default):
    the required set that governs a merge is the one on the protected ref, not the
    one the candidate branch would prefer. `derive_from_config` exists for that ref
    path (and for a deliberate `--ref HEAD` diagnostic), and reads the same parser
    either way.

The parser is `tools/mergify_config_guard.py` (`_load_mergify`, `_success_names`,
`_effective_mode`, `KNOWN_INJECTION_MODES`) — the same CI-gated code the guard
uses for its own clause checks, not a second parser that could drift from it.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape), before any later guard could run.
# The refusal names the invocation that actually works. This module is a LIBRARY (no
# `__main__` block), but it is addressed by PATH — `tools` has an `__init__.py`, yet
# `_expected_invocation` only treats a file three or more segments deep as a package
# module, so the working form here is `uv run python tools/required_set.py` and NOT
# `-m tools.required_set` (which the guard's own `test_expected_invocation_matches_...`
# pins). Naming the wrong form would replace an unattributed failure with a refusal
# that sends the operator to an invocation that does not resolve.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/required_set.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/required_set.py`"
    )

import dataclasses
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mergify_config_guard import (
    KNOWN_INJECTION_MODES,
    LIVE_FRESH_DAYS,
    MERGIFY_REL,
    RECORD_REL,
    GuardUnreadable,
    _effective_mode,
    _load_mergify,
    _now,
    _parse_iso,
    _success_names,
)


class RequiredSetUnreadable(Exception):
    """Neither the config nor the committed snapshot yields a usable required set."""


class RequiredSetConfigError(Exception):
    """The repo's own recorded evidence says the derived set is NOT the required set.

    FOUR shapes, and a handler must not assume any one of them from this class:

    * (a) a check named in BOTH lists under ``mode=merge`` (clause 1);
    * (b) a context the committed record names and the config no longer derives, or
      (c) one the config derives and the record does not confirm — the two EQUALITY
      violations of clause 1, with no overlap involved at all;
    * (d) the record's OWN live verdict — `live_result` / `live_observed_contexts` — is
      not clean (`DIVERGED`, `UNAVAILABLE`, or a self-contradictory pair), so its
      `required_contexts` is a stale payload rather than a witness (clause 2).

    Why (d) belongs to the same family: in every shape the CONFIG may be perfectly
    well-formed and the tree may build — what is broken is the attestation that ties it
    to live branch protection, and the only remedy is a `--live` read under a credential
    that can make one. What must never happen is that this class is caught and treated as
    a retryable read failure: all four shapes are refusals, never a PASS.
    """


# ── Required-set resolution (no credential) ─────────────────────────────────


@dataclass(frozen=True)
class RequiredSet:
    names: tuple[str, ...]
    source: str
    queue_names: tuple[str, ...] = ()
    merge_names: tuple[str, ...] = ()
    #: ``effective check-run name -> the workflow FILES whose jobs emit it``, taken from
    #: the committed record's ``emitters`` projection (it is written by
    #: `mergify_config_guard --recut`, so it is always present in a re-cut record).
    #: Stored as a tuple of pairs, not a dict, so this frozen dataclass stays HASHABLE —
    #: a dict field would raise `TypeError` from the generated `__hash__`.
    #:
    #: ⛔ WHY IT IS HERE AT ALL: a consumer that reads a merge-time context as ABSENT must
    #: answer "is the aggregate not created YET, or does its workflow never run at a
    #: head?" — and only the emitting workflow can separate those (an Actions job with
    #: unmet `needs` has no check-run at all, so absence is what a PENDING aggregate looks
    #: like). Without this map the only reachable answer is "DEFERRED", which is a PASS
    #: arm over evidence nobody read.
    emit_files: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: The WORKFLOW FILES that exist in the tree this set was read from
    #: (``.github/workflows/*.yml`` basenames), or ``None`` when that listing could not be
    #: read. ⛔ THE PROJECTION ABOVE IS ONLY AS GOOD AS THIS LIST: `emitters` is written by
    #: `mergify_config_guard --recut` and nothing re-derives it between recuts, so a workflow
    #: RENAME leaves the record naming a file that no longer exists — and a consumer that
    #: compares a renamed-away spelling against a head's runs matches nothing and reads
    #: "the emitter did not run here", i.e. DEFERRED (a PASS). With this list the consumer
    #: can tell "exists and did not run" from "does not exist any more" (see
    #: `ready_for_owner._merge_time_absence`, #11175).
    #:
    #: ``()` is a MEASUREMENT (the tree was read and carries no workflow files); ``None`` is
    #: "not read" and must fail closed at every consumer, never be read as the empty answer.
    workflow_files: tuple[str, ...] | None = None
    snapshot_agrees: bool | None = None
    # `branch_protection_injection_mode` — LOAD-BEARING, never assumed. Under
    # `merge` (set here) the required contexts are injected for the MERGE only, so
    # `queue_conditions` IS the entry gate and a name in both lists is a config
    # error. Under `queue` (Mergify's default) they are injected for QUEUING AND
    # MERGING, so `merge_conditions` must be a SUBSET of `queue_conditions` — the
    # overlap is LEGAL and every context is an entry requirement, with nothing
    # deferred. A gate that hard-codes one mode disagrees with the guard about
    # what a legal config is; `.mergify.yml` itself says "Read the mode before
    # reasoning about the list".
    mode: str = "merge"

    @property
    def overlap(self) -> tuple[str, ...]:
        return tuple(n for n in self.queue_names if n in set(self.merge_names))

    def emit_workflows(self, name: str) -> tuple[str, ...]:
        """The workflow FILES that emit check-run ``name``, or ``()`` if unattributed.

        ``()`` is a real answer and must be treated as one: it means the committed
        record carries no emitter for a name the config REQUIRES, so "did that workflow
        run at this head?" cannot be asked at all.
        """
        for recorded, files in self.emit_files:
            if recorded == name:
                return files
        return ()


#: Where GitHub reads workflow files from — the only directory whose `*.yml` can emit a
#: check-run, and therefore the only tree `emitters` can legitimately name.
WORKFLOWS_REL = ".github/workflows"


def _workflow_files(root: Path) -> tuple[str, ...] | None:
    """The workflow FILE basenames under ``root/.github/workflows`` — ``None`` if unreadable.

    ⛔ ``()` AND ``None`` ARE DIFFERENT ANSWERS, and conflating them is the fail-open this
    exists to close: a tree that genuinely carries no workflow files means no check-run can
    ever be emitted there (so a recorded emitter is stale), while an unreadable directory is
    a read failure that must not be graded as a measurement of emptiness. Only entries
    GitHub would actually load are counted — files directly in the directory with a YAML
    suffix; subdirectories are not workflows.
    """
    directory = root / WORKFLOWS_REL
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return ()
    except OSError:
        return None
    names: list[str] = []
    try:
        for entry in entries:
            if entry.is_file() and entry.suffix in (".yml", ".yaml"):
                names.append(entry.name)
    except OSError:
        return None
    return tuple(sorted(names))


def _read_record(root: Path) -> dict[str, Any] | None:
    """The committed record, parsed ONCE, or ``None`` if absent/unreadable/non-object.

    ⛔ ONE READ, NOT ONE PER FIELD. `required_contexts`, `emitters` and the live verdict are
    three facts of a SINGLE attestation; reading the file three times lets a concurrent
    writer pair the PAYLOAD of one record with the VERDICT of another — and the verdict is
    now load-bearing (`_live_attestation`), so a torn read would be a real fail-open rather
    than a cosmetic inconsistency.

    Read with `json`, not the guard's YAML loader: this file is JSON, so a non-JSON surprise
    here must degrade to "no record", never to an exception raised from the READ — a parse
    error that escaped would mask the primary path. The CALLER then decides what an unusable
    record MEANS, and for a readable config it is a refusal (`derive_from_config`), because
    the record is the only witness that the derived set equals the live required set.
    """
    path = root / RECORD_REL
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _snapshot_names(doc: dict[str, Any] | None) -> tuple[str, ...] | None:
    """`required_contexts` from the committed record, or None if unreadable/absent.

    ``None`` covers both "no record" and "an empty or malformed `required_contexts`"; the
    caller refuses either way, and distinguishes them only in the message, because in both
    states the union has no witness.
    """
    names = doc.get("required_contexts") if isinstance(doc, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        return None
    return tuple(names)


def _snapshot_emitters(doc: dict[str, Any] | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``emitters`` from the committed record, projected to ``name -> workflow FILES``.

    The record stores ``[[workflow_file, job_key], …]`` per effective check-run name
    (`mergify_config_guard._emitter_map`); a consumer only needs the FILES, because that
    is what an Actions run's ``path`` can be matched against.

    A malformed or absent projection degrades to ``()`` rather than raising: this map is
    not the witness the required set rests on (``required_contexts`` is), so it must not
    be able to turn a readable record into a refusal — but the CONSUMER must treat the
    empty answer as "cannot attribute", never as "nothing can emit it".
    """
    emitters = doc.get("emitters") if isinstance(doc, dict) else None
    if not isinstance(emitters, dict):
        return ()
    out: list[tuple[str, tuple[str, ...]]] = []
    for name, entries in sorted(emitters.items()):
        if not isinstance(name, str) or not name or not isinstance(entries, list):
            continue
        files: list[str] = []
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or not entry:
                continue
            workflow = entry[0]
            # Stored VERBATIM. The basename reduction happens once, at the COMPARISON
            # (`ready_for_owner._run_emits`), because that is the line that decides PASS vs
            # FAIL and it must be spelling-insensitive for EVERY door that can supply this
            # map — the record (here) and the `--fixture` door. ⛔ An earlier version of this
            # comment claimed "Basename only" while the code did no such thing, and the
            # consequence was a fail-open: a record naming `.github/workflows/python-ci.yml`
            # matched no run at all, so the absence arm answered DEFERRED (PASS) on a head
            # where the emitting workflow had really run.
            if isinstance(workflow, str) and workflow and workflow not in files:
                files.append(workflow)
        if files:
            out.append((name, tuple(files)))
    return tuple(out)


def _live_attestation(doc: dict[str, Any] | None, names: tuple[str, ...] | None) -> str:
    """Validate the record's OWN live verdict; return the disclosure that goes in `source`.

    ⛔ THE RECORD STATES TWO FACTS AND THIS MODULE USED TO READ ONLY ONE. `required_contexts`
    is the PAYLOAD — the set a live branch-protection read observed. `live_result` and
    `live_observed_contexts` are the RESULT — whether that read agreed with the config at
    all. `mergify_config_guard._record_live_attempt` rewrites `required_contexts` ONLY when
    the read was `SATISFIED`, so a `DIVERGED` or `UNAVAILABLE` record keeps a STALE payload
    while stating, in the same file, that live protection enforces something else. Measuring
    only `set(derived) == set(required_contexts)` therefore reports `snapshot_agrees: True`
    — and READY — over a set the record itself calls unverified. That is the "silent
    deadlock" `.mergify.yml` clause 2 exists to prevent, and it is the same inference error
    as "a measurement artifact is not a state": the payload was TRUE when it was written,
    and the verdict in the same file says it no longer holds.

    Why a REFUSAL and not a note. Two independent statements of the rule, plus this repo's
    own:

      * SLSA's Verification Summary Attestation requires a consumer to confirm the recorded
        `verificationResult` is `PASSED`, "not merely inspect the attested payload";
      * in-toto's consumer guidance is that ignoring an attestation must never turn a deny
        into an allow (the monotonic-consumer rule);
      * and `mergify_config_guard._clause_viii_a` ALREADY reds `--static` on exactly this
        field (`live_result not in ("", "SATISFIED")`). A note here would make this
        instrument and the repo's own CI guard disagree about whether the tree is sound.

    ⛔ WHY ABSENCE IS TOLERATED AND AGE IS ONLY DISCLOSED. A record that predates the I1
    read carries no `live_result` at all; refusing there would demand an ADMIN credential
    to produce any verdict, which is the 403 this module exists to remove — so absent is
    treated as "no verdict recorded" and said so. A STALE attestation is information for
    the reader, never a refusal, for the same reason: enforcing freshness would put the
    credential back in the gate's critical path.
    """
    if not isinstance(doc, dict):
        return "no recorded live verdict (no readable record)"
    # ⛔ ABSENT IS NOT NULL, the same distinction `Probe.labels` / `required_deferred` make
    # in `ready_for_owner`. `doc.get("live_result")` cannot tell them apart, and conflating
    # them would let an explicit `null` — the shape a half-written record produces — read as
    # the tolerated pre-I1 absence. MUTATION: replace this with `live = doc.get(...)` and
    # `test_a_non_string_live_verdict_is_unreadable` REDs by not raising at all.
    if "live_result" in doc:
        live = doc["live_result"]
        if not isinstance(live, str):
            raise RequiredSetUnreadable(
                f"{RECORD_REL}: `live_result` must be a string, got {type(live).__name__} "
                "— the record's own live verdict cannot be read, so its "
                "`required_contexts` cannot be treated as verified. (An ABSENT field is "
                "tolerated: that is a record predating the live read. A "
                "present-but-unreadable one is not.)"
            )
    else:
        live = None
    observed_raw = doc.get("live_observed_contexts")
    observed: tuple[str, ...] | None = None
    if "live_observed_contexts" in doc:
        if not isinstance(observed_raw, list) or not all(
            isinstance(n, str) and n for n in observed_raw
        ):
            raise RequiredSetUnreadable(
                f"{RECORD_REL}: `live_observed_contexts` must be a list of non-empty "
                f"strings, got {type(observed_raw).__name__} — the record's live "
                "observation cannot be read, so its payload cannot be cross-checked"
            )
        observed = tuple(observed_raw)
    if live is not None and live not in ("", "SATISFIED"):
        when = doc.get("live_checked_at")
        when_note = f" at {when}" if isinstance(when, str) and when else ""
        raise RequiredSetConfigError(
            f"{RECORD_REL} records the last live branch-protection read as {live}{when_note}, "
            "and its `required_contexts` is re-attested ONLY on a SATISFIED read — so the "
            f"payload this module would otherwise cross-check is STALE, not a witness that "
            f"{MERGIFY_REL} equals the live required set (contract clause 2). Re-attest with "
            "`tools/mergify_config_guard.py --live` under a credential that can read branch "
            "protection. `--recut` does NOT fix this — it rewrites only `gate_digest`, "
            "`emitters` and `verified_at`"
        )
    if observed is not None and names is not None and set(observed) != set(names):
        observed_only = tuple(n for n in dict.fromkeys(observed) if n not in set(names))
        names_only = tuple(n for n in dict.fromkeys(names) if n not in set(observed))
        raise RequiredSetConfigError(
            f"{RECORD_REL} contradicts ITSELF: `live_observed_contexts` "
            f"({', '.join(sorted(set(observed)))}) is not the set its own "
            f"`required_contexts` names ({', '.join(sorted(set(names)))}) — so neither field "
            "is a witness that the derived set equals the live required set (contract "
            "clause 2). Only a SATISFIED `--live` read writes them together; re-attest "
            "rather than hand-editing either list"
            + (f" [live-only: {', '.join(observed_only)}]" if observed_only else "")
            + (f" [required_contexts-only: {', '.join(names_only)}]" if names_only else "")
        )
    if live is None:
        return "no recorded live verdict (record predates the live read)"
    label = live or "UNRECORDED"
    stamp = doc.get("live_verified_at")
    if not isinstance(stamp, str) or not stamp:
        return f"live read {label} (no live_verified_at)"
    try:
        age = (_now() - _parse_iso(stamp, f"{RECORD_REL}.live_verified_at")).days
    except GuardUnreadable:
        # Disclose, never refuse: an unreadable timestamp is not a reason to demand an
        # admin token, but it must not be silently omitted either.
        return f"live read {label} at {stamp} (age unreadable)"
    return (
        f"live read {label} at {stamp} "
        f"({age}d old, window {LIVE_FRESH_DAYS}d — disclosed, never enforced)"
    )


def derive_from_config(root: Path, *, workflow_files: tuple[str, ...] | None = None) -> RequiredSet:
    """The required set as `.mergify.yml` declares it — the PRIMARY source.

    Derives the union across EVERY queue rule (not just the first): a repository
    with two rules would otherwise silently drop the second rule's checks.

    ``workflow_files`` overrides the listing of ``root/.github/workflows`` — used by
    `derive_from_ref`, whose temp root holds only the two materialised files and must
    therefore be told what workflows exist at the ref it read. ``None`` means "list *root*".
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

    # De-duplicate a name repeated in the SAME list (harmless). A name present in BOTH
    # lists is mode-dependent, which is why it is not decided here: under `merge` it is a
    # config error (raised below); under `queue` the overlap is the legal shape and is
    # folded into entry, so nothing is raised.
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
    # `merge_conditions` subset-of `queue_conditions`, so an overlap is the legal shape.
    if mode == "merge" and overlap:
        raise RequiredSetConfigError(
            "check(s) named in BOTH queue_conditions and merge_conditions: "
            + ", ".join(overlap)
            + " — under branch_protection_injection_mode=merge the configuration "
            "contract (clause 1) requires EXACTLY ONE list; a name in both is a "
            "configuration error, not a de-duplication problem"
        )
    if mode == "queue":
        # ⛔ MODE `queue` INJECTS EVERY REQUIRED CONTEXT FOR QUEUING *AND* MERGING
        # (`.mergify.yml` clause 4), so there is NO merge-time bucket there: every
        # name gates ENTRY and is evaluated against the PR head. Folding them here,
        # in the DERIVATION, keeps every consumer safe — a consumer that merely
        # bucketed `merge_names` as deferred would tell a lane READY on a head the
        # queue itself would refuse at entry, which is the false PASS this module
        # exists to prevent. The mode is read above, never assumed; this is where it
        # changes the ANSWER and not only the overlap rule.
        q, m = _uniq(list(q) + list(m)), ()

    union = tuple(dict.fromkeys(q + m))
    if not union:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: no positive `check-success=` condition in any queue rule"
        )

    record = _read_record(root)
    snapshot = _snapshot_names(record)
    # ⛔ BEFORE the equality block, not after: a record whose OWN verdict is `DIVERGED`
    # usually still matches the config (that is why the payload is stale), so the equality
    # check would pass and the divergence would never be looked at. Refusing here also
    # keeps the diagnosis the actionable one — "the live read diverged, re-attest" —
    # instead of "the record lacks a name the config derives".
    live_note = _live_attestation(record, snapshot)
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
            "condition 2 would be a PASS/FAIL about an UNVERIFIED set. RESTORE the "
            "record's `required_contexts` (from branch protection, re-attested with "
            "`tools/mergify_config_guard.py --live` under a credential that can read it) "
            "rather than deleting it: its absence is otherwise the cheapest way to make "
            "this gate report READY over a red required check"
        )
    else:
        # ⛔ BOTH DIVERGENCE DIRECTIONS ARE A CONFIG ERROR, NOT A NOTE. Clause 1 is an
        # EQUALITY, and the CI guard (`mergify_config_guard._clause_iv`) already treats
        # both as `EXIT_DIVERGED` (`config_checks != required_set`). A note in either
        # direction would make this instrument quietly tolerate a divergence the repo's
        # own guard calls an error, and the two would disagree about whether the tree is
        # sound.
        #
        # The SNAPSHOT-ONLY direction is the unsafe one: `snapshot` is the committed
        # record of a LIVE branch-protection read, so a context it names and the config
        # does not derive is a name the tool KNOWS is required and then never evaluates.
        # The note alone let `--pr` exit 0 while that context was `failure` — measured at
        # 0995b1137 by deleting one `check-success=docs` line, exactly the #3016
        # regression this module exists to catch.
        #
        # The CONFIG-ONLY direction is the fail-closed one for a gate (it evaluates more
        # names than the record knows, so it can only over-refuse), which is why refusing
        # costs nothing real: the reachable window is between a config edit landing and
        # branch protection catching up, and CI is red in that window anyway.
        # ⛔ THE REMEDY NAMED BELOW IS CHECKED, NOT ASSUMED: `mergify_config_guard.py
        # --recut` writes ONLY `gate_digest`, `emitters` and `verified_at` (see its
        # `run_recut`) — it never touches `required_contexts`, so naming it here would
        # send an operator round a loop while every readiness verdict stays refused.
        union_set, snapshot_set = set(union), set(snapshot)
        snapshot_only = tuple(n for n in dict.fromkeys(snapshot) if n not in union_set)
        if snapshot_only:
            raise RequiredSetConfigError(
                f"{RECORD_REL} records {', '.join(snapshot_only)} as a live-required "
                f"context, but {MERGIFY_REL} no longer derives it — the derived set "
                f"({', '.join(union)}) is therefore NOT the required set (contract "
                "clause 1), and evaluating condition 2 against it would report "
                "READY while a required check is red. Re-add the `check-success=` "
                "line, or, if the context was DELIBERATELY removed from branch "
                "protection, re-attest the record from the live read "
                "(`tools/mergify_config_guard.py --live`) so the two agree"
            )
        config_only = tuple(n for n in union if n not in snapshot_set)
        if config_only:
            raise RequiredSetConfigError(
                f"{MERGIFY_REL} derives {', '.join(config_only)} but {RECORD_REL} does "
                "not record it as live-required — the two sources disagree about "
                "clause 1's EQUALITY, so condition 2 would be a verdict about a set "
                "the repo does not agree is required. TWO remedies, pick by INTENT: "
                "(a) if the name is a typo or a rename, correct the `check-success=` "
                f"spelling in {MERGIFY_REL} — do NOT make it required on the branch, "
                "because a name no workflow emits deadlocks the queue for every PR "
                "(clause 1); (b) if the context really should be required, make it "
                "required on the branch and re-attest with "
                "`tools/mergify_config_guard.py --live` under a credential that can "
                "read protection. `--recut` does NOT fix this — it rewrites only "
                "`gate_digest`, `emitters` and `verified_at`"
            )
        agrees = True
    return RequiredSet(
        names=union,
        # `source` names what was READ, including the record's own live verdict and the
        # AGE of that attestation — so `snapshot_agrees: True` can never again be read as
        # "the required set was verified" when the verification it rests on is a
        # 38-day-old read. Disclosure, not enforcement: see `_live_attestation`.
        source=f"{MERGIFY_REL} (queue_conditions union merge_conditions); {live_note}",
        queue_names=q,
        merge_names=m,
        emit_files=_snapshot_emitters(record),
        # The tree the CONFIG was read from, so the projection is validated against the same
        # bytes that produced the set. `derive_from_ref` PASSES the protected ref's listing
        # instead: its temp root carries only the two files it materialises, so listing there
        # would answer "no workflows exist" for every repo. `None` (the default) means "list
        # root"; an explicit `()` is a MEASUREMENT and is kept as one.
        workflow_files=(_workflow_files(root) if workflow_files is None else workflow_files),
        snapshot_agrees=agrees,
        mode=mode,
    )


def _workflow_files_at(cwd: Path, sha: str, ref: str, timeout: int) -> tuple[str, ...]:
    """The workflow FILE basenames in ``sha``'s tree — read from the COMMIT, or refuse.

    ⛔ PINNED TO ``sha``, NOT ``ref``: the same reason the two blob reads are (`refs/remotes/
    origin/main` is shared with every lane in this fleet and a fetch can land mid-read) —
    the listing must describe the SAME tree whose `.mergify.yml` produced the set.

    A failure here is a REFUSAL, not ``()``: "I could not list the ref's workflows" must not
    be read as "that ref has no workflows", which would mark every recorded emitter stale
    and turn a transient git failure into a fleet-wide NOT-READY.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", sha, "--", WORKFLOWS_REL],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RequiredSetUnreadable(
            f"listing {WORKFLOWS_REL} at {ref} ({sha[:12]}): {exc}"
        ) from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RequiredSetUnreadable(
            f"listing {WORKFLOWS_REL} at {ref} ({sha[:12]}) failed: "
            f"{detail[0] if detail else f'exit {proc.returncode}'} — the emitter projection "
            "is validated against the protected ref's own workflow files, so this is a "
            "refusal rather than an empty listing"
        )
    prefix = f"{WORKFLOWS_REL}/"
    names = [
        line[len(prefix) :]
        for line in proc.stdout.splitlines()
        if line.startswith(prefix)
        and "/" not in line[len(prefix) :]
        and line.endswith((".yml", ".yaml"))
    ]
    return tuple(sorted(names))


def derive_from_ref(cwd: Path, ref: str = "origin/main", *, timeout: int = 60) -> RequiredSet:
    """Derive the required set as it stands on a PROTECTED ref — never the caller's tree.
    ⛔ A GATE THAT READS ITS OWN SUBJECT IS DEFEATABLE BY ITS SUBJECT. Both inputs are
    tracked files, so a PR can edit `.mergify.yml` and `docs/ci/required-contexts.json`
    in the SAME diff and the two agree with each other: the snapshot witness would then
    certify the shrunken set it was just changed to. Measured on a scratch tree — delete
    one `check-success=docs` line and `docs` from `required_contexts` together and the
    derivation reports `snapshot_agrees: True` with `docs` simply absent from the set it
    evaluates, which is the "READY while a required check is red" direction this module
    exists to refuse.

    So the bytes are read from the protected ref (`origin/main` by default), where the
    set that actually governs the merge lives. A ref that cannot be read is
    `RequiredSetUnreadable` — a refusal, never a silent fall back to the workdir copy.

    ``ref`` is a parameter because `--required-set --ref HEAD` is a legitimate
    diagnostic ("what would this BRANCH require?"); it is not a default, because the
    protected ref is the authoritative answer. ⛔ It reads the last COMMIT on that ref
    (``git show <sha>:<rel>``, where `<sha>` is the peeled commit — both files as
    committed), never the working tree, so an uncommitted edit to either file is invisible
    here BY DESIGN: a gate that read uncommitted bytes would be reading the very tree it is
    judging.

    ⛔ IT DOES NOT FETCH, AND IT DOES NOT PRETEND TO. Resolving a ref succeeds whenever that
    ref EXISTS, however stale, so freshness is not something this function can assert.
    What it does instead is DISCLOSE the sha it read (`source` carries `<ref> (<sha12>)`), so
    a stale protected ref is visible in every verdict rather than being invisible. A caller
    that needs a fresh ref must fetch it: `ready_for_owner.collect_probe` does, via
    `_read_tree`, which is why that read is ordered FIRST there.
    """

    def _rev_parse() -> str:
        """The COMMIT `ref` resolves to HERE — so the caller can see what was read.

        ``^{commit}`` is load-bearing for the DISCLOSURE: bare ``--verify`` returns the TAG
        OBJECT for an annotated tag, so the sha in `source` would name an object that is not
        a commit — and the provenance line exists precisely to name the commit whose tree
        was read. (Since the blob reads are pinned to `sha` rather than to `ref`, a
        tag-object sha would still read the same bytes — git peels `<rev>:<path>` itself —
        so the defect this prevents is a WRONG NAME in the disclosure, not different bytes.)
        """
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise RequiredSetUnreadable(f"git rev-parse {ref}: {exc}") from None
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise RequiredSetUnreadable(
                f"git rev-parse --verify {ref} failed: "
                f"{detail[0] if detail else f'exit {proc.returncode}'}"
            )
        return proc.stdout.strip()

    sha = _rev_parse()
    with tempfile.TemporaryDirectory(prefix="required-set-") as tmp:
        root = Path(tmp)
        for rel in (MERGIFY_REL, RECORD_REL):
            try:
                # ⛔ READ THROUGH THE RESOLVED SHA, not the ref. `refs/remotes/origin/main`
                # lives in the SHARED common git dir and every lane in this fleet fetches
                # it — including this very tool, in `_read_tree`. A fetch landing between
                # `_rev_parse` and these reads would make the disclosed sha name a commit
                # whose files were never the ones read, and (since each file is its own
                # subprocess) could even pair `.mergify.yml` from one commit with the
                # record from another. Pinning the read to `sha` makes the disclosure exact.
                proc = subprocess.run(
                    ["git", "show", f"{sha}:{rel}"],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                # `ValueError` covers `UnicodeDecodeError`: `text=True` decodes the blob,
                # and a non-UTF-8 `.mergify.yml` raised straight out of a function whose
                # contract is to return a set or refuse.
                raise RequiredSetUnreadable(f"reading {rel} at {ref} ({sha[:12]}): {exc}") from None
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                raise RequiredSetUnreadable(
                    f"reading {rel} at {ref} ({sha[:12]}) failed: "
                    f"{detail[0] if detail else f'exit {proc.returncode}'} — the required "
                    "set must be read from the protected ref, never from the tree being "
                    "judged, so this is a refusal rather than a fallback"
                )
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(proc.stdout, encoding="utf-8")
        # The committed-config parser is the SAME one, so the ref path cannot drift
        # from the workdir path: only the bytes differ.
        # ⛔ THE WORKFLOW LISTING IS READ FROM THE PROTECTED REF TOO, and it has to be
        # passed in rather than listed off `root`: the temp root deliberately carries ONLY
        # `.mergify.yml` and the record (a gate is not allowed to read the tree it judges),
        # so `_workflow_files(root)` there answers `()` — "this repo has no workflow files" —
        # for every real repository, which would make every recorded emitter look stale and
        # refuse the whole fleet (#11175).
        derived = derive_from_config(
            root, workflow_files=_workflow_files_at(cwd, sha, ref, timeout)
        )
    # Disclose WHAT WAS READ, not what was hoped for: `git show` does not fetch, so the sha
    # is the only honest statement about freshness.
    return dataclasses.replace(derived, source=f"{derived.source} at {ref} ({sha[:12]})")
