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
owner's, and since ``#13084`` **one dimension is ARMED on every live path**:
``graph_storage``, at the tier's hot-window number, resolved from the artifact
(``product/pricing.json``) by ``quota.resolve_org_limits`` — solo 77 MB, pro
102 MB, team 615 MB. The other two dimensions remain UNCONFIGURED
(``postgres_storage`` is BLOCKED on a per-org byte meter; ``capture_extraction``
has no resolved allowance), and ``free``/``anon`` are **deliberately unarmed**
— a RECORDED DEPARTURE from the artifact, which carries ``0`` for them **by
decision** (``billing.storage._included_amounts_status``: *"Free/anon are the
exception: hot capacity 0 is **stated by the decision record**"*;
``docs/architecture/SEARCH-ARCHITECTURE.md`` §8 tabulates their window target as
``0``). A ``0`` cannot be armed as a cap: the gate refuses at
``used >= allowance``, so it would refuse the FIRST byte on a tier that still
grants a graph — hence ``zero_means_unarmed`` (only an exact ``0`` is the
unarmed sentinel; a negative is a fault, not an unset amount). An unarmed
dimension refuses nothing and keeps being metered — the ordering the storage
architecture requires (*"the meter must be wired into the gate before any
allowance is configured"*) — ``docs/architecture/STORAGE-ARCHITECTURE.md``,
§"The byte path".

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

**Four outcomes per configured dimension, and only four:**

* **under the allowance** → allowed (the write proceeds);
* **at/over the allowance** → ``QuotaExceededError``, naming the dimension, with
  ``used``/``limit`` in the dimension's own unit and the meter's honesty attached
  (a sampling estimate must not be presented as exact);
* **at an estimate's cap but not past its hard stop** → WARN, and ALLOW the
  write. A dimension may declare a :class:`CapBand` wider than ``(1.0, 1.0)``,
  which is what ``graph_storage`` does (warn at 1.0x the cap, refuse at 1.5x when
  the dispersion is unmeasured, or from the observed minimum once the reading
  carries several repeats): an estimate must never be the sole cause of a hard
  refusal, so it warns at the cap and refuses only once the reading is past the
  point where sampling noise could explain the gap. Every
  exact-reading dimension keeps ``(1.0, 1.0)``, i.e. its behaviour is unchanged;
* **configured, but the meter yields NO READING** (never measured, measurement
  error, or a reading older than the dimension's stated bound) → **depends on the
  dimension's declaration, never on the accident of who wrote the reader**:

  - **default-deny** (``unmeasured_is_unarmed`` NOT declared) →
    ``QuotaCheckError``. Never a silent pass — the #686 fail-closed posture, and
    never a bare exception escaping as an unmapped 500 (the #4051 lesson).
  - **unarmed-but-loud** (declared, and ``graph_storage`` is the one dimension
    that does) → **the write is SERVED and the cap does not bind**, with an
    operator signal (a throttled ERROR under
    :data:`UNMEASURED_UNARMED_WARNING`, a counter, and a durable
    ``ALLOWANCE_CAP_UNENFORCEABLE`` incident for the reasons that mean OUR
    plumbing failed). ⛔ This is not a widened fail-open: it is the resolution of
    #13102 P0-1, where an armed cap whose only writer is a DEFAULT-OFF scheduled
    sweep refused every gated write of a brand-new paid tenant (a 500 on the
    onboarding demo's first point) and every tenant the sweep had stopped
    reaching. The row is created by that sweep, so refusing the write can never
    make it appear — a deadlock, and ``#3981``'s owner ruling (PROCEED AND ALERT,
    no new user-facing refusal over our own bookkeeping state) forbids it. The
    cost is stated where it is declared: **under-enforcement**, the safe
    direction, until the gauge reports (``#9559`` wires it).

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

import contextlib
import logging
import math
import time
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
    "AT_CAP_WARNING",
    "BLOCKED",
    "DIMENSIONS",
    "HARD_STOP_MULTIPLE",
    "RELEASE_CHECKED",
    "RELEASE_CONDITIONS",
    "UNMEASURED_UNARMED_WARNING",
    "AllowanceDimension",
    "CapBand",
    "MeterReading",
    "carry_allowance_keys",
    "carry_resolved_allowances",
    "enforce_allowances",
    "reset_signal_state_for_tests",
    "unarmed_served_counts",
]

#: The stated bound on how old a GAUGE reading may be and still be enforced.
#: It is an EVIDENCE-AGE bound, not an allowance: a stale gauge describes a graph
#: that no longer exists, so enforcing it would decide from a number we cannot
#: defend. 24 h is chosen because the graph meter is a periodic estimate, not a
#: per-write figure — and it is overridable by the caller passing its own
#: ``meters``/by configuring the dimension, never by an implicit default cap.
STALE_AFTER_SECONDS = 24 * 60 * 60

#: The MINIMUM DEADBAND an ESTIMATED reading is held to (#13084): the gate WARNS
#: at the cap and hard-refuses only at this multiple of it. Declared once here so
#: the declaration and this module's own docstring cannot disagree; a dimension
#: with an EXACT reading keeps ``1.0`` (see :class:`CapBand`).
#:
#: ⛔ IT IS A POLICY MARGIN, NOT A MEASURED ERROR (#13102 P1-2). An earlier
#: version of this comment justified 1.5 with "the graph meter's own reported
#: spread is a few percent" — a figure **this gate never read**: the ledger
#: carries ``graph_storage_spread_mb``/``graph_storage_repeats``, but the gate did
#: not consume them, and the production writer calls the projection WITHOUT
#: repeats, so the recorded spread is ``0.0`` by construction on a real tenant.
#: So 1.5 is what we DECLARE for the case the gate cannot measure — a reading half
#: again past the cap is a magnitude a sampling wobble should not explain — and
#: where the ledger DOES carry measured repeats (``repeats >= 2``) the stop is
#: derived from the reading's own observed minimum instead: :func:`_hard_stop` is
#: the only place the two are combined, and the refusal always says which one it
#: rests on.
HARD_STOP_MULTIPLE = 1.5

#: The phrase the at-cap WARNING carries, so a test pins the SIGNAL rather than
#: the wording of a sentence that will be reworded.
AT_CAP_WARNING = "allowance at cap"

#: The phrase the UNMEASURED-BUT-SERVED signal carries (#13102 P0-1), for the same
#: reason as ``AT_CAP_WARNING``: an operator greps for ONE string, and a reworded
#: sentence must not silently retire the signal.
UNMEASURED_UNARMED_WARNING = "allowance unmeasured (cap unarmed, write served)"

#: How often the same ``(org, dimension)`` may emit the at-cap or unmeasured
#: signal. Both sit on the hot write path: the band is entered by EVERY gated
#: write while a tenant sits between its cap and its hard stop, so an unthrottled
#: WARN is one log line per point write (#13102 P2). The interval is far shorter
#: than any operator's attention span and far longer than a burst.
SIGNAL_INTERVAL_SECONDS = 60 * 60

#: A configured dimension whose meter does NOT exist is BLOCKED, never faked.
BLOCKED = "blocked"

#: ── A BLOCK IS A CLAIM ABOUT THE TREE, SO IT CARRIES ITS OWN RELEASE ─────────
#: Every BLOCKED dimension declares the conditions that would LIFT the block, as
#: machine-readable tokens (:data:`RELEASE_CONDITIONS` names each one). The point
#: is that a block stops being an unrevalidated past decision:
#:
#: * a condition whose EVIDENCE is satisfied must not still be cited as the
#:   obstacle — `tests/test_9559_blocked_release_conditions.py` evaluates every
#:   token against the artifact it names and REDs when a satisfied condition is
#:   quoted in a live ``blocked_reason``;
#: * a block whose conditions have ALL fired REDs with "lift it" rather than
#:   holding a dimension blocked forever on grounds that no longer exist.
#:
#: Why this exists: ``postgres_storage``'s block cited *"no per-org byte meter …
#: BLOCKED until a per-org attribution exists"* and kept saying so after #12762
#: landed that attribution — the ONE registry (:mod:`cost_dimensions`) declares
#: it ``MEASURED`` with an explicit ``OVERRIDES:`` of the very sentence the block
#: repeated. Nothing re-read the block, so five separate lanes re-derived the
#: same falsified premise. A block is a claim: this makes it a CHECKED claim.
#:
#: Tokens are declared here as strings rather than as predicates so production
#: pays nothing for them: the predicates live in the test that revalidates them
#: on every PR, and an UNKNOWN token (a typo, a renamed condition) is a hard
#: failure there rather than a condition that silently never fires.
RELEASE_CONDITIONS: dict[str, str] = {
    "per_org_postgres_attribution": (
        "a per-org Postgres BYTE attribution exists that is a measurement and "
        "not an allocation (evidence: ``postgres_bytes.PerOrgPostgresBytes.for_org`` "
        "+ the ``measured`` attribution in ``cost_dimensions``, #12762)"),
    "postgres_residency_gauge": (
        "a per-org RESIDENCY gauge this gate can afford on a WRITE PATH exists "
        "— neither shipped reader is one: the audit is a whole-table "
        "aggregation with a per-row TOAST lookup plus a second pass over the "
        "TOAST relation, and the ledger reader is the window ADDEND, not the "
        "stored footprint"),
    "storage_included_amounts_confirmed": (
        "the free/anon included storage amounts are OWNER-CONFIRMED rather than "
        "the derived defaults of record (``product/pricing.json`` "
        "``_included_amounts_status``)"),
    "vector_index_measurable": (
        "the HNSW vector index is measurable, so a cap MAY be wired to the graph "
        "reading (evidence: ``graph_storage.PRECISION_NOTE`` no longer forbids "
        "it)"),
}


class _Absent:
    """The meter's answer for "there is no row YET" — distinct from ``None``.

    ``None`` means the meter could not produce a figure (a failure); this is the
    reader's CERTAIN "nothing has been recorded yet" — a successful read that
    found no row, which is not the same claim as "the figure is zero". The two
    must not share a value: flattening them is either a fail-open (a broken
    meter read as zero) or a deadlock (a fresh window refused forever).

    ⛔ WHAT IT MEANS IS THE DIMENSION'S TO DECLARE, and the two declarations are
    different claims (#13102 F3): for a cumulative FLOW it is a certain zero
    (nobody has started the window), while for a GAUGE whose row is written by a
    separate, scheduled job it is a deliberate UNDER-enforcement — the tenant is
    served while its true residency is unknown, because a gauge cannot promise
    that an absent row means zero bytes (`graph_storage`, whose declaration says
    so in as many words). Only a dimension that declares ``absent_is_zero`` may
    consume this as a zero; every other dimension still refuses on it.
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

    ``low``/``spread``/``repeats`` are the estimate's own DISPERSION, when the
    instrument measured one (#13102 P1-2). All three are ``None`` when it is **not
    a measurement** — a single repeat has no dispersion to report, so a ``0.0``
    recorded by a one-repeat writer is absence, not precision. ``low`` is the
    OBSERVED MINIMUM and it is what the widened refusal rests on: ``used`` is the
    MEDIAN of the repeats, not their midpoint, so the observed minimum is NOT
    ``used - spread / 2`` and the gate must not compute it that way (measured
    counterexample: repeats ``[50, 200, 201]`` give used 200 / spread 151, where
    the half-spread would say 124.5 while the observed low end is 50).
    """

    used: float
    measured_at: str | None = None
    estimated: bool = False
    note: str | None = None
    spread: float | None = None
    repeats: int | None = None
    low: float | None = None


@dataclass(frozen=True)
class CapBand:
    """WHERE an estimated reading may hard-refuse — the deadband (#13084).

    ``warn_at``/``refuse_at`` are MULTIPLES of the dimension's allowance, not
    absolute figures, so one declaration serves every tier. The default
    ``(1.0, 1.0)`` reproduces the pre-existing rule exactly (``used >= limit``
    refuses, nothing warns) for every dimension whose reading is EXACT — this
    band only changes behaviour where a dimension opts into a wider one.

    A dimension widens it when its reading is an ESTIMATE: an estimate must not
    be the sole cause of a hard refusal, so it warns at the cap and refuses only
    once the reading is far enough past it that sampling noise cannot explain
    the gap. The dimension that does this states its own numbers and the
    measurement behind them (see ``graph_storage`` below).
    """

    warn_at: float = 1.0
    refuse_at: float = 1.0


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
    #: dimension KIND, and getting it wrong in EITHER direction is a defect:
    #: a cumulative FLOW for a window that has just begun is zero-by-construction
    #: (nobody has captured), while a GAUGE's absence means nobody has MEASURED.
    #: Both must be served, for the same reason — the row is created by a
    #: scheduled writer, never by the operation being refused, so refusing can
    #: never make the row appear. A GAUGE that declares nothing stays strict
    #: (refuse), which is what keeps a missing reading a refusal for every
    #: dimension whose writer is reliably running.
    absent_is_zero: bool = False
    #: Where an ESTIMATED reading may hard-refuse (see :class:`CapBand`).
    cap_band: CapBand = CapBand()
    #: Whether NO READING (or one older than the bound) leaves the dimension
    #: UNARMED-BUT-LOUD instead of refusing the write (#13102 P0-1).
    #:
    #: ⛔ DEFAULT-DENY, and deliberately so: a dimension that does not declare
    #: this keeps the fail-closed posture (#686) — no reading is a refusal. Only a
    #: dimension whose figure is a GAUGE written by a SCHEDULED job may declare it,
    #: because there the absence is OURS, not the tenant's: the row is created by a
    #: sweep, so refusing the write can never make the row appear, and the
    #: customer-facing refusal would be a permanent 500 on every gated door for
    #: every tenant the sweep has not reached (#3981's owner ruling: PROCEED AND
    #: ALERT, no new user-facing refusal over our own bookkeeping state).
    #:
    #: What it costs is stated where it is declared: the cap does not bind until
    #: the gauge reports — under-enforcement, the safe direction — and the signal
    #: (:data:`UNMEASURED_UNARMED_WARNING` + the counter + a durable operator
    #: incident for the states that mean our plumbing failed) is what keeps it
    #: from being silent.
    unmeasured_is_unarmed: bool = False
    #: Whether a non-positive allowance means UNARMED rather than "a cap of
    #: zero". Only a dimension whose artifact records an UNSET amount as ``0``
    #: may declare this — ``used >= 0`` is true at every reading, so arming a
    #: zero would refuse the first byte on a tier that still allows the resource.
    zero_means_unarmed: bool = False
    #: The conditions that would LIFT :attr:`blocked_reason`, as tokens into
    #: :data:`RELEASE_CONDITIONS`. Required (non-empty) on every BLOCKED
    #: dimension and rejected on an unblocked one: a block that cannot name what
    #: would end it is an opinion, and one that lists conditions it no longer
    #: waits on is the staleness this field exists to expose. Revalidated by
    #: ``tests/test_9559_blocked_release_conditions.py``, not by production.
    release: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        """True when this dimension MUST NOT be enforced, and why is recorded.

        ``blocked_reason`` is the whole state: a dimension can carry a working
        meter and still be BLOCKED — ``postgres_storage`` is (no per-org byte
        meter exists, and a per-org split would be an ALLOCATION, not a
        measurement). ``graph_storage`` was in this state until #13084 and is
        now ARMED at the tier number, with the estimate's one-directional error
        handled by its ``cap_band`` rather than by a block. The reason is what
        the refusal shows, so it cannot be a note beside the state.
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

    ⛔ ``reason`` IS A MESSAGE-FREE TOKEN (#13102 P0-1), because the operator
    incident's body is durable and create-once: a free-text sentence would freeze
    one call site's wording as the permanent description of the incident, and the
    operator triages on the token. It is the reader's own vocabulary
    (``unmeasured_reason``) where the reader has one, and a gate-local token for
    the states the reader cannot name (a figure that is present but unusable, a
    meter that raised, a non-reading return).
    """

    def __init__(self, message: str, *, reason: str = "meter_error") -> None:
        super().__init__(message)
        self.reason = reason


#: The reasons ``metering.get_graph_storage_reading`` names for a row that exists
#: but is unusable. They pass through to the operator incident VERBATIM so the
#: durable body says which fault it was; anything else is reported as
#: ``unknown_reason`` (never as ``absent``, and never as free text — the incident
#: body is create-once and durable).
_READER_FAULT_REASONS = frozenset({"unreported", "read_error", "period_error"})


def _graph_storage_mb(org_id: str) -> MeterReading | None:
    """The org's last graph-storage GAUGE, in MB (#5331 unit 2).

    ``get_graph_storage_reading`` never raises: a degraded read is
    indistinguishable from "never measured" in its TIMESTAMP, and the gate must
    not treat them alike. Since #13084 is a CAP INPUT, the reader also publishes
    ``unmeasured_reason`` and this meter consumes it with DEFAULT-DENY — the same
    rule ``_extraction_tokens`` applies to its own reader (#13102):

    * ``"absent"`` — the read SUCCEEDED and NO row exists for the window — is
      the meter's ``ABSENT``, the one state a dimension may consume as a fact
      rather than refuse (``graph_storage`` declares ``absent_is_zero``).
    * **every other reason** — ``"unreported"`` (a row with no age), —
      ``"read_error"``, ``"period_error"``, and any value this gate has never
      seen — is NO READING. Whether that REFUSES is the DIMENSION's declaration
      (``unmeasured_is_unarmed``): this meter never decides it, it only reports
      the state truly. Folding an unknown reason into "absent" would disarm a
      configured allowance the day the reader grows a reason.

    ⛔ A FIGURE IS REQUIRED, AND IT MUST BE FINITE (#13102 P2). The old line read
    ``float(reading.get("graph_storage_mb") or 0.0)``, so a row that carried a
    valid timestamp and a NULL figure became ``0 MB`` — the exact fail-open
    ``absent_is_zero=False`` exists to stop, one field narrower. A null, a
    non-numeric and a NaN/inf figure are all NO READING here.

    ⛔ THE SPREAD IS CARRIED, AND ONLY WHEN IT IS MEASURED (#13102 P1-2). The
    ledger records ``graph_storage_spread_mb`` and ``graph_storage_repeats``, but
    a ONE-repeat write records ``0.0`` — which is not a precision claim, it is
    the absence of one. So ``spread`` is published only when ``repeats >= 2``.
    """
    from .metering import get_graph_storage_reading

    reading = get_graph_storage_reading(org_id)
    measured_at = reading.get("graph_storage_measured_at")
    reason = reading.get("unmeasured_reason")
    if not measured_at:
        # NO AGE = NO READING, unless the reader NAMES the absence itself (the
        # one state that is an ANSWER rather than a fault — see this function's
        # docstring). A present row without an age cannot prove freshness, and a
        # 0 MB figure would compare as "under the cap" and pass silently.
        if reason == "absent":
            return ABSENT
        # ⛔ THE REASON IS CARRIED OUT, not folded into a bare ``None`` (#13102
        # P0-1): the operator incident names the CAUSE, so the gate's counter and
        # its durable body can distinguish a store fault from an unageable row
        # from a reason this gate has never seen — and DEFAULT-DENY stays the
        # rule for the token itself (an unrecognised reason is reported as
        # ``unknown_reason`` and is never read as ``absent``).
        raise MeterUnavailable(
            f"graph storage has no usable reading (reason={reason!r})",
            reason=str(reason) if reason in _READER_FAULT_REASONS
            else "unknown_reason")
    figure = reading.get("graph_storage_mb")
    try:
        used = float(figure)
    except (TypeError, ValueError):
        # A timestamp with no figure is not a reading of zero bytes.
        raise MeterUnavailable(
            f"graph storage reading carries no usable figure ({figure!r})",
            reason="missing_figure") from None
    if not math.isfinite(used):
        raise MeterUnavailable(
            f"graph storage reading is not finite ({used!r})",
            reason="missing_figure")
    return MeterReading(
        used=used,
        measured_at=str(measured_at),
        estimated=True,
        spread=_measured_spread(reading, used),
        repeats=_repeat_count(reading),
        low=_measured_low(reading, used),
        # The note travels into the refusal, so it names the direction of the
        # error as well as its size — and, since #13102 P1-3, it also says out
        # loud that the direction is a LOWER BOUND only UP TO the sampling error,
        # which is the part the earlier wording claimed away: systematic
        # exclusions all remove bytes, but a SAMPLING estimate wobbles both ways
        # (``SEARCH-ARCHITECTURE.md`` §8 says so of this same instrument). It also
        # says what was excluded, because a reader who sees only the number
        # cannot otherwise know the largest resident component was invisible.
        note=("measured with GRAPH.MEMORY USAGE, a SAMPLES-based ESTIMATE that "
              "EXCLUDES per-graph/Redis overhead AND the HNSW vector index "
              "(~2x low on the total, 8.5x-21.7x low on the index share), so "
              "the real figure is AT LEAST this one UP TO this estimate's "
              "sampling error (which is UNMEASURED when the reading comes from "
              "a single sample); it is not invoice-grade"),
    )


def _repeat_count(reading: Mapping[str, Any]) -> int | None:
    """How many repeats produced the recorded spread, or ``None`` if unknown."""
    try:
        repeats = int(reading.get("graph_storage_repeats") or 0)
    except (TypeError, ValueError):
        return None
    return repeats if repeats > 0 else None


def _dispersion(reading: Mapping[str, Any], used: float
                ) -> tuple[float | None, float | None]:
    """``(spread, low)`` — the reading's measured range, or ``(None, None)``.

    ⛔ ONE SOURCE, VALIDATED ONCE (#13102 verification pass). ``low`` and
    ``spread`` used to be two independent readers, so a row with a usable minimum
    and an unusable spread could yield a reading whose minimum the gate acted on
    but whose spread the refusal could not render — a formatter crash on the
    refusal path. The pair now comes from here or not at all, and every value is
    finite, non-negative, and consistent with the figure (``low <= used``).
    """
    if (_repeat_count(reading) or 0) < 2:
        return None, None
    try:
        spread = float(reading.get("graph_storage_spread_mb"))
        low = float(reading.get("graph_storage_min_mb"))
    except (TypeError, ValueError):
        return None, None
    if not all(math.isfinite(v) for v in (spread, low)):
        return None, None
    if spread < 0 or low < 0 or low > used:
        return None, None
    return spread, low


def _measured_low(reading: Mapping[str, Any], used: float) -> float | None:
    """The OBSERVED MINIMUM of the reading, or ``None`` when it is unmeasured.

    Delegates to :func:`_dispersion` (one validation for the pair), so a reading
    whose SPREAD is unusable cannot still act on a minimum: the gate refuses from
    ONE measured range or from none.
    """
    return _dispersion(reading, used)[1]


def _measured_spread(reading: Mapping[str, Any], used: float) -> float | None:
    """The observed dispersion of the reading, or ``None`` when UNMEASURED.

    ⛔ ONE REPEAT CANNOT REPORT A SPREAD. The writer records ``0.0`` for it, and
    reading that ``0.0`` as "no sampling error" is the mistake this function
    exists to prevent: a single sample has no dispersion to report, so it is
    ``None`` (absence), not ``0.0`` (precision).
    """
    return _dispersion(reading, used)[0]


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
        # ⛔ ARMED (#13084). The allowance IS the tier's hot-window number, and
        # it is not restated here: the figures live in the artifact
        # (``product/pricing.json`` -> ``billing.storage.dimensions.graph_storage``,
        # read through ``pricing.storage_limits``) and reach this gate through
        # ``quota.resolve_org_limits``, so the price and the cap cannot drift.
        #
        # THE DERIVATION (recorded here because this is the line that arms it).
        # ⚠️ THEIR AUTHORITY, stated where the arming happens: the artifact records
        # these included amounts as **DERIVED, NOT OWNER-CONFIRMED**
        # (``billing.storage._included_amounts_status``) — solo's hot window comes
        # from the owner's 30 %-of-price rule, and pro/team are that rule carried
        # to another price, NOT a ruling on those tiers (#11758 comment 6807826749:
        # *"a product choice, not implied by the rates"*). They are defaults of
        # record, pending. ARMING a reversible write-refusal on them is this
        # slice's decision (#13082); the numbers themselves are the artifact's to
        # revise, and a revision lands here with no code change.
        #
        # ⚠️ IT IS NOT A DIVISION OF THE 30 % BUDGET BY $74/GB-MONTH — that
        # shortcut reproduces only solo and is what a reader re-running it
        # disputes (#13102 F4: $7.50/74 = 101.4, $44.70/74 = 604.1, against the
        # 102/615 this gate enforces). The cap comes from
        # ``docs/architecture/SEARCH-ARCHITECTURE.md`` §8, which solves the
        # window TOGETHER WITH DENSITY because the shared project line
        # ($85 ÷ tenants) is part of the same budget, and then applies the 75 %
        # occupancy of provisioned RAM:
        #
        #     provisioned MB  p = (0.30·P − $0.125) ÷ ($43.75/1000 + $85/8000)
        #     hot window        = 0.75 · p
        #
        #     solo  $19  → p 102.5 MB → 0.75p = 76.9 → **77 MB**
        #     pro   $25  → p 135.6 MB → 0.75p = 101.7 → **102 MB**
        #     team  $149 → p 819.8 MB → 0.75p = 614.8 → **615 MB**
        #
        # ($0.125 is the 1 GB record allowance at $0.125/GB-month; $43.75/1000
        # is RAM per provisioned MB; $85/8000 is the shared project line per
        # provisioned MB.) The artifact carries 77/102/615 and the ARTIFACT
        # WINS, because two numbers for one allowance is how a cap and a price
        # drift apart. (The issue quotes "solo ≈ 78 MB", the same figure at
        # $73/GB-month.)
        meter=_graph_storage_mb,
        stale_after_seconds=STALE_AFTER_SECONDS,
        # ⚠️ THE ESTIMATE RULE — the reason this dimension is armable at all, and
        # the reconciliation of the meter's own prohibition. ``GRAPH.MEMORY
        # USAGE`` UNDER-reports: it is blind to the HNSW vector index
        # (8.5x-21.7x on the index share, ~2x on the total —
        # ``graph_storage.VECTOR_INDEX_EXCLUDED``). That bias has ONE direction,
        # so a reading is a LOWER BOUND on the tenant's real residency, and
        # refusing at ``reading >= X`` is a refusal only when the true bytes are
        # already ``>= X``.
        #
        # ⛔ BUT THE LOWER-BOUND PROPERTY IS NOT UNCONDITIONAL (#13102 P1-3). Every
        # EXCLUSION removes bytes; the SAMPLING does not — an averaged estimate of
        # the graph's own size wobbles in BOTH directions (``SEARCH-ARCHITECTURE
        # .md`` §8 says so of this same instrument). So the honest statement is:
        # **no tenant can be refused while genuinely under the cap, UP TO this
        # estimate's sampling error** — never the unqualified version an earlier
        # revision of this comment (and the refusal text) asserted. Where the
        # ledger MEASURED the dispersion (``repeats >= 2``) the hard stop is
        # widened past it (``_hard_stop``), and where it did not, the refusal says
        # the error is UNMEASURED rather than quoting a number nobody took.
        #
        # What the direction costs is UNDER-enforcement: a vector-heavy graph can
        # hold more than the cap before its measured share crosses it. That is a
        # weaker ceiling, not an outage — and it is disclosed rather than hidden.
        #
        # THE BAND: at 1.0x the cap the gate only WARNS (an estimate must never be
        # the sole cause of a hard refusal). The hard stop is 1.5x — 115.5 MB solo,
        # 153 MB pro, 922.5 MB team — for the reading production actually records
        # (ONE repeat, so no dispersion is measurable), and it is derived from the
        # reading's own OBSERVED MINIMUM once repeats exist: the gate then refuses
        # only once every sample it took was over the cap (``_hard_stop``). ⛔ 1.5 IS A DECLARED
        # MARGIN, NOT A MEASURED ERROR (#13102 P1-2): an earlier version of this
        # comment justified it with "the meter's own reported spread (a few
        # percent)", which this gate never read — the production writer takes no
        # repeats, so the recorded spread is ``0.0`` by construction on a real
        # tenant. The refusal rests on MAGNITUDE, and it now says which of the two
        # it is resting on. Reads are never
        # refused: the gated doors are the WRITE doors, the posture Redis
        # documents for ``noeviction`` ("commands that only read existing data
        # still work as normal") and the reason eviction is not the answer here —
        # FalkorDB exposes no per-region eviction, so the only evictable unit is a
        # whole graph (``DUMP``->``DEL``->``RESTORE``), which stalls everything it
        # serves. Refusing a new write is reversible; dropping a graph to make
        # room is not.
        cap_band=CapBand(warn_at=1.0, refuse_at=HARD_STOP_MULTIPLE),
        # ⛔ NO READING MUST NEVER REFUSE A PAYING TENANT (#13102 P0-1, the
        # blocker). Every unmeasured state of THIS gauge — no row, ``unreported``,
        # ``read_error``, ``period_error``, a reason the gate has never seen, a
        # meter that raised, a non-reading, a figure that is null/NaN, or a
        # reading stale past the 24 h bound — leaves the dimension UNARMED and the
        # write SERVED, loudly. The reason is structural, not a preference: the
        # gauge's ONLY production writer is the backup sweep, so the row is
        # created by a SCHEDULED job that has nothing to do with the write being
        # gated, and refusing can never make it appear. Measured cost of getting
        # this wrong (before this declaration): a brand-new paid org's FIRST
        # ``POST /v1/points`` answered 500 — the onboarding demo included — and a
        # tenant outside the sweep population was refused FOREVER, with a message
        # ("graph_storage usage has no reading for team …") that presented OUR
        # bookkeeping state as the tenant's own usage.
        #
        # ⛔ WHY NOT "MEASURE AT THE DOOR" INSTEAD. A synchronous
        # ``GRAPH.MEMORY USAGE`` per write puts an engine round trip on the hot
        # points door, over EVERY graph of the org, and it still fails when the
        # engine is what is down. Provisioning-time measurement does not help
        # either: the row is keyed by the billing WINDOW, so every tenant is
        # rowless again at each boundary. Serving + alerting is the cheap, honest
        # option, and it is the ruling already on record (``#3981``, SETTLED
        # 2026-09-18: PROCEED AND ALERT, no new user-facing refusal).
        #
        # ⚠️ WHAT IT COSTS, restated because this is where the decision lives: for
        # a tenant whose gauge has never reported, or whose sweep has stopped, the
        # cap does NOT bind — under-enforcement, the safe direction, disclosed here
        # and in ``STORAGE-ARCHITECTURE.md`` §2.1. It is not silent (a throttled
        # ERROR under ``UNMEASURED_UNARMED_WARNING``, a counter, and a durable
        # ``ALLOWANCE_CAP_UNENFORCEABLE`` incident for the fault reasons), and the
        # real fix is still ``#9559`` (wire the meter so a reading always exists).
        #
        # ⛔ DEFAULT-DENY IS PRESERVED WHERE IT MATTERS. This is a per-dimension
        # DECLARATION, so ``capture_extraction`` — whose meter is a ledger row its
        # own writes create — keeps today's fail-closed behaviour, including on a
        # reason it has never seen. The declaration is not a licence to widen; a
        # new dimension that wants it has to say so and justify whose fault the
        # absence is.
        unmeasured_is_unarmed=True,
        # ⛔ A ZERO INCLUDED AMOUNT IS NOT A CAP OF ZERO — AND IT IS NOT AN UNSET
        # VALUE EITHER (#13102 F3). The artifact records
        # ``max_graph_storage_mb: 0`` for free/anon as a STATED amount
        # (``billing.storage._included_amounts_status``: *"Free/anon are the
        # exception: hot capacity 0 is stated by the decision record"*, and
        # ``SEARCH-ARCHITECTURE.md`` §8 tabulates their window target as 0), so
        # leaving the dimension UNARMED here is a deliberate DEPARTURE from that
        # recorded 0 — not a data gap, and not "the owner has not set it" (an
        # earlier version of this comment said exactly that, which its own cited
        # artifact refutes). The reason is arithmetic: ``used >= 0`` is true at
        # EVERY reading, so arming it would refuse EVERY gated write, including
        # the very first byte, on a tier that still grants a graph. An unarmed
        # amount refuses nothing, and the dimension keeps being metered by its
        # writer. ``zero_means_unarmed`` covers an EXACT zero only: a negative
        # figure is a fault and still refuses.
        zero_means_unarmed=True,
        # ⛔ "NO ROW YET" IS AN ANSWER, NOT A REFUSAL — and this bit is what
        # makes arming survivable at all (#13102). The gauge's ONLY production
        # writer is the backup sweep (``backup_sweep.py`` ->
        # ``record_graph_storage``), which ``BACKUP_SWEEP_ENABLED`` leaves OFF by
        # default; the row is keyed by the billing WINDOW, so EVERY paying tenant
        # loses its reading at each window boundary too. Refusing an absent row
        # therefore refuses a brand-new paying tenant's first ``POST /v1/points``
        # — measured, twice: ``hosted-e2e``/``test_05_backup_restore`` and
        # ``test-slow (b)``'s three ``test_hosted_api.py`` cases, each 500 with
        # "graph_storage usage has no reading for team … refusing to fail open".
        # That is a false refusal, and #3981's owner ruling (SETTLED 2026-09-18:
        # PROCEED AND ALERT, no new user-facing refusal) forbids refusing a paying
        # org over our own bookkeeping state.
        #
        # ⚠️ WHAT IT COSTS, stated rather than discovered later: an absent row is
        # NOT proof of zero bytes (a gauge cannot promise that), so a tenant whose
        # sweep has never run is served while its true residency is unknown. The
        # residual is UNDER-enforcement — the opposite direction from a false
        # refusal — and it lasts until the gauge first reports. Wiring the meter
        # so that a reading always exists is #9559 ("land the byte gate, then wire
        # the meter"); this declaration does not pretend that is done.
        #
        # It is why ``_graph_storage_mb`` must distinguish "absent" from every
        # OTHER reason the reader can report: a row that exists but cannot be
        # aged, a store that refused the read, and an unresolvable window are
        # FAULTS — and since #13102 P0-1 a fault on THIS dimension is UNARMED AND
        # LOUD too (``unmeasured_is_unarmed`` below), not a refusal. Refusing them
        # was the same defect one field over: the reason for the fault is ours, the
        # fix is ours, and a tenant cannot act on it. Where the two differ is the
        # SIGNAL — a fault files a durable operator incident, an absent row does
        # not (see ``_OPERATOR_FAULT_REASONS``).
        absent_is_zero=True,
        # ⚠️ DISCLOSED COST OF ARMING (follow-up filed from #13084): an armed
        # dimension READS its meter on every gated write (`_reading`), so a
        # paying tenant's points/documents door now performs one extra ledger
        # read per request — a graph query in registry/self-host mode, a
        # PostgREST read in Supabase mode. Before this the dimension was BLOCKED
        # and no read was taken. That is acceptable for correctness (a cap that
        # does not read its meter is not a cap) but it is a latency cost on the
        # hot write path; the fix is a short-TTL memo on the gauge read (60 s is
        # far inside the 24 h staleness bound and the 1.5x deadband), NOT a
        # silent "unlimited". Same shape as this module's own note about the
        # resolver read.
    ),
    _dimension(
        "postgres_storage", ALLOWANCE_KEYS["postgres_storage"],
        meter=None,
        # ⛔ THE PER-ORG ATTRIBUTION IS NO LONGER THE OBSTACLE — it was, and this
        # reason said so long after #12762 landed it. The correction is the point
        # of ``release`` below: the condition that HAS fired is recorded in
        # ``per_org_postgres_attribution`` (satisfied, evidence in the test) and
        # is deliberately NOT quoted here as the current obstacle.
        #
        # ⛔ THIS NARROWS THE RECORDED POSITION, AND THE NARROWING IS MARKED.
        # ``cost_dimensions``, ``postgres_bytes``, ``metering`` and two docs each
        # recorded that arming waits on ONE thing: the free/anon included
        # amounts. They are right that that decision is outstanding and
        # incomplete on what else is — #12762 made the ATTRIBUTION measurable,
        # and the gate is the consumer that must hold a cap against a figure on
        # every write, so the FIGURE'S SHAPE is load-bearing too. Without the
        # second condition, clearing the first arms the dimension with
        # ``meter=None`` and every gated write answers 500 ("no meter plumbed").
        #
        # OVERRIDES: the recorded “postgres_storage is armable once the free/anon
        # included amounts are decided” (cost_dimensions.py, postgres_bytes.py,
        # metering.py, docs/ops/postgres-storage-measurement.md,
        # docs/architecture/CONSUMPTION-MODEL.md) — NARROWED, not reversed: that
        # decision is still required, AND no per-org RESIDENCY gauge exists that
        # the gate can read on a write path. If anyone disagrees, the route is a
        # reopen on #9559, not a quiet edit.
        blocked_reason=(
            "no per-org RESIDENCY GAUGE, and the included amounts are not "
            "confirmed. The per-org Postgres BYTE ATTRIBUTION this reason used "
            "to call missing now EXISTS and is MEASURED (#12762 — "
            "``postgres_bytes.PerOrgPostgresBytes.for_org``; the "
            "``cost_dimensions`` attribution carries the OVERRIDES of the #4492 "
            "'there is no per-org attribution' position). Two things still "
            "block a cap: (1) no per-org figure this gate can read ON A WRITE "
            "PATH — the audit (``postgres_bytes.per_org_bytes``, RPC "
            "``postgres_per_org_bytes``) is a whole-table aggregation with a "
            "per-row TOAST lookup plus a second pass over the TOAST relation, "
            "and the ledger reader is the window ADDEND, not the org's stored "
            "footprint (``metering.get_postgres_bytes_written``: 'the ADDEND of "
            "the window, not the org's total stored footprint'); (2) the "
            "free/anon included amounts, which the artifact records as DERIVED, "
            "NOT OWNER-CONFIRMED (``product/pricing.json`` "
            "``billing.storage._included_amounts_status``). BLOCKED until a "
            "per-org gauge exists that is affordable per write AND the included "
            "amounts are confirmed"),
        release=(
            "per_org_postgres_attribution",
            "postgres_residency_gauge",
            "storage_included_amounts_confirmed",
        ),
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


# ── the block's own consistency check, at IMPORT ──────────────────────────────
# Loud at import for the same reason ``_dimension``'s registry resolution is: a
# block with no stated release, or a release token nobody defined, is a gate that
# has stopped being revalidatable while still appearing to protect something. This
# catches the STRUCTURAL half (a missing/unknown token); the SEMANTIC half — "is
# a quoted obstacle still true?" — is revalidated against the artifacts by
# ``tests/test_9559_blocked_release_conditions.py``, because a predicate here
# would import half the tree.
#
# It is a FUNCTION, not a bare module-level loop, for a testability reason that is
# itself the point: a loop can only be tested by grepping the source, and a
# source grep stays green on `if False:`. A callable can be handed synthetic
# dimensions, so each arm is proved to RAISE instead of proved to be spelled.
def _check_release_conditions(
        dims: Iterable[AllowanceDimension]) -> tuple[Any, ...]:
    """Refuse a block that cannot be revalidated (#9559 R11). Returns RECORDS.

    Three arms, each a distinct way a ``release`` declaration goes wrong: a block
    that names nothing that would end it, a condition carried by a dimension that
    is not blocked, and a token nobody defined (so a typo'd condition can never
    sit there silently never firing).

    ⛔ ``dims`` has NO default, on purpose: a default of ``()`` makes
    ``_check_release_conditions()`` a silent no-op, i.e. exactly the inert
    enforcer this function exists to be the opposite of.

    ⛔ IT RETURNS VALIDATED RECORDS, NOT NAMES — and that is load bearing, not
    style. An integer result was measured forgeable first (``RELEASE_CHECKED =
    len(DIMENSIONS)``), so it became a tuple of names; reviewer round 6 then
    measured the trivial enumeration that defeats the names too:
    ``RELEASE_CHECKED = tuple(d.dimension for d in DIMENSIONS)`` — a one-line edit
    that never calls this function — reproduced the published value and stayed
    GREEN. So each element is now an instance of ``_ValidatedBlock``, a class
    created INSIDE this call: no other code can name that type, it carries a seal
    object that exists nowhere else, and every call produces a fresh one. A forger
    must reconstruct that record shape and stamp, i.e. REIMPLEMENT the function —
    which is the honest limit of the guard, stated rather than overclaimed.
    """
    materialised = tuple(dims)

    # One seal per call, captured by the record class below. Two calls never share
    # it, so "this value came out of a run" is observable from outside without
    # exposing anything a module-constant enumeration could reproduce.
    seal = object()

    class _ValidatedBlock:
        __slots__ = ("dimension", "release", "seal")

        def __init__(self, d: AllowanceDimension) -> None:
            self.dimension = d.dimension
            self.release = tuple(d.release)
            self.seal = seal

    for d in materialised:
        if d.blocked and not d.release:
            raise RuntimeError(
                f"allowance dimension {d.dimension!r} is BLOCKED with no declared "
                f"release condition — a block that names nothing that would end "
                f"it cannot be revalidated (see RELEASE_CONDITIONS)")
        if not d.blocked and d.release:
            raise RuntimeError(
                f"allowance dimension {d.dimension!r} is not BLOCKED but declares "
                f"release conditions {d.release!r} — those belong to a block, "
                f"and carrying them on an enforced dimension is the "
                f"stale-grounds shape this field exists to expose")
        unknown = [t for t in d.release if t not in RELEASE_CONDITIONS]
        if unknown:
            raise RuntimeError(
                f"allowance dimension {d.dimension!r} declares release "
                f"condition(s) {unknown!r} that RELEASE_CONDITIONS does not "
                f"define — a typo'd condition would never be revalidated")
    return tuple(_ValidatedBlock(d) for d in materialised)


#: The declared dimensions the import-time release check VALIDATED, as records. ⛔
#: Published so the WIRING is testable: delete the call below and this name is
#: absent (AttributeError in the test), delete the assignment and the module does
#: not import — so "the check exists" and "the check RUNS" cannot drift apart. A
#: source-grep test could not tell those apart: `if False:` leaves the text.
#: Records with a per-call seal, not names, for the reason in the docstring above.
RELEASE_CHECKED = _check_release_conditions(DIMENSIONS)


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


# ── the loud half: a served-but-unenforced cap must never be SILENT ─────────
#
# ⛔ THE #3981 RULING HAS THREE LEGS, and a gate that disarms itself on a missing
# reading honours only the first (#13102 P0-1). The request proceeds; the fact is
# COUNTED; and an OPERATOR SIGNAL fires. Without the third leg, an unarmed cap is
# indistinguishable from a cap that is happily enforcing — which is the silent
# under-enforcement this whole dimension disclosed as a cost rather than hiding.

#: ``(dimension, reason)`` -> how many gated writes were SERVED with the cap
#: unarmed. Read by ``unarmed_served_counts()``; the durable surface is the
#: operator incident, and this is the in-process count an operator dashboard or a
#: test can read without a log parser.
_SERVED_UNMEASURED: dict[tuple[str, str], int] = {}

#: ``(signal, org, dimension)`` -> ``time.monotonic()`` of the last signal
#: emitted, so an unthrottled signal cannot become a log line per point write
#: (#13102 P2). ⛔ ONE KEY PER SIGNAL, deliberately: the at-cap WARN and the
#: unmeasured alert do NOT answer the same question — "over the cap and still
#: enforcing" vs "not enforcing at all, our plumbing broke" — so a shared key let
#: a WARN emitted a minute earlier swallow the durable incident for a fault
#: (measured: at-cap WARN for an org, then a `read_error` within the hour → the
#: counter moved and NOTHING was filed).
_LAST_SIGNAL: dict[tuple[str, str, str], float] = {}

#: The bound on :data:`_LAST_SIGNAL`. A map keyed by org on a hot path must have a
#: ceiling: it is pruned by age on the sweep below, and if a burst of distinct orgs
#: outruns the sweep the map is CLEARED rather than grown (losing the throttle
#: costs at most one extra log line per org; growing without bound costs memory on
#: the write path).
_SIGNAL_CAPACITY = 4096

#: Reasons that mean OUR PLUMBING FAILED, as opposed to the normal state of a
#: tenant the sweep has not reached yet. Only these file a durable operator
#: incident: ``absent`` (and ``absent``-like ``no_reading``) is the expected state
#: of every new tenant and of every tenant at each billing-window boundary, so an
#: incident per tenant would be noise that trains an operator to ignore the kind.
#: They are still LOGGED and COUNTED — loud, just not page-worthy.
_OPERATOR_FAULT_REASONS = frozenset({
    "unreported", "read_error", "period_error", "unknown_reason",
    "missing_figure", "meter_error", "non_reading", "stale",
})


def unarmed_served_counts() -> dict[tuple[str, str], int]:
    """A copy of the ``(dimension, reason) -> served`` counters.

    The in-process half of the operator signal (#13102 P0-1): a served write whose
    cap did not bind is COUNTED even when the log line is throttled away, so the
    fact is observable at any volume.
    """
    return dict(_SERVED_UNMEASURED)


def reset_signal_state_for_tests() -> None:
    """Clear the throttle and the counters (test seam)."""
    _SERVED_UNMEASURED.clear()
    _LAST_SIGNAL.clear()


def _signal_allowed(org_id: str, dim: AllowanceDimension, signal: str) -> bool:
    """True when this ``(org, dimension)`` may emit a signal NOW.

    Monotonic clock, so a system-clock jump cannot silence a live signal for an
    hour. Pruning is amortised into the same call: entries older than the interval
    can never suppress anything, so dropping them is free.
    """
    now = time.monotonic()
    key = (signal, org_id, dim.dimension)
    last = _LAST_SIGNAL.get(key)
    if last is not None and now - last < SIGNAL_INTERVAL_SECONDS:
        return False
    if len(_LAST_SIGNAL) >= _SIGNAL_CAPACITY:
        for stale in [k for k, ts in _LAST_SIGNAL.items()
                      if now - ts >= SIGNAL_INTERVAL_SECONDS]:
            _LAST_SIGNAL.pop(stale, None)
        if len(_LAST_SIGNAL) >= _SIGNAL_CAPACITY:  # a burst outran the sweep
            _LAST_SIGNAL.clear()
    _LAST_SIGNAL[key] = now
    return True


def _served_unmeasured(dim: AllowanceDimension, org_id: str, reason: str) -> None:
    """Record + signal that a write was SERVED with this dimension UNARMED.

    Called only from a dimension that declares ``unmeasured_is_unarmed``, so it
    never turns a refusal into a serve — it makes the DECIDED serve observable.
    Never raises: a broken alert subsystem must not convert an unenforced cap into
    a refused write (the whole point of the declaration).
    """
    _SERVED_UNMEASURED[(dim.dimension, reason)] = (
        _SERVED_UNMEASURED.get((dim.dimension, reason), 0) + 1)
    if not _signal_allowed(org_id, dim, "unmeasured"):
        return
    fault = reason in _OPERATOR_FAULT_REASONS
    log = _logger.error if fault else _logger.warning
    log("%s: team=%s dimension=%s reason=%s — the write is SERVED and the cap "
        "does NOT bind for this org until a usable reading exists "
        "(under-enforcement, not a refusal)",
        UNMEASURED_UNARMED_WARNING, org_id, dim.dimension, reason)
    if not fault:
        return
    # The durable leg. Imported lazily for the same reason ``metering`` does it:
    # the alert plane pulls in the store/channel machinery, and a metering fault
    # must never make an unrelated import fail. ``alert_operator`` is
    # fire-and-forget behind a bounded pool, and it dedups per (kind, org), so this
    # is one incident per org per outage rather than one per gated write.
    with contextlib.suppress(Exception):
        from .operator_alert import alert_allowance_cap_unenforceable
        alert_allowance_cap_unenforceable(dim.dimension, org_id, reason)


def _hard_stop(dim: AllowanceDimension, allowance: float,
               reading: MeterReading) -> float:
    """The figure at which a WRITE is refused — the cap or a measured deadband.

    An EXACT dimension compares at its cap (``refuse_at == 1.0``, unchanged).

    An ESTIMATED one has two cases, and which one applies is the READING's fact,
    not a preference:

    * **the dispersion is UNMEASURED** (one repeat — what every production write
      records today) → the declared policy margin, ``allowance * band.refuse_at``.
      That is a MARGIN, not a measurement, and the refusal says so.
    * **the dispersion IS measured** (``repeats >= 2``) → the stop is derived from
      the reading's own OBSERVED MINIMUM: refuse only once the LOWEST sample the
      instrument took is itself over the cap, i.e. ``
      used - (used - low) >= allowance ``, which in threshold form is
      ``allowance + (used - low)``. ⛔ The half-spread shortcut (``used -
      spread/2``) is NOT this number: ``used`` is the median of the repeats, so
      with repeats ``[50, 200, 201]`` the half-spread would put the low end at
      124.5 while the lowest SAMPLE was 50. Using the recorded minimum is the only
      form of the claim that is true of the instrument.

    ⛔ THE SPREAD IS NOT REQUIRED FOR THE BAND TO EXIST (#13102 P1-2). What is
    forbidden is claiming the band rests on a measurement the gate never read —
    which is what the previous wording did.
    """
    declared = allowance * dim.cap_band.refuse_at
    # ⛔ A MEASURED RANGE IS THE PAIR. ``low`` without ``spread`` is not a
    # measurement the refusal can render (and vice versa), so a half-set reading
    # falls back to the declared margin instead of crashing the formatter — the
    # reading's own contract is that both come from ``_dispersion`` or neither
    # does, and a caller that hand-builds one must not be able to break the
    # refusal path.
    if (dim.cap_band.refuse_at == 1.0 or reading.low is None
            or reading.spread is None):
        return declared
    return allowance + (reading.used - reading.low)


def _warn_at_cap(dim: AllowanceDimension, org_id: str, used: float,
                 allowance: float, refuse_at: float) -> None:
    """WARN that an estimated reading has reached its cap — throttled.

    The write still proceeds and reads are untouched: this is a signal that the
    measured figure has reached the cap, NOT a refusal formed from a number whose
    precision cannot carry one. Throttled per ``(org, dimension)`` because the band
    is entered by EVERY gated write while a tenant sits between its cap and its
    hard stop (#13102 P2).
    """
    if not _signal_allowed(org_id, dim, "at_cap"):
        return
    _logger.warning(
        "%s (warning only, estimated reading): team=%s "
        "dimension=%s used=%s cap=%s hard_stop=%s",
        AT_CAP_WARNING, org_id, dim.dimension, used, allowance, refuse_at)


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
        if dim.unmeasured_is_unarmed:
            # THE P0 (#13102): no reading is NEVER a refusal on a dimension whose
            # figure is written by a SCHEDULED job. Refusing here is what turned a
            # stopped sweep into a customer-wide write outage with a message that
            # blamed the tenant's own usage.
            _served_unmeasured(dim, org_id, getattr(e, "reason", "meter_error"))
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
        elif dim.unmeasured_is_unarmed:
            _served_unmeasured(dim, org_id, "non_reading")
            return None
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
        # The per-dimension ABSENCE semantics: "no ledger row for this window" is
        # a fact, not an unmeasurable figure — and the row is created by a
        # SCHEDULED writer, never by the operation being refused, so refusing can
        # never make it appear. Declared on the dimension, consumed HERE, so a
        # future dimension gets the behaviour from its declaration instead of from
        # a meter-specific branch (and a GAUGE that declares nothing stays strict,
        # which is what keeps a missing gauge a refusal). It RETURNS rather than
        # falling through on purpose: a row that does not exist has no AGE, so the
        # staleness bound below must not be applied to it.
        if dim.unmeasured_is_unarmed:
            # ⛔ LOUD, BUT NOT AN INCIDENT: this is the state of EVERY new tenant
            # and of EVERY tenant at each billing-window boundary, so the signal
            # is a throttled log + a counter rather than a page (see
            # ``_OPERATOR_FAULT_REASONS``). It is still stated, because "the cap
            # is not binding for this org" is exactly the fact an operator needs
            # to see when deciding whether #9559 has been wired.
            _served_unmeasured(dim, org_id, "absent")
        return MeterReading(used=0.0, measured_at=None, estimated=False,
                            note="no usage metered in this window")
    if reading is ABSENT:
        # Absence with NO declared flow semantics is still no reading.
        reading = None
    if reading is None:
        if observed_only:
            return None
        if dim.unmeasured_is_unarmed:
            _served_unmeasured(dim, org_id, "no_reading")
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
        if dim.unmeasured_is_unarmed:
            # A stale gauge describes a graph that no longer exists. Enforcing it
            # would decide from a number we cannot defend, and refusing it turns
            # "the sweep stopped" into "the tenant cannot write" — neither is
            # acceptable, so the cap is UNARMED and the operator is told.
            _served_unmeasured(dim, org_id, "stale")
            return None
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
            unreadable limit value (fail closed) — EXCEPT on a dimension that
            declares ``unmeasured_is_unarmed``, where an unmeasurable/stale
            reading serves the write and signals the operator instead (#13102).
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
        if allowance is None or (allowance == 0 and dim.zero_means_unarmed):
            # ``None`` is the documented unlimited; a ZERO amount on a dimension
            # that declares ``zero_means_unarmed`` is the artifact's UNSET
            # sentinel (see ``graph_storage``) and is equally unarmed.
            # Both still METER: unset is not a reason to stop measuring.
            _reading(dim, org_id, meters, observed_only=True)
            continue
        if allowance < 0:
            # ⛔ NEGATIVE IS NOT A SENTINEL (#13102 review, P3). The artifact
            # spells "unset" as 0, so a negative is a corrupt figure — and
            # reading ``allowance <= 0`` as unarmed made it fail OPEN (uncapped)
            # on a paid tier, while the money path refuses the same value. A
            # non-positive amount is only ever the sentinel when it IS zero.
            _logger.error(
                "allowance limit negative (fail-closed): team=%s dimension=%s "
                "field=%s value=%r", org_id, dim.dimension, dim.limit_key,
                allowance)
            raise QuotaCheckError(
                f"team limits {dim.limit_key} is negative for dimension "
                f"{dim.dimension!r} ({allowance!r}) — an unset amount is 0, "
                f"never a negative figure")
        reading = _reading(dim, org_id, meters, observed_only=False)
        if reading is None:
            # ⛔ REACHED ONLY FOR A DIMENSION THAT DECLARES
            # ``unmeasured_is_unarmed`` (#13102 P0-1). Every other dimension
            # raises inside ``_reading``, so this is not a widened fail-open: it is
            # the DECLARED behaviour of a dimension whose figure is written by a
            # scheduled job. ``_reading`` has already fired the operator signal
            # (log + counter, and a durable incident for the reasons that mean our
            # plumbing failed); the write is SERVED and the cap does not bind.
            continue
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
        band = dim.cap_band
        refuse_at = _hard_stop(dim, allowance, reading)
        warn_at = allowance * band.warn_at
        if band.refuse_at != 1.0 and warn_at <= used < refuse_at:
            # AN ESTIMATE WARNS BEFORE IT REFUSES (see ``CapBand``). The write
            # still proceeds and reads are untouched.
            _warn_at_cap(dim, org_id, used, allowance, refuse_at)
        if used >= refuse_at:
            note = f" ({reading.note})" if reading.note else ""
            if band.refuse_at == 1.0:
                head = (f"Team {dim.dimension} allowance reached "
                        f"({allowance:g} {dim.unit}, used {used:g}). Upgrade "
                        f"your plan to increase it.")
            else:
                # A banded dimension must say BOTH numbers and WHY they differ,
                # or the tenant cannot tell what to do about a refusal that
                # fires above the cap they were told about.
                #
                # ⛔ AND IT MUST NOT OVER-CLAIM THE ESTIMATE (#13102 P1-3). The
                # old text asserted the figure "is already a LOWER BOUND" full
                # stop. The systematic exclusions do all remove bytes, but a
                # SAMPLING estimate has error in BOTH directions — this repo says
                # so of this same instrument (``SEARCH-ARCHITECTURE.md`` §8) — so
                # the lower-bound property holds only UP TO that error. Where the
                # instrument measured its dispersion, the refusal states it and
                # says why the threshold sits where it does; where it did not, the
                # refusal says the error is UNMEASURED rather than inventing one.
                tier = limits.get("tier")
                plan = f" on the {tier} plan" if tier else ""
                if reading.low is None or reading.spread is None:
                    basis = ("this figure is an ESTIMATE whose sampling error is "
                             "UNMEASURED (the reading comes from a single "
                             "sample), so it is a LOWER BOUND on what this "
                             "account holds resident only up to that unmeasured "
                             "error")
                    margin = f"{band.refuse_at:g}x the cap"
                else:
                    basis = ("this figure is an ESTIMATE: it is a LOWER BOUND on "
                             "what this account holds resident up to its sampling "
                             "error, which this reading measured as "
                             f"{reading.spread:g} {dim.unit} across "
                             f"{reading.repeats} samples — and every one of those "
                             f"samples was over the cap (the lowest was "
                             f"{reading.low:g} {dim.unit})")
                    margin = "from the observed samples"
                head = (f"Team {dim.dimension} allowance reached "
                        f"({allowance:g} {dim.unit}{plan}, used {used:g}). "
                        f"Upgrade your plan to increase it, or reduce what you "
                        f"hold resident. The hard stop is {refuse_at:g} "
                        f"{dim.unit} measured ({margin}) because {basis}.")
            _logger.warning(
                "allowance exceeded: team=%s dimension=%s used=%s limit=%s "
                "hard_stop=%s", org_id, dim.dimension, used, allowance,
                refuse_at)
            # ⛔ THE PAYLOAD CARRIES THE VALUES THE GATE COMPARED (#13102).
            # ``quota.py`` documents that every field "must be the ones THIS
            # gate compared", and a banded dimension compares at the HARD STOP —
            # ``used >= 115.5`` — while ``int(used)`` floored the reported figure
            # to 115, i.e. 0.5 MB BELOW the number that fired the refusal, in the
            # payload a dashboard/billing reader consumes. ``limit`` had the same
            # defect one step over: it reported the 1.0x CAP (77) while the
            # threshold compared was the 1.5x stop (115.5), so a machine reader
            # saw ``{used: 115.5, limit: 77}`` — a tenant "150 % over limit" — and
            # could recover the real threshold only from the prose. Both fields
            # now carry what was compared; the CAP is still named in the message
            # ("77 MB ... hard stop 115.5"), which is where a human reads it.
            # Integral values keep their int type, so every unbanded dimension's
            # payload is byte-identical to before.
            payload_used = int(used) if used.is_integer() else used
            payload_limit = (int(refuse_at) if float(refuse_at).is_integer()
                             else refuse_at)
            raise QuotaExceededError(
                with_limit_contact(f"{head}{note}"),
                resource=dim.dimension, used=payload_used,
                limit=payload_limit,
                # Omitted (None) when the figure is exact, so `quota.py`'s
                # documented payload shape holds: only an ESTIMATE is labelled.
                estimated=True if reading.estimated else None,
            )
