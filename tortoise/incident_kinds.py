"""The ONE declaration site for the incident-kind vocabulary (#4780).

Before this module the vocabulary was re-derived in five places: per-module
``*_KIND`` constants, ~50 bare literals across the emitting modules, the
``AlertStore.KIND_OWNERS`` resolution map, the bash driver's mirror of it, and
the runbook's hand-maintained parallel table. Adding one kind meant editing
three sites per kind (a constant, a runbook row, a tuple in the pinning test),
kept in step by hand — the drift class
``tests/test_operator_alert.py::test_kind_constants_match_the_runbook`` was
written to catch for the kinds it happened to list.

Here, ONE entry per kind carries everything the other sites used to restate:
who may resolve it, whether it has a writer at all, whether it needs the manual
close-then-delete procedure, and whether its spelling is grandfathered. The
completeness of that entry set is checked at IMPORT time (see
:func:`_check_closure`), and the runbook ⇄ code set equality is asserted in
BOTH directions by ``tests/test_incident_kinds.py``.

⛔ THE KIND STRING IS THE DEDUP KEY — SO THE STRINGS ARE FROZEN
--------------------------------------------------------------
``ops/alerts/{kind}/{subject}.json`` is the create-once linearization point,
and the kind is also the ``[DR] {kind}`` token the R2-unreachable adoption path
searches on. Renaming a kind is therefore NOT a refactor; it is a data
migration. Doing it as a refactor silently:

* **orphans the dedup object** — the stale ``ops/alerts/{old}/{subj}.json``
  keeps arming the long attempt window while Telegram (which pushes only on
  FILED) never fires again for that incident;
* **files a SECOND issue** for a recurrence that is still open under the old
  kind, because the adoption search matches the ``[DR] {old}`` **title** and
  the new title no longer parses as that incident.

**OVERRIDES:** the common practice of renaming a constant to match a naming
convention when the value is a storage key — a kind string is a durable
storage key, not a symbol, so the convention loses to the key. The two
lowercase spellings (``abuse_review_needed``, ``abuse_suspended``) are
grandfathered on this basis (:data:`LEGACY_LOWERCASE_KINDS`); the frozen set is
pinned by the test, and a rename requires the migration playbook below.

**Migration playbook (the only safe rename path).** All of: (1) write the new
``ops/alerts/{new}/{subj}.json`` FROM each live ``{old}`` object — copy, do not
move, until (4); (2) adopt every open ``[DR] {old}`` issue by hand, because the
GH-search fallback will not match the new title; (3) keep the old spelling
readable at every read/delete path for at least one incident lifetime
(``AlertStore.PLATFORM_ALIASES`` is the existing precedent for a multi-spelling
kind); (4) only then delete the ``{old}`` objects. Skipping (2) is what
produces the duplicate issue; skipping (1) or (4) is what strands the sentinel.

RECOVERY WINDOWS (#3146)
------------------------
``KindSpec.resolve_streak`` declares, per kind, how many CONSECUTIVE healthy
observations are required before the incident may be cleared — the hysteresis
that stops a condition flapping around its threshold from producing an
open/close pair per cycle. The window is declared HERE (one site), consumed by
``tortoise/alert_store.py`` and mirrored by ``recovery_streak_for()`` in
``.github/scripts/registry-cron.sh`` (pinned by the cross-language contract
test beside ``test_kind_owner_contract_with_driver``).

The boundary is deliberate: the threshold-driven DR plane listed under
:data:`DR_RECOVERY_KINDS` (reachability probes, an archive's age against the
staleness threshold, a heartbeat against its window, a whole-pool freshness
reading) carries a window; every other kind keeps 1 and is unchanged. A
human-triggered resolve (``SIZE_GUARD_ABORT`` / ``DATA_LOSS_CANDIDATE`` at
re-baseline) must NOT need the operator to act twice, and an app-owned trigger
that already carries its own threshold on the trigger side
(``ANALYTICS_SINK_DEGRADED``) is outside this plane. **OVERRIDES:** the common
practice of one global hysteresis window for every alert — here the kinds
legitimately differ, so the window is a per-kind declaration with an explicit
default rather than a fleet-wide constant.

The runbook row for each kind lives in ``docs/ops/registry-backup-dr.md``
(§"Alert taxonomy + triage") and is pinned to this module by the test.

⛔ ONE KIND = ONE RECOVERY CONDITION = ONE EVIDENCE TIER (#3147)
---------------------------------------------------------------
R2_DOWN used to cover two failure modes with two different evidence
requirements: (a) R2 is unreachable — a probe does not reach it; (b) R2 answers
but the pool cannot be listed (``R2_LIST_OK=0``, typically a missing
``ListObjects`` permission). The two need different recovery evidence, so the
watcher could not safely clear the kind and ADR-011 had to refuse it the right
to close R2_DOWN at all — a guard around a modelling error rather than a model.
The vocabulary now models it: :data:`KIND_R2_DOWN` keeps the frozen key (a
rename would orphan the dedup object; see the playbook above) with its meaning
NARROWED to reachability, and :data:`KIND_R2_UNLISTABLE` is the new key for the
listing tier. Each has its own owner (see :data:`SPECS`), its own runbook row,
and its own issue-title token, so the watcher legitimately clears the tier its
probe covers and the driver retains the whole-pool measurement.

The operational home for the split is ``docs/ops/registry-backup-dr.md``
§"The two R2 tiers (#3147)". ADR-011 is a dated, human-gated decision record
and is deliberately not edited by that change.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Resolution authority (moved here from AlertStore.KIND_OWNERS, #3127) ─────
# Single-writer principle: for each incident kind, exactly ONE writer's probes
# cover that kind's recovery condition, and only that writer may declare it
# recovered. A writer whose probe does NOT cover the failing dependency must
# never clear it, because "the condition stopped looking true to me" is not
# recovery: it hides a live fault AND leaves no sentinel, so the owner re-files
# on its next run — one duplicate issue + Telegram pair per cycle.
WRITER_DRIVER = "driver"
WRITER_WATCHER = "watcher"
WRITER_APP = "app"
WRITER_UNSPECIFIED = "unspecified"


@dataclass(frozen=True, slots=True)
class KindSpec:
    """Everything the five restating sites used to hold for one incident kind.

    ``owner`` is the resolution authority — the one surface whose probes cover
    the kind's RECOVERY condition, mirrored in the driver's ``kind_owner()``.
    ``emitted`` records whether ANY code can file the kind: ``False`` for the
    documented "kinds with no writer", so a runbook row that says "NOT EMITTED"
    and a code base that emits nothing cannot silently disagree.
    ``manual_close`` means recovery is not programmatic — the runbook row MUST
    name the close-then-delete order, and an operator who reads only the
    incident needs that procedure to exist. ``legacy_lowercase`` grandfathers a
    pre-convention spelling (see the module docstring: the string is the key).
    """

    owner: str
    emitted: bool
    manual_close: bool
    legacy_lowercase: bool = False
    #: One-line purpose, for an importer that only wants the vocabulary.
    meaning: str = ""
    #: #3146 — the RECOVERY HYSTERESIS window: how many CONSECUTIVE healthy
    #: observations are required before the incident may be cleared. `1` (the
    #: default) preserves the historical "one healthy poll resolves" behaviour;
    #: `2` means a condition that flaps around its threshold produces ONE
    #: incident per flap-suppression window instead of one open/close pair per
    #: cycle. Semantics are consecutive OBSERVATIONS, not elapsed time: the two
    #: writers poll on different cadences, so a count is the only cadence-neutral
    #: reading and needs no clock (the #4994 ruling, generalized).
    #:
    #: The window is per KIND, not global, because the kinds differ: a
    #: threshold-driven measurement can hover at its boundary and must not be
    #: cleared by one lucky reading, while a human-triggered resolve (a
    #: re-baseline) or an app-owned trigger that already carries its own
    #: threshold must clear promptly. See :data:`DR_RECOVERY_KINDS` for the
    #: declared plane and the module docstring for the boundary rule.
    resolve_streak: int = 1


# ── The vocabulary — ONE constant per kind ───────────────────────────────────
# ⛔ Never change a value. See the module docstring's migration playbook.

# DR substrate + reachability. The group is by SUBSTRATE, not by owner: #3147
# moved R2_DOWN's owner to the watcher (a probe reaching R2 is a condition the
# watcher's own read tests) while R2_UNLISTABLE and the rest stay driver-owned.
#: #3147: the FROZEN R2 reachability key, its meaning NARROWED to the
#: reachability tier only (the substrate does not answer a probe). The string is
#: retained, not renamed to `R2_UNREACHABLE`, because it IS the R2 dedup key
#: (``ops/alerts/R2_DOWN/_.json``) and the ``[DR] R2_DOWN`` adoption token — a
#: rename is the migration playbook, not a refactor. The unlistable tier, which
#: used to share this key, is now :data:`KIND_R2_UNLISTABLE`.
KIND_R2_DOWN = "R2_DOWN"
#: #3147: R2 answers (a probe reaches it) but the pool cannot be LISTED
#: (``R2_LIST_OK=0``) — typically the access key lacks ``ListObjects`` — or the
#: app reports a ``storage_error``. A NEW key: no live dedup object or open issue
#: existed under it, so there is nothing to migrate. Distinct from
#: :data:`KIND_R2_DOWN` because the two need different recovery evidence and so
#: have different owners (see the module docstring).
KIND_R2_UNLISTABLE = "R2_UNLISTABLE"
KIND_APP_DOWN = "APP_DOWN"
KIND_WATCHER_DOWN = "WATCHER_DOWN"
KIND_SWEEP_CONFIG_ERROR = "SWEEP_CONFIG_ERROR"
KIND_SWEEP_OFF_STALE = "SWEEP_OFF_STALE"
KIND_SWEEP_NO_COVERAGE = "SWEEP_NO_COVERAGE"
#: NOT EMITTED — superseded, never wired (#3033). The enabled-but-0-teams case
#: is SWEEP_NO_COVERAGE; no surface ever emitted this kind.
KIND_LIVENESS_NO_WORK = "LIVENESS_NO_WORK"
#: #4994: ONE driver-owned, subject-less incident naming the N tenants whose
#: default archive the driver's app-independent direct-R2 leg measured stale —
#: instead of one `STALE` per tenant for one systemic fault. The per-tenant
#: residual stays on `STALE` (watcher-owned, below): the watcher resolves it
#: per ORG in-process, while the driver has no per-org probe, so a per-tenant
#: incident filed by the driver could never be cleared by its own writer.
KIND_POOL_STALE = "POOL_STALE"
#: #4612: the ride-along legs' OWN success is the recovery evidence — the driver
#: is the only surface that invokes them. An explicit owner (not "unspecified")
#: stops a future surface from clearing them on evidence its probes never
#: covered.
KIND_PURGE_FAILED = "PURGE_FAILED"
KIND_RECONCILE_FAILED = "RECONCILE_FAILED"

# Watcher-owned: archive/stamp freshness + the driver heartbeat, read in-process.
KIND_STALE = "STALE"
KIND_NEVER_BACKED_UP = "NEVER_BACKED_UP"
KIND_METADATA_LOST = "METADATA_LOST"
KIND_BACKUP_SET_MISSING = "BACKUP_SET_MISSING"
KIND_DRIVER_DOWN = "DRIVER_DOWN"

# Sweep-guard kinds: no single surface's probe owns their recovery, so their
# owner is UNSPECIFIED — anyone may clear them (#3127's pre-existing behaviour).
KIND_SIZE_GUARD_ABORT = "SIZE_GUARD_ABORT"
KIND_DATA_LOSS_CANDIDATE = "DATA_LOSS_CANDIDATE"
KIND_P0_GUARD_FAIL = "P0_GUARD_FAIL"
KIND_NO_ELIGIBLE_TEAMS = "NO_ELIGIBLE_TEAMS"
KIND_ENUM_DELTA = "ENUM_DELTA"
KIND_GRAPH_NAME_RESOLUTION_FAIL = "GRAPH_NAME_RESOLUTION_FAIL"
#: NOT EMITTED — no writer exists. ``gh_ok`` is not on ``/status``, so the
#: driver has nothing to read; the condition surfaces only as a red workflow
#: run. The writer is tracked as #3412 (deferred from #3033).
KIND_ALERTER_DOWN = "ALERTER_DOWN"

# App-owned: the drill resolves on its own success signal.
KIND_RESTORE_DRILL_FAILED = "RESTORE_DRILL_FAILED"

# App-owned, non-backup alert-plane kinds (#3820 / #3981 / #3873 / #4456 / #4872).
KIND_ANALYTICS_SINK_DEGRADED = "ANALYTICS_SINK_DEGRADED"
KIND_UNMETERED_INCREMENT = "UNMETERED_INCREMENT"
KIND_COHORT_CAP_UNENFORCEABLE = "COHORT_CAP_UNENFORCEABLE"
KIND_COHORT_COST_CAP = "COHORT_COST_CAP"
KIND_ABUSE_DECISION_FAULT = "ABUSE_DECISION_FAULT"
KIND_ABUSE_ENFORCEMENT_FAULT = "ABUSE_ENFORCEMENT_FAULT"
KIND_ABUSE_REVIEW_NEEDED = "abuse_review_needed"
KIND_ABUSE_SUSPENDED = "abuse_suspended"
KIND_BILLING_NOTIFY_REFUSED = "BILLING_NOTIFY_REFUSED"
KIND_PROVIDER_BILLING_EXHAUSTED = "PROVIDER_BILLING_EXHAUSTED"
#: #3631: a deferred billing-notification send failed. Given a triage row by
#: #4780 — the kind was emitted with NO runbook row, so an operator held an
#: incident with no documented recovery.
KIND_BILLING_SEND_FAILED = "BILLING_SEND_FAILED"
#: #3631 sibling: the transactional invite/OTP send leg failed. Same gap as
#: ``BILLING_SEND_FAILED`` — emitted, previously untriaged.
KIND_INVITE_SEND_FAILED = "INVITE_SEND_FAILED"

#: The grandfathered pre-convention spellings. A kind added from #4780 onward
#: must be SCREAMING_CASE; these two predate the convention and are frozen as
#: keys by the module docstring's rename policy.
LEGACY_LOWERCASE_KINDS: frozenset[str] = frozenset(
    {KIND_ABUSE_REVIEW_NEEDED, KIND_ABUSE_SUSPENDED})


#: #3146: the sane default for the THRESHOLD-DRIVEN DR plane — the kinds whose
#: recovery condition is a bounded measurement that can hover at its boundary
#: (a reachability probe, an archive's age against the staleness threshold, a
#: heartbeat against its window, a whole-pool freshness reading). Two
#: consecutive healthy observations, matching the repo's own precedent on
#: POOL_STALE (#4994/#5047). It is a named constant so the boundary is ONE
#: reviewable declaration; every other kind keeps the field default (1) and is
#: unchanged by #3146.
DR_RECOVERY_STREAK = 2


def _spec(owner: str, *, emitted: bool = True, manual_close: bool = False,
          legacy_lowercase: bool = False, resolve_streak: int = 1,
          meaning: str = "") -> KindSpec:
    return KindSpec(owner=owner, emitted=emitted, manual_close=manual_close,
                    legacy_lowercase=legacy_lowercase,
                    resolve_streak=resolve_streak, meaning=meaning)


#: kind -> its declaration. The ONE site for a kind's metadata.
SPECS: dict[str, KindSpec] = {
    # driver: its own R2 preflight + storage_error + a measured pool.
    # #3147: R2 reachability is decided by a PROBE REACHING the substrate — a
    # condition the watcher's own R2 read also tests — so the WATCHER owns the
    # reachability tier (it clears R2_DOWN on its next positive read) and the
    # driver, which cannot clear it, files it when its own preflight fails.
    # The whole-pool measurement stays with the only surface that performs it.
    # #3146: BOTH tiers are threshold-driven — a reachability probe and a
    # listing that can flap at its boundary — so both carry the declared
    # recovery window, like the rest of the DR plane below.
    KIND_R2_DOWN: _spec(
        WRITER_WATCHER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="R2 unreachable — no probe reaches the substrate (#3147)"),
    KIND_R2_UNLISTABLE: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="R2 answers but the pool cannot be listed (R2_LIST_OK=0) or the "
                "app reports storage_error (#3147)"),
    KIND_APP_DOWN: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="app unreachable from the driver"),
    KIND_WATCHER_DOWN: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="watcher heartbeat stale"),
    KIND_SWEEP_CONFIG_ERROR: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="enabled:false with a non-null config_error"),
    KIND_SWEEP_OFF_STALE: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="sweep off and the pool is not measured fresh"),
    KIND_SWEEP_NO_COVERAGE: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="a conclusive run backed up 0 teams"),
    KIND_LIVENESS_NO_WORK: _spec(
        WRITER_DRIVER, emitted=False,
        meaning="superseded, never wired (#3033)"),
    KIND_POOL_STALE: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="pooled stale tenants in ONE incident (#4994)"),
    KIND_PURGE_FAILED: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="hourly trash-purge ride-along incomplete"),
    KIND_RECONCILE_FAILED: _spec(
        WRITER_DRIVER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="hourly reconcile ride-along incomplete"),
    # watcher: freshness + heartbeat, read in-process.
    KIND_STALE: _spec(
        WRITER_WATCHER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="newest archive older than the threshold"),
    KIND_NEVER_BACKED_UP: _spec(
        WRITER_WATCHER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="eligible graph with no archive"),
    KIND_METADATA_LOST: _spec(
        WRITER_WATCHER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="archives exist but the per-graph state is missing"),
    KIND_BACKUP_SET_MISSING: _spec(
        WRITER_WATCHER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="state exists but no archives"),
    KIND_DRIVER_DOWN: _spec(
        WRITER_WATCHER, resolve_streak=DR_RECOVERY_STREAK,
        meaning="driver heartbeat stale"),
    # sweep guards: no single probe owns recovery.
    KIND_SIZE_GUARD_ABORT: _spec(
        WRITER_UNSPECIFIED,
        meaning="team graph over the node limit; the dump was aborted "
                "(re-baseline resolves it)"),
    KIND_DATA_LOSS_CANDIDATE: _spec(
        WRITER_UNSPECIFIED,
        meaning="a team's node count dropped past the loss guard "
                "(re-baseline resolves it)"),
    KIND_P0_GUARD_FAIL: _spec(
        WRITER_UNSPECIFIED, meaning="the dump manifest did not name its graph"),
    KIND_NO_ELIGIBLE_TEAMS: _spec(
        WRITER_UNSPECIFIED, meaning="team sweep enabled, 0 eligible teams (#655)"),
    KIND_ENUM_DELTA: _spec(
        WRITER_UNSPECIFIED, meaning="team enumeration went >0 -> 0 (#669)"),
    KIND_GRAPH_NAME_RESOLUTION_FAIL: _spec(
        WRITER_UNSPECIFIED, meaning="every team failed graph-name resolution"),
    KIND_ALERTER_DOWN: _spec(
        WRITER_UNSPECIFIED, emitted=False,
        meaning="daemon PAT dead; NOT EMITTED, writer tracked as #3412"),
    # app: the drill resolves on its own success signal.
    KIND_RESTORE_DRILL_FAILED: _spec(
        WRITER_APP, meaning="scheduled restore drill failed or breached the RTO"),
    # app alert-plane kinds.
    KIND_ANALYTICS_SINK_DEGRADED: _spec(
        WRITER_UNSPECIFIED, meaning="the analytics_events write sink degraded"),
    KIND_UNMETERED_INCREMENT: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="a metering increment was dropped; request served (#3981)"),
    KIND_COHORT_CAP_UNENFORCEABLE: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="pre-spend cohort cap not evaluated (window unresolvable)"),
    KIND_COHORT_COST_CAP: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="the pre-spend cohort cap FIRED (capture refused 402)"),
    KIND_ABUSE_DECISION_FAULT: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="an abuse DECISION-path store call raised (#4872)"),
    KIND_ABUSE_ENFORCEMENT_FAULT: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="an abuse enforcement ACTION could not be applied (#4872)"),
    KIND_ABUSE_REVIEW_NEEDED: _spec(
        WRITER_UNSPECIFIED, manual_close=True, legacy_lowercase=True,
        meaning="persistent breach: human review, not suspension (#5425)"),
    KIND_ABUSE_SUSPENDED: _spec(
        WRITER_UNSPECIFIED, manual_close=True, legacy_lowercase=True,
        meaning="operator-initiated suspension (no automatic lane since #5425)"),
    KIND_BILLING_NOTIFY_REFUSED: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="the offload seam REFUSED a Stripe billing notification (#4456)"),
    KIND_PROVIDER_BILLING_EXHAUSTED: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="a serving provider refused on its own budget/limit (#3873)"),
    KIND_BILLING_SEND_FAILED: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="the Resend billing-notification send leg failed (#3631)"),
    KIND_INVITE_SEND_FAILED: _spec(
        WRITER_UNSPECIFIED, manual_close=True,
        meaning="the transactional invite/OTP send leg failed (#3631)"),
}

#: name -> value for every ``KIND_*`` constant in this module. Derived from the
#: namespace, NOT restated: a constant that is never entered in :data:`SPECS`
#: is caught by :func:`_check_closure` below, and a constant defined AFTER this
#: line is caught by the registry test (which re-derives from the same
#: namespace once the whole module has executed).
_KIND_CONSTANTS: dict[str, str] = {
    name: value for name, value in list(globals().items())
    if name.startswith("KIND_") and isinstance(value, str)
}

#: The declared vocabulary. Derived, never restated.
KIND_NAMES: frozenset[str] = frozenset(_KIND_CONSTANTS.values())


def _check_closure() -> None:
    """Fail at IMPORT time if the vocabulary and its metadata disagree.

    The drift this module exists to remove is a kind declared in one place and
    forgotten in another. An import-time refusal means a half-declared kind
    cannot even be deployed, rather than surfacing as a missing runbook row or
    an ``unspecified`` owner months later.
    """
    if set(SPECS) != set(KIND_NAMES):
        missing = sorted(set(KIND_NAMES) - set(SPECS))
        extra = sorted(set(SPECS) - set(KIND_NAMES))
        raise RuntimeError(
            "incident-kind registry is half-declared — every kind needs a "
            f"KindSpec entry and a KIND_* constant: no spec for {missing}, "
            f"no constant for {extra}")
    # A duplicate VALUE would make two constants the same key: the dedup object
    # and the adoption search cannot tell them apart, while the set comparison
    # above would still pass.
    if len(_KIND_CONSTANTS) != len(KIND_NAMES):
        raise RuntimeError(
            "two KIND_* constants share one value — the kind string IS the "
            "dedup key, so a duplicate silently merges two incidents")
    for legacy in LEGACY_LOWERCASE_KINDS:
        if legacy not in KIND_NAMES:
            raise RuntimeError(f"unknown grandfathered kind {legacy!r}")
        if not SPECS[legacy].legacy_lowercase:
            raise RuntimeError(f"{legacy!r} is grandfathered but not marked so")
    for kind, spec in SPECS.items():
        if kind.isupper() != (not spec.legacy_lowercase):
            raise RuntimeError(
                f"{kind!r}: a new kind must be SCREAMING_CASE; only the "
                "grandfathered pre-convention spellings may be lowercase")
        if kind in LEGACY_LOWERCASE_KINDS and kind.isupper():
            raise RuntimeError(f"{kind!r} is SCREAMING_CASE; un-grandfather it")
        if spec.owner not in (WRITER_DRIVER, WRITER_WATCHER, WRITER_APP,
                              WRITER_UNSPECIFIED):
            raise RuntimeError(f"{kind!r}: unknown owner {spec.owner!r}")
        # #3146: a window below 1 would mean "never resolve" — a typo no
        # deployment may reach. 1 is the explicit "no window" spelling.
        if not isinstance(spec.resolve_streak, int) or spec.resolve_streak < 1:
            raise RuntimeError(
                f"{kind!r}: resolve_streak must be an integer >= 1, got "
                f"{spec.resolve_streak!r}")


_check_closure()

#: kind -> the one writer allowed to resolve it. Derived, never restated:
#: ``AlertStore.KIND_OWNERS`` re-exports this, and ``kind_owner()`` falls back
#: to :data:`WRITER_UNSPECIFIED` for anything unlisted — which is every kind
#: not named here.
KIND_OWNERS: dict[str, str] = {
    kind: spec.owner for kind, spec in SPECS.items()
    if spec.owner != WRITER_UNSPECIFIED
}


#: #3146: the kinds that carry a recovery window, DERIVED from the declaration
#: (never restated), so the boundary is exactly the set whose
#: :attr:`KindSpec.resolve_streak` is above 1.
DR_RECOVERY_KINDS: frozenset[str] = frozenset(
    kind for kind, spec_ in SPECS.items() if spec_.resolve_streak > 1)


def spec(kind: str) -> KindSpec:
    """The declaration for ``kind``; raises ``KeyError`` for an unknown string.

    Deliberately strict: a caller holding a kind the registry does not know is
    a caller that can create a dedup object nothing else understands.
    """
    return SPECS[kind]


def is_declared(kind: str) -> bool:
    """True iff ``kind`` is part of the declared vocabulary."""
    return kind in SPECS


def _coerce_streak(value: Any, origin: str) -> int | None:
    """A configured window, or ``None`` when the value is not usable (#3146).

    Mirrors the driver's own coercion exactly: a non-integer OR a NEGATIVE
    value is IGNORED with a warning (the declared value stands), and ``0`` is
    read as 1 with a warning — a window of zero would mean "never resolve",
    which no typo may reach. A negative value is a typo, not the deliberate
    "clear now" spelling (that is the explicit ``1``), so it must not silently
    defeat the hysteresis this window exists to provide (#3146 review P2: the
    bash twin already ignored it, so the two writers disagreed on the recovery
    window for the same env var).

    The accepted shape is spelled out rather than left to ``int()``, which is
    deliberately MORE permissive than the bash ``case`` guard (it accepts
    ``+3``, ``1_0`` and non-ASCII digits). Relying on it is how the two writers
    drifted in the first place, so the shape here is exactly the driver's:
    an optional ``-`` followed by ASCII digits, compared after
    :meth:`str.strip` (the same surrounding whitespace the bash twin trims).
    """
    text = str(value).strip()
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if not digits or any(ch not in "0123456789" for ch in digits):
        logger.warning(
            "ALERT_RECOVERY_STREAK config %s=%r is not an integer — ignoring it",
            origin, value)
        return None
    if negative and any(ch != "0" for ch in digits):
        logger.warning(
            "ALERT_RECOVERY_STREAK config %s=%r is negative — ignoring it "
            "(the declared window stands; use 1 to deliberately clear now)",
            origin, value)
        return None
    # ``-0`` is ZERO (the sign carries no value), not a negative window, so it
    # falls through the below-1 clamp exactly like the bash twin.
    parsed = 0 if negative else int(digits)
    if parsed < 1:
        logger.warning(
            "ALERT_RECOVERY_STREAK config %s=%r is below 1 — reading it as 1 "
            "(a window of 0 would mean 'never resolve')", origin, value)
        return 1
    return parsed


def resolve_streak_for(kind: str, env: Mapping[str, str] | None = None) -> int:
    """The EFFECTIVE recovery window for ``kind`` (#3146).

    Precedence, highest first — the same contract the driver's
    ``recovery_streak_for()`` implements:

    1. ``ALERT_RECOVERY_STREAK=N`` — every kind. The "clear now" emergency
       lever (set it to 1 to restore the pre-#3146 behaviour fleet-wide).
    2. ``ALERT_RECOVERY_STREAKS="KIND=n,KIND2=m"`` — per kind.
    3. ``BACKUP_POOL_RESOLVE_STREAK`` — the legacy POOL_STALE-only knob
       (#4994), kept working as that kind's alias by decision 7/8 of #3146.
    4. the declared :attr:`KindSpec.resolve_streak`.

    An unknown kind has no declaration, so it falls back to 1 ("no window"),
    the same safe default ``kind_owner`` uses for an unlisted kind.
    """
    environment = os.environ if env is None else env
    declared = SPECS[kind].resolve_streak if kind in SPECS else 1
    window = declared
    if kind == KIND_POOL_STALE:
        legacy = environment.get("BACKUP_POOL_RESOLVE_STREAK")
        if legacy not in (None, ""):
            coerced = _coerce_streak(legacy, "BACKUP_POOL_RESOLVE_STREAK")
            if coerced is not None:
                window = coerced
    for pair in (environment.get("ALERT_RECOVERY_STREAKS") or "").split(","):
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        if name.strip() != kind:
            continue
        coerced = _coerce_streak(value, "ALERT_RECOVERY_STREAKS")
        if coerced is not None:
            window = coerced
    global_value = environment.get("ALERT_RECOVERY_STREAK")
    if global_value not in (None, ""):
        coerced = _coerce_streak(global_value, "ALERT_RECOVERY_STREAK")
        if coerced is not None:
            window = coerced
    return window
