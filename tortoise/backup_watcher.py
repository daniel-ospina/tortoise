"""Backup watcher — the driver-disabled leg of the dual-watcher design.

A read-only, in-process daemon (spawned in the app lifespan, Task 7) that
computes PER-ORG backup staleness and drives the alert store directly (files
GitHub issues + pushes Telegram itself — it does not depend on any R2 marker
being read by someone else, so the driver-disabled case is covered by
construction).

Read-only w.r.t. the graphs: the watcher takes an injected org provider
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
from collections.abc import Mapping, MappingView
from datetime import datetime, timezone
from typing import Any, Callable  # noqa: UP035

from .alert_store import AlertStore, CloseCooldown

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "ops/watcher-heartbeat.json"
SIMULATE_PREFIX = "ops/simulate/"


def _read_json(storage, key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(storage.download(key))
        return parsed if isinstance(parsed, dict) else {}
    except (KeyError, ValueError):
        return {}


def _org_prefixes(storage) -> list[str]:
    """Top-level org directories under ``backups/`` — app-down-independent."""
    orgs: set[str] = set()
    for k in storage.list("backups/"):
        parts = k.split("/")
        if len(parts) >= 2 and parts[0] == "backups" and parts[1]:
            orgs.add(parts[1])
    return sorted(orgs)


def _parse_backup_ts(token: str) -> datetime | None:
    """Parse a manifest-key timestamp token (``{ts}_{rnd}``) into UTC."""
    ts = token.split("_", 1)[0]  # strip the {rnd} suffix (review P1-1)
    for fmt in ("%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)  # noqa: UP017
        except ValueError:
            continue
    return None


def _default_graph_name(storage, org_id: str) -> str | None:
    """The DEFAULT graph's dump name, from its per-graph state if present.

    Post-#2313 the per-graph sweep writes graph_name into each graph's state
    (ops/teams/{t}/graphs/{gid}/state.json). Legacy flat manifests carry the
    graph they dumped — pre-#2313 C5-era graph-bound ON-DEMAND dumps of
    CUSTOM graphs also wrote flat keys with the custom namespace as
    graph_name. Reading the default's expected name lets the flat-freshness
    scan exclude those custom-era artifacts.
    """
    try:
        state = json.loads(storage.download(
            f"ops/teams/{org_id}/graphs/default/state.json"))
    except Exception:
        return None
    name = state.get("graph_name") if isinstance(state, dict) else None
    return str(name) if name else None


def _default_graph_newest(storage, org_id: str, newest: datetime | None) -> datetime | None:
    """Merge the DEFAULT graph's archive freshness into ``newest``.

    Post-#2313 the default graph's dumps live under the literal ``default``
    key segment (``backups/{org}/default/{ts}_{rnd}/…``); pre-#2313 sweep
    dumps are flat (``backups/{org}/{ts}_{rnd}/…``). Both shapes are the
    default graph — org-level freshness is the max over the two (#2313 Task
    4). Custom nested keys (``backups/{org}/{g_..}/…``) are NOT org-level;
    the per-graph surface handles them.

    Legacy flat manifests are disambiguated by their manifest ``graph_name``
    when the default's expected name is known (post-#2313 state): a flat
    manifest naming a CUSTOM namespace is a pre-#2313 C5-era on-demand dump
    — it is NOT the default graph and does not gate org freshness. Before
    any per-graph state exists (first post-#2313 sweep not yet run) every
    flat manifest is treated as the default (pre-#2313 parity, ≤1h window).

    #2370: classification is read from the sweep-written legacy-flat index
    (ops/legacy-flat-index/{org}.json) — ONE object read per org per poll
    replaces downloading every flat manifest. The index is authoritative
    while present: flats it marks custom (graph_id set, ≠ default) never
    gate org freshness; default/unresolvable flats count. Index absent
    (pre-first-sweep) falls back to the per-manifest read. Transient read
    failures NEVER exclude an archive: an unreadable manifest/index counts
    as default for the cycle (pre-#2313 key-derived parity) — excluding it
    fabricated spurious STALE on a one-off read error.
    """
    default_name = _default_graph_name(storage, org_id)
    from tortoise.backup_sweep import read_legacy_flat_index
    try:
        index = read_legacy_flat_index(storage, org_id)
    except Exception:
        index = {}  # index read failure → per-manifest fallback below
    for k in storage.list(f"backups/{org_id}/"):
        if not k.endswith("/manifest.json"):
            continue
        parts = k.split("/")
        if len(parts) == 4:
            # legacy flat — default unless classified custom
            meta = index.get(f"{parts[1]}/{parts[2]}")
            if index and meta is not None:
                gid = str(meta.get("graph_id") or "")
                if gid and gid != "default":
                    continue  # C5-era custom on-demand artifact (index)
            elif default_name is not None:
                # Pre-index fallback: per-manifest read (round-1
                # disambiguation). An unreadable manifest is counted as the
                # default — NEVER excluded (#2370: exclusion fabricated
                # spurious STALE on a transient read failure; a manifest
                # that lists exists, and counting it is the pre-#2313
                # key-derived parity).
                try:
                    m = json.loads(storage.download(k))
                    if isinstance(m, dict) and m.get("graph_name") != default_name:
                        continue  # C5-era custom on-demand artifact
                except Exception:
                    pass  # unreadable → count as default this cycle
            parsed = _parse_backup_ts(parts[2])
        elif len(parts) == 5 and parts[2] == "default":
            parsed = _parse_backup_ts(parts[3])  # default nested
        else:
            continue  # custom nested — per-graph surface
        if parsed is not None and (newest is None or parsed > newest):
            newest = parsed
    return newest


def _newest_backup_ts(storage, org_id: str) -> datetime | None:
    """Newest archive timestamp for an org's DEFAULT graph from R2 manifest
    keys (key-derived, restore-surviving) — legacy flat + ``default`` nested."""
    return _default_graph_newest(storage, org_id, None)


def _newest_graph_backup_ts(storage, org_id: str, graph_id: str) -> datetime | None:
    """Newest archive timestamp for ONE graph (#2313 Task 4)."""
    newest: datetime | None = None
    for k in storage.list(f"backups/{org_id}/{graph_id}/"):
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
    orgs: list[str],
    r2_orgs: list[str],
    state_orgs: list[str],
    newest_ts_by_org: dict[str, datetime],
    simulate_age: datetime | None,
    driver_heartbeat_ts: datetime | None,
    r2_ok: bool,
    known_good: bool,
    stale_threshold_min: int,
    driver_down_threshold_min: int,
    in_grace: bool,
    kill_switch_off: bool,
    eligible_orgs: set[str] | None = None,
) -> dict[str, Any]:
    """Pure staleness computation. Returns the watcher's decision surface.

    Per-org statuses: ``never`` (seam org with no R2 archive), ``stale``
    (newest archive older than threshold — a non-expired simulate object
    carries an OLD age and therefore forces the stale evaluation), ``ok``,
    ``stamp_missing`` (archives exist but no state object), ``not_eligible``
    (org the sweep will not back up — #3658). ``unknown`` when R2 is
    unreachable with no last-known-good (fresh boot — honest silence).

    ``eligible_orgs`` is the sweep's eligibility set
    (``backup_sweep.enumerate_eligible_orgs``: ``tier != 'free' AND
    backup_enabled``). When it is provided, an org outside it is
    ``not_eligible`` — not ``never``. ``never`` is a GAP (a backup was owed
    and never happened) and only means that for an org the sweep targets;
    outside the eligible set the two states are the same string and a
    genuine missing backup is un-triageable (#3658). ``None`` means the
    gate is off (legacy all-org sweep, or an unconfirmed read) — the
    pre-#3658 behaviour, byte-for-byte.
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
        r2_orgs = list(orgs)

    for org in sorted(set(orgs + r2_orgs)):
        if eligible_orgs is not None and org not in eligible_orgs:
            # #3658: the sweep never archives a non-eligible org, so it can
            # never be a "never backed up" GAP. Classify it separately and
            # open no DR incident for it — otherwise `never` (and the whole
            # NEVER_BACKED_UP census) is guaranteed by construction for the
            # free-tier / backup_enabled=false tail, which both floods
            # incidents and hides a genuine eligible-and-never-backed-up org.
            result["per_team"][org] = "not_eligible"
            continue
        newest = newest_ts_by_org.get(org)
        if org not in r2_orgs:
            result["per_team"][org] = "never"
            continue
        if newest is None:
            result["per_team"][org] = "stale"
            continue
        if simulate_age is not None and simulate_age < newest:
            newest = simulate_age  # simulate object wins newest-primary selection
        age_min = (now - newest).total_seconds() / 60.0
        result["per_team"][org] = "stale" if age_min > stale_threshold_min else "ok"
        if org not in state_orgs and org in orgs:
            result["per_team"][org] = "stamp_missing"

    if not orgs and not r2_orgs:
        result["no_teams"] = True

    for org in state_orgs:
        if org not in r2_orgs:
            if eligible_orgs is not None and org not in eligible_orgs:
                continue  # #3658: no archive is owed → not a missing set
            result["backup_set_missing"].append(org)

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
        org_provider: Callable[[], list[str] | None],
        state_reader: Callable[[str], dict[str, Any]],
        driver_heartbeat_reader: Callable[[], dict[str, Any]],
        graph_provider: Callable[[str], list[str] | None] | None = None,
        eligible_provider: Callable[[], list[str] | None] | None = None,
        stale_threshold_min: int = 90,
        driver_down_threshold_min: int = 240,
        grace_min: int = 120,
        simulate_enabled: bool = False,
        kill_switch_off: Callable[[], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._alerts = alert_store
        self._orgs = org_provider
        self._state_reader = state_reader
        # #2313: per-org CUSTOM-graph seam (org_id -> active sweep-eligible
        # custom graph ids, or None when the control plane could not be read —
        # an UNCONFIRMED surface). The DEFAULT graph rides the org-level
        # surface (legacy back-compat). None/empty -> no per-graph surface
        # (the pre-#2313 watcher behavior, byte-for-byte).
        self._graphs_for = graph_provider or (lambda org_id: [])
        # #3658: the sweep's eligibility seam (eligible org ids, or None when
        # the control plane could not be read). None/absent -> no gate (the
        # pre-#3658 watcher behavior, byte-for-byte).
        self._eligible = eligible_provider
        self._heartbeat_reader = driver_heartbeat_reader
        self._stale_min = stale_threshold_min
        self._driver_down_min = driver_down_threshold_min
        self._grace_min = grace_min
        self._simulate_enabled = simulate_enabled
        self._kill_switch_off = kill_switch_off or (lambda: False)
        self._now = now or (lambda: datetime.now(timezone.utc))  # noqa: UP017
        self._known_good: bool = False
        self._known_newest: dict[str, datetime] = {}
        self._known_state_orgs: list[str] = []
        self._known_graph_newest: dict[str, datetime] = {}
        self._known_graph_state: set[str] = set()
        self._last_graph_keys: set[str] = set()
        # #3658: last CONFIRMED eligibility set — degraded evaluation uses it
        # when a control-plane blip leaves eligibility unconfirmed (mirror of
        # the R2 last-known-good cache).
        self._known_eligible: set[str] | None = None
        # True while a poll evaluates eligibility from the last-known set
        # because the current read was unconfirmed — exposed on the status +
        # heartbeat so a stale-cache classification is OBSERVABLE and not
        # mistaken for a confirmed one (#3658 review).
        self._eligible_degraded: bool = False
        # Subjects of the previous BACKUP_SET_MISSING census, so a subject that
        # leaves the set resolves — the per_team scan alone cannot close a
        # state-only org (never present in per_team).
        self._last_backup_set_missing: set[str] = set()
        # #3658 review (cycle 7): the universe-shrink reference, re-baselined
        # ONLY on a CONFIRMED poll — the mirror of `_last_graph_keys`. A
        # degraded poll's `per_team` is the census (see `compute_status`), so
        # persisting it would forget an org that left the census, and once the
        # poll recovered the shrink set would no longer contain that org —
        # stranding its incidents open forever.
        self._last_confirmed_per_team: set[str] = set()
        # #3658 review (cycle 8): the eligibility CREDIBILITY reference — the
        # last CONFIRMED PURE CENSUS. Not `per_team` (which is `census ∪
        # r2_orgs`): on an unconfirmed census the fallback `orgs` IS the previous
        # `per_team`, so reusing it would let an R2-only id (a departed org whose
        # prefix outlives it) credential a foreign eligibility read — silencing
        # the whole census and poisoning `_known_eligible`, which never clears
        # by itself.
        self._last_confirmed_census: set[str] = set()
        self._last_status: dict[str, Any] = {}
        self._rss_baseline: int | None = None
        self._start_time: datetime = self._now()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _eligible_set(self, census_orgs: set[str]) -> set[str] | None:
        """The sweep's eligibility set, or None when the gate must be OFF.

        ``census_orgs`` is the org census this poll watches. It is the
        CREDIBILITY reference: a non-empty eligibility set that shares no id
        with it is not usable evidence and is treated as unconfirmed (see the
        check below). Deliberately the CENSUS and not the union with the R2
        listing — a ``backups/<id>/`` prefix outlives a departed or foreign
        org until lifecycle purge, so an R2-only id must not be able to make an
        otherwise-foreign eligibility read look healthy.

        ``None`` means "every org is a backup target": no provider is wired
        (the legacy all-org sweep), or eligibility is unconfirmed AND there is
        no last-known-good set. The gate is deliberately fail-OPEN toward
        ALERTING — an unconfirmed eligibility read must never silence a
        genuine NEVER_BACKED_UP, and the census provider already degrades to
        ``[]`` (no alerts) on its own failure.

        An unconfirmed read (a raise, a non-id-set value, the provider
        returning ``None``, or an EMPTY result) does NOT re-open the whole
        non-eligible census: it falls back to the last CONFIRMED set, exactly
        as a degraded R2 poll evaluates from the last-known-good cache, and
        reports ``eligible_degraded``. An empty result is treated as
        unconfirmed rather than as "no org is eligible" because the two are
        indistinguishable here and the confirmed reading would resolve the
        entire DR surface while claiming eligibility was healthy. Only a
        first-contact failure — no confirmed set yet — leaves the gate off
        (pre-#3658 parity, the fail-open-toward-alerting direction); that case
        is reported by ``eligible_degraded`` as well.

        Known residual (bounded, documented in `docs/ops/registry-backup-dr.md`):
        while eligibility reads keep failing, an org that BECOMES eligible
        after the last confirmed read is classified ``not_eligible`` and its
        NEVER_BACKED_UP is withheld until a read succeeds — the mirror of the
        fail-open first-contact case. ``eligible_degraded`` surfaces it.
        """
        if self._eligible is None:
            self._eligible_degraded = False
            return None
        try:
            value = self._eligible()
            if value is None:
                resolved = None
            else:
                # str/bytes and Mapping are iterable in ways that yield
                # NON-ids (characters; keys). So is a mapping VIEW —
                # `dict.keys()` is a MappingView, NOT a Mapping, and would slip
                # past a Mapping-only check. Reject them explicitly rather than
                # leaving it to the credibility check below, so the failure is
                # named for what it is.
                if isinstance(value, (str, bytes, Mapping, MappingView)):
                    raise TypeError(
                        f"eligibility provider returned {type(value).__name__}, not a str id set")
                # Materialise ONCE: validating a one-shot iterator would
                # exhaust it and cache an EMPTY confirmed set.
                seq = list(value)
                if not all(isinstance(v, str) for v in seq):
                    raise TypeError("eligibility provider returned non-str ids")
                resolved = set(seq)
                # CREDIBILITY, not shape (review cycles 3-5). The harmful
                # reading of an eligibility set is a NON-EMPTY set that names
                # no org we actually watch: every census org then reads
                # `not_eligible` and the whole DR surface is resolved — total
                # alerting silence, the one outcome this gate must never
                # produce. No shape rule closes that class: `array('u',
                # "team_a")`, `iter("team_a")`, a generator over a string, a
                # Mapping's keys, a padded id and a foreign id ALL form a
                # plausible-looking set while matching nothing. Requiring the
                # set to overlap the CENSUS does close it, and it is the honest
                # test — a set naming no org we are actually tracking is not
                # usable evidence. (The census, not `census ∪ r2_orgs`: an R2
                # prefix outlives a departed org, so an R2-only id must not be
                # able to make a foreign read look healthy.) It degrades like
                # any other unconfirmed read: last-known-good if there is one,
                # else the gate stays OFF (fail open toward ALERTING). The cost
                # is the mirror case — an all-free deployment, or a census read
                # that misses the eligible orgs, keeps the gate off and
                # re-alerts the non-eligible tail until a usable read arrives,
                # which is the SAFE direction.
                if not resolved or not (resolved & census_orgs):
                    raise ValueError(
                        "eligibility set is empty or names no known org "
                        f"({len(resolved)} id(s), 0 matching the census)")
        except Exception as exc:  # never let the gate kill a poll
            logger.warning("eligibility enumeration failed (using last-known-good if any): %s", exc)
            resolved = None
        if resolved is None:
            # `eligible_degraded` means THIS poll's eligibility is not
            # confirmed — set it for a first-contact failure too, not only for
            # a fallback: otherwise an operator cannot tell "no provider
            # wired" from "the eligibility read is failing", and the second
            # is the one that needs attention.
            self._eligible_degraded = True
            cached = self._known_eligible
            # The CACHED set must also stay credible against the CURRENT census
            # (review cycle 10): after a census turnover a stale cached set
            # names none of the orgs we now watch, and returning it as the gate
            # would `not_eligible` every one of them — the total-silence class
            # the credibility check above exists to close. Fail OPEN instead
            # (gate off ⇒ over-alerting).
            if cached and not (cached & census_orgs):
                return None
            return cached
        self._known_eligible = resolved
        self._eligible_degraded = False
        return resolved

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
            r2_orgs = _org_prefixes(self._storage)
            # (The `ops/state.json` read that used to sit here was dead — the
            # name was never read, and only escaped ruff because the alert
            # loops below rebound it. #3031 moved those loops into
            # `_drive_alerts`, which is what exposed it.)
            heartbeat = self._heartbeat_reader()
            driver_ts: datetime | None = None
            try:
                driver_ts = datetime.fromisoformat(heartbeat.get("ran_at", ""))
            except (ValueError, TypeError):
                driver_ts = None
            newest_by_org = {t: _newest_backup_ts(self._storage, t) for t in r2_orgs}
            state_orgs = [
                k.split("/")[2]
                for k in self._storage.list("ops/teams/")
                # ONLY the 4-segment org mirror (ops/teams/{org}/state.json)
                # is the org surface. The #2313 per-graph states
                # (ops/teams/{org}/graphs/{gid}/state.json — 6 segments) ride
                # the per-graph surface and must not suppress the default
                # graph's METADATA_LOST when its mirror is missing (#2367).
                if k.endswith("/state.json") and len(k.split("/")) == 4
            ]
            r2_ok = True
            # Cache the last-known-good surface for degraded polls.
            self._known_newest = newest_by_org
            self._known_state_orgs = state_orgs
        except Exception as e:
            logger.warning("R2 read failed (r2_ok=false): %s", e)
            r2_orgs, state_orgs, newest_by_org, driver_ts, r2_ok = [], [], {}, None, False
            if not self._known_good:
                self._last_status = {"unknown": True, "r2_ok": False}
                return self._last_status
            # Degraded from known-good: evaluate from the CACHED surface (review
            # P1-2 — never reclassify from an empty cache; that fabricates stale).
            r2_orgs = list(self._last_status.get("per_team", {}).keys())
            newest_by_org = dict(getattr(self, "_known_newest", {}) or {})
            state_orgs = list(getattr(self, "_known_state_orgs", []) or [])

        # ── org census (the per-team denominator) ──
        # A None/raise is an UNCONFIRMED census, NOT an empty universe: the
        # control plane can fail while R2 stays healthy, and a fabricated-empty
        # census would resolve real incidents (the per-graph surface guards
        # this with `graph_surface_confirmed`; the org surface needs the same).
        # Evaluate from the last-known surface instead, exactly like a
        # degraded R2 poll.
        try:
            raw_orgs = self._orgs()
        except Exception as exc:  # never let a census failure kill the poll
            logger.warning("team enumeration failed (using last-known census): %s", exc)
            raw_orgs = None
        census_confirmed = raw_orgs is not None
        if raw_orgs is None:
            orgs = list(self._last_status.get("per_team", {}).keys())
        else:
            orgs = list(raw_orgs)
            # Derived from the MATERIALISED list the classifier uses, not from
            # `raw_orgs`: `str()`-coercing a separate view of it made the
            # reference and `compute_status` disagree (a stringified reference
            # credentialed an eligibility read while every raw id still read
            # `not_eligible` — silence reported as healthy), and re-iterating a
            # one-shot iterator left the reference EMPTY, permanently rejecting
            # every later correct read (#3658 review, cycle 9).
            self._last_confirmed_census = set(orgs)
        eligible_orgs = self._eligible_set(self._last_confirmed_census)

        # ── #2313 per-graph surface (custom graphs; the default rides the
        # org surface). Scans R2 per seam graph; on R2 failure falls back to
        # the last-known-good cache (degraded polls keep the custom surface
        # honest — same policy as the org surface). ──
        graph_r2_ok = r2_ok
        graph_surface_confirmed = graph_r2_ok
        # The enumeration used for the STATUS table is cached from the surface
        # scan (cycle-2 review P1): the two loops used to call `_graphs_for(t)`
        # independently, so a control-plane failure on only the SECOND read left
        # `graph_surface_confirmed` True while `per_graph` was missing that team —
        # a live graph then looked VANISHED, its incidents were closed, and it was
        # re-opened on the next poll (fabricated false recovery + ✅/🚨 flap).
        # `per_graph` is therefore built from THIS cached read, so the resolved
        # surface is exactly the surface the gate judged. The second read is not
        # gone, though: it is restored in the build loop as a CONFIRMATION read
        # that gates the shrink DECISION only (#3405 review P2), so one silently
        # TRUNCATED enumeration cannot resolve a live graph's incidents — the
        # confirmation read disagrees with the cache and clears
        # `graph_surface_confirmed`, while `per_graph` itself stays on the CACHED
        # list (an inconsistent read never drops keys either).
        gids_by_team: dict[str, list[str] | None] = {}
        try:
            graph_newest: dict[str, datetime] = {}
            graph_state: set[str] = set()
            if graph_r2_ok:
                for t in sorted(set(r2_orgs + orgs)):
                    if eligible_orgs is not None and t not in eligible_orgs:
                        # #3658: the sweep only archives ELIGIBLE orgs, so a
                        # non-eligible org's custom graphs are not
                        # un-backed-up gaps either. Skip them on this surface
                        # too (their keys fall out of per_graph → resolved by
                        # the universe-shrink below).
                        continue
                    gids = self._graphs_for(t)
                    gids_by_team[t] = gids
                    if gids is None:
                        # Control-plane read failed — the custom surface is
                        # UNCONFIRMED this poll. Never open or resolve custom
                        # incidents off a fabricated-empty surface (mirror of
                        # the never-requires-confirmed-listing invariant; a
                        # CP blip at sweep time is exactly when customs age).
                        graph_surface_confirmed = False
                        continue
                    for gid in gids:
                        n = _newest_graph_backup_ts(self._storage, t, gid)
                        if n is not None:
                            graph_newest[f"{t}:{gid}"] = n
                for k in self._storage.list("ops/teams/"):
                    parts = k.split("/")
                    # ops/teams/{org}/graphs/{gid}/state.json → "{org}:{gid}"
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
            graph_surface_confirmed = False
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
            orgs=orgs,
            r2_orgs=r2_orgs,
            state_orgs=state_orgs,
            newest_ts_by_org=newest_by_org,
            simulate_age=simulate_age,
            driver_heartbeat_ts=driver_ts,
            r2_ok=r2_ok,
            known_good=self._known_good,
            stale_threshold_min=self._stale_min,
            driver_down_threshold_min=self._driver_down_min,
            in_grace=in_grace,
            kill_switch_off=self._kill_switch_off(),
            eligible_orgs=eligible_orgs,
        )
        self._known_good = r2_ok or self._known_good

        # ── #2313 per-graph status table (custom graphs; the default rides
        # the org surface). Mirrors the per-org table's classes. ──
        per_graph: dict[str, str] = {}
        for t in sorted(set(r2_orgs + orgs)):
            if eligible_orgs is not None and t not in eligible_orgs:
                continue  # #3658: no backup is owed to a non-eligible org
            # Healthy poll → the cached enumeration from the surface scan (see the
            # gids_by_team comment: a second read could disagree with the gate).
            # Degraded poll → read now, and never classify "never" off it below.
            gids = gids_by_team.get(t) if graph_r2_ok else self._graphs_for(t)
            if gids is None:
                # Control-plane read failed — the custom surface is
                # UNCONFIRMED for this org: never open/resolve custom
                # incidents off a fabricated-empty surface, and never crash
                # the poll (the org-level default surface is the never-
                # silent core and must keep evaluating). This arm must ALSO
                # clear the confirmed flag: the scan above is a SEPARATE
                # `_graphs_for` call, so a provider that answered there and
                # not here would otherwise let this org's keys drop out of
                # per_graph and be resolved by the universe-shrink below — a
                # fabricated recovery off an unconfirmed surface.
                graph_surface_confirmed = False
                continue
            if graph_r2_ok:
                # CONFIRMATION read — main's second read, restored for the SHRINK
                # DECISION only (#3405 review P2). The cache above is the single
                # source for `per_graph`, but it also removed the redundancy that
                # caught a single silently-TRUNCATED enumeration (the documented
                # PostgREST `db-max-rows` fail-open class): a short scan read would
                # drop a live graph's key out of `per_graph`, and the
                # universe-shrink would then resolve its still-active incidents —
                # a fabricated false recovery, re-opened when the full read returns
                # (✅/🚨 flap). So re-read here and clear the flag when the re-read
                # is UNCONFIRMED (None) or DISAGREES with the cached set. `per_graph`
                # below still uses the CACHED list, so an inconsistent read can
                # never drop keys: the worst case is a one-poll DEFERRAL of a
                # legitimate vanish — a deliberate trade, because a deferred
                # resolve is honest while a fabricated one pages an operator about
                # a recovery that did not happen.
                confirm = self._graphs_for(t)
                if confirm is None or set(confirm) != set(gids):
                    graph_surface_confirmed = False
            for gid in gids:
                key = f"{t}:{gid}"
                newest = graph_newest.get(key)
                if newest is None:
                    # "never" REQUIRES a confirmed listing (module docstring
                    # invariant). On a HEALTHY scan, absence from R2 IS the
                    # confirmed listing → never. On a DEGRADED surface (R2
                    # down OR the graph scan failed), a cache-miss graph is
                    # UNCONFIRMED — never fabricate NEVER_BACKED_UP; classify
                    # stale (same as the org table's cache-miss handling) so
                    # an unseen graph at worst reads as "can't confirm a
                    # fresh archive".
                    if not graph_r2_ok:
                        per_graph[key] = "stale"
                    elif key in graph_state:
                        # #2374: per-graph state EXISTS but no archives —
                        # mirror the org surface's backup-set-missing class
                        # (archives lost/pruned), NOT "never" (never backed
                        # up is impossible once state exists: state is
                        # written only after a successful dump). Bulk-deleted
                        # archives must triage as a missing set, not as a
                        # graph that never produced a backup.
                        per_graph[key] = "backup_set_missing"
                    else:
                        per_graph[key] = "never"
                    continue
                age_min = (now - newest).total_seconds() / 60.0
                per_graph[key] = "stale" if age_min > self._stale_min else "ok"
                if key not in graph_state and t in orgs:
                    per_graph[key] = "stamp_missing"
        # Universe shrink: graphs no longer on the seam surface (deleted /
        # ineligible) resolve their incidents — but ONLY on a CONFIRMED surface.
        # A degraded R2 or a failed control-plane read must never resolve real
        # incidents (a CP blip at sweep time is exactly when customs age into
        # staleness; delete-to-resolve would close the issue and re-file a fresh
        # one on recovery — fabricated false recovery).
        # #3031 (review): this was ON the poll's critical path OUTSIDE any
        # containment — a raise here escaped before the heartbeat and fabricated
        # the very WATCHER_DOWN this change removes. Same gate, contained.
        if graph_surface_confirmed:
            self._resolve_vanished_graphs(per_graph)
        prev_per_team = set(self._last_confirmed_per_team)
        status = dict(status)
        status["per_graph"] = per_graph
        status["eligible_degraded"] = self._eligible_degraded
        self._last_status = status

        # ── Drive the alert store (no graph writes anywhere here). ──
        # Hoisted out of the grace/unknown gate: the shrink reference below is
        # re-baselined even while in grace (review cycle 10).
        r2_confirmed = r2_ok is True
        if not status.get("unknown") and not status.get("in_grace"):
            # #3031: the (fail-soft) alert drive, extraction of the inline loops
            # below. #3658's gating is carried in: `census_confirmed` gates the
            # org universe-shrink and `r2_ok` gates every archive-derived resolve.
            self._drive_alerts(
                status, r2_ok,
                prev_per_team=prev_per_team,
                census_confirmed=census_confirmed,
            )

        # Re-baseline the universe-shrink reference ONLY on a confirmed poll (the
        # `_last_graph_keys` convention) — and OUTSIDE the grace/unknown gate.
        # Inside it, the reference would stay EMPTY for the whole grace window
        # (120 min by default, restarted on every process boot), so an org that
        # left the census across the grace boundary would never be shrunk out
        # and its incidents would strand open forever — a regression against
        # HEAD, whose `_last_status` was assigned before that gate (review
        # cycle 10).
        if census_confirmed and r2_confirmed:
            self._last_confirmed_per_team = set(status["per_team"])

        # ── Heartbeat + pending-push retries (R2 writes — safe to skip when down). ──
        try:
            self._storage.upload(
                HEARTBEAT_KEY,
                json.dumps(
                    {
                        "last_poll_at": now.isoformat(),
                        "r2_ok": r2_ok,
                        "eligible_degraded": self._eligible_degraded,
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

    def _resolve_vanished_graphs(self, per_graph: dict[str, Any]) -> None:
        """Close the incidents of graphs no longer on the seam surface.

        Only ever called on a CONFIRMED surface (a degraded R2 or a failed
        control-plane read must never resolve real incidents — a CP blip at sweep
        time is exactly when customs age into staleness, and delete-to-resolve
        would close the issue and re-file a fresh one on recovery: fabricated
        false recovery).

        #3031 (review): contained like every other alert leg. The extraction also
        # fixed the retry semantics for the new raise-on-close-failure contract
        # (cycle-2 review P1): `resolve_incident` RAISES when the close fails (a
        # `False` return only ever means "nothing was open"), so a key whose close
        # failed is kept in `_last_graph_keys` and the `prev - cur` diff still
        # contains it next poll. Retiring it would have documented a retry that
        # never happened and orphaned the incident forever — the graph is gone, so
        # no other surface ever revisits it.
        """
        pending: set[str] = set()
        try:
            prev_graph_keys = set(getattr(self, "_last_graph_keys", set()))
            cur_graph_keys = set(per_graph)
            for key in sorted(prev_graph_keys - cur_graph_keys):
                for kind in ("STALE", "NEVER_BACKED_UP", "METADATA_LOST",
                             "BACKUP_SET_MISSING"):
                    try:
                        self._alerts.resolve_incident(kind, key)
                    except Exception:
                        logger.exception(
                            "vanished-graph resolve failed for %s/%s — kept "
                            "pending for the next poll", kind, key,
                        )
                        pending.add(key)
            # A pending key is no longer in `cur`, so keeping it in the set makes
            # the next poll's diff re-include it.
            self._last_graph_keys = cur_graph_keys | pending
        except Exception:
            logger.exception(
                "vanished-graph resolution failed — heartbeat unaffected "
                "(retried next poll)"
            )

    def _alert_leg(self, op: str, kind: str, subject: str = "") -> bool:
        """One alert-store leg, contained AND attributed (#3031 cycle-2 review P2).

        Contained per leg rather than per block, for two reasons:
        * attribution — `logger.exception` prints a traceback into alert_store
          internals but no locals, so a block-level log left the operator unable
          to tell WHICH team/graph/incident leg died;
        * isolation — a single broken incident (or a GitHub outage on one close)
          no longer starves every remaining leg until the next poll. Retry is
          unchanged: the next poll re-evaluates every leg, and the lifecycle is
          dedup-backed and idempotent.

        Returns True when the leg SUCCEEDED. Callers that iterate a shrunken
        universe need that signal to keep a failed subject pending instead of
        retiring it (a swallowed failure + a retired subject is a permanent
        orphan — the defect the graph path had and the team path shared).
        `CloseCooldown` is logged at INFO without a traceback: it means "still
        open, backing off" rather than "something broke".

        The heartbeat written just after this block is the watcher's own liveness
        evidence, so no leg may ever escape into `poll()` — an escaped exception
        is what fabricated the false WATCHER_DOWN this change removes.
        """
        if op not in ("open_incident", "resolve_incident"):
            # A typo'd op would raise AttributeError into the catch-all below and
            # silently disable EVERY leg while the heartbeat stayed healthy
            # (final-cycle review P2). Fail loudly instead — the bot/daemon
            # watchdogs surface a crash, a green-but-deaf alerter does not.
            raise ValueError(f"unknown alert leg op: {op!r}")
        try:
            getattr(self._alerts, op)(kind, subject)
            return True
        except CloseCooldown as cool:
            logger.info(
                "alert leg cooling down: %s(%s, %r) — %s", op, kind, subject, cool,
            )
            return False
        except Exception:
            logger.exception(
                "alert leg failed: %s(%s, %r) — heartbeat unaffected, "
                "re-evaluated next poll", op, kind, subject,
            )
            return False

    def _drive_alerts(self, status: dict[str, Any], r2_ok: bool | None,
                      *, prev_per_team: set[str] | None = None,
                      census_confirmed: bool = False) -> None:
        """#3031: drive the alert store — fail-soft, never at the heartbeat's expense.

        The heartbeat written right after this call is the watcher's OWN
        liveness evidence: the DR driver files WATCHER_DOWN when it goes stale.
        The alerts share the same R2 store, so the degraded condition that
        R2_DOWN exists to report is exactly the one that makes
        ``open_incident``/``resolve_incident`` raise (``_read_json`` re-raises a
        read ``RuntimeError``; ``create_if_not_exists`` re-raises anything that
        is neither 412 nor an accepted fallback). Uncontained, that exception
        escaped ``_poll_inner``, was swallowed by ``poll()``, and skipped the
        heartbeat — so a broken alert path fabricated a *different*, false
        incident (WATCHER_DOWN) and masked the real R2 fault.

        The block is best-effort AND per-leg contained: a leg that fails is logged
        with its (op, kind, subject) and the remaining legs still run (see
        ``_alert_leg``). The outer guard below is therefore only reachable for a
        structural defect in the status dict, not for an ordinary store failure.
        If a leg fails, the next poll re-evaluates it — the lifecycle is
        dedup-backed and idempotent. That is the deliberate trade for never losing
        the heartbeat.

        #3658 semantics are carried through unchanged and only routed through
        ``_alert_leg``: ``r2_confirmed`` (``r2_ok is True``) gates every
        ARCHIVE-DERIVED resolve — a degraded poll does not MEASURE the archive
        surface, so resolving off it is a fabricated recovery — while the
        ``not_eligible`` arm's resolves stay UNGATED (eligibility is a
        control-plane fact, not an R2 measurement). The org universe-shrink
        additionally requires ``census_confirmed``.
        """
        try:
            r2_confirmed = r2_ok is True
            for org, state in status["per_team"].items():
                # Each state resolves the kinds it is NOT the current truth for,
                # so a transition (e.g. `stamp_missing`→`stale`) cannot leave a
                # stale incident open beside the new one.
                if state == "never":
                    if r2_confirmed:
                        self._alert_leg("resolve_incident", "STALE", org)
                        self._alert_leg("resolve_incident", "METADATA_LOST", org)
                    self._alert_leg("open_incident", "NEVER_BACKED_UP", org)
                elif state == "stale":
                    if r2_confirmed:
                        self._alert_leg("resolve_incident", "NEVER_BACKED_UP", org)
                        self._alert_leg("resolve_incident", "METADATA_LOST", org)
                    self._alert_leg("open_incident", "STALE", org)
                elif state == "stamp_missing":
                    if r2_confirmed:
                        self._alert_leg("resolve_incident", "STALE", org)
                        self._alert_leg("resolve_incident", "NEVER_BACKED_UP", org)
                    self._alert_leg("open_incident", "METADATA_LOST", org)
                elif state == "not_eligible":
                    # #3658: an org the sweep does not target is not a DR
                    # gap. Open nothing — and close everything a previous
                    # (eligible) poll opened, so an org that is downgraded
                    # to free does not leave an incident behind forever.
                    # NOT gated on `r2_confirmed`: eligibility is a
                    # control-plane fact, not an R2 measurement.
                    self._alert_leg("resolve_incident", "STALE", org)
                    self._alert_leg("resolve_incident", "NEVER_BACKED_UP", org)
                    self._alert_leg("resolve_incident", "METADATA_LOST", org)
                    self._alert_leg("resolve_incident", "BACKUP_SET_MISSING", org)
                else:
                    if r2_confirmed:
                        self._alert_leg("resolve_incident", "STALE", org)
                        self._alert_leg("resolve_incident", "NEVER_BACKED_UP", org)
                        self._alert_leg("resolve_incident", "METADATA_LOST", org)
            # Universe shrink: an org that LEFT the census (partial or full —
            # `no_teams` is the degenerate case) keeps no DR incident. The
            # per-org loop above only maintains orgs in THIS poll's census, so
            # without this a departed org's incidents stay open forever (and
            # drop out of `_last_status`, making them un-closeable). Gated on a
            # CONFIRMED census AND a confirmed archive read: a control-plane
            # blip must never resolve a real incident, and on a degraded R2
            # poll `per_team` is the census, not a faithful archive surface
            # (mirror of the per-graph `graph_surface_confirmed`).
            #
            # SAFETY INVARIANT, relied on deliberately: `per_team` is
            # `census ∪ r2_orgs`, so an org can leave it only by leaving the
            # CENSUS *and* having no R2 archive listing. An org that still
            # holds archives therefore can never be shrunk here — which is what
            # keeps a silently SHORT census read (the documented PostgREST
            # `db-max-rows` fail-open class) from resolving a real incident for
            # an org whose data is present. Residual: a short read can still
            # close a NO-ARCHIVE org's NEVER_BACKED_UP, which the next complete
            # poll re-files (filed as a follow-up).
            #
            # NOTE: `BACKUP_SET_MISSING` is deliberately NOT in this tuple. It
            # is state/archive-derived, not census-derived: `compute_status`
            # re-lists a departed state-only org in `backup_set_missing`, so
            # resolving it here would be immediately undone by the BSM open
            # later in this same poll — emitting a false "resolved" push and
            # filing a fresh incident for a still-active condition (review
            # cycle 3). Its own block (open + positive scan + cross-poll diff)
            # fully reconciles it.
            #
            # An org whose resolve FAILED stays pending (our #3031 cycle P1):
            # `prev_per_team` is a one-shot snapshot, so without carrying the
            # failure forward the subject would never be retried and the
            # incident would be orphaned forever (the same defect the graph
            # path had, fixed the same way).
            if census_confirmed and r2_confirmed:
                cur_per_team = set(status.get("per_team", {}))
                pending = set(getattr(self, "_pending_team_resolves", set()))
                pending -= cur_per_team  # reappeared → resolved, drop from pending
                departed = (set(prev_per_team or set()) - cur_per_team) | pending
                for org in sorted(departed):
                    ok = True
                    for kind in ("STALE", "NEVER_BACKED_UP", "METADATA_LOST"):
                        if not self._alert_leg("resolve_incident", kind, org):
                            ok = False
                    if ok:
                        pending.discard(org)
                    else:
                        pending.add(org)
                self._pending_team_resolves = pending
            if status.get("driver_down"):
                self._alert_leg("open_incident", "DRIVER_DOWN")
            elif r2_ok is not False:
                # Resolving requires HAVING READ the heartbeat: on an R2 read
                # failure the heartbeat is never read (`driver_ts=None`), so
                # `driver_down` is False out of ignorance, not out of evidence.
                # Resolving there would clear a real DRIVER_DOWN and re-file it on
                # recovery (final-cycle review P2).
                self._alert_leg("resolve_incident", "DRIVER_DOWN")
            for key, state in status.get("per_graph", {}).items():
                if state == "never":
                    self._alert_leg("open_incident", "NEVER_BACKED_UP", key)
                elif state == "stale":
                    self._alert_leg("open_incident", "STALE", key)
                elif state == "stamp_missing":
                    self._alert_leg("open_incident", "METADATA_LOST", key)
                elif state == "backup_set_missing":
                    self._alert_leg("open_incident", "BACKUP_SET_MISSING", key)
                else:
                    self._alert_leg("resolve_incident", "STALE", key)
                    self._alert_leg("resolve_incident", "NEVER_BACKED_UP", key)
                    self._alert_leg("resolve_incident", "METADATA_LOST", key)
                    self._alert_leg("resolve_incident", "BACKUP_SET_MISSING", key)
            if r2_confirmed:
                # The BACKUP_SET_MISSING OPEN is an ABSENCE claim ("this org's
                # state exists and NO archive does"), so it needs a confirmed
                # archive read for the same reason the resolves do: on a
                # degraded poll `compute_status` substitutes the CENSUS for the
                # archive surface, so an org whose archives are intact but
                # which is absent from the live census would be paged as a
                # data-loss condition. Opening is normally the conservative
                # direction; this one is not, because the state it asserts was
                # never measured.
                for org in status.get("backup_set_missing", []):
                    self._alert_leg("open_incident", "BACKUP_SET_MISSING", org)
                # BACKUP_SET_MISSING resolves when the org's archives reappear
                # OR the org leaves the set. Gated on a confirmed archive read,
                # like every other resolve here.
                cur_bsm = set(status.get("backup_set_missing", []))
                # POSITIVE reconciliation — the pre-#3658 per_team scan,
                # restored (its removal was a regression): after a watcher
                # RESTART `_last_backup_set_missing` is empty, so the
                # cross-poll diff below alone can never close a
                # BACKUP_SET_MISSING whose archives recovered before the first
                # poll — the incident would stay open and its dedup state
                # could absorb the next genuine recurrence.
                for org in set(status["per_team"]) - cur_bsm:
                    self._alert_leg("resolve_incident", "BACKUP_SET_MISSING", org)
                # ...and a state-only org that LEFT the set (tracked across
                # polls — it is never in `per_team`, so the scan above cannot
                # close it).
                for org in self._last_backup_set_missing - cur_bsm:
                    self._alert_leg("resolve_incident", "BACKUP_SET_MISSING", org)
                self._last_backup_set_missing = cur_bsm
            # R2_DOWN: emit while degraded-from-known-good, resolve when healthy.
            # Its own guard kept for symmetry with the other legs; the outer
            # `except` below remains as a backstop for structural errors (a
            # malformed status dict), and per-leg containment means it is now
            # only reached for defects rather than for an ordinary store failure.
            if r2_ok is False:
                self._alert_leg("open_incident", "R2_DOWN")
            else:
                self._alert_leg("resolve_incident", "R2_DOWN")
        except Exception:
            logger.exception(
                "alert store legs failed — heartbeat unaffected (a broken "
                "alerter must never fabricate WATCHER_DOWN)"
            )

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
