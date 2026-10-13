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

The runbook row for each kind lives in ``docs/ops/registry-backup-dr.md``
(§"Alert taxonomy + triage") and is pinned to this module by the test.
"""

from __future__ import annotations

from dataclasses import dataclass

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


# ── The vocabulary — ONE constant per kind ───────────────────────────────────
# ⛔ Never change a value. See the module docstring's migration playbook.

# Driver-owned DR substrate (its own R2 preflight + storage_error + a measured pool).
KIND_R2_DOWN = "R2_DOWN"
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


def _spec(owner: str, *, emitted: bool = True, manual_close: bool = False,
          legacy_lowercase: bool = False, meaning: str = "") -> KindSpec:
    return KindSpec(owner=owner, emitted=emitted, manual_close=manual_close,
                    legacy_lowercase=legacy_lowercase, meaning=meaning)


#: kind -> its declaration. The ONE site for a kind's metadata.
SPECS: dict[str, KindSpec] = {
    # driver: its own R2 preflight + storage_error + a measured pool.
    KIND_R2_DOWN: _spec(WRITER_DRIVER, meaning="R2 unreachable or not listable"),
    KIND_APP_DOWN: _spec(WRITER_DRIVER, meaning="app unreachable from the driver"),
    KIND_WATCHER_DOWN: _spec(WRITER_DRIVER, meaning="watcher heartbeat stale"),
    KIND_SWEEP_CONFIG_ERROR: _spec(
        WRITER_DRIVER, meaning="enabled:false with a non-null config_error"),
    KIND_SWEEP_OFF_STALE: _spec(
        WRITER_DRIVER, meaning="sweep off and the pool is not measured fresh"),
    KIND_SWEEP_NO_COVERAGE: _spec(
        WRITER_DRIVER, meaning="a conclusive run backed up 0 teams"),
    KIND_LIVENESS_NO_WORK: _spec(
        WRITER_DRIVER, emitted=False,
        meaning="superseded, never wired (#3033)"),
    KIND_POOL_STALE: _spec(
        WRITER_DRIVER, meaning="pooled stale tenants in ONE incident (#4994)"),
    KIND_PURGE_FAILED: _spec(
        WRITER_DRIVER, meaning="hourly trash-purge ride-along incomplete"),
    KIND_RECONCILE_FAILED: _spec(
        WRITER_DRIVER, meaning="hourly reconcile ride-along incomplete"),
    # watcher: freshness + heartbeat, read in-process.
    KIND_STALE: _spec(
        WRITER_WATCHER, meaning="newest archive older than the threshold"),
    KIND_NEVER_BACKED_UP: _spec(
        WRITER_WATCHER, meaning="eligible graph with no archive"),
    KIND_METADATA_LOST: _spec(
        WRITER_WATCHER, meaning="archives exist but the per-graph state is missing"),
    KIND_BACKUP_SET_MISSING: _spec(
        WRITER_WATCHER, meaning="state exists but no archives"),
    KIND_DRIVER_DOWN: _spec(WRITER_WATCHER, meaning="driver heartbeat stale"),
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


_check_closure()

#: kind -> the one writer allowed to resolve it. Derived, never restated:
#: ``AlertStore.KIND_OWNERS`` re-exports this, and ``kind_owner()`` falls back
#: to :data:`WRITER_UNSPECIFIED` for anything unlisted — which is every kind
#: not named here.
KIND_OWNERS: dict[str, str] = {
    kind: spec.owner for kind, spec in SPECS.items()
    if spec.owner != WRITER_UNSPECIFIED
}


def spec(kind: str) -> KindSpec:
    """The declaration for ``kind``; raises ``KeyError`` for an unknown string.

    Deliberately strict: a caller holding a kind the registry does not know is
    a caller that can create a dedup object nothing else understands.
    """
    return SPECS[kind]


def is_declared(kind: str) -> bool:
    """True iff ``kind`` is part of the declared vocabulary."""
    return kind in SPECS
