"""#14186 — ONE embedder seam: in-process | worker | external, plus bounded
micro-batching of query embeds.

WHY THIS EXISTS. The query's embedding runs *inside* the request, on the same
shared cores that serve everything else, and it costs 2-4 s there (measured;
``embed_encode`` is 2,078-4,003 ms inside a live search). Every comparable puts
that step behind a worker, a purpose-built index, or an API
(``docs/architecture/SCALING-ROADMAP.md`` §1 on branch
``plan-private-beta-scale``). The two gaps are **the same change**: to move the
encode out of the request you need a seam, and the seam is where batching
belongs — a batch is worthless without a queue, and a queue without a bound
trades tail latency for throughput.

THREE PROPERTIES THIS MODULE GUARANTEES, and the constraint each one serves.

1. **ONE interface, THREE backings, chosen by config** — ``TORTOISE_EMBED_BACKING``
   ∈ :data:`BACKINGS`. Every caller asks the seam; the backing decides *where*
   the work runs. Switching backings is an environment change and nothing else
   — no call site moves, no code changes, no redeploy of a different branch.
   ⛔ The seam deliberately does **not** pick a vendor: the external backing
   speaks a documented, vendor-neutral JSON contract (see
   :class:`ExternalBacking`), so an operator can point it at any provider and
   reverse the choice by unsetting one variable.

2. **Micro-batching with a HARD maximum wait.** Concurrent query embeds are
   coalesced into one encode, and the collection window is bounded from the
   moment the batch's first ticket is DEQUEUED — it is never extended by a
   later arrival. That is the whole point: *"batching improves efficiency but can worsen tail latency"*,
   and the bound is what stops a busy collector from turning a 40 ms wait into
   an unbounded one. A batch also fires early when it reaches
   :data:`DEFAULT_MAX_BATCH`. Both bounds are observable
   (:func:`seam_status`) and neither is a suggestion.

   ⛔ DEQUEUE, NOT SUBMISSION. Anchoring at the ticket's submission timestamp
   looks equivalent and is not: a ticket that waited in the queue while the
   PREVIOUS batch was encoding carries a submission time up to one encode in
   the past, so its deadline is already expired and the batch fires with ONE
   ticket — every time, under exactly the sustained load batching exists for.
   That made the window do nothing on the two backings whose window is non-zero
   (`worker`/`external`), while the 0 ms default coalesced correctly and hid it
   (#14186 review F1).

3. **p50/p95 per backing, exported.** An operator who moved the backing has no
   way to tell whether it helped without a distribution, and a mean over a
   bimodal latency profile answers the wrong question. See :func:`seam_status`,
   published on ``GET /health`` under ``embed_seam``.

WHAT IT DELIBERATELY DOES NOT DO.

* ⛔ **It does not change the intra-op thread pin** (#10296, #14186's
  contradiction test (c)). The coalescer runs encodes on ONE long-lived
  collector thread, and every backing calls ``ensure_embed_threads()`` on the
  thread that runs the forward pass — which is exactly the per-thread re-assert
  #10296 landed, not a new pin. The pin's number is untouched; moving the
  forward pass to a dedicated thread is a scheduling change whose effect must be
  MEASURED before any number moves.
* ⛔ **It does not derive or drop a record-truth vector** (#5004). The seam is a
  *router*: it returns the backing's vectors or None, and every existing
  decline/count path above it (``encode_for_row``'s bounded gate, the
  ``#13224`` write budget, ``embed_metering``) keeps owning those decisions.
  A backing that fails raises or returns None and the callers degrade exactly as
  they do today.
* ⛔ **It does not fall back silently.** A worker that will not start, or an
  external endpoint that times out, is a DECLINE with a named reason
  (:func:`last_decline`) — never a quiet second encode in-process. A silent
  fallback is how "we moved the embedder off the request path" becomes untrue
  while every dashboard says ok (the ``#7795`` class).
* ⛔ **No MCP/SDK surface change.** Nothing here adds a tool or a public SDK
  method; ``seam_status`` is a module function read by ``/health``.

DEFAULT POSTURE AND ROLLBACK. Both are stated per backing in
:data:`DEFAULT_MAX_WAIT_MS_FOR_BACKING` and in :func:`resolve_backing`:

* ``inprocess`` (the default) — the collection window is **0 ms**. A warm
  encode is 25-43 ms (#13224's measurement), so a 20 ms wait would be a
  *comparable* addition to the very latency it is meant to hide; delaying every
  query to batch it is a bad trade at that scale. With a 0 ms window the
  collector still coalesces **opportunistically**: whatever is already queued
  when the collector finishes the previous batch rides the next one, so a
  loaded process batches without an uncontended query ever waiting. This is
  continuous batching with no admission cost.
* ``worker`` / ``external`` — the window is :data:`DEFAULT_MAX_WAIT_MS` (20 ms),
  because there the marginal request cost is dominated by a round trip and
  20 ms is noise beside it.
* **Rollback is one variable.** ``TORTOISE_EMBED_BACKING=inprocess`` (or
  unsetting it — the default) returns the process to the pre-#14186 path on the
  next encode: same encode call, same thread pin, same decline semantics. The
  seam is on the path either way; nothing has to be reverted in code, and no
  state is left behind that a restart would need.

⛔ **KNOWN RESIDUAL — ONE ENCODE RUNS AT A TIME.** The collector is a single
thread and calls the backing from it, so concurrent queries are SERIALISED behind
the queue instead of running on N read-lane workers in parallel. For the shipped
configuration that is the intended change and not a compromise: the encoder is
pinned to ONE intra-op thread (#10296), so N concurrent encodes on a shared box
do not buy N× throughput — they buy N× contention, and the 2-4 s ``embed_encode``
this issue measures *is* that contention, on the same cores the event loop needs.
Serialising them behind a batch is the scheduling change; the 0.36 s/item figure
was measured on a single batched encode and is only reachable in this shape. It
IS a real ceiling, though, and it is stated rather than assumed away: a
multi-collector variant (N parallel encodes, batching per collector) is a
FOLLOW-UP TO BE GATED ON A MEASUREMENT, because on a wide, otherwise-idle box it
could win on wall time — and "could" is not a reason to ship it.
"""
from __future__ import annotations

import collections
import json
import logging
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Protocol

_logger = logging.getLogger(__name__)

# ── the closed vocabulary ─────────────────────────────────────────────────────

#: The in-process embedder — :class:`tortoise.embeddings.EmbeddingModel`. The
#: DEFAULT, and the behaviour every process had before #14186.
BACKING_INPROCESS = "inprocess"
#: A local worker PROCESS (:mod:`tortoise.embed_worker`) spoken to over a UNIX
#: socket. Same encoder, out of the serving process's address space and off its
#: GIL — the step that makes the API tier disposable (Track S1).
BACKING_WORKER = "worker"
#: A remote HTTP endpoint with a documented, vendor-neutral JSON contract.
BACKING_EXTERNAL = "external"

#: The closed set. An unknown value is REFUSED at resolve time with a warning,
#: never coerced — a typo'd backing that silently ran in-process would read as
#: "the migration is done" while nothing moved (the fail-open class
#: ``MEMORY.md`` records).
BACKINGS: tuple[str, ...] = (BACKING_INPROCESS, BACKING_WORKER, BACKING_EXTERNAL)

# ── configuration (all read per call, never frozen at import) ────────────────
#
# Per call, deliberately: a test, an operator's `fly ssh` shell, or a lane's
# bulk job can retarget the seam without a process restart, and a lever that
# only reaches the code at import time is not the lever this module documents
# (the #10296 lesson, one layer up).

ENV_BACKING = "TORTOISE_EMBED_BACKING"
ENV_MAX_WAIT_MS = "TORTOISE_EMBED_MAX_WAIT_MS"
ENV_MAX_BATCH = "TORTOISE_EMBED_MAX_BATCH"
ENV_TIMEOUT_S = "TORTOISE_EMBED_QUERY_TIMEOUT_S"
#: Where the worker listens. Default: a per-process path under the temp dir.
ENV_WORKER_SOCKET = "TORTOISE_EMBED_WORKER_SOCKET"
#: The interpreter the worker is spawned with. Default: the parent's own
#: ``sys.executable``, so the worker imports the SAME tortoise and the same
#: extras — a worker on a different interpreter is a different encoder, and
#: that divergence would present as "the vectors changed after we moved the
#: embedder" with no reason in it.
ENV_WORKER_PYTHON = "TORTOISE_EMBED_WORKER_PYTHON"
#: How long the CLIENT waits for a worker to become READY at start-up. It is a
#: separate budget from `ENV_TIMEOUT_S` on purpose — see
#: :data:`DEFAULT_WORKER_STARTUP_S` — because the two measure different things
#: and one number cannot honestly serve both.
ENV_WORKER_STARTUP_S = "TORTOISE_EMBED_WORKER_STARTUP_S"
ENV_EXTERNAL_URL = "TORTOISE_EMBED_EXTERNAL_URL"
ENV_EXTERNAL_TOKEN = "TORTOISE_EMBED_EXTERNAL_TOKEN"
ENV_EXTERNAL_MODEL = "TORTOISE_EMBED_EXTERNAL_MODEL"

#: EVERY lever this module reads, in one place. ``tests/conftest.py`` clears this
#: tuple between tests, so a lever that is added without being listed here leaks
#: into the next test — and a leaked ``TORTOISE_EMBED_BACKING=worker`` makes every
#: later ``compute_embeddings`` skip its local-model check and try to spawn a
#: process, which presents as a scatter of unrelated failures rather than one
#: (review F8: ``ENV_WORKER_STARTUP_S`` was missing from a hand-written list).
#: A row pins this tuple against the module's own ``ENV_*`` constants, so the list
#: cannot drift silently.
SEAM_ENV_LEVERS: tuple[str, ...] = (
    ENV_BACKING,
    ENV_MAX_WAIT_MS,
    ENV_MAX_BATCH,
    ENV_TIMEOUT_S,
    ENV_WORKER_SOCKET,
    ENV_WORKER_PYTHON,
    ENV_WORKER_STARTUP_S,
    ENV_EXTERNAL_URL,
    ENV_EXTERNAL_TOKEN,
    ENV_EXTERNAL_MODEL,
    # Read by the WORKER process, not by this module
    # (``tortoise.embed_worker.ENV_WORKER_REQUIRE_PEER``), and listed here because
    # this tuple is the env-lever CENSUS the test fixture clears — a lever the
    # worker honours is still a lever that can leak between tests.
    "TORTOISE_EMBED_WORKER_REQUIRE_PEER",
)

#: The interactive-traffic collection window (ms) for the backings where a round
#: trip dominates. SCALING-ROADMAP §1 quotes "small micro-batches for interactive
#: traffic … a hard maximum batch wait"; the issue's own example bound is
#: "<= 50 ms". 20 ms is inside it and is a small fraction of the 2-4 s encode it
#: batches — the point of the number is that the tail it adds is BOUNDED and
#: visible, not that it is zero.
DEFAULT_MAX_WAIT_MS = 20.0

#: The per-backing window. See the module docstring's "DEFAULT POSTURE".
DEFAULT_MAX_WAIT_MS_FOR_BACKING: dict[str, float] = {
    # ⛔ 0 is not "batching disabled" — it is "do not DELAY for a batch". The
    # collector still drains whatever arrived while the previous encode ran, so
    # a loaded process coalesces and an idle one never waits. A 20 ms window
    # here would be a comparable addition to the 25-43 ms warm encode it hides.
    BACKING_INPROCESS: 0.0,
    BACKING_WORKER: DEFAULT_MAX_WAIT_MS,
    BACKING_EXTERNAL: DEFAULT_MAX_WAIT_MS,
}

#: The largest batch a single encode call will carry. Matches the encoder's own
#: historical ``batch_size`` default (32) and the 32-in-11.6 s measurement the
#: issue quotes (0.36 s/item). A larger batch raises per-item tail latency
#: without improving the per-item cost, so this is a ceiling, not a target.
DEFAULT_MAX_BATCH = 32

#: How long a query embed may take ONCE THE BATCH HAS FIRED. Bounds the worker
#: and external backings, which can otherwise hang on a dead socket or a stalled
#: provider; the in-process backing keeps today's unbounded behaviour because a
#: forward pass it cannot interrupt anyway would only turn into "we abandoned a
#: vector that was about to arrive" (#5004's class).
#: ⛔ This is a REQUEST budget. It does NOT bound start-up: a worker pays the
#: cold import and the model load before it is ready (#13674), which no 30 s
#: request budget can cover, so start-up has its own lever
#: (:data:`ENV_WORKER_STARTUP_S`). Conflating the two is what made the FIRST
#: query after every worker boot decline — see
#: :meth:`WorkerBacking._ensure_started`.
DEFAULT_TIMEOUT_S = 30.0

#: How long the client waits for a worker to become READY. The worker publishes
#: readiness by CREATING its socket only after its encoder is loaded
#: (``embed_worker._preload_encoder``), so the client's wait is the boot cost.
#: That cost is the cold import the worker exists to move out of the serving
#: process — 156.5 s, measured in #13674 — plus ``EmbeddingModel``'s own load
#: bound (``embeddings._LOAD_TIMEOUT_S``, 300 s), because a socket that exists
#: means the encoder finished loading, not merely that the module imported. 600 s
#: covers both terms with headroom; a budget below their sum would convert a slow
#: but HEALTHY boot into the exact decline this lever exists to remove.
DEFAULT_WORKER_STARTUP_S = 600.0

#: How many observations each latency reservoir keeps. Enough for a useful p95
#: (a p95 over fewer than ~100 samples is an interpolation, not a measurement)
#: and small enough that the memory is a rounding error against a 432 MB model.
LATENCY_WINDOW = 512

# ── ceilings for the levers that bound a WAIT ───────────────────────────────
#
# ⛔ FLOORED IS NOT ENOUGH — a lever that is only floored accepts ``inf``, and
# ``inf`` is the exact value that turns a bound into a park (review F1 at
# ``76e58a21bb``). ``_env_float`` refuses anything non-finite outright; these
# ceilings exist for the finite-but-absurd value (``1e9``), which is the same
# park with a longer fuse. Each ceiling is stated against something measured, not
# picked as a round number.

#: The collection window (ms). Two orders of magnitude above the 20 ms default and
#: well above the issue's "<= 50 ms" posture: past a minute the window is no
#: longer batching interactive traffic, it is shelving it. ``submit``'s valve is
#: ``window + budget + slack``, so an unbounded window is an unbounded TICKET wait.
MAX_WAIT_MS_CEILING = 60_000.0

#: One fired request's round trip (s). An hour: beyond any provider answer, and
#: short of the ``inf`` that would let ``sock.settimeout`` / an ``urlopen`` bound
#: outlive the process it is supposed to protect.
TIMEOUT_S_CEILING = 3_600.0

#: A worker's boot budget (s). A cold import plus a model load was measured at
#: 156.5 s + <= 300 s (#13674), so the 600 s default already carries headroom; a
#: day is two orders past that, and anything longer only means a read parked for
#: the life of the process when the boot never completes.
WORKER_STARTUP_S_CEILING = 86_400.0


# ── small total helpers ──────────────────────────────────────────────────────


def _env_float(name: str, default: float, *, minimum: float = 0.0,
               maximum: float | None = None) -> float:
    """Read a float lever, refusing an unusable value with a warning.

    TOTAL: an unparseable value falls back to ``default`` and says so. Silently
    coercing ``"20ms"`` to 0 would disable the very bound this module exists to
    enforce, and the operator would have no way to see that.

    ⛔ NOT-FINITE IS UNUSABLE, AND IT IS THE DANGEROUS SHAPE (review F1 at
    ``76e58a21bb``). ``float("inf")`` satisfies any ``>= minimum`` test and is not
    NaN, so the guard this replaces (``not (value >= minimum) or value != value``)
    ACCEPTED ``TORTOISE_EMBED_MAX_WAIT_MS=inf``: the collector's
    ``deadline = ... + window_s`` became infinite, so a lone query's batch could
    only ever fire on ``max_batch``, and ``submit``'s valve parked the caller
    effectively forever — the unbounded wait the issue's contradiction test (a)
    makes unshippable, reachable by one typo. Hence ``math.isfinite`` (which also
    covers NaN, since ``isfinite(nan)`` is False) AND a ``maximum``: a finite but
    absurd value is the same park with a longer fuse.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        value = -1.0
    if not math.isfinite(value) or not (value >= minimum) or \
            (maximum is not None and value > maximum):
        _logger.warning(
            "%s=%r is not a finite number >= %s%s — using the default %s",
            name, raw, minimum,
            "" if maximum is None else f" (and <= {maximum})", default)
        return default
    return value


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Integer twin of :func:`_env_float` — same total, same refusal."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = 0
    if value < minimum:
        _logger.warning(
            "%s=%r is not an integer >= %s — using the default %s",
            name, raw, minimum, default)
        return default
    return value


def resolve_backing() -> str:
    """The active backing name, from ``TORTOISE_EMBED_BACKING``.

    Unset ⇒ :data:`BACKING_INPROCESS` (the pre-#14186 path). An unknown value is
    REFUSED and the default applies, with a WARNING naming it: a typo must not
    be able to present as "the seam is deployed" while the encode is still in the
    request.
    """
    raw = os.environ.get(ENV_BACKING)
    if raw is None or not raw.strip():
        return BACKING_INPROCESS
    value = raw.strip().lower()
    if value not in BACKINGS:
        _logger.warning(
            "%s=%r is not one of %s — refusing it and running %s (#14186)",
            ENV_BACKING, raw, "/".join(BACKINGS), BACKING_INPROCESS)
        return BACKING_INPROCESS
    return value


def encodes_in_process() -> bool:
    """Does the seam run its forward pass in THIS process? (#14186 review F5)

    ``True`` for ``inprocess`` (the default) and ``False`` for ``worker`` /
    ``external``. It resolves the LEVER only — it constructs no backing and
    retires no collector — so a boot path can ask it without side effects.
    ⛔ THE ONE QUESTION THE EAGER LOADS ASK. ``hosted_api``'s lifespan pre-warm and
    ``sdk``'s engine-init kick both exist to make the IN-PROCESS forward pass warm.
    With the encoder elsewhere they buy nothing and cost exactly what the migration
    exists to remove: 432 MB resident in the serving process (3.66 GB with two
    overlapping loads, #13674), on top of the worker's own copy.

    ⛔ DELIBERATELY NOT APPLIED INSIDE ``EmbeddingModel.start_warm_up``. The write
    and read readiness paths read that call's return value as "is a load in
    flight"; answering ``None`` there would convert a healthy LOCAL load into a
    decline on a deployment that still uses the in-process encoder for some path
    (the documented ``fallback_snapshot`` residual). Gate the eager KICKS, not the
    primitive.

    ⛔ AND IT IS APPLIED ONE LAYER EARLIER TOO (review P1): both readiness bounds
    consult it before anything else, because they exist to bound a LOCAL load and
    with the encoder elsewhere their only effect was to start that load and then
    decline the write/read that triggered it — a vectorless row or a keyword-only
    read on a deployment whose encoder was healthy. ⛔ THE TWO ANSWERS DIFFER
    (review P2, cycle 5): the READ bound answers ``None`` (ready) — every read is
    a round trip, which :func:`backing_booting` can start and wait for — while the
    WRITE bound answers ``backing_cold`` while the backing BOOTS, because a
    bounded write may not pay a boot (#4055/#13224). Both are decided above the
    ``TORTOISE_EMBEDDER_WARMUP`` opt-out, which is a statement about a local
    background lane, not about where the encoder lives.

    ⛔ WHAT IT DOES NOT COVER (review P2): the residual paths that still encode
    in-process and call ``EmbeddingModel.get()`` synchronously —
    ``entity_resolver`` / ``cross_lens`` arrive through ``_encode``, not through
    the seam — so with a worker backing those pay the cold load on the request
    thread instead of at boot. Documented in ``docs/infra-runbook.md`` §4.1.1e;
    routing them through the seam is a behaviour change, not a gate.
    """
    return resolve_backing() == BACKING_INPROCESS


def resolve_max_wait_ms(backing: str | None = None) -> float:
    """The collection window in ms — the operator's lever, else the backing default."""
    name = backing if backing is not None else resolve_backing()
    default = DEFAULT_MAX_WAIT_MS_FOR_BACKING.get(name, DEFAULT_MAX_WAIT_MS)
    return _env_float(ENV_MAX_WAIT_MS, default, minimum=0.0,
                      maximum=MAX_WAIT_MS_CEILING)


def resolve_max_batch() -> int:
    """The ceiling on one encode's batch length."""
    return _env_int(ENV_MAX_BATCH, DEFAULT_MAX_BATCH, minimum=1)


def resolve_timeout_s() -> float:
    """The bound on one fired request's round trip (worker / external)."""
    return _env_float(ENV_TIMEOUT_S, DEFAULT_TIMEOUT_S, minimum=0.1,
                      maximum=TIMEOUT_S_CEILING)


def resolve_worker_startup_s() -> float:
    """The bound on a worker BECOMING READY — not on a request answering it.

    Kept distinct from :func:`resolve_timeout_s` because the quantities differ by
    an order of magnitude: a warm request is milliseconds, a boot is the cold
    import plus the model load (#13674). A single lever cannot bound both without
    either parking a read for the boot cost or declining a healthy worker on a
    request-sized budget.
    """
    return _env_float(
        ENV_WORKER_STARTUP_S, DEFAULT_WORKER_STARTUP_S, minimum=1.0,
        maximum=WORKER_STARTUP_S_CEILING)


#: Slack added to a ticket's delivery bound, on top of the phase budget it guards:
#: it covers the fire itself and ordinary scheduling jitter, not a second wait.
_TICKET_WAIT_SLACK_S = 1.0


def resolve_ticket_wait_s(timeout_s: float,
                          backing: EmbeddingBacking | None = None) -> float:
    """How long ONE ticket may wait for its batch to FIRE — sized per PHASE.

    ⛔ SAME LESSON AS :data:`ENV_WORKER_STARTUP_S`, ONE LAYER OUT. ``submit``
    waits for a batch to fire, and while a ``worker`` backing is still BOOTING
    the encode that will fire it is inside :meth:`WorkerBacking._ensure_started`
    — legally paying the cold import plus the model load (#13674). Bounding that
    wait by the REQUEST budget abandoned the ticket after ~31 s of a boot that
    may take 600 s, so the first query after every worker boot declined on a
    healthy worker: the very failure the startup lever was split off to remove,
    left in the layer the caller actually blocks in.

    So the bound is ``window + (start-up budget while a boot is in flight, else
    the request budget) + slack``. It stays BOUNDED in both phases — the issue's
    contradiction test (a) is untouched — and the tight backstop is kept for the
    steady state, where a wedged collector must not be able to park a read lane
    worker for a boot's worth of seconds (#13528).

    This is a helper rather than arithmetic inside ``submit`` so that no caller
    can re-introduce a single ceiling by passing the wrong budget: the backing in
    use is ASKED what phase it is in (:meth:`WorkerBacking.startup_pending`), and
    a backing that does not implement it is answered with the request budget.

    ⛔ THE BACKING IS PEEKED, NEVER RESOLVED. :func:`active_backing` CONSTRUCTS a
    backing on first use and retires the collector it replaces — calling it from
    ``submit`` retired the very collector holding the ticket, which then declined
    a perfectly healthy query. Sizing a bound is a READ, so it reads.
    """
    window_s = max(resolve_max_wait_ms(), 0.0) / 1000.0
    budget_s = (resolve_worker_startup_s() if backing_booting(backing)
                else timeout_s)
    return window_s + budget_s + _TICKET_WAIT_SLACK_S


def backing_booting(backing: EmbeddingBacking | None = None) -> bool:
    """Whether the NEXT encode would pay a BACKING BOOT — a READ, never a resolve.

    ``True`` only when a ``worker`` boot is in flight (or about to be paid, which
    is the same wait for this question): a warm worker is ``False``, and
    ``external`` is ``False`` because a round trip has no boot. The configured
    lever is also read, so a lever that has just moved to ``worker`` while the
    live backing is still the old one answers ``True`` — the next encode pays the
    whole boot.

    ⛔ WHY IT IS PUBLIC. Two callers must ask the same question with the same
    answer: :func:`resolve_ticket_wait_s` (sizing a ticket's bound) and
    ``EmbeddingModel.await_write_ready_reason`` (deciding whether a bounded write
    declines or proceeds, #14186). A second copy of this logic is how one of them
    ends up believing a warm worker is cold — which stored no derived vector for
    the life of the process instead of for a boot (review P2).

    ⛔ THE BACKING IS PEEKED, NEVER RESOLVED. :func:`active_backing` CONSTRUCTS a
    backing on first use and retires the collector it replaces, so a health or
    readiness read must not use it.
    """
    candidate = backing if backing is not None else _peek_backing()
    pending = getattr(candidate, "startup_pending", None)
    if backing is not None:
        # ⛔ AN EXPLICIT SUBJECT IS ANSWERED AS ITSELF. The ticket valve asks about
        # the backing that will FIRE its batch, and that is this object whether or
        # not it is the configured one — the phase, not the lever, is the question.
        return bool(pending is not None and pending())
    if candidate is None:
        # Nothing constructed yet: the configured name is the only evidence about
        # which phase the FIRST encode will pay.
        return resolve_backing() == BACKING_WORKER
    if _injected is not None:
        # ⛔ AN INJECTED BACKING IS WHAT THE NEXT ENCODE USES (review P3, cycle 5).
        # `active_backing` returns `_injected` before it consults the lever, so the
        # configured name says nothing about this process's next encode — asking it
        # instead sized a real boot by the request budget and declined a healthy
        # worker at 1.4 s (tests/test_14186…::test_a_ticket_waits_out_a_worker_boot).
        return bool(pending is not None and pending())
    configured = resolve_backing()
    if pending is not None and pending() and configured == getattr(
            candidate, "name", None):
        # ⛔ AND ONLY IF IT IS THE BACKING THE NEXT ENCODE WILL USE. When the lever
        # has moved OFF `worker`, the next encode retires this candidate and
        # constructs the configured one — an `external` round trip pays no boot —
        # so declining on the retired backing's boot would leave a derived row
        # vectorless for a boot nobody pays. Fail-closed either way, but the honest
        # answer costs nothing here.
        return True
    # ⛔ A LEVER THAT NOW SAYS `worker` WHILE THE LIVE BACKING IS NOT ONE. The
    # candidate answers for the backing it IS, but the next encode will resolve
    # the CONFIGURED one — and for a worker that first encode pays the whole
    # boot. ``docs/infra-runbook.md`` documents retargeting without a restart,
    # so this is a supported path: reading only the candidate sized the bound
    # for a steady-state request and declined a healthy boot (review: the phase
    # sizing asked the OLD backing).
    return (configured == BACKING_WORKER
            and getattr(candidate, "name", None) != BACKING_WORKER)


# ── vector normalisation ─────────────────────────────────────────────────────


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` still exists. An UNREADABLE probe counts as alive.

    Fail-safe direction, and the same one :func:`tortoise.embed_worker._parent_alive`
    takes: a probe that raises must not make us delete a directory a live process
    is still using. Only a provable ``ProcessLookupError`` answers "gone".
    """
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def vector_to_list(vector: Any) -> list[float]:
    """One vector as plain Python floats.

    ONE definition, because it is reached from two directions and the failure
    mode is silent: ``compute_embeddings`` used to spell ``vec.tolist()`` and
    would raise ``AttributeError`` on a plain list — inside a ``try`` whose
    handler returns ``[None] * n`` (a whole batch of successfully computed
    vectors discarded, the ``#4280`` shape). Accepting both shapes is strictly
    safer than requiring the one the encoder currently happens to return.
    """
    tolist = getattr(vector, "tolist", None)
    if tolist is not None:
        return list(tolist())
    return [float(x) for x in vector]


# ── the backing protocol ─────────────────────────────────────────────────────


class EmbeddingBacking(Protocol):
    """What a backing must provide. Three methods, no hidden lifecycle.

    ``encode`` returns one vector per input text IN INPUT ORDER, raises on
    failure (or returns ``None`` where the encoder is unavailable — the
    documented ``EmbeddingModel.get()`` contract), and never returns a SHORT
    list: the callers index the result per input, and a short batch would raise
    ``IndexError`` inside a write loop (the ``encode_batch_for_store`` guard).

    A backing is constructed with no arguments and resolved lazily, so selecting
    one costs nothing at import and a bad configuration is a DECLINE at first
    use rather than an import-time crash.
    """

    name: str

    def encode(self, texts: Sequence[str], *,
               timeout_s: float | None = None) -> list[list[float]] | None: ...

    def close(self) -> None: ...

    def describe(self) -> dict: ...


class InProcessBacking:
    """The default: :class:`tortoise.embeddings.EmbeddingModel`, in this process.

    ⛔ THE ENCODE CALL IS DELIBERATELY UNCHANGED. It is ``model.encode(list)``
    with no kwargs, exactly what ``compute_embeddings`` and the read path call
    today, because this backing's entire job is to be the pre-#14186 behaviour
    behind a new interface. A nice-looking extra kwarg here would raise
    ``TypeError`` inside every in-repo double that declares a narrower
    signature, and that ``TypeError`` is swallowed by the write paths' blanket
    handlers into "no vector stored" — the same fail-open in a new place (see
    ``compute_embeddings``'s own note about its narrow ``(texts, max_tokens)``
    shape).
    """

    name = BACKING_INPROCESS

    def _resolved(self) -> Any:
        # Imported lazily: `embeddings` imports this module, so a module-level
        # import here is the cycle. The local import costs a dict lookup after
        # the first call.
        #
        # ⛔ THE MODEL IS RE-RESOLVED ON EVERY CALL, never cached on this object.
        # `EmbeddingModel.get()` can legitimately return a DIFFERENT instance
        # later (``_reset()``, a transient load failure clearing ``_instance``,
        # a test that swaps the loader), and a cached handle would then keep
        # encoding with a model the process has already discarded — the "which
        # embedder produced this vector" question #1349 opened. `get()` is a
        # few attribute reads on the warm path, so the cache would buy nothing
        # and risk exactly that.
        from .embeddings import EmbeddingModel

        return EmbeddingModel.get()

    def encode(self, texts: Sequence[str], *,
               timeout_s: float | None = None) -> list[list[float]] | None:
        model = self._resolved()
        if model is None:
            return None
        from .embeddings import ensure_embed_threads

        # ⛔ #10296: the pin is re-asserted on the thread that RUNS the forward
        # pass. Under this seam that thread is the coalescer's collector, not
        # the request's — which is precisely why the re-assert has to be here,
        # at the encode, and not at the call site. The pin's NUMBER is not
        # touched.
        ensure_embed_threads()
        vecs = model.encode(list(texts))
        if vecs is None:
            return None
        rows = [vector_to_list(row) for row in vecs]
        if len(rows) != len(texts):
            raise ValueError(
                f"backing {self.name!r} returned {len(rows)} row(s) for "
                f"{len(texts)} text(s)")
        return rows

    def close(self) -> None:
        return None

    def describe(self) -> dict:
        return {"backing": self.name, "encoder": "EmbeddingModel (in-process)"}


def _socket_peer_uid(conn: socket.socket) -> int | None:
    """The uid on the other end of ``conn``, or ``None`` if the kernel cannot say.

    Deliberately a second small implementation of
    :func:`tortoise.embed_worker._peer_uid` rather than an import: the two run in
    DIFFERENT processes on opposite sides of the same check (the worker cannot
    import ``tortoise.embed_seam`` at module level without paying the cold import
    it exists to avoid), so sharing one definition would mean one of them
    importing the other's whole module graph.

    ``SO_PEERCRED`` is the kernel's own answer and cannot be spoofed by the
    connecting process. ``None`` means "this kernel did not tell us", never uid 0.
    """
    so_peercred = getattr(socket, "SO_PEERCRED", None)
    if so_peercred is None:
        return None
    try:
        raw = conn.getsockopt(
            socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return int(uid)


class _LineJSONClient:
    """A newline-delimited JSON request/response client over a stream socket.
    The wire is deliberately trivial — one JSON object per line, no framing
    library, no pickle — because (a) a length-prefixed binary protocol on a
    local socket buys nothing at these sizes and (b) ``json`` is the one
    serialiser both this module and any future non-Python worker already speak.
    A socket is opened per request under a mute x, since the seam's collector
    thread is the only client and a persistent connection would only add a
    half-open failure mode to reason about.

    ⛔ THE CLIENT VERIFIES ITS PEER. The worker's ``_peer_allowed`` runs in the
    process that BOUND the socket, so it defends the legitimate worker against a
    foreign client — it cannot defend against the attack that matters here: a
    process that binds the path FIRST and becomes the server. The check that then
    runs on ``accept`` is the attacker's own code (review: the peer-credential
    belt was on the wrong side of the connection). Verified on this side, after
    ``connect``, ``SO_PEERCRED`` names whoever is actually answering.
    """

    def __init__(self, address: str) -> None:
        self._address = address

    #: How long a ``connect`` that found NO LISTENER may keep retrying.
    #:
    #: ⛔ THE SECOND HALF OF THE READINESS RACE (review F2 at ``76e58a21bb``). The
    #: worker now renames its socket into place only after ``listen(16)``, so the
    #: path it publishes is always connectable — but a path can also exist WITHOUT
    #: a listener behind it: a predecessor that was SIGKILLed leaves its socket
    #: file, and a predecessor that is mid-``unlink``/``bind`` leaves a window. Both
    #: answer ``ECONNREFUSED``, and pre-fix either one was a recorded decline on a
    #: healthy worker. Bounded well inside the request budget (250 ms against the
    #: 30 s default), because a genuinely dead path must still decline promptly —
    #: the cost of this belt is bounded, the cost of the false decline is not.
    _CONNECT_REFUSED_GRACE_S = 0.25

    def open_stream(self, timeout_s: float) -> socket.socket:
        deadline = time.monotonic() + timeout_s
        refused_until = time.monotonic() + min(
            timeout_s, self._CONNECT_REFUSED_GRACE_S)
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(max(deadline - time.monotonic(), 0.001))
                sock.connect(self._address)
            except (ConnectionRefusedError, FileNotFoundError):
                # The path exists with nothing LISTENING behind it
                # (``ECONNREFUSED``: a SIGKILLed predecessor's leftover file, or a
                # bound-but-not-listening socket), or it is momentarily absent
                # while a predecessor unlinks/rebinds (``ENOENT``). Neither is a
                # verdict on the worker yet. Retry within the grace, then let it
                # out — a worker that never comes up must decline, not park.
                sock.close()
                if time.monotonic() >= refused_until:
                    raise
                time.sleep(0.005)
                continue
            except BaseException:
                sock.close()
                raise
            peer = _socket_peer_uid(sock)
            if peer is not None and peer != os.getuid():
                sock.close()
                raise RuntimeError(
                    f"embed seam: the worker socket {self._address} is answered by "
                    f"uid {peer}, not this process's uid {os.getuid()} — refusing to "
                    f"accept vectors from a foreign process (#14186)")
            return sock

    def request(self, payload: dict, *, timeout_s: float) -> dict:
        with self.open_stream(timeout_s) as sock:
            sock.sendall(
                (json.dumps(payload) + "\n").encode("utf-8"))
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError(
                        "worker closed the connection before answering")
                buffer += chunk
                if len(buffer) > 64 * 1024 * 1024:
                    raise ConnectionError(
                        "worker answer exceeded 64 MiB without a newline")
        line, _, _rest = buffer.partition(b"\n")
        return json.loads(line.decode("utf-8"))


class WorkerBacking:
    """A local worker PROCESS (:mod:`tortoise.embed_worker`) over a UNIX socket.

    WHY A PROCESS AND NOT A THREAD. The forward pass releases the GIL only
    inside torch, and the cold import alone is 156.5 s (#13674); a worker
    process keeps both off the serving process's address space, which is the
    prerequisite for "servers are disposable caches" (SCALING-ROADMAP §2.5,
    Track S1).

    ⛔ IT NEVER FALLS BACK TO IN-PROCESS. A worker that will not start, or that
    dies mid-request, is a DECLINE recorded by :func:`last_decline` and surfaced
    by :func:`seam_status` — the caller degrades exactly as it does for a broken
    in-process embedder (no vector, the leg declared). Quietly encoding locally
    would hide the migration's failure and put the 2-4 s back inside the
    request, which is the entire thing #14186 exists to remove.

    The child is spawned WITH the parent's interpreter by default and dies with
    the parent: it reads stdin and exits when that pipe closes, so an OOM-killed
    parent or a SIGKILLed lane cannot leave an orphaned 432 MB worker behind.
    """

    name = BACKING_WORKER

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._address: str | None = None
        #: The private directory this backing minted for its socket, if any.
        #: Created lazily by :meth:`socket_path` and removed by :meth:`close`.
        self._socket_dir: str | None = None
        self._path_lock = threading.Lock()
        self._lock = threading.Lock()
        #: Set by :meth:`close`. A closed backing never spawns again: the
        #: collector that would ask it has been retired, and a late
        #: ``describe()``/``encode`` must not resurrect a 432 MB child that
        #: nothing will close.
        self._closed = False
        #: How many ms of the LAST encode were spent BOOTING (spawn + wait for
        #: the socket). Written and read on the collector thread, and published
        #: so ``encode_ms`` can exclude it — a cold boot is 156.5 s of import
        #: plus the model load (#13674), and charging that to ``encode_ms`` made
        #: the number the backing-switch decision rests on incomparable across
        #: backings (review F4).
        self._last_startup_ms = 0.0

    def socket_path(self) -> str:
        """Where this backing listens — inside a PRIVATE directory (review F2).

        ⛔ NOT in ``$TMPDIR`` DIRECTLY. The old default was
        ``$TMPDIR/tortoise-embed-worker-<pid>.sock``, a world-writable directory,
        and :meth:`_ensure_started` treats the path's first EXISTENCE as READY
        while the worker creates it only after its preload. So for the whole boot
        the expected path was absent and the client was polling for anyone to
        create it — any local user who bound it first became the embedder, and
        the vectors it answered were written as record-truth (#5004's class of
        damage, undetectable downstream).

        A ``0700`` directory owned by this uid closes that: another user cannot
        traverse it, so it cannot create the socket inside it either. The
        worker's peer-credential check (:func:`tortoise.embed_worker._peer_allowed`)
        is the belt for the one case the directory cannot cover — an operator who
        points ``TORTOISE_EMBED_WORKER_SOCKET`` at a shared path.
        """
        path = os.environ.get(ENV_WORKER_SOCKET)
        if path and path.strip():
            path = path.strip()
            self._warn_if_shared_parent(path)
            return path
        return os.path.join(self._private_dir(), "embed.sock")

    @staticmethod
    def _warn_if_shared_parent(path: str) -> None:
        """Name the one thing the private directory cannot cover.

        The ``0700`` directory is the PRIMARY control, and an operator who points
        the lever elsewhere gives it up. Nothing is refused here — the path is a
        documented lever and the client's own peer check
        (:class:`_LineJSONClient`) is the belt that still holds — but the reason
        the peer check exists is worth saying out loud once, at the path where an
        operator would look.
        """
        parent = os.path.dirname(os.path.abspath(path)) or "/"
        try:
            info = os.stat(parent)
        except OSError:  # pragma: no cover — parent not created yet
            return
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            _logger.warning(
                "%s=%r is inside %s, which this uid does not own privately "
                "(uid %s, mode %s) — the socket's directory is NOT protecting "
                "it; reliance is on the client's peer-credential check (#14186)",
                ENV_WORKER_SOCKET, path, parent, info.st_uid,
                oct(info.st_mode & 0o777))

    def described_socket_path(self) -> str | None:
        """The socket path WITHOUT minting anything — for diagnostics.

        ⛔ ``describe()`` calls this, not :meth:`socket_path`. Readiness is a path
        existence check, so a ``/health`` read that MINTED a private directory just
        to print it left an empty ``tortoise-embed-worker-*`` dir behind for the
        life of any process whose boot never completed (review: private-dir
        lifecycle). A diagnostic must not have a side effect.
        """
        path = os.environ.get(ENV_WORKER_SOCKET)
        if path and path.strip():
            return path.strip()
        with self._path_lock:
            directory = self._socket_dir
        return None if directory is None else os.path.join(directory, "embed.sock")

    def last_startup_ms(self) -> float:
        """Milliseconds of the last encode that were spent reaching READY.

        Zero on the warm path. Read by :meth:`_Collector._fire` so ``encode_ms``
        measures the ENCODE, which is what the runbook tells an operator is
        directly comparable across backings.
        """
        return self._last_startup_ms

    def _private_dir(self) -> str:
        """The ``0700`` directory this backing owns, minted once.

        Memoised so the path is STABLE across the many ``socket_path()`` calls a
        start-up makes (the poll loop, ``describe()``, the spawn argument);
        ``mkdtemp`` creates the directory ``0700`` (no ``mode`` argument exists on
        it — the mode is not negotiable) and guarantees a name no other process can
        predict, which is what makes the directory private in both senses.
        """
        with self._path_lock:
            if self._socket_dir is None:
                self._prune_dead_socket_dirs()
                self._socket_dir = tempfile.mkdtemp(
                    prefix=f"tortoise-embed-worker-{os.getpid()}-")
            return self._socket_dir

    @staticmethod
    def _prune_dead_socket_dirs() -> None:
        """Remove private socket dirs left by workers that will never return.

        A SIGKILLed process cannot unlink its own directory, and nothing else did,
        so ``$TMPDIR`` accumulated one ``tortoise-embed-worker-<pid>-*`` per
        forced death (review: private-dir lifecycle). Only directories named for a
        pid that is PROVABLY gone are removed, and every step is best-effort — a
        directory we cannot read is left alone rather than guessed about.
        """
        root = tempfile.gettempdir()
        prefix = "tortoise-embed-worker-"
        try:
            names = os.listdir(root)
        except OSError:  # pragma: no cover — unreadable temp dir
            return
        for name in names:
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            pid_text, separator, _unique = rest.partition("-")
            if not separator or not pid_text.isdigit():
                continue
            pid = int(pid_text)
            if pid == os.getpid() or _pid_alive(pid):
                continue
            # ⛔ ``rmtree``, NOT ``rmdir`` (review P2). A worker that was SIGKILLed
            # leaves its socket INSIDE this directory — and since the readiness
            # fix, possibly a staging sibling too — so ``rmdir`` refused every
            # directory this function exists to reap and the ``$TMPDIR``
            # accumulation it claims to bound kept happening. The ``rmdir`` row
            # that pinned it built an EMPTY directory, a shape the code never
            # produces: a guard that could not be reddened.
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)

    def startup_pending(self) -> bool:
        """Whether the next encode will pay a boot (review F1, cycle 4).

        Read WITHOUT the state lock on purpose: the boot holds that lock for its
        whole duration, and a ticket's bound must not block on it.

        ⛔ ``_proc is None`` IS NOT THE QUESTION (review P1). ``_ensure_started``
        RESPAWNS a ``_proc`` that is dead (``poll()`` returns) or alive with its
        socket gone — so both of those are boots the NEXT encode pays, and
        answering ``False`` for them sized the bound for steady state: the bounded
        write answered READY, walked into the seam, and charged the worker's 600 s
        start-up to the request (the #4055/#13224 defect this gate exists to
        remove), while the read valve declined a worker that was merely
        restarting. Readiness is the conjunction it actually is: a live child AND
        a bound socket.
        """
        if self._closed:
            return False
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return True
        return not os.path.exists(self._address or "")

    def _ensure_started(self, timeout_s: float) -> str:
        """Spawn the worker if needed and wait until it is READY.

        READY means the socket EXISTS, and ``embed_worker.serve`` creates it only
        after ``_preload_encoder`` has paid the cold import and the model load.
        That ordering is the contract this wait depends on: an existence check
        that preceded readiness is how the first query after every boot used to
        block for the REQUEST budget and then decline on a healthy worker
        (``docs/infra-runbook.md`` §4.1.1e told an operator to "repair" it).

        ``timeout_s`` is therefore the START-UP budget
        (:func:`resolve_worker_startup_s`), never the per-request one — the two
        are passed by different callers and mean different things.
        See :meth:`described_socket_path` for the side-effect-free variant.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    "embed seam: this worker backing has been CLOSED — a closed "
                    "backing is never respawned (#14186)")
            if self._proc is not None and self._proc.poll() is None:
                assert self._address is not None
                # ⛔ RE-VALIDATE THE CACHED ADDRESS. ``poll() is None`` says the
                # CHILD is alive, never that the path it bound still exists — a
                # retired instance of this backing (or anything that removes the
                # path) can leave a live child whose socket is gone. Returning the
                # cached address then fails every later request with
                # ``FileNotFoundError`` while ``describe()`` still publishes
                # ``worker_alive: true``: a silent, permanent loss of the dense
                # leg on a surface that reports OK.
                if os.path.exists(self._address):
                    self._last_startup_ms = 0.0
                    return self._address
                _logger.warning(
                    "embed seam: worker pid %s is alive but its socket %s is "
                    "gone — respawning (#14186)", self._proc.pid, self._address)
                with _swallow_base_exception():
                    self._proc.kill()
                with _swallow_base_exception():
                    self._proc.wait(timeout=5)
                self._proc = self._address = None
            boot_started = time.monotonic()
            address = self.socket_path()
            with _swallow_oserror():
                os.unlink(address)
            python = (os.environ.get(ENV_WORKER_PYTHON) or sys.executable)
            # ⛔ `-m tortoise.embed_worker` with the parent's interpreter, so the
            # worker imports the SAME tortoise and the same extras. stdin is a
            # pipe the child watches: our death closes it, the child exits.
            proc = subprocess.Popen(
                [python, "-m", "tortoise.embed_worker", "--socket", address],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            deadline = time.monotonic() + max(timeout_s, 1.0)
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    # ⛔ The child's stderr goes to DEVNULL (it must never write
                    # into the serving process's own stream), so "see its
                    # stderr" would be an unactionable instruction for the
                    # failure mode that matters most: a worker that will not
                    # start. Name the command instead, so an operator can run
                    # the exact thing that failed.
                    # ⛔ REAP before raising: an exited child that is never waited
                    # for stays a zombie until this process exits, and a start-up
                    # that fails repeatedly would accumulate one per attempt.
                    with _swallow_base_exception():
                        proc.wait(timeout=5)
                    raise RuntimeError(
                        f"embed worker exited during start-up (code "
                        f"{proc.returncode}) — reproduce with: "
                        f"{python} -m tortoise.embed_worker --socket "
                        f"{address}")
                if os.path.exists(address):
                    self._proc, self._address = proc, address
                    self._last_startup_ms = (
                        time.monotonic() - boot_started) * 1000.0
                    _logger.info(
                        "embed seam: worker started (pid %s, socket %s) — "
                        "query embeds now run outside this process (#14186)",
                        proc.pid, address)
                    return address
                time.sleep(0.02)
            # ⛔ Kill AND reap: ``kill()` alone leaves the SIGKILLed child a
            # zombie, so a bounded start-up that repeats leaks one process per
            # attempt.
            proc.kill()
            with _swallow_base_exception():
                proc.wait(timeout=5)
            raise TimeoutError(
                f"embed worker did not create {address} within {timeout_s}s")

    def encode(self, texts: Sequence[str], *,
               timeout_s: float | None = None) -> list[list[float]]:
        budget = resolve_timeout_s() if timeout_s is None else timeout_s
        # ⛔ The START-UP budget, not the request budget: this call is what pays
        # the worker's boot (cold import + model load, #13674), and bounding it by
        # the 30 s request timeout declined the first query after every boot while
        # the worker was still legitimately loading.
        address = self._ensure_started(resolve_worker_startup_s())
        answer = _LineJSONClient(address).request(
            {"texts": list(texts)}, timeout_s=budget)
        error = answer.get("error")
        if error:
            raise RuntimeError(f"embed worker: {error}")
        rows = [[float(x) for x in row] for row in answer.get("vectors") or ()]
        if len(rows) != len(texts):
            raise ValueError(
                f"embed worker returned {len(rows)} row(s) for "
                f"{len(texts)} text(s)")
        return rows

    def close(self) -> None:
        # ⛔ TAKE THE PRIVATE DIRECTORY **FIRST**, under its own lock. Doing it at
        # the end (as this used to) left two windows: a ``describe()`` in between
        # re-minted a NEW directory, and the ``rmdir`` here could then remove that
        # one — the socket path the just-closed worker had bound. Clearing the
        # attribute up front makes ``close()`` idempotent and makes a re-mint
        # impossible; ``_closed`` stops a spawn after it.
        with self._path_lock:
            directory, self._socket_dir = self._socket_dir, None
        with self._lock:
            self._closed = True
            proc, address = self._proc, self._address
            self._proc = self._address = None
            if proc is not None:
                # ⛔ CLOSE STDIN FIRST: the child's stdin watcher is the mechanism
                # that makes it die with this process, so a graceful close should
                # exercise the same path an OOM-kill does, instead of leaving the
                # pipe open until the child is terminated out from under it.
                if proc.stdin is not None:
                    with _swallow_base_exception():
                        proc.stdin.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        # ⛔ KILL THEN REAP — a SIGKILL that is not waited for
                        # leaves a zombie for the life of the process.
                        proc.kill()
                        with _swallow_base_exception():
                            proc.wait(timeout=5)
            if address:
                with _swallow_oserror():
                    os.unlink(address)
        # The private directory is ours: remove it with the socket, so a backing
        # that is retired leaves nothing behind in the temp dir (review F2).
        # ⛔ ``rmtree`` for the same reason as the prune above: ``rmdir`` refuses
        # the directory a killed worker actually leaves (its socket inside it).
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    def describe(self) -> dict:
        alive = self._proc is not None and self._proc.poll() is None
        return {
            "backing": self.name,
            "worker_pid": None if self._proc is None else self._proc.pid,
            "worker_alive": alive,
            # ⛔ SIDE-EFFECT FREE: a diagnostics read must not MINT the private
            # directory the readiness check is about.
            "socket": self.described_socket_path(),
            "closed": self._closed,
        }


class ExternalBacking:
    """A remote HTTP endpoint, spoken to with a VENDOR-NEUTRAL contract.

    ⛔ NO VENDOR IS BAKED IN (the issue's contradiction test (d)). The seam
    posts a documented shape and accepts any of the three answer shapes every
    embedding provider already publishes, so pointing it at a different provider
    is a URL change. That is what makes the choice reversible, and reversibility
    is the reason this backing is allowed to exist at all before a spend gate
    approves one.

    REQUEST::

        POST <TORTOISE_EMBED_EXTERNAL_URL>
        Authorization: Bearer <TORTOISE_EMBED_EXTERNAL_TOKEN>   (omitted when unset)
        {"model": "<TORTOISE_EMBED_EXTERNAL_MODEL, or the pinned local id>",
         "texts": ["…", "…"]}

    ANSWER — any one of, in this order of preference::

        {"vectors":   [[float, …], …]}
        {"embeddings":[[float, …], …]}
        {"data":      [{"embedding": [float, …]}, …]}     # the OpenAI shape

    The three are accepted rather than one because refusing two of them would
    force an adapter service in front of a provider that already answers
    correctly — a vendor choice made by omission.

    ⛔ The WIDTH is not checked here. ``encode_for_store``'s ``_degrade_to_width``
    is the ONE place a store's width constraint is applied (#4280), and a
    second check here would either duplicate it or disagree with it.
    """

    name = BACKING_EXTERNAL

    def url(self) -> str:
        return (os.environ.get(ENV_EXTERNAL_URL) or "").strip()

    def encode(self, texts: Sequence[str], *,
               timeout_s: float | None = None) -> list[list[float]]:
        url = self.url()
        if not url:
            raise RuntimeError(
                f"{ENV_EXTERNAL_URL} is unset — the external embedding backing "
                f"has nowhere to send work (#14186)")
        budget = resolve_timeout_s() if timeout_s is None else timeout_s
        model = (os.environ.get(ENV_EXTERNAL_MODEL) or "").strip()
        if not model:
            from .embeddings import EMBEDDING_MODEL

            model = EMBEDDING_MODEL
        body = json.dumps({"model": model, "texts": list(texts)}).encode("utf-8")
        headers = {"content-type": "application/json"}
        token = (os.environ.get(ENV_EXTERNAL_TOKEN) or "").strip()
        if token:
            headers["authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=budget) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(
                f"external embedding endpoint answered HTTP {exc.code}: "
                f"{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"external embedding endpoint unreachable: {exc.reason}") from exc
        rows = _external_rows(payload)
        if len(rows) != len(texts):
            raise ValueError(
                f"external embedding endpoint returned {len(rows)} row(s) for "
                f"{len(texts)} text(s)")
        return rows

    def close(self) -> None:
        return None

    def describe(self) -> dict:
        return {
            "backing": self.name,
            "url": self.url() or None,
            "model": (os.environ.get(ENV_EXTERNAL_MODEL) or None),
        }


def _external_rows(payload: Any) -> list[list[float]]:
    """Pick the vector table out of an external answer (:class:`ExternalBacking`).

    Three accepted shapes, one normalisation. An answer that carries none of
    them raises with the keys it DID carry — a validator that says "bad shape"
    and nothing else is how a provider migration turns into a silent
    ``encode_failed`` degrade on every read.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"external embedding answer is a {type(payload).__name__}, not an "
            f"object")
    for key in ("vectors", "embeddings"):
        table = payload.get(key)
        if isinstance(table, list):
            return [[float(x) for x in row] for row in table]
    data = payload.get("data")
    if isinstance(data, list):
        try:
            return [[float(x) for x in item["embedding"]] for item in data]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"external embedding answer's `data` is not the OpenAI shape "
                f"(every item needs an `embedding` list): {exc}") from exc
    raise ValueError(
        "external embedding answer carries none of vectors/embeddings/data "
        f"(keys: {sorted(payload)})")


class _swallow_oserror:
    """Ignore ``OSError`` around a best-effort unlink (a missing path is fine)."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


# ── the coalescer ────────────────────────────────────────────────────────────


class _Ticket:
    """One queued query embed: its text, its result, and the event that fires it."""

    __slots__ = ("done", "error", "future", "submitted_at", "text", "vectors")

    def __init__(self, text: str) -> None:
        self.text = text
        self.submitted_at = time.monotonic()
        self.done = threading.Event()
        self.vectors: list[list[float]] | None = None
        self.error: BaseException | None = None
        #: How long this ticket waited before its batch fired (ms). Measured,
        #: not inferred: it is the number that shows whether the bound is being
        #: respected, and the p95 of it is what the operator watches.
        self.future = 0.0


class _Latency:
    """A bounded reservoir with ``p50``/``p95`` — one per measured quantity.

    ⛔ A MEAN IS NOT A READING HERE. Encode latency is bimodal (a warm encode is
    25-43 ms, a cold one seconds), so the mean lands in the valley between two
    populations and describes neither. The two percentiles are what the issue
    asks for and what an operator can act on.
    """

    __slots__ = ("_lock", "_samples", "_total")

    def __init__(self, window: int = LATENCY_WINDOW) -> None:
        self._lock = threading.Lock()
        self._samples: collections.deque[float] = collections.deque(maxlen=window)
        #: Every observation ever, so "how many encodes has this process done"
        #: is answerable even after the reservoir has rolled over.
        self._total = 0

    def note(self, value: float) -> None:
        with self._lock:
            self._samples.append(value)
            self._total += 1

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._total = 0

    def snapshot(self) -> dict:
        with self._lock:
            samples = sorted(self._samples)
            total = self._total
        view: dict[str, Any] = {"n": total, "window": len(samples)}
        if samples:
            view["p50"] = round(_percentile(samples, 0.50), 2)
            view["p95"] = round(_percentile(samples, 0.95), 2)
            view["max"] = round(samples[-1], 2)
        else:
            # None, never 0: "no observation" and "cost nothing" are different
            # claims, and a dashboard that cannot tell them apart reads an
            # unwarmed process as an infinitely fast one.
            view["p50"] = view["p95"] = view["max"] = None
        return view


def _percentile(sorted_samples: list[float], quantile: float) -> float:
    """Nearest-rank percentile over an already-sorted, non-empty list.

    Nearest-rank rather than linear interpolation on purpose: every published
    value is then a latency the process ACTUALLY OBSERVED, which is what makes
    "p95 = 1.9 s" auditable against a log line. An interpolated percentile can
    name a number nothing ever measured.

    The formula is the textbook one — the ``ceil(q * n)``-th smallest, 1-indexed
    — rather than ``round(q * (n - 1))``, which is the interpolating variant's
    index and gives p50 = the upper of the two middles on an even sample. One
    definition, so a reading quoted from ``/health`` matches one computed from
    the same numbers by hand.
    """
    index = math.ceil(quantile * len(sorted_samples)) - 1
    return sorted_samples[min(max(index, 0), len(sorted_samples) - 1)]


#: How long ``_Collector.stop`` waits for an in-flight encode before returning.
#: A run-time bound on the JOIN, never a licence to close the backing early —
#: see :func:`_retire`.
_STOP_JOIN_S = 5.0

#: How long :func:`_close_when_idle` waits for a retired collector's in-flight
#: encode before it closes the backing anyway and says so. Deliberately far above
#: any legitimate encode so the warn-and-force branch is pathological only.
_RETIRE_JOIN_S = _STOP_JOIN_S + DEFAULT_TIMEOUT_S + 30.0

#: The decline a ticket gets when the collector is STOPPED before its batch was
#: encoded. Named so a caller can tell teardown from an encoder failure.
_STOPPED_BEFORE_ENCODE = (
    "embed seam: the collector was stopped before this query's batch was encoded")

#: The decline a ticket gets when the collector's own bookkeeping (not the
#: backing call, which carries its own exception) failed for its batch.
_BATCH_FAULTED = (
    "embed seam: batch failed before this query's answer was produced")

#: The decline a ticket gets when it is submitted to a collector that has been
#: STOPPED. A stopped collector is TERMINAL: see :meth:`_Collector.stop`.
_COLLECTOR_CLOSED = (
    "embed seam: this collector has been stopped and cannot be reused")


class _Collector:
    """The bounded collection window: one queue, one thread, one hard deadline.

    THE BOUND, PRECISELY. The window opens when the FIRST ticket of a batch is
    dequeued and closes ``max_wait_ms`` later — OR earlier, when the batch
    reaches ``max_batch``. A ticket that arrives at 19.9 ms of a 20 ms window is
    served by the batch that fires at 20 ms; a ticket that arrives at 20.1 ms
    joins the NEXT batch, which opens a FRESH window of its own. The deadline is
    therefore never extended by an arrival, which is the property the batching
    literature demands and the one an "wait until the queue is empty or N ms
    since the LAST arrival" design loses.

    WHY A THREAD AND NOT ``asyncio``. The seam is called from synchronous code
    on the read lane (``CONTROL_PLANE_READ_WORKERS`` daemon workers) and from
    the write folds, which run on their own threads. A dedicated blocking
    collector serves all of them with one implementation, and the encode must
    run on a thread whose intra-op pin we can re-assert (#10296) — an
    ``asyncio`` task would run the forward pass on whichever loop thread
    happened to be scheduled, so the pin would become a guess.

    ⛔ ONE FORWARD PASS AT A TIME. The collector is a single thread and the
    backing is called from it, so encodes are serialised. That is deliberate: N
    concurrent encodes on shared cores are what produced the 2-4 s measurement,
    and serialising them behind a batching queue is the scheduling change the
    issue asks for. It also gives the pin one place to be asserted.
    """

    def __init__(self, backing_factory) -> None:
        self._backing_factory = backing_factory
        self._lock = threading.Lock()
        self._queue: collections.deque[_Ticket] = collections.deque()
        self._wakeup = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._stop = False
        #: Set by :meth:`stop` and NEVER cleared. A stopped collector must not be
        #: reusable: :meth:`_ensure_thread_unlocked` used to reset ``_stop`` and
        #: start a fresh thread on the SAME object, so a late submit on a captured
        #: collector (after ``reset_seam`` cleared the module globals) ran a batch
        #: through a freshly constructed backing that nothing would ever close
        #: (review: ``stop()``'s "nothing fires afterwards" was false).
        self._closed = False
        #: Per-backing counters. Reset by :func:`reset_seam`.
        self.submissions = 0
        self.batches = 0
        self.coalesced = 0
        self.declines = 0
        self.wait_ms = _Latency()
        self.collect_ms = _Latency()
        self.encode_ms = _Latency()
        self.batch_size = _Latency()
        #: When the batch currently being collected was DEQUEUED — the instant
        #: its window opened. Set by :meth:`_collect`, read by :meth:`_fire`.
        #: Collector-thread-only, so it needs no lock.
        self._window_opened_at: float | None = None

    # ── submission (any thread) ─────────────────────────────────────────

    def submit(self, text: str, *, timeout_s: float) -> list[list[float]] | None:
        """Queue one query embed and block until its batch has answered."""
        ticket = _Ticket(text)
        started = False
        with self._wakeup:
            if not self._closed:
                self._queue.append(ticket)
                self.submissions += 1
                started = self._ensure_thread_unlocked()
                self._wakeup.notify()
        if not started:
            # ⛔ NOT a re-arm, and this covers BOTH teardown windows the two
            # reviews found. (a) A teardown is IN PROGRESS: the collector has been
            # told to stop and is finishing an encode, so a ticket queued now would
            # wait out its whole bound (a boot's worth, on ``worker``) for a thread
            # that will exit without looking at it. (b) The thread has ALREADY
            # exited: the pre-fix path cleared ``_stop`` and started a fresh thread
            # on this collector, which ran a batch through a backing constructed
            # after teardown — one nothing would ever close (the 432 MB leak
            # ``reset_seam`` promises not to leave). ``stop()`` is TERMINAL; both
            # windows decline by name instead.
            raise RuntimeError(_COLLECTOR_CLOSED)
        # ⛔ The wait is bounded by the collection window PLUS the budget of the
        # PHASE it is guarding — the start-up budget while a worker boot is in
        # flight, the request budget otherwise (see :func:`resolve_ticket_wait_s`).
        # A ticket whose batch has not fired by then is a DECLINE, not an
        # indefinite park: a wedged collector must not be able to hang a read
        # lane worker, which is the #13528 shape this seam sits under.
        fired_by = resolve_ticket_wait_s(timeout_s)
        if not ticket.done.wait(fired_by):
            # Drop it so the collector does not encode a text nobody is waiting
            # for (the caller has already declined).
            with self._lock, _swallow_value_error():
                self._queue.remove(ticket)
            # ⛔ RECORD it. This raise happens OUTSIDE ``_fire``, so without this
            # the decline left no trace at all: ``last_decline`` stayed null and
            # the runbook told an operator to read that absence as "it was only
            # the boot" — the one reading that is inverted whenever the valve
            # fires. A ticket that waited out its whole bound is a WEDGE.
            timed_out = TimeoutError(
                f"embed seam: no batch fired for this query within "
                f"{fired_by:.2f}s — the collector is wedged or the backing never "
                f"answered")
            _note_decline(timed_out)
            raise timed_out
        if ticket.error is not None:
            raise ticket.error
        return ticket.vectors

    # ── the collector thread ────────────────────────────────────────────

    def _ensure_thread_unlocked(self) -> bool:
        """Start the collector thread if it is not alive. False ⇒ closed."""
        if self._closed:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop = False
        self._thread = threading.Thread(
            target=self._run, name="embed-seam-collector", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        while True:
            batch = self._collect()
            if batch is None:
                return
            if self._stop:
                # ⛔ ``stop()`` landed between the DEQUEUE and the fire. Encoding
                # this batch would (a) make a stopped collector do work the
                # deployment has already torn down, and (b) re-resolve a backing
                # through :func:`active_backing` — constructing a fresh worker
                # (432 MB) that nothing will close, which is the opposite of what
                # ``reset_seam`` promises. Decline instead (review F5).
                for ticket in batch:
                    self._decline(ticket, _STOPPED_BEFORE_ENCODE)
                return
            # ⛔ THE COLLECTOR MUST NOT DIE. If it did, every later submission
            # would sit until its own bound expired and then raise, which
            # presents as "the embedder got slow" rather than "the scheduler
            # stopped". `_fire` already hands any backing failure to every
            # waiter; this is the belt for a fault in the bookkeeping between
            # that and the ticket loop, so one bad batch cannot take the seam
            # down for the rest of the process.
            try:
                self._fire(batch)
            except Exception:
                _logger.warning(
                    "embed seam: a batch faulted outside the backing call — the "
                    "collector continues (#14186)", exc_info=True)
                for ticket in batch:
                    self._decline(ticket, _BATCH_FAULTED)

    @staticmethod
    def _decline(ticket: _Ticket, message: str) -> None:
        """Hand ``ticket`` a named error instead of an answer. Idempotent."""
        if ticket.done.is_set():
            return
        ticket.error = RuntimeError(message)
        ticket.done.set()

    def _collect(self) -> list[_Ticket] | None:
        """Take the next batch, honouring the hard window. ``None`` ⇒ stop."""
        with self._wakeup:
            while not self._queue and not self._stop:
                self._wakeup.wait()
            if self._stop:
                # ⛔ ``stop()`` is a STOP, not a drained queue: nothing is
                # dequeued (and therefore nothing can fire) once it is set. The
                # tickets still in the queue were already declined by ``stop()``.
                return None
            batch = [self._queue.popleft()]
            # ⛔ THE WINDOW OPENS HERE, not at the first ticket's submission: a
            # ticket that arrived while the previous batch was being encoded sat
            # in the QUEUE, and that wait is not the window's doing. Anchoring
            # here is what makes ``collect_ms`` a quantity ``max_wait_ms``
            # actually bounds — anchoring at submission made the two incomparable
            # and had an operator reading a broken bound on a healthy process
            # (review F5).
            self._window_opened_at = time.monotonic()
        window_s = resolve_max_wait_ms() / 1000.0
        limit = resolve_max_batch()
        if window_s > 0.0:
            # ⛔ ANCHOR AT THE DEQUEUE (recorded just above), NOT at the ticket's
            # submission. `batch[0].submitted_at` is up to one whole encode in
            # the past whenever this collector was busy, so anchoring there made
            # `remaining` already <= 0 on the first iteration and fired a
            # ONE-ticket batch — every batch, under exactly the sustained load
            # batching exists for. `max()` makes the intent explicit: the window
            # can never open earlier than the moment we took the batch.
            opened_at = self._window_opened_at
            if opened_at is None:  # pragma: no cover — defensive, always set above
                opened_at = time.monotonic()
            deadline = max(batch[0].submitted_at, opened_at) + window_s
            while len(batch) < limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                with self._lock:
                    if not self._queue:
                        # Nothing to take yet, but the window is still open:
                        # sleep on the condition, never on a poll, so an
                        # arrival wakes us immediately and a spurious wakeup
                        # costs one loop iteration.
                        self._wakeup.wait(min(remaining, 0.005))
                        continue
                    while self._queue and len(batch) < limit:
                        batch.append(self._queue.popleft())
        else:
            # Window of 0: take whatever ALREADY arrived while the previous
            # encode ran, then fire. Opportunistic coalescing with no added
            # latency — see DEFAULT_MAX_WAIT_MS_FOR_BACKING.
            with self._lock:
                while self._queue and len(batch) < limit:
                    batch.append(self._queue.popleft())
        return batch

    def _fire(self, batch: list[_Ticket]) -> None:
        """Encode ``batch`` in ONE call and hand each ticket its row."""
        fired_at = time.monotonic()
        # ⛔ TWO DIFFERENT QUANTITIES, published separately because conflating
        # them is a claim that cannot be true on the DEFAULT backing:
        #  * ``collect_ms`` — how long the window stayed OPEN (dequeue -> fire).
        #    THIS is what ``max_wait_ms`` bounds, so this is the bound's proof.
        #  * ``wait_ms`` — submission -> fire, i.e. END-TO-END latency. It also
        #    carries however long the ticket queued behind an in-flight encode,
        #    so on ``inprocess`` (a 0 ms window) every non-zero observation
        #    "exceeds" the published bound while the bound is fine.
        if self._window_opened_at is not None:
            self.collect_ms.note((fired_at - self._window_opened_at) * 1000.0)
            self._window_opened_at = None
        for ticket in batch:
            ticket.future = (fired_at - ticket.submitted_at) * 1000.0
            self.wait_ms.note(ticket.future)
        self.batches += 1
        self.batch_size.note(float(len(batch)))
        self.coalesced += len(batch) - 1
        started = time.monotonic()
        backing: EmbeddingBacking | None = None
        try:
            backing = self._backing_factory()
            rows = backing.encode([t.text for t in batch],
                                  timeout_s=resolve_timeout_s())
            if rows is not None and len(rows) != len(batch):
                raise ValueError(
                    f"backing returned {len(rows)} row(s) for {len(batch)} "
                    f"text(s)")
            error: BaseException | None = None
        except BaseException as exc:
            rows = None
            error = exc
            self.declines += 1
            _note_decline(exc)
        # ⛔ ``encode_ms`` MEASURES THE ENCODE, NOT THE BOOT (review F4). The
        # clock above starts before the backing is resolved, and for a worker that
        # call begins with ``_ensure_started(600 s)`` — so a cold first sample was
        # the 156.5 s import plus the model load, charged to a number
        # ``docs/infra-runbook.md`` tells operators is "directly comparable across
        # backings" and which the switching decision rests on. The backing says
        # how much of this call was spent reaching READY; subtract it. Nearest-rank
        # makes the tail worse (``_percentile`` is ``ceil(q*n)``, so for ``n < 20``
        # the published p95 IS the largest sample), and the first reading after a
        # switch is exactly the one taken on ``n = 1``.
        bootstrap_ms = 0.0
        startup_probe = getattr(backing, "last_startup_ms", None)
        if callable(startup_probe):
            with _swallow_base_exception():
                bootstrap_ms = float(startup_probe())
        self.encode_ms.note(
            max(0.0, (time.monotonic() - started) * 1000.0 - bootstrap_ms))
        for index, ticket in enumerate(batch):
            if error is None and rows is not None:
                ticket.vectors = [rows[index]]
            else:
                ticket.error = error
            ticket.done.set()

    def stop(self, timeout_s: float | None = None) -> None:
        """Stop the collector: decline what is queued, fire nothing new.

        ⛔ WHAT THIS USED TO DO, AND WHY IT WAS NOT A STOP (review F5). It set the
        flag and joined for a bounded time, then returned. But ``_collect`` only
        exited on the flag when the queue was EMPTY, and a ticket had already
        been popped into ``batch`` — so after ``stop()`` returned, ``_run`` still
        called ``_fire(batch)``. That fired an encode after teardown, recorded a
        decline teardown itself caused, and — after ``reset_seam`` cleared the
        module globals — constructed a FRESH backing through
        :func:`active_backing` that nothing would ever close (a 432 MB worker for
        a ``worker`` backing). Two collectors could then encode concurrently,
        breaking the one-forward-pass-at-a-time ceiling this module states.

        Now: queued tickets are declined with a named reason (their callers do
        not wait out their whole bound), a batch dequeued but not yet fired is
        declined by :meth:`_run`, and nothing fires afterwards. ``timeout_s``
        bounds only the JOIN — a caller that still sees a live collector knows an
        encode was in flight and must not close its backing yet (see
        :func:`_retire`).
        """
        bound_s = _STOP_JOIN_S if timeout_s is None else timeout_s
        with self._wakeup:
            self._stop = True
            # ⛔ TERMINAL. See :attr:`_closed`: a stopped collector that could
            # restart was the hole in "nothing fires afterwards".
            self._closed = True
            queued = list(self._queue)
            self._queue.clear()
            self._wakeup.notify_all()
        for ticket in queued:
            self._decline(ticket, _STOPPED_BEFORE_ENCODE)
        thread = self._thread
        if thread is not None and thread.is_alive() and \
                thread is not threading.current_thread():
            thread.join(timeout=bound_s)

    def alive(self) -> bool:
        """Whether the collector thread is still running (an encode is in flight)."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def join(self, timeout_s: float | None = None) -> None:
        """Wait for the collector thread to finish — unbounded unless told otherwise."""
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_s)

    def close(self) -> None:
        self.stop()

    def snapshot(self) -> dict:
        return {
            "submissions": self.submissions,
            "batches": self.batches,
            "coalesced": self.coalesced,
            "declines": self.declines,
            # ⛔ The MEDIAN batch, not a mean: batch size is the count of
            # whoever happened to be queued, so a mean is dominated by the
            # rare large batch and answers no question an operator asks.
            "median_batch": (
                self.batch_size.snapshot().get("p50") if self.batches else None),
            "wait_ms": self.wait_ms.snapshot(),
            #: The window-attributable wait (dequeue -> fire). ``max_wait_ms``
            #: bounds THIS; ``wait_ms`` above is end-to-end and is not bounded by
            #: it (see :meth:`_fire`).
            "collect_ms": self.collect_ms.snapshot(),
            "encode_ms": self.encode_ms.snapshot(),
            "batch_size": self.batch_size.snapshot(),
        }


class _swallow_value_error:
    """Ignore ``ValueError`` around a best-effort ``deque.remove``."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, ValueError)


# ── the seam's process-wide state ────────────────────────────────────────────

_lock = threading.Lock()
#: The instantiated backing, keyed by name, so a config change retires the old
#: one (its worker is closed) rather than leaving two alive.
_backing: EmbeddingBacking | None = None
_backing_name: str | None = None
_collector: _Collector | None = None
#: A test-installed backing. Never set by production code; :func:`reset_seam`
#: clears it. Declared here (rather than in the tests) because the seam must be
#: injectable WITHOUT env manipulation — a test that had to set an env var to
#: exercise a backing would be testing the resolver, not the seam.
_injected: EmbeddingBacking | None = None
#: The most recent decline, so an operator can read WHY without a log search.
_last_decline: str | None = None
_last_decline_at: float | None = None

#: The last published distribution of a backing that RAN and was then replaced.
#: ⛔ Kept so ``backings[*].latency`` can answer the question its own docstring
#: claims it answers ("would the other one be better"): without it, every
#: non-selected entry is ``null`` by construction even when that backing served
#: traffic a minute ago and its reservoir was thrown away with its collector
#: (review F7). Cleared by :func:`reset_seam`.
_backing_snapshots: dict[str, dict] = {}


def _record_snapshot(name: str | None, collector: _Collector | None) -> None:
    """Remember the distribution of a backing that is about to be replaced."""
    if not name or collector is None:
        return
    with _swallow_base_exception():
        _backing_snapshots[name] = collector.snapshot()


def _note_decline(exc: BaseException) -> None:
    """Record the reason the last backing call failed. TOTAL."""
    global _last_decline, _last_decline_at
    _last_decline = f"{type(exc).__name__}: {exc}"[:400]
    _last_decline_at = time.monotonic()


def last_decline() -> str | None:
    """The last backing failure, or ``None``. Read by :func:`seam_status`."""
    return _last_decline


def _construct(name: str) -> EmbeddingBacking:
    if name == BACKING_WORKER:
        return WorkerBacking()
    if name == BACKING_EXTERNAL:
        return ExternalBacking()
    return InProcessBacking()


def _retire(retired: _Retired) -> None:
    """Stop a replaced collector and close a replaced backing — OUTSIDE the lock.

    ⛔ WHY THIS IS A FUNCTION AND NOT TWO INLINE CALLS (#14186 review). Stopping
    the collector JOINS its thread, and that thread calls :func:`active_backing`
    — which takes the module ``_lock`` — on every batch. So a caller that held
    ``_lock`` across the join would wait for a thread that is waiting for the
    lock it is holding: a BOUNDED stalemate of every ``encode_query`` for the
    join's whole duration (``_Collector.stop`` joins with an explicit
    :data:`_STOP_JOIN_S`), not an unbounded hang. The fix is the same, and the
    record says what it was: commit ``0eb23c2f``'s subject called it a "deadlock";
    it was not one (review F6). Both callers therefore mutate state under the
    lock, collect what was retired, and do the waiting after it is released.

    ⛔ AND THE CLOSE WAITS FOR THE ENCODE (review F5). ``stop()``'s join is
    bounded, so a collector can still be INSIDE an encode on the backing we are
    retiring. Closing it there makes that encode fail and records a decline the
    TEARDOWN caused — a phantom fault in the one number an operator reads. When
    the collector is still alive the backing is therefore handed to a short-lived
    daemon thread that closes it once the encode finishes: nothing is leaked, and
    nothing is closed out from under a forward pass.

    Every step is best-effort: a configuration change must not fail because the
    thing it is replacing is already broken.
    """
    if retired.collector is not None:
        retired.collector.stop()
    if retired.backing is None:
        return
    if retired.collector is not None and retired.collector.alive():
        _close_when_idle(retired.collector, retired.backing)
        return
    with _swallow_base_exception():
        retired.backing.close()


def _close_when_idle(collector: _Collector, backing: EmbeddingBacking) -> None:
    """Close ``backing`` once ``collector`` has finished its in-flight encode.

    Daemon on purpose: the remaining work is at most one encode, and a retire
    thread must never be able to keep the process alive.

    ⛔ THE WAIT IS BOUNDED, AND OVERRUNNING IT IS LOUD (review: ``_close_when_idle``
    joined unbounded). ``join()`` with no timeout meant a forward pass that never
    returns left this thread parked for the life of the process and the retired
    worker's 432 MB held forever — and SILENTLY, because the only thing that would
    have reported it is the code that never ran. The bound is generous (a full
    request budget plus the stop join and slack), so a legitimate encode never
    reaches it; past it the ticket has already been declared a wedge by its own
    valve, and the honest choice is to free the resource and NAME the overrun
    rather than hold it forever in silence.
    """
    def _wait_then_close() -> None:
        collector.join(timeout_s=_RETIRE_JOIN_S)
        if collector.alive():
            _logger.warning(
                "embed seam: closing a retired %r backing after %.1fs — its "
                "collector is STILL inside an encode (a forward pass that never "
                "returned), so that encode fails and this teardown's close is "
                "recorded as a decline (#14186)",
                getattr(backing, "name", backing), _RETIRE_JOIN_S)
        with _swallow_base_exception():
            backing.close()

    threading.Thread(
        target=_wait_then_close, name="embed-seam-retire", daemon=True).start()


class _Retired:
    """What a backing change put out of work (see :func:`_retire`)."""

    __slots__ = ("backing", "collector")

    def __init__(self, backing=None, collector=None) -> None:
        self.backing = backing
        self.collector = collector


def _peek_backing() -> EmbeddingBacking | None:
    """The backing currently in use — READ-ONLY, constructing nothing.

    ⛔ Why this exists next to :func:`active_backing`. That function is a MUTATION
    on first use: it constructs the backing and retires the collector it replaces.
    A caller that only wants to ASK the backing a question ("is a boot in
    flight?", :func:`resolve_ticket_wait_s`) must not do that — calling it from
    ``_Collector.submit`` retired the collector that was holding the ticket, and
    the ticket then declined with "the collector was stopped", on a healthy query.
    """
    with _lock:
        if _injected is not None:
            return _injected
        return _backing


def active_backing() -> EmbeddingBacking:
    """The backing this process is using, constructing it on first use."""
    global _backing, _backing_name, _collector
    retired = _Retired()
    with _lock:
        name = resolve_backing()
        if _injected is not None:
            return _injected
        if _backing is not None and _backing_name == name:
            return _backing
        # The lever moved (or this is the first resolution): retire the old
        # backing — a worker process is CLOSED, not leaked — and the collector
        # built for it, whose counters described the previous backing.
        retired = _Retired(_backing, _collector)
        _record_snapshot(_backing_name, _collector)
        _backing = _construct(name)
        # ⛔ THE COLLECTOR IS CLEARED BEFORE THE NAME MOVES (review P3).
        # ``seam_status`` reads these under no lock by design (a health read must
        # not block on a boot), so a reader landing between the two stores would
        # see the NEW name beside the OLD collector and publish the retired
        # backing's reservoir as the new one's FRESH measurement — the F3
        # mis-attribution, in the reset window. Clearing first makes that window
        # answer "no collector" (null/stale), which is honest.
        _collector = None
        _backing_name = name
        fresh = _backing
    _retire(retired)
    _logger.info(
        "embed seam: backing resolved to %r (wait %sms, batch %s) "
        "(#14186)", name, resolve_max_wait_ms(name), resolve_max_batch())
    return fresh


def install_backing(backing: EmbeddingBacking | None) -> None:
    """Install a backing for tests (``None`` restores the configured one).

    ⛔ Tests use this instead of env vars so they exercise the SEAM — the
    coalescer, the bound, the percentile bookkeeping — rather than re-testing
    :func:`resolve_backing`, which has its own rows.
    """
    global _injected, _collector
    retired = _Retired()
    with _lock:
        _injected = backing
        retired = _Retired(None, _collector)
        _collector = None
    _retire(retired)


class _swallow_base_exception:
    """Ignore any failure raised while CLOSING a retired backing.

    Closing is best-effort by design: a worker that has already died raises
    from ``terminate``/``wait``, and a configuration change must not fail
    because the thing it is replacing is already broken.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None


def _collector_for() -> _Collector:
    """The one collector for this process (rebuilt when the backing changes).

    ⛔ No backing argument: the backing is resolved by the collector itself, per
    batch, through :func:`active_backing`. Passing one in would have been a
    parameter the body never read — and a stale one the moment the lever moves.
    """
    global _collector
    with _lock:
        if _collector is None:
            _collector = _Collector(lambda: active_backing())
        return _collector


# ── the public entry points ──────────────────────────────────────────────────


def encode_query(text: str) -> list[float] | None:
    """Encode ONE query text through the seam, coalesced behind a bounded window.

    This is the entry point for the *query* embed — the 2-4 s step that runs
    inside the request today. Concurrent callers are collected into a single
    encode by :class:`_Collector`, whose window is bounded from the first
    arrival (see that class and :data:`DEFAULT_MAX_WAIT_MS_FOR_BACKING`).

    Returns the vector, or ``None`` when the backing reports the encoder is
    unavailable — the same ``None`` every caller already handles. A backing
    FAILURE raises, so the caller's existing ``except Exception`` records its
    own ``encode_failed``/decline term rather than this module inventing a
    second vocabulary for the same event.

    ⛔ WHY THE COALESCER IS IN FRONT OF THE IN-PROCESS BACKING TOO. With a 0 ms
    window the in-process path adds no wait and yields whatever coalescing the
    load already provides: while the collector encodes batch N, arrivals
    accumulate and become batch N+1. That is continuous batching with no
    admission cost, and it is why the default posture does not regress the
    uncontended query.

    ⛔ WHY THE BACKING IS RESOLVED **BEFORE** THE COLLECTOR. ``_collector_for()``
    takes the process's collector, and that collector's first ``_fire`` calls
    ``_backing_factory()`` → :func:`active_backing` — whose FIRST resolution
    retires the collector it is running on. Taking the collector first therefore
    landed the first batch's ``collect_ms``/``wait_ms``/``declines`` on an object
    ``_collector`` no longer pointed at (``/health`` published
    ``submissions: 0`` immediately after a cold start had served its first
    batch), and any ticket queued in that instant was declined with "the
    collector was stopped" on a healthy process (review F3: moved, not fixed).
    Resolving first makes that retirement a no-op, because ``_fire`` then finds a
    backing that already matches the lever.
    """
    active_backing()
    collector = _collector_for()
    rows = collector.submit(text, timeout_s=resolve_timeout_s())
    if rows is None:
        return None
    return rows[0]


def encode_texts(texts: Sequence[str], *,
                 timeout_s: float | None = None) -> list[list[float]] | None:
    """Encode a batch DIRECTLY — no coalescing, no added wait.

    The entry point for work that is ALREADY batched (the store/write funnel,
    the turn window, a backfill). Routing a batch through the collector would
    add a window to a call that has nothing left to coalesce, so the batch
    bypasses the queue entirely and the backing is called on the caller's
    thread. Returns ``None`` when the encoder is unavailable; raises on a
    backing failure, so the caller keeps its own decline bookkeeping.
    """
    backing = active_backing()
    budget = resolve_timeout_s() if timeout_s is None else timeout_s
    try:
        return backing.encode(list(texts), timeout_s=budget)
    except BaseException as exc:
        _note_decline(exc)
        raise


def seam_status() -> dict:
    """The seam's declared state and its per-backing distributions.

    Shape (stable — ``/health`` publishes it verbatim under ``embed_seam``)::

        {"backing": "inprocess" | "worker" | "external",
         "max_wait_ms": 0.0, "max_batch": 32, "timeout_s": 30.0,
         "last_decline": "<Type: message>" | null,
         "last_decline_age_s": 1.2 | null,
         "injected": false,
         "backing_state": {...},        # the backing's own describe()
         "latency": {"submissions":…, "batches":…, "coalesced":…,
                     "declines":…, "median_batch":…,
                     "collect_ms": {"n","window","p50","p95","max"},
                     "wait_ms": {"n","window","p50","p95","max"},
                     "encode_ms": {...}, "batch_size": {...}},
         "latency_stale": false,       # do the two blocks above describe
                                       # the CONFIGURED backing?
         "backings": {                  # EVERY backing, selected or not
             "inprocess": {"max_wait_ms": 0.0, "selected": true},
             "worker":    {"max_wait_ms": 20.0, "selected": false},
             "external":  {"max_wait_ms": 20.0, "selected": false}}}

    ⛔ ``backing`` IS THE CONFIGURED CHOICE — always. The state and the
    distributions are the LIVE backing's, and between a lever flip and the next
    encode those are two different backings (review F3): ``backing_state.backing``
    names the one ``backing_state``/``latency`` actually describe, and
    ``latency_stale: true`` says they are not the configured one's, so the next
    encode pays the switch (a boot, for ``worker``). Without that mark the
    pre-switch distribution was published as fresh exactly when an operator was
    reading it to verify a rollout.

    ⛔ ``backings`` lists ALL THREE whatever is selected. The issue's indicator
    is "p50/p95 exported **per backing**", and a table that only appears once you
    have already switched cannot answer "would the other one be better".
    A backing that has never run reports ``null`` percentiles, never 0. A backing
    that ran EARLIER in this process keeps its last distribution — flagged
    ``latency_stale: true``, because those observations belong to a collector that
    has since been retired, not to the one now serving (review F7).

    ⛔ TWO WAITS, and which one is the bound's proof: ``latency.collect_ms`` is
    dequeue->fire, the window-attributable wait that ``max_wait_ms`` bounds;
    ``latency.wait_ms`` is submission->fire, i.e. END-TO-END latency, which also
    includes queueing behind an in-flight encode and is therefore NOT bounded by
    the window (review F5 — on the 0 ms default a healthy process publishes
    non-zero ``wait_ms``, which is not a broken bound).

    Never raises: it is read from a health handler, and a diagnostics surface
    that can 5xx is worse than a missing key.
    """
    try:
        name = resolve_backing()
        backing = _backing
        # ⛔ WHOSE OBSERVATIONS ARE ``latency`` AND ``backing_state``?
        # (review F3 at ``76e58a21bb``.) The live collector encodes through the
        # backing that was ACTIVE when it fired, and normally that is the
        # configured one. Between a LEVER FLIP and the next encode — the
        # documented rollout path, where ``docs/infra-runbook.md`` §4.1.1e tells
        # the operator to verify with ``jq .embed_seam.backing`` — it is not: the
        # configured name has already moved while ``_backing``/``_collector``
        # still belong to the OLD backing, so its counters were published as the
        # NEW backing's, with no ``latency_stale`` mark. An operator verifying a
        # switch therefore read the pre-switch distribution as fresh, which is
        # the one reading this block exists to make impossible.
        #
        # INJECTION IS EXEMPT: an injected backing IS what the collector encodes
        # through, and its own ``describe()`` names it (``backing_state.backing``).
        live_is_configured = (
            _injected is not None or _backing_name is None
            or _backing_name == name)
        per_backing: dict[str, dict] = {}
        for candidate in BACKINGS:
            entry: dict[str, Any] = {
                "max_wait_ms": resolve_max_wait_ms(candidate),
                "selected": candidate == name,
            }
            if candidate == name and _collector is not None \
                    and live_is_configured:
                entry["latency"] = _collector.snapshot()
            else:
                # A backing that ran EARLIER in this process keeps its last
                # distribution, marked stale; one that has never run is an
                # honest "no observation", never a zero that reads as "instant".
                prior = _backing_snapshots.get(candidate)
                if candidate == _backing_name and _collector is not None:
                    # The LIVE collector belongs to the backing the lever has
                    # moved away from. Its numbers are real and current — but
                    # they are THAT backing's, so they are filed under it and
                    # marked stale, never presented as the configured one's.
                    prior = _collector.snapshot()
                entry["latency"] = prior
                entry["latency_stale"] = prior is not None
            per_backing[candidate] = entry
        view: dict[str, Any] = {
            "backing": name,
            "max_wait_ms": resolve_max_wait_ms(name),
            "max_batch": resolve_max_batch(),
            "timeout_s": resolve_timeout_s(),
            "last_decline": _last_decline,
            "last_decline_age_s": (
                None if _last_decline_at is None
                else round(time.monotonic() - _last_decline_at, 3)),
            "injected": _injected is not None,
            "backing_state": (
                _injected.describe() if _injected is not None
                else (backing.describe() if backing is not None else None)),
            "latency": (
                _collector.snapshot() if _collector is not None else {
                    "submissions": 0, "batches": 0, "coalesced": 0,
                    "declines": 0, "median_batch": None,
                    "collect_ms": _Latency().snapshot(),
                    "wait_ms": _Latency().snapshot(),
                    "encode_ms": _Latency().snapshot(),
                    "batch_size": _Latency().snapshot(),
                }),
            #: Do ``latency`` (and the ``backing_state`` above) describe the
            #: CONFIGURED backing? ``false`` ⇒ the lever has moved since the last
            #: encode, ``backing_state.backing`` names the one they DO describe,
            #: and the next encode pays the switch (a boot, for ``worker``).
            "latency_stale": not live_is_configured,
            "backings": per_backing,
        }
        return view
    except Exception as exc:
        return {
            "backing": None, "max_wait_ms": None, "max_batch": None,
            "timeout_s": None, "last_decline": None, "last_decline_age_s": None,
            "injected": False, "backing_state": None, "latency": None,
            "latency_stale": None, "backings": None,
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def reset_seam() -> None:
    """Tear down everything this module holds — the ONE test/reset hook.

    Stops the collector, closes the configured backing (a worker process is
    terminated, not orphaned), clears the injected one and the decline latch.
    Called from ``conftest`` where it matters and by the seam's own rows.
    """
    global _backing, _backing_name, _collector, _injected
    global _last_decline, _last_decline_at
    with _lock:
        collector, _collector = _collector, None
        backing, _backing = _backing, None
        _backing_name = None
        _injected = None
        _last_decline = None
        _last_decline_at = None
        _backing_snapshots.clear()
    # ⛔ ONE teardown path, shared with a backing change: stop before close, and
    # defer the close while an encode is still running on this backing
    # (:func:`_retire`). Two hand-rolled copies is how the reset grew a close
    # that could land under an in-flight forward pass while the swap path did not
    # (review F5).
    _retire(_Retired(backing, collector))
