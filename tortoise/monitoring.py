"""GAP-09b #6996: Health checks + Prometheus metrics + cost tracking.

#7395: Auth-gated — requires Bearer token when TORTOISE_API_KEY is set.
Binds 127.0.0.1 by default (not 0.0.0.0).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import math
import os
import queue
import socket
import socketserver
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from prometheus_client import Counter, Histogram, generate_latest

from .env_truthy import is_truthy  # #4097: the declared truthy contract

logger = logging.getLogger(__name__)

# Auth functions imported lazily (in _Handler.do_GET) to avoid
# triggering TORTOISE_SECRET_PEPPER requirement at module import time (#67).

_start = time.monotonic()
_last_ingest: float | None = None
_sdk = None  # set by register()

# Per-ATTEMPT bound on the deep DB probe (#1384): a stopped FalkorDB (incident
# #1381 — NXDOMAIN with /health staying ok) must flip /health to degraded
# within a sub-second-to-1.5s window, never hang the handler. This bounds ONE
# attempt. The #1565 retry does NOT take a SECOND bound of this size — it
# rides the REMAINDER of the caller's one deadline (see ``probe_db``), so the
# platform liveness shape's real total is ~``PROBE_TIMEOUT``, and the #3143
# explicit-allowance shape's total is ``PROBE_SETUP_TIMEOUT + PROBE_TIMEOUT``.
# ``PROBE_DB_TOTAL_TIMEOUT`` (2 x this + the retry delay) survives ONLY as the
# loose outer-alignment figure for the platform plane — an over-estimate of
# this shape, not its exact total. Quoting the per-attempt figure as the total
# is the trap that produced an inverted coordinator ordering in the
# #2850/#2988 merge.
PROBE_TIMEOUT = 1.5

# #3143: the probe has TWO phases and only the second is a reachability signal.
#
#   (1) projection cold-start — ``sdk._get_proj()``: connect + the FalkorDB
#       version probe + ``_ensure_indexes()``. That is ~28 sequential round
#       trips, and when an index is missing it BUILDS the index over the whole
#       graph — cost that scales with graph size and server load. It is paid in
#       full on every call that starts from a fresh SDK, which is exactly what
#       mcp_server.tortoise_health does (request-scoped team SDK per call).
#   (2) the ``RETURN 1`` reachability query — sub-millisecond.
#
# Bounding (1)+(2) with PROBE_TIMEOUT made a large, fully-reachable graph time
# out during SETUP and report ``db.ok=false`` / ``status=degraded`` /
# ``graph_size=0`` — the onboarding gate lie (#2202's symptom class). This is
# the default cold-start allowance. It is opt-in, because it deepens the total
# to ``setup_timeout + PROBE_TIMEOUT``: only a caller that can afford it may
# pass it.
#
# WHO MAY SPEND IT — the deciding line is whether the probe IS the request's
# answer, not request-path vs background (#2988/#3243):
#   * the on-demand MCP health tool passes it: the probe IS the answer (its only
#     job is "is the served graph reachable?"), so the deep budget is correct;
#   * NEVER a request-path liveness GATE (a `/health` handler that probes
#     inline) — there the fast <1.5 s degrade contract must hold;
#   * a BACKGROUND liveness REFRESHER passes it. The selfhost ``/health``
#     coordinator does (#2988): its request path reads an in-memory snapshot and
#     cannot be slowed by the allowance, so the deep budget buys a correct
#     verdict for a cold large graph at zero gate latency (#3243);
#   * a REQUEST-PATH liveness probe must NOT. The standalone ``serve_health``
#     ``/health`` handler keeps ``setup_timeout=None``, because there the
#     allowance *is* a slower gate — exactly the trade #3243's notes forbid.
#     The same reasoning keeps the hosted ``/health/ready`` coordinator on the
#     tight bound, since its request path waits on the verdict.
#
# The selfhost liveness coordinator is currently the ONLY background spender
# of this allowance. The hosted ``/health`` coordinator (``hosted_api.
# _HEALTH_PROBE``) also reads in-memory, but deliberately keeps
# ``setup_timeout=None``/``DB_PROBE_HARD_TIMEOUT``: its probe reuses ONE cached
# connection (``hosted_api._probe_sdk``), so the cold start is paid at most once
# and its tight bound is already coherent — extending the allowance there is a
# separate decision owned by the hosted health lineage (#3070/#3062), not a
# consequence of this rule. Do not "fix" it by analogy.
# Both phases stay bounded (the worker is
# abandoned on overrun); the caller is never blocked past its budget.
PROBE_SETUP_TIMEOUT = 20.0

#: Accepted range for the operator override. Out-of-range values are rejected
#: in favour of the default (with a warning), never clamped and never honoured:
#:
#: * below ``PROBE_TIMEOUT`` the allowance is TIGHTER than the platform
#:   liveness gate's own cold-start budget: a cold-start the platform gate
#:   would have covered now fails, reproducing #3143's false-degrade. The tool
#:   is not uniformly stricter — an explicit allowance also buys the query a
#:   fresh ``PROBE_TIMEOUT``, so the query phase can be MORE permissive than
#:   ``/health``'s shared budget — but that compensation is not a reason to
#:   accept the value: the cold-start bound is the one this knob exists to set,
#:   so a value below the gate's own budget is a misconfiguration, not a tuning.
#: * above the max, a typo (``3000``) would pin an on-demand tool call for tens
#:   of minutes.
#:
#: Both bounds are inclusive. ``1.5`` is accepted because it IS the gate's own
#: setup budget — but it is the FLOOR, not a safe value for a large graph: the
#: #3143 shape (a cold-start over 1.5s) still times out there. And the explicit
#: form always hands the query its own fresh ``PROBE_TIMEOUT``, so the total is
#: ``setup_timeout + PROBE_TIMEOUT`` — never ``/health``'s single shared budget.
PROBE_SETUP_TIMEOUT_MIN = PROBE_TIMEOUT
PROBE_SETUP_TIMEOUT_MAX = 300.0


def probe_setup_timeout() -> float:
    """Resolve the #3143 cold-start allowance.

    Spent by the on-demand MCP health tool, and — off the request path only —
    by the selfhost liveness coordinator's refresher (#2988/#3243; see
    ``PROBE_SETUP_TIMEOUT`` for the request-path-vs-background rule).

    Read at CALL time, not import time, for two reasons:

    * ``mcp_server._load_dotenv()`` runs AFTER ``tortoise.monitoring`` is
      imported (monitoring is imported at ``tortoise.sdk`` module scope), so an
      import-time read would silently ignore ``TORTOISE_PROBE_SETUP_TIMEOUT``
      set in the repo-root ``.env`` — the very surface ``.env.example``
      documents the knob on.
    * A malformed value must never brick ``import tortoise.sdk`` (and with it
      the CLI, MCP server and daemon). Blank/unset SILENTLY uses the default
      (the shipped ``.env.example`` line is blank-valued, so warning on it
      would warn on every default deployment); non-numeric, non-finite and
      out-of-range values fall back to the default WITH a warning — the repo's
      existing tolerant env convention (``rerank._env_float``).
      The accepted range is ``[PROBE_SETUP_TIMEOUT_MIN, PROBE_SETUP_TIMEOUT_MAX]``
      — see the constants above for why sub-PROBE_TIMEOUT values are rejected
      rather than honoured.
    """
    raw = (os.environ.get("TORTOISE_PROBE_SETUP_TIMEOUT") or "").strip()
    if not raw:
        return PROBE_SETUP_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("TORTOISE_PROBE_SETUP_TIMEOUT=%r is not a number — "
                       "using %ss", raw, PROBE_SETUP_TIMEOUT)
        return PROBE_SETUP_TIMEOUT
    if not math.isfinite(value) or not (
            PROBE_SETUP_TIMEOUT_MIN <= value <= PROBE_SETUP_TIMEOUT_MAX):
        logger.warning("TORTOISE_PROBE_SETUP_TIMEOUT=%r is outside "
                       "[%s, %s] — using %ss", raw, PROBE_SETUP_TIMEOUT_MIN,
                       PROBE_SETUP_TIMEOUT_MAX, PROBE_SETUP_TIMEOUT)
        return PROBE_SETUP_TIMEOUT
    return value

# ── #2850 (P0 liveness/readiness decouple) ────────────────────────────────
#
# Incident: 2026-09-10, api.premiselabs.co unreachable ~35 min. Fly logged
# "[PR01] no known healthy instances", CHECKS 0/1, while the process was idle
# (/proc/loadavg 0.05) and 127.0.0.1:8000/health answered ok. The public
# route died because Fly's http_check (targeting /health) could no longer be
# served in time.
#
# The two structural hazards this module now removes:
#   1. `_probe_once` built a NEW ThreadPoolExecutor per probe and abandoned
#      its worker with `shutdown(wait=False)` on timeout. A black-holed
#      FalkorDB (connect never returns) leaked one OS thread PER probe — the
#      repro in tests/test_monitoring.py shows +10 threads for 10 hung calls.
#   2. The /health handler rode the SHARED asyncio default executor
#      (`asyncio.to_thread`). Every stalled DB call on the request path holds
#      a worker for the redis socket timeout (5s connect + 10s read); once
#      enough were occupied, /health queued behind them and blew the 15s.
#
# `PROBE_STALE_AFTER` is when an in-flight probe is presumed wedged and its
# last good result must stop being reported as live. `PROBE_HARD_TIMEOUT` —
# the ``HealthProbe`` constructor DEFAULT — is defined below next to
# ``PROBE_DB_TOTAL_TIMEOUT``, because the default is DERIVED from that loose
# outer-alignment over-estimate of the DB probes' platform-shape total rather
# than chosen independently (the pre-#2988 2.0s literal sat below the 3.1s
# total — see PROBE_HARD_TIMEOUT).
# Deriving it lifts the default above the part of the inner path that IS
# statically bound. Since #3446 that part is the WHOLE DB probe path: the SDK
# acquisition is an enforced phase too (``probe_db(acquire=…)``), so for a
# caller that hands its acquisition in, the outer>inner ordering IS provable as
# an inequality between enforced deadlines — see the guarantee summary at
# ``PROBE_MAX_SUPERSEDES`` for exactly what that does and does not cover.
# ``PROBE_STALE_AFTER`` itself is about freshness, not bounds.
PROBE_STALE_AFTER = 30.0
# A wedged probe worker may be superseded at most this many times per WEDGE
# EPISODE — enough to notice a genuine recovery. NOT a process-lifetime cap:
# ``HealthProbe._run`` resets the counter to 0 on any live completion (a
# completion proves the wedge cleared), so a backend that wedges, recovers and
# wedges again can strand up to this many threads per episode, without limit
# over the process lifetime.
#
# WHAT THIS DESIGN ACTUALLY GUARANTEES — stated ONCE here; everything else
# cross-references it rather than restating it:
#   (a) the outer COORDINATOR bound caps how long the event loop / a readiness
#       verdict WAITS. This is the load-bearing property, and it holds.
#   (b) each coordinator is SINGLE-FLIGHT for its CALLERS: concurrent
#       run()/snapshot() calls JOIN the one in-flight probe instead of
#       starting their own. The SOLE way a second probe starts is an explicit
#       SUPERSEDE of a wedged one, capped by (d) — so "at most one probe runs
#       at a time" is FALSE while a wedge is being superseded (verified: 2-3
#       probes run concurrently during successive supersedes).
#   (c) a worker that outlives the outer bound is STRANDED only until its
#       underlying call returns on its own. Its own socket timeouts / retry
#       exhaustion bound that IN PRACTICE, not by design.
#   (d) ``PROBE_MAX_SUPERSEDES`` caps supersede STARTS per wedge episode (the
#       counter resets on any live completion). It does NOT bound how many
#       threads are stranded SIMULTANEOUSLY: a completion resets the counter
#       but does not un-strand workers already abandoned, so they accumulate
#       across episodes until each one's underlying call returns per (c). Nor
#       is it a process-lifetime cap.
#
# The DB-PLANE INNER PATH is now a SUM OF ENFORCED DEADLINES (#3446), so the
# layered timeout above it is an inequality between quantities the code
# actually imposes rather than an approximation of a worst case:
#
#   * SDK acquisition — ``probe_db(acquire=…)`` bounds the phase at
#     ``PROBE_SDK_ACQUISITION_BUDGET`` on the shared probe worker;
#   * projection cold start — ``setup_timeout`` (``_probe_once``);
#   * ``RETURN 1`` reachability query, plus the one #1565 retry riding the
#     remainder — the caller's single ``timeout``.
#
# ``PROBE_HARD_TIMEOUT`` (and therefore ``hosted_api.DB_PROBE_HARD_TIMEOUT``
# and selfhost's liveness bound) is DERIVED strictly above the sum of those
# enforced terms for each caller's shape (platform total ~``PROBE_TIMEOUT``;
# the #3143 explicit-allowance shape's is ``setup_timeout + PROBE_TIMEOUT``;
# ``PROBE_DB_TOTAL_TIMEOUT`` is only a loose OVER-ESTIMATE of the former, see
# its definition). For a shape with a coordinator above it that ordering is
# now PROVABLE — and it is pinned by tests that recompute both sides, so it
# cannot re-stale into prose.
#
# WHAT IS STILL NOT PROVABLE — stated here so nobody re-derives a "proof" from
# the paragraph above:
#   * A phase that overruns its ENFORCED deadline is ABANDONED, not cancelled
#     (CPython #87185; ``asyncio.wait_for`` cancels the awaitable, not the
#     thread). The WAIT is bounded; the WORKER is not. That is guarantee (c) —
#     stranding — and it is the standing residual, not a bug in the sum.
#   * ``probe_db`` only enforces an acquisition deadline when its caller passes
#     ``acquire=``. A caller that acquires the SDK itself (``metrics()`` / the
#     MCP health tool) still owns that phase's cost.
#   * The CONTROL plane's request is NOT bounded by construction: httpx's
#     ``read`` timeout applies PER READ OPERATION, so a slowly-dribbling server
#     can outlive the sum of the phases (a reviewer measured a response
#     surviving 5.3x the configured ``read`` phase). ``CONTROL_PLANE_HARD_TIMEOUT``
#     remains a safety net over an unenforceable request bound.
#   * On the EMBEDDED lane the enforced deadline is a bound on the PHASE, not a
#     small static ceiling on its interior: ``TortoiseSDK.__init__`` runs the
#     unbounded cross-process ``_probe_embedded_busy`` liveness probe
#     (``tortoise/sdk.py``) and ``_make_sdk``'s anchor connects EAGERLY and runs
#     real queries. #3350 capped the redis retry MULTIPLIER at
#     ``1 + projection._EMBEDDED_RETRY_COUNT`` (ONE retry, NOT this client's
#     own multi-retry default — the figure that used to sit here, "10 retries /
#     ~26.8s", described the upstream library's default rather than this repo's
#     pinned one and is retired with it), so the stale figure is gone; what
#     remains is that the per-operation multiplier is now known.
#     ``(1 + _EMBEDDED_RETRY_COUNT) * socket_timeout + backoff``, and the NUMBER
#     of sequential operations in the prefix is graph-size dependent. Bounding
#     the phase from outside is what stops that interior from becoming a
#     WHOLE-probe unbounded phase.
PROBE_MAX_SUPERSEDES = 4
PROBE_POLL_INTERVAL = 0.02

#: Name of the single bounded daemon thread a ``HealthProbe`` starts for its
#: probe (``_start_locked``). Defined once so callers/tests that must tell the
#: background refresher's own worker apart from a request-path invocation can
#: compare a symbol instead of re-typing the literal — if the literal drifts,
#: such a check silently misclassifies the refresher.
HEALTH_PROBE_THREAD_NAME = "tortoise-health-probe"

# #1565: ONE bounded retry on a TRANSIENT connect failure only (an embedded
# redislite server momentarily starting / momentarily unreachable under
# parallel-suite load). The 100ms delay covers a server mid-startup; a REAL
# outage (NXDOMAIN, stopped FalkorDB) fails the retry identically and still
# reports degraded ~0.1s later — the retry never masks a persistent failure.
PROBE_RETRY_DELAY = 0.1

#: Prefix of ``_probe_once``'s synthesized SETUP-phase timeout. ``probe_db``
#: matches it to tell a retry that never reached the reachability query (its
#: remaining slice of the deadline could not redo the cold-start) from one
#: that reached (and failed at) the query: only the latter's error may replace
#: the FIRST attempt's real error (#3143 review).
#:
#: ⚠️ This is a distinct error STRING, NOT a distinct status. A setup timeout
#: still returns ``ok=False`` from ``probe_db``, and ``metrics()`` maps
#: ``db["ok"] is False`` to ``status="degraded"`` + ``graph_size=0`` — #3143's
#: symptom SHAPE, with only the ``error`` text changed. Do not read the
#: separate spelling as "no verdict reported": to a caller the report is
#: indistinguishable from a real unreachability except for that string.
_PROBE_SETUP_TIMEOUT_MSG = "probe setup timeout after "

#: Prefix of ``probe_db``'s synthesized SDK-ACQUISITION-phase timeout (#3446).
#: A THIRD spelling, for the third phase: the acquisition is neither the
#: projection cold start nor the reachability query, so collapsing it into
#: either would misattribute the fault. Same status shape as the other two —
#: ``ok=False`` -> ``metrics()`` reports ``degraded`` + ``graph_size=0`` —
#: with only the text telling the phases apart.
_PROBE_ACQUISITION_TIMEOUT_MSG = "probe sdk acquisition timeout after "

#: A LOOSE outer-alignment bound for :func:`probe_db`'s PLATFORM liveness
#: shape (no explicit ``setup_timeout``): the figure a DB-plane coordinator is
#: sized ABOVE. It is deliberately an OVER-ESTIMATE, not the function's exact
#: total — since #3143 the #1565 retry rides the REMAINDER of the caller's
#: single deadline (``total_budget - elapsed - PROBE_RETRY_DELAY``) instead of
#: taking a second ``PROBE_TIMEOUT``, so the platform shape's real total is
#: ~``PROBE_TIMEOUT`` and this 2 x ``PROBE_TIMEOUT`` + delay figure sits
#: comfortably above it. It does NOT bound the #3143 explicit-allowance (MCP)
#: shape, whose total is ``setup_timeout + PROBE_TIMEOUT`` — that caller has no
#: coordinator above it, the tool IS the outermost caller. Reading only
#: ``PROBE_TIMEOUT`` as the platform total is the trap that produced an
#: inverted ordering in the #2850/#2988 merge: a 2.0s coordinator bound looked
#: safely above a "1.5s" inner bound while sitting below the then-real 3.1s
#: total. It does NOT cover the wrapper's SDK-acquisition prefix either (see
#: ``PROBE_SDK_ACQUISITION_BUDGET`` and the guarantee summary at
#: ``PROBE_MAX_SUPERSEDES``). Derive it rather than restating it.
PROBE_DB_TOTAL_TIMEOUT = 2 * PROBE_TIMEOUT + PROBE_RETRY_DELAY

#: #2850/#2988: the CONNECT leg of acquiring an SDK before :func:`probe_db`
#: can run — the redis client's DEFAULT ``socket_connect_timeout``, i.e.
#: ``projection._DB_CONNECT_TIMEOUT_DEFAULT`` (2.0s, the ``connect`` leg of
#: ``projection._socket_timeouts()``). A test pins this literal to that
#: default so the two cannot drift silently.
#:
#: #3446: since ``probe_db`` grew its ``acquire=`` seam this is not just a
#: nominal charge — it is the ENFORCED deadline of the SDK-acquisition PHASE
#: (the wait on the shared probe worker, exactly as ``_probe_once`` bounds its
#: own two phases). That is what makes the layered-timeout derivation sound:
#: the outer coordinator bound was always DERIVED by summing this figure into
#: the inner total, but the phase it named ran on the coordinator's own thread
#: with nothing bounding it, so the sum mixed an enforced term with an
#: unbounded one and the ordering could not be proven.
#:
#: ⚠️ It bounds the PHASE (the wait), NOT the acquisition's INTERIOR, and must
#: not be read as a ceiling on the latter:
#:   * URI mode (``TORTOISE_DB_URI`` set — the hosted steady state):
#:     ``_make_sdk(namespace=None)`` returns an SDK whose projection is LAZY,
#:     so the prefix is ~free and the connect happens INSIDE ``probe_db``'s
#:     own per-attempt worker bound. The 2.0s budget is then pure headroom.
#:   * EMBEDDED mode: the anchor path connects EAGERLY and runs real queries
#:     (auto-health-recover, version probe, index creation) bounded by the
#:     redis READ timeout — 10s by default and clampable to 60s via
#:     ``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S`` — and ``TortoiseSDK.__init__``
#:     runs the unbounded cross-process ``_probe_embedded_busy`` liveness
#:     probe first. #3350 capped the retry MULTIPLIER at
#:     ``1 + projection._EMBEDDED_RETRY_COUNT`` (ONE retry, not this client's
#:     own multi-retry default — see ``projection._embedded_retry()`` for the
#:     one that is actually installed; do NOT restate an upstream default
#:     number here, the pin is version-dependent), so the per-OPERATION ceiling is
#:     ``(1 + _EMBEDDED_RETRY_COUNT) * socket_timeout + backoff`` — but the
#:     number of sequential operations is graph-size dependent, so the prefix
#:     has no small static ceiling of its own. An embedded acquisition that
#:     cannot finish inside this deadline is REPORTED as a failed probe phase
#:     (and its worker abandoned, never cancelled) instead of outlasting the
#:     coordinator. See the guarantee summary at ``PROBE_MAX_SUPERSEDES``.
#:
#: The env override ``TORTOISE_FALKORDB_CONNECT_TIMEOUT_S`` can also raise the
#: connect leg itself (clamped at ``projection._DB_TIMEOUT_MAX_S`` = 60s), so
#: even the leg this constant models is only bounded at the DEFAULT setting.
PROBE_SDK_ACQUISITION_BUDGET = 2.0

#: Safety margin so a DB coordinator's outer bound sits STRICTLY above the
#: loose outer-alignment figure (``PROBE_DB_TOTAL_TIMEOUT``), NOT the probes'
#: exact inner total. A bound EXACTLY equal to that figure is still
#: a race — the worker's own timeout and the coordinator's deadline fire at
#: the same instant — and after the inner bound fires the worker still needs a
#: moment to store and notify its result. 0.5s is ~25x ``PROBE_POLL_INTERVAL``
#: and ample for scheduler jitter on a loaded box. Since #3446 both terms it
#: sits above are ENFORCED deadlines (the acquisition phase and ``probe_db``'s
#: own), so it is a margin on a real inequality rather than on an estimate —
#: the residual is stranding (a phase that overruns is abandoned, not
#: cancelled), see the guarantee summary at ``PROBE_MAX_SUPERSEDES``.
PROBE_DB_BOUND_MARGIN_S = 0.5

#: The ``HealthProbe`` constructor DEFAULT — a safety net for any future
#: ``HealthProbe(fn)`` that omits ``timeout``. It is DERIVED from
#: ``PROBE_DB_TOTAL_TIMEOUT`` — the loose outer-alignment figure for the
#: PLATFORM liveness shape, deliberately an OVER-ESTIMATE of that shape's real
#: ~``PROBE_TIMEOUT`` total (see its definition), NOT the probes' exact inner
#: total — PLUS the acquisition budget PLUS a strict-above margin, and NOT the
#: bare ``PROBE_TIMEOUT`` (1.5s). The pre-#2988 literal was 2.0s: it LOOKED
#: safely above a "1.5s" inner bound while actually sitting below the
#: THEN-REAL ~3.1s total (the #1565 retry still took a second ``PROBE_TIMEOUT``
#: before #3143 made it ride the remainder), so any
#: ``HealthProbe(lambda: _probe_db())`` built without an explicit timeout was
#: stranded a worker thread on every timeout (CPython #87185 —
#: ``asyncio.wait_for`` cancels the awaitable, not the thread).
#:
#: What this does and does not buy: it lifts the default above the SUM OF
#: ENFORCED inner deadlines (#3446) — the acquisition phase's and
#: ``probe_db``'s own — so the inner bound normally fires first AND the
#: ordering is now provable for a caller that hands ``probe_db`` its
#: acquisition via ``acquire=``. It still does not bound the acquisition's
#: interior, and the env override can raise the connect leg (see
#: ``PROBE_SDK_ACQUISITION_BUDGET`` and the guarantee summary at
#: ``PROBE_MAX_SUPERSEDES``).
#:
#: Note this constant is now used as a real bound by NO production
#: coordinator: every hosted coordinator (``_HEALTH_PROBE`` / ``_READY_PROBE``
#: / ``_CONTROL_PLANE_PROBE``) passes ``timeout=`` explicitly. It is DERIVED
#: rather than restated so a ``PROBE_TIMEOUT`` change propagates, and
#: ``hosted_api.DB_PROBE_HARD_TIMEOUT`` aliases it (single source of truth for
#: the DB plane).
PROBE_HARD_TIMEOUT = (PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET
                      + PROBE_DB_BOUND_MARGIN_S)

#: How often a background health refresher re-probes, keeping an in-memory
#: ``db`` field fresh WITHOUT the request path doing any I/O (#2850 hosted,
#: #2988 selfhost). ONE spelling for both surfaces: the refresh period is a
#: property of the shared health contract, not of one app, so the two cannot
#: drift apart or disagree about what ``TORTOISE_HEALTH_PROBE_INTERVAL``
#: means. (It moved here from ``hosted_api`` when the selfhost liveness
#: coordinator landed — #3286's one-mechanism-per-requirement discipline,
#: applied to the refresher period as well as to its executor.)
#: Must stay below ``PROBE_STALE_AFTER`` (30s) or a healthy DB would read as
#: degraded between refreshes.
HEALTH_PROBE_REFRESH_S = 10.0
#: Lower bound on a configured probe period (round-3 review P2). ``1e-9`` is
#: finite but turns the refresher into a ~50 Hz loop, each iteration spawning a
#: probe daemon thread and issuing a DB round trip — the same busy-loop the
#: ``nan`` rejection exists to prevent. The upper clamp was one-sided.
HEALTH_PROBE_MIN_INTERVAL_S = 0.5


def health_probe_interval() -> float:
    """Probe refresh period (``TORTOISE_HEALTH_PROBE_INTERVAL``, seconds).

    Shared by the hosted and selfhost liveness coordinators (see
    ``HEALTH_PROBE_REFRESH_S``).

    Clamped to half the probe staleness window (review P2): a period longer
    than ``PROBE_STALE_AFTER`` makes a HEALTHY DB read as ``degraded`` between
    refreshes, which then fails the deploy gate and gets misdiagnosed as a DB
    outage. Half the window leaves a full refresh of margin.

    NON-FINITE values are rejected and fall back to the default (round-2
    review P2): ``float()`` accepts ``nan``/``inf`` and neither is caught by
    the ``v <= 0`` guard (``nan <= 0`` is False) nor by the ``v > cap`` clamp
    (``nan > cap`` is False). ``nan`` flows into ``asyncio.sleep(nan)``, which
    returns almost immediately — a busy loop hammering the DB probe and the
    event loop. ``inf`` means the probe never refreshes, so a healthy DB reads
    stale forever. Both DISABLE (fall back to ``HEALTH_PROBE_REFRESH_S``).

    A finite but SUB-FLOOR period is rejected the same way (round-3 review
    P2): ``1e-9`` busy-loops the probe exactly as ``nan`` did.
    """
    try:
        v = float(os.environ.get("TORTOISE_HEALTH_PROBE_INTERVAL") or HEALTH_PROBE_REFRESH_S)
    except (TypeError, ValueError):
        return HEALTH_PROBE_REFRESH_S
    if not math.isfinite(v):
        logger.error(
            "TORTOISE_HEALTH_PROBE_INTERVAL=%s is not finite — falling back "
            "to the default %.0fs; a nan period busy-loops the probe and an "
            "infinite one leaves a healthy DB reading stale forever",
            v, HEALTH_PROBE_REFRESH_S)
        return HEALTH_PROBE_REFRESH_S
    if v <= 0:
        return HEALTH_PROBE_REFRESH_S
    # Round-3 review P2: a finite but tiny period busy-loops the probe just
    # like ``nan`` did — ``1e-9`` yields ~50 generations/s, each spawning a
    # daemon thread and issuing a DB round trip. The clamp below is
    # one-sided, so a floor is required too.
    if v < HEALTH_PROBE_MIN_INTERVAL_S:
        logger.warning(
            "TORTOISE_HEALTH_PROBE_INTERVAL=%s is below the %.2fs floor — "
            "falling back to the default %.0fs; a sub-floor period "
            "busy-loops the probe and duplicates the DB round trip",
            v, HEALTH_PROBE_MIN_INTERVAL_S, HEALTH_PROBE_REFRESH_S)
        return HEALTH_PROBE_REFRESH_S
    cap = PROBE_STALE_AFTER / 2.0
    if v > cap:
        logger.warning(
            "TORTOISE_HEALTH_PROBE_INTERVAL=%s exceeds half the probe "
            "staleness window (%.0fs) — clamping to %.0fs; a longer period "
            "would report a healthy DB as degraded and fail the deploy gate",
            v, PROBE_STALE_AFTER, cap)
        return cap
    return v


#: Default period for the event-retention sweep (seconds). Shared by the
#: hosted retention loop and the SDK lazy purge so both fall back identically.
EVENT_RETENTION_INTERVAL_DEFAULT_S = 3600


def event_retention_interval() -> int:
    """Validate ``TORTOISE_EVENT_RETENTION_INTERVAL`` to a positive int.

    Round-4 review P2 (PRE-EXISTING, fixed here): a bare ``int()`` in both
    call sites accepted ``0``/``-1``. In the hosted retention loop
    ``asyncio.sleep(0)``/``sleep(-1)`` return immediately, hammering
    ``_sweep_events``/``_purge_deleted_orgs`` with no delay; in the SDK lazy
    purge the gate ``now - _EVENT_PURGE_LAST < interval`` is always false, so
    every ``events_poll`` issued a DELETE. A non-numeric value also raised out
    of the SDK poll. Anything that is not a positive whole number of seconds
    falls back to ``EVENT_RETENTION_INTERVAL_DEFAULT_S`` with a warning.
    """
    raw = os.environ.get("TORTOISE_EVENT_RETENTION_INTERVAL")
    if raw is None or not str(raw).strip():
        return EVENT_RETENTION_INTERVAL_DEFAULT_S
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "TORTOISE_EVENT_RETENTION_INTERVAL=%r is not a whole number of "
            "seconds — using %ds", raw, EVENT_RETENTION_INTERVAL_DEFAULT_S)
        return EVENT_RETENTION_INTERVAL_DEFAULT_S
    if value < 1:
        logger.warning(
            "TORTOISE_EVENT_RETENTION_INTERVAL=%r is not a positive number "
            "of seconds — using %ds; a zero/negative interval makes "
            "asyncio.sleep() return immediately and the SDK purge gate always "
            "false (a DELETE on every events_poll)",
            raw, EVENT_RETENTION_INTERVAL_DEFAULT_S)
        return EVENT_RETENTION_INTERVAL_DEFAULT_S
    return value


# Prometheus metrics
REQUEST_COUNT = Counter("tortoise_requests_total", "Total HTTP requests", ["endpoint"])
REQUEST_LATENCY = Histogram("tortoise_request_latency_seconds", "Request latency")
ERROR_COUNT = Counter("tortoise_errors_total", "Total errors")
TEAM_COST = Counter("tortoise_team_cost_cents", "Cost by team", ["team"])
# #3820: the analytics write path's terminal outcome, one label child per
# member of `hosted_api._ANALYTICS_OUTCOMES`. The single writer is
# `record_analytics_outcome` — the write path never touches the counter
# directly, so the label vocabulary stays in one place (#501/#3677 house shape
# for a hot path: `Counter` labelled by outcome, as `REQUEST_COUNT`/
# `ERROR_COUNT` are labelled by endpoint).
ANALYTICS_OUTCOME_COUNT = Counter(
    "tortoise_analytics_events_total",
    "Analytics writes by terminal outcome (#3820)",
    ["outcome"],
)
# #3498 item 1: event-loop LAG = how late each heartbeat tick actually fired
# relative to its requested interval. The heartbeat (below) already proves the
# loop is SCHEDULING; this histogram records HOW FAR off schedule it is, so a
# residual on-loop stall is measurable rather than only visible as a stale
# timestamp. Buckets span sub-millisecond jitter to a multi-second freeze.
LOOP_LAG = Histogram(
    "tortoise_event_loop_lag_seconds",
    "Event-loop scheduling lag per heartbeat tick (#3498)",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


def register(sdk) -> None:
    """Wire SDK so /health can check FalkorDB connectivity + graph size."""
    global _sdk
    _sdk = sdk


def record_ingest() -> None:
    global _last_ingest
    _last_ingest = time.time()


def record_error() -> None:
    ERROR_COUNT.inc()


def record_cost(team: str, cents: int) -> None:
    """Track LLM/tool cost for a team. cents is integer (avoids float drift)."""
    TEAM_COST.labels(team=team).inc(cents)


def record_analytics_outcome(outcome: str) -> None:
    """Count one analytics write by its terminal outcome (#3820).

    ``outcome`` is a member of ``hosted_api._ANALYTICS_OUTCOMES``; the caller
    owns that vocabulary and increments this AFTER the sink leg resolved, so a
    write cannot be counted as delivered before it was.
    """
    ANALYTICS_OUTCOME_COUNT.labels(outcome=outcome).inc()


def analytics_outcome_counts() -> dict[str, int]:
    """In-process snapshot of ``ANALYTICS_OUTCOME_COUNT``, keyed by outcome.

    ``/metrics`` carries the Prometheus text form of the same counter; this is
    the readable form, for tests and for any operator-facing surface that has
    no scrape (today nothing scrapes it in production — see #3820's audit:
    ``serve_health`` is not a ``fly.toml`` process).
    """
    counts: dict[str, int] = {}
    for family in ANALYTICS_OUTCOME_COUNT.collect():
        for sample in family.samples:
            if not sample.name.endswith("_total"):
                continue
            outcome = sample.labels.get("outcome")
            if outcome is not None:
                counts[outcome] = int(sample.value)
    return counts


def _is_transient_connect_error(exc: BaseException) -> bool:
    """True for a TRANSIENT connection-level probe failure — the one class a
    single retry may legitimately clear (a DB server mid-startup / momentarily
    unreachable under parallel load).

    OSError covers ConnectionRefusedError and socket errors (refused, DNS/
    gaierror — a startup DNS race is exactly the transient class the retry
    targets); the redis client raises its OWN ConnectionError class
    (redis-py 8.x) that is NOT an OSError subclass, so match the name too.
    Builtin TimeoutError IS an OSError subclass but is NEVER retried (a hung
    DB stays hung) — excluded FIRST. Everything else — redis TimeoutError,
    auth/response errors, arbitrary RuntimeErrors — is NOT retried: a
    genuinely broken DB must keep flipping /health to degraded without the
    retry masking it (#1565).
    """
    if isinstance(exc, TimeoutError):
        return False
    if isinstance(exc, OSError):
        return True
    return type(exc).__name__ == "ConnectionError"


class _WorkerBacklogFull(concurrent.futures.TimeoutError):
    """A worker's bounded backlog refused a submission.

    Subclass of ``concurrent.futures.TimeoutError`` so the historical
    ``run_on_daemon_worker`` contract (a saturated backlog surfaces as
    ``concurrent.futures.TimeoutError``) is unchanged, while
    ``run_control_plane_call`` can tell a REFUSED submission apart from a
    builtin ``TimeoutError`` raised by the callable itself (#3498 review).
    """


class _SingleSlotWorker:
    """ONE (or more) process-lifetime daemon threads running submitted callables.

    #2850: ``_probe_once`` used to build a NEW ``ThreadPoolExecutor`` on every
    probe and abandon its worker on timeout (``shutdown(wait=False)``). A
    black-holed FalkorDB makes that worker block for the lifetime of the
    socket call, so every probe leaked a thread (repro: 10 hung ``probe_db``
    calls → +10 live ``ThreadPoolExecutor-N_0`` threads) plus the DB
    connection that abandoned call was holding.

    One long-lived worker bounds the thread count to exactly one per process
    no matter how many times a probe hangs. Callers keep their own hard
    timeout through the returned ``Future`` (``future.result(timeout=…)``).

    Daemon, NOT ``ThreadPoolExecutor`` (whose workers are non-daemon): a
    wedged probe must never block interpreter/uvicorn shutdown — Fly SIGTERMs
    the machine and ``concurrent.futures.thread._python_exit`` would join the
    stuck worker forever.

    #3498: ``workers`` > 1 turns this into a BOUNDED MULTI-WORKER pool — N
    daemon threads draining the SAME bounded queue. The single-slot default is
    unchanged, so probe behaviour is bit-identical. The multi-worker shape
    exists for the control-plane/auth offload seam: a 2–6-round-trip-per-
    request auth path on ONE slot would serialise auth to roughly one
    concurrent request, which is why this defect is not fixed by routing auth
    onto the existing single-slot probe worker.
    """

    #: Bounded backlog: a wedged probe must not let submissions grow the
    #: queue without limit. Overflow fails fast (the caller's own
    #: ``PROBE_TIMEOUT`` reports degraded) instead of buffering forever.
    MAX_BACKLOG = 32

    def __init__(self, name: str, workers: int = 1,
                 max_backlog: int | None = None) -> None:
        if workers < 1:
            raise ValueError(f"workers must be >= 1 (got {workers!r})")
        self._name = name
        self._max_backlog = self.MAX_BACKLOG if max_backlog is None else max_backlog
        self._queue: queue.Queue[tuple | None] = queue.Queue(maxsize=self._max_backlog)
        # ``workers == 1`` keeps the historical thread name EXACTLY (ops
        # recipes and tests match on it); a pool suffixes each thread with its
        # index so a trace can tell the pool members apart.
        self._threads = [
            threading.Thread(
                target=self._loop,
                name=name if workers == 1 else f"{name}-{i}",
                daemon=True,
            )
            for i in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    @property
    def alive(self) -> bool:
        # ``any`` (not ``all``): one pool member dying must not be reported as
        # a dead pool while its siblings are still draining work. A fully dead
        # pool still reads not-alive and is recreated by ``daemon_worker``.
        return any(t.is_alive() for t in self._threads)

    @property
    def workers(self) -> int:
        return len(self._threads)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)

    def submit(self, fn):
        """Queue ``fn``; return a Future the CALLER bounds with a timeout.

        A saturated backlog means the worker is wedged and callers are piling
        up — fail fast (the probe reports degraded) rather than queueing
        without bound.
        """
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            self._queue.put_nowait((fn, future))
        except queue.Full:
            future.set_exception(
                _WorkerBacklogFull(
                    f"{self._name} backlog full ({self._max_backlog}) — worker wedged"))
        return future


_PROBE_WORKER: _SingleSlotWorker | None = None
_PROBE_WORKER_LOCK = threading.Lock()


def _probe_worker() -> _SingleSlotWorker:
    """Lazy singleton — no thread is created until the first probe runs."""
    global _PROBE_WORKER
    worker = _PROBE_WORKER
    if worker is None or not worker.alive:
        with _PROBE_WORKER_LOCK:
            worker = _PROBE_WORKER
            if worker is None or not worker.alive:
                worker = _SingleSlotWorker("tortoise-probe-worker")
                _PROBE_WORKER = worker
    return worker


def _reset_probe_worker() -> None:
    """Drop the shared worker so the next probe lazily starts a fresh one.

    Escape hatch for tests and for ops recovery when a probe thread is
    presumed wedged past any realistic socket timeout. The old (possibly
    wedged) thread is a daemon — it is abandoned, never joined.
    """
    global _PROBE_WORKER
    with _PROBE_WORKER_LOCK:
        _PROBE_WORKER = None


#: Named process-wide daemon workers for blocking work that must NOT occupy
#: the event loop's shared default executor.
_DAEMON_WORKERS: dict[str, _SingleSlotWorker] = {}
_DAEMON_WORKERS_LOCK = threading.Lock()


def daemon_worker(name: str, *, workers: int = 1,
                  max_backlog: int | None = None) -> _SingleSlotWorker:
    """Process-wide named DAEMON worker pool (lazily started).

    ``workers=1`` (the default) is the historical single-slot worker; a larger
    ``workers`` builds the bounded multi-worker pool #3498 needs for the
    control-plane seam.

    #2850: ``asyncio.to_thread`` submits to the loop's DEFAULT executor, whose
    workers are NON-daemon — and ``asyncio.run`` calls
    ``loop.shutdown_default_executor()``, which **joins every worker**. So a
    ``to_thread`` call blocked on a black-holed socket does not merely stall
    its own await: it makes uvicorn's whole process shutdown wait for that
    thread (until the socket timeout, or forever with no timeout).

    A ``_SingleSlotWorker`` is a daemon, so a wedged call can be abandoned at
    interpreter exit and a cancelled task never delays shutdown.
    """
    with _DAEMON_WORKERS_LOCK:
        worker = _DAEMON_WORKERS.get(name)
        if worker is None or not worker.alive:
            worker = _SingleSlotWorker(name, workers=workers,
                                       max_backlog=max_backlog)
            _DAEMON_WORKERS[name] = worker
        return worker


def _consume_future_exception(future) -> None:
    """Retrieve a finished future's exception so an abandoned failure is not
    reported only as an unattributed asyncio warning. NEVER raises.

    Registered by ``_await_future`` on the wrap_future awaitable of the
    non-cancellable lane. Calling ``exception()`` is what marks the exception
    RETRIEVED (clears ``Future._log_traceback``); without it, a failure that
    lands AFTER the await bound was abandoned surfaces ONLY as asyncio's
    "Future exception was never retrieved" when the future is collected.
    """
    with contextlib.suppress(Exception):
        if not future.cancelled():
            future.exception()


async def _await_future(future, *, timeout: float | None,
                        cancel_on_timeout: bool = True):
    """Await a concurrent Future, optionally bounded.

    Shared by ``run_on_daemon_worker`` and ``run_control_plane_call`` so the
    wrap/cancel/bound semantics have ONE implementation (#3498 review — the
    two offload await paths must not drift).

    ``cancel_on_timeout=False`` (#4456) is for work whose DELIVERY matters.
    ``asyncio.wait_for`` cancels the awaitable it is handed, and
    ``asyncio.wrap_future`` propagates that cancellation to the underlying
    ``concurrent.futures.Future``. For a submission still QUEUED (never
    dequeued) that ``cancel()`` SUCCEEDS; when a worker later dequeues it,
    ``set_running_or_notify_cancel()`` returns False and
    ``_SingleSlotWorker._loop`` SKIPS the callable — the work is silently
    DROPPED, not merely abandoned. ``asyncio.shield`` keeps the
    ``wrap_future`` awaitable alive, so the bound abandons only the AWAIT and
    a QUEUED submission still runs. Callers for which fail-closed
    abandonment is correct keep the default ``True``.

    On the non-cancellable lane the abandoned awaitable has NO retriever, and
    ``shield`` does NOT supply one: in CPython 3.12 ``_outer_done_callback``
    runs on outer-cancel and, because the inner is not yet done (exactly the
    bound-miss case), REMOVES ``_inner_done_callback`` — whose only job was
    ``inner.exception()``. The wrapped future's outcome is therefore consumed
    HERE (#4456), and ``run_control_plane_call`` attributes a later failure at
    the op level.
    """
    awaitable = asyncio.wrap_future(future)
    if timeout is None:
        return await awaitable
    if not cancel_on_timeout:
        awaitable.add_done_callback(_consume_future_exception)
        awaitable = asyncio.shield(awaitable)
    return await asyncio.wait_for(awaitable, timeout)


async def run_on_daemon_worker(fn, *, name: str, timeout: float | None = None):
    """Await blocking ``fn`` on a named daemon worker — never the shared pool.

    Cancellation is prompt: cancelling the task wakes it at the ``await``
    immediately; the (daemon) worker thread is abandoned, so shutdown is never
    blocked behind a wedged call. A saturated worker backlog raises
    ``concurrent.futures.TimeoutError`` to the caller (fail fast).

    ``timeout`` (#3498) is an OPTIONAL explicit WAIT BOUND on the submission.
    ``None`` (the default) preserves the historical unbounded await for the
    probe lane. When given, a worker that has not completed within the bound
    raises ``TimeoutError`` to the caller and the (daemon) thread is
    ABANDONED — ``wait_for`` cancels the awaitable, not the thread (CPython
    #87185), which is why the outer bound must remain above the probe's own
    inner bound (see the probe-bound ordering tests).
    """
    future = daemon_worker(name).submit(fn)
    return await _await_future(future, timeout=timeout)


# ── #3498: bounded multi-worker CONTROL-PLANE offload seam ────────────────
#
# The auth/session path (``tortoise/supabase_control.py`` — 0 ``async def``
# across ~100 functions, ``httpx.Client`` transport) is SYNCHRONOUS by
# construction, so every async handler that needs it bridges to a blocking
# API. ``asyncio.to_thread`` submits to the loop's SHARED default executor
# (``min(32, cpu+4)`` = 6 workers on the 2-vCPU production machine) which
# ``/health``'s DB probe and the abuse hooks also use — the #3060 lesson.
# The existing ``run_on_daemon_worker`` is dedicated but SINGLE-SLOT, so a
# 2–6-round-trip-per-request auth path on it would collapse auth concurrency
# to roughly one request and convert a stall into fail-fast 503s.
#
# This seam is the third option: a DEDICATED, MULTI-WORKER, BOUNDED pool with
# an explicit wait bound and a fail-closed error. It is separate from the
# probe workers (its own name) so the /health probe budget and the auth path
# can never starve each other.
#
# #3498 review P1: it is ALSO split into named pools. Best-effort work
# (``update_last_used``, the analytics emit, the GitHub repo count) must not
# occupy the auth slots — a telemetry burst or a hung display-only GitHub call
# parking every auth worker is the same total-auth-outage blast radius this
# issue is about. ``best_effort=True`` protects the CALLER; the separate pool
# protects the AUTH CALLERS sharing capacity. #3669 adds a THIRD pool for the
# attacker-reachable OAuth client-resolution lane (see
# ``CONTROL_PLANE_OAUTH_WORKER_NAME``).
CONTROL_PLANE_WORKER_NAME = "tortoise-control-plane"

#: The best-effort pool's name — never shares slots with ``auth``.
CONTROL_PLANE_TELEMETRY_WORKER_NAME = "tortoise-telemetry"

#: Pool size. The calls are network-bound (PostgREST), so a small multiple of
#: the loop's parallelism is what removes the serialisation a single slot
#: would impose. Deliberately modest: on the 2-vCPU production machine a large
#: pool buys no throughput, only parked threads.
CONTROL_PLANE_WORKERS = 8

#: Best-effort pool size — smaller: it is off the critical path.
CONTROL_PLANE_TELEMETRY_WORKERS = 4

#: Bounded backlog. A saturated queue means the pool is wedged; submissions
#: fail fast (mapped to a 503) instead of buffering without bound.
CONTROL_PLANE_BACKLOG = 128

#: Best-effort backlog — larger, because best-effort submissions that are
#: refused are simply dropped (never a user-visible failure).
CONTROL_PLANE_TELEMETRY_BACKLOG = 256

#: #3669: a THIRD pool, for the OAuth client-resolution lane. A CIMD fetch is
#: attacker-reachable (an unauthenticated ``client_id`` URL), so sharing the
#: ``auth`` pool would let a fetch flood occupy every auth slot — the same
#: isolation argument that split ``telemetry`` out (#3498 review P1), applied
#: to a new attacker class. Sized ABOVE ``cimd.MAX_IN_FLIGHT_FETCHES`` so the
#: CIMD in-flight cap is the binding constraint on FETCHES (defence in depth),
#: with the remaining workers carrying the token grants and registry reads.
#: NOTE the token grants share this pool and are awaited with no offload wait
#: bound, so N concurrent grants can occupy N workers; a resolution submitted
#: while the pool is saturated waits on the shared backlog and fails closed at
#: its own bound. That read-lane pressure is accepted (bounded by the backlog
#: and the grant's httpx phase timeouts), not hidden.
CONTROL_PLANE_OAUTH_WORKER_NAME = "tortoise-oauth"
CONTROL_PLANE_OAUTH_WORKERS = 8
CONTROL_PLANE_OAUTH_BACKLOG = 64

#: #3773: a FOURTH pool, for the DATA-PLANE (FalkorDB) offload. The write
#: handlers' per-request graph helpers (``_data_sdk``'s connect / embedded
#: anchor probe, ``_check_org_limit``'s count query) ran inline on the loop
#: immediately before an already off-loaded write, so a blocked loop still
#: stalled every concurrent request for their duration. They reuse this seam's
#: bounded multi-worker pool, wait bound and fail-closed error, on a pool of
#: their OWN: a burst of graph writes must never park a single auth slot (the
#: #3498 review P1 isolation argument, applied to the data plane).
#:
#: Occupancy disclosure (the #3669-cycle-2 "not hidden" rule): each WRITE
#: consumes TWO sequential submissions here (the quota count, then the SDK
#: open); the REST ``/v1/events`` and ``/v1/dream`` handlers' SDK open also
#: submits here. The write-triggered ``_dream_worker`` does NOT (it builds
#: inside the ``_DREAM_EXECUTOR`` item). The graph-bound ``_data_sdk`` path
#: reaches a blocking CONTROL-plane PostgREST read (``_assert_graph_owned`` ->
#: ``get_control_plane().query("graphs")``), so a control-plane stall parks a
#: graph slot here and a bound miss on that read reports ``graph_unavailable``.
#: Routing the ownership probe through the control-plane pool is a follow-up;
#: the shared-capacity shape is accepted.
CONTROL_PLANE_GRAPH_WORKER_NAME = "tortoise-graph"
CONTROL_PLANE_GRAPH_WORKERS = 8
CONTROL_PLANE_GRAPH_BACKLOG = 128

#: Margin added to the projection cold-start allowance for the DATA-PLANE graph
#: lane (#3773). The bound is resolved at CALL time (``graph_offload_timeout_s``)
#: from ``probe_setup_timeout()``, NOT from the frozen import-time default: an
#: operator who raises ``TORTOISE_PROBE_SETUP_TIMEOUT`` for a large/cold graph
#: must not make the graph lane fall BELOW the allowance it is meant to cover
#: (which would 503-retry a merely-cold first write — the #3143 false-degrade
#: class). The ordering is pinned by a test against the RESOLVED value.
CONTROL_PLANE_GRAPH_OFFLOAD_MARGIN_S = 10.0


def graph_offload_timeout_s() -> float:
    """#3773: the DATA-PLANE graph lane's wait bound, resolved at CALL time.

    ``_data_sdk``'s embedded anchor probe and ``_check_org_limit``'s count query
    can each open a COLD projection (connect + version probe +
    ``_ensure_indexes()`` — ~28 sequential round trips), which the probe lane
    already budgets via ``probe_setup_timeout()``. The graph lane's bound is
    that resolved allowance PLUS a margin, so a cold first write is never
    abandoned and 503'd. Still bounded and fail-fast for a genuinely wedged
    graph.
    """
    return probe_setup_timeout() + CONTROL_PLANE_GRAPH_OFFLOAD_MARGIN_S

#: Wait bound for ONE offloaded control-plane resolution. Sits ABOVE a normal
#: round-trip's several phases but below the edge/proxy budget, so a
#: black-holed PostgREST call fails the ONE request closed instead of holding
#: a slot (and the loop's await) indefinitely. Resolved at CALL time so it
#: stays monkeypatchable.
#: COMPOSITE NOTE (#3498 review): a single request can chain several offloads
#: (``_session_user_org`` alone issues three), and the bound is PER CALL, so
#: the worst case is N x bound. It is bounded and fail-fast — the FIRST leg
#: that misses the bound ends the request with the structured 503 — but if the
#: proxy budget is ever tightened, lower this rather than deriving a shared
#: per-request deadline.
CONTROL_PLANE_OFFLOAD_TIMEOUT_S = 10.0

#: Bounded per-call ``(op, duration_s)`` record for the offload seam.
_CP_OFFLOAD_RECORDS: deque[tuple[str, float]] = deque(maxlen=512)
#: Bounded per-call ``(duration_s, thread_name)`` record for the CONTROL-PLANE
#: CLIENT itself (#3498 item 2 — the falsifier). Recorded at the httpx choke
#: point in ``supabase_control.SupabaseControlPlane``, so a call that ran on
#: ``MainThread`` shows up as ``MainThread`` — i.e. it never left the loop.
_CP_CLIENT_RECORDS: deque[tuple[float, str]] = deque(maxlen=512)
_CP_RECORDS_LOCK = threading.Lock()


class ControlPlaneOffloadError(RuntimeError):
    """A control-plane offload did not complete inside its bound.

    Fail-closed: raised when the bounded pool's wait expires (the daemon
    worker is abandoned) or its backlog is full. The hosted seam maps this to
    the repo-standard 503 ``control_plane_unavailable`` — never a hang and
    never a silent pass-through.

    ``refused`` (#4456) is the public discriminator between the two outcomes:
    ``True`` when the pool REFUSED the submission (its backlog was full) or
    the submission was CANCELLED before any worker ran it — the callable did
    NOT and WILL NOT run; ``False`` for a plain bound miss, where a RUNNING
    worker still completes the callable (the seam abandons only the await)
    and a non-cancellable lane keeps a QUEUED submission alive.
    """

    def __init__(self, message: str, *, refused: bool = False) -> None:
        super().__init__(message)
        self.refused = refused


def control_plane_worker(pool: str = "auth") -> _SingleSlotWorker:
    """Lazy process-wide multi-worker pool for the control-plane seam.

    ``pool="auth"`` (default) is the AUTH-CRITICAL pool; ``pool="telemetry"``
    is a SEPARATE pool for best-effort work, so telemetry can never park the
    auth slots (#3498 review P1); ``pool="oauth"`` (#3669) is a separate pool
    for the attacker-reachable OAuth client-resolution lane, so a CIMD fetch
    flood cannot park the auth slots either; ``pool="graph"`` (#3773) is the
    DATA-PLANE pool for the write handlers' graph helpers, kept off auth
    capacity for the same isolation reason. The graph pool's callables SET
    ContextVars (the #2600 actor bind), so it must be reached ONLY through the
    hosted ``_graph_offload`` wrapper, which runs them under a copy of the
    caller's context — a bare ``run_control_plane_call(..., pool="graph")``
    would write into the process-lifetime pool thread's own context and leak
    that value into the NEXT request the worker serves.

    An UNKNOWN selector raises rather than falling back to auth: the pool
    choice is the only thing keeping best-effort or attacker-reachable work
    off the auth-critical capacity, so a typo must fail closed, not silently
    revert the split.
    """
    if pool == "graph":
        return daemon_worker(CONTROL_PLANE_GRAPH_WORKER_NAME,
                             workers=CONTROL_PLANE_GRAPH_WORKERS,
                             max_backlog=CONTROL_PLANE_GRAPH_BACKLOG)
    if pool == "oauth":
        return daemon_worker(CONTROL_PLANE_OAUTH_WORKER_NAME,
                             workers=CONTROL_PLANE_OAUTH_WORKERS,
                             max_backlog=CONTROL_PLANE_OAUTH_BACKLOG)
    if pool == "telemetry":
        return daemon_worker(CONTROL_PLANE_TELEMETRY_WORKER_NAME,
                             workers=CONTROL_PLANE_TELEMETRY_WORKERS,
                             max_backlog=CONTROL_PLANE_TELEMETRY_BACKLOG)
    if pool == "auth":
        return daemon_worker(CONTROL_PLANE_WORKER_NAME,
                             workers=CONTROL_PLANE_WORKERS,
                             max_backlog=CONTROL_PLANE_BACKLOG)
    raise ValueError(f"unknown control-plane pool {pool!r}")


def record_control_plane_offload(op: str, duration_s: float) -> None:
    """Record ONE offloaded control-plane resolution ``(op, duration)``."""
    with _CP_RECORDS_LOCK:
        _CP_OFFLOAD_RECORDS.append((op, duration_s))


def control_plane_offload_records() -> list[tuple[str, float]]:
    """Snapshot of the bounded offload records (oldest first)."""
    with _CP_RECORDS_LOCK:
        return list(_CP_OFFLOAD_RECORDS)


def record_control_plane_client_call(duration_s: float, thread_name: str) -> None:
    """Record ONE control-plane HTTP call as ``(duration, thread name)``.

    Called at the ``SupabaseControlPlane`` transport choke point (#3498 item
    2). ``thread_name`` is what makes the record a FALSIFIER: a call recorded
    as ``MainThread`` ran on the event loop, i.e. was never offloaded.
    """
    with _CP_RECORDS_LOCK:
        _CP_CLIENT_RECORDS.append((duration_s, thread_name))


def control_plane_client_records() -> list[tuple[float, str]]:
    """Snapshot of the bounded control-plane client records (oldest first)."""
    with _CP_RECORDS_LOCK:
        return list(_CP_CLIENT_RECORDS)


def reset_control_plane_records() -> None:
    """Clear both record buffers (test/ops seam)."""
    with _CP_RECORDS_LOCK:
        _CP_OFFLOAD_RECORDS.clear()
        _CP_CLIENT_RECORDS.clear()


def _log_abandoned_outcome(op: str):
    """Done-callback factory: attribute an ABANDONED callable's later failure.

    A bound miss on the ``cancel_on_timeout=False`` lane abandons ONLY the
    await — the worker still runs the callable — so a failure that lands after
    the bound has nowhere to be reported. ``_await_future`` consumes it (so it
    is not just an unattributed asyncio warning); this names the op (#4456).
    Never raises: it runs on the completing thread's done-callback path.
    """
    def _cb(future) -> None:
        with contextlib.suppress(Exception):
            if future.cancelled():
                return
            exc = future.exception()
            if exc is not None:
                logger.error(
                    "control-plane call %r abandoned at the wait bound then "
                    "FAILED: %r", op, exc)
    return _cb


async def run_control_plane_call(fn, *, op: str,
                                 timeout: float | None = None,
                                 pool: str = "auth",
                                 cancel_on_timeout: bool = True):
    """Offload ONE blocking control-plane helper to a bounded pool.

    The unit of offload is the RESOLUTION, not an individual HTTP call:
    ``resolve_api_key`` is up to 8 dependent round-trips and
    ``_orgs_row_fail_soft`` up to 6, and both are sequential and dependent
    (the ladder's next rung depends on the previous rung's error). Offloading
    per round-trip would pay N thread hops and interleave unrelated requests
    into the ladder; offloading the helper pays ONE hop and keeps the ladder's
    ordering intact inside one thread.

    ``pool`` selects the worker: ``"auth"`` (default) for auth-critical
    resolutions, ``"telemetry"`` for best-effort work that must never consume
    auth capacity, ``"oauth"`` (#3669) for the attacker-reachable OAuth
    client-resolution lane, or ``"graph"`` (#3773) for the DATA-PLANE graph
    helpers — a separate pool for the same isolation reason.

    Fail-closed: a missed bound or a saturated backlog raises
    :class:`ControlPlaneOffloadError`. A builtin ``TimeoutError`` raised by
    ``fn`` ITSELF is a DOMAIN error and propagates unchanged — the three cases
    are disambiguated by inspecting the future, not conflated (#3498 review).

    ``cancel_on_timeout`` (#4456) selects the bound-miss semantics. ``True``
    (default) is fail-closed: the bound cancels the submission, so a QUEUED
    callable is dropped. ``False`` is DELIVERY-preserving: the bound abandons
    only the await (``asyncio.shield``) and a QUEUED callable still runs —
    used by the Stripe billing notify, whose event is already claimed and can
    never be re-fired. The raised error's public ``refused`` attribute still
    tells the two apart.
    """
    bound = CONTROL_PLANE_OFFLOAD_TIMEOUT_S if timeout is None else timeout
    future = control_plane_worker(pool).submit(fn)
    started = time.monotonic()
    try:
        result = await _await_future(future, timeout=bound,
                                     cancel_on_timeout=cancel_on_timeout)
    except TimeoutError as exc:
        # Distinguish the three sources of TimeoutError that meet here:
        #   1. `fn` raised it              -> a domain error, propagate
        #   2. the pool refused the submit -> `_WorkerBacklogFull`, fail closed
        #   3. `wait_for`'s bound expired  -> fail closed
        future_exc = (future.exception()
                      if future.done() and not future.cancelled() else None)
        if future_exc is not None and not isinstance(future_exc, _WorkerBacklogFull):
            raise
        reason = ("pool backlog full" if isinstance(future_exc, _WorkerBacklogFull)
                  else f"exceeded its {bound}s bound")
        if not future.done():
            # The callable is still QUEUED or RUNNING: the bound abandoned the
            # AWAIT, not the work. Attribute whatever it eventually does at the
            # op level instead of leaving it to a bare asyncio warning (#4456).
            future.add_done_callback(_log_abandoned_outcome(op))
        # ``refused`` is the DELIVERY discriminator (#4456): True when the
        # callable did not and will not run (backlog-full refusal, or a queued
        # submission cancelled by the bound); False when a bound miss left it
        # running (or, on a non-cancellable lane, still queued).
        raise ControlPlaneOffloadError(
            f"control-plane call {op!r} {reason}",
            refused=(isinstance(future_exc, _WorkerBacklogFull)
                     or future.cancelled()),
        ) from exc
    record_control_plane_offload(op, time.monotonic() - started)
    return result


def _probe_once(sdk, timeout=None,
                setup_timeout=None) -> tuple[bool, str | None, bool]:
    """Execute ONE bounded probe on the shared probe worker.

    Returns ``(ok, error, transient)`` — ``transient`` is True only when the
    failure was a connection-level error that a single retry could clear,
    never a timeout (a hung DB stays hung).

    #3143: the two phases are bounded separately. ``setup_timeout`` bounds the
    projection cold-start (``sdk._get_proj()`` — connect + ``_ensure_indexes()``,
    see PROBE_SETUP_TIMEOUT); ``timeout`` bounds the ``RETURN 1`` query itself,
    which is the only phase that is a reachability signal. Both resolve
    ``PROBE_TIMEOUT`` at CALL time (not as frozen default args) so the
    module-global stays monkeypatchable.

    When ``setup_timeout`` is NOT given (the platform-liveness shape) the two
    phases SHARE the single ``timeout`` budget, so the caller's total wait is
    still ≤ ``timeout`` exactly as before #3143 (the `/health` fast-degrade
    contract, #1384). Only callers that pass an explicit ``setup_timeout`` opt
    into a separate cold-start allowance, and their total can then reach
    ``setup_timeout + timeout``.

    #3143 P1 (concurrency): ``future.result(timeout=…)`` starts its clock at
    SUBMISSION, so with the single #3062 slot a query that queues behind
    another probe's cold-start would spend its reachability budget queued and
    time out — a reachable graph reported degraded. The explicit-allowance
    shape therefore charges the WAIT FOR THE SLOT to the cold-start allowance's
    LEFTOVER and applies ``timeout`` only once the worker has picked the query
    up (the ``query_started`` event), keeping the call's total at
    ``setup_timeout + timeout``. The platform-liveness shape keeps its single
    hard total (#1384) — there the queue wait is deliberately INSIDE the
    budget, because that gate exists to fast-degrade.

    #2850: BOTH phases run on the process-lifetime daemon ``_probe_worker()``
    — never a per-call executor — so a probe that overruns cannot leak a
    thread; it holds the single slot only until its own blocked socket call
    returns. The ``sdk._get_proj`` / ``proj.g.query`` lookups live INSIDE the
    submitted callables, so a malformed SDK raises inside the future and is
    classified here: this function keeps its never-raise contract. A TIMEOUT
    is never retried by ``probe_db``.
    """
    if timeout is None:
        timeout = PROBE_TIMEOUT
    combined = setup_timeout is None
    if combined:
        setup_timeout = timeout
    start = time.monotonic()

    def _setup():
        # The lookup is INSIDE the worker: a missing/broken `_get_proj` must
        # surface as a classified probe failure, never as a raised
        # AttributeError on the caller thread (probe_db never raises).
        return sdk._get_proj()

    setup = _probe_worker().submit(_setup)
    try:
        proj = setup.result(timeout=setup_timeout)
    except concurrent.futures.TimeoutError as e:
        # ``setup.done()`` is True for TWO different causes and so cannot be
        # read as "the worker refused the submission" (#3143 review P2):
        #   (a) the worker refused/aborted the submission (saturated #2850
        #       backlog) — the Future already carries its own message; or
        #   (b) the CALLABLE raised a TimeoutError — on py3.12
        #       ``concurrent.futures.TimeoutError`` IS ``builtins.TimeoutError``,
        #       so a bare ``TimeoutError()``/``socket.timeout`` from inside
        #       ``_get_proj`` is re-raised here and ``str(e)`` is often EMPTY.
        #       Fall back to the synthesized setup message rather than
        #       returning ``error=""``.
        msg = str(e)[:200] or f"{_PROBE_SETUP_TIMEOUT_MSG}{setup_timeout}s"
        if setup.done():
            return False, msg, _is_transient_connect_error(e)
        # Not done: the cold-start genuinely overran its allowance (the worker
        # is abandoned, never cancelled — #2850).
        return False, f"{_PROBE_SETUP_TIMEOUT_MSG}{setup_timeout}s", False
    except Exception as e:  # noqa: BLE001, RUF100
        return False, str(e)[:200], _is_transient_connect_error(e)

    if combined:
        # Platform-liveness shape (#1384): the query may spend only what the
        # cold-start left of the SINGLE budget, so the caller's total wait
        # stays ≤ timeout. A busy worker's queue wait is inside that total BY
        # DESIGN — this is a fast-degrade gate, not a reachability report, so
        # congestion must not extend its budget.
        query_budget = timeout - (time.monotonic() - start)
        if query_budget <= 0:
            # The cold-start consumed the shared budget, so the reachability
            # query never ran. Report the SETUP spelling (the phase at fault):
            # ONE spelling per phase, and ``probe_db``'s retry uses this prefix
            # to keep the first attempt's real error when its own remainder was
            # eaten by the cold-start (#3143 review P2).
            return False, f"{_PROBE_SETUP_TIMEOUT_MSG}{setup_timeout}s", False
        slot_wait_budget = None
    else:
        # Explicit-allowance shape (#3143, the MCP health tool): ``timeout``
        # bounds the reachability query's OWN execution. The wait for the
        # single #3062 worker to PICK THE QUERY UP is not a reachability
        # signal — it is charged to the LEFTOVER of the cold-start allowance
        # instead. Without this, a query queued behind another probe's slow
        # (or already-abandoned) cold-start spent the 1.5s reachability budget
        # queued, timed out, and reported a REACHABLE graph ``degraded``/0 —
        # the #3143 symptom surviving under concurrency (review P1).
        query_budget = timeout
        slot_wait_budget = max(0.0, setup_timeout - (time.monotonic() - start))

    query_started = threading.Event()

    def _run_query():
        # The signal fires as the WORKER picks the submission up, so the
        # caller can bound EXECUTION rather than the queue wait (a
        # ``Future.result`` clock starts at SUBMISSION — the whole P1 bug).
        query_started.set()
        # Same for `proj.g.query` — the lookup is the worker's.
        return proj.g.query("RETURN 1")

    query = _probe_worker().submit(_run_query)
    # The ``query.done()`` guard skips the wait when ``submit()`` refused the
    # submission outright (saturated #2850 backlog) — fail fast.
    # TRUTHINESS is deliberate: ``slot_wait_budget`` is ``None`` in the
    # COMBINED shape (no slot wait) and a float in the EXPLICIT one, where it
    # clamps to exactly ``0.0``. A zero leftover is NOT a fault: the caller has
    # not yielded the GIL, so ``wait(0.0)`` can never let a submission made
    # microseconds earlier look started, and firing on ``is not None`` there
    # would fail a REACHABLE graph on a FREE slot (the reverted #3143
    # false-FAIL — a zero-leftover query runs as soon as the caller blocks in
    # ``Future.result``, which DOES release the GIL).
    if (slot_wait_budget and not query.done()
            and not query_started.wait(slot_wait_budget)):
        # The worker never BEGAN the query inside the leftover allowance, so the
        # query never ran. That is a distinct error STRING, NOT a distinct
        # status: ``ok=False`` still flows out to ``metrics()`` as
        # ``status="degraded"`` + ``graph_size=0`` — #3143's shape with only
        # the text changed (see ``_PROBE_SETUP_TIMEOUT_MSG``). Same setup
        # spelling as a cold-start overrun (one spelling per phase). The
        # abandoned submission holds no extra thread (#2850).
        return False, f"{_PROBE_SETUP_TIMEOUT_MSG}{setup_timeout}s", False
    try:
        query.result(timeout=query_budget)
        return True, None, False
    except concurrent.futures.TimeoutError as e:
        if query.done() and not query_started.is_set():
            # The worker refused/aborted the submission (saturated backlog) —
            # its own message, never a synthesized phase timeout.
            return False, str(e)[:200], _is_transient_connect_error(e)
        if not query_started.is_set():
            # The submission was QUEUED and never RAN (the worker was busy for
            # the whole reachability budget), so the phase at fault is the
            # wait for the slot, not a query that overran — report the SETUP
            # spelling, the same "one spelling per phase" rule the guard above
            # follows. Only a query that actually STARTED may claim
            # ``probe timeout after …``. Reuses the EXISTING ``query_started``
            # event; no new machinery.
            return False, f"{_PROBE_SETUP_TIMEOUT_MSG}{setup_timeout}s", False
        # NOT retried — a slow/hung DB would just hang again.
        return False, f"probe timeout after {timeout}s", False
    except Exception as e:  # noqa: BLE001, RUF100
        return False, str(e)[:200], _is_transient_connect_error(e)


def _acquire_on_probe_worker(acquire, budget):
    """Run ``acquire`` on the shared probe worker under its OWN deadline (#3446).

    The SDK-acquisition phase used to run on the coordinator's own thread with
    nothing bounding it, so the layered-timeout derivation mixed one enforced
    term (``probe_db``'s single caller deadline) with one unenforced one — and
    an outer bound can only be PROVEN above a sum of ENFORCED terms. Submitting
    the phase here gives it exactly the treatment ``_probe_once`` gives its own
    two phases: a bounded ``Future.result`` wait on the process-lifetime daemon
    worker, so an acquisition that never returns cannot add a thread per probe
    (#2850) and cannot outlast the coordinator silently.

    Returns ``(value, None)`` on success, ``(None, message)`` when the deadline
    expired or the submission was refused. A callable that RAISES propagates to
    the caller, which applies the same never-raise classification
    ``_probe_once`` uses for its phases.
    """
    future = _probe_worker().submit(acquire)
    try:
        return future.result(timeout=budget), None
    except concurrent.futures.TimeoutError as exc:
        if future.done():
            # ``done()`` is True for two different causes and so cannot be read
            # as "the deadline expired": (a) the worker refused the submission
            # (saturated backlog) — the Future already carries its own message;
            # (b) the callable itself raised a TimeoutError, which on py3.12 IS
            # this exception class. Its own message, never a synthesized phase
            # timeout — the same discrimination ``_probe_once`` applies to its
            # setup phase.
            return None, (str(exc)[:200]
                          or f"{_PROBE_ACQUISITION_TIMEOUT_MSG}{budget}s")
        # A genuine overrun of the enforced phase deadline. The worker is
        # ABANDONED, never cancelled (#2850 / CPython #87185) — the wait is
        # bounded, the worker is not; that is guarantee (c).
        return None, f"{_PROBE_ACQUISITION_TIMEOUT_MSG}{budget}s"


def probe_db(sdk=None, setup_timeout=None, *, acquire=None) -> dict:
    """Deep-check graph-DB connectivity through an SDK's projection.

    Runs a trivial ``RETURN 1`` on the SAME connection graph-touching
    endpoints use (the SDK's projection — registry/shared or default graph
    depending on caller), hard-bounded by a 1.5s worker-thread timeout PER
    ATTEMPT: the redis client's own socket_connect_timeout is 2s
    (``projection._DB_CONNECT_TIMEOUT_DEFAULT``; 5s pre-#2850), still slower
    than a health poll, so a dead URI would otherwise hang the handler. The
    TOTAL bound of THIS FUNCTION is ONE caller deadline, not the per-attempt
    figure and not ``PROBE_DB_TOTAL_TIMEOUT``:

    * no ``setup_timeout`` (the platform liveness shape, #1384): a single
      ``PROBE_TIMEOUT`` covering BOTH phases. The #1565 retry adds no second
      bound — it rides what is LEFT of that deadline
      (``total_budget - elapsed - PROBE_RETRY_DELAY``), so this shape's real
      total is ~``PROBE_TIMEOUT``. ``PROBE_DB_TOTAL_TIMEOUT`` (2 x
      ``PROBE_TIMEOUT`` + the retry delay) survives only as the loose
      outer-alignment figure a coordinator is sized above.
    * explicit ``setup_timeout`` (the MCP ``tortoise_health`` tool): one
      deadline of ``setup_timeout + PROBE_TIMEOUT`` — the cold-start allowance
      plus one reachability budget; the retry rides the remainder of THAT.

    #3446 — ``acquire``: the SDK handle may be handed in either way. ``sdk``
    is the historical shape, where the CALLER acquired it and therefore owns
    that cost (the MCP leg in ``metrics()`` does this). ``acquire`` is a
    zero-arg callable that ``probe_db`` runs ITSELF, as a bounded THIRD phase
    under ``PROBE_SDK_ACQUISITION_BUDGET`` on the shared probe worker. Use it
    whenever a coordinator's outer bound is derived from this function's total:
    it is what makes that outer bound exceed a sum of deadlines the code
    actually ENFORCES, instead of a sum that silently includes an unbounded
    phase. Passing both ``sdk`` and ``acquire`` is a programming error and
    raises ``ValueError``.

    ⚠️ WHAT ENFORCING THIS PHASE COSTS (declared, not hidden): the phase runs
    on the SAME single-slot ``_probe_worker()`` that ``_probe_once`` submits
    its own phases to. Before #3446 a hung acquisition held only the
    coordinator's own daemon thread, so it could not starve a concurrent
    probe; now its overrun holds the shared slot and concurrent probes queue
    behind it. That is the same contention class tracked at #3683 ("false
    degrades a reachable graph when the shared probe slot is occupied ≥ the
    setup allowance") and #4608 (probe-SDK reset racing an in-flight probe),
    and it is the accepted price of having a deadline at all — the alternative
    is the unbounded phase this closes. Two consequences to state plainly: the
    phase's clock starts at SUBMISSION, so under a wedge its error names the
    WAIT rather than a callable that ran; and it is a SECOND occupant of a
    one-slot worker, so capacity has not been widened, only bounded.

    ⚠️ THE DERIVATION IS A SUM OVER A KNOWN PHASE SET: the acquisition, the
    projection setup, and the reachability query. A FOURTH bounded phase added
    inside this function would raise the real inner total without moving
    ``PROBE_HARD_TIMEOUT``, and no constant-vs-constant test can see that —
    extend the outer bound's derivation deliberately when adding one.

    That says nothing about the acquisition's INTERIOR (an unbounded
    cross-process probe plus real queries on the embedded lane — see
    ``PROBE_SDK_ACQUISITION_BUDGET``), so an outer bound sized against this
    function is an inequality between enforced WAITS; the residual is
    stranding, not an unenforced phase.

    #3143: callers may pass a ``setup_timeout`` — the projection-cold-start
    allowance that bears the ``sdk._get_proj()`` cost (connect + a
    size-dependent ``_ensure_indexes()``) ON TOP of the ``PROBE_TIMEOUT``
    reachability budget. The platform liveness gate passes nothing and keeps
    the single shared budget; the callers that opt in are the MCP
    ``tortoise_health`` tool and the selfhost liveness coordinator's refresher
    (``selfhost._probe_db``, #2988 — off the request path, which is what makes
    spending the allowance there free).

    #1565: a single TRANSIENT connection-level failure (embedded redislite
    # server mid-startup / momentarily unreachable under parallel load —
    # refused, DNS/gaierror, redis ConnectionError) is retried ONCE with a
    # short delay before declaring degraded. A persistent outage (stopped
    # FalkorDB, NXDOMAIN) fails the retry identically and still reports
    # degraded within the same sub-second window; a hung black-hole DB is a
    # worker TIMEOUT and is NEVER retried.

    Returns ``{"ok": bool, "latency_ms": float, "error": str|None}`` —
    NEVER raises on a probe failure (``ValueError`` is reserved for a malformed
    CALL, never for the DB), so /health can report ``status: degraded`` instead
    of crashing the process.

    #3143: ``setup_timeout`` is the projection-cold-start allowance (see
    ``_probe_once``). When it is not given, the cold-start and the query SHARE
    the single ``PROBE_TIMEOUT`` budget, so the platform liveness gate keeps
    its tight fast-degrade bound (#1384). The callers that opt in — the MCP
    ``tortoise_health`` tool, and the selfhost liveness coordinator's refresher
    (``selfhost._probe_db``, #2988) — pay a separate allowance for a large
    graph's cold-start instead of being reported unreachable for it. In that
    explicit shape the reachability budget is NOT spent waiting for the single #3062
    worker slot — a query queued behind another probe's cold-start is charged
    to the leftover of the allowance instead, so congestion cannot fake the
    degraded/0 report this change exists to remove (review P1).

    ONE spelling per phase in ``error``: a cold-start that overran its
    allowance, OR consumed the whole shared budget so the reachability query
    never ran, reports ``probe setup timeout after Ns``; only a query that
    actually RAN and overran reports ``probe timeout after Ns``; and the
    acquisition phase reports ``probe sdk acquisition timeout after Ns``
    (#3446). The setup spelling is also the prefix ``probe_db`` uses to keep
    the first attempt's real error when the retry's own remainder was eaten by
    the cold-start.
    """
    start = time.monotonic()
    if acquire is not None:
        if sdk is not None:
            raise ValueError(
                "probe_db takes either an already-acquired sdk or an acquire "
                "callable, never both — the phase's owner must be unambiguous")
        try:
            # May raise: an acquisition callable that itself fails is classified
            # here so this function keeps its never-raise contract for the DB.
            sdk, acquire_error = _acquire_on_probe_worker(
                acquire, PROBE_SDK_ACQUISITION_BUDGET)
        except Exception as exc:  # noqa: BLE001, RUF100
            return {
                "ok": False,
                "latency_ms": round((time.monotonic() - start) * 1000, 1),
                "error": str(exc)[:200],
            }
        if acquire_error is not None:
            return {
                "ok": False,
                "latency_ms": round((time.monotonic() - start) * 1000, 1),
                "error": acquire_error,
            }
    elif sdk is None:
        raise ValueError(
            "probe_db needs an already-acquired sdk or an acquire callable")
    # Resolve at CALL time so the module-global stays monkeypatchable.
    attempt_timeout = PROBE_TIMEOUT
    total_budget = (attempt_timeout if setup_timeout is None
                    else setup_timeout + attempt_timeout)
    ok, error, transient = _probe_once(sdk, setup_timeout=setup_timeout)
    if not ok and transient:
        remaining = total_budget - (time.monotonic() - start) - PROBE_RETRY_DELAY
        if remaining > 0:
            time.sleep(PROBE_RETRY_DELAY)
            # Combined shape on purpose: the retry gets what the deadline has
            # LEFT, split across both phases — not a second allowance.
            retry_ok, retry_error, _ = _probe_once(
                sdk, timeout=remaining, setup_timeout=None)
            if retry_ok:
                ok, error = True, None
            elif not (retry_error or "").startswith(_PROBE_SETUP_TIMEOUT_MSG):
                # The retry reached (and failed at) the query — a genuine
                # verdict; take it.
                ok, error = retry_ok, retry_error
            # else: the remainder was too small to REDO the cold-start, so the
            # retry never observed the DB. Keep the FIRST attempt's real error
            # instead of letting the clock artifact ("probe setup timeout
            # after 0.01s") mask it — the `remaining > 0` guard alone does not
            # cover the 0 < remaining < cold-start window (#3143 review).
    return {
        "ok": ok,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
        "error": error,
    }


class HealthProbe:
    """Single-flight, hard-bounded DB health probe (#2850).

    Wraps a synchronous ``probe_fn`` (never-raise, returns the
    ``{ok, latency_ms, error}`` shape) and guarantees the four things the
    P0 liveness/readiness decouple requires:

    1. **Hard-bounded reads.** ``run()`` returns within ``timeout`` (default
       ``PROBE_HARD_TIMEOUT``, itself derived from ``PROBE_DB_TOTAL_TIMEOUT`` +
       the SDK-acquisition budget; production coordinators pass explicit
       per-plane bounds) even if
       the probe never returns. It never waits on the DB directly and never
       touches the shared asyncio default executor — nothing a stalled DB
       does can queue behind or exhaust it.
    2. **No accumulation from callers.** Concurrent ``run()``/``snapshot()``
       callers JOIN an in-flight probe, so a 15s-interval checker cannot grow
       the work in flight. A second probe starts ONLY by explicitly superseding
       a wedged one, capped per episode (item 3) — so "at most ONE probe runs
       at a time" does not hold while a wedge is being superseded. See the
       guarantee summary at ``PROBE_MAX_SUPERSEDES``.
    3. **No per-check thread leak.** Exactly one daemon thread per in-flight
       probe; a wedged probe is superseded at most ``max_supersedes`` times
       per WEDGE EPISODE, never once per check. NOT a process-lifetime cap:
       the counter resets on any live completion (``self._supersedes = 0`` in
       ``_run``, because a completion proves the wedge cleared), so a backend
       that wedges, recovers and wedges again can strand up to
       ``max_supersedes`` threads per episode. What keeps the steady state
       bounded in practice is the caller's LAYERED TIMEOUT — keep ``timeout``
       ABOVE the probe function's own total for THAT caller's shape (for the
       platform shape ~``PROBE_TIMEOUT``; ``PROBE_DB_TOTAL_TIMEOUT`` is only a
       loose outer-alignment figure, and the per-attempt figure is NEVER the
       right one) so the inner timeout normally fires first and the
       worker returns by itself, making abandonment the exception instead of
       the norm. Since #3446 that total is a sum of ENFORCED deadlines for a
       probe function that hands ``probe_db`` its SDK acquisition, so the
       ordering is provable for those callers — but PROVABLE is not
       ACHIEVED: a phase that overruns its own deadline is still abandoned,
       not cancelled. Abandoning a probe does not stop its thread
       (CPython #87185), so stranding remains the residual — see the guarantee
       summary at ``PROBE_MAX_SUPERSEDES``.
    4. **Honest staleness.** While a probe is wedged, the last *good* result
       stops being reported as live once it is older than ``stale_after``
       (or once a superseded probe has been in flight that long) — /health
       flips to ``degraded`` instead of serving a fossil "ok".
    5. **Optional fail-closed reads (`fresh_only`).** /health is allowed to
       serve "stale but honest" last-known-good; READINESS is not. With
       ``fresh_only=True`` a completed result is only served while it is
       younger than the READ BUDGET (``timeout``), and a read that exhausts
       its budget fails closed instead of returning the previous verdict.
       Without this flag a readiness check that JOINED a probe which then
       outlived the budget returned the pre-outage ``{ok: True}`` for the
       whole ``stale_after`` window (30s) — a 200 "connected" while the
       control plane or DB was already dead (review P1).

    Escape hatches: ``reset()`` (tests / ops recovery) drops the in-flight
    state so a fresh probe may start; ``info()`` exposes the age/supersede
    counters for observability and tests.
    """

    def __init__(self, probe_fn, *, timeout: float = PROBE_HARD_TIMEOUT,
                 stale_after: float = PROBE_STALE_AFTER,
                 max_supersedes: int = PROBE_MAX_SUPERSEDES,
                 poll_interval: float = PROBE_POLL_INTERVAL,
                 fresh_only: bool = False,
                 refresh_budget: float | Callable[[], float] = PROBE_STALE_AFTER) -> None:
        self._probe_fn = probe_fn
        self._timeout = timeout
        self._stale_after = stale_after
        self._max_supersedes = max_supersedes
        self._poll_interval = poll_interval
        #: Age at which ``snapshot()``'s self-heal may start a probe when the
        #: background refresher appears dead (round-3 review P2). Kept
        #: separate from ``stale_after`` so the read path is gated on the
        #: REFRESHER's period: without this, every ``/health`` read started a
        #: probe once the previous finished, turning an unauthenticated
        #: SKIP_AUTH route into a request→DB-query amplifier duplicating the
        #: background loop. A CALLABLE is resolved per read (round-4 review
        #: P2): ``_HEALTH_PROBE``'s operator-configurable refresher period is
        #: 0.5-15s, so freezing the import-time default here let ``/health``
        #: start a duplicate probe once the operator's period exceeded it.
        self._refresh_budget = refresh_budget
        #: ``True`` -> /health semantics (stale-but-honest last-known-good is
        #: acceptable); ``False`` -> readiness semantics (never serve a verdict
        #: older than the read budget; see the class docstring item 5).
        self._fresh_only = fresh_only
        self._cv = threading.Condition()
        self._running = False
        self._seq = 0
        self._worker: threading.Thread | None = None
        self._result: dict | None = None
        self._started_at = 0.0
        self._completed_at = 0.0
        self._supersedes = 0

    # ── internals (all callers hold self._cv) ────────────────────────────

    def _refresh_budget_now(self) -> float:
        """Resolve the self-heal gate to a float (round-4 review P2).

        ``refresh_budget`` may be a float (tests, fixed coordinators) or a
        zero-arg callable that returns one (the selfhost ``_HEALTH_PROBE``
        wires it to ``monitoring.health_probe_interval`` — the shared resolver
        since #2988, re-exported by ``hosted_api`` as ``_health_probe_interval``
        — so the gate always equals the refresher's ACTUAL, operator-resolved
        period rather than the import-time default).
        A callable that raises falls back to ``PROBE_STALE_AFTER`` — the gate
        must never break an unauthenticated ``/health`` read.
        """
        budget = self._refresh_budget
        if callable(budget):
            try:
                budget = float(budget())
            except Exception:  # noqa: BLE001, RUF100 — a read path must not raise
                logger.warning(
                    "health probe refresh_budget callable failed — using the "
                    "default %.0fs gate", PROBE_STALE_AFTER, exc_info=True)
                return PROBE_STALE_AFTER
        return budget

    def _start_locked(self) -> None:
        self._seq += 1
        seq = self._seq
        self._running = True
        self._started_at = time.monotonic()
        self._worker = threading.Thread(
            target=self._run, args=(seq,),
            name=HEALTH_PROBE_THREAD_NAME, daemon=True,
        )
        self._worker.start()

    def _run(self, seq: int) -> None:
        try:
            result = self._probe_fn()
        except BaseException as exc:
            result = {"ok": False, "latency_ms": 0.0,
                      "error": f"{type(exc).__name__}: {exc}"[:200]}
        if not isinstance(result, dict):
            result = {"ok": False, "latency_ms": 0.0,
                      "error": "probe returned a non-dict result"}
        with self._cv:
            # Ignore a result from a generation superseded by reset()/supersede
            # — the newest worker is the authority.
            if seq == self._seq:
                self._running = False
                self._result = result
                self._completed_at = time.monotonic()
                self._supersedes = 0  # a live completion proves the wedge cleared
            self._cv.notify_all()

    def _fail_closed_locked(self, why: str) -> dict:
        """Explicit NOT-ok verdict for a read that has no fresh basis.

        Readiness (``fresh_only=True``) must never answer 200 from a verdict
        it cannot vouch for; this is the shape it returns instead.
        """
        latency = float(self._result.get("latency_ms") or 0.0) \
            if self._result else 0.0
        return {"ok": False, "latency_ms": latency, "error": why}

    def _view_locked(self, now: float) -> dict:
        """Best honest result available right now (never blocks).

        The freshness window depends on the coordinator's mode:

        * ``fresh_only=False`` (/health): serve a completed result while it is
          younger than ``stale_after`` (the documented "stale but honest"
          window), else report it stale.
        * ``fresh_only=True`` (readiness): serve a completed result only while
          it is younger than the READ BUDGET (``timeout``) — a readiness gate
          may not claim a freshness it does not have — else fail closed.
        """
        if self._result is not None:
            age = now - self._completed_at
            max_age = self._timeout if self._fresh_only else self._stale_after
            if age <= max_age:
                return dict(self._result)
            if self._fresh_only:
                return self._fail_closed_locked(
                    f"probe result too old for readiness "
                    f"({age:.1f}s > {max_age:g}s budget)")
            return {
                "ok": False,
                "latency_ms": float(self._result.get("latency_ms") or 0.0),
                "error": f"probe result stale ({age:.0f}s old)",
            }
        return {
            "ok": False,
            "latency_ms": 0.0,
            "error": ("probe in flight (%.1fs)" % (now - self._started_at)
                      if self._running else "probe has not produced a result"),
        }

    # ── public API ───────────────────────────────────────────────────────

    def begin(self, *, if_stale: bool = False) -> None:
        """Start a probe if none is live. Never blocks, never accumulates.

        A probe still running past ``stale_after`` is superseded (up to
        ``max_supersedes`` times) so a genuine recovery can be observed; its
        abandoned daemon thread is bounded by that cap.

        ``if_stale=True`` gates the *self-heal* on the last result being
        older than ``refresh_budget`` (round-3 review P2). Only
        ``snapshot()`` — the unauthenticated ``/health`` read path — uses it:
        otherwise each read started a fresh probe as soon as the previous
        finished, so ``/health`` amplified into one DB ``RETURN 1`` per
        request and duplicated the background refresher. A dead refresher is
        still recovered, just no sooner than the refresh budget (one probe,
        not one per read). ``run()``/``read()``/``wait()`` keep the
        unconditional self-heal — they ARE the refresher/readiness callers
        and must start work when asked.
        """
        with self._cv:
            if self._running and self._worker is not None and self._worker.is_alive():
                if (time.monotonic() - self._started_at >= self._stale_after
                        and self._supersedes < self._max_supersedes):
                    self._supersedes += 1
                    self._start_locked()
                return
            # Idle, or the worker died without recording a result — self-heal,
            # but (opt-in) only when the recorded result is genuinely stale.
            if (if_stale and self._result is not None
                    and time.monotonic() - self._completed_at < self._refresh_budget_now()):
                return
            self._start_locked()

    async def run(self) -> dict:
        """Coalesced, hard-bounded read: start/join the probe, return ≤ timeout.

        Concurrent callers share the one in-flight probe (single-flight). The
        result is the completed probe when it lands in time, otherwise the
        last honest view (degraded once stale) — /health always answers.
        """
        self.begin()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while True:
            with self._cv:
                if not self._running:
                    return self._view_locked(time.monotonic())
            remaining = deadline - loop.time()
            if remaining <= 0:
                with self._cv:
                    if self._fresh_only:
                        # The probe we joined is still in flight and this read
                        # exhausted its budget: fail closed. Returning the
                        # coordinator's older cached verdict here is the
                        # fail-open the review caught (a 200 for the whole
                        # stale_after window after the plane died).
                        return self._fail_closed_locked(
                            f"probe did not complete within {self._timeout:g}s "
                            "— readiness fails closed")
                    return self._view_locked(time.monotonic())
            await asyncio.sleep(min(self._poll_interval, remaining))

    def read(self) -> dict:
        """Synchronous non-waiting view (for callers already off the loop)."""
        self.begin()
        with self._cv:
            return self._view_locked(time.monotonic())

    def snapshot(self) -> dict:
        """Pure in-memory view — the /health read path (#2850 item 2).

        Returns the last known probe result (or an honest "no result yet" /
        "stale" verdict) WITHOUT waiting for anything: no I/O, no thread
        hand-off, no lock held across a submit, no await. It returns in
        microseconds and therefore CANNOT queue behind a stalled DB, a
        saturated executor, or another caller — which is the whole point of
        decoupling liveness from work.

        ``begin(if_stale=True)`` is still called, and that is deliberate: it
        is non-blocking (worst case it starts the ONE bounded single-flight
        probe daemon thread), so it cannot delay this call, but it means a
        refresher task that died cannot pin the report to a frozen verdict
        forever. The self-heal is gated on the refresh budget (round-3 review
        P2) so a healthy refresher is never duplicated by the read path —
        ``/health`` stays zero-I/O and is no longer a request→DB-query
        amplifier. Freshness normally comes from the background probe loop
        (see ``hosted_api._health_probe_loop``); this is the recovery path.
        """
        self.begin(if_stale=True)
        with self._cv:
            return self._view_locked(time.monotonic())

    def wait(self, timeout: float | None = None) -> dict:
        """Synchronous bounded wait (for non-async probes/tests)."""
        self.begin()
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)
        while True:
            with self._cv:
                if not self._running:
                    return self._view_locked(time.monotonic())
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if self._fresh_only:
                        return self._fail_closed_locked(
                            f"probe did not complete within {self._timeout:g}s "
                            "— readiness fails closed")
                    return self._view_locked(time.monotonic())
                self._cv.wait(min(self._poll_interval, remaining))

    def info(self) -> dict:
        """Observability: in-flight/stale state (additive /health metadata)."""
        with self._cv:
            now = time.monotonic()
            return {
                "in_flight": self._running,
                "in_flight_s": (round(now - self._started_at, 2)
                                if self._running else None),
                "result_age_s": (round(now - self._completed_at, 2)
                                 if self._completed_at else None),
                "supersedes": self._supersedes,
            }

    def reset(self) -> None:
        """Drop in-flight/result state; a later probe starts fresh.

        The previous worker (if wedged) is abandoned as a daemon thread —
        never joined. Used by tests and by app startup so each process/app
        instance begins from a clean probe state.
        """
        with self._cv:
            self._seq += 1  # invalidate any in-flight worker's write
            self._running = False
            self._worker = None
            self._result = None
            self._started_at = 0.0
            self._completed_at = 0.0
            self._supersedes = 0
            self._cv.notify_all()


# ── #2850 item 3: event-loop heartbeat ────────────────────────────────────
#
# The liveness question is "can the event loop still run?" — so the answer
# has to be produced BY the loop. A thread that checks "is the process up?"
# answers a question nobody asks: the process is up, that is why the thread
# is running at all. Only work the loop itself performs proves the loop is
# scheduling.
#
# ``time.monotonic`` — NOT wall clock. systemd's ``WatchdogSec`` and Erlang's
# ``heart`` both have documented false-trigger failures on a clock step (NTP
# slew/step, laptop suspend/resume, VM snapshot restore): a backward wall-clock
# step makes the last tick look arbitrarily old and kills a healthy app; a
# forward step makes a hung app look fresh. Monotonic never goes backwards and
# is immune to both.
LOOP_HEARTBEAT_INTERVAL = 0.25   # tick period of the loop-side heartbeat task

#: Age (seconds) past which /healthz reports 503 "the loop is not currently
#: scheduling".
#:
#: WHY 90s, and what the signal does NOT mean (review P2). This app still
#: makes SYNCHRONOUS calls on the event loop, so "the loop did not tick" is
#: NOT proof of a wedge — a busy loop looks identical to a hung one:
#:   * the LLM extractor is called synchronously from ``_capture_session_impl``
#:     with HTTP timeouts of 60s (``tortoise/models.py`` urlopen(timeout=60),
#:     ``tortoise/model_adapters.py`` httpx timeout=(10, 60)), and the v2 path
#:     runs several stages;
#:   * the FalkorDB client's own socket timeouts are 2s connect / 10s read
#:     (``projection._socket_timeouts``), and one request can run a per-turn
#:     loop of synchronous queries.
#: A low threshold therefore flaps 503 on ordinary slow requests. The value is
#: set ABOVE the longest single bounded on-loop operation (the 60s LLM call) so
#: it distinguishes "wedged" from "busy" for the single-operation case. It is
#: still not a load signal: multi-stage extraction can legitimately exceed it,
#: which is exactly why the destructive self-kill below is opt-in and gated on
#: an idle workload, and why the durable remedy is offloading these synchronous
#: calls rather than tuning this number.
LOOP_STALE_AFTER = 90.0

#: monotonic timestamp of the last loop tick; 0.0 == the loop has NEVER ticked
#: (which is reported as stale, never as healthy).
_LOOP_HEARTBEAT_AT: float = 0.0
_LOOP_HEARTBEAT_TICKS = 0
_LOOP_HEARTBEAT_LOCK = threading.Lock()


def heartbeat_record(at: float | None = None) -> None:
    """Record a loop tick. Called from the loop; ``at`` is a test seam."""
    global _LOOP_HEARTBEAT_AT, _LOOP_HEARTBEAT_TICKS
    with _LOOP_HEARTBEAT_LOCK:
        _LOOP_HEARTBEAT_AT = time.monotonic() if at is None else at
        _LOOP_HEARTBEAT_TICKS += 1


def heartbeat_read() -> tuple[float, int]:
    """``(last_tick_monotonic, tick_count)`` — pure memory read."""
    with _LOOP_HEARTBEAT_LOCK:
        return _LOOP_HEARTBEAT_AT, _LOOP_HEARTBEAT_TICKS


def loop_heartbeat_age() -> float | None:
    """Seconds since the last loop tick, or ``None`` if it never ticked."""
    at, ticks = heartbeat_read()
    if ticks == 0:
        return None
    return max(0.0, time.monotonic() - at)


def loop_is_stale(threshold: float | None = None) -> bool:
    """True when the loop has not ticked within ``threshold`` seconds.

    A loop that has NEVER ticked is stale (fail-closed): "no evidence of
    liveness" must never be reported as live.
    """
    age = loop_heartbeat_age()
    if age is None:
        return True
    return age > (LOOP_STALE_AFTER if threshold is None else threshold)


def loop_heartbeat_info() -> dict:
    """Observability payload shared by /health and the /healthz listener.

    #3498 item 1: carries the loop-lag baseline (max / p99 / sample count over
    the bounded window) beside the staleness signal, so the effect of the
    control-plane offload seam is measurable instead of inferred.
    """
    age = loop_heartbeat_age()
    lag = loop_lag_stats()
    return {
        "loop_age_ms": None if age is None else round(age * 1000, 1),
        "loop_ticks": heartbeat_read()[1],
        "loop_stale": True if age is None else age > LOOP_STALE_AFTER,
        "loop_lag_max_ms": lag["max_ms"],
        "loop_lag_p99_ms": lag["p99_ms"],
        "loop_lag_samples": lag["samples"],
    }


# ── #3498 item 1: continuous loop-lag baseline ────────────────────────────
# The heartbeat answers "did the loop tick?". This records the COMPLEMENTARY
# quantity — the lag of each tick — so the effect of the control-plane offload
# seam is measurable (max + p99) instead of inferred from a stale timestamp.
# A blocked loop shows up as a tick that fired far later than its interval.
_LOOP_LAG_SAMPLES: deque[float] = deque(maxlen=4096)
_LOOP_LAG_LOCK = threading.Lock()


def record_loop_lag(lag_s: float) -> None:
    """Record ONE tick's scheduling lag (seconds, clamped at 0)."""
    value = max(0.0, float(lag_s))
    with _LOOP_LAG_LOCK:
        _LOOP_LAG_SAMPLES.append(value)
        LOOP_LAG.observe(value)


def loop_lag_stats() -> dict:
    """``{samples, max_ms, p99_ms, mean_ms}`` over the bounded window.

    ``None`` percentiles when no tick has been sampled yet — never 0.0, which
    would read as "perfectly on time" for a loop that never ran.
    """
    with _LOOP_LAG_LOCK:
        samples = sorted(_LOOP_LAG_SAMPLES)
    if not samples:
        return {"samples": 0, "max_ms": None, "p99_ms": None, "mean_ms": None}
    count = len(samples)
    # nearest-rank p99: the smallest value at or above the 99th percentile.
    idx = min(count - 1, max(0, math.ceil(0.99 * count) - 1))
    return {
        "samples": count,
        "max_ms": round(samples[-1] * 1000, 3),
        "p99_ms": round(samples[idx] * 1000, 3),
        "mean_ms": round(sum(samples) / count * 1000, 3),
    }


def reset_loop_lag() -> None:
    """Clear the loop-lag window (test/ops seam)."""
    with _LOOP_LAG_LOCK:
        _LOOP_LAG_SAMPLES.clear()


async def loop_heartbeat_task(interval: float = LOOP_HEARTBEAT_INTERVAL) -> None:
    """Forever-loop that proves the event loop is scheduling.

    Started as a task on the app's loop at startup. If the loop blocks — a
    synchronous DB call on the loop, a CPU-bound encode, a deadlock — this
    task stops being scheduled and the timestamp goes stale, which is exactly
    the signal ``/healthz`` (port 9090) and the stall watchdog read.

    #3498: each tick also records its own LAG (actual elapsed minus the
    requested interval) into the bounded ``LOOP_LAG`` histogram/window, so the
    loop-lag baseline is sampled continuously and available as max/p99.
    """
    while True:
        heartbeat_record()
        started = time.monotonic()
        await asyncio.sleep(interval)
        record_loop_lag(time.monotonic() - started - interval)


def _reset_heartbeat() -> None:
    """Test seam: pretend the loop has never ticked."""
    global _LOOP_HEARTBEAT_AT, _LOOP_HEARTBEAT_TICKS
    with _LOOP_HEARTBEAT_LOCK:
        _LOOP_HEARTBEAT_AT = 0.0
        _LOOP_HEARTBEAT_TICKS = 0


# ── #2850 item 4: the DEDICATED liveness listener (own port) ─────────────
#
# Fixed interface contract: port 9090, bound 0.0.0.0, ``GET /healthz`` ->
# 200 (loop fresh, OR loop stale with work in flight) / 503 (loop stale AND
# nothing in flight). A non-routing top-level Fly check points here, so the
# answer must NOT depend on the app's event loop: this is a plain
# ``ThreadingHTTPServer`` on its own daemon thread, in its own OS thread(s),
# touching nothing but in-memory state. It never uses
# ``asyncio.to_thread`` or the app's default executor, so it cannot be
# queued behind ~89 stalled DB calls the way /health was.
#
# ⚠ SIGNAL CONTRACT (round-3 review P1): this is an ALERTING signal, NOT an
# availability/routing gate. It must not be wired as one (e.g. a Fly check
# whose failure de-registers the machine) until the synchronous DB/LLM work
# in ``_capture_session_impl`` is offloaded off the event loop — that is the
# durable follow-up. Reason: a merely BUSY loop is not a wedged loop, and
# this app runs seconds-long synchronous DB queries and 60s-timeout LLM calls
# on the loop, so a legitimate request can exceed ``LOOP_STALE_AFTER``. With a
# loop-stale 503 and 2xx-means-healthy semantics, that would de-register the
# sole machine — a total outage. The handler therefore reports 503 ONLY for
# stale-and-idle (a genuinely wedged loop); see ``_HealthzHandler.do_GET``.
#
# Deliberately NOT reusing ``serve_health``/``_Handler`` above: that handler
# is Bearer-auth-gated in prod mode (#7395) and a platform check cannot send
# a token; it also exposes /metrics, which does not belong on a liveness
# port. Extending it would require an auth bypass flag on the shared handler
# — a fail-open foot-gun next to #7395.
HEALTHZ_PORT = 9090
HEALTHZ_BIND = "0.0.0.0"
#: Socket timeout for one healthz request. ``BaseHTTPRequestHandler.timeout``
#: defaults to ``None`` (a client that completes the handshake and then sends
#: nothing holds a thread + fd FOREVER — slowloris against the one listener
#: that must survive overload). A liveness check is a few hundred bytes; 5s is
#: generous and still bounded.
HEALTHZ_HANDLER_TIMEOUT_S = 5.0
#: How long (TOTAL wall clock) a rejected socket is drained for before it is
#: closed, and the byte ceiling on that drain. Closing a socket that still has
#: the unread request in its receive queue makes the kernel send RST instead of
#: FIN, which can discard a reply that was already written — the round-2 review
#: observation (the first overload probe saw 503, every subsequent one saw
#: ``ConnectionResetError``). A bounded drain buys a clean close without
#: letting a trickle turn the drain itself into the slowloris it prevents.
HEALTHZ_REJECT_DRAIN_TIMEOUT_S = 0.2
HEALTHZ_REJECT_DRAIN_MAX_BYTES = 8192
#: Max concurrent healthz handler threads. The whole point of this listener is
#: that it keeps answering when the app is overloaded, so it must bound its own
#: work: a saturated listener answers 503 (the platform sees an unhealthy
#: machine) instead of spawning unbounded threads and dying.
HEALTHZ_MAX_THREADS = 8
#: Small accept backlog — keep the kernel queue short so overload is shed at
#: accept time instead of being buffered into an unbounded connection set.
HEALTHZ_REQUEST_QUEUE_SIZE = 5


def _drain_unread(sock, timeout: float, max_bytes: int) -> None:
    """Consume a bounded amount of an unread request so ``close`` sends FIN.

    Closing a socket that still has data in its receive queue makes the kernel
    send RST instead of FIN, which can discard a reply that was already written
    — the round-2 review failure (a 503 that the client saw as
    ``ConnectionResetError``). Draining first buys a clean close.

    Bounded on BOTH axes — total wall clock (``timeout``) and bytes
    (``max_bytes``) — so a client trickling one byte at a time cannot turn the
    drain into the slowloris it exists to avoid. Best-effort: every failure is
    swallowed and the caller still closes. ``socket.timeout`` is an ``OSError``
    subclass, so one except covers it.
    """
    deadline = time.monotonic() + timeout
    remaining = max_bytes
    while remaining > 0:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        try:
            sock.settimeout(left)
            chunk = sock.recv(min(remaining, 1024))
        except OSError:
            return
        if not chunk:
            return
        remaining -= len(chunk)


def _drain_unread_now(sock, max_bytes: int) -> None:
    """Non-blocking drain of bytes ALREADY buffered — for the ACCEPT path.

    ``_drain_unread`` above WAITS (up to its timeout), which is fine on a
    handler thread but not on the accept loop (round-3 review P1: ~0.2s per
    rejected connection serialized accepts, capping the listener at ~5 conn/s
    and letting a flood starve the one endpoint that must never go dark).
    This variant flips the socket non-blocking, consumes only what is already
    in the receive queue, and returns immediately when the queue empties or
    ``max_bytes`` is reached — no wall-clock wait, ever. The kernel still sends
    FIN on the subsequent close because the queued request has been consumed,
    which is the RST this drain exists to prevent.

    Blocking mode is RESTORED before returning, so the caller
    (``_reject_overloaded``) leaves the socket in the mode it found it.
    ``BlockingIOError``/``InterruptedError`` mean "nothing more is queued right
    now" and stop the loop; any other ``OSError`` is swallowed (best-effort).
    """
    try:
        sock.setblocking(False)
    except OSError:
        return
    try:
        remaining = max_bytes
        while remaining > 0:
            try:
                chunk = sock.recv(min(remaining, 1024))
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if not chunk:
                break
            remaining -= len(chunk)
    finally:
        with contextlib.suppress(OSError):
            sock.setblocking(True)


class _HealthzHandler(BaseHTTPRequestHandler):
    """Liveness only: one in-memory heartbeat read, HTTP 200 or 503.

    The 503 condition is STALE **AND** IDLE (round-3 review P1): a busy loop
    is not a wedged loop, so a stale heartbeat with a request in flight
    (``workload_is_idle()`` false) reports 200. This is an ALERTING signal,
    not an availability/routing gate — do not wire its failure to machine
    de-registration until the synchronous on-loop work is offloaded (the
    durable follow-up).

    Hardened because this port is unauthenticated and must never become the
    way the machine is exhausted (review P1/P2):
      * ``timeout`` bounds a single recv of a slow/partial request, AND a total
        deadline (see ``setup``) bounds the WHOLE request — the per-recv
        timeout alone cannot stop a client trickling one byte at a time
        (review round 2);
      * ``protocol_version = HTTP/1.1`` + ``Connection: close`` on every reply
        (no keep-alive thread pinning);
      * request bodies are rejected unread — a liveness GET has none;
      * the version banner is a fixed product string, not
        ``BaseHTTP/0.6 Python/<exact version>``, so an unauthenticated endpoint
        discloses nothing about the interpreter.
    """

    # #2850 review P2: do not advertise the Python interpreter version.
    server_version = "tortoise-healthz"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    #: PER-RECV socket timeout (slow/partial request).
    timeout = HEALTHZ_HANDLER_TIMEOUT_S
    #: TOTAL deadline for one connection (round-2 review). ``timeout`` above is
    #: applied to each ``recv``, so a client trickling one byte at a time never
    #: trips it and pins a handler thread forever. This is the whole-request
    #: bound enforced by ``setup``.
    total_timeout = HEALTHZ_HANDLER_TIMEOUT_S

    def setup(self):
        super().setup()
        # Review round 2: ``timeout`` above is a PER-RECV timeout, so a client
        # that sends one byte every few seconds never trips it and holds a
        # handler thread FOREVER. With ``HEALTHZ_MAX_THREADS=8`` a trickle can
        # pin every slot permanently and make the liveness signal go dark — the
        # worst possible outcome for the one endpoint that must never go dark.
        # Enforce a TOTAL deadline instead: a one-shot timer shuts the socket
        # down, which unblocks the reader and lets the handler (and its slot)
        # exit. ``finish`` always cancels it. The timer inherits this handler
        # thread's daemon flag, so it can never keep the process alive.
        timer = threading.Timer(self.total_timeout, self._expire_connection)
        timer.daemon = True
        self._deadline_timer = timer
        timer.start()

    def _expire_connection(self) -> None:
        """Total-deadline enforcement: unblock the handler so it can exit."""
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def finish(self) -> None:
        timer = getattr(self, "_deadline_timer", None)
        if timer is not None:
            timer.cancel()
        with contextlib.suppress(OSError):
            super().finish()

    def handle_one_request(self) -> None:
        # A client cut off by the TOTAL deadline (``setup``) can have its 4xx
        # reply land on a socket that is already shut down. That is a normal
        # outcome of shedding an abusive connection, not a server error, and
        # ``socketserver`` would otherwise print a traceback for it — same
        # policy as ``_send``. Suppress only socket-teardown errors, never a
        # bare OSError.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError,
                                 ConnectionAbortedError):
            super().handle_one_request()

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        # A check that gave up and disconnected mid-reply is normal; it must
        # not print a traceback from the server's handler thread.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            self.wfile.write(body)

    def _has_body(self) -> bool:
        """True when the request carries (or ambiguously frames) a body."""
        if self.headers.get("Transfer-Encoding"):
            return True
        try:
            return int(self.headers.get("Content-Length") or 0) > 0
        except (TypeError, ValueError):
            return True

    def do_GET(self):
        if self._has_body():
            # Never read the body: reading an attacker-sized body is the
            # resource the listener must not spend. Close instead.
            self._send(413, {"status": "body-not-allowed"})
            # Deliver the 413 cleanly: a bounded drain (never the whole body)
            # is what stops the close from becoming an RST that eats the
            # reply. See HEALTHZ_REJECT_DRAIN_*.
            _drain_unread(self.connection, HEALTHZ_REJECT_DRAIN_TIMEOUT_S,
                          HEALTHZ_REJECT_DRAIN_MAX_BYTES)
            return
        if self.path.split("?", 1)[0] != "/healthz":
            self._send(404, {"status": "not-found"})
            return
        info = loop_heartbeat_info()
        stale = bool(info["loop_stale"])
        # STALE **AND** IDLE — the same idle predicate the stall watchdog
        # uses (#2850 round-3 review P1). A busy loop is not a wedged loop:
        # the live top-level Fly check points here (fly.toml
        # [checks.loop_liveness]) and 2xx-means-healthy to flyctl, so
        # answering 503 for a legitimate synchronous request would fail
        # EVERY deploy and cry wolf on the operator signal. It cannot
        # de-register the machine — the proxy ignores top-level checks for
        # routing; only flyctl's deploy wait reads it. Only "nothing has
        # ticked AND nothing is in flight" is a genuinely wedged loop.
        idle = workload_is_idle()
        wedged = stale and idle
        self._send(503 if wedged else 200,
                   {"status": "stale" if wedged else "ok",
                    "workload_idle": idle, **info})

    def _method_not_allowed(self):
        self._send(405, {"status": "method-not-allowed", "allow": "GET"})

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def log_message(self, *args):
        pass  # silence per-request logs (Fly polls this every few seconds)


class _HealthzServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that does not do a DNS lookup while binding.

    #2850: ``HTTPServer.server_bind`` calls ``socket.getfqdn(host)`` to fill
    ``server_name``. For ``0.0.0.0`` that is a reverse-DNS round trip with no
    PTR record to find: **measured at 5.0 s** on a macOS dev box, and it runs
    on the EVENT LOOP because ``start_health_listener`` is called from the
    lifespan. That is exactly the blocking-startup hazard this issue removes —
    do not reintroduce it in the bind path of its own fix. ``server_name`` only
    ever feeds the ``Server:`` response header, which a liveness probe never
    reads, so take the bind address verbatim instead.
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = HEALTHZ_REQUEST_QUEUE_SIZE

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Bound the handler fleet (review P1): ThreadingHTTPServer spawns one
        # OS thread per connection with no ceiling, and this listener is the
        # unauthenticated one that must survive overload. Shedding load with a
        # 503 is honest and keeps the accept loop alive; unbounded threads is
        # how the last-resort liveness port becomes the outage.
        self._slots = threading.BoundedSemaphore(HEALTHZ_MAX_THREADS)

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            self._reject_overloaded(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            # The thread never started / never reached
            # process_request_thread's finally — return the slot here or the
            # listener bleeds capacity.
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    @staticmethod
    def _reject_overloaded(request) -> None:
        """Answer 503 directly on the accepted socket (no thread spawned).

        Runs ON THE ACCEPT LOOP, so it must never WAIT on the client in
        either direction (round-3 review P1):
          * the whole reject runs with the socket NON-BLOCKING — a client
            advertising a closed receive window can no longer pin the accept
            loop for the old 5s send timeout;
          * the unread request is drained with ``_drain_unread_now``, which
            consumes only bytes ALREADY in the receive queue and returns at
            zero wall-clock cost. A drain that *waits* (the old
            ``_drain_unread``, 0.2s) cost ~0.2s of accept time per rejected
            connection, capping the listener at ~5 conn/s: a
            ``request_queue_size`` of 5 stays full, further SYNs drop, and an
            unauthenticated flood starves the one endpoint that must never go
            dark — strictly worse than the RST it replaced.
        """
        body = b'{"status":"overloaded"}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"Retry-After: 1\r\n\r\n" + body
        )
        try:
            request.setblocking(False)
        except OSError:
            return
        # Non-blocking: a full send buffer (zero-window client) raises
        # BlockingIOError instead of blocking — best-effort delivery is the
        # right trade for keeping the accept path free.
        with contextlib.suppress(OSError):
            request.sendall(response)
        # Drain what is already queued (non-blocking) so the caller's close
        # sends FIN rather than RST, which would discard the 503 we just
        # wrote (review round 2). This restores blocking mode before
        # returning.
        _drain_unread_now(request, HEALTHZ_REJECT_DRAIN_MAX_BYTES)


_HEALTHZ_LOCK = threading.Lock()
_HEALTHZ_SERVERS: dict[tuple[str, int], ThreadingHTTPServer] = {}


def _healthz_port_from_env() -> int:
    """``TORTOISE_HEALTHZ_PORT`` or the 9090 contract, NEVER raising.

    Review P2: the previous inline ``int(os.environ.get(...) or HEALTHZ_PORT)``
    ran BEFORE the try in ``start_health_listener`` and the try only caught
    ``OSError`` — so ``TORTOISE_HEALTHZ_PORT=http`` raised ``ValueError`` and
    ``-1``/``70000`` raised ``OverflowError``, neither of which is an OSError,
    and both escaped the UNGUARDED ``_start_liveness`` call and aborted
    ``lifespan.startup()``. A one-character typo crash-looped the machine at
    boot. Parse defensively here and fall back to the contract with a loud log.
    """
    raw = os.environ.get("TORTOISE_HEALTHZ_PORT")
    if raw is None or not str(raw).strip():
        return HEALTHZ_PORT
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.error("TORTOISE_HEALTHZ_PORT=%r is not an integer — using the "
                     "default %d", raw, HEALTHZ_PORT)
        return HEALTHZ_PORT
    if not (0 <= port <= 65535):
        logger.error("TORTOISE_HEALTHZ_PORT=%r is outside 0-65535 — using "
                     "the default %d", raw, HEALTHZ_PORT)
        return HEALTHZ_PORT
    return port


def _healthz_required() -> bool:
    """Truthy spellings of ``TORTOISE_HEALTHZ_REQUIRED`` (review P2).

    The previous exact ``== "1"`` match made ``true``/``yes``/``on`` silently
    do nothing — a deploy-time contract believed to be enforced and not.
    #4097: resolved through the declared truthy contract.
    """
    return is_truthy(os.environ.get("TORTOISE_HEALTHZ_REQUIRED"))


def resolve_healthz_target(port: int | None = None, bind: str | None = None,
                           ) -> tuple[str, int]:
    """Resolve the listener's ``(bind, port)`` from args > env > contract.

    Defaults are the FIXED deployment contract: ``0.0.0.0`` on port ``9090``
    (a non-routing top-level Fly check points at it). Split out from
    ``start_health_listener`` so the contract is assertable without binding.

    Never raises for a bad port — an env typo must degrade to the default with
    an ERROR log, not abort boot (see ``_healthz_port_from_env``).
    """
    if port is None:
        port = _healthz_port_from_env()
    elif not isinstance(port, int) or not (0 <= port <= 65535):
        logger.error("invalid healthz port %r — using the default %d",
                     port, HEALTHZ_PORT)
        port = HEALTHZ_PORT
    if bind is None:
        bind = os.environ.get("TORTOISE_HEALTHZ_BIND") or HEALTHZ_BIND
    return bind, port


def start_health_listener(port: int | None = None, bind: str | None = None,
                          ) -> ThreadingHTTPServer | None:
    """Bind + serve ``/healthz`` on its own port/thread (idempotent).

    Defaults come from ``TORTOISE_HEALTHZ_PORT`` (9090) and
    ``TORTOISE_HEALTHZ_BIND`` (0.0.0.0) so the port is a deployment
    contract, overridable for tests and self-host.

    Idempotent per (bind, port): the listener is process-lifetime, and the
    app lifespan can run more than once in one process (TestClient reuse,
    in-process reload), which must not rebind or leak servers.

    Returns the server, or ``None`` when the bind failed. A bind failure is
    logged at ERROR and does not abort startup: the process still serves, and
    the platform check on the missing port fails LOUDLY at the platform
    layer rather than silently. Set ``TORTOISE_HEALTHZ_REQUIRED=1`` to make it
    fatal instead (deploy-time contract enforcement).
    """
    bind, port = resolve_healthz_target(port, bind)
    key = (bind, port)
    with _HEALTHZ_LOCK:
        existing = _HEALTHZ_SERVERS.get(key)
        if existing is not None:
            return existing
        try:
            server = _HealthzServer((bind, port), _HealthzHandler)
        except (OSError, OverflowError, ValueError) as exc:
            msg = (f"#2850: could not bind the dedicated liveness listener on "
                   f"{bind}:{port} — {exc}")
            if _healthz_required():
                raise RuntimeError(msg) from exc
            logger.error("%s (the platform liveness check on that port will fail)", msg)
            return None
        threading.Thread(target=lambda: server.serve_forever(poll_interval=0.1),
                         name="tortoise-healthz", daemon=True).start()
        # Key on the REQUESTED port (so a repeat call is a no-op) AND on the
        # resolved one: with port=0 (tests) the OS picks the port, and a
        # caller that learned the real port must not trigger a second bind.
        _HEALTHZ_SERVERS[key] = server
        _HEALTHZ_SERVERS[(bind, server.server_address[1])] = server
        logger.info("#2850: dedicated liveness listener on http://%s:%d/healthz",
                    bind, port)
        return server


def stop_health_listener(server: ThreadingHTTPServer | None = None) -> None:
    """Test/lifecycle seam: stop one listener (or every listener) and drop it."""
    with _HEALTHZ_LOCK:
        items = list(_HEALTHZ_SERVERS.items())
        if server is None:
            _HEALTHZ_SERVERS.clear()
        else:
            for k, srv in items:
                if srv is server:
                    del _HEALTHZ_SERVERS[k]
    targets = list({id(s): s for _, s in items}.values()) if server is None else [server]
    for srv in targets:
        with contextlib.suppress(Exception):  # best-effort teardown
            srv.shutdown()
            srv.server_close()


# ── #2850 item 5: loop-stall watchdog (opt-in, default OFF) ───────────────
#
# DEFAULT: DISABLED. The in-process self-kill is OPT-IN via
# ``TORTOISE_LOOP_STALL_EXIT_S``; the default behaviour is to PUBLISH the
# stall signal (the /healthz 503) and NOT exit.
#
# WHY the default flipped (review P0). The pre-review default (30s, exit on
# the first stale poll) would have caused the very outage it was meant to
# prevent. This app still performs UNBOUNDED synchronous work on the event
# loop:
#   * ``_capture_session_impl`` (hosted_api) calls the LLM extractor
#     SYNCHRONOUSLY — not awaited, not offloaded — with HTTP timeouts of 60s
#     (models.py urlopen(timeout=60); model_adapters httpx timeout=(10, 60)),
#     and the v2 path runs several stages back to back;
#   * the same handler runs a per-turn loop of synchronous
#     ``proj.g.query()`` calls (up to 1000 turns; SessionRequest.conversation
#     max_length=1000), i.e. thousands of sequential round trips with no
#     ``await``.
# So ONE authenticated request (free-tier signup is open) can stall the loop
# for 60-180s+, and a heartbeat-age self-kill would ``os._exit`` the process
# mid-request, killing every other tenant's in-flight work — repeatedly, in a
# restart loop. That converts transient provider slowness into a total outage.
#
# The correct split, therefore:
#   * the /healthz signal (above) is the DETECTOR — it publishes staleness;
#   * destructive action belongs OUT OF PROCESS (PR #3064's external watchdog,
#     which is not stuck behind the same loop and can be given a real budget);
#   * the in-process self-kill exists only as an operator escape hatch, and
#     even then it demands (a) N CONSECUTIVE stale windows (hysteresis), (b) an
#     IDLE WORKLOAD — never "the loop is busy serving a request" — and (c) at
#     least one REAL heartbeat tick, so a slow boot can never be mistaken for a
#     wedge. See ``start_stall_watchdog``.
#
# CONTRACT OF THE IDLE GATE (round-2 review P2) — read before enabling. The
# gate is fed by an ASGI in-flight gauge that is incremented for the WHOLE
# lifetime of a request, and an ASGI call does not return until the response
# has finished. This app serves a LONG-LIVED SSE stream on the canonical MCP
# endpoint (``GET /mcp`` with ``Accept: text/event-stream`` via
# mcp/server/streamable_http.py + sse_starlette), so ANY connected MCP client
# pins the gauge >= 1 for as long as it stays connected — the idle predicate
# may then NEVER become true and self-kill never fires. Two consequences to
# state plainly rather than discover later:
#   * the gate ERRORS TOWARD NOT KILLING, so this is a contract/honesty
#     problem, not a safety one; but
#   * the wedge this escape hatch targets (thousands of synchronous on-loop
#     queries from one request) is ITSELF an in-flight request, so the gate
#     VETOES it by construction.
# The in-process self-kill therefore cannot be relied on as wedge detection at
# all: the mechanism that actually handles a wedge is the OUT-OF-BAND watchdog
# (PR #3064), which is not stuck behind the same loop and does not share this
# gauge. Enabling ``TORTOISE_LOOP_STALL_EXIT_S`` buys a GC-pause/hard-hang
# backstop for the no-MCP-client case ONLY.
# A safe refinement (NOT implemented here — premature optimisation of a path
# that is off by default) would be to exclude streaming responses from the
# gauge: the SSE response's ``http.response.start`` fires once at stream open,
# so a gauge release could be driven by a wrapper that ties the in-flight
# window to the request BODY/route work rather than to connection close. That
# needs its own design + tests; do not bolt it on.
#
# The DURABLE fix is offloading those synchronous calls off the loop; until
# that lands, any age-only kill is unsafe.
#
#   * ``TORTOISE_LOOP_STALL_EXIT_S=0`` (or unset) disables the self-kill, which
#     is the shipped default. A NEGATIVE value also disables it, but logs at
#     WARNING because it is indistinguishable from a typo. A NON-FINITE value
#     (``nan``/``inf``) also disables it, but logs at ERROR — ``float()``
#     accepts both and a ``nan`` threshold would otherwise arm a killer that
#     restart-loops a healthy process (round-2 review P2).
#   * when enabled, the threshold is clamped UP to
#     ``max(LOOP_STALL_EXIT_FLOOR_S, 2 * LOOP_STALE_AFTER)`` so the process can
#     never die before /healthz has had a chance to report.
#   * ``os._exit`` rather than a graceful shutdown: a wedged interpreter must
#     not be given the chance to run atexit/finalizers that may themselves
#     block. Non-zero status is what makes the platform restart the machine.
LOOP_STALL_EXIT_S = 0.0            # 0 == self-kill DISABLED (the default)
LOOP_STALL_EXIT_FLOOR_S = 120.0    # lower bound on an ENABLED threshold
LOOP_STALL_EXIT_WINDOWS = 3        # consecutive stale polls required to exit
LOOP_STALL_EXIT_MIN_TICKS = 2      # at least one REAL heartbeat tick before judging


# ── #2850 P0 review: workload-in-flight gauge for the idle gate ───────────
#
# The self-kill must never fire while the loop is merely BUSY. The watchdog
# cannot see that from heartbeat age alone, so the request path increments
# this counter for as long as a request is in flight; a wedged request keeps
# it above zero, which is exactly the case the kill must not act on. Plain
# int behind a lock (the watcher reads it from its own thread).
_WORKLOAD_LOCK = threading.Lock()
_WORKLOAD_IN_FLIGHT = 0


def workload_enter() -> None:
    """Mark one request as in flight (called by the app's middleware)."""
    global _WORKLOAD_IN_FLIGHT
    with _WORKLOAD_LOCK:
        _WORKLOAD_IN_FLIGHT += 1


def workload_exit() -> None:
    """Mark one in-flight request as finished (always in a ``finally``)."""
    global _WORKLOAD_IN_FLIGHT
    with _WORKLOAD_LOCK:
        _WORKLOAD_IN_FLIGHT = max(0, _WORKLOAD_IN_FLIGHT - 1)


def workload_in_flight() -> int:
    """Requests currently in flight — the watchdog's idle predicate."""
    with _WORKLOAD_LOCK:
        return _WORKLOAD_IN_FLIGHT


def _reset_workload() -> None:
    """Test seam: pretend no request is in flight."""
    global _WORKLOAD_IN_FLIGHT
    with _WORKLOAD_LOCK:
        _WORKLOAD_IN_FLIGHT = 0


def workload_is_idle() -> bool:
    """Default idle predicate for the watchdog: no request in flight.

    NOTE (round-2 review P2): this is NOT equivalent to "the loop is not
    wedged". The gauge counts a request for its whole ASGI lifetime, so a
    connected MCP client streaming SSE on ``GET /mcp`` holds this ``False``
    indefinitely, and the synchronous on-loop wedge the self-kill targets is
    itself an in-flight request. See the CONTRACT OF THE IDLE GATE note above
    ``LOOP_STALL_EXIT_S``. It errs toward NOT killing, which is the safe
    direction; wedge detection belongs to the out-of-band watchdog (#3064).
    """
    return workload_in_flight() == 0


def _loop_stall_floor_s() -> float:
    """Minimum safe threshold for the DESTRUCTIVE in-process self-kill.

    The threshold must sit above the total floor: it has to exceed
    ``2 * LOOP_STALE_AFTER`` so /healthz reports the stall before the process
    dies, and it has to clear the absolute floor that keeps a GC pause from
    becoming a crash loop. Shared by the env parser and the programmatic API
    (round-4 review P2) so the two cannot drift.
    """
    return max(LOOP_STALL_EXIT_FLOOR_S, LOOP_STALE_AFTER * 2.0)


def _loop_stall_threshold() -> float:
    """Resolve the self-kill threshold, validating it (review P2).

    Returns ``0`` (disabled) for unset/blank/0, for a non-numeric value, for a
    NON-FINITE value (``nan``/``inf``), and for a negative value. An ENABLED
    threshold is clamped up to the safe floor because ``STALL_EXIT_S <=
    STALE_AFTER`` would kill the process before /healthz could ever report the
    stall, and a sub-second value would turn a GC pause into a permanent crash
    loop.

    ``nan``/``inf`` are rejected rather than forwarded (round-2 review P2):
    ``float()`` accepts both, and neither is caught by the 0/negative guards
    (``nan == 0`` and ``nan < 0`` are both False). A ``nan`` threshold arms the
    killer while making ``age <= threshold_s`` False forever, so a healthy
    ticking loop reads as permanently stale and the process exits via
    ``os._exit(1)`` every ~6s from boot — the exact restart loop the round-1 P0
    removed, back through the validation door. ``inf`` wedges the same path in
    reverse (never fires, but silently claims a guard that cannot act). Both
    DISABLE.
    """
    raw = os.environ.get("TORTOISE_LOOP_STALL_EXIT_S")
    if raw is None or not str(raw).strip():
        return LOOP_STALL_EXIT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("TORTOISE_LOOP_STALL_EXIT_S=%r is not a number — the "
                       "in-process self-kill stays disabled", raw)
        return 0.0
    if not math.isfinite(value):
        logger.error("TORTOISE_LOOP_STALL_EXIT_S=%r is not finite — the "
                     "in-process self-kill is DISABLED. A nan/inf threshold "
                     "arms a watchdog whose staleness comparison can never "
                     "succeed, restart-looping a healthy process", raw)
        return 0.0
    if value == 0:
        return 0.0
    if value < 0:
        logger.warning("TORTOISE_LOOP_STALL_EXIT_S=%r is negative — the "
                       "in-process self-kill is DISABLED (0 is the documented "
                       "disable value)", raw)
        return 0.0
    floor = _loop_stall_floor_s()
    if value < floor:
        logger.error(
            "TORTOISE_LOOP_STALL_EXIT_S=%r is below the safe floor (%.0fs: "
            "it must exceed 2x LOOP_STALE_AFTER=%.0fs so /healthz reports the "
            "stall before the process dies, and must sit above the app's "
            "legitimate synchronous on-loop work) — clamping",
            raw, floor, LOOP_STALE_AFTER)
        return floor
    return value


def start_stall_watchdog(threshold_s: float | None = None, *, exit_fn=None,
                         stop_event: threading.Event | None = None,
                         poll_interval: float | None = None,
                         consecutive_windows: int = LOOP_STALL_EXIT_WINDOWS,
                         min_ticks: int = LOOP_STALL_EXIT_MIN_TICKS,
                         workload_idle_fn=None,
                         ) -> threading.Thread | None:
    """Daemon thread that MAY exit the process if the loop stops ticking.

    DISABLED BY DEFAULT: with ``threshold_s`` resolved to ``0`` (unset env, the
    shipped default) this returns ``None`` and no thread is started — the
    /healthz 503 is the only signal, and destructive action stays out of
    process. Read the constant block above for why that is the safe default.

    When explicitly enabled, an exit requires ALL of:

    1. the heartbeat older than ``threshold_s`` for
       ``consecutive_windows`` CONSECUTIVE polls (hysteresis — a single stale
       sample, or an unlucky pause straddling two polls, must not suffice);
    2. ``min_ticks`` heartbeat ticks recorded (default 2: the synthetic
       startup tick plus at least ONE tick from the real
       ``loop_heartbeat_task``). Without this the watchdog judges a loop that
       has never demonstrated liveness and can kill the process during a slow
       boot — before the machine has bound a socket (review P2);
    3. ``workload_idle_fn()`` truthy. The default reads
       :func:`workload_in_flight`, so the watchdog refuses to kill while a
       request is in flight — the exact shape of "the loop is blocked doing
       legitimate work". A raising predicate is treated as NOT idle
       (fail-closed, i.e. no kill).

    ``exit_fn`` defaults to ``os._exit``; tests inject it so a test can never
    kill the test process. ``stop_event`` lets the lifespan stop the watchdog
    on shutdown — without it, a clean shutdown (which cancels the heartbeat
    task, making the heartbeat legitimately stale) would look like a wedge.
    """
    if threshold_s is None:
        threshold_s = _loop_stall_threshold()
    # Round-3 review P2: also reject a NON-FINITE programmatic threshold, not
    # just at the env boundary. ``nan <= 0`` is False, so the old guard armed
    # a killer whose ``age <= nan`` comparison is False forever — the same
    # restart-loop the env parser rejects. This matches the docstring's
    # promise ("a non-finite value DISABLES").
    if not math.isfinite(threshold_s) or threshold_s <= 0:
        logger.warning(
            "#2850: in-process loop-stall self-kill DISABLED (threshold=%s). "
            "The /healthz listener still reports staleness; destructive "
            "action belongs to the out-of-band watchdog.", threshold_s)
        return None
    if exit_fn is None:
        # Round-4 review P2: the env path floors an enabled threshold, but a
        # PROGRAMMATIC finite-but-absurd value (``1e-9``) passed the
        # isfinite/<=0 guard and armed the DESTRUCTIVE default. With
        # ``poll_interval = max(0.1, min(2.0, threshold/4))`` that is ~0.3s of
        # a stale heartbeat before ``os._exit`` — the "GC pause becomes a
        # crash loop" the floor exists to prevent. Floor only the destructive
        # path: an injected ``exit_fn`` is the test seam and keeps its exact
        # threshold.
        floor = _loop_stall_floor_s()
        if threshold_s < floor:
            logger.warning(
                "start_stall_watchdog: programmatic threshold %.3fs is below "
                "the safe floor (%.0fs) for the destructive exit path — "
                "clamping; a sub-second threshold turns a GC pause into a "
                "crash loop", threshold_s, floor)
            threshold_s = floor
        exit_fn = os._exit
    if stop_event is None:
        stop_event = threading.Event()
    if workload_idle_fn is None:
        workload_idle_fn = workload_is_idle
    if poll_interval is None:
        poll_interval = max(0.1, min(2.0, threshold_s / 4.0))
    consecutive_needed = max(1, int(consecutive_windows))

    def _watch() -> None:
        stale_windows = 0
        while not stop_event.wait(poll_interval):
            # (2) judge only a loop that has demonstrated liveness.
            _, ticks = heartbeat_read()
            if ticks < min_ticks:
                stale_windows = 0
                continue
            age = loop_heartbeat_age()
            if age is not None and age <= threshold_s:
                stale_windows = 0
                continue
            # (3) never kill a loop that is busy doing work.
            try:
                idle = bool(workload_idle_fn())
            except Exception:  # a broken predicate must not authorise a kill
                idle = False
            if not idle:
                stale_windows = 0
                continue
            # (1) hysteresis.
            stale_windows += 1
            if stale_windows < consecutive_needed:
                logger.warning(
                    "#2850 loop-stall watchdog: stale window %d/%d "
                    "(age %s, threshold %.1fs, workload idle)",
                    stale_windows, consecutive_needed,
                    "never" if age is None else f"{age:.1f}s", threshold_s)
                continue
            logger.critical(
                "#2850 loop-stall watchdog: event loop has not ticked for %s "
                "across %d consecutive windows (threshold %.1fs) with no "
                "request in flight — exiting so the platform restarts a clean "
                "process", "never" if age is None else f"{age:.1f}s",
                consecutive_needed, threshold_s)
            with contextlib.suppress(BaseException):
                exit_fn(1)  # we are exiting anyway
            return

    thread = threading.Thread(target=_watch, name="tortoise-loop-watchdog", daemon=True)
    thread.start()
    return thread


def _counter_val(counter) -> int:
    """Extract current value of a Counter via public collect() API."""
    for m in counter.collect():
        for s in m.samples:
            if s.name.endswith("_total") and not s.name.endswith("_created_total"):
                return int(s.value)
    return 0


def metrics(sdk=None, setup_timeout=None) -> dict:
    """Return {status, db, falkordb, graph_size, last_ingest, errors, uptime}.

    ``db`` is the deep-check result ({ok, latency_ms, error}) added by
    #1384; ``falkordb`` keeps the legacy message form for backward compat.

    #2202 (health-truthful): the probe target is ``sdk`` when the caller
    passes one, otherwise the module-global handle registered by
    ``register()``. Serving surfaces pass the SDK whose graph they actually
    serve (mcp_server.tortoise_health passes the request-scoped team SDK), so
    the report reflects the REAL graph. The pre-#2202 code probed ONLY the
    module-global, which the stdio entrypoint registers but the HTTP
    daemon/hosted surfaces never do — tortoise_health reported
    degraded/no_sdk_registered while the same daemon's /health (fresh SDK
    probe of the same DB) said ok.

    A missing probe target (no ``sdk=`` and nothing registered — reachable
    only from the standalone serve_health server or bare direct calls) is an
    HONEST intermediate state: ``status="unknown"`` with ``db.ok=None`` —
    never "degraded". "degraded" means an observed probe FAILURE (a real
    component failing); an absent registration is an unverified handle, not
    a broken DB, so reporting degraded there is the lie #2202 removes.

    ``graph_size`` is counted ONLY on a successful probe (review fix, #2202):
    a dead/hung DB must degrade fast (the bounded RETURN-1 probe — ONE
    ``PROBE_TIMEOUT`` deadline in the default shape, or ``setup_timeout +
    PROBE_TIMEOUT`` when an explicit allowance is passed) and never drag an
    extra unbounded taxonomy round-trip onto the health call, and its failure
    must not inflate the very ``errors`` field this response reports. A
    degraded report carries graph_size 0 with the probe error. The count
    itself (``taxonomy()``) carries NO budget of its own — it is safe only
    because it runs after a successful ``RETURN 1`` (a reachable server is
    expected to answer label counts promptly; that is an assumption, not a
    measurement), so the MCP tool's total latency is ``setup_timeout +
    PROBE_TIMEOUT`` PLUS that round-trip. If the probe SUCCEEDS but the count
    raises, the report is ``status="ok"`` with ``graph_size 0`` and an
    incremented ``errors`` counter — the failure is recorded, never raised, so
    ``ok`` + 0 is deliberately indistinguishable from a genuinely empty graph
    and callers needing certainty must read ``errors``.

    #3143: ``setup_timeout`` (named to match ``probe_db``'s keyword — the
    previous ``probe_setup_timeout`` SHADOWED the module function of the same
    name inside this body, review P2) is the projection-cold-start allowance
    threaded to ``probe_db``. It is the MCP health tool's seam: the platform
    liveness gate passes nothing (tight 1.5s, fail-fast), while
    ``tortoise_health`` passes its call-time-resolved allowance
    (``probe_setup_timeout()``) so a reachable graph whose cold-start exceeds
    1.5s is reported ``ok`` with its real ``graph_size`` instead of
    ``degraded``/0.
    """
    target = sdk if sdk is not None else _sdk
    if target is None:
        db = {"ok": None, "latency_ms": 0.0, "error": "no_sdk_registered"}
    else:
        db = probe_db(target, setup_timeout=setup_timeout)
    if db["ok"] is True:
        status = "ok"
    elif db["ok"] is False:
        status = "degraded"
    else:
        status = "unknown"
    graph_size = 0
    try:
        if target is not None and db["ok"] is True:
            graph_size = sum(target.taxonomy().values())
    except Exception:
        record_error()
    return {
        "status": status,
        "db": db,
        "falkordb": "connected" if db["ok"] is True else db["error"] or "unreachable",
        "graph_size": graph_size,
        "last_ingest": _last_ingest,
        "errors": _counter_val(ERROR_COUNT),
        "uptime": round(time.monotonic() - _start, 2),
    }


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Auth gate (#7395): require valid Bearer token in prod mode
        from tortoise.auth import require_auth, is_dev_mode  # lazy — #67  # noqa: I001
        if not is_dev_mode():
            headers = {k.lower(): v for k, v in self.headers.items()}
            if not require_auth(headers):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return

        if self.path == "/health":
            self._handle_endpoint("health", lambda: json.dumps(metrics()).encode(),
                                  "application/json")
        elif self.path == "/metrics":
            self._handle_endpoint("metrics", generate_latest,
                                  "text/plain; version=0.0.4")
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_endpoint(self, endpoint: str, body_fn, content_type: str):
        REQUEST_COUNT.labels(endpoint=endpoint).inc()
        with REQUEST_LATENCY.time():
            try:
                body = body_fn()
            except Exception:
                record_error()
                self.send_response(500)
                self.end_headers()
                return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence logs


def serve_health(port: int = 9090, bind: str = "127.0.0.1") -> None:
    """Standalone /health + /metrics HTTP server. Auth-gated in prod mode (#7395)."""
    HTTPServer((bind, port), _Handler).serve_forever()
