"""Backup watcher — the driver-disabled leg of the dual-watcher design.

A read-only, in-process daemon (spawned in the app lifespan, Task 7) that
computes PER-TEAM backup staleness and drives the alert store directly (files
GitHub issues + pushes Telegram itself — it does not depend on any R2 marker
being read by someone else, so the driver-disabled case is covered by
construction).

Read-only w.r.t. the graphs: the watcher takes an injected team provider
(default: the registry seam) and NEVER writes to any graph — asserted by the
absence of any graph handle in the class.

R2-outage semantics (neither fabricate NOR silence):
- Fresh boot + R2 down + empty last-known-good cache ⇒ UNKNOWN → no alerts
  (silence is honest when nothing was read; NEVER requires a confirmed listing).
- Degraded-from-known-good ⇒ evaluate from the cached last-known-good state
  and file via the alert store's GH-search fallback.
- DRIVER_DOWN is gated on last-known-good R2 state and suppressed while the
  kill-switch is off.

The daemon loop is crash-safe (per-poll try/except) and self-healing: a
watchdog thread restarts it if it exits; every HTTP call carries explicit
timeouts (in the clients); process RSS growth is trend-checked per poll.
"""

from __future__ import annotations

import json
import logging
import resource
import threading
import time  # noqa: F401
from datetime import datetime, timezone
from typing import Any, Callable  # noqa: UP035

from .alert_store import AlertStore

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "ops/watcher-heartbeat.json"
SIMULATE_PREFIX = "ops/simulate/"


def _read_json(storage, key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(storage.download(key))
        return parsed if isinstance(parsed, dict) else {}
    except (KeyError, ValueError):
        return {}


def _team_prefixes(storage) -> list[str]:
    """Top-level team directories under ``backups/`` — app-down-independent."""
    teams: set[str] = set()
    for k in storage.list("backups/"):
        parts = k.split("/")
        if len(parts) >= 2 and parts[0] == "backups" and parts[1]:
            teams.add(parts[1])
    return sorted(teams)


def _parse_backup_ts(token: str) -> datetime | None:
    """Parse a manifest-key timestamp token (``{ts}_{rnd}``) into UTC."""
    ts = token.split("_", 1)[0]  # strip the {rnd} suffix (review P1-1)
    for fmt in ("%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)  # noqa: UP017
        except ValueError:
            continue
    return None


def _default_graph_newest(storage, team_id: str, newest: datetime | None) -> datetime | None:
    """Merge the DEFAULT graph's archive freshness into ``newest``.

    Post-#2313 the default graph's dumps live under the literal ``default``
    key segment (``backups/{team}/default/{ts}_{rnd}/…``); pre-#2313 dumps
    are flat (``backups/{team}/{ts}_{rnd}/…``). Both shapes are the default
    graph — team-level freshness is the max over the two (#2313 Task 4).
    Custom nested keys (``backups/{team}/{g_..}/…``) are NOT team-level; the
    per-graph surface handles them.
    """
    for k in storage.list(f"backups/{team_id}/"):
        if not k.endswith("/manifest.json"):
            continue
        parts = k.split("/")
        if len(parts) == 4:
            parsed = _parse_backup_ts(parts[2])  # legacy flat
        elif len(parts) == 5 and parts[2] == "default":
            parsed = _parse_backup_ts(parts[3])  # default nested
        else:
            continue  # custom nested — per-graph surface
        if parsed is not None and (newest is None or parsed > newest):
            newest = parsed
    return newest


def _newest_backup_ts(storage, team_id: str) -> datetime | None:
    """Newest archive timestamp for a team's DEFAULT graph from R2 manifest
    keys (key-derived, restore-surviving) — legacy flat + ``default`` nested."""
    return _default_graph_newest(storage, team_id, None)


def _newest_graph_backup_ts(storage, team_id: str, graph_id: str) -> datetime | None:
    """Newest archive timestamp for ONE graph (#2313 Task 4)."""
    newest: datetime | None = None
    for k in storage.list(f"backups/{team_id}/{graph_id}/"):
        if not k.endswith("/manifest.json"):
            continue
        parts = k.split("/")
        if len(parts) != 5:
            continue
        parsed = _parse_backup_ts(parts[3])
        if parsed is not None and (newest is None or parsed > newest):
            newest = parsed
    return newest


def compute_status(
    *,
    now: datetime,
    teams: list[str],
    r2_teams: list[str],
    state_teams: list[str],
    newest_ts_by_team: dict[str, datetime],
    simulate_age: datetime | None,
    driver_heartbeat_ts: datetime | None,
    r2_ok: bool,
    known_good: bool,
    stale_threshold_min: int,
    driver_down_threshold_min: int,
    in_grace: bool,
    kill_switch_off: bool,
) -> dict[str, Any]:
    """Pure staleness computation. Returns the watcher's decision surface.

    Per-team statuses: ``never`` (seam team with no R2 archive), ``stale``
    (newest archive older than threshold — a non-expired simulate object
    carries an OLD age and therefore forces the stale evaluation), ``ok``,
    ``stamp_missing`` (archives exist but no state object). ``unknown`` when
    R2 is unreachable with no last-known-good (fresh boot — honest silence).
    """
    result: dict[str, Any] = {
        "per_team": {},
        "no_teams": False,
        "unknown": False,
        "driver_down": False,
        "backup_set_missing": [],
        "in_grace": in_grace,
    }
    if not r2_ok and not known_good:
        # Fresh boot + R2 down + empty cache — honest silence (NEVER requires
        # a confirmed listing or a prior successful poll).
        result["unknown"] = True
        return result
    if not r2_ok:
        # Degraded from known-good: evaluate from the cached surface.
        r2_teams = list(teams)

    for team in sorted(set(teams + r2_teams)):
        newest = newest_ts_by_team.get(team)
        if team not in r2_teams:
            result["per_team"][team] = "never"
            continue
        if newest is None:
            result["per_team"][team] = "stale"
            continue
        if simulate_age is not None and simulate_age < newest:
            newest = simulate_age  # simulate object wins newest-primary selection
        age_min = (now - newest).total_seconds() / 60.0
        result["per_team"][team] = "stale" if age_min > stale_threshold_min else "ok"
        if team not in state_teams and team in teams:
            result["per_team"][team] = "stamp_missing"

    if not teams and not r2_teams:
        result["no_teams"] = True

    for team in state_teams:
        if team not in r2_teams:
            result["backup_set_missing"].append(team)

    if driver_heartbeat_ts is not None and not kill_switch_off:
        age_min = (now - driver_heartbeat_ts).total_seconds() / 60.0
        result["driver_down"] = age_min > driver_down_threshold_min

    return result


class BackupWatcher:
    """Read-only staleness daemon. ``poll()`` is one check cycle; the lifespan
    spawn (Task 7) calls it every ``interval_seconds``."""

    def __init__(
        self,
        storage,
        alert_store: AlertStore,
        *,
        team_provider: Callable[[], list[str]],
        state_reader: Callable[[str], dict[str, Any]],
        driver_heartbeat_reader: Callable[[], dict[str, Any]],
        graph_provider: Callable[[str], list[str]] | None = None,
        stale_threshold_min: int = 90,
        driver_down_threshold_min: int = 240,
        grace_min: int = 120,
        simulate_enabled: bool = False,
        kill_switch_off: Callable[[], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._alerts = alert_store
        self._teams = team_provider
        self._state_reader = state_reader
        # #2313: per-team CUSTOM-graph seam (team_id -> active sweep-eligible
        # custom graph ids). The DEFAULT graph rides the team-level surface
        # (legacy back-compat). None/empty -> no per-graph surface (the
        # pre-#2313 watcher behavior, byte-for-byte).
        self._graphs_for = graph_provider or (lambda team_id: [])
        self._heartbeat_reader = driver_heartbeat_reader
        self._stale_min = stale_threshold_min
        self._driver_down_min = driver_down_threshold_min
        self._grace_min = grace_min
        self._simulate_enabled = simulate_enabled
        self._kill_switch_off = kill_switch_off or (lambda: False)
        self._now = now or (lambda: datetime.now(timezone.utc))  # noqa: UP017
        self._known_good: bool = False
        self._known_newest: dict[str, datetime] = {}
        self._known_state_teams: list[str] = []
        self._known_graph_newest: dict[str, datetime] = {}
        self._known_graph_state: set[str] = set()
        self._last_graph_keys: set[str] = set()
        self._last_status: dict[str, Any] = {}
        self._rss_baseline: int | None = None
        self._start_time: datetime = self._now()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _simulate_age(self, now: datetime) -> datetime | None:
        """Newest non-expired simulate-stale object (forces stale evaluation)."""
        if not self._simulate_enabled:
            return None
        newest: datetime | None = None
        for k in self._storage.list(SIMULATE_PREFIX):
            state = _read_json(self._storage, k)
            expires = state.get("expires_at")
            age_ts = state.get("age_ts")
            if not expires or not age_ts:
                continue
            try:
                if now >= datetime.fromisoformat(expires):
                    continue  # expired — ignore
                age = datetime.fromisoformat(age_ts)
            except ValueError:
                continue
            if newest is None or age < newest:
                newest = age
        return newest

    def poll(self) -> dict[str, Any]:
        """One check cycle. Returns the computed status. Never raises."""
        now = self._now()
        try:
            return self._poll_inner(now)
        except Exception as e:
            logger.exception("watcher poll failed: %s", e)
            self._last_status = {"poll_error": str(e)}
            return self._last_status

    def _poll_inner(self, now: datetime) -> dict[str, Any]:
        # ── R2 read (the only external read the daemon makes). ──
        try:
            r2_teams = _team_prefixes(self._storage)
            state = _read_json(self._storage, "ops/state.json")
            heartbeat = self._heartbeat_reader()
            driver_ts: datetime | None = None
            try:
                driver_ts = datetime.fromisoformat(heartbeat.get("ran_at", ""))
            except (ValueError, TypeError):
                driver_ts = None
            newest_by_team = {t: _newest_backup_ts(self._storage, t) for t in r2_teams}
            state_teams = [
                k.split("/")[2]
                for k in self._storage.list("ops/teams/")
                if k.endswith("/state.json") and len(k.split("/")) >= 4
            ]
            r2_ok = True
            # Cache the last-known-good surface for degraded polls.
            self._known_newest = newest_by_team
            self._known_state_teams = state_teams
        except Exception as e:
            logger.warning("R2 read failed (r2_ok=false): %s", e)
            r2_teams, state_teams, newest_by_team, driver_ts, r2_ok = [], [], {}, None, False
            if not self._known_good:
                self._last_status = {"unknown": True, "r2_ok": False}
                return self._last_status
            # Degraded from known-good: evaluate from the CACHED surface (review
            # P1-2 — never reclassify from an empty cache; that fabricates stale).
            r2_teams = list(self._last_status.get("per_team", {}).keys())
            newest_by_team = dict(getattr(self, "_known_newest", {}) or {})
            state_teams = list(getattr(self, "_known_state_teams", []) or [])

        teams = self._teams()

        # ── #2313 per-graph surface (custom graphs; the default rides the
        # team surface). Scans R2 per seam graph; on R2 failure falls back to
        # the last-known-good cache (degraded polls keep the custom surface
        # honest — same policy as the team surface). ──
        graph_r2_ok = r2_ok
        try:
            graph_newest: dict[str, datetime] = {}
            graph_state: set[str] = set()
            if graph_r2_ok:
                for t in sorted(set(r2_teams + teams)):
                    for gid in self._graphs_for(t):
                        n = _newest_graph_backup_ts(self._storage, t, gid)
                        if n is not None:
                            graph_newest[f"{t}:{gid}"] = n
                for k in self._storage.list("ops/teams/"):
                    parts = k.split("/")
                    # ops/teams/{team}/graphs/{gid}/state.json → "{team}:{gid}"
                    if (k.endswith("/state.json") and len(parts) == 6
                            and parts[3] == "graphs"):
                        graph_state.add(f"{parts[2]}:{parts[4]}")
                self._known_graph_newest = graph_newest
                self._known_graph_state = graph_state
            else:
                graph_newest = dict(getattr(self, "_known_graph_newest", {}) or {})
                graph_state = set(getattr(self, "_known_graph_state", set()) or set())
        except Exception as e:
            logger.warning("per-graph R2 read failed (using cache): %s", e)
            graph_r2_ok = False  # scan failure = unconfirmed surface (F1)
            graph_newest = dict(getattr(self, "_known_graph_newest", {}) or {})
            graph_state = set(getattr(self, "_known_graph_state", set()) or set())

        try:
            simulate_age = self._simulate_age(now)
        except Exception as e:  # R2 read — fail soft during degraded polls
            logger.warning("simulate read failed: %s", e)
            simulate_age = None
        in_grace = (now - self._start_time).total_seconds() < (self._grace_min * 60)
        status = compute_status(
            now=now,
            teams=teams,
            r2_teams=r2_teams,
            state_teams=state_teams,
            newest_ts_by_team=newest_by_team,
            simulate_age=simulate_age,
            driver_heartbeat_ts=driver_ts,
            r2_ok=r2_ok,
            known_good=self._known_good,
            stale_threshold_min=self._stale_min,
            driver_down_threshold_min=self._driver_down_min,
            in_grace=in_grace,
            kill_switch_off=self._kill_switch_off(),
        )
        self._known_good = r2_ok or self._known_good

        # ── #2313 per-graph status table (custom graphs; the default rides
        # the team surface). Mirrors the per-team table's classes. ──
        per_graph: dict[str, str] = {}
        for t in sorted(set(teams + r2_teams)):
            for gid in self._graphs_for(t):
                key = f"{t}:{gid}"
                newest = graph_newest.get(key)
                if newest is None:
                    # "never" REQUIRES a confirmed listing (module docstring
                    # invariant). On a HEALTHY scan, absence from R2 IS the
                    # confirmed listing → never. On a DEGRADED surface (R2
                    # down OR the graph scan failed), a cache-miss graph is
                    # UNCONFIRMED — never fabricate NEVER_BACKED_UP; classify
                    # stale (same as the team table's cache-miss handling) so
                    # an unseen graph at worst reads as "can't confirm a
                    # fresh archive".
                    per_graph[key] = "never" if graph_r2_ok else "stale"
                    continue
                age_min = (now - newest).total_seconds() / 60.0
                per_graph[key] = "stale" if age_min > self._stale_min else "ok"
                if key not in graph_state and t in teams:
                    per_graph[key] = "stamp_missing"
        # Universe shrink: graphs no longer on the seam surface (deleted /
        # ineligible) resolve their incidents; the known set follows.
        prev_graph_keys = set(getattr(self, "_last_graph_keys", set()))
        cur_graph_keys = set(per_graph)
        for key in prev_graph_keys - cur_graph_keys:
            for kind in ("STALE", "NEVER_BACKED_UP", "METADATA_LOST"):
                self._alerts.resolve_incident(kind, key)
        self._last_graph_keys = cur_graph_keys
        status = dict(status)
        status["per_graph"] = per_graph
        self._last_status = status

        # ── Drive the alert store (no graph writes anywhere here). ──
        if not status.get("unknown") and not status.get("in_grace"):
            for team, state in status["per_team"].items():
                if state == "never":
                    self._alerts.open_incident("NEVER_BACKED_UP", team)
                elif state == "stale":
                    self._alerts.open_incident("STALE", team)
                elif state == "stamp_missing":
                    self._alerts.open_incident("METADATA_LOST", team)
                else:
                    self._alerts.resolve_incident("STALE", team)
                    self._alerts.resolve_incident("NEVER_BACKED_UP", team)
                    self._alerts.resolve_incident("METADATA_LOST", team)
            if status.get("no_teams"):
                # Resolve the per-team incidents of the last-known surface
                # (review P2-4: per-team kinds must close on universe shrink).
                for team in list(self._last_status.get("per_team", {}).keys()):
                    for kind in ("STALE", "NEVER_BACKED_UP", "METADATA_LOST"):
                        self._alerts.resolve_incident(kind, team)
            if status.get("driver_down"):
                self._alerts.open_incident("DRIVER_DOWN")
            else:
                self._alerts.resolve_incident("DRIVER_DOWN")
            for key, state in status.get("per_graph", {}).items():
                if state == "never":
                    self._alerts.open_incident("NEVER_BACKED_UP", key)
                elif state == "stale":
                    self._alerts.open_incident("STALE", key)
                elif state == "stamp_missing":
                    self._alerts.open_incident("METADATA_LOST", key)
                else:
                    self._alerts.resolve_incident("STALE", key)
                    self._alerts.resolve_incident("NEVER_BACKED_UP", key)
                    self._alerts.resolve_incident("METADATA_LOST", key)
            for team in status.get("backup_set_missing", []):
                self._alerts.open_incident("BACKUP_SET_MISSING", team)
            # BACKUP_SET_MISSING resolves when the team's archives reappear.
            for team in status.get("per_team", {}):
                if team not in status.get("backup_set_missing", []):
                    self._alerts.resolve_incident("BACKUP_SET_MISSING", team)
            # R2_DOWN: emit while degraded-from-known-good, resolve when healthy.
            if r2_ok is False:
                self._alerts.open_incident("R2_DOWN")
            else:
                self._alerts.resolve_incident("R2_DOWN")

        # ── Heartbeat + pending-push retries (R2 writes — safe to skip when down). ──
        try:
            self._storage.upload(
                HEARTBEAT_KEY,
                json.dumps(
                    {
                        "last_poll_at": now.isoformat(),
                        "r2_ok": r2_ok,
                        "status": status.get("per_team", {}),
                    }
                ).encode(),
                content_type="application/json",
            )
        except Exception as e:
            logger.warning("heartbeat write failed: %s", e)
        try:
            self._alerts.retry_pending()
        except Exception as e:
            logger.warning("pending-push retry failed: %s", e)

        self._check_memory()
        return status

    def _check_memory(self) -> None:
        """Process-RSS trend guard: if RSS grows beyond 50 MB over baseline,
        log loudly (the daemon restart policy is handled by the watchdog)."""
        try:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if self._rss_baseline is None:
                self._rss_baseline = rss
            elif rss - self._rss_baseline > 50_000:  # KB
                logger.error(
                    "watcher RSS growth %.1f MB above baseline — investigate",
                    (rss - self._rss_baseline) / 1024.0,
                )
        except Exception:
            pass


class WatcherThread:
    """Daemon thread + watchdog: restarts the poll loop if it exits, so a
    crashed poll loop can never silently kill the driver-disabled leg."""

    def __init__(self, watcher: BackupWatcher, interval_seconds: int = 600) -> None:
        self._watcher = watcher
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="backup-watcher", daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name="backup-watcher-watchdog", daemon=True)
        self._thread.start()
        self._watchdog.start()

    def _loop(self) -> None:
        # Initial 60 s delay so the app boots before the first evaluation.
        self._stop.wait(60)
        while not self._stop.is_set():
            self._watcher.poll()
            self._stop.wait(self._interval)

    def _watch(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(30)
            if self._thread and not self._thread.is_alive() and not self._stop.is_set():
                logger.warning("watcher thread exited — restarting")
                self._thread = threading.Thread(target=self._loop, name="backup-watcher", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop.set()
