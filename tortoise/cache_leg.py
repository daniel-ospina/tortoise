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

from . import monitoring

__all__ = [
    "CACHE_NOT_SHARED",
    "cache_leg_reason",
    "note_local_cache",
]

_LOGGER = logging.getLogger(__name__)

#: The record is armed (a SHARED store) and this process's cache is local.
#: The remediation is per-shape and the warning below spells both out.
CACHE_NOT_SHARED = "cache_not_shared"

#: Reasons already rendered as a warning by THIS process — see the module
#: docstring for why the latch exists (one SDK per request on the hosted lane).
_WARNED: set[str] = set()


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
    coherent one, including the three that must keep working unchanged: an
    unarmed record (#7722's onboarding lane), a declared single-tenant
    deployment (both stores local to one box, by the operator's own statement),
    and the test lane's redirect onto an ambient server.
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
