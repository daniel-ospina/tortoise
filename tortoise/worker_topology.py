"""Multi-worker uvicorn topology — the fail-closed admission gate (#1677).

WHY THIS MODULE EXISTS
======================
`Dockerfile.hosted` passes no `--workers`, so production runs ONE uvicorn
worker today. That is not a decision the engine can see: **uvicorn resolves
`--workers` as `CLI flag -> $WEB_CONCURRENCY -> 1`** (uvicorn settings docs), so
any actor that can set one Fly env var moves the deployment to N workers with
no code change, no review, and no signal. The issue this module answers
(#1677) measured what breaks at N >= 2, and all of it is silent:

* the ``_lifespan`` BackupWatcher (files GitHub issues, sends Telegram) and the
  hourly event-retention loop run **once per worker** — non-idempotent external
  side effects, N times (P1-2);
* ``_GITHUB_STATES`` — the GitHub OAuth CSRF register — is an in-memory dict, so
  onboarding CSRF-400s intermittently depending on which worker the callback
  lands on (P1-C, launch feature);
* ``GoldenSignals``/``collect_signals`` compute P99/throughput/error-rate
  **per process**, so at N >= 2 no process holds the service's numbers — a
  "boots and CSRF passes" smoke test stays green while the metrics go wrong;
* the 9090 ``/healthz`` listener binds on one worker and every other worker's
  bind failure is **swallowed** unless ``TORTOISE_HEALTHZ_REQUIRED=1``;
* each worker opens its own FalkorDB Cloud pool and its own JWKS cache — N x an
  external client budget.

Under``multiprocessing``'s **spawn** start method (uvicorn's supervisor does not
fork) the ~85 s model pre-warm also re-executes in full per worker, so the
memory envelope is ~N x, not "1 x + copy-on-write pages" — and uvicorn has no
``--preload`` (that is a Gunicorn flag), so there is no COW mitigation.

Until those guards land, ``workers > 1`` is not a supported topology. This
module makes turning it on a **deliberate, logged act** instead of an ambient
side effect of an environment variable.

WHAT IT DOES NOT DO
===================
It does not make multi-worker work. It refuses it. The refusal names every
unlanded guard and the issue that owns it, so the message is a diagnosis rather
than a mystery — and it is the ledger a later lane edits when a guard lands.

TWO FACTS, MEASURED — NOT INFERRED
==================================
1. **The count.** ``entrypoint.sh`` measures it exactly as uvicorn will
   (``--workers N`` / ``--workers=N`` over the CMD argv, else
   ``$WEB_CONCURRENCY``, else 1) and exports it as ``TORTOISE_WEB_WORKERS``.
   ⚠️ That variable is an **OUTPUT of the boot script**, never an operator
   input: setting it by hand cannot raise the count (uvicorn never reads it),
   so it cannot be used to defeat this gate.

2. **Whether this process IS one of N.** ``multiprocessing.parent_process()``
   is not ``None`` **iff** uvicorn's multi-worker supervisor spawned us —
   verified against the pinned uvicorn (0.52.1): workers=1 boots in-process
   with ``parent_process() is None``, while workers=2 yields
   ``parent_process() = <MainProcess>`` and ``SpawnProcess-{1,2}``. This is what
   closes the bypass: ``uvicorn tortoise.hosted_api:app --workers 2`` run
   directly (skipping the entrypoint) still refuses. It is deliberately NOT
   derived from ``WEB_CONCURRENCY``, which is only the flag's *default source*
   and can read 1 while the process is worker 3 of 8.

REFUSAL IS FATAL, ON PURPOSE
============================
The gate raises during ``_lifespan`` startup. uvicorn's supervisor treats a
worker's ``STARTUP_FAILURE`` as fatal for the whole parent
(``supervisors/multiprocess.py::keep_subprocess_alive`` -> ``should_exit`` ->
``terminate_all``), so a misconfiguration is a loud, deploy-blocking refusal
rather than a machine that half-works. That is the same posture
``entrypoint.sh`` already takes for a missing ``FALKORDB_CLOUD_URI``
("Refusing to start with no durable DB"): a topology this deployment has not
been proved in is not a degraded mode to be tolerated, it is a corrupting one.

WHY NOT "just run 1 worker and warn"
------------------------------------
The count is the *parent's* — a worker cannot lower it. It could only refuse or
pretend. Pretending is how the ambient ``WEB_CONCURRENCY`` hole would survive.

THE ESCAPE HATCHES, STATED EXACTLY
==================================
* ``TORTOISE_MULTI_WORKER=1`` — acknowledgement that this deployment carries the
  multi-worker guards. It converts the "unlanded guards" refusal into a
  ``WARNING`` that re-lists the residual risks on every worker's boot, so the
  state stays visible instead of becoming folklore. Same shape as the repo's
  existing ``TORTOISE_HEALTHZ_REQUIRED`` / ``TORTOISE_ALLOW_NONSTANDARD_PATH``
  opt-ins.
* **No escape hatch for the embedded case.** With no resolvable
  ``TORTOISE_DB_URI`` the app runs an embedded redislite file; N workers get N
  *divergent* copies of it. That is silent data divergence, not a degraded
  feature, so ``TORTOISE_MULTI_WORKER`` does not reach it.

RESIDUAL, NAMED RATHER THAN IMPLIED
===================================
The spawned-worker probe recognises **uvicorn's** supervisor. A different
process manager that pre-forks before importing the app (Gunicorn +
``UvicornWorker``) leaves ``parent_process() is None``, so on that (not
deployed, not supported) topology only the ``TORTOISE_WEB_WORKERS`` arm of the
gate would speak. Closing it properly would need a per-manager adapter; doing
that for a manager this repo does not run would be machinery with no measured
failure behind it.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Mapping
from dataclasses import dataclass

from tortoise.config import SUPPORTED_URI_SCHEMES
from tortoise.env_truthy import is_truthy

logger = logging.getLogger(__name__)

#: Set by ``entrypoint.sh``: the worker count uvicorn WILL run, measured with
#: uvicorn's own precedence. **An output of the boot script — not an input.**
WORKERS_ENV = "TORTOISE_WEB_WORKERS"

#: The explicit acknowledgement that this deployment has landed the
#: multi-worker guards. Unset (the default) makes ``workers > 1`` fatal.
ACK_ENV = "TORTOISE_MULTI_WORKER"

#: ``WEB_CONCURRENCY`` is uvicorn's *default source* for ``--workers``. It is
#: read here only to RECOGNISE a topology, never to authorise one.
WEB_CONCURRENCY_ENV = "WEB_CONCURRENCY"

#: Every guard #1677 identifies as unlanded. This tuple IS the ledger: a lane
#: that lands one deletes its row, and the deletion is the review artifact.
#: Each row names the surface and the issue section that owns it.
UNLANDED_GUARDS: tuple[str, ...] = (
    "daemons (P1-2): the _lifespan BackupWatcher files GitHub issues and sends "
    "Telegram, and the hourly event-retention loop runs too — both arm once PER "
    "WORKER, so their non-idempotent side effects fire N times. Awaiting the "
    "owner's A/B decision recorded on #1677.",
    "_GITHUB_STATES (P1-C): the GitHub OAuth CSRF register is an in-memory dict, "
    "so an onboarding callback lands on a worker that never saw the state and "
    "answers 404 — onboarding CSRF-fails intermittently at N>=2.",
    "metrics: GoldenSignals/collect_signals compute P99, throughput, error-rate "
    "and saturation PER PROCESS, so no process holds the service-wide numbers.",
    "healthz: the 9090 listener binds on exactly one worker; every other "
    "worker's bind failure is swallowed unless TORTOISE_HEALTHZ_REQUIRED=1, and "
    "fly.toml marks that check deploy-blocking by design.",
    "pools: each worker opens its own FalkorDB Cloud connection pool and its own "
    "JWKS cache — N x the managed client budget and N independent IdP refreshes.",
)


class MultiWorkerRefused(RuntimeError):
    """Raised at startup when this process may not be one of N>1 workers."""


def _positive_int(raw: object) -> int | None:
    """``raw`` as a positive int, or None when it does not parse as one.

    None — never a default — so a malformed value cannot be silently read as 1
    (or as anything else). Callers that need a count fall through to the next
    source only on None.
    """
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def resolve_effective_workers(environ: Mapping[str, str] | None = None) -> int:
    """The worker count uvicorn will run, by uvicorn's own precedence.

    ``TORTOISE_WEB_WORKERS`` (measured by ``entrypoint.sh`` from the CMD argv,
    which is authoritative and already accounts for a CLI ``--workers``) wins;
    then ``$WEB_CONCURRENCY`` (uvicorn's default source); then 1.

    ⚠️ Returns a **recognition** of the topology, not an authorisation: see
    :func:`refusal_reason` for the policy.
    """
    env = os.environ if environ is None else environ
    for name in (WORKERS_ENV, WEB_CONCURRENCY_ENV):
        count = _positive_int(env.get(name))
        if count is not None:
            return count
    return 1


def is_spawned_worker() -> bool:
    """True iff this process was spawned by uvicorn's multi-worker supervisor.

    Verified against the pinned uvicorn: ``workers=1`` runs the server in the
    current process (``parent_process() is None``); ``workers>=2`` spawns
    children through ``multiprocessing``'s spawn context, so each child has a
    parent process. This is the arm that catches a direct
    ``uvicorn ... --workers N`` invocation that never ran ``entrypoint.sh``.
    """
    return multiprocessing.parent_process() is not None


def _uri_names_a_shared_backend(value: str | None) -> bool:
    """True when ``value`` is a URI with a scheme this repo routes to a server.

    Deliberately the SAME scheme set the connection layer validates
    (``tortoise.config.SUPPORTED_URI_SCHEMES``), so "is the DB shared" cannot
    drift from "will this URI be dialled". A value without ``://`` is a
    filesystem path (backward compat in ``config.py``) — i.e. embedded.
    """
    if not value:
        return False
    scheme, sep, rest = str(value).partition("://")
    return bool(sep) and bool(rest.strip()) and scheme.strip().lower() in SUPPORTED_URI_SCHEMES


def db_is_shared(environ: Mapping[str, str] | None = None) -> bool:
    """True when a shared (server) DB is resolvable, i.e. NOT embedded mode.

    ``entrypoint.sh`` promotes ``FALKORDB_CLOUD_URI`` to ``TORTOISE_DB_URI``
    before exec, so on the sanctioned boot path ``TORTOISE_DB_URI`` alone
    answers it. The second arm exists for a direct ``uvicorn`` invocation (and
    for tests) where that promotion has not run.
    """
    env = os.environ if environ is None else environ
    return (
        _uri_names_a_shared_backend(env.get("TORTOISE_DB_URI"))
        or _uri_names_a_shared_backend(env.get("FALKORDB_CLOUD_URI"))
    )


def ack_is_set(environ: Mapping[str, str] | None = None) -> bool:
    """True when ``TORTOISE_MULTI_WORKER`` is set to a truthy value.

    Truthiness is the repo's declared contract (``tortoise.env_truthy``), so
    ``0``/``false``/``no``/``off`` AND a typo all read "not acknowledged"
    rather than "a non-empty string is set" — a security acknowledgement must
    fail closed on a typo, not open.
    """
    env = os.environ if environ is None else environ
    return is_truthy(env.get(ACK_ENV))


@dataclass(frozen=True)
class Topology:
    """The measured worker topology of THIS process, plus this deployment's facts."""

    workers: int
    spawned: bool
    shared_db: bool
    acknowledged: bool

    @property
    def is_multi_worker(self) -> bool:
        """True when this process is one of N>1 workers, by either measurement.

        ``spawned`` is checked independently of ``workers`` on purpose: an
        unresolved count with a real supervisor parent is exactly the
        ``WEB_CONCURRENCY`` case (it can read 1 while the process is worker 3),
        so the probe must not be able to contradict the observation.
        """
        return self.spawned or self.workers > 1


def observe(environ: Mapping[str, str] | None = None,
            *, spawned: bool | None = None) -> Topology:
    """Measure the topology. ``spawned`` is injectable so the policy is testable."""
    return Topology(
        workers=resolve_effective_workers(environ),
        spawned=is_spawned_worker() if spawned is None else spawned,
        shared_db=db_is_shared(environ),
        acknowledged=ack_is_set(environ),
    )


def _guard_list() -> str:
    return "\n".join(f"  [{i}] {row}" for i, row in enumerate(UNLANDED_GUARDS, 1))


def refusal_reason(topology: Topology) -> str | None:
    """Why this topology may not run, or None when it may.

    Pure function of a measured :class:`Topology` — no environment reads — so
    every branch is directly testable.
    """
    if not topology.is_multi_worker:
        return None
    if not topology.shared_db:
        # NOT reachable by TORTOISE_MULTI_WORKER, deliberately: divergent
        # embedded copies are corruption, not a degraded feature.
        return (
            "Refusing to run N>=2 uvicorn workers on an EMBEDDED database.\n"
            "No shared TORTOISE_DB_URI/FALKORDB_CLOUD_URI resolves, so each "
            "worker would open its own embedded redislite file and the copies "
            "would DIVERGE — silent data divergence, not a degraded feature, so "
            f"{ACK_ENV} does not lift this refusal.\n"
            "Fix: set TORTOISE_DB_URI (or FALKORDB_CLOUD_URI) to the shared "
            "instance, or run a single worker (unset "
            f"{WEB_CONCURRENCY_ENV} / drop --workers)."
        )
    if not topology.acknowledged:
        return (
            f"Refusing to run N>={topology.workers} uvicorn workers: this "
            "topology is not supported yet and its guards are unlanded (#1677).\n"
            f"{_guard_list()}\n"
            f"Set {ACK_ENV}=1 to acknowledge these residuals and run anyway "
            "(they are re-listed as a WARNING on every worker's boot), or run a "
            f"single worker by unsetting {WEB_CONCURRENCY_ENV} / dropping "
            "--workers."
        )
    return None


def assert_multi_worker_admissible(environ: Mapping[str, str] | None = None,
                                   *, spawned: bool | None = None) -> Topology:
    """Fail closed unless THIS process may be one of N>1 workers.

    Returns the measured :class:`Topology` on the permitted path (so a caller
    can log it). Raises :class:`MultiWorkerRefused` otherwise — from the
    startup half of ``_lifespan`` that is a uvicorn ``STARTUP_FAILURE``, which
    terminates the whole parent rather than leaving a machine that half-works.
    """
    topology = observe(environ, spawned=spawned)
    reason = refusal_reason(topology)
    if reason is not None:
        raise MultiWorkerRefused(reason)

    if topology.is_multi_worker:
        # Permitted only via the acknowledgement — say so EVERY boot, so an
        # acknowledged-but-risky topology never becomes invisible folklore.
        logger.warning(
            "multi-worker topology is ACKNOWLEDGED but its guards are still "
            "unlanded (#1677) — workers=%s shared_db=%s:\n%s",
            topology.workers, topology.shared_db, _guard_list(),
        )
    return topology


# --- self-check ---
if __name__ == "__main__":
    _shared = {"TORTOISE_DB_URI": "rediss://host:1/db"}
    _embedded: dict[str, str] = {}

    # uvicorn precedence: entrypoint measurement beats WEB_CONCURRENCY beats 1.
    assert resolve_effective_workers({}) == 1
    assert resolve_effective_workers({"WEB_CONCURRENCY": "3"}) == 3
    assert resolve_effective_workers({"WEB_CONCURRENCY": "3", WORKERS_ENV: "2"}) == 2
    assert resolve_effective_workers({"WEB_CONCURRENCY": "oops"}) == 1

    # embedded + N>=2 is refused and NOT liftable by the acknowledgement.
    assert refusal_reason(observe(_embedded, spawned=False)) is None
    assert refusal_reason(observe({**_embedded, ACK_ENV: "1"}, spawned=True)) is not None
    # shared + N>=2 needs the acknowledgement.
    assert refusal_reason(observe({**_shared, WORKERS_ENV: "2"}, spawned=False)) is not None
    assert refusal_reason(
        observe({**_shared, WORKERS_ENV: "2", ACK_ENV: "1"}, spawned=False)) is None
    # a spawned child refuses even when the count reads 1.
    assert refusal_reason(observe({**_shared, WORKERS_ENV: "1"}, spawned=True)) is not None

    print("✅ worker_topology")
