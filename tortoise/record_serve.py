"""Interface D, the read path — the ANCHORED surface served by the record (#7970).

WHY THIS MODULE EXISTS
======================
`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record** and FalkorDB a **derived hot cache**. The epic
that carries the ruling (#7879) built every other half of it: the schema
`#7968`, the record projection `#7969`, the completeness marker `#7952`, the
residency router `#7953`, the cache projection `#7971`. What no unit carried is
the consumer the router NAMES — `residency_router`'s own word for this issue:
*"Not the record read path. Fetching from the record is ``#7970`` (deferred by
§2.0); this module names the repair and carries the coordinate it must be
fetched by."*

This module is that fetch. It is the `fetch-record` repair's implementation, and
it is the first read the record answers **with the graph engine stopped**.

WHAT IS SERVED — the decision, in code
======================================
§4 of the epic's scope asks *"which of the 697 anchored / 377 whole-graph Cypher
sites are served by Postgres directly"*. The answer is a REGISTRY, not a
paragraph: :data:`ANCHORED_READS` enumerates every read this module serves and,
just as importantly, every anchored read it deliberately does **not** — each with
the reason and (for a served read) the parity rule that keeps it honest.

**Served** (the anchored class: the read NAMES one node, so the record can answer
it by its own interface-E coordinate — `Point → id`, `Subject`/`Object` → `id`
(they keyed `name` until #9881), `Source → url`, `Event → eventId`,
`Session → id`; `public.node_identity` is the contract, and
`record_projection.key_properties` is the reader that decides it — a
consumer must NOT hard-code this list):

* ``point-by-id``  — ``MATCH (n:Point {id:$id}) RETURN properties(n)``, the
  shape behind ``GET /v1/points/{id}``.
* ``node-by-key`` — the same read for any record label, addressed by the
  coordinate the residency router already mints
  (``cache_projection.declared_key``), which is what a ``fetch-record`` repair
  is handed.

**Not served, with the reason** — a decision that does not name its exclusions
in the same artifact is indistinguishable from an omission:

* ``anchored-neighbours`` — the anchor's edges. The record shape exists
  (``public.relation``) but the cache's answer to it includes operator nodes and
  an ``idx`` convention that a record row cannot reproduce without re-deciding
  it. Serving it now would pin parity to an answer this unit cannot show is the
  same one, so it is deferred *by name*, not forgotten. (The cache read also had
  a real defect in this family — #6976, an operator-hop requirement that hid
  operator-less direct edges; fixed by #8991. It is the reason this module
  treats an unbound anchor as an error rather than a plausible answer.)
* ``anchored-ep-scalars`` — a read whose answer IS the epistemic scalars
  (``confidence`` / ``ep_*`` / ``posterior_*``), e.g. an EP ranking. These are
  **recomputed from the structure** and the record REFUSES them as columns and as
  ``props`` keys (the carve-out, ``chk_point_props_no_derived``). A record read
  cannot answer them, and answering them from a stored copy is the exact thing
  §17.5 forbids. ⚠️ ``point-by-id`` IS served and its CACHE answer may carry
  ``confidence`` as a stored prop; the RECORD answer omits it. That omission is
  disclosed at the endpoint (``hosted_api.get_point``'s docstring), because the
  exclusion below is about a read whose answer *is* the scalars, not about every
  read that could carry one.
* ``internal-accessor`` — ``TortoiseSDK.get_point``. It is the SDK's INTERNAL
  accessor: write paths call it mid-write to build journal events, so serving it
  from the record would make a writer read a store that the same write has not
  finished projecting. The caller-facing reads are the hosted endpoint served
  here and the MCP route that reaches the same accessor —
  ``tortoise_get_entity(id, type="point")`` (``tortoise_get_point`` is its
  RETIRED alias, ``tool_registry.RETIRED_USE_INSTEAD``) — neither is wired,
  because an MCP call carries no hosted org credential to resolve a record
  tenant from. (#8877 names the same internal/caller-facing
  separation.) ⚠️ The TYPED form ``tortoise_get_entity(id, type="point")`` is
  that route and DOES reach this accessor; the BARE ``tortoise_get_entity(id)``
  reaches ``TortoiseSDK.get_entity`` — a different read with its own coordinate,
  excluded under ``entity-accessor`` below rather than folded in here.
* ``entity-accessor`` — ``TortoiseSDK.get_entity``. A different coordinate (a
  name/alias resolution), not a node id, so a record answer would have to
  re-decide the resolution order to claim parity.
* ``selfhost-endpoint`` — the self-host server's point route. It carries no
  hosted org credential either, so there is no record tenant to scope the read
  to; serving it would answer ACROSS tenants.
* ``whole-graph`` — the 377 sites that name no node. They have no coordinate, so
  a record answer cannot be shown to be the same SET as the cache's; the issue
  scopes this unit to the anchored surface.

THE ANCHORING — what makes a silent fallback impossible
=======================================================
The failure mode is not "the record is wrong"; it is **"the read quietly
answered from the cache and nobody could tell"**. Three properties, and each is
a test rather than an intention (``tests/test_record_serve.py``):

1. **The read takes no cache handle.** :func:`read_node` is handed a *database
   connection*, a scope and a coordinate — there is no graph client, no
   ``sdk``, no projection in its signature, and this module imports no graph
   driver. A fallback is not "avoided" here; it is unrepresentable.
2. **The anchor must BIND, or the read fails loudly.** The row query carries
   ``org_id``, ``graph_id`` and the label's contract key, and asks for **two**
   rows. One row is an answer; **two rows mean the coordinate predicate did not
   restrict**, which is refused (:class:`AnchorDidNotBind`) rather than
   silently returning the first. A predicate that does not restrict its own
   subject is a defect class with NO tell at the cache — #6976 was one (an
   operator-hop requirement that answered ``[]`` for points that had edges,
   fixed by #8991) — so the tell here is an exception an operator can see, and
   it is deliberately not downgraded to a warning.
3. **A fallback is a NAMED, COUNTED event — never a branch.** Only
   :data:`MODE_PREFER` may consult the cache, and when it does the answer says
   so (``served_from="cache"``) with the reason (``record-unavailable`` /
   ``record-miss``) and increments :func:`fallback_counts`. :data:`MODE_RECORD`
   never falls back: a record outage raises, because an answer that cannot be
   attributed to the record is worse than no answer.

AND THE ASYMMETRY THE MARKER ALREADY SETTLED
============================================
§17.2/§17.3 forbids a **cache** miss from reading as an absence. The record is
the mirror: it IS authoritative, so a miss there is a genuine absence — **but
only where the record is complete**, which is a deployment fact, not a property
of this code. That is why the two modes exist: ``record`` asserts it, ``prefer``
does not and pays for the doubt with a named fallback. Default is OFF: with
``TORTOISE_RECORD_SERVE`` unset the read path behaves exactly as before, byte
for byte — this module is imported (it OWNS the mode's parsing, so the caller
must not re-parse the flag), but it is never *consulted*: no connection, no
query, no attribution field.

PARITY — the comparison rule, not an eyeball
============================================
The record cannot answer every field the cache can, and pretending otherwise
would be the lie this module has to avoid. The record holds the **PRIMARY** field
set (§3's per-write test), and the schema REFUSES the derived/volatile names by
CHECK. So "the same answer" means: *equal on every field the record can claim,
with every absence EXPLAINED by that refusal*. :func:`parity_difference` is that
rule in one place — and it fails in **both** directions, so neither a dropped
column nor an invented field can hide behind the carve-out. A field the cache
has and the record does not, and that is **not** in the refused set, is an
UNEXPLAINED absence and reddens the test.

⛔ FOUR sanctioned asymmetries, not one, and ALL are enumerated in
:func:`parity_difference` rather than absorbed by a tolerant comparison: the
SCHEMA's refusal set (the EP carve-out above), a fold ``ON CREATE`` default
(``FOLD_ON_CREATE_DEFAULTS``, #9634), since #10853 the
PROJECTION's own §9.1/D1 decline of an episodic Source's turn prose
(``cache_projection.episodic_source_declined_properties``), and since #10394 the
NOT-PROJECTABLE column class (``cache_projection.CACHE_FORBIDDEN_COLUMNS`` —
§9.1/D1's narrative, authority the record KEEPS and the cache must never
materialise). The decline exists because this module's parity rule is one of the
THREE record↔cache
field-comparison instruments in the tree — ``record_reconcile``, this function,
and ``tools/turn_parity_census.py``, which compares turn rows rather than
Source nodes; teaching only `record_reconcile` about the
decline (#10867 review ISSUE 1) left this one reporting a legacy session node's
own prose as a projection gap, i.e. a declared asymmetry reddening the same as a
real defect. The not-projectable class exists for the MIRROR reason: it is
explained in BOTH directions, because a record row rightly holding the column is
the state the deny exists to create (#10394).

⚠️ ``content_hash`` is in the refused set and is still recomputable (a pure
function of ``content``, #2795 D2) — a caller that wants it re-derives it with
the writer's own function rather than reading a stored copy. This module does not
re-derive anything: recomputation belongs to the caller's presentation
(``hosted_api``), and one declaration of the derivation stays one.

NO SURFACE CHANGE
=================
No MCP tool and no public ``TortoiseSDK`` method is added, removed or renamed
(the #7879 constraint: a surface change needs Daniel's approval first). The
module is internal; its caller is an existing endpoint.
"""
from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ANCHORED_READS",
    "MODES",
    "MODE_OFF",    "MODE_PREFER",
    "MODE_RECORD",
    "NODE_KEY_COLUMN",
    "NOT_SERVED",
    "READ_NODE_BY_KEY",
    "READ_POINT_BY_ID",
    "RECORD_SERVE_ENV",
    "RECORD_STATEMENT_TIMEOUT_MS",
    "SERVED",
    "AnchorDidNotBind",
    "AnchoredRead",
    "CacheAnswerRequired",
    "ParityDiff",
    "RecordServeError",
    "RecordUnavailable",
    "ServedAnswer",
    "answered_by_cache",
    "fallback_counts",
    "not_served_reads",
    "open_record",
    "parity_difference",
    "read_node",
    "read_point",
    "reset_fallback_counts",
    "serve_mode",
    "serve_point",
    "served_reads",
]

#: The arming surface. Off by default — the §2.0 posture (the cache answers
#: every read while the FalkorDB limit is held high). Off means never CONSULTED
#: by the read path (the module is imported because it owns this flag's
#: parsing, so the caller cannot re-parse it), so a default deployment is
#: byte-identical: no connection, no query, no attribution field.
RECORD_SERVE_ENV = "TORTOISE_RECORD_SERVE"

#: ``off``     the cache answers; the record is never consulted (the default).
#: ``record``  the record answers; there is no cache branch at all.
#: ``prefer``  the record answers first, and a NAMED, COUNTED fallback covers a
#:             record that ANSWERS and cannot serve (`record-miss`), cannot be
#:             reached (`record-unavailable`), cannot be scoped because this
#:             CREDENTIAL resolves no record tenant (`record-tenant-unresolved`
#:             — since #10046 no credential CLASS lands here by design: an API
#:             key since #10025, a session/JWT credential and an OAuth `oat_`
#:             token since #10046 all name the org's default graph, so read this
#:             as an UNNAMEABLE default row, never as ordinary session traffic),
#:             was never asked because the READ LANE refused the submission
#:             (`record-read-lane-refused`), or WAS asked and did not answer
#:             inside the lane's bound (`record-read-bound-miss` — a running
#:             bound miss: the record is neither down nor unasked).
#:             It does NOT cover a record that was never armed or is
#:             misconfigured: an absent/malformed DSN, a missing driver, an
#:             unprojectable row and a bad tenancy are structural and answer
#:             500 — retrying never repairs them, and a permanent increment of
#:             the outage counter would hide that.
MODE_OFF = "off"
MODE_RECORD = "record"
MODE_PREFER = "prefer"
MODES: tuple[str, ...] = (MODE_OFF, MODE_RECORD, MODE_PREFER)

#: The record read's SESSION bound. `record-reconcile` bounds the same thing for
#: the same reason: an armed read must not be able to hang a request on a wedged
#: database. A non-positive bound is not a bound (the driver reads 0 as "wait
#: forever"), so the constant is positive and any caller that ever exposes it
#: must refuse zero.
#:
#: ⚠️ The CONNECT bound is NOT declared here: `_default_connect` applies
#: :data:`tortoise.record_writer.CONNECT_TIMEOUT_SECONDS`, the one arming module
#: that owns opening a record connection. This module re-declared its own copy
#: once and nothing read it — a second constant that LOOKS like it bounds the
#: connect is worse than none, because the next reader trusts it.
RECORD_STATEMENT_TIMEOUT_MS = 15_000

#: The record's identity-contract column every node table carries, and the
#: primary key's last member (`supabase/migrations/20261012000001`). Each node
#: table pins it to the label's key PROPERTY with a CHECK, so it IS the interface
#: E coordinate while also being the indexed one — see :func:`_read_row`.
NODE_KEY_COLUMN = "node_key"

SERVED = "served"
NOT_SERVED = "not-served"


@dataclass(frozen=True)
class AnchoredRead:
    """ONE read of the anchored surface, and what is decided about it.

    ``cypher``      the cache-side shape this read replaces, quoted so the
                    decision is anchored to the site it is about.
    ``coordinate``  ``(label, property)`` the read is addressed by — the
                    record's own interface-E key, never a bare id.
    ``parity``      for a ``served`` read, the rule that keeps the two answers
                    comparable (``tortoise.record_serve.parity_difference``);
                    ``None`` for a ``not-served`` one.
    ``reason``      for a ``not-served`` read, WHY — an exclusion without a
                    reason is indistinguishable from an omission.
    """

    name: str
    status: str
    cypher: str
    coordinate: tuple[str, str] | None = None
    parity: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in (SERVED, NOT_SERVED):
            raise ValueError(f"status must be {SERVED!r} or {NOT_SERVED!r}")
        if self.status == SERVED and not self.parity:
            raise ValueError(
                f"{self.name!r} is served without a parity rule — a served read "
                f"with no comparison rule is an unchecked claim")
        if self.status == NOT_SERVED and not self.reason:
            raise ValueError(
                f"{self.name!r} is not-served without a reason — an exclusion "
                f"that does not say why is an omission")


READ_POINT_BY_ID = "point-by-id"
READ_NODE_BY_KEY = "node-by-key"

ANCHORED_READS: dict[str, AnchoredRead] = {
    READ_POINT_BY_ID: AnchoredRead(
        name=READ_POINT_BY_ID,
        status=SERVED,
        cypher="MATCH (n:Point {id:$id}) RETURN properties(n)",
        coordinate=("Point", "id"),
        parity="tortoise.record_serve.parity_difference",
    ),
    READ_NODE_BY_KEY: AnchoredRead(
        name=READ_NODE_BY_KEY,
        status=SERVED,
        cypher="MATCH (n:<Label> {<key_property>:$key}) RETURN properties(n)",
        coordinate=("Label", "interface-E key"),
        parity="tortoise.record_serve.parity_difference",
        reason="a served CAPABILITY with no consumer wired yet: it answers any "
               "record label by its interface-E coordinate, which is the shape "
               "a `fetch-record` repair is handed "
               "(residency_router.route_for_coordinate mints the coordinate, "
               "but that router has no non-test caller yet). The endpoint this "
               "unit wires serves point-by-id. "
               "⚠️ THE PARITY CLAIM IS MEASURED FOR `Point` ONLY, and this "
               "registry must not be read as 'parity proven for every label': "
               "the cache fold WRITES `ON CREATE` defaults for keys a producer "
               "omitted (the ONTOLOGY §4.5-REQUIRED `eventKind` becomes ``''``), "
               "while the record stores the ABSENCE — so a record-sourced "
               "`:Event` that a producer under-filled loses those keys, and "
               "`parity_difference(label='Event')` reports them as UNEXPLAINED "
               "(`FOLD_ON_CREATE_DEFAULTS` does not cover them) — the open gap in "
               "#9721, not something this "
               "module papers over. A consumer wiring this capability for "
               "another label must run the parity rule on that label first and "
               "read a red result as 'not yet served'.",
    ),
    "anchored-neighbours": AnchoredRead(
        name="anchored-neighbours",
        status=NOT_SERVED,
        # ⚠️ NOT a query — this field states the read this unit REFUSES, and nothing
        # executes an `AnchoredRead.cypher` at all: the registry's only two readers
        # (`served_reads` / `not_served_reads`) enumerate its names and statuses.
        # It is written in WORDS because the Cypher sketch it carried was wrong
        # twice over. `MATCH (a {id:$id})-[r]-(op) ... RETURN …` reads as an
        # ALL-TYPE walk, while the cache read it meant
        # (`search_engine.get_relationships`, reached by `sdk.expand_relationships`)
        # BINDS its types — `:IMPL|NAND|hasPart` off the anchor to an operator node,
        # `:IMPL|NAND|hasPart` on to the related claim, plus a direct `:IMPL|NAND`
        # leg — so the sketch OVERSTATED the read it described. And a pseudo-query
        # sitting in a prose field is a false positive for `#7948`'s census, which
        # derives an all-type walk from any governed `MATCH … -[r]- …` string and
        # would have had to carry this documentation string as an `EMPTY_PATTERN`
        # EXCLUSION — a register whose three legal grounds all excuse a REAL read
        # site by a fact about the pattern's schema (type-restricted, endpoints
        # outside every `aboutObject` triple, structural degree/existence test),
        # stated in `tests/_about_call_time_census.py` and in §13 of
        # `docs/architecture/STORAGE-ARCHITECTURE.md`. A fourth, prose-based ground
        # ("this string is never executed") would widen a gate to accommodate a
        # comment; §13 enumerates real readers and this entry is not one of them.
        cypher="not a query — the cache's neighbour answer is a two-leg operator "
               "hop over `IMPL|NAND|hasPart` edges plus a direct `IMPL|NAND` leg, "
               "labelled from the operator node's `op_type`/`label` and an `idx` "
               "convention a record row cannot reproduce without re-deciding it "
               "(see `reason`)",
        reason="the record shape exists (public.relation) but the cache's "
               "answer includes operator nodes and an idx convention a record "
               "row cannot reproduce without re-deciding it. Pinning parity to "
               "an answer this unit cannot show is the same one would freeze a "
               "decision it has not made. (#6976 — an operator-hop requirement "
               "that returned [] for points that had edges, fixed by #8991 — is "
               "the historical member of this defect family, and the reason an "
               "unbound anchor is an error rather than a plausible answer.)",
    ),
    "anchored-ep-scalars": AnchoredRead(
        name="anchored-ep-scalars",
        status=NOT_SERVED,
        cypher="any read whose ANSWER IS confidence / ep_* / posterior_*",
        reason="these are RECOMPUTED from the structure (§2.0 clause (a), "
               "§17.5): the schema refuses them as columns AND as props keys "
               "(chk_point_props_no_derived), so a record read cannot answer "
               "them and answering them from a stored copy is what the "
               "carve-out forbids. This excludes a read whose answer IS the "
               "scalars; `point-by-id` is SERVED and its cache answer may carry "
               "`confidence` as a stored prop, so the record answer omits it — "
               "disclosed in `hosted_api.get_point`'s docstring, not hidden "
               "behind `servedFrom`.",
    ),
    "internal-accessor": AnchoredRead(
        name="internal-accessor",
        status=NOT_SERVED,
        cypher="TortoiseSDK.get_point(id) — the SDK's internal accessor",
        reason="write paths call it MID-WRITE to build journal events, so a "
               "record-served answer would read a store the same write has not "
               "finished projecting. The caller-facing reads of the same shape "
               "are the hosted endpoint served here AND the MCP route that reaches "
               "it (`tortoise_get_entity(id, type=\"point\")` — `tortoise_get_point` "
               "is its RETIRED alias, `tool_registry.RETIRED_USE_INSTEAD`), which is "
               "not wired because an MCP call carries no hosted org credential from "
               "which to resolve a record tenant.",
    ),
    "entity-accessor": AnchoredRead(
        name="entity-accessor",
        status=NOT_SERVED,
        cypher="TortoiseSDK.get_entity(id) -> tortoise_get_entity(id)",
        reason="a SECOND internal accessor, and the same mid-write hazard: the "
               "write paths call it to echo a written entity back "
               "(`sdk._get_entity` at two write sites — a write echo and a "
               "post-write read-back — plus the read accessor's own delegation), "
               "so a record-served answer "
               "would read a store the same write has not finished projecting. "
               "Its caller-facing surface is `tortoise_get_entity`, EXCLUDED for "
               "the same reason as the point accessor: no hosted org credential "
               "from which to resolve a record tenant. ⚠️ `tortoise_get` is NOT a "
               "separate escape from the exclusion — it is a by-id dispatcher, and "
               "`tortoise_get(id, type=\"point\")` lands on the SAME accessor this "
               "module excludes (`tortoise_get_point` → `TortoiseSDK.get_point`), so "
               "it carries a coordinate and is covered here, not by `whole-graph`. "
               "Only a list-shaped call has no coordinate.",
    ),
    "selfhost-endpoint": AnchoredRead(
        name="selfhost-endpoint",
        status=NOT_SERVED,
        cypher="GET /points/{point_id} -> TortoiseSDK.get_point(id)",
        reason="the SELFHOST API's get-point route is a second caller-facing "
               "anchored read (tortoise/selfhost_api.py). It is not wired here: "
               "the record tenant it would need is resolved from a hosted org "
               "credential, and the selfhost surface presents none — serving it "
               "would need its own tenant decision, not a copy of this one.",
    ),
    "whole-graph": AnchoredRead(
        name="whole-graph",
        status=NOT_SERVED,
        cypher="the 377 sites that name no node",
        reason="no coordinate, so a record answer cannot be shown to be the "
               "same SET as the cache's; this unit is scoped to the anchored "
               "surface.",
    ),
}


class RecordServeError(RuntimeError):
    """A record read failed structurally — never silently returns an absence.

    The distinction this class exists for is §17.2's, applied to the record:
    *"the record holds no such row"* is an answer, and *"the record could not be
    asked"* is not. Collapsing them turns an outage into a negative finding.
    """


class AnchorDidNotBind(RecordServeError):
    """The coordinate predicate returned MORE than one row.

    ``public.point``'s primary key is ``(org_id, graph_id, node_key)``, so one
    coordinate can only ever match one row. Two rows therefore prove the
    anchor did not restrict. A filter that returns MORE than the coordinate
    allows is a defect class with no tell at the cache (#6976 was one: an
    operator-hop requirement that answered ``[]`` for points that had edges,
    fixed by #8991). Here the tell IS an exception, and it is deliberately not
    downgraded to a warning: an unanchored read that answers from a row nobody
    asked for is the worst outcome this module can produce.
    """


class RecordUnavailable(RecordServeError):
    """The record is armed for serving but could not be reached or read.

    Raised only in :data:`MODE_RECORD`, where a fallback is not permitted. In
    :data:`MODE_PREFER` the same condition is a *named* fallback instead.
    """


# ── the mode ────────────────────────────────────────────────────────────────


def serve_mode() -> str:
    """The armed mode: ``off`` (default) · ``record`` · ``prefer``.

    The #4097 truthy contract applies to the OFF spellings (unset, blank, ``0``,
    ``false``, ``no``, ``off``) — a typo must read OFF, never ON, because the ON
    side changes which store answers a customer's read. An unrecognised value
    that is not a falsey spelling is also OFF, and that is deliberate: refusing
    to boot on a typo would be a worse failure than serving from the cache, which
    is the documented §2.0 posture. ``record`` / ``prefer`` are matched exactly
    (case-insensitively, whitespace-stripped) so ``1``/``true``/``yes`` — the
    usual "turn it on" spellings — do NOT silently select the strictest mode.
    """
    raw = (os.environ.get(RECORD_SERVE_ENV) or "").strip().lower()
    if raw in (MODE_RECORD, MODE_PREFER):
        return raw
    # Everything else is OFF: unset, blank, the falsey spellings (`0`/`false`/
    # `no`/`off`), AND the truthy ones (`1`/`true`/`yes`/`on`). This flag has no
    # boolean mode, so the OFF arm is the single default rather than a
    # `is_truthy` branch whose both arms returned the same value — a check that
    # could not change the outcome was a safeguard a reader could not rely on.
    return MODE_OFF


def served_reads() -> tuple[str, ...]:
    """The anchored reads this module serves — the decision, enumerable."""
    return tuple(n for n, r in ANCHORED_READS.items() if r.status == SERVED)


def not_served_reads() -> tuple[str, ...]:
    """The anchored reads deliberately NOT served, each with its reason."""
    return tuple(n for n, r in ANCHORED_READS.items() if r.status == NOT_SERVED)


# ── fallback accounting — the fallback must be MEASURABLE ───────────────────

#: Why a read was answered by the cache while the record was armed. The reasons
#: are kept apart because they have different repairs: an OUTAGE is ops, a MISS is
#: a backfill/completeness question, an UNRESOLVED TENANT and a REFUSED LANE are
#: each a condition the record never caused.
FALLBACK_RECORD_UNAVAILABLE = "record-unavailable"
FALLBACK_RECORD_MISS = "record-miss"
#: A THIRD reason, for a condition that is neither: the credential resolves no
#: record tenant at all. `record_tenant_for` returns `None` when nothing NAMED a
#: tenant — the org's default row is absent, tombstoned, another org's or
#: unreadable (or the deployment is non-Supabase). It is NOT a credential class:
#: since #10046 an API key (#10025), a session/JWT credential and an OAuth `oat_`
#: token all name the org's default graph, so this counter firing means a default
#: row could not be named, not that the caller's credential is merely
#: session-shaped. Folding it into `record-unavailable` would fire an OUTAGE
#: counter permanently on a condition the record never caused — and the counter's
#: whole purpose is to be alertable.
FALLBACK_RECORD_TENANT_UNRESOLVED = "record-tenant-unresolved"
#: A FOURTH reason, for the same class as the third: the read LANE refused the
#: submission (backlog full / wait bound) so the record was NEVER ASKED. The
#: record was not slow and is not down, so folding this into
#: `record-unavailable` would fire the OUTAGE counter — permanently, on every
#: saturated read — and send an operator to Postgres for a pool condition. That
#: is the identical misattribution `record-tenant-unresolved` exists to prevent.
#: `exc.refused` is precisely the discriminator: the submission never ran.
FALLBACK_RECORD_LANE_REFUSED = "record-read-lane-refused"
#: A FIFTH reason, for the OTHER half of the same lane condition: the submission
#: RAN — the record WAS asked — and the bound abandoned only the AWAIT, so the
#: callable is still running and may still answer. `exc.refused` is `False`
#: here. It is not `record-unavailable` (the record was asked, and is not
#: necessarily down: it was slower than the lane's budget, which is a capacity
#: question about OUR lane or about the record's load), and it is emphatically
#: not absence (the §17.2 rule). Kept apart from the fourth reason for the same
#: reason the fourth is kept apart from the first: the two have different
#: repairs, and an operator sent to the wrong one repairs nothing.
FALLBACK_RECORD_BOUND_MISS = "record-read-bound-miss"
FALLBACK_REASONS: tuple[str, ...] = (
    FALLBACK_RECORD_UNAVAILABLE, FALLBACK_RECORD_MISS,
    FALLBACK_RECORD_TENANT_UNRESOLVED, FALLBACK_RECORD_LANE_REFUSED,
    FALLBACK_RECORD_BOUND_MISS)

_FALLBACKS: dict[str, int] = {}
#: The counter is incremented from BOTH the event loop (the endpoint's
#: tenant-unresolved arm) and a worker thread (`serve_point` under
#: `asyncio.to_thread`), so the read-modify-write needs a lock. Without it two
#: concurrent fallbacks can both read N and write N+1 — silently under-reporting
#: exactly the events this module's "the fallback is MEASURABLE" argument rests
#: on. This is REASONED, not proven: no test pins the interleaving (a threaded
#: stress test would assert a schedule it cannot guarantee), so it is stated as
#: unproven here rather than implied to be covered.
_FALLBACKS_LOCK = threading.Lock()


def note_fallback(reason: str) -> None:
    """Count ONE named fallback. Unknown reasons are refused."""
    if reason not in FALLBACK_REASONS:
        raise ValueError(
            f"fallback reason must be one of {FALLBACK_REASONS}, got {reason!r}")
    with _FALLBACKS_LOCK:
        _FALLBACKS[reason] = _FALLBACKS.get(reason, 0) + 1


def fallback_counts() -> dict[str, int]:
    """The process's named fallbacks, by reason (a copy)."""
    with _FALLBACKS_LOCK:
        return dict(_FALLBACKS)


def reset_fallback_counts() -> None:
    """Zero the counters — for a test, and for a meter that reports deltas."""
    with _FALLBACKS_LOCK:
        _FALLBACKS.clear()


# ── the record door ─────────────────────────────────────────────────────────


def open_record(record_dsn: str | None = None) -> Any:
    """Open a BOUNDED record connection on the ONE declared arming surface.

    The DSN is resolved by :func:`tortoise.record_writer.record_dsn` — the module
    that DECLARES the arming surface — so this door cannot drift from the
    writer's notion of "armed", and a non-Postgres value is the MISCONFIGURATION
    it is rather than an unreadable record. The connection is opened with the
    CONNECT bound the ARMING module owns
    (:data:`tortoise.record_writer.CONNECT_TIMEOUT_SECONDS`, applied inside
    ``_default_connect``) plus a ``statement_timeout`` set here: a served read
    must not be able to hang a request on a wedged database.

    ⚠️ The timeout is set **transaction-locally** (``set_config(..., true)``), not
    with a session-level ``SET``, because ``_default_connect`` is POOLED (#9558)
    and may hand back a connection an earlier caller returned: a session-level
    ``SET`` would outlive this read and impose a READ-sized bound on the next
    BORROWER — a write door's record, or a tenant's read under a different
    timeout. Transaction-local scope is exactly the read (psycopg2's first
    statement opens the transaction), and the pool's reset-at-reuse
    (``record_writer._reset_parked_transaction``) then cannot leave it behind —
    the bound dies with the transaction even though the handle outlives it. Pinned by
    ``tests/test_9558_record_write_cost.py::test_the_read_door_bounds_its_read_transaction_locally``:
    reverting this line reds it.
    """
    # ⚠️ The resolver is imported under a DISTINCT name: the parameter above is
    # also called `record_dsn`, and a same-named import rebinds the name so the
    # call would pass the FUNCTION as the DSN (a malformed-DSN refusal naming a
    # hash of a `<function ...>` repr — measured by the live S9 test).
    from .record_writer import RecordWriteError, _default_connect
    from .record_writer import record_dsn as _resolve_record_dsn

    # The RESOLUTION is inside the guard: `record_dsn` refuses a non-Postgres
    # value with `RecordWriteError`, and that is a MISCONFIGURATION, not an
    # outage — so it must not reach a caller as a bare foreign exception (which
    # `serve_point`'s last arm would file under `record-unavailable`, telling an
    # operator to look at the database instead of at the value they set). It is
    # re-raised as a STRUCTURAL `RecordServeError` (never `RecordUnavailable`),
    # so `record` mode reports it as a fault and `prefer` mode does not hide it
    # behind a fallback it would never repair.
    try:
        dsn = _resolve_record_dsn(record_dsn)
    except RecordWriteError as e:
        raise RecordServeError(
            f"the record DSN is not usable ({type(e).__name__}) — that is a "
            f"misconfiguration of the arming surface, not an unreadable record"
        ) from e
    if not dsn:
        # STRUCTURAL, like the malformed-DSN arm just above: an armed serve mode
        # with no DSN is a deployment that never becomes armed by retrying. Filed
        # as `RecordUnavailable` it would answer 503 in `record` mode and make
        # every `prefer` read increment the OUTAGE counter, permanently, on a
        # condition the record never caused. `prefer`'s "still incomplete" case is
        # a record that ANSWERS with no row (`record-miss`), not one that was
        # never armed.
        raise RecordServeError(
            "no record DSN — pass one or set TORTOISE_RECORD_DSN. The record "
            "cannot be read, so an absence is NOT established")
    try:
        conn = _default_connect(dsn)
    except RecordWriteError as e:
        # A MISSING DRIVER is a deployment defect, not an outage: retrying never
        # installs `psycopg2`, and the message names the fix ("install the
        # 'postgres' extra") — which the availability arm below would replace
        # with a 503 whose detail is a type name. `_default_connect` raises
        # `RecordWriteError` ONLY for that import (a failed `psycopg2.connect`
        # propagates as the driver's own exception, handled below).
        raise RecordServeError(f"the record is armed but its driver is not usable ({e})") from e
    except Exception as e:  # the driver's own message can carry the DSN
        raise RecordUnavailable(
            f"the record could not be opened ({type(e).__name__}) — an absence "
            f"is NOT established") from e
    try:
        cur = conn.cursor()
        # ⛔ TRANSACTION-LOCAL, because the handle is now POOLED (#9558).
        # `_default_connect` may return a handle an earlier call returned, and a
        # session-level `SET` would then outlive this read: the next borrower of
        # that connection would inherit this read path's 15 s statement timeout
        # (and, on the write door, a bound chosen for reads). `set_config(...,
        # true)` scope it to THIS transaction — which is the whole read, since
        # psycopg2's first statement opens one — and the pool resets that
        # transaction at the next BORROW (`_reset_parked_transaction`), so the
        # bound dies with it instead of following the handle to the next
        # borrower. `SET LOCAL` would do
        # the same; `set_config` is used because it takes a bind parameter, which
        # `SET` does not.
        cur.execute("SELECT set_config('statement_timeout', %s, true)",
                    (str(int(RECORD_STATEMENT_TIMEOUT_MS)),))
        cur.close()
        return conn
    except Exception as e:
        # The connection EXISTS at this point, so it must be closed before the
        # read is abandoned: `serve_point` only closes a connection the opener
        # RETURNED, so leaking here would leak a live socket per failed armed
        # request — on the path that is already degraded.
        with contextlib.suppress(Exception):
            conn.close()
        raise RecordUnavailable(
            f"the record could not be opened ({type(e).__name__}) — an absence "
            f"is NOT established") from e


def _rows(cur: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    """Rows as dicts — reusing the projection's own reader, not a second copy.

    ``cache_projection._rows`` already handles both cursor shapes the record's
    doors advertise (a ``RealDict`` cursor, or a plain one plus ``description``).
    A second local copy of that handling is how two readers of the same table
    start disagreeing about column names.
    """
    from .cache_projection import _rows as _cp_rows

    return _cp_rows(cur, sql, params)


def _read_row(conn: Any, *, org_id: str, graph_id: str, table: str,
              key_column: str, key: str) -> Mapping[str, Any] | None:
    """THE anchored row read — the only row query this module issues.

    ⚠️ The predicate is the anchor: ``org_id`` + ``graph_id`` (the record's own
    scope, R6) + ``node_key`` — the record's identity-contract column, which is
    the primary key's last member and is pinned to the label's key PROPERTY by
    the schema's own CHECK (``chk_point_node_key_is_contract_key`` and its
    siblings). ⚠️ The CHECK's token list is NOT restated here: it moves — #9881
    dropped the ``name`` pins and re-added ``Subject``/``Object`` as
    ``node_key = id`` — and a stale list in this docstring would be read as the
    predicate to bind. :func:`record_projection.key_properties` is the reader
    that decides it. Anchoring on
    ``node_key`` rather than on the property column is deliberate: the property
    column is a plain text column with no index, while ``node_key`` is the PK,
    and the CHECK makes the two the same value by construction. ``table`` comes
    from :data:`cache_projection.NODE_TABLES` — the schema's own reading, never
    from a request.

    ``LIMIT 2`` is the bind check, not a paging device: the primary key makes a
    second row impossible, so a second row means the predicate did not restrict
    and the answer would be the wrong node. See :class:`AnchorDidNotBind`.
    """
    from .record_projection import scope_connection

    # The tenant GUC + the role posture, installed by the ONE function both
    # record doors use (#9600): an unscoped read under RLS returns ZERO rows on
    # this door too, and a zero-row read is exactly the absence this module
    # promises never to invent.
    cur = scope_connection(conn, org_id)
    rows = _rows(
        cur,
        f"SELECT * FROM public.{table} "
        f"WHERE org_id = %s AND graph_id = %s AND {key_column} = %s LIMIT 2",
        (org_id, graph_id, key),
    )
    if len(rows) > 1:
        raise AnchorDidNotBind(
            f"{table}: the coordinate {key!r} matched {len(rows)} rows — the "
            f"anchor did not restrict, so answering from the first row would be "
            f"answering from a row nobody asked for")
    return rows[0] if rows else None


def read_node(conn: Any, *, org_id: str, graph_id: str, label: str,
              key: str) -> dict[str, Any] | None:
    """ONE node, from the record, by its interface-E coordinate.

    Returns the node's graph-property map (the record's PRIMARY field set, in
    the cache's own property vocabulary) or ``None`` when the record holds no
    such row. ``None`` is an ANSWER — the record is authoritative — and it is
    reachable only after the record was successfully read; a failure to read
    raises :class:`RecordServeError`.

    ⚠️ "The record's PRIMARY set" is the record's primary set **as the CACHE may
    hold it**: `cache_projection.CACHE_FORBIDDEN_COLUMNS` subtracts the
    not-projectable columns (an authority the target store forbids — §9.1/D1's
    narrative on `source`/`event`, `#10394`), so a served node does not carry a
    PRIMARY record column there. That is the same map `rebuild_from_record`
    writes, which is the contract this function exists to keep.

    ⚠️ The map is the record's PRIMARY set **plus** the §2.6 cache names that
    ``node_from_row`` DERIVES from those columns (`status`, from a supersession
    carrier) — #9974 — **plus** the same `status` derived from the record's own
    HISTORY, through THREE legs of `EVENT_STREAM_FOLDS`, all applied here:
    the connector/pack lifecycle where an event kind names the `:Object`
    (#10198), an explicit `EntityMutated` `state.status` — a caller's own
    lifecycle write, for an `:Object` OR a `:Subject` (#9974), and the CREATION
    record's `payload ->> 'status'` (`ObjectRegistered`/`SubjectAdded`, the BIRTH
    writer, #9974). So the schema's refused names CAN appear here,
    synthesized rather than stored, and a consumer must not read their presence
    as "the record holds a column for this": ``_refused('object')`` still lists
    `status`. That is the projection's contract for a record-sourced node (the
    same map a `rebuild_from_record` materializes), and it is what makes this
    read agree with the rebuilt cache instead of silently dropping a lifecycle
    value the cache carries.

    ⭐ The row becomes properties through ``cache_projection.node_from_row`` —
    the CACHE PROJECTION's own row→property map. The projection writes the cache
    through it; this reads the record through it. One declaration, so the two
    directions cannot drift about what a column is called, and which derived
    names a row can yield is decided in ONE place (that table's bound, not a
    second rule here).
    """
    from .cache_projection import (
        EVENT_STREAM_FOLDS,
        NODE_TABLES,
        apply_event_stream_folds,
        node_from_row,
        read_lifecycle_events,
    )

    table = NODE_TABLES.get(label)
    if table is None:
        raise RecordServeError(
            f"{label!r} has no record table — a record read cannot answer it "
            f"(record labels: {sorted(NODE_TABLES)})")
    if not key:
        raise RecordServeError("a record read must name a non-empty key")
    row = _read_row(conn, org_id=org_id, graph_id=graph_id, table=table,
                    key_column=NODE_KEY_COLUMN, key=key)
    if row is None:
        return None
    node = node_from_row(label, row)
    # #10198 / #9974: the event-stream half of the same `status` cache is
    # derived from the record's HISTORY, which no layer row carries — so a read
    # that stopped at `node_from_row` would answer without the status a
    # `rebuild_from_record` materializes, breaking this function's own "the same
    # map" contract. THREE legs reach that stream and all are applied here:
    #   * the connector/pack lifecycle (#10198) — an `EventRecorded` whose
    #     `eventKind` a fold maps onto `Object.status` (`:Object` only, as the
    #     live fold is);
    #   * an explicit `EntityMutated` `state.status` (#9974) — the caller's own
    #     lifecycle write for an `:Object` OR a `:Subject`, whose only carrier is
    #     `public.journal_event.payload`. Narrowed to the events that can
    #     name THIS Object: a single-row read folds only the events that could
    #     possibly name it. ⚠️ That narrows the FOLD, not the read — the table's
    #     only index is keyed on the event's own subject, so the predicate filters
    #     after the scope scan (see `read_lifecycle_events`).
    #   * a CREATION record (#9974) — `ObjectRegistered`/`SubjectAdded`, whose
    #     `payload ->> 'status'` is the BIRTH value the record may not store.
    #     Selected by the same id coordinate (the snapshot's record key).
    target = node.props.get("name")
    # ⛔ The gate is the FOLD's membership, not a NAME. Since #9881 an Object's
    # record key is the `id` (`chk_object_node_key_is_contract_key`: `node_key =
    # id`), so `name` is not the coordinate the fold needs — and it can be the
    # EMPTY STRING (`name IS NOT NULL` does not exclude `''`), which made the old
    # truthiness gate skip the fold for a node `rebuild_from_record` folded by id.
    # That is the two-answer split this half exists to remove. A row with NO name
    # is not a legal shape (`name IS NOT NULL`), so this is not defence against a
    # nameless row — only against the empty one. `key` is required non-empty
    # above, so the fold always has a coordinate.
    if label in EVENT_STREAM_FOLDS:
        node = apply_event_stream_folds(
            (node,),
            read_lifecycle_events(conn, org_id=org_id, graph_id=graph_id,
                                  # #9974: the second coordinate. A `restatus`
                                  # names its target by the stable id, so a
                                  # name-only narrowing would filter out the
                                  # record that carries the value and this door
                                  # would answer a status the rebuild does not.
                                  target=target if isinstance(target, str) and target else None,
                                  target_id=key),
        )[0]
    return dict(node.props)


def read_point(conn: Any, *, org_id: str, graph_id: str,
               point_id: str) -> dict[str, Any] | None:
    """``point-by-id`` — the anchored read behind ``GET /v1/points/{id}``."""
    return read_node(conn, org_id=org_id, graph_id=graph_id, label="Point",
                     key=point_id)


# ── the seam: serve, or a NAMED fallback ────────────────────────────────────


@dataclass(frozen=True)
class ServedAnswer:
    """ONE answered read, with the store it came from and why.

    ``served_from``    ``"record"`` or ``"cache"`` — never inferred, always
                       stated, so an invisible fallback cannot exist.
    ``fallback_reason``   the named reason a ``prefer``-mode read landed on the
                       cache, or ``None`` when no fallback happened (including
                       the whole OFF mode, where the record was never in play).
    ``props``          the node's properties, or ``None`` for an absence.
    """

    read: str
    coordinate: str
    served_from: str
    props: dict[str, Any] | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if self.served_from not in ("record", "cache"):
            raise ValueError(
                f"served_from must be 'record' or 'cache', got "
                f"{self.served_from!r}")

    @property
    def found(self) -> bool:
        return self.props is not None

    def as_wire(self) -> dict[str, Any]:
        """The attribution fields a caller puts on the response.

        ⚠️ BOTH names are emitted on every ARMED read, ``fallbackReason`` as
        ``None`` when no fallback happened. Omitting the null would let a point
        whose own ``props`` carry that name ship a FAKE fallback attribution —
        the same confusion (attribution vs stored data) in the other direction,
        and the dangerous one: it could drive an ops/backfill signal from
        customer data. ``off`` never calls this, so the default response is
        untouched.
        """
        return {"servedFrom": self.served_from,
                "fallbackReason": self.fallback_reason}


class CacheAnswerRequired(Exception):
    """The record did not answer, and the CALLER must answer from the cache.

    Raised by :func:`serve_point` in ``prefer`` mode when the caller passed
    ``cache_answer=None`` — the deferred shape, for a caller whose cache read
    has to run on a different LANE than the record consult (#10602): the record
    is a bounded round trip on one pool, the cache's COLD OPEN (connect, version
    probe, ``_ensure_indexes()``, and on a restored graph an index BUILD) is
    budgeted by ``probe_setup_timeout()`` on another. One synchronous callback
    cannot honour both bounds, so the seam keeps the DECISION (whether to fall
    back, and the named reason why) and the caller keeps the EXECUTION.

    ``reason`` is the same named, counted reason a callback fallback would have
    used (:data:`FALLBACK_RECORD_MISS` / :data:`FALLBACK_RECORD_UNAVAILABLE`).
    ⛔ Nothing is COUNTED here: the fallback is counted by
    :func:`answered_by_cache` when the caller's cache read actually answers, so
    a read that never came back still contributes no increment.
    """

    def __init__(self, reason: str) -> None:
        if reason not in FALLBACK_REASONS:
            raise ValueError(
                f"reason must be one of {FALLBACK_REASONS}, got {reason!r}")
        self.reason = reason
        super().__init__(f"the record did not answer ({reason}) — the caller "
                         f"must read the cache on its own lane")


def answered_by_cache(*, read: str, coordinate: str,
                      props: dict[str, Any] | None,
                      reason: str) -> ServedAnswer:
    """The ONE way a fallback that LANDED is recorded — count then answer.

    The count belongs to the ANSWER, not to the intent: a fallback whose cache
    read raised did not serve anything, and counting it would report a read that
    never existed (the metric's meaning is "answered by the cache", which is
    what an operator alerts on). Both fallback shapes go through here — the
    callback one inside :func:`serve_point`, and the DEFERRED one a route runs
    itself after :class:`CacheAnswerRequired` (`hosted_api.get_point`) — so the
    ordering cannot be stated correctly in one place and inverted in the other.
    """
    note_fallback(reason)
    return ServedAnswer(read=read, coordinate=coordinate, served_from="cache",
                        props=props, fallback_reason=reason)


def serve_point(*, tenant: tuple[str, str], point_id: str,
                cache_answer: Callable[[], dict[str, Any] | None] | None,
                record_dsn: str | None = None,
                connect: Callable[[str | None], Any] | None = None,
                mode: str | None = None) -> ServedAnswer:
    """Answer ``point-by-id`` under the armed mode — the ONE seam.

    ``tenant``       ``(org_id, graph_id)``, resolved by the CALLER from the
                     request's credential (one resolution, never a second).
    ``cache_answer`` a CALLABLE that performs the cache read. It is a callback,
                     not a handle, on purpose: :data:`MODE_RECORD` never calls
                     it, so the strict mode's inability to fall back is
                     structural rather than a rule someone remembers.

                     ⛔ ``None`` is legal in ``prefer`` mode ONLY, and means
                     the DEFERRED shape: the seam decides and names the
                     fallback exactly as it would for a callback, then raises
                     :class:`CacheAnswerRequired` so the caller runs the cache
                     read on the lane its own cold open is budgeted for. The
                     caller records the answer with :func:`answered_by_cache`.
    ``connect``      the connection opener (injected by DB-free tests); defaults
                     to :func:`open_record`.
    ``mode``         an explicit override of :func:`serve_mode` — the seam's
                     own tests drive all three modes without touching the
                     process environment, and a caller that has already read the
                     mode passes the same value it routed on.

    The three modes, and nothing else:

    * ``off``    → the cache answers; the record is never opened and the
                   attribution says so (``served_from="cache"``, no fallback
                   reason — nothing was skipped).
    * ``record`` → the record answers or the read RAISES. A miss is an absence
                   (``props=None``, ``served_from="record"``). The cache
                   callback is never invoked.
    * ``prefer`` → the record first; an outage or a miss falls back to the
                   cache, and the fallback is NAMED and COUNTED
                   (:func:`fallback_counts`).
    """
    mode = mode if mode is not None else serve_mode()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if cache_answer is None and mode != MODE_PREFER:
        # `off` has no other answer, so a missing callback would be a silent
        # "nothing to serve"; `record` never needs one and passes the real
        # callback so the never-called contract stays visible at the call site.
        raise ValueError(
            f"cache_answer may be None only in {MODE_PREFER!r} mode (the "
            f"deferred shape), got mode={mode!r}")
    coordinate = f"Point:{point_id}"
    if mode == MODE_OFF:
        return ServedAnswer(read=READ_POINT_BY_ID, coordinate=coordinate,
                            served_from="cache", props=cache_answer())

    org_id, graph_id = tenant
    opener = connect or open_record

    def _from_record() -> dict[str, Any] | None:
        conn = opener(record_dsn)
        try:
            return read_point(conn, org_id=org_id, graph_id=graph_id,
                              point_id=point_id)
        finally:
            # A failed close must not replace the answer (or the exception) this
            # read already produced.
            with contextlib.suppress(Exception):
                conn.close()

    def _fell_back(reason: str) -> ServedAnswer:
        if cache_answer is None:
            # The DEFERRED shape: the caller's cache read must run on a lane
            # this seam cannot reach (see `CacheAnswerRequired`). The reason is
            # still the seam's — that is the decision being kept here — and
            # nothing is counted, because nothing has answered yet.
            raise CacheAnswerRequired(reason)
        # The count happens AFTER the cache answers (`answered_by_cache`): a
        # fallback that itself failed did not land, and counting it would report
        # a served read that never existed (the metric's meaning is "answered by
        # the cache", which is what an operator alerts on). A cache failure
        # raises out of here, so this request contributes no increment.
        return answered_by_cache(read=READ_POINT_BY_ID,
                                 coordinate=coordinate,
                                 props=cache_answer(), reason=reason)

    from .cache_projection import CacheProjectionError
    from .record_projection import RecordProjectionError

    try:
        props = _from_record()
    except (AnchorDidNotBind, CacheProjectionError, RecordProjectionError):
        # STRUCTURAL refusals, never availability events. `AnchorDidNotBind`
        # means the anchor did not restrict, so NEITHER store's answer is
        # trustworthy for this coordinate. `CacheProjectionError`
        # (`UnprojectableRecord` / `UnknownRecordColumn`) means the record row —
        # or a column the reader does not know — cannot be projected at all:
        # the "the record is newer than the reader" class.
        # ⛔ One of its subclasses, `PlacementTableUnavailable`, is NOT that class
        # and must not be read with it: nothing about the record is wrong — the
        # property namespace came from a broken INSTALL (no carrier, a carrier
        # or doc that will not decode/read/parse, an EMPTY or truncated one, or
        # a malformed row), so the operator's remedy is to repair the install,
        # not the credentials or the reader version (#10663, #11227).
        # `UnprojectableRecord` and `UnknownRecordColumn` are not the whole set —
        # `CacheProjectionError` has FIVE direct subclasses in
        # `cache_projection.py`: `UnknownRecordLabel` is a caller's label with no
        # record table and `AmbiguousRecordColumn` is a placement-table
        # declaration fault, and neither is "newer than the reader" nor a broken
        # install.
        # `RecordProjectionError` is where `scope_connection` refuses a
        # MISCONFIGURED tenancy: a role that BYPASSES RLS, a lost tenant GUC, an
        # unreadable role posture. Its message names the fix ("arm
        # TORTOISE_RECORD_DSN with tortoise_record_app"), and letting it fall to
        # the availability arm below would both lose that instruction and count
        # it as `record-unavailable` — an ops reason for a value problem, which
        # is the same misclassification the misconfigured-DSN arm refuses.
        # All three are defects a fallback would hide, and all three reach the
        # endpoint as a fault (500) rather than an impairment (503).
        raise
    except RecordServeError as e:
        if not isinstance(e, RecordUnavailable):
            raise  # structural: an unknown label, an empty key — a defect
        if mode == MODE_RECORD:
            raise
        return _fell_back(FALLBACK_RECORD_UNAVAILABLE)
    except Exception as e:
        # The arm is DELIBERATELY wide: any failure while reading is surfaced as
        # an outage (retryable) rather than a fault, because mis-reporting a real
        # outage as a defect takes the read path down, while mis-reporting a
        # defect as an outage is corrected by the retry that then succeeds. The
        # reading is never silent either way — `servedFrom`/`fallbackReason`
        # reach the caller and the count reaches `fallback_counts()`.
        if mode == MODE_RECORD:
            raise RecordUnavailable(
                f"the record could not answer {coordinate} "
                f"({type(e).__name__}) — in 'record' mode there is no cache "
                f"branch, so an absence is NOT established") from e
        return _fell_back(FALLBACK_RECORD_UNAVAILABLE)

    if props is None and mode == MODE_PREFER:
        return _fell_back(FALLBACK_RECORD_MISS)
    return ServedAnswer(read=READ_POINT_BY_ID, coordinate=coordinate,
                        served_from="record", props=props)


# ── parity: the comparison rule ─────────────────────────────────────────────


@dataclass(frozen=True)
class ParityDiff:
    """The EXPLAINED difference between a cache answer and the record's.

    ``unexplained_missing``  cache fields the record did not answer, and that NO
                             declared asymmetry explains — not the schema's
                             carve-out, not a fold `ON CREATE` default, not the
                             §9.1/D1 decline, and not the not-projectable set —
                             i.e. a projection gap. Non-empty = RED. ⚠️ FOUR
                             classes, not three: the decline (an episodic
                             Source's `summary`/`_searchText`, #10853) is one of
                             the explainers, so a reader resolving against only
                             the other three concludes RED on a field that is
                             correctly explained. The four are the same four
                             ``explained_missing`` enumerates below.
    ``invented``             record fields the cache does not have, and that no
                             declared ``ON CREATE`` default explains — i.e. a
                             field the record path made up. Non-empty = RED.
                             ⚠️ The not-projectable columns
                             (``cache_projection.CACHE_FORBIDDEN_COLUMNS``) are
                             EXCLUDED here, because a declared asymmetry runs in
                             BOTH directions (#10394): the record holding a
                             column the projection must not materialise is the
                             state the deny exists to create, and calling it an
                             invention would make the rule forbid its own fix.
    ``changed``              fields both stores answer, with different values.
                             Non-empty = RED.
    ``explained_missing``    a DECLARED asymmetry at work. Every member comes
                             from one of FOUR declarations, and the member's
                             NAME (not this dict) says which by resolving
                             against it: the schema's carve-out (`confidence`,
                             `content_hash`, `ep_*`, …), a fold `ON CREATE`
                             default (`cache_projection.FOLD_ON_CREATE_DEFAULTS`,
                             #9634 — the field is one the record row is SILENT
                             about, so the column exists and only its VALUE is
                             supplied), §9.1/D1's declined
                             turn prose for an episodic Source (`summary`,
                             `_searchText`), or a **not-projectable** column
                             (`cache_projection.CACHE_FORBIDDEN_COLUMNS` —
                             §9.1/D1's narrative on `source`/`event`, #10394).
                             Enumerated, never silently tolerated.
                             ⚠️ FOUR and not three: a fold default reaches this
                             dict too (`parity_difference({'name': 'acme',
                             'objectKind': 'other', 'title': ''}, {'name':
                             'acme'}, label='Object')` explains both), so
                             resolving a member against only the other three
                             finds a legitimate one unmatched.
    """

    label: str
    unexplained_missing: dict[str, Any] = field(default_factory=dict)
    invented: dict[str, Any] = field(default_factory=dict)
    changed: dict[str, Any] = field(default_factory=dict)
    explained_missing: dict[str, Any] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not (self.unexplained_missing or self.invented or self.changed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "clean": self.clean,
            "unexplained_missing": sorted(self.unexplained_missing),
            "invented": sorted(self.invented),
            "changed": sorted(self.changed),
            "explained_missing": sorted(self.explained_missing),
        }


def parity_difference(cache_props: Mapping[str, Any],
                      record_props: Mapping[str, Any], *,
                      label: str) -> ParityDiff:
    """Compare a cache answer with the record's, and EXPLAIN every difference.

    This is the rule the served reads are held to (S4/S5: *"a read answered from
    the record must return the same answer as the cache path"*). It is a
    function so the rule has ONE home: the tests call it, and an audit of a
    production pair can call it without re-deciding what "the same" means.

    Five explanations, and every one of them comes from a declaration the other
    halves of the epic already made — none is re-listed here. (The clause after
    them — *"nothing else"* — is the ABSENCE rule that closes the list, not a
    sixth explanation: it says what an UNEXPLAINED difference does.) ⛔ It was
    TWO before #10853, the THIRD arrived with the narrowing that made it
    necessary (#10867 review ISSUE 1), the FOURTH with `#10394`, and the FIFTH
    with `#11747`: an asymmetry absent from this list is not explained by
    omission, it is a RED, so a declared asymmetry that this rule did not know
    about turned a sanctioned decline into the same signal as a projection gap —
    *"a signal indistinguishable from noise is not observability"*, the class
    this same change treats as P1 elsewhere.

    * **the EP carve-out** — a field absent from the record whose name the SCHEMA
      refuses (``cache_projection._refused(table)``: ``confidence``,
      ``content_hash``, ``ep_*``, …). A name the schema starts refusing is
      explained automatically; a name it stops refusing reddens the comparison
      instead of vanishing into it.
    * **a fold ``ON CREATE`` default** — a field absent from the record whose
      value is the constant the cache projection fills when the record is SILENT
      (``FOLD_ON_CREATE_DEFAULTS``, #9634).
    * **the §9.1/D1 decline (#10853)** — for a ``Source`` whose CACHE node is an
      EPISODIC source (``cache_projection.is_episodic_source_node``: either
      identity arm), the names in
      ``cache_projection.episodic_source_declined_properties()`` (``summary``,
      ``_searchText`` / ``_search_text``). The record KEEPS them and this
      projector deliberately does not materialise them, so a cache node that
      still carries them — the legacy population, and every session the current
      capture wrote — is CACHE-HELD, not a gap. ⛔ "cache-held", never
      "cache-owned": the record DOES hold this pair, so the term reserved for what
      the record cannot hold (`record_reconcile.RecordReconcileResult.graph_only_total`)
      would tell an operator there is nothing to repair (#10394 review, cycle 9).
      Scoped by the node's own
      identity, never by the label alone: a transcript DOCUMENT Source keeps its
      own ``summary``, and there the same absence IS a projection gap (leg
      ``test_parity_reddens_on_the_same_names_for_a_document_source``).
    * **a NOT-PROJECTABLE column** — a field the RECORD legitimately holds and
      the projection must not materialise (``cache_projection.
      CACHE_FORBIDDEN_COLUMNS``: §9.1/D1's narrative on ``source``/``event``,
      ``#10394``). It is DECLARED IN BOTH DIRECTIONS, because the asymmetry is
      the projection's, not a difference between the two stores:
      - a LEGACY cache node that still carries the pre-purge prose is
        ``explained``, not ``unexplained_missing``. ⛔ Without this arm it is
        reported as a **projection gap**, whose only sanctioned repair is a
        re-projection — i.e. this rule would instruct the operator to re-open
        the door the projection exists to keep shut.
      - and a RECORD row that carries the column the cache rightly lacks is
        ``explained``, not ``invented``. ⛔ Without THAT arm the raw-record
        direction reads ``invented: {story_arc}`` — *"the record path made up a
        field"* — whose only sanctioned repair is likewise a re-projection, on
        the very pair this deny exists to separate.
    * **the unserved-vector drop (`#11747`)** — for a ``label`` in
      ``cache_projection.UNSERVED_VECTOR_LABELS`` (today: ``Session`` only, the
      one record table with no ``run_vector_query`` path), the vector properties
      (``cache_projection.unserved_vector_declined_properties``) are dropped from
      the RECORD ANSWER ``node_from_row`` builds — the map ``read_node`` answers
      from — so a LEGACY cache node that still carries one is CACHE-HELD, not a
      projection gap. ⛔ Without this arm the profile that `#11747` is about
      reports ``unexplained_missing: {'embedding': …}``, whose only sanctioned
      repair is a re-projection that can no longer add the field: a permanent
      red on a correct pair, and one that contradicted ``record_reconcile`` on
      the same node (``graph_only``). Declared in both directions, on the
      §9.1/D1 pattern above, for the same reason.
    * **nothing else.** A field the cache answers and the record does not, with no
      explanation, is a projection gap — and it reddens. A field the record
      answers and the cache does not is an invention — and it reddens too, UNLESS
      it is a not-projectable column, a DECLINED name (the §9.1/D1 pair above),
      a dropped unserved vector, or a fold default. The five explanations above
      are the ONLY sanctioned asymmetries, and because all five are enumerated
      they cannot be used to hide a gap. ⚠️ FOUR of the five are mirrored into
      the ``invented`` direction (the decline, the unserved vector, and the
      not-projectable set explicitly, and a FOLD DEFAULT via its own
      ``_is_fold_default`` skip — so that arm needs no branch for it); the
      CARVE-OUT is not, so a raw record row still
      reports its own ``content_hash`` as invented — a pre-existing gap of that
      arm, filed as ``#11192`` rather than absorbed here.
      ⚠️ ``as_dict()`` is DIRECTION-BLIND by design: a legacy cache node still
      holding the prose and a record row rightly holding it both render as
      ``explained_missing: [...]``, because the caller supplied the arguments and
      so knows which direction it asked. The two need different repairs
      (on either ANCHOR a destructive clear — a full ``record-restore``'s
      whole-graph wipe (refused PRE-WIPE on a production server graph name,
      #2944's L2 guard), or a graph-side ``REMOVE n.story_arc`` no instrument
      ships today — ⚠️ and the WIPE cannot be row-scoped: ``_wipe_all_nodes`` is
      ``MATCH (n) DETACH DELETE n``, so a graph holding ANY row whose PROSE neither
      ``public.{source,event}.story_arc`` nor ``public.session_narrative.narrative``
      carries must NOT be restored at all — copy that prose out first, because the
      wipe destroys the only copy (§17.2, `#11325`); ⚠️ a ``:Point``
      copy has a NON-destructive one, **on the same per-row test** — only where the
      record still HOLDS the text for that session
      (``public.session_narrative.narrative`` — ⛔ NOT the point's own ``props``
      tail, which the clear's OWN record write consumes: ``point`` has no
      ``story_arc`` column, so the projection tails the key and the patch merges
      RIGHT-HAND, ``props = props || %s::jsonb``, so a null OVERWRITES the prose);
      where the narrative row does not carry it the
      node's copy is the only one no SHIPPED route can put back on a production
      graph (the record's KEYED view does not hold it), and the clear deletes it, because the
      ``rebuild_all`` that would put it back is refused PRE-WIPE on a production
      name:
      ``update_point(<id>, story_arc=None)`` clears the property and keeps the
      node (measured; ``record_reconcile``'s module docstring states the door);
      ``#10017``'s purge clears only TURN-SHAPED legacy episodic ``:Point``
      copies (turn shape = ``is_episodic`` + ``pointKind='event'`` +
      ``is_operator=false`` + a ``<sid>_t<digits>`` id, per
      ``tools/turn_parity_census.py``) — any other
      episodic ``:Point`` refuses its sweep rather than being cleared — vs
      nothing at all), and
      ``record_reconcile`` is where that distinction is made (``graph_only``
      versus a record-side omission), not here.

    ⚠️ The decline explains an ABSENCE FROM THE RECORD'S ANSWER, never a VALUE:
    the record's answer here is the projection's own map
    (``cache_projection.node_from_row``, the same map ``read_node`` returns), and
    the declined pair is absent from it by construction on every row where the
    decline applies — ``summary`` on every row of the EPISODIC Source class,
    ``_searchText`` too unless #10965 relaxes it — exactly the shape the
    comparison must not redden on.
    ⛔ NOT on every row: since #10965 a post-#10000 episodic row PROJECTS
    ``_searchText``, so there the name is PRESENT in that same map and this arm
    is OPERATIVE on the sanctioned path — the measured false GREEN the
    ``invented``-loop comment below describes (filed as ``#11233``).

    ⚠️ The `#11747` unserved-vector arm has the same property for the same
    reason: the vector is removed FROM THAT MAP by :func:`node_from_row` on every
    row of an unserved label, so its absence is the map's own shape and never a
    value the two sides disagreed about.

    ⚠️ A field BOTH sides carry is always compared, refused name or not. That is
    what keeps a caller's own re-derivation honest: the served endpoint puts a
    recomputed ``content_hash`` back into its response, and a wrong recomputation
    must redden here rather than hide behind the carve-out.
    """
    from .cache_projection import (
        FOLD_ON_CREATE_DEFAULTS,
        NODE_TABLES,
        _refused,
        cache_forbidden_columns,
        episodic_source_declined_properties,
        is_episodic_source_node,
        placement_spellings,
        unserved_vector_declined_properties,
    )

    table = NODE_TABLES.get(label)
    if table is None:
        raise RecordServeError(f"{label!r} has no record table")
    refused = _refused(table)
    # ⚠️ WIDENED through the placement table, because the loop below compares
    # GRAPH property names (`cache_props` keys) while `CACHE_FORBIDDEN_COLUMNS`
    # holds RECORD COLUMN names — the contract
    # :func:`cache_projection.cache_forbidden_columns` states and the `props`-tail
    # filter honours. Today's one entry (`story_arc`) spells one way, so this
    # changes no behaviour; it is here so a forbidden AMBIGUOUS column cannot be
    # explained at the tail door and reported as a projection gap HERE, which
    # would tell an operator to re-project the very field the deny keeps out.
    not_projectable = cache_forbidden_columns(table) | {
        spelled for column in cache_forbidden_columns(table)
        for spelled in placement_spellings().get(table, {}).get(column, ())
    }
    defaults = FOLD_ON_CREATE_DEFAULTS.get(label, {})
    # #10853's declared decline, resolved ONCE per comparison and only for a
    # Source: the set is graph-property names AND the raw column spellings, read
    # from the projection's ONE declaration rather than re-listed here.
    declined = (episodic_source_declined_properties()
                if label == "Source" and is_episodic_source_node(cache_props)
                else frozenset())
    # `#11747`'s declared drop, read from the projection's ONE declaration. A
    # vector on a label no query leg serves is REFUSED by the projection
    # (`UNSERVED_VECTOR_LABELS`: it is dropped from the record answer in
    # `node_from_row` and from the statement in `node_statement`), so a LEGACY
    # cache node that still carries one is CACHE-HELD — not a projection gap.
    # ⛔ The arm is REQUIRED, and it is the "declared, not silent" half of that
    # drop: without it this rule reports `unexplained_missing: {'embedding': …}`
    # on exactly the population `#11747` is about, and a projection gap's only
    # sanctioned repair is a re-projection — which can no longer add the field,
    # so the red would be PERMANENT until a wipe-and-rebuild, while
    # `record_reconcile` reports the same pair as `graph_only`. Two instruments
    # disagreeing about one node is the asymmetry this enumeration exists to
    # refuse. Resolved ONCE per comparison, like the decline above.
    declined_vectors = unserved_vector_declined_properties(label)

    def _is_fold_default(field_name: str, value: Any) -> bool:
        return field_name in defaults and value == defaults[field_name]

    unexplained: dict[str, Any] = {}
    explained: dict[str, Any] = {}
    for field_name, value in cache_props.items():
        if field_name in record_props:
            continue
        if (field_name in refused or field_name in not_projectable
                or _is_fold_default(field_name, value)):
            explained[field_name] = value
            continue
        if field_name in declined or field_name in declined_vectors:
            # §9.1/D1 decline (#10853): the record keeps the prose, the
            # projection does not materialise it, and a cache node that still
            # carries it is CACHE-HELD — never a projection gap. Not
            # "cache-owned": the record holds it (§17.5's term is for what the
            # record cannot hold; see `graph_only_total`'s retraction).
            # `#11747`'s unserved-vector drop is the SAME shape, one class over:
            # the record answer declines the vector (it can never be read), so a
            # legacy cache node holding it is cache-held, not a gap.
            explained[field_name] = value
            continue
        unexplained[field_name] = value

    invented: dict[str, Any] = {}
    for field_name, value in record_props.items():
        if field_name in cache_props:
            continue
        # ⛔ THE DECLARED ASYMMETRY IS SYMMETRIC (#10394 review, P2). The arm
        # above explains a NOT-PROJECTABLE column the CACHE still holds; the
        # record holding the same column is the state the projection exists to
        # create (text authority in Postgres, never in the graph), so its
        # absence from the cache is DECLARED, not an invention. Left out, this
        # loop reddens `clean: False, invented: ['story_arc']` on a raw record
        # row — the direction an audit of a production pair is handed — and
        # `invented`'s only sanctioned repair is a re-projection, i.e. re-opening
        # the door. Measured red-first in
        # `tests/test_record_serve.py::test_a_not_projectable_column_in_the_RECORD_is_NOT_an_invention`.
        # ⚠️ SCOPED, and the scope is narrower than it first looked — MEASURED,
        # not reasoned (#11224's review, P2): with the record side supplied THE
        # WAY THIS MODULE SAYS TO SUPPLY IT (`read_node`'s map = the projector's
        # `node_from_row` map, plus the event-stream `status` fold for the labels
        # that have one — a fold that adds no declined or not-projectable name),
        # `not_projectable` CANNOT reach this loop, and NEITHER CAN `summary`:
        # both are absent from that map by construction. ⛔ BUT `_searchText`
        # CAN — whether it is declined is decided PER ROW, and since #10965 a
        # post-#10000 episodic row PROJECTS it (`cache_projection._props_from_row`
        # relaxes the decline when the row's `summary` is the topic join;
        # `episodic_source_declined_properties`' own docstring says the set stays
        # the full pair). So on that population this arm is OPERATIVE on the
        # sanctioned path, and it FORGIVES a real gap: a cache node that lost the
        # one field the Source FTS index is declared on answers `clean`.
        # MEASURED (a post-#10000 row whose cache answer lacks `_searchText`):
        # `parity_difference(...).clean is True`, `explained_missing
        # ['_searchText']`. ⛔ That is a FALSE GREEN and it is FILED, not
        # absorbed here — `#11233` owns the fix (exclude `_searchText` from the
        # decline in this direction, or decide it per row), and until it lands
        # `record_reconcile` is the instrument that still compares the name.
        # What remains true of the arm: it is the right shape for a caller that
        # hands RAW record rows (an audit that opened the record with its own
        # SQL) — and such a caller also reds on `sessionId`-vs-`session_id`-style
        # pairs (measured), which is why `read_node`'s map stays the shape to use.
        # ⚠️ The same mirror for the §9.1/D1 DECLINE (`declined`, #10853) is
        # applied here for the same reason and by the same rule: the record keeps
        # `summary`/`_searchText` and the projection declines to materialise
        # them, so the record holding them is DECLARED, not invented. It was
        # MISSING until review cycle 2 measured it — a genuinely episodic Source
        # audit reported `invented: ['summary']` on a correct pair, the identical
        # false RED this arm exists to remove, one class over. Pinned by
        # `test_the_DECLINED_prose_in_the_RECORD_is_not_an_invention`.
        # ⚠️ ONE class is still unmirrored, and it is a PRE-EXISTING gap of this
        # arm rather than this change's: the SCHEMA-REFUSED names (`_refused`),
        # so a raw record row reports its own `content_hash` as invented. Filed
        # as #11192 and deliberately NOT fixed here — that class is not the one
        # this change introduces, and the fix is a different decision (the record
        # is not even supposed to carry those names).
        if field_name in not_projectable or field_name in declined:
            explained[field_name] = value
            continue
        # `#11747` — the MIRROR of the unserved-vector drop, for a caller that
        # hands RAW record rows (an audit that opened the record with its own
        # SQL): the projection declines to store the vector, so the record row
        # carrying it is DECLARED, not invented. `invented`'s only sanctioned
        # repair is likewise a re-projection, i.e. re-opening the door the drop
        # exists to keep shut.
        if field_name in declined_vectors:
            explained[field_name] = value
            continue
        # A fold `ON CREATE` default fills a field the record row is SILENT
        # about (#9634): a cache-side constant, not an invention.
        if _is_fold_default(field_name, value):
            continue
        invented[field_name] = value

    changed: dict[str, Any] = {}
    for field_name, value in record_props.items():
        if field_name not in cache_props:
            continue
        if cache_props[field_name] != value:
            changed[field_name] = {"cache": cache_props[field_name],
                                   "record": value}

    return ParityDiff(label=label, unexplained_missing=unexplained,
                      invented=invented, changed=changed,
                      explained_missing=explained)
