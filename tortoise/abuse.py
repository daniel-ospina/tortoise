"""Abuse detection + enforcement for the hosted platform (#308).

Durable substrate (migration 0015): ``abuse_events`` rows + ``teams.
suspended_at``/``flagged_at`` + ``api_keys`` INSERT trigger (the only seam
that sees BOTH dashboard mints and the signup ``provision_org`` RPC).

Rules (env-overridable thresholds):
- R1  point_create: SUM(weight) > 500 / 1h   -> stage-1 flag, stage-2 suspend
- R2  key_create:   count    > 10  / 24h     -> stage-1 flag, stage-2 suspend
- R3  reads:        > 100 / 5min per-key OR per-org -> ops alert (Telegram-only, #3639)
- R4  geo:          first unseen CF-IPCountry per org -> ops alert (Telegram-only)
- R8 signup_velocity: N anon signups/IP/window (breach >= threshold) ->
                     notify ops only (Telegram; never suspends)

Two-stage staging with EPISODE semantics (scoping delta 13 + code-review
fixes): flags are PER-RULE (flag event rows carry the rule). Stage 2
suspends only when (a) the rule's flag is a full window old, AND (b) the
rule has at least one event between the flag and the current window's start
— evidence the breach actually persisted across the boundary. Episodes END
on a clean evaluation (window back under threshold → flag_clear event) or on
un-suspend (the RPC clears all episodes) — so a burst after a quiet period
or after recovery is a NEW episode: it re-flags and can never suspend on its
first evaluation. Rules are independent: an R1 flag never escalates a first
R2 breach.

Suspension signal set (scoping delta 14): the process-wide set is a
CACHE-INVALIDATION SIGNAL, never a rejection authority — membership forces a
fresh resolution; the durable ``teams.suspended_at`` is the sole ground for
403/-32006; entries clear when a fresh resolution returns NULL (un-suspend
self-heals on the next request).

Everything here is best-effort on the request path: recording/evaluation
failures are logged and swallowed — abuse telemetry must never break the
write path. Kill-switch: ``TORTOISE_ABUSE_DISABLED=1``.

Notification volume (#3631): an R1/R2 flag or suspend alert is emitted ONLY
after the corresponding store write persists — a store read/write failure
REDUCES notification volume, never increases it (the 2026-09-14 storm was a
swallowed ``flag_org`` write failure that notified on every evaluation, 401
alerts in 3h). The ALERT is also bounded to once per ``(org, rule)`` per
STAGE per staging window in-process, so neither a read that raises nor one that
returns a stale ``None`` (replica lag) can re-notify per evaluation (the stage
distinction is what lets the stage-2 suspend alert still escalate a stage-1
flag alert). The claim is released when the engine observes the episode end (a
clean window), so a genuinely NEW episode alerts again. The durable store
remains authoritative for staging; the in-process map bounds ONE process, so
the honest ceiling across replicas is ``N replicas × 1`` per window — a global
cap needs shared state (Redis/DB) and is deliberately out of scope here.
(The notify-only rules R3/R4/R8 have no flag to persist.)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

EVENT_POINT_CREATE = "point_create"
EVENT_KEY_CREATE = "key_create"
EVENT_AUTH_IP = "auth_ip"
EVENT_FLAG = "flag"
EVENT_FLAG_CLEAR = "flag_clear"  # episode end (clean eval or un-suspend)
EVENT_SUSPEND = "suspend"
EVENT_UNSUSPEND = "unsuspend"
EVENT_READ_VELOCITY = "read_velocity"
EVENT_SIGNUP_VELOCITY = "signup_velocity"
EVENT_RECOVERY_VELOCITY = "recovery_velocity"

ALERT_TYPES = (EVENT_FLAG, EVENT_SUSPEND, EVENT_AUTH_IP, EVENT_READ_VELOCITY,
               EVENT_SIGNUP_VELOCITY, EVENT_RECOVERY_VELOCITY)


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
        "This organization has been suspended due to unusual activity. "
        f"Appeal: {appeal_url()}"
    )


# ── Suspended signal set (delta 14) ─────────────────────────────────────────
_SUSPENDED_SIGNAL: set[str] = set()
_SIGNAL_LOCK = threading.Lock()


def mark_suspended(org_id: str) -> None:
    if org_id:
        with _SIGNAL_LOCK:
            _SUSPENDED_SIGNAL.add(org_id)


def clear_suspended(org_id: str) -> None:
    with _SIGNAL_LOCK:
        _SUSPENDED_SIGNAL.discard(org_id)


def is_suspended_signal(org_id: str) -> bool:
    with _SIGNAL_LOCK:
        return org_id in _SUSPENDED_SIGNAL


def _utcnow(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)  # noqa: UP017


def _ensure_aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)  # noqa: UP017


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


def _org_email(store, org_id: str) -> str | None:
    """Best-effort owner email. Retained for the caller shape only — abuse
    alerts are Telegram-only since #3639, so ``notify_abuse`` no longer reads
    the ``email`` key (removing the now-dead call sites is a #3646 follow-up).
    """
    try:
        return store.org_email(org_id) if org_id else None
    except Exception:
        return None


# ── Stores ──────────────────────────────────────────────────────────────────

class MemoryAbuseStore:
    """In-memory store: registry/selfhost mode + tests.

    Optional ``registry_write`` callback makes enforcement durable in
    selfhost: ``registry_write(org_id, suspended_at, flagged_at)`` writes
    the Org node props (scoping delta 4). Deploy-reset semantics apply to
    the in-memory rows themselves (documented degradation).
    """

    def __init__(self, registry_write=None):
        self.rows: list[dict] = []
        self.flags: dict[str, datetime] = {}
        self.suspended: dict[str, datetime] = {}
        self.org_emails: dict[str, str | None] = {}
        self._registry_write = registry_write
        self._lock = threading.Lock()

    def _append(self, org_id: str, event_type: str, *, weight: int = 1,
                key_id: str | None = None, country: str | None = None,
                rule: str | None = None, details: dict | None = None,
                created_at: datetime | None = None) -> None:
        with self._lock:
            self.rows.append({
                "org_id": org_id, "event_type": event_type,
                "weight": int(weight), "key_id": key_id, "country": country,
                "rule": rule, "details": details or {},
                "created_at": _ensure_aware(_utcnow(created_at)),
            })

    def record_event(self, org_id: str, event_type: str, *, weight: int = 1,
                     key_id: str | None = None, country: str | None = None,
                     rule: str | None = None, details: dict | None = None,
                     created_at: datetime | None = None) -> None:
        self._append(org_id, event_type, weight=weight, key_id=key_id,
                     country=country, rule=rule, details=details,
                     created_at=created_at)

    def window_sum(self, org_id: str, event_type: str, window_s: int,
                   now: datetime | None = None) -> int:
        now = _ensure_aware(_utcnow(now))
        cutoff = now - timedelta(seconds=window_s)
        with self._lock:
            return sum(
                int(r.get("weight") or 1) for r in self.rows
                if r["org_id"] == org_id and r["event_type"] == event_type
                and r["created_at"] > cutoff
            )

    def latest_flag_at(self, org_id: str, rule: str) -> datetime | None:
        """ACTIVE per-rule flag-episode anchor: the newest flag row, or None
        when a flag_clear (clean evaluation or un-suspend) ended the
        episode after it."""
        with self._lock:
            flags = [r["created_at"] for r in self.rows
                     if r["org_id"] == org_id
                     and r["event_type"] == EVENT_FLAG
                     and r.get("rule") == rule]
            clears = [r["created_at"] for r in self.rows
                      if r["org_id"] == org_id
                      and r["event_type"] == EVENT_FLAG_CLEAR
                      and r.get("rule") == rule]
        if not flags:
            return None
        newest_flag = max(flags)
        if clears and max(clears) >= newest_flag:
            return None  # episode ended
        return newest_flag

    def flag_clear(self, org_id: str, rule: str,
                   now: datetime | None = None) -> None:
        """End a flag episode (clean evaluation or recovery)."""
        now = _ensure_aware(_utcnow(now))
        self._append(org_id, EVENT_FLAG_CLEAR, rule=rule, created_at=now)
        # teams.flagged_at is the org-level chip: clear only when NO rule
        # still has an active episode.
        other_active = any(
            self.latest_flag_at(org_id, r) is not None
            for r in (EVENT_POINT_CREATE, EVENT_KEY_CREATE) if r != rule)
        if not other_active:
            self.flags.pop(org_id, None)
            self._durable(org_id, "flagged_at", None)

    def rule_event_between(self, org_id: str, rule: str, after: datetime,
                          before: datetime) -> bool:
        """Continuity evidence: any rule event in (after, before]."""
        after, before = _ensure_aware(after), _ensure_aware(before)
        with self._lock:
            return any(r["org_id"] == org_id and r["event_type"] == rule
                       and after < r["created_at"] <= before
                       for r in self.rows)

    def org_flagged_at(self, org_id: str) -> datetime | None:
        return self.flags.get(org_id)

    def flag_org(self, org_id: str, rule: str,
                  details: dict | None = None,
                  now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.flags[org_id] = now
        self._append(org_id, EVENT_FLAG, rule=rule, details={
            **(details or {}), "rule": rule}, created_at=now)
        self._durable(org_id, "flagged_at", now.isoformat())



    def suspend_org(self, org_id: str, details: dict | None = None,
                     now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.suspended[org_id] = now
        self._append(org_id, EVENT_SUSPEND,
                     rule=(details or {}).get("rule"),
                     details=details or {}, created_at=now)
        self._durable(org_id, "suspended_at", now.isoformat())

    def unsuspend_org(self, org_id: str, now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.suspended.pop(org_id, None)
        self.flags.pop(org_id, None)
        self._append(org_id, EVENT_UNSUSPEND, created_at=now)
        # end every flag episode — a recovered org starts clean, so its
        # first post-recovery burst re-flags instead of auto-suspending
        for rule in (EVENT_POINT_CREATE, EVENT_KEY_CREATE):
            self._append(org_id, EVENT_FLAG_CLEAR, rule=rule, created_at=now)
        self._durable(org_id, "suspended_at", None)
        self._durable(org_id, "flagged_at", None)

    def org_suspended(self, org_id: str) -> bool:
        return org_id in self.suspended

    def org_email(self, org_id: str) -> str | None:
        return self.org_emails.get(org_id)

    def seen_countries(self, org_id: str) -> set[str]:
        with self._lock:
            return {r["country"] for r in self.rows
                    if r["org_id"] == org_id
                    and r["event_type"] == EVENT_AUTH_IP and r["country"]}

    def recent_alerts(self, org_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = [r for r in self.rows
                    if r["org_id"] == org_id
                    and r["event_type"] in ALERT_TYPES]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_alert_dict(r) for r in rows[:limit]]

    def _durable(self, org_id: str, field: str, value) -> None:
        """Field-scoped write-through to the registry Org node when a
        callback is wired (selfhost durability, scoping delta 4). Writing
        ONLY the changed field means a concurrent flag/suspend can never
        clobber the other prop (code-review P2). Best-effort."""
        if self._registry_write is None:
            return
        try:
            self._registry_write(org_id, field, value)
        except Exception:
            logger.debug("abuse registry write-through failed for %s", org_id)


class SupabaseAbuseStore:
    """Supabase-backed durable store (migration 0015)."""

    def __init__(self, cp):
        self._cp = cp

    def record_event(self, org_id: str, event_type: str, *, weight: int = 1,
                     key_id: str | None = None, country: str | None = None,
                     rule: str | None = None, details: dict | None = None,
                     created_at: datetime | None = None) -> None:
        body = {"org_id": org_id, "event_type": event_type,
                "weight": int(weight)}
        if key_id is not None:
            body["key_id"] = key_id
        if country is not None:
            body["country"] = country
        if rule is not None:
            body["rule"] = rule
        if details:
            body["details"] = details
        self._cp.query("abuse_events", method="POST", json_body=body)

    def window_sum(self, org_id: str, event_type: str, window_s: int,
                   now: datetime | None = None) -> int:
        cutoff = (_ensure_aware(_utcnow(now))
                  - timedelta(seconds=window_s)).isoformat()
        rows = self._cp.query(
            "abuse_events", select=["weight"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", event_type),
                     ("created_at", "gt", cutoff)],
        )
        return sum(int(r.get("weight") or 1) for r in rows)

    def latest_flag_at(self, org_id: str, rule: str) -> datetime | None:
        """ACTIVE per-rule flag-episode anchor (None when a flag_clear ended
        the episode after the newest flag)."""
        rows = self._cp.query(
            "abuse_events", select=["created_at"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", EVENT_FLAG),
                     ("rule", "eq", rule)],
            order="-created_at", limit=1,
        )
        if not rows:
            return None
        newest_flag = _parse_ts(rows[0].get("created_at"))
        clears = self._cp.query(
            "abuse_events", select=["created_at"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", EVENT_FLAG_CLEAR),
                     ("rule", "eq", rule)],
            order="-created_at", limit=1,
        )
        if clears:
            newest_clear = _parse_ts(clears[0].get("created_at"))
            if newest_clear is not None and newest_flag is not None \
                    and newest_clear >= newest_flag:
                return None  # episode ended
        return newest_flag

    def flag_clear(self, org_id: str, rule: str,
                   now: datetime | None = None) -> None:
        """End a flag episode; clear the org-level chip only when no other
        rule keeps an active episode."""
        self.record_event(org_id, EVENT_FLAG_CLEAR, rule=rule)
        other_active = any(
            self.latest_flag_at(org_id, r) is not None
            for r in (EVENT_POINT_CREATE, EVENT_KEY_CREATE) if r != rule)
        if not other_active:
            self.clear_flag(org_id)

    def rule_event_between(self, org_id: str, rule: str, after: datetime,
                          before: datetime) -> bool:
        rows = self._cp.query(
            "abuse_events", select=["created_at"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", rule),
                     ("created_at", "gt", _ensure_aware(after).isoformat()),
                     ("created_at", "lte", _ensure_aware(before).isoformat())],
            limit=1,
        )
        return bool(rows)

    def _org_field(self, org_id: str, field: str):
        rows = self._cp.query("organizations", select=[field],
                              filters=[("id", "eq", org_id)])
        return rows[0].get(field) if rows else None

    def org_flagged_at(self, org_id: str) -> datetime | None:
        return _parse_ts(self._org_field(org_id, "flagged_at"))

    def flag_org(self, org_id: str, rule: str,
                  details: dict | None = None,
                  now: datetime | None = None) -> None:
        self._cp.query(
            "organizations", method="PATCH", filters=[("id", "eq", org_id)],
            json_body={"flagged_at": _ensure_aware(_utcnow(now)).isoformat()},
        )
        self.record_event(org_id, EVENT_FLAG, rule=rule,
                          details={**(details or {}), "rule": rule})

    def clear_flag(self, org_id: str) -> None:
        self._cp.query("organizations", method="PATCH",
                       filters=[("id", "eq", org_id)],
                       json_body={"flagged_at": None})

    def suspend_org(self, org_id: str, details: dict | None = None,
                     now: datetime | None = None) -> None:
        # The RPC sets suspended_at (DB-side now()) AND records the suspend
        # event atomically; ``now`` accepted for store-protocol parity.
        self._cp.rpc("abuse_suspend", {"p_org_id": org_id})

    def unsuspend_org(self, org_id: str, now: datetime | None = None) -> None:
        self._cp.rpc("abuse_unsuspend", {"p_org_id": org_id})

    def org_suspended(self, org_id: str) -> bool:
        return self._org_field(org_id, "suspended_at") is not None

    def org_email(self, org_id: str) -> str | None:
        # #1765 demotion re-point: prefer the owner's USER email (teams.email
        # is no longer synced by claim), fall back to the contact field.
        from tortoise.supabase_control import owner_email
        try:
            owner = owner_email(self._cp, org_id)
        except Exception:
            owner = None
        return owner or self._org_field(org_id, "email")

    def seen_countries(self, org_id: str) -> set[str]:
        rows = self._cp.query(
            "abuse_events", select=["country"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", EVENT_AUTH_IP)],
        )
        return {r["country"] for r in rows if r.get("country")}

    def recent_alerts(self, org_id: str, limit: int = 20) -> list[dict]:
        rows = self._cp.query(
            "abuse_events",
            select=["event_type", "created_at", "country", "key_id",
                    "details"],
            filters=[("org_id", "eq", org_id)],
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
        EVENT_SUSPEND: "Organization auto-suspended due to unusual activity",
        EVENT_AUTH_IP: f"Access from new location: {row.get('country') or 'unknown'}",
        EVENT_READ_VELOCITY: "Unusual read velocity detected on an API key",
        EVENT_SIGNUP_VELOCITY: f"Signup velocity breach: {details.get('count', '?')} anon signups from {details.get('ip', '?')}",
    }
    at = row.get("created_at")
    return {
        "type": etype,
        "at": at.isoformat() if isinstance(at, datetime) else at,
        "message": messages.get(etype, etype),
    }


# ── Engine ──────────────────────────────────────────────────────────────────

class AbuseEngine:
    """Two-stage rule engine with per-rule flag episodes (delta 13)."""

    def __init__(self, store):
        self.store = store
        # In-process alert dedup (#3631): (org, rule, stage) -> the instant the
        # alert budget for that key re-opens. Bounds ONE process; the durable
        # store remains authoritative for staging and is the cross-replica gate.
        self._last_notified: dict[tuple[str, str, str], datetime] = {}
        self._notify_lock = threading.Lock()

    def _claim_notify(self, org_id: str, rule: str, stage: str,
                      now: datetime, window_s: int) -> bool:
        """True only if no alert of this ``stage`` for ``(org_id, rule)`` was
        emitted within the last ``window_s`` seconds (#3631 target a).

        Keyed by stage as well as ``(org, rule)``: the two-stage machine is
        SUPPOSED to emit a stage-1 flag alert and, a window later, a stage-2
        suspend alert — sharing one key would swallow the escalation. Each
        entry stores its OWN expiry, so pruning never evicts a long-window
        (R2, 24h) claim by a short-window (R1, 1h) caller's clock.
        """
        key = (org_id, rule, stage)
        with self._notify_lock:
            expires = self._last_notified.get(key)
            if expires is not None and expires > now:
                return False
            self._last_notified[key] = now + timedelta(seconds=window_s)
            if len(self._last_notified) > 10_000:
                self._last_notified = {
                    k: exp for k, exp in self._last_notified.items()
                    if exp > now}
            return True

    def _release_notify(self, org_id: str, rule: str, stage: str) -> None:
        """Re-arm the alert budget for a stage when its episode ENDS, so a
        genuinely NEW episode alerts again. The claim is per-EPISODE, not a
        wall-clock cooldown (#3631) — without this release, a burst that
        recycles inside the previous episode's window would re-flag durably but
        stay silent to ops.

        An out-of-band un-suspend (the ``abuse_unsuspend`` RPC writes a
        ``flag_clear`` with no in-process caller here) is not observed until the
        next clean window, so a re-breach before then may be muted for up to
        ``window_s``. That is the deliberate conservative side: releasing on a
        bare ``None`` would re-open the stale-read storm this claim exists to
        stop.
        """
        with self._notify_lock:
            self._last_notified.pop((org_id, rule, stage), None)

    def point_threshold(self) -> int:
        return _int_env("TORTOISE_ABUSE_POINT_THRESHOLD", 500)

    def point_window_s(self) -> int:
        return _int_env("TORTOISE_ABUSE_POINT_WINDOW_S", 3600)

    def key_threshold(self) -> int:
        return _int_env("TORTOISE_ABUSE_KEY_THRESHOLD", 10)

    def key_window_s(self) -> int:
        return _int_env("TORTOISE_ABUSE_KEY_WINDOW_S", 86400)

    def record_point_create(self, org_id: str, n: int = 1,
                            now: datetime | None = None) -> str | None:
        """R1 recording + evaluation. Piggybacks R2 evaluation (delta-13 fix:
        trigger-recorded key_create events have no app request of their own —
        they evaluate on the org's next hooked request)."""
        if abuse_disabled() or not org_id or n <= 0:
            return None
        now = _ensure_aware(_utcnow(now))
        try:
            self.store.record_event(org_id, EVENT_POINT_CREATE, weight=n,
                                    created_at=now)
        except Exception:
            logger.debug("abuse record_point_create failed for %s", org_id)
        r1 = self._evaluate(org_id, EVENT_POINT_CREATE,
                            self.point_threshold(), self.point_window_s(), now)
        r2 = self._evaluate(org_id, EVENT_KEY_CREATE,
                            self.key_threshold(), self.key_window_s(), now)
        return "suspend" if "suspend" in (r1, r2) else (r1 or r2)

    def evaluate_key_creates(self, org_id: str,
                             now: datetime | None = None) -> str | None:
        """R2 evaluation (key_create events land via the DB trigger)."""
        if abuse_disabled() or not org_id:
            return None
        return self._evaluate(org_id, EVENT_KEY_CREATE,
                              self.key_threshold(), self.key_window_s(),
                              _ensure_aware(_utcnow(now)))

    def _evaluate(self, org_id: str, rule: str, threshold: int,
                  window_s: int, now: datetime) -> str | None:
        try:
            total = self.store.window_sum(org_id, rule, window_s, now)
        except Exception:
            logger.debug("abuse window_sum failed for %s/%s", org_id, rule)
            return None
        if total <= threshold:
            # Clean window → end any active flag episode for this rule, so a
            # later burst starts fresh (re-flag, never a stale-flag suspend).
            ended = False
            try:
                if self.store.latest_flag_at(org_id, rule) is not None:
                    self.store.flag_clear(org_id, rule, now=now)
                ended = True
            except Exception:
                logger.debug("abuse flag_clear failed for %s/%s", org_id, rule)
            if ended:
                # Episode CONFIRMED over: re-arm the alert budget so a NEW
                # episode alerts again (#3631 — per-episode, not a wall-clock
                # cooldown). NOT released when the episode-end read/write
                # FAILED — a store failure must reduce alert volume, never
                # increase it, so the claim is left to expire on its own.
                self._release_notify(org_id, rule, EVENT_FLAG)
                self._release_notify(org_id, rule, EVENT_SUSPEND)
            return None
        details = {"rule": rule, "count": total,
                   "threshold": threshold, "window_s": window_s}
        try:
            flagged_at = self.store.latest_flag_at(org_id, rule)
        except Exception:
            # Unreadable anchor → treat as a fresh episode for STAGING, but the
            # alert itself stays bounded by _claim_notify (#3631 target a).
            flagged_at = None
        if flagged_at is None:
            return self._flag(org_id, rule, details, now, window_s)
        flagged_at = _ensure_aware(flagged_at)
        age_s = (now - flagged_at).total_seconds()
        if age_s < window_s:
            return "breach"  # still inside the staging window
        # The flag is a full window old. Stage 2 requires CONTINUITY — at
        # least one rule event between the flag and the current window's
        # start. Without it the breach is a NEW episode after a quiet period:
        # re-flag, never suspend (code-review fix; delta-13 guarantee).
        try:
            continuity = self.store.rule_event_between(
                org_id, rule, flagged_at,
                now - timedelta(seconds=window_s))
        except Exception:
            continuity = True  # fail-safe toward the conservative path
        if not continuity:
            return self._flag(org_id, rule, details, now, window_s)
        try:
            self.store.suspend_org(org_id, details, now=now)
        except Exception:
            logger.debug("abuse suspend_team failed for %s", org_id)
            return "breach"
        mark_suspended(org_id)
        if self._claim_notify(org_id, rule, EVENT_SUSPEND, now, window_s):
            self._notify("abuse_suspended", org_id, details)
        return "suspend"

    def _flag(self, org_id: str, rule: str, details: dict, now: datetime,
              window_s: int) -> str:
        """Stage-1 flag. The notification is CONTINGENT on the flag having
        been persisted (#3631 target b): a store write failure must reduce
        alert volume, never increase it — the pre-fix swallow notified on
        every evaluation, because a never-persisted flag also reads as None.

        The alert is ALSO bounded to once per ``(org, rule)`` per staging
        window (#3631 target a) even when the durable anchor is unreliable —
        a read that returns a stale ``None`` (replica lag) or raises would
        otherwise re-flag and re-notify on every evaluation. Staging is still
        store-authoritative; this bounds the ALERT only.
        """
        try:
            self.store.flag_org(org_id, rule, details, now=now)
        except Exception:
            logger.warning(
                "abuse: flag NOT persisted for %s/%s — suppressing the abuse "
                "notification (a store failure must never amplify alerts, "
                "#3631)", org_id, rule)
            return "flag"
        if not self._claim_notify(org_id, rule, EVENT_FLAG, now, window_s):
            return "flag"  # alert budget for this (org, rule)/window is spent
        self._notify("abuse_flag", org_id, details)
        return "flag"

    def _notify(self, kind: str, org_id: str, details: dict) -> None:
        try:
            from tortoise.notify import notify_abuse
            notify_abuse(kind,
                         {"org_id": org_id,
                          "email": _org_email(self.store, org_id)},
                         {**details, "appeal_url": appeal_url()})
        except Exception:
            logger.debug("abuse notify failed (%s, %s)", kind, org_id)


# ── R3: read-velocity tracker (in-memory, notify-only) ─────────────────────

class ReadVelocityTracker:
    """>100 reads / 5min per-key OR per-org → notify once per window.

    In-memory by design (the 5-min window bounds deploy-reset damage);
    notify-only per the issue — R3 never suspends. The notification goes to
    the ops Telegram channel (abuse alerts are Telegram-only since #3639, so
    there is no email recipient to resolve); a best-effort read_velocity event
    row surfaces the alert in the dashboard list.
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_READ_THRESHOLD", 100)
        self.window_s = window_s if window_s is not None else _int_env(
            "TORTOISE_ABUSE_READ_WINDOW_S", 300)
        self._by_key: dict[str, list[float]] = defaultdict(list)
        self._by_org: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def record_read(self, key_id: str | None, org_id: str | None,
                    now: float | None = None) -> tuple[str, str] | None:
        """Returns ('key'|'org', id) on breach, else None. Notify dedup is
        once per window per (scope, id)."""
        if abuse_disabled():
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            if key_id:
                kb = self._by_key[key_id]
                kb[:] = [t for t in kb if t > cutoff]
                kb.append(now)
                if len(kb) > self.threshold:
                    breach = ("key", key_id)
            if org_id:
                tb = self._by_org[org_id]
                tb[:] = [t for t in tb if t > cutoff]
                tb.append(now)
                if breach is None and len(tb) > self.threshold:
                    breach = ("team", org_id)
            # bound memory growth (mirrors _register_buckets hygiene)
            if len(self._by_key) > 10_000:
                self._by_key = {k: v for k, v in self._by_key.items()
                                if any(t > cutoff for t in v)}
            if len(self._by_org) > 10_000:
                self._by_org = {k: v for k, v in self._by_org.items()
                                 if any(t > cutoff for t in v)}
            # prune the notify-dedup map too (code-review P3)
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(breach)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[breach] = now
        if breach is not None:
            self._notify(breach, org_id)
        return breach

    def _notify(self, breach: tuple[str, str], org_id: str | None) -> None:
        scope, ident = breach
        # Dashboard alert row (best-effort; same dedup gate as the notify)
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if org_id:
                store.record_event(org_id, EVENT_READ_VELOCITY,
                                   details={"scope": scope, "id": ident})
        except Exception:
            logger.debug("read-velocity event record failed (%s)", breach)
        try:
            from tortoise.notify import notify_abuse
            notify_abuse("abuse_read_velocity",
                         {"org_id": org_id,
                          "email": _org_email(store, org_id)},
                         {"scope": scope, "id": ident,
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("read-velocity notify failed (%s)", breach)


READ_TRACKER = ReadVelocityTracker()


def record_read(key_id: str | None, org_id: str | None,
                now: float | None = None):
    """Module-level seam (monkeypatchable) over the shared tracker."""
    return READ_TRACKER.record_read(key_id, org_id, now)


# ── R8: signup-velocity tracker (in-memory, notify-only) ───────────────────

class SignupVelocityTracker:
    """>N anonymous signups per IP per window → notify ops once per window.

    Anon orgs have NULL user_id, so R3/R4 owner-notify resolves nothing —
    R8 is the OPS-visible farming signal (abuse alerts are Telegram-only since
    #3639, so the anon case needs no recipient resolution at all). In-memory
    by design (mirrors
    ReadVelocityTracker/R3): R8 NEVER suspends, so deploy-reset damage is
    bounded to a notify. The durable multi-instance sweeper over audit_events
    is a documented follow-on (idx_audit_ip_time ships in #1081; sweeper
    contract in docs/scoping/scoping-1081-agent-signup-abuse.md).

    Two feeds:
    - record_signup  — SUCCESSFUL mint path (consensus: blocked farmers must
      not inflate the success count with attempts). Breach on >= threshold:
      threshold = allowance (2/24h) so the feed fires exactly when an IP
      consumes its entire anonymous allowance (the designed review signal).
    - record_block   — the signup limiter's 429 (the unmistakable farming
      evidence). Same dedup key as the success feed (bare ip) — one ops
      email per (ip, window), never two (P1-FIX-2).
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_SIGNUP_THRESHOLD",
            _int_env("TORTOISE_SIGNUP_IP_LIMIT", 2))  # P3-5: defaults follow allowance
        self.window_s = window_s if window_s is not None else _int_env(
            "TORTOISE_ABUSE_SIGNUP_WINDOW_S", 86400)
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[str, float] = {}  # bare ip -> last notify ts
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Test seam: clear per-IP counts and dedup state (P1-FIX-10 —
        module-scoped TestClient shares one host across tests)."""
        with self._lock:
            self._by_ip.clear()
            self._notified.clear()

    def record_signup(self, ip: str | None, org_id: str | None = None,
                      now: float | None = None) -> tuple[str, str] | None:
        """Success-path feed: count minted orgs per IP per window.
        Returns ('ip', ip) on breach (len >= threshold), else None. Notify
        dedup once per window per IP (bare-ip key, shared with block path)."""
        if abuse_disabled() or not ip:
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            bucket = self._by_ip[ip]
            bucket[:] = [t for t in bucket if t > cutoff]
            bucket.append(now)
            if len(bucket) >= self.threshold:
                breach = ("ip", ip)
            if len(self._by_ip) > 10_000:
                # re-wrap: a dict-comprehension replace would lose defaultdict
                # semantics and KeyError on NEW ips after the prune (caught by
                # test_memory_bound — 10,100 distinct IPs)
                self._by_ip = defaultdict(
                    list, {k: v for k, v in self._by_ip.items()
                           if any(t > cutoff for t in v)})
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(ip)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[ip] = now
        if breach is not None:
            self._notify("velocity", ip, org_id, {"count": len(bucket)})
        return breach

    def record_block(self, ip: str | None, org_id: str | None = None,
                     now: float | None = None) -> None:
        """Block-path feed: the signup limiter 429'd this IP. Same dedup key
        (bare ip) as the success feed — the 429 after a 2-mint allowance is
        dedup-suppressed (one email per episode, P1-FIX-2)."""
        if abuse_disabled() or not ip:
            return
        now = now if now is not None else time.time()
        with self._lock:
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            last = self._notified.get(ip)
            if last is not None and now - last < self.window_s:
                return
            self._notified[ip] = now
        self._notify("blocked", ip, org_id, {})

    def _notify(self, reason: str, ip: str, org_id: str | None,
                details: dict) -> None:
        # P4-FIX: payload carries count ALWAYS (block path details={} → count
        # 0 is fine; _alert_dict reads details.get('count')).
        details = dict(details)  # do not mutate caller's dict
        details.setdefault("count", 0)
        # Dashboard alert row (best-effort; minted org anchors the FK).
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if org_id:
                store.record_event(
                    org_id, EVENT_SIGNUP_VELOCITY,
                    details={"ip": ip, "reason": reason, **details})
        except Exception:
            logger.debug("signup-velocity event record failed (%s)", ip)
        try:
            from tortoise.notify import notify_abuse
            # org_id is None for anon orgs; the Telegram alert needs no
            # recipient resolution (#3639 — abuse alerts are Telegram-only)
            notify_abuse("abuse_signup_velocity",
                         {"org_id": org_id, "email": None},
                         {"ip": ip, "reason": reason,
                          "count": details.get("count", 0),
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("signup-velocity notify failed (%s)", ip)


SIGNUP_TRACKER = SignupVelocityTracker()


def record_signup(ip: str | None, org_id: str | None = None,
                  now: float | None = None) -> tuple[str, str] | None:
    """Module-level seam (monkeypatchable) over the shared tracker."""
    return SIGNUP_TRACKER.record_signup(ip, org_id, now)


def record_signup_block(ip: str | None, org_id: str | None = None,
                        now: float | None = None) -> None:
    """Module-level seam for the 429 path."""
    SIGNUP_TRACKER.record_block(ip, org_id, now)


class RecoveryVelocityTracker:
    """>N keyless recoveries per IP per window → notify ops once per window.

    #1709: the keyless-recovery surface (POST /v1/agent/recover + the
    token-present signup branch) mints keys WITHOUT an authenticated identity
    — the signup-velocity feed (record_signup) must not conflate recoveries
    with mints (ops metrics + alert thresholds differ). This tracker mirrors
    SignupVelocityTracker: in-memory by design (never suspends — deploy-reset
    damage bounded to a notify), one notify per (ip, window) on breach.

    Fired on SUCCESSFUL recovery mints only (possession-authenticated); the
    per-IP + per-token attempt limiters handle the probe case (uniform 422).
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_RECOVER_THRESHOLD",
            _int_env("TORTOISE_RECOVER_IP_LIMIT", 5))
        self.window_s = window_s if window_s is not None else _int_env(
            "TORTOISE_ABUSE_RECOVER_WINDOW_S", 86400)
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[str, float] = {}  # bare ip -> last notify ts
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Test seam: clear per-IP counts and dedup state."""
        with self._lock:
            self._by_ip.clear()
            self._notified.clear()

    def record(self, ip: str | None, org_id: str | None = None,
               now: float | None = None) -> tuple[str, str] | None:
        """Success-path feed: count recovery mints per IP per window.
        Returns ('ip', ip) on breach (len >= threshold), else None."""
        if abuse_disabled() or not ip:
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            bucket = self._by_ip[ip]
            bucket[:] = [t for t in bucket if t > cutoff]
            bucket.append(now)
            if len(bucket) >= self.threshold:
                breach = ("ip", ip)
            if len(self._by_ip) > 10_000:
                self._by_ip = defaultdict(
                    list, {k: v for k, v in self._by_ip.items()
                           if any(t > cutoff for t in v)})
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(ip)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[ip] = now
        if breach is not None:
            self._notify("velocity", ip, org_id, {"count": len(bucket)})
        return breach

    def _notify(self, reason: str, ip: str, org_id: str | None,
                details: dict) -> None:
        details = dict(details)
        details.setdefault("count", 0)
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if org_id:
                store.record_event(
                    org_id, EVENT_RECOVERY_VELOCITY,
                    details={"ip": ip, "reason": reason, **details})
        except Exception:
            logger.debug("recovery-velocity event record failed (%s)", ip)
        try:
            from tortoise.notify import notify_abuse
            notify_abuse("abuse_recovery_velocity",
                         {"org_id": org_id, "email": None},
                         {"ip": ip, "reason": reason,
                          "count": details.get("count", 0),
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("recovery-velocity notify failed (%s)", ip)


RECOVERY_TRACKER = RecoveryVelocityTracker()


def record_recovery(ip: str | None, org_id: str | None = None,
                    now: float | None = None) -> tuple[str, str] | None:
    """Module-level seam (monkeypatchable) over the shared recovery tracker."""
    return RECOVERY_TRACKER.record(ip, org_id, now)


# ── R4: geo (CF-IPCountry header, fail-open) ───────────────────────────────

_GEO_CACHE: dict[str, tuple[float, set[str]]] = {}
_GEO_TTL_S = 86400
_GEO_LOCK = threading.Lock()
# per-org geo-notify flood cap (CF-IPCountry is spoofable on Fly-direct —
# security review): at most N new-country notifications per org per 24h.
_GEO_NOTIFY_MAX_PER_DAY = 10
_GEO_NOTIFIED: dict[str, list[float]] = defaultdict(list)


def resolve_country(headers) -> str | None:
    """CF-IPCountry passthrough (Fly/Cloudflare set it). Fail-open: no header
    → None → R4 inactive. IPINFO_TOKEN resolver is a documented follow-on."""
    try:
        value = headers.get("cf-ipcountry") if headers else None
    except Exception:
        return None
    value = (value or "").strip()
    return value.upper() or None


def check_new_country(org_id: str, country: str | None, store,
                      now: float | None = None) -> bool:
    """True when the country is new for the org (records auth_ip + notifies
    ops, flood-capped). Seen-set cached in-process (24h TTL); durable lookup
    on cache miss."""
    if abuse_disabled() or not org_id or not country:
        return False
    now = now if now is not None else time.time()
    with _GEO_LOCK:
        cached = _GEO_CACHE.get(org_id)
        if cached is None or now - cached[0] > _GEO_TTL_S:
            try:
                seen = set(store.seen_countries(org_id))
            except Exception:
                return False  # fail-open: unseen-set unavailable → inactive
            cached = (now, seen)
            _GEO_CACHE[org_id] = cached
            # evict other expired entries (bounded memory)
            expired = [t for t, (ts, _) in _GEO_CACHE.items()
                       if now - ts > _GEO_TTL_S]
            for t in expired:
                _GEO_CACHE.pop(t, None)
                _GEO_NOTIFIED.pop(t, None)
        seen = cached[1]
        if country in seen:
            return False
        # flood cap before recording anything
        stamps = [t for t in _GEO_NOTIFIED[org_id] if now - t < _GEO_TTL_S]
        _GEO_NOTIFIED[org_id] = stamps
        if len(stamps) >= _GEO_NOTIFY_MAX_PER_DAY:
            seen.add(country)  # still track it; just don't notify/event
            return False
    try:
        store.record_event(org_id, EVENT_AUTH_IP, country=country)
    except Exception:
        # Unrecorded → not marked seen → retried on a later request (and
        # NOT notified: a notify without a durable event would re-fire on
        # every request until the store recovers — code-review P3).
        logger.debug("geo event record failed for %s", org_id)
        return False
    with _GEO_LOCK:
        cached2 = _GEO_CACHE.get(org_id)
        if cached2 is not None:
            cached2[1].add(country)
        _GEO_NOTIFIED[org_id].append(now)
    try:
        from tortoise.notify import notify_abuse
        notify_abuse("abuse_new_ip",
                     {"org_id": org_id,
                      "email": _org_email(store, org_id)},
                     {"country": country, "appeal_url": appeal_url()})
    except Exception:
        logger.debug("geo notify failed for %s", org_id)
    return True


def reset_geo_cache() -> None:
    with _GEO_LOCK:
        _GEO_CACHE.clear()
        _GEO_NOTIFIED.clear()


# ── Engine singleton seam ───────────────────────────────────────────────────

_engine: AbuseEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> AbuseEngine:
    """Lazy process-wide engine (tests monkeypatch or set_engine())."""
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
