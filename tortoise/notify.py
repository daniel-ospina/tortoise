"""Billing notifications — Resend email + Telegram, both best-effort.

#310 (Stripe Billing) user decision 2026-08-08: notify the ops channel on
Stripe events via BOTH channels — Resend email (premiselabs.co domain,
sending-restricted key) and Telegram (@Premislabs_notifications_bot). A
notification failure must NEVER block or fail the webhook — every channel is
wrapped in try/except and routed through ``redact_error``; this module never
raises.

Channels are gated on their secrets being set in env:
- Resend:   RESEND_API_KEY (Bearer), BILLING_NOTIFY_TO (recipient inbox)
- Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Absent secret → channel skipped (logged once per process).

Channel scope by kind (#3639, user decision 2026-09-16): **billing** events keep
BOTH channels (the #310 decision above stands). **Abuse/security** events are
TELEGRAM ONLY — the email leg was removed because abuse notifications bypassed
the shared Resend send budget, so a flag storm consumed the entire
transactional quota and starved user-facing email. There is deliberately no
email fallback: a Telegram outage means a lost abuse alert. The billing email
leg now reserves from that SAME shared budget (``email_notify.reserve_send_slot``,
#3631) instead of bypassing it — abuse is the only kind with no Resend leg.

Abuse DELIVERY (``notify_abuse``) is split into two registers (#14407), because
"how a message reaches a human" and "what record it belongs to" were two
different decisions and had drifted apart:

* :data:`ABUSE_INCIDENT_KINDS` — a STATE CHANGE. The message belongs to the
  incident lifecycle, so the store's create-once path files the issue AND emits
  the episode's first push, and the message names the issue. While the episode
  stays OPEN it re-alarms at most once per
  :data:`_INCIDENT_DELIVERY_WINDOW_S`, naming the same issue; the engine closes
  the incident and re-arms delivery at episode end.
* :data:`ABUSE_ACTIVITY_KINDS` — a MEASUREMENT (#10156's ACTIVITY register). It
  files nothing **by design**, and every message says so; delivery is a bounded
  digest rather than one message per detector window.

When no incident channel can be built at all, an incident class is still
delivered — bounded, and labelled with the reason. A missing tracker must never
mute a security signal (#4872).

Sender identity (#1136): the Resend sender comes from RESEND_FROM_EMAIL — the
single managed sender identity shared with the transactional invite sender
(email_notify.py) — so one domain/identity is managed in env
(deploy-hosted.yml + .env.example). BILLING_FROM_EMAIL overrides it when a
distinct billing sender is desired (the pre-#1136 hardcoded
billing@premiselabs.co).
"""
from __future__ import annotations  # noqa: I001

import logging
import os
import threading
import time
from datetime import datetime, timezone

import httpx
from tortoise.alert_store import CloseCooldown, OpenOutcome, ResolveOutcome
from tortoise.incident_kinds import (
    KIND_ABUSE_FLAG_RAISED,
    KIND_ABUSE_REVIEW_NEEDED,
    KIND_ABUSE_SUSPENDED,
    KIND_BILLING_SEND_FAILED,
)
from tortoise.telegram_push import send_message as telegram_send  # noqa: E402, RUF100

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM_ADDRESS = "noreply@premiselabs.co"  # single managed sender identity (#1136)

# Kinds mirror the audit/analytics event names (billing_upgrade, billing_downgrade,
# billing_payment_failed, billing_cancel). ⛔ This is the NOTIFICATION-EVENT
# vocabulary, not the incident-kind vocabulary: it names the business EVENT
# whose delivery failed, while ``tortoise.incident_kinds`` names the incident
# that a failed delivery FILES. The two overlap on exactly two members
# (``abuse_review_needed`` / ``abuse_suspended``) and are otherwise disjoint on
# purpose — see ``_BILLING_SEND_FAILED_KIND`` below.
KINDS = {"billing_upgrade", "billing_downgrade", "billing_payment_failed", "billing_cancel",
         # #308: abuse prevention notifications
         "abuse_flag", KIND_ABUSE_SUSPENDED, "abuse_new_ip", "abuse_read_velocity",
         # #5425: the engine no longer auto-suspends; a persistent breach alerts
         # for HUMAN review instead.
         KIND_ABUSE_REVIEW_NEEDED,
         # #1081: R8 signup-velocity (anon signups per IP per window)
         "abuse_signup_velocity",
         # #1709: recovery-velocity (keyless recovery mints per IP per window)
         "abuse_recovery_velocity"}

# Ops incident KINDs (the alert_store sink) — deliberately NOT members of KINDS.
# KINDS names business EVENTS; these name a failed DELIVERY of one. Keeping them
# separate stops an egress outage from being deduped against a billing event.
# The VALUE comes from the one declaration site (#4780), because it is an R2
# dedup key.
_BILLING_SEND_FAILED_KIND = KIND_BILLING_SEND_FAILED

#: #14407 — the abuse classes whose human alert is a property of the
#: INCIDENT, not of the detection. Each one files (or reuses) a durable,
#: assignable incident, and the Telegram message is emitted by the store's
#: create-once path — so ONE incident is ONE message for its whole lifecycle.
#: Pre-#14407 the push was emitted per DETECTION here and ``abuse_flag`` filed
#: nothing at all, which is how an alert arriving in the owner's chat could be
#: attributed by no issue, no runbook and no search.
ABUSE_INCIDENT_KINDS: frozenset[str] = frozenset({
    "abuse_flag",
    KIND_ABUSE_SUSPENDED,
    KIND_ABUSE_REVIEW_NEEDED,
})
#: #14407 — the abuse classes that are MEASUREMENTS, not incidents.
#:
#: This is a DECLARED non-filing decision, not a gap: #10156 split the dashboard
#: into two registers and put R3/R4/R8 in ``REGISTER_ACTIVITY`` — *"a detector
#: OBSERVED a rate. It measured; it changed nothing. Informational, muted, no
#: verb."* Filing a GitHub incident for a rate blip is the exact framing #10156
#: removed, so these classes file nothing BY DESIGN and every message says so.
#: Their delivery is a DIGEST (:data:`_ACTIVITY_DELIVERY_WINDOW_S`), because the
#: detector windows are minutes wide — one message per window is what made the
#: human channel "out of control" while the detector was working correctly.
ABUSE_ACTIVITY_KINDS: frozenset[str] = frozenset({
    "abuse_read_velocity",
    "abuse_signup_velocity",
    "abuse_recovery_velocity",
    "abuse_new_ip",
})

#: notification-EVENT name → the INCIDENT kind it files (#14407).
#:
#: The two vocabularies are deliberately disjoint: :data:`KINDS` names the
#: business EVENT whose detector fired, ``incident_kinds`` names the durable
#: incident and is the R2 dedup key. Two members are shared spellings (the
#: grandfathered ``abuse_*`` incident kinds); ``abuse_flag`` is not — its
#: incident kind is the SCREAMING_CASE ``ABUSE_FLAG_RAISED`` added for this
#: change, because the incident-kind convention (post-#4780) allows a new kind
#: only in that form. Keeping the mapping explicit means a reader sees the event
#: and the incident as the two things they are.
_INCIDENT_KIND_OF_EVENT: dict[str, str] = {
    "abuse_flag": KIND_ABUSE_FLAG_RAISED,
    KIND_ABUSE_SUSPENDED: KIND_ABUSE_SUSPENDED,
    KIND_ABUSE_REVIEW_NEEDED: KIND_ABUSE_REVIEW_NEEDED,
}

#: The incident kinds the episode-end resolver clears — the two classes a RULE's
#: flag episode can file. ``abuse_suspended`` is deliberately NOT here: it has no
#: automatic writer (``#5425`` deleted the automatic suspension), so it records an
#: operator action rather than an episode, and a clean window must not resolve a
#: record of a decision a human made.
_EPISODE_INCIDENT_KINDS: tuple[str, ...] = (
    KIND_ABUSE_FLAG_RAISED, KIND_ABUSE_REVIEW_NEEDED)


def _episode_delivery_kinds() -> tuple[str, ...]:
    """The DELIVERY-LEDGER spellings an episode end must re-arm (#14407).

    The ledger is armed by the notification **event** (``_claim_delivery(kind,
    ...)`` in ``_notify_abuse_incident`` — ``"abuse_flag"``) while the resolver
    works in the **incident** vocabulary (``ABUSE_FLAG_RAISED``). For the two
    classes whose spellings coincide (the grandfathered lowercase
    ``abuse_review_needed``/``abuse_suspended``) the two vocabularies are the
    same string and the release worked by luck; for ``abuse_flag`` it did not,
    so the window armed by an episode was NEVER released and the next episode
    folded into it — up to a full window of silence for a genuinely new
    episode. That is invisible in the store path (the store's create-once push
    carries the opening message) and complete in the no-incident-channel shape,
    where this ledger is the only delivery there is.

    Returning BOTH vocabularies per incident kind costs one dict walk and makes
    the release independent of which spelling a caller used — the bound is the
    episode (``#4872``/R2), and an episode end must never leave a stale window
    behind in any spelling.
    """
    kinds = list(_EPISODE_INCIDENT_KINDS)
    kinds.extend(event for event, incident in _INCIDENT_KIND_OF_EVENT.items()
                 if incident in _EPISODE_INCIDENT_KINDS and event not in kinds)
    return tuple(kinds)

#: Delivery windows (#14407). One message per ``(kind, subject)`` per window.
#:
#: INCIDENT kinds: the normal path is the store's create-once lifecycle — the
#: episode's first message is the store's own push, and this window is the
#: RE-ALARM bound while the episode stays open (the `DEDUP` outcome). 24h keeps a
#: persistent breach from going silent without paging a human every window: the
#: pre-fix code re-paged hourly (``_claim_notify``'s window expiry) for a breach
#: that was already tracked by ONE issue, so a single org over its R1 threshold
#: cost ~24 messages/day. The engine releases the window when it OBSERVES the
#: episode end (the clean window that persisted `flag_clear`, or its bounded
#: retry), so a genuinely new episode pages at once. DECLARED RESIDUAL: an episode
#: end the engine cannot observe — the `abuse_unsuspend` SQL function, a manual
#: `flag_clear`, a death between the clear and the release — leaves the window
#: armed, so a new episode inside it is folded and its reminder fires at the
#: window's end naming the open issue. That is deliberately NOT inferred from a
#: `latest_flag_at() is None` read: the same read returns None for a flag this
#: process just wrote when the read is stale (#3631), and releasing on it re-pages
#: per detection and can close the incident of an org still over threshold. The
#: runbook row names the manual close. The same window bounds the
#: no-incident-channel fallback, where it is the only bound there is.
#:
#: ACTIVITY kinds: the collapse window for the digest. 6h is chosen against the
#: detector cadence — R3 re-arms every 5 minutes (288 detections/day at
#: defaults) and R4/R8 are daily — so 6h bounds one SUBJECT to 4 messages/day
#: while still surfacing a fresh burst immediately (the FIRST detection of a
#: window is delivered at once; only the repeats are folded).
#:
#: The ledger key is ``(kind, subject)``, and the subject is the ORG (``"_"``
#: when the detection carried none) — NOT an R3 key scope and not an R8 client
#: IP. Stated explicitly because the earlier wording said "per scope" and the
#: code has never keyed that way: one org's five breaching keys share ONE digest,
#: and every keyless R8/R4/R3 source platform-wide shares the ``"_"`` subject.
#: That is over-folding, never a mute, and the folded SOURCES are carried into
#: the digest and named there (bounded by `_MAX_FOLDED_IDENTITIES`) instead of
#: the key being per-client — per-client keying would page once per IP under a
#: distributed burst, which is the storm, not the bound.
_INCIDENT_DELIVERY_WINDOW_S = 86400.0
_ACTIVITY_DELIVERY_WINDOW_S = 21600.0

_skip_logged: set[str] = set()

#: The delivery ledger: ``(kind, subject) -> (window_deadline, folded_count,
#: folded_identities, identity_overflow)``. In-process like the engine's own alert
#: claim (#3631): the honest ceiling across replicas is one message per replica
#: per window, and shared state for a cosmetic bound is deliberately out of scope.
#: #14407.
_delivery_lock = threading.Lock()
#: entry: (deadline, folded_count, folded_identities, identities_over_cap)
_delivery: dict[tuple[str, str], tuple[float, int, tuple[str, ...], int]] = {}
_delivery_now = time.monotonic  # test seam (monotonic: no clock-jump aliasing)
_MAX_DELIVERY_KEYS = 4096
#: How many DISTINCT folded sources a digest NAMES (#14407 round 5). A digest
#: that collapses a distributed burst into one message must not also throw the
#: attackers away: the folded sources are carried, bounded so the message stays
#: finite, with the overflow counted.
_MAX_FOLDED_IDENTITIES = 8

# Budget-skip billing incidents are informational and the budget stays exhausted
# for the rest of the UTC day: file at most one per (kind, org) per process per
# day. AlertStore dedups the ISSUE but still pays an uncached GitHub read per
# call, and this branch is reached on every Stripe billing event while the
# budget is spent (#3631).
_incident_day: dict[tuple[str, str], str] = {}


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _from_address() -> str:
    """Resend sender for billing notifications (#1136).

    RESEND_FROM_EMAIL is the single managed sender identity (same env as the
    transactional invite sender). BILLING_FROM_EMAIL overrides it when a
    distinct billing sender is desired. Falls back to the default identity.
    """
    return _env("BILLING_FROM_EMAIL") or _env("RESEND_FROM_EMAIL") or DEFAULT_FROM_ADDRESS


def _skip_channel(channel: str, secret: str) -> bool:
    if secret is None:
        if channel not in _skip_logged:
            _skip_logged.add(channel)
            logger.warning("billing notify: %s channel skipped — secret not set", channel)
        return True
    return False


def file_incident(kind: str, org_id: str = "", detail: dict | None = None) -> bool:
    """File (or REUSE) an ops incident on the shared sink. NEVER raises.

    The swallow is the point: every caller is a FAILURE path that must still
    complete (a best-effort send leg), so a dead alert channel — no
    ``DR_ISSUES_PAT``, an unusable object store, or R2/GitHub/Telegram down —
    degrades to the log line the caller already wrote rather than propagating.
    There is no sink-disable switch to list as a separate off state: after
    #4778 the only "off" state is "no channel", described on
    ``_incident_alert_store`` at the call site below. Returns True only when
    THIS call was the
    filer; a dedup hit returns False, which is not an error.

    Dedup is ``AlertStore.open_incident``'s per-``(kind, org_id)`` create-once
    object, so a repeated failure of the same channel files ONE issue; recovery
    deletes the object (delete-to-resolve), so a later outage is a NEW incident.
    That is why repeated failures must not be filed by the caller — pass a
    SUBJECT that identifies the outage, not the individual send.

    Mirrors the abuse-incident call site below (``abuse_suspended`` /
    ``abuse_review_needed`` — same ``_incident_alert_store`` → ``open_incident``
    path), factored out because more than one send leg now needs it. The hosted_api
    import is function-level for the reason stated at that call site: hosted_api
    imports notify, so a module-level import would cycle.
    """
    try:
        from tortoise import hosted_api as _ha
        # #4778: the ALERT-only policy seam — NOT the sweep-gated raw chain.
        # A billing-notify failure is a NON-backup path, so its incident must be
        # VISIBLE on a deployment whose backups are off (the production
        # default), which is exactly what building the channel from
        # `_backup_config_safe()` (None whenever `BACKUP_SWEEP_ENABLED` is off)
        # prevented.
        store = _ha._incident_alert_store()
        if store is None:
            return False  # no channel — the caller's own log is the record
        return bool(store.open_incident(kind, org_id, detail or {}))
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("notify: %s incident filing failed (%s)", kind, redact_safe(e))
        return False


def _file_incident_once_a_day(kind: str, org_id: str, detail: dict) -> None:
    """`file_incident` at most once per ``(kind, org_id)`` per UTC day."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017
    key = (kind, org_id)
    if _incident_day.get(key) == day:
        return
    _incident_day[key] = day
    file_incident(kind, org_id, detail)


def _email_text(kind: str, org: dict, details: dict) -> str:
    tier = details.get("tier", org.get("tier", "?"))
    lines = [
        f"Tortoise Billing — {kind}",
        "",
        f"Team: {org.get('name', org.get('org_id', '?'))} (id {org.get('org_id', '?')})",
        f"Tier: {tier}",
    ]
    if details.get("subscription_status"):
        lines.append(f"Subscription status: {details['subscription_status']}")
    if details.get("message"):
        lines.append(f"Detail: {details['message']}")
    if details.get("grace_until"):
        lines.append(f"Grace until: {details['grace_until']}")
    return "\n".join(lines)


def _telegram_text(kind: str, org: dict, details: dict) -> str:
    tier = details.get("tier", org.get("tier", "?"))
    parts = [f"💰 Tortoise Billing: {kind}", f"Team: {org.get('name', org.get('org_id', '?'))} | Tier: {tier}"]
    if details.get("subscription_status"):
        parts.append(f"Status: {details['subscription_status']}")
    if details.get("message"):
        parts.append(details["message"])
    return "\n".join(parts)


def _send_resend(api_key: str, to: str, subject: str, html: str) -> None:
    """POST to Resend /emails via httpx. Raises on failure (caller swallows)."""
    resp = httpx.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": _from_address(), "to": [to], "subject": subject, "html": html},
        timeout=15.0,
    )
    resp.raise_for_status()


def notify_billing_event(kind: str, org: dict, details: dict | None = None) -> None:
    """Send a billing notification over both channels. NEVER raises.

    kind must be in KINDS. org is the Org node dict (name/org_id/tier).
    details may carry subscription_status / message / grace_until / tier.
    """
    if kind not in KINDS:
        logger.warning("billing notify: unknown kind %r ignored", kind)
        return
    details = details or {}

    api_key = _env("RESEND_API_KEY")
    to = _env("BILLING_NOTIFY_TO")
    if not _skip_channel("resend", api_key) and not _skip_channel("resend-recipient", to):
        # #3631: billing email shares the Resend account with transactional
        # email, so it reserves from the SAME send budget (not a second one) —
        # an uncounted billing storm could otherwise starve invites/OTPs. The
        # import is function-level (email_notify imports file_incident from
        # here, so a module-level import would cycle), and BOTH it and the
        # reservation are guarded: this function's never-raise contract is
        # load-bearing on the Stripe webhook, which has already claimed its
        # event marker by the time it calls us.
        reason: str | None = None
        try:
            from tortoise.email_notify import refund_send_slot, reserve_send_slot
            reason = reserve_send_slot()
        except Exception as e:  # noqa: BLE001, RUF100
            reason = f"send-budget guard unavailable ({redact_safe(e)})"
        if reason is not None:
            logger.warning("billing notify: resend SKIPPED — %s", reason)
            # The webhook has consumed its idempotency marker, so a dropped
            # billing alert cannot re-fire — surface it on the same deduped
            # incident the provider-failure path uses (once per day, see
            # _file_incident_once_a_day).
            _file_incident_once_a_day(_BILLING_SEND_FAILED_KIND, "", {
                "channel": "resend",
                "event_kind": kind,
                "org_id": org.get("org_id", "?"),
                "reason": reason,
            })
        else:
            try:
                subject = f"Tortoise Billing — {kind}"
                body = _email_text(kind, org, details).replace("\n", "<br>")
                _send_resend(api_key, to, subject, f"<pre>{body}</pre>")
            except Exception as e:  # noqa: BLE001, RUF100
                refund_send_slot()  # provider rejected/failed — free the slot
                logger.warning("billing notify: resend failed (%s)", redact_safe(e))
                # Ops incident (GH issue + Telegram) — a billing notification that
                # never left the building was previously visible only in a log
                # line. Platform subject ("") on purpose: ONE Resend account serves
                # every team, so keying by team would file one issue per affected
                # team for a single outage. The team is still in the detail.
                file_incident(_BILLING_SEND_FAILED_KIND, "", {
                    "channel": "resend",
                    "event_kind": kind,
                    "org_id": org.get("org_id", "?"),
                    "error": redact_safe(e),
                })

    bot_token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not _skip_channel("telegram", bot_token) and not _skip_channel("telegram-chat", chat_id):
        try:
            telegram_send(bot_token, chat_id, _telegram_text(kind, org, details))
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("billing notify: telegram failed (%s)", redact_safe(e))


def _abuse_alert_store():
    """The abuse path's incident channel: ``(store, reason-if-absent)``.

    #4778's ALERT-only policy seam, never the sweep-gated raw chain: the abuse
    path is a REQUEST-path decision with nothing to do with the backup sweep, so
    gating on ``_backup_config_safe()`` (None whenever ``BACKUP_SWEEP_ENABLED``
    is off — the production default) made abuse invisible on exactly the
    deployments that do not run backups.

    The REASON is returned because #14407's invariant is that a notification is
    attributable — a filed incident, **or** an explicit reason the class could
    not file one. A bare ``None`` (which is what every caller got before) is
    exactly the silent asymmetry that made these messages unattributable.
    Function-level import: notify must never import hosted_api at module level
    (hosted_api imports notify).
    """
    try:
        from tortoise import hosted_api as _ha
        store = _ha._incident_alert_store()
    except Exception as e:  # noqa: BLE001, RUF100
        return None, f"alert channel unavailable ({redact_safe(e)})"
    if store is None:
        return None, _channel_absent_reason()
    return store, ""


def _channel_absent_reason() -> str:
    """WHY no incident channel exists — the leg that is actually missing (#14407).

    ``_incident_alert_store`` returns ``None`` for two physically different
    reasons (its own D6 residual, ``hosted_api``): no issue filer, or no usable
    object store. Reporting every absence as "``DR_ISSUES_PAT`` unset" sends an
    operator to the wrong credential when the ``R2_*`` set is the broken one —
    and this string is rendered into the message a human acts on, so being wrong
    here costs the fix. Names only, never values (``#4010``).
    """
    if not _env("DR_ISSUES_PAT"):
        return "no issue filer configured (DR_ISSUES_PAT unset)"
    missing = [n for n in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                           "R2_SECRET_ACCESS_KEY", "R2_BUCKET") if not _env(n)]
    if missing:
        return ("no usable object store for the incident sentinel "
                f"({', '.join(missing)} unset)")
    # Both legs look configured and the builder still refused: do not guess.
    return "alert channel unavailable (see the service log)"


def _abuse_subject(org_id: str, rule: object) -> str:
    """The incident subject for an abuse class — RULE-SCOPED.

    #2313 established the shape (``{org}:{graph}``): a per-rule key, because the
    incident BODY is create-once, so a subject shared between R1 and R2 would let
    the first rule to fire own the record for the whole lifecycle of the second —
    the same defect #4872 separated ``ABUSE_DECISION_FAULT`` from
    ``ABUSE_ENFORCEMENT_FAULT`` to avoid. A caller with no rule keeps the org
    alone (the operator-suspension path).
    """
    name = rule.strip() if isinstance(rule, str) else ""
    # STRIPPED, not just validated: "point_create " and "point_create" would
    # otherwise be two create-once points for ONE rule — the #2844 alias class this
    # scoped subject exists to remove.
    return f"{org_id}:{name}" if name else org_id


def _claim_delivery(kind: str, subject: str, window_s: float,
                    identity: str | None = None,
                    now: float | None = None,
                    ) -> tuple[bool, int, tuple[str, ...], int]:
    """May this detection be DELIVERED? Returns the digest for the NEXT window.

    ``(deliver, folded_count, folded_identities, identity_overflow)``: how many
    detections the PREVIOUS window collapsed, which distinct SOURCES they came
    from (bounded to :data:`_MAX_FOLDED_IDENTITIES`, with the overflow counted),
    and whether this one goes out now. The count is carried rather than dropped
    because "12 more happened" is the information a rate digest exists to
    convey — and so are the sources: collapsing a distributed signup flood into
    one message must not also discard WHICH clients were refused, or the bound
    has deleted the security record instead of bounding it (#14407 round 5).

    ``identity`` is the detection's own scope when it has one (the blocked IP for
    the signup velocity classes, whose callers pass no org at all). It is
    optional: an incident delivery names its ISSUE instead.

    The FIRST detection of a window is delivered at once (no timer, no batching
    delay): only repeats are folded. On a key whose window never expires the
    entry is bumped in place, so a sustained detector costs no allocation.
    """
    global _delivery
    now = _delivery_now() if now is None else now
    key = (kind, subject)
    with _delivery_lock:
        entry = _delivery.get(key)
        if entry is not None:
            deadline, folded, identities, overflow = entry
            if now < deadline:
                folded += 1
                # A REPEAT of a source already named is not a new source.
                if identity and identity not in identities:
                    if len(identities) < _MAX_FOLDED_IDENTITIES:
                        identities = (*identities, identity)
                    else:
                        overflow += 1
                _delivery[key] = (deadline, folded, identities, overflow)
                return False, 0, (), 0
            _delivery[key] = (now + window_s, 0, (), 0)
            _enforce_delivery_cap(now)
            return True, folded, identities, overflow
        _delivery[key] = (now + window_s, 0, (), 0)
        _enforce_delivery_cap(now)
        return True, 0, (), 0


def _enforce_delivery_cap(now: float) -> None:
    """Hold the ledger at :data:`_MAX_DELIVERY_KEYS` LIVE entries (#14407).

    The previous version filtered on ``deadline > now``, i.e. it only removed
    entries that had ALREADY expired — so with 24 h incident windows a live
    population above the cap was never reduced and the constant bounded nothing
    (measured in review: 4596 live subjects, 4596 entries). A real bound has to
    evict LIVE entries, and the honest choice is the soonest-to-expire: those
    are the ones the window is about to free anyway, so evicting them costs at
    most one extra message for a subject that was at the end of its window.
    """
    global _delivery
    if len(_delivery) <= _MAX_DELIVERY_KEYS:
        return
    live = {k: v for k, v in _delivery.items() if v[0] > now}
    if len(live) > _MAX_DELIVERY_KEYS:
        kept = sorted(live.items(), key=lambda kv: kv[1][0],
                      reverse=True)[:_MAX_DELIVERY_KEYS]
        live = dict(kept)
    _delivery = live


def release_abuse_delivery(kind: str, subject: str) -> None:
    """Re-arm delivery for a ``(kind, subject)`` — the EPISODE ended (#14407).

    The bound is the episode, never a wall-clock cooldown: a genuinely new
    episode must alert at once, and only the engine knows a rule's window went
    clean (``AbuseEngine._evaluate``). Without this release a long-lived fallback
    window would mute the second episode — the direction a security signal must
    never fail in (#4872).

    Released on the episode end the engine OBSERVES (the clean window that
    persisted ``flag_clear``, plus its bounded retry). An episode cleared out of
    band — the ``abuse_unsuspend`` SQL function, a manual ``flag_clear`` — is not
    observed, so its window stays armed and a new episode inside it is folded;
    the reminder at the window's end still fires and names the open issue. That
    residual is stated where the window is declared, and the runbook row names
    the manual close (#14407 round 5).
    """
    with _delivery_lock:
        _delivery.pop((kind, subject), None)


def reset_abuse_delivery_for_tests() -> None:
    """Clear the delivery ledger (test seam, #14407).

    A process-wide ledger dedups ACROSS tests in one pytest process: a window
    opened by one test would silently swallow an unrelated test's first message.
    """
    with _delivery_lock:
        _delivery.clear()


def resolve_abuse_episode(org_id: str, rule: str | None) -> bool:
    """Close + delete-to-resolve an abuse rule's incidents — EPISODE END (#14407).

    Called by ``AbuseEngine._evaluate`` when a rule's window came back clean and
    its durable ``flag_clear`` was persisted. Without it nothing ever resolved an
    abuse incident: the sentinel and its issue stayed OPEN for the rest of time,
    so the tracker showed a permanent incident for a condition that had recovered
    (the shape #9944 sat in for a month) and the recurring-alert story could not
    distinguish "still broken" from "long resolved".

    Releasing the DELIVERY window happens even when no incident channel can be
    built (the ledger is local), then the incident is resolved when there is one.
    With a store, the release waits for a CONFIRMED end: a clean window that only
    counts one observation of the #3146 recovery window is not an ended episode,
    so it neither resolves nor re-arms (see the `skipped_window` gate below).

    Returns True when every close that was attempted succeeded (or there was
    nothing to close), False when any of them RAISED: a failed close re-raises by
    design (``#5143`` — nothing is announced and nothing is deleted), and the
    engine must know so it can RETRY. The episode end fires exactly once (the next
    clean window reads ``flagged=False``), so a swallowed failure here would
    strand the incident OPEN for the rest of time.

    NEVER raises and never blocks the request path on a failure: ``abuse.py``'s
    evaluation is best-effort by contract, and a failed close is already handled
    by the store (``CloseCooldown`` / a re-raise that leaves the incident OPEN —
    ADR-011's "a blip must not re-file; a permanent failure must not be silent").
    A failure here costs this episode's resolution, never the poll.
    """
    subject = _abuse_subject(org_id, rule)
    kinds = _EPISODE_INCIDENT_KINDS
    # ONE subject: the rule-scoped one. A legacy rule-less incident has been
    # MIGRATED onto its scoped key by `notify._adopt_legacy_subject`, so the bare
    # `{org}` spelling no longer names any rule's episode — resolving it here
    # would let ANY rule's clean window close the shared object (a false
    # all-clear, then a re-file). The bare spelling is resolved only by a
    # rule-less caller, whose `subject` IS `org_id`.
    #
    # DECLARED RESIDUAL: adoption runs on a DETECTION, so an org that recovers
    # before it sends another one keeps its pre-#14407 `{org}` object and its
    # issue OPEN. The trade is deliberate — the alternative (also resolving the
    # bare spelling from a rule-scoped end) is the false all-clear this change
    # removes, and a stale OPEN incident is visible while a wrong ✅ is not. The
    # runbook row names the legacy path as the manual close.
    subjects = (subject,)
    # The DELIVERY window is re-armed only by a CONFIRMED episode end — see the
    # `skipped_window` gate below. A release on every clean evaluation would
    # undo the episode bound for a rule that hovers at its threshold: each
    # crossing would re-arm the ledger and page a reminder, so the 24h reminder
    # window would degenerate back into one message per crossing.
    #
    # The no-channel shape keeps the old behaviour below (the ledger is local
    # and is the ONLY delivery there is, so an ended episode must re-arm it).
    store, reason = _abuse_alert_store()
    if store is None:
        for kind in _episode_delivery_kinds():
            for subj in subjects:
                release_abuse_delivery(kind, subj)
        logger.debug("abuse episode end: no incident channel (%s) — delivery "
                     "re-armed only (%s/%s)", reason, org_id, rule)
        return True
    ok = True
    skipped_window = False
    for kind in kinds:
        for subj in subjects:
            try:
                outcome = store.resolve_incident_state(kind, subj)
            except CloseCooldown as cool:
                # A REFUSAL, not a failed attempt: the store is backing off for
                # `close_cooldown_min` after a recent failure (#5143) and NOTHING
                # was tried. It must not consume the engine's retry budget —
                # otherwise the budget is spent entirely inside one cooldown and
                # the incident is dropped exactly when the cooldown expires.
                ok = False
                logger.info("abuse episode end: %s (%s) backing off after a "
                            "recent failed close (%s)", kind, subj,
                            redact_safe(cool))
                continue
            except Exception as e:  # noqa: BLE001, RUF100
                ok = False
                logger.warning("abuse episode end: %s (%s) did not resolve (%s) — "
                               "the incident stays OPEN and the engine retries it "
                               "on the next clean window", kind, subj,
                               redact_safe(e))
                continue
            # A close that did NOT happen is not a resolution (#5143/ADR-011).
            # Two outcomes leave the incident ON RECORD and must keep the subject
            # PENDING, or a fleet that raises the recovery window
            # (`ALERT_RECOVERY_STREAK`, `docs/ops/registry-backup-dr.md`) silently
            # never resolves an abuse incident at all — defect 3 coming back
            # through an env var:
            #   * SKIPPED_WINDOW — the healthy observation was counted, the
            #     recovery window is not satisfied yet (`#3146`); this episode end
            #     is observation 1 of N.
            #   * SKIPPED_FRESH  — the incident is on record and this attempt may
            #     not clear it (fresh, or owned by another writer, `#3127`).
            if outcome in (ResolveOutcome.SKIPPED_WINDOW, ResolveOutcome.SKIPPED_FRESH):
                ok = False
                if outcome is ResolveOutcome.SKIPPED_WINDOW:
                    skipped_window = True
                logger.info(
                    "abuse episode end: %s (%s) not resolved yet (%s) — staying "
                    "pending so a later clean window completes the recovery window",
                    kind, subj, getattr(outcome, "name", outcome))
    # BOTH KIND spellings: the delivery ledger is armed by the event kind, so
    # releasing only the incident kind left `abuse_flag`'s window armed across
    # episodes (see `_episode_delivery_kinds`).
    #
    # NOT released while the recovery window is unsatisfied: this clean window is
    # observation k of `resolve_streak`, so the EPISODE has not ended — and
    # re-arming the ledger here would page a reminder on every crossing of a rule
    # hovering at its threshold, which is the volume this whole change bounds.
    if not skipped_window:
        for kind in _episode_delivery_kinds():
            for subj in subjects:
                release_abuse_delivery(kind, subj)
    return ok


def _abuse_message(kind: str, org_id: str, details: dict, *,
                   attribution: str, folded: int = 0,
                   identities: tuple[str, ...] = (), overflow: int = 0) -> str:
    """The human text: what fired, for whom, and WHY it is attributable.

    Every abuse message carries an ``attribution`` line — an issue number, the
    declared non-filing decision, or the reason no incident could be filed. That
    line is the invariant #14407 leaves behind: a human can always say what this
    message is, and where to look it up.
    """
    parts = [f"🚨 Tortoise Security: {kind}", f"Team: {org_id}"]
    if details.get("rule"):
        parts.append(f"Rule: {details['rule']}")
    if details.get("count") is not None:
        parts.append(f"Count: {details['count']}")
    if details.get("country"):
        parts.append(f"Country: {details['country']}")
    if details.get("ip"):
        parts.append(f"IP: {details['ip']}")
    parts.append(attribution)
    if folded:
        # The dashboard row is written by the CALLER, and only when the detection
        # carried an org (`abuse.py`'s `if org_id` guards; `notify_abuse` maps a
        # missing one to "_"). Telling an anonymous subject that its folded
        # detections "are recorded on the dashboard" would assert an attribution
        # that does not exist — the defect class this whole change removes.
        recorded = (
            "all of them are recorded on the dashboard alert list"
            if org_id and org_id != "_"
            else "they are NOT recorded on the dashboard (the detection carried "
                 "no org scope, so there is no row to write)")
        parts.append(f"Digest: {folded} further {kind} detection(s) in the "
                     f"previous window were folded into this message; {recorded}.")
    if identities or overflow:
        # The folded SOURCES, not just their count (#14407 round 5): a distributed
        # burst collapsed into one message still has to say which clients it
        # refused, or the digest has replaced the evidence instead of bounding it.
        listed = ", ".join(identities) if identities else "(none named)"
        more = f" (+{overflow} more)" if overflow else ""
        parts.append(f"Folded sources: {listed}{more}")
    return "\n".join(parts)


def _send_abuse(bot_token: str, chat_id: str, text: str) -> bool:
    """Send one abuse message. True when the channel accepted it.

    The return value exists so a caller that has ALREADY ARMED the delivery
    window can release it on a failure: otherwise one Telegram blip costs that
    subject the whole window (24h for an incident class in the no-channel shape),
    which is the same "bound becomes a mute" direction #4872 forbids.
    """
    try:
        telegram_send(bot_token, chat_id, text)
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("abuse notify: telegram failed (%s)", redact_safe(e))
        return False
    return True


def _adopt_legacy_subject(store, incident_kind: str, subject: str,
                          org_id: str) -> str:
    """Adopt a pre-#14407 RULE-LESS incident instead of filing a sibling.

    Subjects became rule-scoped in #14407, and the R2 object IS the dedup
    linearisation point — so without this a live incident filed under the old
    ``{org}`` spelling would be invisible to the new ``{org}:{rule}`` key and the
    first recurrence would file a SECOND issue for one condition: exactly the
    #2844 alias defect (two spellings, two create-once points, a resolve that
    strands one). One such incident is MEASURED on record (#10576), so this is a
    migration step, not a hypothetical.

    The migration moves the SENTINEL (``AlertStore.migrate_incident_subject``),
    never the deduped thing: the scoped key takes over the legacy issue number
    and the legacy object is released with its issue left OPEN. Returning the
    bare ``org_id`` instead — what this did before — re-collapsed the subject and
    re-created the cross-rule coupling the scoped key exists to remove: every
    filing and dedup for that kind/org then used the shared spelling, so one
    rule's clean window resolved the OTHER rule's incident (a false all-clear to
    the human, the sentinel deleted, and the next detection re-filing: the
    #3029/#3031 flip).

    Cost is one R2 GET, on the notification path the engine already throttles to
    one attempt per (org, rule, window). A store read that fails, or a store that
    cannot move the object, returns the scoped subject: filing under the new key
    is the pre-#14407-compatible fallback, and a duplicate issue is visible while
    a stranded sentinel is not.
    """
    if subject == org_id:
        return subject
    try:
        legacy = store.incident_record(incident_kind, org_id)
    except Exception:  # noqa: BLE001, RUF100
        return subject
    number = legacy.get("issue_number")
    if not number:
        return subject
    migrate = getattr(store, "migrate_incident_subject", None)
    if callable(migrate) and migrate(incident_kind, subject, org_id):
        logger.info(
            "abuse notify: adopted the pre-#14407 rule-less incident for %s "
            "(issue #%s) under %s — one issue, one scoped linearisation point",
            org_id, number, subject)
    else:
        logger.warning(
            "abuse notify: the pre-#14407 rule-less incident for %s (issue #%s) "
            "could not be moved under %s — filing under the scoped key; close "
            "the legacy issue by hand if this recurs",
            org_id, number, subject)
    return subject


def _notify_abuse_incident(kind: str, org_id: str, details: dict) -> None:
    """A state-change class: the INCIDENT owns the message (#14407).

    One decision point for both channels — the store's create-once object files
    the issue AND emits the single Telegram push for the lifecycle, so the human
    and agent channels cannot disagree. Pre-fix they were two decisions: the
    push here fired on every detection, the issue was deduped, and 4 of the 6
    classes filed nothing at all.

    ``DEDUP`` means the human was already told for this incident and the issue is
    live: repeats inside the delivery window are folded (the store records them
    on the ISSUE, agent-visible, bounded to one a day by #12243), and at most one
    RE-ALARM per window is sent while the episode stays open so a persistent
    breach cannot go silent. ``SUPPRESSED`` means an operator paused the kind in
    ``ops/suppression.json`` — an explicit decision, which is honoured.

    When no incident can be established at all the message is delivered ANYWAY,
    bounded, and labelled with the reason: a missing tracker must never mute a
    security signal (#4872), and an unattributable message must never look like
    an attributable one.
    """
    subject = _abuse_subject(org_id, details.get("rule"))
    incident_kind = _INCIDENT_KIND_OF_EVENT[kind]
    store, reason = _abuse_alert_store()
    if store is not None:
        subject = _adopt_legacy_subject(store, incident_kind, subject, org_id)
        what = ("Suspended by an operator"
                if kind == KIND_ABUSE_SUSPENDED
                else "Human review needed — rate-limit, do not suspend"
                if kind == KIND_ABUSE_REVIEW_NEEDED
                else "Suspicious activity flagged")
        detail = {
            "detail": (f"{what}: {details.get('rule', '?')} "
                       f"count={details.get('count', '?')}"),
            # A bounded, message-free token: the store renders it into the alert
            # text so the human keeps the rule + count they had before, while
            # the issue body carries the whole detail.
            "context": _abuse_context(details),
        }
        try:
            outcome = store.open_incident_state(incident_kind, subject, detail)
        except Exception as e:  # noqa: BLE001, RUF100
            # The raise is NOT proof that nothing was filed: `open_incident_state`
            # can raise AFTER it created + announced the issue (a trailing
            # `_write_json`, the pending-push bookkeeping, `_set_healthy_streak`
            # on the DEDUP path deliberately propagates). Saying "no incident was
            # filed" then tells the human there is no record while issue #N is
            # open and was pushed in the same breath — a duplicate page and a false
            # statement, the class this change removes (#14407 round 5).
            reason = f"incident filing failed ({redact_safe(e)})"
            # Best-effort: does an incident exist for this subject anyway?
            # ⛔ A FAILED READ IS NOT AN ABSENT RECORD. `known` alone cannot tell
            # "nothing was filed" from "the read that would have found it raised",
            # and those two failures are CORRELATED (an R2 outage fails the write
            # first, then the read) — so the arm that says "no incident was filed"
            # must not be reachable on the second one. `record`/`read_failed` keep
            # the facts apart, the same way `number_known` does in the branch below.
            known = None
            record: dict = {}
            read_failed = False
            try:
                record = store.incident_record(incident_kind, subject) or {}
                known = record.get("issue_number")
            except Exception:  # noqa: BLE001, RUF100 — the read itself may fail
                read_failed = True
            if known:
                deliver, folded, _ids, _over = _claim_delivery(
                    kind, subject, _INCIDENT_DELIVERY_WINDOW_S)
                if deliver:
                    _deliver_unattributed(
                        kind, org_id, subject, details, folded,
                        reason=reason, known_number=known)
                return
            if read_failed or record:
                # Whether an incident is on record is UNKNOWN when the READ raised:
                # the write may have filed + pushed first (see above), so the absence
                # cannot be established. When the read SUCCEEDED and returned a
                # record, the opposite IS the fact — a number-less record is still a
                # record (`incident_open` counts it) — but WHY it has no number is
                # not one cause: `AlertStore` writes a sentinel with
                # `issue_number: None` when the open-issue SEARCH raised (filing
                # deferred), when `_file` raised (filing FAILED), and when a
                # successful CLOSE could not delete the object (a tombstone whose
                # issue is already resolved). Naming only the first would send a
                # human looking for a live incident that is already closed.
                deliver, folded, _ids, _over = _claim_delivery(
                    kind, subject, _INCIDENT_DELIVERY_WINDOW_S)
                if deliver:
                    tail = (
                        " and the read-back raised too — whether an incident is on "
                        "record is UNKNOWN"
                        if read_failed else
                        " and the record carries no issue number (filing was "
                        "deferred or failed, or it is a resolved incident's "
                        "leftover sentinel) — an incident record EXISTS but cannot "
                        "be named"
                    )
                    _deliver_unattributed(
                        kind, org_id, subject, details, folded,
                        reason=f"{reason}{tail}")
                return
        else:
            # The bound is the EPISODE, with a bounded RE-ALARM, and both are the
            # delivery ledger's (`_claim_delivery`). Arm the window here on EVERY
            # outcome so the first repeat cannot page again in the same minute:
            # the create-once path has already pushed the episode's first message
            # when it FILED, and on DEDUP the ledger alone decides whether the
            # open incident has earned its next reminder.
            deliver, folded, _ids, _over = _claim_delivery(
                kind, subject, _INCIDENT_DELIVERY_WINDOW_S)
            # A read that RAISES is not "no issue number": the store may have
            # pushed an attributed alert already, so treating unknown as absent
            # would send a second, wrongly-unattributed message. `number_known`
            # keeps the two facts apart.
            number = None
            number_known = True
            try:
                number = store.incident_record(incident_kind, subject).get(
                    "issue_number")
            except Exception:  # noqa: BLE001, RUF100 — NEVER raise from the alert path
                number_known = False
            if outcome is OpenOutcome.DEDUP and deliver:
                # A persistent breach must not go silent: an episode that stays
                # open re-alarms at most once per window, and the message NAMES
                # the incident so the human can see it is the same one — the
                # pre-fix code pushed every window with no reference at all.
                ref = (f"issue #{number}" if number
                       else ("an open incident whose issue number could not be "
                             "read just now" if not number_known
                             else "an open incident with no issue number on record"))
                bot_token = _env("TELEGRAM_BOT_TOKEN")
                chat_id = _env("TELEGRAM_CHAT_ID")
                channel_ok = not (
                    _skip_channel("telegram", bot_token)
                    or _skip_channel("telegram-chat", chat_id))
                if channel_ok and not _send_abuse(bot_token, chat_id, _abuse_message(
                        kind, org_id, details,
                        attribution=(f"Still open — {ref}. One reminder per "
                                     f"{int(_INCIDENT_DELIVERY_WINDOW_S // 3600)}h "
                                     "while the episode stays open; repeats "
                                     "inside that window are folded (the issue "
                                     "comment trail is the recurrence record)."),
                        folded=folded)):
                    # A failed send must not consume the reminder window.
                    release_abuse_delivery(kind, subject)
            elif outcome is OpenOutcome.FILED and not number_known and deliver:
                # The store filed and may well have pushed a correctly attributed
                # alert; the read-back is what failed. Sending the unattributed
                # text would assert an absence we cannot establish, and LEAVING THE
                # WINDOW ARMED would be silent for the whole window whenever the
                # store filed WITHOUT a number (deferred filing) — "silence is an
                # outcome", which #4872 forbids. Defer instead: release the window
                # and let the next detection read the number.
                release_abuse_delivery(kind, subject)
                logger.warning(
                    "abuse notify: %s (%s) filed but the issue number could not be "
                    "read back — delivery deferred to the next detection (the "
                    "window was released, not spent)", kind, subject)
            elif (outcome is OpenOutcome.FILED and number_known and not number
                  and deliver):
                # FILED with NO issue number is NOT a delivered alert. The store
                # pushes only when it holds a number (`alert_store` guards its own
                # push on it), and `_become_filer` returns exactly this way when
                # the open-issue SEARCH raises (filing DEFERRED) or when `_file`
                # raises — and then this branch used to fall straight out of the
                # `return` with nothing sent and the window already armed.
                #
                # That is a REGRESSION against the pre-fix code, which pushed the
                # Telegram leg independently of the store, and it lands precisely
                # when filing is failing hardest (a search rate limit exhausted by
                # the very alert burst this change exists to bound): a total, silent
                # mute of a security class — the direction #4872 forbids.
                #
                # Deliver the labelled message instead, bounded by the same
                # window: unattributable is allowed, SILENT is not.
                _deliver_unattributed(
                    kind, org_id, subject, details, folded,
                    reason=("no incident could be attributed — an incident exists "
                            "but carries no issue number (filing was deferred or "
                            "failed)"))
            elif outcome is OpenOutcome.SUPPRESSED:
                # An operator PAUSED this kind in ``ops/suppression.json``: an
                # explicit decision, honoured (the docstring above says so). The
                # window is released rather than spent so that LIFTING the pause
                # of a still-live condition pages at once instead of staying
                # silent for the rest of the window (#4872: never a mute).
                release_abuse_delivery(kind, subject)
                logger.info(
                    "abuse notify: %s (%s) suppressed by an operator — the delivery "
                    "window was released, not spent", kind, subject)
            elif not (
                outcome is OpenOutcome.DEDUP
                or outcome is OpenOutcome.FILED
            ):
                # A fact this chain does not classify — a FOURTH ``OpenOutcome``
                # member, or a store returning something that is not one of the
                # enum's members at all. The chain above names its facts by
                # IDENTITY (``is DEDUP`` / ``is FILED``), so anything added later
                # matches nothing and its fall-through would be SILENCE — the one
                # outcome #14407 forbids and the very one the neighbouring
                # ``number_known`` arm exists to remove (#4872).
                #
                # Releasing the window alone does not close that hole: while the
                # store keeps answering with the unclassified fact, every later
                # detection takes this same arm and releases again, so nothing is
                # ever delivered and the class is mute for as long as the fact
                # lasts. DELIVER instead, labelled with the uncertainty — an
                # unattributable message a human can act on beats a mute — bounded
                # by the same window as every other incident delivery, so repeats
                # inside it are still folded.
                if deliver:
                    _deliver_unattributed(
                        kind, org_id, subject, details, folded,
                        reason=("the incident store returned an outcome this path "
                                "does not classify ("
                                f"{getattr(outcome, 'name', outcome)!r}) — whether "
                                "an incident is on record is UNKNOWN"))
            # DEDUP without `deliver` and FILED without a send are REACHED here on
            # purpose: the repeat was FOLDED into the current window (and, for
            # FILED, the store's own create-once push is the episode's first
            # message). Releasing the window there would let the next detection
            # send again — the per-detection storm this change removes.
            return

    deliver, folded, _ids, _over = _claim_delivery(
        kind, subject, _INCIDENT_DELIVERY_WINDOW_S)
    if not deliver:
        return
    _deliver_unattributed(kind, org_id, subject, details, folded,
                          reason=f"no incident was filed — {reason}")


def _deliver_unattributed(kind: str, org_id: str, subject: str, details: dict,
                          folded: int, *, reason: str,
                          known_number: int | None = None) -> None:
    """Send the BOUNDED message for a class whose incident cannot be named.

    #14407: the invariant is "every abuse message is attributable — or it SAYS
    it is not". Silence is the one outcome that is never allowed: an
    unattributable message a human can act on beats a mute (#4872), and the
    delivery ledger has already bounded the rate before this is called.
    """
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if _skip_channel("telegram", bot_token) or _skip_channel("telegram-chat", chat_id):
        return
    logger.warning(
        "abuse notify: delivered %s (%s) with NO attributable incident — %s. "
        "The message says so; the class is bounded to one such message per %dh "
        "per subject until the incident channel is repaired (#14407).",
        kind, subject, reason, int(_INCIDENT_DELIVERY_WINDOW_S // 3600))
    if not _send_abuse(bot_token, chat_id, _abuse_message(
            kind, org_id, details,
            attribution=(
                # `known_number` means an incident DOES exist (the store filed it,
                # then a later step raised). Asserting "no incident was filed" here
                # would be false, and would page a second time about the same
                # condition without saying so.
                f"Attribution uncertain — issue #{known_number} exists, but the "
                f"incident state could not be confirmed: {reason}."
                if known_number else f"Not attributable: {reason}."),
            folded=folded)):
        # A failed send must not consume the window: an outage must not mute the
        # class for the next window after it recovers.
        release_abuse_delivery(kind, subject)


def _abuse_context(details: dict) -> str:
    """The message-free token the incident's Telegram push renders.

    Built from a CLOSED vocabulary (a rule name and a count), never free text: the
    alert text is a publication sink and an exception message is
    attacker-influenceable (#3293).
    """
    bits = []
    rule = details.get("rule")
    if isinstance(rule, str) and rule.strip():
        bits.append(f"rule={rule.strip()}")
    count = details.get("count")
    if isinstance(count, int):
        bits.append(f"count={count}")
    return " ".join(bits)


def _notify_abuse_activity(kind: str, org_id: str, details: dict) -> None:
    """A measurement class: a bounded DIGEST, and it says what it is (#14407).

    R3/R4/R8 are ``REGISTER_ACTIVITY`` in #10156's split — a detector measured a
    rate and changed nothing — so filing an incident for one would reverse that
    recorded decision. They must still be DELIVERED (they are the only signal that
    a rate moved), so two things make that delivery not "out of control":

    * the rate is bounded per ``(kind, subject)`` per
      :data:`_ACTIVITY_DELIVERY_WINDOW_S`, with the folded count carried into the
      next message;
    * every message states the declared non-filing decision and that nothing was
      disabled, so a human never reads a measurement as an incident — the framing
      defect #10156 fixed on the dashboard and this change fixes in the chat.
    """
    # The detection's OWN scope is the identity a digest must carry: the
    # signup-velocity callers reach here with no org at all (`record_signup_block`
    # is called with no `org_id` by the 429 path), so without this every refused
    # client in a window collapses into ONE anonymous message and the other IPs
    # are in no alert record anywhere — the bound would have deleted the evidence
    # rather than bounding it (#14407 round 5).
    identity = details.get("ip")
    identity = identity if isinstance(identity, str) and identity else None
    deliver, folded, identities, overflow = _claim_delivery(
        kind, org_id, _ACTIVITY_DELIVERY_WINDOW_S, identity=identity)
    if not deliver:
        return
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if _skip_channel("telegram", bot_token) or _skip_channel("telegram-chat", chat_id):
        return
    if not _send_abuse(bot_token, chat_id, _abuse_message(
            kind, org_id, details,
            attribution=("Activity, not an incident (#10156): a detector measured a "
                         "rate. Nothing was disabled or restricted, and this class "
                         "files no incident by design — "
                         + ("the rows are on the dashboard's activity register."
                            if org_id and org_id != "_" else
                            "the detection carried no org scope, so NO dashboard "
                            "row was written for it.")),
            folded=folded, identities=identities, overflow=overflow)):
        release_abuse_delivery(kind, org_id)


def notify_abuse(kind: str, org: dict, details: dict | None = None) -> None:
    """Abuse notification — Telegram ONLY (#308, channel decision #3639).

    NEVER raises. The Resend leg was removed 2026-09-16 (#3639): abuse
    notifications are not counted by the Resend send budget (``email_notify.py``
    counts every send it takes — invite/OTP/onboarding + billing, #3631), so a
    flag storm burned the whole transactional quota — 401 ``abuse_flag`` emails
    in 3 hours drove two consecutive days to a reported 200% of the daily cap
    and starved invites/OTPs. Telegram is the channel for this use case; email
    is intentionally no longer sent, so abuse consumes no Resend slot at all
    and a Telegram failure loses the alert.

    #14407 — TWO REGISTERS, ONE DECISION PER CLASS. The class decides whether the
    message belongs to an INCIDENT (:data:`ABUSE_INCIDENT_KINDS` — filed, deduped
    by the store's create-once lifecycle, one push per lifecycle) or is an
    ACTIVITY measurement (:data:`ABUSE_ACTIVITY_KINDS` — declared non-filing,
    delivered as a bounded digest). Both paths state their attribution in the
    message itself.

    ``org`` is retained for the caller shape (``abuse.py`` passes org_id and
    email); the ``email`` key is no longer read. Callers in async contexts
    invoke via asyncio.to_thread (#310 pattern).
    """
    if kind not in KINDS or not kind.startswith("abuse_"):
        logger.warning("abuse notify: unknown kind %r ignored", kind)
        return
    details = details or {}
    org_id = org.get("org_id") or "_"
    if kind in ABUSE_INCIDENT_KINDS:
        _notify_abuse_incident(kind, org_id, details)
    elif kind in ABUSE_ACTIVITY_KINDS:
        _notify_abuse_activity(kind, org_id, details)
    else:
        # An ``abuse_*`` kind in NEITHER register: the DIGEST path is the default
        # on purpose — an unrecognised class must be bounded, never a storm — but
        # it is called out, because a new kind that should FILE must not be born
        # asleep as a measurement (the #10156 register split is a decision, and a
        # silent default would decide it by accident).
        logger.warning(
            "abuse notify: %r is in neither ABUSE_INCIDENT_KINDS nor "
            "ABUSE_ACTIVITY_KINDS — delivering it as a bounded digest; declare "
            "its register in notify.py", kind)
        _notify_abuse_activity(kind, org_id, details)


def redact_safe(e: BaseException) -> str:
    """Redacted error string for logs — never secrets, payloads, or headers.

    Strips any known secret env value (RESEND_API_KEY, TELEGRAM_BOT_TOKEN,
    BILLING_NOTIFY_TO) from the message before applying ``redact_error``.
    """
    from tortoise.security import redact_error

    msg = str(e) or e.__class__.__name__
    for secret in ("RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "BILLING_NOTIFY_TO"):
        val = _env(secret)
        if val and val in msg:
            msg = msg.replace(val, "[redacted]")
    return redact_error(RuntimeError(msg))
