"""The CACHE leg's own legibility — a write must reach the cache OF the record (#12943).

THE DEFECT THIS EXISTS FOR, MEASURED. A deployment arms the record with
``TORTOISE_RECORD_DSN`` and leaves ``TORTOISE_DB_URI`` empty. Two INDEPENDENT
variables then choose the two stores, and nothing compares them: the record
goes to a shared Postgres while ``hosted_api._make_sdk`` takes its embedded
fallback (#7722) and the cache becomes a process-local redislite file. Every
write returns success, the record holds the rows, and the cache it was
supposedly derived into holds nothing.

Reproduced on a throwaway tenant, one write through the hosted data plane's own
seam (``hosted_api._make_sdk(graph_name=…)`` + ``sdk._bind_record_tenant``),
counted in both stores:

===================================  ==================  ================
                                     ``TORTOISE_DB_URI``  empty
===================================  ==================  ================
record (``public.object``/``point``)  2 / 3               2 / 3
cache graph ``org_<org>_<gid>``       2 / 3               0 / 0
``record_writes_applied``             0 → 6               0 → 6
``cache_leg_failures`` (this module)  ``{}``              ``{"cache_not_shared": 2}``
===================================  ==================  ================

The second column is #12943's own shape for its IN-PROCESS writes (8 objects /
39 events / 5 operators in Postgres and none of them in FalkorDB), and it is not
a lost write: it is a write that landed, in full, in a store the record is not
in. Nothing counted it, nothing warned, and no caller could tell — note that
``record_writes_applied`` is IDENTICAL in both columns, which is why nothing
downstream could tell them apart until the last row existed.

⚠️ The issue's cache column is **1**, not 0, and the difference is the
diagnosis: that one object was written through REST (`POST /v1/objects`) by the
DEPLOYED app, so the app had a shared cache and only the in-process writer did
not reach it. The split is per PROCESS — a write from a process with no
`TORTOISE_DB_URI` files truth in a shared Postgres and cache in a local file.
(That is an inference from the issue's numbers plus this code path; it is not a
measurement of the deployment, which this tree cannot reach. The counter below
is what confirms it — but read it on the process that PERFORMED the write: a
process with a shared cache reports ``{}`` BY CONSTRUCTION, so the app's own
reading falsifies nothing about a same-deployment worker.)

WHY A COUNTER AND A WARNING, AND NOT A REFUSAL. ⛔ Two recorded decisions
bound this module, and neither is reversed here:

* **``#7722`` — the embedded fallback is DELIBERATE.** A hosted process with no
  URI still has to onboard, so ``_make_sdk`` keeps working without one. The
  fallback is not the fault; using it as the cache OF A SHARED RECORD is.
* **A single-tenant self-host is a legitimate shape.** ``TORTOISE_RECORD_DSN``
  arming a Postgres that lives ON THE SAME BOX as an embedded graph is coherent:
  both stores are local to that deployment, so the cache really is the record's
  cache. ``record_writer.single_tenant_declared`` is the operator's statement of
  exactly that (#9192), and it is what distinguishes the coherent case from the
  split one.

So the incoherence is narrow and nameable: **record armed + cache process-local
+ tenancy NOT declared single**. Refusing on it was rejected for a measured
reason: ``test-record-postgres`` deliberately exports no ``TORTOISE_DB_URI``
(its job comment says so — that job is the RECORD's leg), so ~192 tests run in
exactly this shape on purpose and a raise would red the whole job for a cache
they are not testing. The issue's own instruction is the right one — *"make the
failure observable before making it rare"* — and this is that observable.

WHAT A READER CAN RELY ON. The counter is cumulative and per-reason
(``monitoring.cache_leg_failure_counts()``, surfaced on
``monitoring.metrics()["cache_leg_failures"]``), so a zero there means no cache
leg was found misrouted — never that no write happened (that is
``record_writes_applied``'s question, one key above). The warning is emitted
ONCE PER PROCESS PER REASON: ``TortoiseSDK`` is constructed per request on the
hosted lane, so an unlatched warning would write one line per request into the
log of the very deployment an operator is trying to read.
"""
from __future__ import annotations

import logging
import os
import re
import threading

from . import monitoring

__all__ = [
    "CACHE_NOT_SHARED",
    "CACHE_TARGET_OVERFLOW",
    "TENANT_GRAPH_MISMATCH",
    "cache_leg_reason",
    "cache_store_fingerprint",
    "cache_write_targets",
    "note_cache_write_target",
    "note_local_cache",
    "note_tenant_graph",
    "reset_cache_write_targets",
    "tenant_graph_reason",
]

_LOGGER = logging.getLogger(__name__)

#: The record is armed (a SHARED store) and this process's cache is local.
#: The remediation is per-shape and the warning below spells both out.
CACHE_NOT_SHARED = "cache_not_shared"

#: A write was filed under ``(org_id, graph_id)`` while the cache graph it folded
#: into is a PER-GRAPH name for a DIFFERENT id (``org_<org>_<gid>`` /
#: ``team_<org>_<gid>``) — the record's tenant and the cache's graph disagree
#: about WHICH graph this write belongs to. The one shape of the split that is
#: decidable from inside the process without reading either store.
TENANT_GRAPH_MISMATCH = "tenant_graph_mismatch"

#: The bucket a write target is filed under once ``_TARGETS`` is full. The
#: registry is BOUNDED on purpose: a hosted process serves one graph per tenant,
#: and an unbounded per-graph map is a memory leak with a monitoring label on it.
CACHE_TARGET_OVERFLOW = "(other targets)"

#: How many distinct ``store|graph`` pairs the process remembers. Sixteen covers
#: every deployment shape this repo runs (one shared cache, one embedded file,
#: the registry graph) with room for the handful of graphs one process writes on
#: a healthy day.
#:
#: ⚠️ WHAT THE CAP COSTS, stated so it is not over-read: past sixteen distinct
#: targets a further one is folded into :data:`CACHE_TARGET_OVERFLOW`, so a
#: tenant's own target is only visible when it was among the first sixteen this
#: process wrote. The failing direction is therefore "a big multi-tenant worker
#: reports `(other targets): N` instead of N graph names" — it never hides a
#: target for a process small enough to read, and the overflow count itself is
#: the signal that this process is past the registry's resolution.
_TARGETS_CAP = 16

#: ``{"<store>|<graph>": live write attempts}`` for THIS process, and its lock.
#: Written by :func:`note_cache_write_target` from the five live lanes — the fold,
#: the structural-edge mint, and the three declared gateways — so the UNIT is one
#: lane operation (a fold, a mint, a gateway statement), not one journal record.
#: Read it for WHERE a process writes; do not read it as "writes landed", because
#: an arm whose fold returns no count is counted even when its statement matched
#: 0 rows (the bound is stated on `note_cache_write_target`).
_TARGETS: dict[str, int] = {}
_TARGETS_LOCK = threading.Lock()

#: Reasons already rendered as a warning by THIS process — see the module
#: docstring for why the latch exists (one SDK per request on the hosted lane).
_WARNED: set[str] = set()

#: ``org_<org>_<gid>`` / ``team_<org>_<gid>`` — the two per-graph namespace
#: families this tree mints (``supabase_control``/``_provision_graph`` and the
#: pre-#3543 ``team_`` form). Only their TAIL is read, and only to answer "does
#: this graph name carry a graph id at all?" — a name that does not carry one
#: (the org-level ``org_<org>``, ``tortoise``, the registry graph) cannot be
#: graded here and is deliberately left ungraded rather than guessed at.
_GRAPH_ID_IN_NAME = re.compile(r"(?:^|_)(g_[A-Za-z0-9]+)$")


def _record_armed() -> bool:
    """Is the record armed in this process? Presence-based, never a connection.

    ``record_writer.record_dsn()`` is NOT used here on purpose: it RAISES on a
    malformed DSN, and this predicate is called from an SDK constructor, where
    raising would hand a caller an exception for a graph it can still use. The
    arming rule itself is presence-based (``record_writer``'s decision 2: "Unset
    or blank ⇒ NOT_ARMED"), so presence is the whole question — a MALFORMED
    value counts as armed, which is correct here: it is still a shared store
    this process has been told to write to.
    """
    from .record_writer import RECORD_DSN_ENV

    return bool((os.environ.get(RECORD_DSN_ENV) or "").strip())


def _redirected_to_a_server() -> bool:
    """Is a process-local construction about to be REDIRECTED to a server?

    ``projection.FalkorProjection.__init__`` rewrites an explicit ``db_path``
    construction onto the ambient ``TORTOISE_DB_URI`` server under
    ``TORTOISE_TEST_MODE=1`` (epic #1647's docker-lane redirect). So in that
    lane an SDK whose ``_db_uri`` is ``None`` is NOT writing a local store, and
    counting it would report a split that does not exist. The gate is mirrored
    here rather than imported because the projection decides it inside
    ``__init__`` from the CALLER FRAME; this predicate answers the coarse
    process-level question, which is all a constructor-time check can ask.

    Outside the test lane the redirect never applies (it is gated on
    ``TORTOISE_TEST_MODE``), so a production embedded construction is always
    counted.

    ⚠️ RESIDUAL, and the direction matters: this is a strict SUBSET of the real
    gate (which also requires an explicit path, a supported scheme, and a test
    caller frame absent from ``TORTOISE_TEST_NO_REDIRECT``). So it can only
    UNDER-count — under ``TORTOISE_TEST_MODE=1`` with a URI set whose
    construction is NOT redirected (a carve-out stem, a subprocess CLI child
    with no test frame, an unsupported scheme) a genuinely-embedded SDK is
    silently exempted. It never OVER-counts: production does not set
    ``TORTOISE_TEST_MODE``, so no deployed process is exempted by this arm.
    Mirroring the gate exactly was rejected because it reads the CALLER FRAME,
    which is a per-construction fact a process-level constructor check cannot
    ask; the miss is therefore named here instead of pretended away.
    """
    if os.environ.get("TORTOISE_TEST_MODE") != "1":
        return False
    return bool((os.environ.get("TORTOISE_DB_URI") or "").strip())


def cache_store_fingerprint(endpoint: object) -> str:
    """A credential-free identity for the store a projection writes to.

    ``endpoint`` is ``FalkorProjection._stable_endpoint()``'s answer —
    ``("embedded", path)`` or ``("server", host, port)`` — so the identity is
    the projection's OWN, not a second reading of the environment. That matters:
    the whole #12943 class is "two variables chose two stores", and a fingerprint
    derived from the variable would agree with itself while the write went
    elsewhere.

    No credential is ever rendered (same rule as the record's
    ``dsn_fingerprint``): a target list is a health payload and a log line.
    """
    try:
        parts = tuple(endpoint or ())
    except TypeError:
        return "unknown"
    if not parts:
        return "unknown"
    kind = parts[0]
    if kind == "embedded" and len(parts) > 1:
        return f"embedded:{parts[1]}"
    if kind == "server" and len(parts) > 1:
        host = parts[1]
        port = parts[2] if len(parts) > 2 else None
        return f"server://{host}" + (f":{port}" if port else "")
    return ":".join(str(p) for p in parts)


def note_cache_write_target(endpoint: object, graph_name: str | None) -> str:
    """Record one LIVE write ATTEMPT against the store+graph it reached.

    Five call sites, and the list is maintained rather than implied:
    ``projection.note_live_write_target`` is the ONE helper they all go through,
    so the endpoint resolution has a single owner.

    * ``FalkorProjection.apply`` — the fold every door passes (SDK, REST, MCP,
      capture, the metadata lanes), counted once per fold that LANDED (a fold that
      provably wrote nothing is not a write);
    * ``_record_structural_edges`` — ``create_edge`` and the about-lane mint
      their edges with a raw ``MERGE`` under ``_structural_edge_write_scope`` and
      never pass ``apply``, so without that hook a process whose writes are edges
      registers NO target (the same "{} means healthy" illusion, one lane over);
    * ``onboarding.state._run``, ``webhook_delivery._run``,
      ``why_snapshot._run`` — the three declared live GATEWAYS that issue their
      own statements under ``_onboarding_write_scope`` /
      ``_cache_write_authorized(<lane>)`` and fold no record at all.

    A sixth gateway added tomorrow must be added here; a raw statement issued at
    ``proj.g`` from somewhere else is the named residual (see the helper).

    ⛔ THE RESIDUAL IS NAMED RATHER THAN IMPLIED: a raw statement issued at
    ``proj.g`` inside a live scope — the capture provenance stamps
    (``_CAPTURE_PROVENANCE_LANE``) are the standing example — reaches the graph
    without passing either lane and is NOT counted. That is accepted because such
    a writer does not CHOOSE its graph (it stamps nodes by id in the graph the
    SDK is already bound to), so it cannot hide the split this registry exists to
    expose; a lane that could choose would have to go through a fold or a mint.
    Closing it belongs at the write handle (``_GuardedGraph``), where a
    per-statement census already exists (§17.1), not here.

    Replay is excluded by both callers: a rebuild WRITES the target on purpose,
    and counting it would let a repair make the picture look healthier than the
    live traffic it repaired.

    WHAT THIS ANSWERS THAT ``cache_leg_failures`` CANNOT. ``cache_leg_failures``
    counts a CONFIGURATION split (record armed + this process's cache is the
    embedded file), decided once per construction. It is blind to every other way
    the two copies can diverge — a cache URI naming a DIFFERENT server, a
    credential class that opens a different GRAPH (measured: an org-wide
    credential opens ``org_<org>`` while the record files the same write under
    ``org_<org>_<gid>``), a per-request construction that binds a tenant it did
    not resolve. This records the fact those share: **the store and graph each
    write actually reached**, per process, so "the record has 8 and the cache you
    looked at has 0" becomes "this process wrote 8 to THAT target" — one number
    to compare, whichever of the shapes caused it (#12943 reopening).

    A COUNT IS NOT A VERDICT, AND IT IS NOT A WRITE COUNT. The unit is one lane
    operation — a fold, a mint, a gateway statement — so it answers WHERE a
    process writes, which is the question the split turns on; an arm whose fold
    returns no count is still counted when its statement matched 0 rows (see
    `FalkorProjection._apply_event`'s `_skip` bound), so the number is an upper
    bound, deliberately erring that way rather than under-reporting. Two targets
    in one process is normal for a deployment that writes the registry graph as
    well as tenant graphs. What is pathological is the TENANT's target absent
    from this list — or present under a store the deployment does not read.

    Deliberately cannot raise (#5148's rule — observability must never deny a
    write that already landed): the caller folds inside a live scope and the
    fold's failure is the caller's problem, not this function's.
    """
    try:
        key = f"{cache_store_fingerprint(endpoint)}|{graph_name or '?'}"
        with _TARGETS_LOCK:
            if key in _TARGETS or len(_TARGETS) < _TARGETS_CAP:
                _TARGETS[key] = _TARGETS.get(key, 0) + 1
            else:
                _TARGETS[CACHE_TARGET_OVERFLOW] = (
                    _TARGETS.get(CACHE_TARGET_OVERFLOW, 0) + 1)
        return key
    except Exception:  # noqa: BLE001, RUF100 — never deny a write that landed
        return "unknown"


def cache_write_targets() -> dict[str, int]:
    """Snapshot of this process's live-write targets (``store|graph`` → writes).

    Copied under the lock so a caller iterating it cannot see a concurrent
    increment land mid-render.
    """
    with _TARGETS_LOCK:
        return dict(_TARGETS)


def reset_cache_write_targets() -> None:
    """Clear the registry. TESTS ONLY — production never resets a total.

    A cumulative total that resets cannot be alerted on, which is why the
    counters beside this one have no reset either; the registry is a snapshot of
    WHERE writes go rather than a total to alert on, and tests need a clean
    baseline to distinguish "this fold counted" from "an earlier one did".
    """
    with _TARGETS_LOCK:
        _TARGETS.clear()


def tenant_graph_reason(*, cache_graph: str | None,
                        graph_id: str) -> str | None:
    """Does the cache graph name DISAGREE with the record tenant? Pure.

    ``cache_graph`` is the name this SDK will fold into
    (``sdk._cache_graph_name()`` — the projection's own derivation, computed
    without opening anything). Returns :data:`TENANT_GRAPH_MISMATCH` only when
    the name carries a graph id and it is not the tenant's.

    ⛔ IT IS DELIBERATELY SILENT ON THREE SHAPES, and the silence is the design:

    * **a name with no graph id** — the org-level ``org_<org>`` (the DEFAULT
      graph's namespace, and what an org-wide credential legitimately opens for
      a tenant that IS the default graph), ``tortoise``, ``registry_tortoise``.
      Nothing in a constructor can tell "the default graph of this org" from "a
      custom graph, opened at the org level" — that needs the control plane — so
      guessing would count a coherent hosted org-wide request as a fault and
      drown the signal. The TARGET registry above is what makes that shape
      visible instead of silently trusted;
    * **no binding decided yet** (``cache_graph`` falsy) — #12950's arm owns the
      construction-level split (``cache_not_shared``), and reporting it twice
      under two names would make one defect look like two;
    * **a graph id that MATCHES** — the ordinary coherent write.

    What is left is the measured misroute: a write whose record row says
    ``org_<org>_g_A`` and whose cache node is in ``org_<org>_g_B``. That is not a
    lag, not a residency miss, and not repairable by a replay into the graph the
    reconciler reads.

    ⚠️ It takes NO ``org_id``, and that is deliberate rather than an omission: the
    ``<org>`` half of ``org_<org>_<gid>`` is not unambiguously parseable (org ids
    may themselves contain underscores and a ``g_``-shaped segment is not
    excluded), and a predicate that advertises an org comparison it cannot make
    would be read as checking one. Graph ids are unique across orgs, so the
    graph-id half is the whole decidable question.
    """
    if not cache_graph:
        return None
    name = str(cache_graph)
    match = _GRAPH_ID_IN_NAME.search(name)
    if match is None:
        return None
    if match.group(1) == graph_id:
        return None
    return TENANT_GRAPH_MISMATCH


def note_tenant_graph(*, cache_graph: str | None, org_id: str,
                      graph_id: str) -> str | None:
    """Count (+ warn once) a tenant bound to a cache graph of ANOTHER graph.

    Called from ``TortoiseSDK._bind_record_tenant`` — the by-value carrier the
    hosted REST lane binds in ``_data_sdk`` and the MCP lane installs as a
    scope — so it grades the moment the two answers become comparable, before
    any write can file a row under one and a node under the other.

    Never raises, same reason as its siblings: a fault in observability must not
    refuse a request that can still be served.
    """
    try:
        reason = tenant_graph_reason(cache_graph=cache_graph,
                                     graph_id=graph_id)
        if reason is None:
            return None
        monitoring.record_cache_leg_failure(reason)
        if reason not in _WARNED:
            _WARNED.add(reason)
            _LOGGER.warning(
                "the record tenant and the CACHE graph disagree: this SDK "
                "writes the cache graph %r while the record will file the same "
                "write under graph_id %r (org %r). Nothing is lost — the write "
                "lands in a graph the record's own reconciler will not look "
                "at, so `record-reconcile` reports the tenant missing while "
                "this process reports success (#12943, counted on "
                "monitoring.metrics()['cache_leg_failures']). Fix: open the SDK "
                "for the tenant's own graph (hosted: `_data_sdk`; a script: "
                "`_make_sdk(graph_name='org_<org>_<gid>')`) and bind the tenant "
                "you actually resolved.",
                cache_graph, graph_id, org_id,
            )
        return reason
    except Exception:  # noqa: BLE001, RUF100
        return None


def cache_leg_reason(*, cache_uri: str | None) -> str | None:
    """Why this process's cache cannot be the cache OF its record, or ``None``.

    Pure: it reads the environment and its argument, touches no store, and is
    the ONE declaration of the condition (``TortoiseSDK.__init__`` calls it; the
    tests call it directly, so the predicate and the pin cannot drift).

    ``cache_uri`` is the SDK's resolved ``TORTOISE_DB_URI`` — the value that
    decided host-vs-embedded one line earlier. A falsy value means embedded.

    ⚠️ **A truthy ``cache_uri`` is PRESENCE, not LOCALITY — the one split this
    predicate cannot see.** A deployment with a shared Postgres and
    ``TORTOISE_DB_URI=redis://localhost:6379`` (a sidecar, a per-container cache,
    a single-node Redis on the same host) IS the split #12943 measured, and it
    reports ``None`` here: something names a cache, and nothing in a constructor
    can tell whether the server behind it is shared with the record. Named rather
    than silently over-trusted — narrowing it (treating a loopback/sidecar cache
    host as the split shape) is its own decision, because a genuinely
    single-tenant self-host runs both stores on ONE box and must keep reading
    ``None``.

    Returns :data:`CACHE_NOT_SHARED` for the split shape and ``None`` for every
    coherent one — FOUR of them: a shared cache (the first arm below), an unarmed
    record (#7722's onboarding lane), a declared single-tenant deployment (both
    stores local to one box, by the operator's own statement), and the test
    lane's redirect onto an ambient server. ⚠️ There is deliberately no
    "graph unbound" member: this predicate has no graph-binding condition, so a
    construction that binds no graph IS counted.
    """
    if cache_uri:
        return None
    if not _record_armed():
        return None
    if _redirected_to_a_server():
        return None
    from .record_writer import single_tenant_declared

    if single_tenant_declared():
        return None
    return CACHE_NOT_SHARED


def note_local_cache(*, cache_uri: str | None,
                    cache_path: str | None) -> str | None:
    """Count (+ warn once) that this process's cache is not the record's store.

    Called from ``TortoiseSDK.__init__`` — the one constructor every write door
    passes through, so SDK direct callers, the hosted REST lane
    (``hosted_api._data_sdk`` / ``_make_sdk``) and the MCP lane
    (``mcp_auth._get_org_sdk``) are all covered by ONE call site rather than
    three that can drift.

    ``cache_uri`` is passed THROUGH to the predicate rather than assumed: the
    caller's own ``if self._db_uri is None`` is a cheap short-circuit, and the
    moment it becomes the only thing standing between a SHARED cache and a
    counter increment, the predicate's shared-cache arm is dead on the deployed
    path and this module's "one declaration" claim is false. It is also what a
    second caller (a moved hook, a new door) would otherwise get wrong.

    Deliberately cannot raise: a fault in observability must never deny a
    caller a graph it can use (the ``#5148`` rule this seam's siblings follow).
    """
    try:
        reason = cache_leg_reason(cache_uri=cache_uri)
        if reason is None:
            return None
        monitoring.record_cache_leg_failure(reason)
        if reason not in _WARNED:
            _WARNED.add(reason)
            _LOGGER.warning(
                "the CACHE leg of this process is not the record's store: "
                "TORTOISE_RECORD_DSN is armed (a shared Postgres) while this "
                "SDK's cache is the embedded store %r. The RECORD will hold "
                "every write and the RECORD's cache will hold none of them — "
                "nothing is lost, it is stranded in a store the record is not "
                "in — and every call still returns success (#12943, counted on "
                "monitoring.metrics()['cache_leg_failures']). Fix: set "
                "TORTOISE_DB_URI to the shared FalkorDB this deployment reads "
                "from — or, on a SINGLE-TENANT self-host where both stores are "
                "meant to be local, declare it with TORTOISE_RECORD_SINGLE_TENANT=1 "
                "plus TORTOISE_RECORD_ORG_ID/TORTOISE_RECORD_GRAPH_ID.",
                cache_path,
            )
        return reason
    except Exception:
        return None
