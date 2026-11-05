"""The per-DIMENSION allowance gate — the ruled storage/LLM dimensions (#5331).

**Why this module exists.** The plan's storage allowance was measured against the
node-count dimension: ``quota.enforce_org_limit`` counts NODES and refuses at
``max_points``. The owner ruled the customer-facing unit is **bytes** — storage in
MB/GB for Postgres, MB for the graph, and LLM usage in tokens for extraction
(``#4495``, ``#5331``) — and the shipped gate counts none of them. So the meter
and the gate disagree about what is being sold, which is why the capstone item
could not be measured (#9567 D11/D12).

**⛔ THIS MODULE GATES; IT DOES NOT PRICE.** It sets no allowance, no rate, no
tier boundary, no overage and no retention window. Every allowance value is the
owner's and is, today, ``TBD-BY-MEASUREMENT`` under the ``#4051`` ruling: **no
production resolver emits any ``ALLOWANCE_KEYS`` value**, so on every live path
every dimension below is UNCONFIGURED and the gate is inert. It refuses nothing
until someone configures a limit — which is the ordering the storage architecture
requires (*"the meter must be wired into the gate before any allowance is
configured"*, ``docs/architecture/STORAGE-ARCHITECTURE.md``: §"The byte path").

**⚠️ "INERT" IS ABOUT WHAT IT REFUSES, NOT ABOUT WHAT IT READS (#9567 review
round 10).** Arming nothing means no dimension can be over its allowance — it does
NOT mean the gate is free. ``hosted_api._check_org_limit`` fills the missing keys
at the REST seam from ``quota.resolve_org_limits``, so every gated REST request
(``points``, ``documents``, ``sessions``) performs ONE control-plane read, and a
**fault** in that read is a ``QuotaCheckError`` → HTTP 500 **even with no allowance
configured**. ⚠️ "One read" is the SUPABASE shape: in registry/self-host mode the
same fill is an SDK construction plus a ``MATCH (t:Team {id:…})`` query
(``resolve_org_limits``'s registry arm → ``_make_sdk(namespace="registry")``), so
the per-request figure is larger there — it is an SDK open, not an HTTP GET
(#9567 review round 12, P2). Both substrates are disclosed rather than averaged:
this gate is inert about what it REFUSES on every substrate, but never about what
it READS, and a self-hosted deployment pays the bigger read. That is an over-block on our own bookkeeping fault, and the read
side of the ``#3981`` tension this module states below: the ruling forbids
refusing a paying org over a METERING fault, while a cap that serves past our
fault is a cap that can be evaded during an outage. We chose refuse-and-name it
for the read, so that a config read we cannot complete never degrades silently to
"unlimited" — the cost is that an outage of the orgs/Team read refuses gated
writes rather than serving them while nothing is armed. This is a DISCLOSED
judgement, not an oversight; the alternative (mirroring the carry into every REST
limits builder) is the drift ``#3874`` rules out, and the fix if the read ever
matters operationally is a short-TTL memo on the resolver — NOT a silent
"unlimited" on a failed read.

**⚠️ BUT A LOOKUP MISS IS NOT ONE OF THOSE FAULTS (#9567 review round 11).** The
read has THREE outcomes, not two, and only the middle one is ours to refuse:

* the resolver **resolves** the org → fill the missing keys;
* the resolver **faults** (control plane down, unreadable row, schema drift) →
  ``QuotaCheckError`` → HTTP 500, the disclosed judgement above;
* the resolver has **no record** for the org (``quota.OrgNotFoundError``) → the
  caller's already-authoritative dict is returned UNCHANGED, and the dimension
  stays at this module's declared default (an absent key is unconfigured =
  unlimited, ``#4051``).

The third case is a well-defined STATE, not a fault: a cap cannot be armed from a
record that does not exist, and refusing it made every gated REST door answer 500
for a tenant that had done nothing wrong — while the *same* absence read as
UNLIMITED on every door that skips this fill, i.e. one state with two answers
depending on the door. It also made the shipped tests that stub an org dict
(no registry row) go red against a behaviour change nothing wanted.

**⛔ ONE REGISTRY, NOT TWO (#1178).** The dimensions are NOT redeclared here.
``cost_dimensions.DIMENSIONS`` declares them (``graph_storage``,
``postgres_storage``, ``capture_extraction``); this module only states, per
declared dimension, **which resolved-limits key carries its allowance** and
**which meter reads its usage**. ``_dimension()`` resolves every entry against
that registry at import, so a renamed or removed dimension is a hard import
failure rather than a gate that silently stops gating. A test pins the same
invariant.

**⚠️ HOW AN ALLOWANCE REACHES THE GATE (the arming seam).** The gate reads the
limits dict it is GIVEN, so a key a surface never carried is not a warning — it
is UNLIMITED. Three carriers close that on every surface, and the two that
enumerate from :data:`ALLOWANCE_KEYS` do so generically:

* ``hosted_api._check_org_limit`` (the ONE REST convergence point) fills any key
  the lane's hand-assembled dict is missing from ``quota.resolve_org_limits`` —
  the ONE resolver allowed to emit an allowance (#3874's rule: *"there is ONE
  resolver, so the two cannot drift"*).
* ``mcp_server._enforce_quota`` (the pre-write gate for every ``_quota_gated``
  MCP tool) does the same fill, because the MCP lane's ContextVar carries none
  of the three in Supabase mode (``mcp_auth`` sets ``limits = org``, the
  ``resolve_api_key`` dict, which has no allowance column). Without it, arming
  in the resolver would REFUSE on REST and SERVE WITHOUT LIMIT on ~19 MCP
  points/documents tools — the same fail-open, one surface short (#9567 review
  round 15).
* the MCP capture BRIDGE (``mcp_server.tortoise_session_capture``) carries them
  out of the resolved limits dict it already holds.

So arming an allowance in the resolver reaches BOTH lanes with no second edit,
instead of refusing on one while the other silently serves past it. The REST
fill runs in ``_check_org_limit``, which REST doors must call OFF the event
loop — it is one control-plane round-trip per gated request, not a dict lookup
(#9567 review round 10).

**Three outcomes per configured dimension, and only three:**

* **under the allowance** → allowed (the write proceeds);
* **at/over the allowance** → ``QuotaExceededError``, naming the dimension, with
  ``used``/``limit`` in the dimension's own unit and the meter's honesty attached
  (a sampling estimate must not be presented as exact);
* **configured, but the meter yields NO READING** (never measured, measurement
  error, or a reading older than the dimension's stated bound) →
  ``QuotaCheckError``. Never a silent pass — the #686 fail-closed posture, and
  never a bare exception escaping as an unmapped 500 (the #4051 lesson).

An UNCONFIGURED dimension (key absent) is UNLIMITED by construction: no invented
cap, and the dimension keeps being METERED by its own writer — an unset allowance
never disables measurement. A key that is PRESENT with ``None`` is also
unlimited, but the meter is still READ (and a broken meter logged, not raised), so
"unlimited" cannot be used to stop measuring a dimension.

**Units are the meter's own; nothing is converted.** ``graph_storage`` is
denominated in MB because that is what the byte meter publishes
(``graph_storage_mb``), and converting it to a byte allowance would force a
MiB-vs-decimal-MB factor — the error class ``STORAGE-ARCHITECTURE.md`` records as
costing 3.77× on the per-node figures. ``capture_extraction`` is in tokens;
``postgres_storage`` is in bytes, which is #5331's own ruled base (*"the unit is
bytes"* — MB/GB for Postgres and MB for the graph are byte multiples and are a
PRESENTATION decision, not a second unit). The ``unit`` of every entry is READ
from the declared row (``cost_dimensions.by_name(...).unit``), never restated
here — two copies of a unit is how a unit factor goes wrong.

**⚠️ WHICH DOORS ARE GATED (per dimension, declared — not one global set).** The
gate runs inside ``quota.enforce_org_limit``, the seam REST and MCP share. Each
entry names the resources whose operation can move ITS meter: the storage
dimensions are gated on the write doors (``points``/``documents``), and
``capture_extraction`` on ``sessions`` ONLY — the capture lane
(``hosted_api._capture_session_impl``) reaches ``_check_org_limit(org,
"sessions")`` and that is the only door that SPENDS extraction tokens. A key
mint (``api_keys``), a seat (``users``) or a graph create (``graphs``) moves no
meter, so gating them would refuse an operation that cannot cross the cap — the
cap that blocks a fix, which this module refuses to build.

⚠️ **BOUNDARY 3 — a failed meter WRITE looks like an empty window.**
``record_capture_usage`` swallows a write failure (the capture is served, #3981)
and records ``drop_class="increment_write_unconfirmed"``; no ledger row is
created, so ``get_capture_usage`` answers ``"absent"`` — which this dimension
admits as a real zero. During such an outage the allowance reads a genuine zero
and the tenant keeps spending, i.e. the write-side mirror of the read fault this
module refuses on. ``get_unmetered_increments`` cannot close it as-is (it is
org-CUMULATIVE with no window, so gating on it would refuse forever after one past
drop); closing it needs a WINDOW-scoped drop counter, which is a follow-up tracked
by #10776 and declared here rather than left to be discovered.

⚠️ **BOUNDARY 4 — WHERE the carry runs (no lock, its own lane).** The fill above
is a network read, so it must not be submitted to a pool (or a LOCK) that
belongs to a different substrate, and it must not be held under a per-org
mutex. These doors satisfy both: the
``points`` doors run it through ``hosted_api._check_org_limit_off_loop``
(Supabase → the
control-plane pool, self-host → the graph pool, because the registry IS the
graph connection), the capture door runs it through the SAME twin
(``_check_org_limit_off_loop``, ``check_org_limit.sessions`` — it used to run
the whole check on a bare worker thread, which put a control-plane read on the
loop's default executor; main-repair 2026-11-04),
and ``POST /v1/sessions/commit`` runs the carry in the HANDLER, before its
per-graph commit lock is taken, handing the stamped dict down (review round 13
— held inside that lock, a control-plane stall was a network wait every other
commit for the org queued behind, since #9567 made that in-lock gate perform
this read). TWO doors do NOT, and both are named so neither is inherited
silently. The first is the declared exception: ``/v1/demo`` runs the check
inside its own per-org seed lock, deliberately, because the idempotent
``existing`` short-circuit must come BEFORE the read — a re-seed of an
already-seeded org must not pay a control-plane read, and the lock is held only
once per org's first seed. The second is a declared GAP: the ``documents``
door never reached this seam at all — it calls ``enforce_org_limit(limits,
"documents", …)`` directly on a limits dict ``_run_docs_indexing`` resolved
INLINE on the event loop, which is the on-loop read this whole boundary exists
to prevent. It is pre-existing, it is filed as #10946, and it is named here
because this sentence used to claim it as satisfied (main-repair 2026-11-04,
review cycle 3). Any NEW gated door has to make this choice explicitly;
inheriting whichever pool or lock the rest of its work uses is how
review round 12 (a PostgREST read on the graph pool) and round 13 (the same
read inside the commit lock) both happened.

⚠️ **RESIDUAL, named rather than hidden:** the ``sessions`` door is
``_check_org_limit(org, "sessions")`` in ``hosted_api._capture_session_impl``,
and it runs unconditionally — including the store-only lanes (keyless #3892 and
extraction-disabled #4258, both CLOSED) and replays, which make no provider call and cannot
move the token meter. So an exhausted token allowance would also refuse a
zero-token capture on that one door. Narrowing it needs the check moved into the
branch that actually extracts, which is a change INSIDE that handler and a
follow-up (tracked by #10776, with #3892/#4258 above now closed); it is stated here
so the boundary is a known one rather than a surprise at arming time.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .cost_dimensions import by_name as _declared_dimension
from .quota import (
    OrgNotFoundError,
    QuotaCheckError,
    QuotaExceededError,
    with_limit_contact,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWANCE_GATED_RESOURCES",
    "ALLOWANCE_KEYS",
    "BLOCKED",
    "DIMENSIONS",
    "AllowanceDimension",
    "MeterReading",
    "carry_allowance_keys",
    "carry_resolved_allowances",
    "enforce_allowances",
]

#: The stated bound on how old a GAUGE reading may be and still be enforced.
#: It is an EVIDENCE-AGE bound, not an allowance: a stale gauge describes a graph
#: that no longer exists, so enforcing it would decide from a number we cannot
#: defend. 24 h is chosen because the graph meter is a periodic estimate, not a
#: per-write figure — and it is overridable by the caller passing its own
#: ``meters``/by configuring the dimension, never by an implicit default cap.
STALE_AFTER_SECONDS = 24 * 60 * 60

#: A configured dimension whose meter does NOT exist is BLOCKED, never faked.
BLOCKED = "blocked"


class _Absent:
    """The meter's answer for "there is no row YET" — distinct from ``None``.

    ``None`` means the meter could not produce a figure (a failure); this means
    the figure is KNOWN to be zero for a cumulative flow nobody has started. The
    two must not share a value: flattening them is either a fail-open (a broken
    meter read as zero) or a deadlock (a fresh window refused forever). Only a
    dimension that declares ``absent_is_zero`` may consume this as a zero; every
    other dimension still refuses on it.
    """

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<no usage yet>"


ABSENT = _Absent()


@dataclass(frozen=True)
class MeterReading:
    """One meter's answer for one org. The unit is the DIMENSION's unit.

    ``estimated``/``note`` carry the instrument's honesty so a refusal built on
    a sampling estimate cannot read as exact (the graph meter's own contract).
    ``measured_at`` is the ISO instant the reading describes, when the instrument
    records one; ``None`` means the instrument has no age, not that it is fresh.
    """

    used: float
    measured_at: str | None = None
    estimated: bool = False
    note: str | None = None


@dataclass(frozen=True)
class AllowanceDimension:
    """One DECLARED cost dimension, as the allowance gate sees it.

    ``dimension`` must name a row in ``cost_dimensions.DIMENSIONS`` — verified at
    import. ``limit_key`` is the key in the RESOLVED limits dict (the same dict
    ``quota.enforce_org_limit`` already receives), and its absence is what makes
    the dimension unlimited. ``meter`` is ``None`` for a dimension with no
    per-org meter plumbed: ``blocked_reason`` then states exactly what is
    missing, and configuring such a dimension REFUSES (fail closed) rather than
    passing on a figure nobody measured.
    """

    dimension: str
    limit_key: str
    unit: str
    meter: Callable[[str], MeterReading | None] | None = None
    stale_after_seconds: int | None = None
    blocked_reason: str | None = None
    resources: frozenset[str] = frozenset({"points", "documents"})
    #: Whether "no ledger row yet" is a real ZERO or NO reading. It differs by
    #: dimension KIND: a cumulative FLOW for a window that has just begun is
    #: zero-by-construction (nobody has captured), while a GAUGE's absence means
    #: the instrument has not reported. Getting this wrong in the flow direction
    #: is a deadlock: the row is created by the very operation being refused.
    absent_is_zero: bool = False

    @property
    def blocked(self) -> bool:
        """True when this dimension MUST NOT be enforced, and why is recorded.

        ``blocked_reason`` is the whole state: a dimension can carry a working
        meter and still be BLOCKED (``graph_storage`` is — its meter's own
        contract forbids capping on the ledger figure). The reason is what the
        refusal shows, so it cannot be a note beside the state.
        """
        return self.blocked_reason is not None


# ── the meters: adapters onto the instruments that already exist ────────────
#
# Neither adapter raises: a meter is FAIL-SOFT by its own contract
# (``graph_storage``'s docstring: "FAIL-SOFT … never raise"; the metering readers
# degrade to a zero view, #923). The fail-CLOSED half is the GATE's, and it lives
# in ``_enforce_dimension`` below: a meter that cannot answer returns ``None``,
# and ``None`` is refused. A meter that raised would make the gate's refusal
# depend on an exception rather than on "no reading", which is the distinction
# D12 measures.


class MeterUnavailable(RuntimeError):
    """A meter that has NO reading for a reason worth naming.

    ``None`` means "no reading" with nothing more to say (a dimension nobody has
    measured yet). This carries the ones that DO have something to say — chiefly
    that the fault is OURS (an unresolvable metering window, a store error), which
    a refusal must not present to the tenant as their usage. It is still a
    REFUSAL (fail closed, #686), and it is LOGGED AT ERROR with the org and the
    reason: the durable disclosure needs its own ``served=False`` surface and
    drop class on #10776 — reusing the swallow-site reporter would assert the
    opposite of what happened — and that is a follow-up, not something this gate
    may fake.
    """


def _graph_storage_mb(org_id: str) -> MeterReading | None:
    """The org's last graph-storage GAUGE, in MB (#5331 unit 2).

    ``get_graph_storage_reading`` never raises: a degraded read is
    indistinguishable from "never measured" in its return value, and both carry
    ``graph_storage_measured_at is None``. Both are NO READING here — which is
    the honest answer for both, because a zero-valued degradation would otherwise
    compare as 0 MB and pass silently.
    """
    from .metering import get_graph_storage_reading

    reading = get_graph_storage_reading(org_id)
    measured_at = reading.get("graph_storage_measured_at")
    if not measured_at:
        return None
    return MeterReading(
        used=float(reading.get("graph_storage_mb") or 0.0),
        measured_at=str(measured_at),
        estimated=True,
        note=("measured with GRAPH.MEMORY USAGE (a SAMPLES-based estimate that "
              "excludes per-graph/Redis overhead)"),
    )


def _describe_fault(reason: str) -> str:
    """The sentence a REFUSAL uses for an our-side metering fault.

    ⚠️ It does NOT log and does NOT report on the #3981/#4779 surface, for a
    reason the logging sites make explicit: the SAME meter is read on two paths.
    On the fail-closed path the write is refused (``_reading`` logs at ERROR with
    ``tenant refused``); on the "unlimited but still metered" path it is SERVED
    (``_reading`` logs a WARNING). A message minted here cannot know which, and a
    durable disclosure cannot either — so the fault is described for the refusal,
    and the logging is done by the caller that KNOWS. ``metering``'s
    ``report_unmetered_increment`` is the swallow-site reporter: its text asserts
    "the request was served" and it hard-codes ``DROP_CLASS_WINDOW_UNRESOLVABLE``,
    so reusing it here would assert the opposite of a refusal and file the wrong
    class. The durable ``served=False`` surface + class is tracked by #10776
    (#4779, its original home, is CLOSED).
    """
    return f"the {reason} fault is on our side"


def _extraction_tokens(org_id: str) -> MeterReading | None:
    """Extraction tokens consumed in the org's current window (#5331 unit 3).

    The figure is ``capture_tokens_in + capture_tokens_out``: the allowance is on
    LLM consumption for extraction, and one limit cannot name a side. A recorded
    ZERO is a reading (#5045: an extraction that reported no provider usage), so
    only ``measured is False`` — no ledger row, or a read/window failure — is NO
    READING.
    """
    from .metering import get_capture_usage

    usage = get_capture_usage(org_id)
    if not usage.get("measured"):
        reason = usage.get("unmeasured_reason")
        if reason != "absent":
            # ⛔ DEFAULT-DENY. Only the ONE reason that is CERTAIN (no ledger row
            # yet for a cumulative flow) may pass as a zero; every other reason —
            # including one this gate has never heard of, e.g. a future
            # "degraded"/"schema_error" — is an unmeasurable figure and refuses.
            # Folding an unknown reason into "absent" would silently disarm a
            # configured allowance the day `get_capture_usage` grows a reason.
            # ⚠️ OUR fault, not the tenant's: the ledger or the billing window
            # failed. The gate still REFUSES (#686 fail-closed), the refusal
            # names the fault, and it is logged at ERROR for the operator (the
            # durable #10776 surface is a follow-up) —
            # ``metering._current_period``'s own docstring records the opposing
            # ruling ("refusing a paying org over our own bookkeeping fault is
            # exactly the #3981 ruling forbids"), and this is the reconciliation:
            # refuse and name it, leaving the logging to `_reading` (which KNOWS
            # whether this path refuses or serves). Choosing "serve instead" needs a ruling
            # (a cap that serves past our fault is a cap that can be evaded during
            # an outage), so it is not taken here.
            raise MeterUnavailable(
                f"{_describe_fault(reason)} (capture usage could not be read), "
                f"not on the tenant's")
        return ABSENT
    return MeterReading(
        used=float(usage.get("capture_tokens_total") or 0),
        measured_at=None,
        estimated=False,
        note="tokens in + out, as reported by the extraction provider",
    )


def _dimension(name: str, limit_key: str,
               **kwargs: Any) -> AllowanceDimension:
    """Build an entry, resolving ``name`` against the DECLARED registry (#1178).

    Raises at IMPORT when the dimension is not declared — a second registry or a
    renamed dimension must be a loud failure, not a gate that stops closing. The
    ``unit`` is TAKEN from the declared row: restating it here is how the row and
    the refusal message drift into two units for one dimension (review round 2).
    A dimension whose declared unit is a phrase ("MB (graph on-disk size, with
    index size, spread and sampled min/max)", "tokens in / tokens out (plus
    calls…)") is reduced to its HEAD unit — the base the allowance is
    denominated in: the first word. Anything finer is a presentation detail the
    refusal does not need.
    """
    declared = _declared_dimension(name)  # raises KeyError when undeclared
    unit = str(declared.unit).split()[0]
    return AllowanceDimension(dimension=name, limit_key=limit_key, unit=unit,
                              **kwargs)


#: The allowance key per ruled dimension. Named for the dimension, one key each:
#: a shared key would make two independent dimensions charge the same figure,
#: which is the double-counting the #5331 model exists to avoid. ⚠️ The graph key
#: is NOT the unmerged ``max_storage_bytes`` name on ``feat/5331-node-to-byte-cap``
#: (R11 in ``docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md``):
#: that branch denominates the GRAPH meter's MB reading in bytes, which needs a
#: MiB-vs-MB factor the storage architecture records as a measured 3.77× error
#: class. The ruled decision (storage denominated in the bytes family, never in
#: nodes) is untouched by the key's name — no node count appears here.
ALLOWANCE_KEYS: dict[str, str] = {
    "graph_storage": "max_graph_storage_mb",
    "postgres_storage": "max_postgres_storage_bytes",
    "capture_extraction": "max_extraction_tokens",
}

#: Set by a caller that has ALREADY run :func:`carry_resolved_allowances` for
#: this limits dict, so a SECOND call — the graph-lane half of
#: ``hosted_api._check_org_limit_off_loop``, which runs the enforcement after
#: the fill was performed on the control-plane lane — pays no second resolver
#: read (#9567 review round 12).
#:
#: Why a marker and not "the keys are present": the resolver's LOOKUP MISS
#: (``OrgNotFoundError``) is the one carried state that adds NO key, and it is a
#: STATE, not a fault — so without this the graph lane would resolve the very
#: same missing record again, i.e. re-run the PostgREST read on the pool the
#: caller just moved it off. The key is deliberately private and inert: the gate
#: iterates ``DIMENSIONS`` by their declared ``limit_key``, and
#: ``enforce_org_limit`` validates presence per key, never the key SET — so an
#: extra key changes nothing it can observe.
CARRIED_MARKER_KEY = "_allowances_carried"

DIMENSIONS: tuple[AllowanceDimension, ...] = (
    _dimension(
        "graph_storage", ALLOWANCE_KEYS["graph_storage"],
        # ⛔ BLOCKED, and NOT because a meter is missing — because OUR OWN
        # instrument forbids wiring a cap to this figure yet. ``graph_storage``'s
        # module docstring states it outright: *"Until they are corrected, a cap
        # MUST NOT be wired to the ledger figure for vector-bearing tenants"* —
        # the HNSW index share is under-reported by 8.5×–21.7× (≈2× on the total)
        # and the ledger returns the old wording. A cap wired to it would not fire
        # for exactly the tenants who grow biggest (fail-open), and a refusal
        # built on it would present an unmeasurable figure as measured.
        meter=_graph_storage_mb,
        stale_after_seconds=STALE_AFTER_SECONDS,
        blocked_reason=(
            "the graph byte meter exists but its own contract forbids capping on "
            "the ledger figure: the vector-index share is under-reported 8.5x-21.7x "
            "(~2x on the total) and metering.get_graph_storage_reading returns the "
            "uncorrected numerics (tortoise/graph_storage.py: 'a cap MUST NOT be "
            "wired to the ledger figure for vector-bearing tenants'). BLOCKED on "
            "the corrected ledger figure"),
    ),
    _dimension(
        "postgres_storage", ALLOWANCE_KEYS["postgres_storage"],
        meter=None,
        blocked_reason=(
            "no per-org byte meter: ``postgres_storage`` measures at DATABASE "
            "grain (``measure_postgres_storage``; its cost-dimension "
            "``attribution`` states that a per-org byte split would be an "
            "ALLOCATION, not a measurement, because Postgres records no per-org "
            "byte ownership). BLOCKED until a per-org attribution exists — a "
            "gate that charged this org for the whole database's bytes would "
            "over-charge every tenant"),
    ),
    _dimension(
        "capture_extraction", ALLOWANCE_KEYS["capture_extraction"],
        meter=_extraction_tokens,
        # ``sessions`` is the capture lane's door (``_check_org_limit(org,
        # "sessions")``) and it is the ONLY operation that spends extraction
        # tokens. The write doors are deliberately NOT gated on this dimension:
        # a token allowance exhausted mid-window must not refuse a points write
        # that spends no tokens — a cap that blocks an unrelated fix is the
        # anti-pattern this module's door principle refuses to build.
        resources=frozenset({"sessions"}),
        # A window with no ledger row has captured NOTHING — a real zero for a
        # cumulative flow. Treating it as "no reading" would deadlock: the row is
        # MERGE-created by record_capture_usage, i.e. by the very capture the gate
        # would be refusing, so the window could never open (review round 3, P1).
        absent_is_zero=True,
    ),
)

#: Every resource ANY dimension gates on. Per-DIMENSION scope lives on
#: ``AllowanceDimension.resources`` (above): one global set could not express that
#: the token dimension must also cover the capture lane while a storage dimension
#: must not.
ALLOWANCE_GATED_RESOURCES = frozenset().union(
    *(d.resources for d in DIMENSIONS))


def carry_allowance_keys(target: dict, source: Mapping[str, Any],
                         keys: Iterable[str] | None = None) -> dict:
    """Carry every allowance key the SOURCE resolved onto ``target`` (#9567 D12).

    The gate reads the dict it is GIVEN, so a key a surface fails to carry is not
    a warning — it reads as UNLIMITED (see ``enforce_allowances``). Enumerating
    from :data:`ALLOWANCE_KEYS` rather than hand-listing is what makes a NEW
    declared dimension lift on every surface at once; a hand-list is how one
    surface keeps spending past a configured allowance while another refuses
    (#1349's silent-drop class).

    ``keys`` narrows the carry (default: every declared allowance key) so a
    caller filling only what is MISSING cannot overwrite a value the surface
    resolved itself.

    Only keys the source ACTUALLY resolved are carried: an absent key means
    unconfigured, which is the gate's own declared default — synthesizing one
    would invent a cap (#4051).
    """
    for key in (ALLOWANCE_KEYS.values() if keys is None else keys):
        if key in source:
            target[key] = source[key]
    return target


def carry_resolved_allowances(
        limits: dict | None, *, org_id: str | None = None,
        resolver: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict | None:
    """Fill any MISSING allowance key from the ONE resolver (#9567 D12).

    **Why this exists.** ``quota.resolve_org_limits`` is where an allowance value
    is emitted for the MCP lane, but REST does NOT consume that resolver: every
    REST lane hand-assembles its own limits dict from its own store read
    (``hosted_api._get_current_org`` registry, ``supabase_control.resolve_api_key``,
    ``_membership_org``, ``_org_limits_from_node``), and none of them reads an
    allowance column. So an arming edit made in the resolver alone would REFUSE on
    MCP and SERVE WITHOUT LIMIT on REST — the fail-open this closes, and the exact
    class the MCP bridge's carry was added for one surface at a time. Resolving at
    the seam instead of mirroring the carry into each builder is the same rule
    #3874 applied to ``max_api_keys``: *"there is ONE resolver, so the two cannot
    drift"*.

    Only MISSING keys are filled, so a surface that already resolved them (the
    MCP lane carries from ``resolve_org_limits`` per request) pays no read and is
    never overridden.

    **A lookup MISS is not a fault** (#9567 review round 11). The three outcomes
    of the read are DIFFERENT states and must not collapse into one:

    * the resolver has **no record** for this org
      (``quota.OrgNotFoundError``) → the dict is returned UNCHANGED. A cap
      cannot be armed from a record that does not exist, so the caller's own already-authoritative
      dict stands and the dimension stays at the gate's declared default (an
      absent key is unconfigured = unlimited, ``#4051``). Refusing here refused a
      tenant whose absence is a well-defined state — a 500 on every gated door
      with nothing armed, which is the over-block the module's ``#3981`` note
      rules out for OUR bookkeeping, and it gave the SAME absence two answers
      (unlimited on doors that skip this fill).
    * the read **faults** → the documented ``QuotaCheckError`` (fail closed,
      ``#686``/``#4051``), never served: a cap that serves past a config read we
      could not complete is a cap that can be evaded during an outage.
    * it resolves → fill only the missing keys.
    """
    if limits is None:
        return None
    if limits.get(CARRIED_MARKER_KEY):
        # Already carried by the caller (an off-loop fill that then hands the
        # dict to a second lane — see CARRIED_MARKER_KEY). Returning UNCHANGED
        # is the same answer the all-keys-present arm below gives: the caller's
        # resolution stands and no resolver read is repeated.
        return limits
    org_id = org_id or limits.get("org_id")
    if not org_id:
        return limits  # stdio/operator — no org context, nothing to resolve
    if all(key in limits for key in ALLOWANCE_KEYS.values()):
        return limits  # the caller's own resolution already carried all three
    missing = [key for key in ALLOWANCE_KEYS.values() if key not in limits]
    if resolver is None:
        # Local import: ``quota`` imports this module inside ``enforce_org_limit``
        # (a function), so the cycle is closed at module level here on purpose —
        # same shape as this module's existing ``from .quota import …`` above.
        from .quota import resolve_org_limits as resolver
    try:
        resolved = resolver(org_id)
    except OrgNotFoundError as e:
        # A STATE, not a fault: no record ⇒ no allowance to carry. Documented
        # and observable (WARNING), never silent, never a refusal.
        _logger.warning(
            "no org record for the allowance lookup (leaving the caller's "
            "allowances as given): team=%s error=%r", org_id, e)
        return limits
    except QuotaCheckError:
        raise
    except Exception as e:  # mapped below, never an unmapped 500
        _logger.error(
            "team allowances unresolvable (fail-closed): team=%s error=%r",
            org_id, e)
        raise QuotaCheckError(
            f"the team allowances could not be resolved for {org_id!r} "
            f"({type(e).__name__}: {e}) — refusing to read an uncarried "
            f"allowance as unlimited") from e
    return carry_allowance_keys(dict(limits), resolved or {}, keys=missing)


def _limit(value: Any, dim: AllowanceDimension, org_id: str) -> int | None:
    """Read one configured allowance. Unreadable is REFUSED, never 'unlimited'.

    ``None`` (an explicit null) is unlimited by the same rule
    ``enforce_org_limit`` documents; an UNREADABLE value is the #4051 class and
    raises ``QuotaCheckError`` naming the field.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        _logger.error(
            "allowance limit unreadable (fail-closed): team=%s dimension=%s "
            "field=%s value=%r", org_id, dim.dimension, dim.limit_key, value)
        raise QuotaCheckError(
            f"team limits {dim.limit_key} unreadable for dimension "
            f"{dim.dimension!r} ({value!r})") from None


def _stale(reading: MeterReading, dim: AllowanceDimension) -> bool:
    """True when a reading is older than the dimension's stated bound."""
    if dim.stale_after_seconds is None:
        return False  # the dimension declares no age bound
    if not reading.measured_at:
        # ``None`` means the instrument has no age — which cannot prove
        # freshness, so on a BOUNDED dimension it is treated as stale. Silently
        # reading it as fresh would make the stated bound unenforceable for
        # exactly the readings that cannot be aged (review round 1, P2).
        return True
    try:
        measured = datetime.fromisoformat(str(reading.measured_at))
    except (TypeError, ValueError):
        # An unparseable timestamp cannot prove freshness → treat as stale.
        return True
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=UTC)
    return (datetime.now(UTC) - measured).total_seconds() > dim.stale_after_seconds


def _reading(dim: AllowanceDimension, org_id: str,
             meters: Mapping[str, Callable[[str], MeterReading | None]] | None,
             *, observed_only: bool) -> MeterReading | None:
    """Call one dimension's meter. ``observed_only`` swallows every fault.

    ``observed_only`` is the UNLIMITED path (a key present with ``None``): the
    dimension must still be METERED, but there is no allowance to enforce, so a
    broken meter is LOGGED and cannot refuse a write nobody capped.
    """
    if dim.blocked and not observed_only:
        # A dimension whose meter our own contract forbids capping on is BLOCKED:
        # configuring it REFUSES, and no reading is taken (a `meters=` override
        # cannot un-block it — that is the point of the declaration).
        _logger.error(
            "allowance dimension BLOCKED (fail-closed): team=%s dimension=%s",
            org_id, dim.dimension)
        raise QuotaCheckError(
            f"dimension {dim.dimension!r} has an allowance configured for team "
            f"{org_id!r} but is {BLOCKED.upper()} — {dim.blocked_reason}")
    meter = (meters or {}).get(dim.dimension, dim.meter)
    if meter is None:
        if observed_only:
            return None
        # ⛔ A missing meter is NOT a blocked dimension (#9567 review round 12,
        # P2). The two states are different and a fail-closed refusal must not
        # misdescribe its own, so THIS branch names only the state it is in —
        # "no meter plumbed" — and never a ruling: the BLOCKED message used to
        # be reused here with ``dim.blocked_reason`` None, inventing a ruling
        # that does not exist.
        #
        # ⛔ The BLOCKED state needs no arm HERE: the guard at the top of this
        # function already raises for every ``dim.blocked`` that carries an
        # allowance, so this point is reached only when ``observed_only`` is
        # NOT set (that mode returns on the line above) AND ``dim.blocked`` is
        # False. A ``if dim.blocked:`` re-check was
        # present and could never run (main-repair, 2026-11-04: probed with a
        # blocked dimension whose meter is suppressed through the ``meters=``
        # seam — the TOP guard fires, line-for-line, and the inner arm never
        # does). Removed rather than left as dead code, because an unreachable
        # arm with a reachability rationale invites a reader to trust a path
        # that does not exist.
        _logger.error(
            "allowance dimension has no meter (fail-closed): team=%s "
            "dimension=%s", org_id, dim.dimension)
        raise QuotaCheckError(
            f"dimension {dim.dimension!r} has an allowance configured for team "
            f"{org_id!r} but has no meter plumbed — nothing can measure "
            f"{dim.limit_key}, so the allowance cannot be enforced")
    try:
        reading = meter(org_id)
    except Exception as e:
        if observed_only:
            _logger.warning(
                "allowance meter failed (dimension unlimited, no refusal): "
                "team=%s dimension=%s error=%r", org_id, dim.dimension, e)
            return None
        _logger.error(
            "allowance meter failed (fail-closed, tenant refused): team=%s "
            "dimension=%s error=%r", org_id, dim.dimension, e)
        raise QuotaCheckError(
            f"{dim.dimension} usage could not be measured for team {org_id!r}: "
            f"{e!r}") from e
    if reading is not None and reading is not ABSENT \
            and not isinstance(reading, MeterReading):
        # ONE type gate, here, instead of validating each field where it is read
        # (`.used`, `.measured_at`, `.note`, `.estimated`): a meter returning
        # anything else — a bare number, a SimpleNamespace missing a field —
        # would otherwise escape as a bare AttributeError, i.e. an unmapped 500
        # (review rounds 6-7).
        if observed_only:
            _logger.warning(
                "allowance meter returned %s, not a reading (dimension "
                "unlimited, no refusal): team=%s dimension=%s",
                type(reading).__name__, org_id, dim.dimension)
            reading = None
        else:
            _logger.error(
                "allowance meter returned a non-reading (fail-closed): "
                "team=%s dimension=%s type=%s", org_id, dim.dimension,
                type(reading).__name__)
            raise QuotaCheckError(
                f"{dim.dimension} usage reading for team {org_id!r} is not a "
                f"reading ({type(reading).__name__}) — refusing to enforce "
                f"{dim.limit_key} from it")
    if reading is ABSENT and dim.absent_is_zero:
        # The per-dimension FLOW semantics: for a cumulative flow, "no ledger row
        # for this window" is a real ZERO (nobody has done it yet) — not an
        # unmeasurable figure. Declared on the dimension, consumed HERE, so a
        # future flow dimension gets the behaviour from its declaration instead of
        # from a meter-specific branch (and a GAUGE that declares nothing stays
        # strict, which is what keeps a missing gauge a refusal).
        return MeterReading(used=0.0, measured_at=None, estimated=False,
                            note="no usage metered in this window")
    if reading is ABSENT:
        # Absence with NO declared flow semantics is still no reading.
        reading = None
    if reading is None:
        if observed_only:
            return None
        _logger.error(
            "allowance unmeasurable (fail-closed): team=%s dimension=%s "
            "reason=no_reading", org_id, dim.dimension)
        raise QuotaCheckError(
            f"{dim.dimension} usage has no reading for team {org_id!r} and an "
            f"allowance of {dim.limit_key} is configured — refusing to fail "
            f"open (#686)")
    if _stale(reading, dim):
        if observed_only:
            return reading
        _logger.error(
            "allowance reading stale (fail-closed): team=%s dimension=%s "
            "measured_at=%s bound=%ss", org_id, dim.dimension,
            reading.measured_at, dim.stale_after_seconds)
        raise QuotaCheckError(
            f"{dim.dimension} reading for team {org_id!r} is older than "
            f"{dim.stale_after_seconds}s (measured_at={reading.measured_at!r}) "
            f"— refusing to enforce {dim.limit_key} from a stale figure")
    return reading


def enforce_allowances(
        limits: dict | None, *, org_id: str | None = None, resource: str = "points",
        meters: Mapping[str, Callable[[str], MeterReading | None]] | None = None,
) -> None:
    """Apply every CONFIGURED dimension allowance. Unconfigured ones are inert.

    Args:
        limits: the SAME resolved limits dict ``quota.enforce_org_limit``
            receives. ``None`` or no ``org_id`` → skip (stdio/operator).
        org_id: resolved org, defaulting to ``limits["org_id"]``.
        resource: the quota resource being gated; only
            :data:`ALLOWANCE_GATED_RESOURCES` are checked (a key mint cannot move
            a storage meter).
        meters: optional per-dimension meter overrides, keyed by DECLARED
            dimension name. The production path passes nothing and uses the
            instruments; D11/D12's "a reading supplied" is this seam.

    Raises:
        QuotaExceededError: at/over a configured allowance (names the dimension).
        QuotaCheckError: configured but unmeasurable, stale, blocked or with an
            unreadable limit value (fail closed).
    """
    if limits is None:
        return
    org_id = org_id or limits.get("org_id")
    if not org_id:
        return
    if resource not in ALLOWANCE_GATED_RESOURCES:
        return
    for dim in DIMENSIONS:
        if resource not in dim.resources:
            continue  # this door cannot move THIS dimension's meter
        if dim.limit_key not in limits:
            continue  # ABSENT = unlimited. No invented cap, and the dimension
            #           keeps being metered by its own writer.
        allowance = _limit(limits.get(dim.limit_key), dim, org_id)
        if allowance is None:
            _reading(dim, org_id, meters, observed_only=True)
            continue
        reading = _reading(dim, org_id, meters, observed_only=False)
        if reading is None:  # _reading refuses, so this is belt-and-braces
            raise QuotaCheckError(
                f"{dim.dimension} usage has no reading for team {org_id!r}")
        # #3665's trap, in the other direction: ``nan >= allowance`` is False, so
        # a NaN reading would SILENTLY PASS (a disarmed allowance), and ``inf``
        # would raise a bare OverflowError out of ``int(used)`` below — an
        # unmapped 500 this module promises never to produce (#4051). And a
        # reading that is not number-LIKE at all (``None``, ``"five"``, an
        # arbitrary object) would raise a bare TypeError at the comparison. So
        # the figure is COERCED once, here, and every shape that does not coerce
        # to a FINITE float is REFUSED.
        #
        # ⚠️ Coercion, not a type gate — and the honest statement matters more
        # than a tidier one (#9567 review round 12, P2). ``float()`` accepts a
        # NUMERIC STRING: ``MeterReading(used="5")`` is read as ``5.0``, and
        # ``used="1e9"`` is refused as OVER the cap, not as non-numeric. That is
        # sound for what this refusal exists to stop (never a silently-passing
        # or uncomparable figure — the two shipped meters both hand back a
        # float), and a stricter ``isinstance(used, (int, float))`` gate would
        # refuse a ``Decimal`` meter for its TYPE rather than its value. What is
        # REFUSED is exactly: a value that raises out of ``float()``, and the
        # non-finite ones NaN / +/-inf.
        raw_used = getattr(reading, "used", None)
        try:
            used = float(raw_used)
        except (TypeError, ValueError):
            _logger.error(
                "allowance reading non-numeric (fail-closed): team=%s "
                "dimension=%s used=%r", org_id, dim.dimension, raw_used)
            raise QuotaCheckError(
                f"{dim.dimension} usage reading for team {org_id!r} is not a "
                f"number ({raw_used!r}) — refusing to enforce "
                f"{dim.limit_key} from it") from None
        if not math.isfinite(used):
            _logger.error(
                "allowance reading non-finite (fail-closed): team=%s "
                "dimension=%s used=%r", org_id, dim.dimension, used)
            raise QuotaCheckError(
                f"{dim.dimension} usage reading for team {org_id!r} is not a "
                f"finite number ({used!r}) — refusing to enforce "
                f"{dim.limit_key} from it")
        if used >= allowance:
            note = f" ({reading.note})" if reading.note else ""
            _logger.warning(
                "allowance exceeded: team=%s dimension=%s used=%s limit=%s",
                org_id, dim.dimension, used, allowance)
            raise QuotaExceededError(
                with_limit_contact(
                    f"Team {dim.dimension} allowance reached "
                    f"({allowance:g} {dim.unit}, used {used:g}). Upgrade your "
                    f"plan to increase it.{note}"),
                resource=dim.dimension, used=int(used), limit=int(allowance),
                # Omitted (None) when the figure is exact, so `quota.py`'s
                # documented payload shape holds: only an ESTIMATE is labelled.
                estimated=True if reading.estimated else None,
            )
