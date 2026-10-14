"""Self-host off-box backup DRIVER (#8544).

#8543 (owner decision 2026-10-13, durability PARITY with hosted) made the
off-box backup target a STEP of the self-host setup path — declare an ``R2_*``
target, or decline one explicitly — and had the daemon report an undecided
target loudly at boot. The lane that landed it recorded, on #8544, that the
CONFIGURATION half had no EXECUTION half: ``backup_sweep.run_backup_sweep``'s
only production caller is the hosted app's ``POST /v1/internal/backups/sweep``,
so a self-host deployment that declared a target validated its config and
produced **no archive**. This module is that missing driver.

WHY THIS IS NOT ``run_backup_sweep`` (measured, not assumed):

* Its enumeration seam is a CONTROL PLANE — a Supabase PostgREST source or the
  ``registry_control_plane`` FalkorDB graph (``enumerate_orgs`` /
  ``enumerate_org_graphs`` / ``org_graph_name``). A self-host deployment has
  neither: it is single-tenant, and its only graph is ``org_selfhost``.
* ``_backup_graph`` fail-closes without ``REGISTRY_STREAM_KEY`` (a Fly-only
  hosted secret, ``backup_config.py`` #661), and ``_sweep_org`` writes hosted
  OPS STATE + per-graph metering into the same bucket.
* Pointing the hosted sweep at a self-host deployment therefore requires
  inventing a registry adapter, a second encryption key and a metering
  no-op — machinery wearing the mask of reuse (#2880 S1).

WHAT IT REUSES INSTEAD: ``hosted_backup.create_backup``, which is exactly the
per-graph primitive ``_backup_graph`` calls — dump (``tortoise-logical-dump-v1``)
→ AES-256-GCM encrypt → upload → per-object ledger → **read-back verification**
of the uploaded objects against the ledger (#5062). The artifact is the same
shape a hosted restore reads, so the archive is not a private dialect.

NO CONTROL PLANE, AND WHY THAT IS SAFE: ``create_backup``'s registry argument
is used only for a best-effort "backup_latest_at" stamp (``_stamp_backup_latest``,
wrapped in try/except by the caller). ``_NoControlPlane`` below answers that one
read with an EMPTY result set rather than an exception: the self-host lane has
no control-plane row to stamp, so a no-op is the truthful answer and an hourly
warning would be pure noise.

DELIVERY MODEL (#8543 chose parity; #2880 S1 rejected a self-host backup
*service*): the driver is an IN-PROCESS daemon thread armed by the same
``off_box_backup_state`` decision the boot signal reports. It is deliberately
not a compose sidecar — a sidecar adds a SECOND silent-absence surface (target
declared, sidecar not started, no archive) and the daemon is already the one
process that reads the setup decision. It is deliberately not a scheduler
crate: the interval is one env var, and a failed run is a loud log line on
every attempt, never a silent skip.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

_logger = logging.getLogger(__name__)

#: Seconds between sweep attempts. One hour mirrors the hosted
#: `registry-backup-cron.yml` cadence (17 * * * *) so the two lanes' RPO
#: windows are stated in the same unit.
DEFAULT_INTERVAL_SECONDS = 3600

#: Floor on the operator-settable interval. A sub-minute cadence would hammer
#: the store for no honest gain (the dump is a full graph walk) — and a typo'd
#: "0" must not become a busy loop, so the resolver clamps rather than trusts.
MIN_INTERVAL_SECONDS = 60

#: Operator override for the cadence.
INTERVAL_ENV = "TORTOISE_OFFBOX_BACKUP_INTERVAL_SECONDS"

#: The prune-window knobs the HOSTED sweep reads (``backup_config._load_from_env``;
#: documented in ``.env.example``). The self-host driver resolves the SAME three
#: so a window an operator configured for one lane applies to the other — the
#: ``BackupConfig`` class attributes are only the fallback, never a second copy.
RETENTION_ENVS = (
    ("BACKUP_RETENTION_HOURLY", "retention_hourly"),
    ("BACKUP_RETENTION_DAILY", "retention_daily"),
    ("BACKUP_RETENTION_WEEKLY", "retention_weekly"),
)


class SelfhostBackupError(RuntimeError):
    """A self-host off-box backup run failed (no archive is durable)."""


class _NoControlPlane:
    """The self-host lane's control-plane seam: there is nothing to stamp.

    ``create_backup`` calls ``_stamp_backup_latest(registry, org_id, ts)``
    after the upload's read-back verification, inside a best-effort
    try/except. A registry-dialect source needs a ``query(cypher, ...)`` whose
    result carries ``result_set``; answering with an EMPTY set makes the stamp
    a proven no-op — an exception would instead log a warning on every run for
    a surface this deployment genuinely does not have.
    """

    def query(self, cypher: str, params: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(result_set=[])


def offbox_backup_interval_seconds(env: dict | None = None) -> int:
    """Resolve the sweep interval, clamped to ``[MIN_INTERVAL_SECONDS, ∞)``.

    A non-numeric value falls back to the default rather than raising; a value
    below the floor — including a non-positive one — is clamped UP to
    ``MIN_INTERVAL_SECONDS`` (a typo'd ``0`` becomes one minute, never a busy
    loop). Either way the cadence knob can never wedge a boot, and the resolved
    value is visible in the boot line the driver logs.
    """
    raw = (env if env is not None else os.environ).get(INTERVAL_ENV, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    if value < MIN_INTERVAL_SECONDS:
        return MIN_INTERVAL_SECONDS
    return value


def selfhost_retention(env: dict | None = None) -> tuple[int, int, int]:
    """Resolve ``(hourly, daily, weekly)`` exactly as the hosted sweep does.

    ``run_backup_sweep`` prunes with a ``BackupConfig`` built by
    ``backup_config._load_from_env``, which reads the ``BACKUP_RETENTION_*``
    knobs. Pruning with the bare class attributes instead silently ignored an
    operator's configured window on this lane (#8610 review P2): a raised
    window was pruned back to the default (fewer recovery points than the
    operator asked for) and a lowered one was not applied either.

    ``load_config()`` is deliberately NOT used to resolve them: it fail-closes
    to a DISABLED config when ``BACKUP_SWEEP_ENABLED`` is unset — exactly the
    hosted switch this lane removed from its target set. A malformed value
    falls back to the ``BackupConfig`` default with a warning rather than
    raising, because a retention typo must not be able to stop the archive this
    driver exists to write.
    """
    from tortoise.backup_config import BackupConfig

    source = env if env is not None else os.environ
    counts: list[int] = []
    for var, attr in RETENTION_ENVS:
        fallback = int(getattr(BackupConfig, attr))
        raw = str(source.get(var, "") or "").strip()
        if not raw:
            counts.append(fallback)
            continue
        try:
            counts.append(int(raw))
        except ValueError:
            _logger.warning(
                "selfhost off-box backup: %s=%r is not an integer — pruning "
                "with the default %d", var, raw, fallback,
            )
            counts.append(fallback)
    return counts[0], counts[1], counts[2]


def selfhost_graph_name() -> str:
    """The graph this deployment backs up — derived, never re-spelled.

    ``sdk._derive_graph_name`` is the ONE namespace → DB-graph derivation
    (#4240 review F2). A second copy here would key the archive to a graph the
    daemon does not open the day the constant or the rule changes.
    """
    from tortoise.mcp_auth import SELFHOST_ORG_ID
    from tortoise.sdk import _derive_graph_name

    return _derive_graph_name(namespace=SELFHOST_ORG_ID, graph_name=None,
                              uri_graph=None)


def _default_storage():
    """The declared target's object store (R2), fail-closed on a partial config.

    Mirrors ``hosted_api._backup_storage``'s R2 leg without importing the
    hosted app: the self-host daemon must not pull hosted platform machinery
    into its process (``selfhost.py``'s grep gate).
    """
    from tortoise.hosted_backup import R2Storage

    return R2Storage()


def _default_sdk():
    """The SAME SDK the REST door uses, so the driver dumps the live graph.

    ``selfhost_api._sdk`` owns the embedded keepalive anchor and the journal
    path; building a second SDK here would (a) open a second connection to the
    same store and (b) risk a graph-name/journal divergence — the #4240 class.
    """
    from tortoise.selfhost_api import _sdk

    return _sdk()


def run_selfhost_offbox_backup(*, storage=None, sdk=None, now=None,
                               prune: bool = True) -> dict:
    """One off-box backup of this deployment's graph. Returns the manifest.

    Raises on any failure that leaves no verified archive (``R2Storage`` on a
    partial config, a dump error, an upload or read-back failure). The caller
    — the driver loop, or a test — owns the "loud, never silent" contract;
    this function never swallows.

    The SDK is held in a local for the whole call: its pooled connection must
    outlive the dump (a collected SDK closes on GC — the #3718/#3750 shape).
    """
    from tortoise.hosted_backup import create_backup, prune_backups
    from tortoise.mcp_auth import SELFHOST_ORG_ID

    org_id = SELFHOST_ORG_ID
    graph_name = selfhost_graph_name()

    store = _default_storage() if storage is None else storage
    held_sdk = _default_sdk() if sdk is None else sdk
    proj = held_sdk._get_proj()
    manifest = create_backup(
        proj, _NoControlPlane(), store,
        org_id=org_id, graph_name=graph_name,
    )
    if prune:
        # Same retention triple the hosted sweep uses — the ONE default
        # authority is ``BackupConfig`` (docs/retention-and-deletion.md states
        # the horizon), resolved from the same ``BACKUP_RETENTION_*`` knobs the
        # hosted lane reads so a configured window is not silently ignored
        # (#8610 review P2). Pruning the SAME pool we just wrote is what bounds
        # the bucket — an unbounded bucket is a cost defect, not a durability
        # one.
        keep_hourly, keep_daily, keep_weekly = selfhost_retention()
        try:
            prune_backups(
                store, org_id,
                keep_daily=keep_daily,
                keep_weekly=keep_weekly,
                keep_hourly=keep_hourly,
            )
        except Exception as exc:
            # The archive IS durable — a prune failure must not fail the run
            # (mirrors the sweep's own contract), but it must be visible.
            _logger.warning("selfhost off-box backup prune failed: %s", exc)
    ts = now or datetime.now(timezone.utc)  # noqa: UP017
    return {
        "status": "backed_up",
        "org_id": org_id,
        "graph_name": graph_name,
        "backup_id": manifest.get("backup_id", ""),
        "node_count": manifest.get("node_count"),
        "edge_count": manifest.get("edge_count"),
        "sha256": manifest.get("sha256"),
        "at": ts.isoformat(),
    }


class OffboxBackupDriver:
    """Daemon-thread sweep loop: one run per interval, starting after the first.

    THE FIRST RUN IS AFTER ONE INTERVAL, NOT AT BOOT, for two reasons: the
    cadence then matches the hosted lane's hourly roll-up (up to ~1h RPO, the
    parity #8543 chose) with no special-case "burst on every restart", and a
    short-lived process — every in-process ``TestClient`` boot in the suite
    included — arms the loop without ever reaching the network. A deployment
    that restarts before its first interval produces no archive, which is why
    the boot signal says a declared target is not yet a copy.

    State is deliberately minimal — a stop event and counters. ``failures``
    counts CONSECUTIVE failed runs (a success resets it), which is what the
    "N consecutive failure(s)" error line claims and what lets an operator see
    a target recover. The thread is a daemon so a wedged store can never hold
    the interpreter open past the daemon's own shutdown drain (#2203's
    discipline).
    """

    def __init__(self, *, interval_seconds: int, run=None, sleep=None) -> None:
        self.interval_seconds = interval_seconds
        self._run = run or run_selfhost_offbox_backup
        # ``Event.wait`` (not ``time.sleep``) so ``stop()`` is prompt even
        # mid-interval; injectable for tests to avoid real waiting.
        self._sleep = sleep or self._event_wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last_result: dict | None = None

    def _event_wait(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sleep(self.interval_seconds)
            if self._stop.is_set():
                break
            self.run_once()

    def run_once(self) -> dict | None:
        """Run one sweep. A failure is LOGGED and counted, never raised.

        The loop must survive a transient store outage (the next pass retries);
        it must not survive one SILENTLY, so every failure is an error line
        carrying the exception, and the counters are readable for a future
        health surface.
        """
        try:
            result = self._run()
        except Exception as exc:
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            _logger.error(
                "selfhost off-box backup FAILED (%d consecutive failure(s)): %s",
                self.failures, self.last_error,
            )
            return None
        self.runs += 1
        # CONSECUTIVE, not cumulative: a success resets the streak so the
        # error line's "N consecutive failure(s)" is true after a recovery
        # rather than reporting an intermittent target as a continuous outage
        # (#8610 review P3).
        self.failures = 0
        self.last_error = None
        self.last_result = result
        _logger.info(
            "selfhost off-box backup ok: org=%s graph=%s nodes=%s backup_id=%s",
            result.get("org_id"), result.get("graph_name"),
            result.get("node_count"), result.get("backup_id"),
        )
        return result

    def start(self) -> bool:
        """Start the loop thread. Idempotent — a second call is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="selfhost-offbox-backup", daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the loop to stop and bound the join."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)


_DRIVER: OffboxBackupDriver | None = None
_DRIVER_LOCK = threading.Lock()


def start_offbox_backup_driver(*, interval_seconds: int | None = None,
                               run=None) -> OffboxBackupDriver:
    """Arm the process-wide driver and return it (idempotent).

    The interval is resolved ONCE here and logged, so the cadence the
    deployment is actually running at is stated in the container log rather
    than inferred from env after the fact.
    """
    global _DRIVER
    interval = (offbox_backup_interval_seconds() if interval_seconds is None
                else interval_seconds)
    with _DRIVER_LOCK:
        if _DRIVER is None:
            _DRIVER = OffboxBackupDriver(interval_seconds=interval, run=run)
        driver = _DRIVER
    if driver.start():
        _logger.warning(
            "selfhost off-box backup DRIVER armed: every %ds, graph=%s "
            "(#8544). A failed run is logged loudly on every attempt.",
            interval, selfhost_graph_name(),
        )
    return driver


def stop_offbox_backup_driver() -> None:
    """Stop the process-wide driver (idempotent; used by the app lifespan)."""
    global _DRIVER
    with _DRIVER_LOCK:
        driver, _DRIVER = _DRIVER, None
    if driver is not None:
        driver.stop()
