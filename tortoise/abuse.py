"""Abuse detection + enforcement for the hosted platform (#308).

Durable substrate (migration 0015): ``abuse_events`` rows + ``teams.
suspended_at``/``flagged_at`` + ``api_keys`` INSERT trigger (the only seam
that sees BOTH dashboard mints and the signup ``provision_team`` RPC).

Rules (env-overridable thresholds):
- R1  point_create: SUM(weight) > 500 / 1h   -> stage-1 flag, stage-2 suspend
- R2  key_create:   count    > 10  / 24h     -> stage-1 flag, stage-2 suspend
- R3  reads:        > 100 / 5min per-key OR per-team -> notify Owner only
- R4  geo:          first unseen CF-IPCountry per team -> notify Owner

Two-stage staging (scoping delta 13 — the false-positive guarantee):
evaluation is event-triggered. Stage 1 flags when the window sum exceeds the
threshold (durable ``teams.flagged_at``). Stage 2 suspends ONLY when an
evaluation occurs at/after ``flagged_at + window`` AND the window still
breaches — a single burst contained in one window can flag but can NEVER
auto-suspend; a team that flags and goes quiet is never suspended.

Suspension signal set (scoping delta 14): the process-wide set is a
CACHE-INVALIDATION SIGNAL, never a rejection authority — membership forces a
fresh resolution; the durable ``teams.suspended_at`` is the sole ground for
403/-32006; entries clear when a fresh resolution returns NULL (un-suspend
self-heals on the next request).

Everything here is best-effort on the request path: recording/evaluation
failures are logged and swallowed — abuse telemetry must never break the
write path. Kill-switch: ``TORTOISE_ABUSE_DISABLED=1``.
"""
from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

EVENT_POINT_CREATE = "point_create"
EVENT_KEY_CREATE = "key_create"
EVENT_AUTH_IP = "auth_ip"
EVENT_FLAG = "flag"
EVENT_SUSPEND = "suspend"
EVENT_UNSUSPEND = "unsuspend"

ALERT_TYPES = (EVENT_FLAG, EVENT_SUSPEND, EVENT_AUTH_IP, "read_velocity")


def _int_env(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v and v.strip().isdigit() else default
    except (TypeError, ValueError):
        return default


def abuse_disabled() -> bool:
    return os.environ.get("TORTOISE_ABUSE_DISABLED") == "1"


def appeal_url() -> str:
    return os.environ.get(
        "TORTOISE_ABUSE_APPEAL_URL",
        "https://tortoise.premiselabs.co/docs.html#appeal",
    )


def suspended_message() -> str:
    return (
        "This team has been suspended due to unusual activity. "
        f"Appeal: {appeal_url()}"
    )


# ── Suspended signal set (delta 14) ─────────────────────────────────────────
# Cache-invalidation signal ONLY — never a rejection authority. Membership
# forces a fresh resolution; durable suspended_at decides; entries clear when
# a fresh resolution returns suspended_at=NULL.
_SUSPENDED_SIGNAL: set[str] = set()
_SIGNAL_LOCK = threading.Lock()


def mark_suspended(team_id: str) -> None:
    if team_id:
        with _SIGNAL_LOCK:
            _SUSPENDED_SIGNAL.add(team_id)


def clear_suspended(team_id: str) -> None:
    with _SIGNAL_LOCK:
        _SUSPENDED_SIGNAL.discard(team_id)


def is_suspended_signal(team_id: str) -> bool:
    with _SIGNAL_LOCK:
        return team_id in _SUSPENDED_SIGNAL


def _utcnow(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _ensure_aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _parse_ts(value) -> datetime | None:
    """Parse an ISO timestamp (PostgREST 'Z' or offset form) → aware dt."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_aware(parsed)


# ── Stores ──────────────────────────────────────────────────────────────────

class MemoryAbuseStore:
    """In-memory store: registry/selfhost mode + tests.

    Optional ``registry_write`` callback makes enforcement durable in
    selfhost: ``registry_write(team_id, suspended_at, flagged_at)`` writes
    the Team node props (scoping delta 4). Deploy-reset semantics apply to
    the in-memory rows themselves (documented degradation).
    """

    def __init__(self, registry_write=None):
        self.rows: list[dict] = []
        self.flags: dict[str, datetime] = {}
        self.suspended: dict[str, datetime] = {}
        self.team_emails: dict[str, str | None] = {}
        self._registry_write = registry_write
        self._lock = threading.Lock()

    def _append(self, team_id: str, event_type: str, *, weight: int = 1,
                key_id: str | None = None, country: str | None = None,
                details: dict | None = None,
                created_at: datetime | None = None) -> None:
        with self._lock:
            self.rows.append({
                "team_id": team_id, "event_type": event_type,
                "weight": int(weight), "key_id": key_id, "country": country,
                "details": details or {},
                "created_at": _ensure_aware(_utcnow(created_at)),
            })

    def record_event(self, team_id: str, event_type: str, *, weight: int = 1,
                     key_id: str | None = None, country: str | None = None,
                     details: dict | None = None,
                     created_at: datetime | None = None) -> None:
        self._append(team_id, event_type, weight=weight, key_id=key_id,
                     country=country, details=details, created_at=created_at)

    def window_sum(self, team_id: str, event_type: str, window_s: int,
                   now: datetime | None = None) -> int:
        now = _ensure_aware(_utcnow(now))
        cutoff = now - timedelta(seconds=window_s)
        with self._lock:
            return sum(
                int(r.get("weight") or 1) for r in self.rows
                if r["team_id"] == team_id and r["event_type"] == event_type
                and r["created_at"] > cutoff
            )

    def team_flagged_at(self, team_id: str) -> datetime | None:
        return self.flags.get(team_id)

    def flag_team(self, team_id: str, event_type: str,
                  details: dict | None = None,
                  now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.flags[team_id] = now
        self._append(team_id, EVENT_FLAG, details={
            **(details or {}), "rule": event_type}, created_at=now)
        self._durable(team_id)

    def clear_flag(self, team_id: str) -> None:
        self.flags.pop(team_id, None)
        self._durable(team_id)

    def suspend_team(self, team_id: str, details: dict | None = None,
                     now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.suspended[team_id] = now
        self.flags.pop(team_id, None)
        self._append(team_id, EVENT_SUSPEND, details=details or {},
                     created_at=now)
        self._durable(team_id)

    def unsuspend_team(self, team_id: str, now: datetime | None = None) -> None:
        self.suspended.pop(team_id, None)
        self.flags.pop(team_id, None)
        self._append(team_id, EVENT_UNSUSPEND, created_at=now)
        self._durable(team_id)

    def team_suspended(self, team_id: str) -> bool:
        return team_id in self.suspended

    def team_email(self, team_id: str) -> str | None:
        return self.team_emails.get(team_id)

    def seen_countries(self, team_id: str) -> set[str]:
        with self._lock:
            return {r["country"] for r in self.rows
                    if r["team_id"] == team_id
                    and r["event_type"] == EVENT_AUTH_IP and r["country"]}

    def recent_alerts(self, team_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = [r for r in self.rows
                    if r["team_id"] == team_id
                    and r["event_type"] in ALERT_TYPES]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_alert_dict(r) for r in rows[:limit]]

    def _durable(self, team_id: str) -> None:
        """Write-through to the registry Team node when a callback is wired
        (selfhost durability, scoping delta 4). Best-effort."""
        if self._registry_write is None:
            return
        try:
            sus = self.suspended.get(team_id)
            self._registry_write(
                team_id,
                sus.isoformat() if sus else None,
                self.flags.get(team_id).isoformat()
                if team_id in self.flags else None,
            )
        except Exception:
            logger.debug("abuse registry write-through failed for %s", team_id)


class SupabaseAbuseStore:
    """Supabase-backed durable store (migration 0015)."""

    def __init__(self, cp):
        self._cp = cp

    def record_event(self, team_id: str, event_type: str, *, weight: int = 1,
                     key_id: str | None = None, country: str | None = None,
                     details: dict | None = None) -> None:
        body = {"team_id": team_id, "event_type": event_type,
                "weight": int(weight)}
        if key_id is not None:
            body["key_id"] = key_id
        if country is not None:
            body["country"] = country
        if details:
            body["details"] = details
        self._cp.query("abuse_events", method="POST", json_body=body)

    def window_sum(self, team_id: str, event_type: str, window_s: int,
                   now: datetime | None = None) -> int:
        cutoff = (_ensure_aware(_utcnow(now))
                  - timedelta(seconds=window_s)).isoformat()
        rows = self._cp.query(
            "abuse_events", select=["weight"],
            filters=[("team_id", "eq", team_id),
                     ("event_type", "eq", event_type),
                     ("created_at", "gt", cutoff)],
        )
        return sum(int(r.get("weight") or 1) for r in rows)

    def _team_field(self, team_id: str, field: str):
        rows = self._cp.query("teams", select=[field],
                              filters=[("id", "eq", team_id)])
        return rows[0].get(field) if rows else None

    def team_flagged_at(self, team_id: str) -> datetime | None:
        return _parse_ts(self._team_field(team_id, "flagged_at"))

    def flag_team(self, team_id: str, event_type: str,
                  details: dict | None = None,
                  now: datetime | None = None) -> None:
        self._cp.query(
            "teams", method="PATCH", filters=[("id", "eq", team_id)],
            json_body={"flagged_at": _ensure_aware(_utcnow(now)).isoformat()},
        )
        self.record_event(team_id, EVENT_FLAG,
                          details={**(details or {}), "rule": event_type})

    def clear_flag(self, team_id: str) -> None:
        self._cp.query("teams", method="PATCH",
                       filters=[("id", "eq", team_id)],
                       json_body={"flagged_at": None})

    def suspend_team(self, team_id: str, details: dict | None = None,
                     now: datetime | None = None) -> None:
        # The RPC sets suspended_at (DB-side now()) AND records the suspend
        # event atomically; ``now`` accepted for store-protocol parity.
        self._cp.rpc("abuse_suspend", {"p_team_id": team_id})

    def unsuspend_team(self, team_id: str) -> None:
        self._cp.rpc("abuse_unsuspend", {"p_team_id": team_id})

    def team_suspended(self, team_id: str) -> bool:
        return self._team_field(team_id, "suspended_at") is not None

    def team_email(self, team_id: str) -> str | None:
        return self._team_field(team_id, "email")

    def seen_countries(self, team_id: str) -> set[str]:
        rows = self._cp.query(
            "abuse_events", select=["country"],
            filters=[("team_id", "eq", team_id),
                     ("event_type", "eq", EVENT_AUTH_IP)],
        )
        return {r["country"] for r in rows if r.get("country")}

    def recent_alerts(self, team_id: str, limit: int = 20) -> list[dict]:
        rows = self._cp.query(
            "abuse_events",
            select=["event_type", "created_at", "country", "key_id",
                    "details"],
            filters=[("team_id", "eq", team_id)],
            order="-created_at", limit=100,
        )
        out = [_alert_dict(r) for r in rows
               if r.get("event_type") in ALERT_TYPES]
        return out[:limit]


def _alert_dict(row: dict) -> dict:
    etype = row.get("event_type")
    details = row.get("details") or {}
    messages = {
        EVENT_FLAG: f"Suspicious activity flagged ({details.get('rule', 'rule')})",
        EVENT_SUSPEND: "Team auto-suspended due to unusual activity",
        EVENT_AUTH_IP: f"Access from new location: {row.get('country') or 'unknown'}",
        "read_velocity": "Unusual read velocity detected on an API key",
    }
    at = row.get("created_at")
    return {
        "type": etype,
        "at": at.isoformat() if isinstance(at, datetime) else at,
        "message": messages.get(etype, etype),
    }


# ── Engine ──────────────────────────────────────────────────────────────────

class AbuseEngine:
    """Two-stage rule engine over an AbuseStore (scoping deltas 8/13)."""

    def __init__(self, store):
        self.store = store

    # thresholds read at call time so env overrides apply per-process
    def point_threshold(self) -> int:
        return _int_env("TORTOISE_ABUSE_POINT_THRESHOLD", 500)

    def point_window_s(self) -> int:
        return _int_env("TORTOISE_ABUSE_POINT_WINDOW_S", 3600)

    def key_threshold(self) -> int:
        return _int_env("TORTOISE_ABUSE_KEY_THRESHOLD", 10)

    def key_window_s(self) -> int:
        return _int_env("TORTOISE_ABUSE_KEY_WINDOW_S", 86400)

    def record_point_create(self, team_id: str, n: int = 1,
                            now: datetime | None = None) -> str | None:
        """R1 recording + evaluation. Piggybacks R2 evaluation (delta-13 fix:
        trigger-recorded key_create events have no app request of their own —
        they evaluate on the team's next hooked request)."""
        if abuse_disabled() or not team_id or n <= 0:
            return None
        try:
            self.store.record_event(team_id, EVENT_POINT_CREATE, weight=n)
        except Exception:
            logger.debug("abuse record_point_create failed for %s", team_id)
        r1 = self._evaluate(team_id, EVENT_POINT_CREATE,
                            self.point_threshold(), self.point_window_s(), now)
        r2 = self._evaluate(team_id, EVENT_KEY_CREATE,
                            self.key_threshold(), self.key_window_s(), now)
        # suspension outranks flag for the caller's view
        return "suspend" if "suspend" in (r1, r2) else (r1 or r2)

    def evaluate_key_creates(self, team_id: str,
                             now: datetime | None = None) -> str | None:
        """R2 evaluation (key_create events land via the DB trigger)."""
        if abuse_disabled() or not team_id:
            return None
        return self._evaluate(team_id, EVENT_KEY_CREATE,
                              self.key_threshold(), self.key_window_s(), now)

    def _evaluate(self, team_id: str, event_type: str, threshold: int,
                  window_s: int, now: datetime | None = None) -> str | None:
        now = _ensure_aware(_utcnow(now))
        try:
            total = self.store.window_sum(team_id, event_type, window_s, now)
        except Exception:
            logger.debug("abuse window_sum failed for %s/%s", team_id, event_type)
            return None
        if total <= threshold:
            return None
        try:
            flagged_at = self.store.team_flagged_at(team_id)
        except Exception:
            flagged_at = None
        details = {"rule": event_type, "count": total,
                   "threshold": threshold, "window_s": window_s}
        if flagged_at is None:
            # Stage 1 — flag. Durable flagged_at anchors the staging window.
            try:
                self.store.flag_team(team_id, event_type, details, now=now)
            except Exception:
                logger.debug("abuse flag_team failed for %s", team_id)
            self._notify("abuse_flag", team_id, details)
            return "flag"
        if (now - _ensure_aware(flagged_at)).total_seconds() >= window_s:
            # Stage 2 — the breach persisted across a full window boundary.
            try:
                self.store.suspend_team(team_id, details, now=now)
            except Exception:
                logger.debug("abuse suspend_team failed for %s", team_id)
                return "breach"
            mark_suspended(team_id)
            self._notify("abuse_suspended", team_id, details)
            return "suspend"
        return "breach"

    def _notify(self, kind: str, team_id: str, details: dict) -> None:
        try:
            from tortoise.notify import notify_abuse
            email = None
            try:
                email = self.store.team_email(team_id)
            except Exception:
                pass
            notify_abuse(kind, {"team_id": team_id, "email": email},
                         {**details, "appeal_url": appeal_url()})
        except Exception:
            logger.debug("abuse notify failed (%s, %s)", kind, team_id)


# ── R3: read-velocity tracker (in-memory, notify-only) ─────────────────────

class ReadVelocityTracker:
    """>100 reads / 5min per-key OR per-team → notify Owner once per window.

    In-memory by design (the 5-min window bounds deploy-reset damage);
    notify-only per the issue — R3 never suspends.
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_READ_THRESHOLD", 100)
        self.window_s = window_s if window_s is not None else _int_env(
            "TORTOISE_ABUSE_READ_WINDOW_S", 300)
        self._by_key: dict[str, list[float]] = defaultdict(list)
        self._by_team: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def record_read(self, key_id: str | None, team_id: str | None,
                    now: float | None = None) -> tuple[str, str] | None:
        """Returns ('key'|'team', id) on breach, else None. Notify dedup is
        once per window per (scope, id)."""
        if abuse_disabled():
            return None
        import time as _time
        now = now if now is not None else _time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            if key_id:
                kb = self._by_key[key_id]
                kb[:] = [t for t in kb if t > cutoff]
                kb.append(now)
                if len(kb) > self.threshold:
                    breach = ("key", key_id)
            if team_id:
                tb = self._by_team[team_id]
                tb[:] = [t for t in tb if t > cutoff]
                tb.append(now)
                if breach is None and len(tb) > self.threshold:
                    breach = ("team", team_id)
            # bound memory growth (mirrors _register_buckets hygiene)
            if len(self._by_key) > 10_000:
                self._by_key = {k: v for k, v in self._by_key.items()
                                if any(t > cutoff for t in v)}
            if len(self._by_team) > 10_000:
                self._by_team = {k: v for k, v in self._by_team.items()
                                 if any(t > cutoff for t in v)}
            if breach is not None:
                last = self._notified.get(breach)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[breach] = now
        if breach is not None:
            self._notify(breach)
        return breach

    def _notify(self, breach: tuple[str, str]) -> None:
        scope, ident = breach
        try:
            from tortoise.notify import notify_abuse
            notify_abuse("abuse_read_velocity",
                         {"team_id": ident if scope == "team" else None},
                         {"scope": scope, "id": ident,
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("read-velocity notify failed (%s)", breach)


READ_TRACKER = ReadVelocityTracker()


def record_read(key_id: str | None, team_id: str | None,
                now: float | None = None):
    """Module-level seam (monkeypatchable) over the shared tracker."""
    return READ_TRACKER.record_read(key_id, team_id, now)


# ── R4: geo (CF-IPCountry header, fail-open) ───────────────────────────────

_GEO_CACHE: dict[str, tuple[float, set[str]]] = {}
_GEO_TTL_S = 86400
_GEO_LOCK = threading.Lock()


def resolve_country(headers) -> str | None:
    """CF-IPCountry passthrough (Fly/Cloudflare set it). Fail-open: no header
    → None → R4 inactive. IPINFO_TOKEN resolver is a documented follow-on."""
    try:
        value = headers.get("cf-ipcountry") if headers else None
    except Exception:
        return None
    value = (value or "").strip()
    return value.upper() or None


def check_new_country(team_id: str, country: str | None, store,
                      now: float | None = None) -> bool:
    """True when the country is new for the team (records auth_ip + notifies).
    Seen-set cached in-process (24h TTL); durable lookup on cache miss."""
    if abuse_disabled() or not team_id or not country:
        return False
    import time as _time
    now = now if now is not None else _time.time()
    with _GEO_LOCK:
        cached = _GEO_CACHE.get(team_id)
        if cached is None or now - cached[0] > _GEO_TTL_S:
            try:
                seen = set(store.seen_countries(team_id))
            except Exception:
                return False  # fail-open: unseen-set unavailable → inactive
            cached = (now, seen)
            _GEO_CACHE[team_id] = cached
        seen = cached[1]
        if country in seen:
            return False
        seen.add(country)
    try:
        store.record_event(team_id, EVENT_AUTH_IP, country=country)
    except Exception:
        logger.debug("geo event record failed for %s", team_id)
    try:
        from tortoise.notify import notify_abuse
        notify_abuse("abuse_new_ip", {"team_id": team_id},
                     {"country": country, "appeal_url": appeal_url()})
    except Exception:
        logger.debug("geo notify failed for %s", team_id)
    return True


def reset_geo_cache() -> None:
    with _GEO_LOCK:
        _GEO_CACHE.clear()


# ── Engine singleton seam ───────────────────────────────────────────────────

_engine: AbuseEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> AbuseEngine:
    """Lazy process-wide engine (tests monkeypatch or reset_engine())."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from tortoise.supabase_control import get_abuse_store
                _engine = AbuseEngine(get_abuse_store())
    return _engine


def set_engine(engine: AbuseEngine | None) -> None:
    global _engine
    with _engine_lock:
        _engine = engine
