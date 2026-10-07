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
from datetime import datetime, timezone

import httpx
from tortoise.telegram_push import send_message as telegram_send  # noqa: E402, RUF100

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM_ADDRESS = "noreply@premiselabs.co"  # single managed sender identity (#1136)

# Kinds mirror the audit/analytics event names (billing_upgrade, billing_downgrade,
# billing_payment_failed, billing_cancel).
KINDS = {"billing_upgrade", "billing_downgrade", "billing_payment_failed", "billing_cancel",
         # #308: abuse prevention notifications
         "abuse_flag", "abuse_suspended", "abuse_new_ip", "abuse_read_velocity",
         # #1081: R8 signup-velocity (anon signups per IP per window)
         "abuse_signup_velocity",
         # #1709: recovery-velocity (keyless recovery mints per IP per window)
         "abuse_recovery_velocity"}

# Ops incident KINDs (the alert_store sink) — deliberately NOT members of KINDS.
# KINDS names business EVENTS; these name a failed DELIVERY of one. Keeping them
# separate stops an egress outage from being deduped against a billing event.
_BILLING_SEND_FAILED_KIND = "BILLING_SEND_FAILED"

_skip_logged: set[str] = set()

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
    complete (a best-effort send leg), so a dead alert channel — sink disabled,
    R2/GitHub/Telegram down — degrades to the log line the caller already
    wrote rather than propagating. Returns True only when THIS call was the
    filer; a dedup hit returns False, which is not an error.

    Dedup is ``AlertStore.open_incident``'s per-``(kind, org_id)`` create-once
    object, so a repeated failure of the same channel files ONE issue; recovery
    deletes the object (delete-to-resolve), so a later outage is a NEW incident.
    That is why repeated failures must not be filed by the caller — pass a
    SUBJECT that identifies the outage, not the individual send.

    Mirrors the ``abuse_suspended`` call site below (same
    ``_backup_config_safe`` → ``_alert_store_from`` → ``open_incident`` path),
    factored out because more than one send leg now needs it. The hosted_api
    import is function-level for the reason stated at that call site: hosted_api
    imports notify, so a module-level import would cycle.
    """
    try:
        from tortoise import hosted_api as _ha
        cfg = _ha._backup_config_safe()
        if cfg is None:
            return False  # sink disabled — the caller's own log is the record
        store = _ha._alert_store_from(cfg)
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

    ``org`` is retained for the caller shape (``abuse.py`` passes org_id and
    email); the ``email`` key is no longer read. Callers in async contexts
    invoke via asyncio.to_thread (#310 pattern).
    """
    if kind not in KINDS or not kind.startswith("abuse_"):
        logger.warning("abuse notify: unknown kind %r ignored", kind)
        return
    details = details or {}

    bot_token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not _skip_channel("telegram", bot_token) and not _skip_channel("telegram-chat", chat_id):
        try:
            parts = [f"🚨 Tortoise Security: {kind}",
                     f"Team: {org.get('org_id', '?')}"]
            if details.get("rule"):
                parts.append(f"Rule: {details['rule']}")
            if details.get("count") is not None:
                parts.append(f"Count: {details['count']}")
            if details.get("country"):
                parts.append(f"Country: {details['country']}")
            if details.get("ip"):
                parts.append(f"IP: {details['ip']}")
            telegram_send(bot_token, chat_id, "\n".join(parts))
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("abuse notify: telegram failed (%s)", redact_safe(e))

    if kind == "abuse_suspended":
        # Ops incident alert (GH issue + Telegram) — best-effort: absence of
        # backup config or any failure degrades to the Telegram leg above
        # (there is no email leg since #3639).
        # Function-level import: notify must never import hosted_api at
        # module level (hosted_api imports notify).
        try:
            from tortoise import hosted_api as _ha
            cfg = _ha._backup_config_safe()
            if cfg is not None:
                store = _ha._alert_store_from(cfg)
                store.open_incident(
                    "abuse_suspended", org.get("org_id") or "_",
                    {"detail": (f"Auto-suspended: {details.get('rule', '?')} "
                                f"count={details.get('count', '?')}")})
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("abuse notify: alert_store incident failed (%s)",
                           redact_safe(e))


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
