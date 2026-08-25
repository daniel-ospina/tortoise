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

_skip_logged: set[str] = set()


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


def _email_text(kind: str, team: dict, details: dict) -> str:
    tier = details.get("tier", team.get("tier", "?"))
    lines = [
        f"Tortoise Billing — {kind}",
        "",
        f"Team: {team.get('name', team.get('team_id', '?'))} (id {team.get('team_id', '?')})",
        f"Tier: {tier}",
    ]
    if details.get("subscription_status"):
        lines.append(f"Subscription status: {details['subscription_status']}")
    if details.get("message"):
        lines.append(f"Detail: {details['message']}")
    if details.get("grace_until"):
        lines.append(f"Grace until: {details['grace_until']}")
    return "\n".join(lines)


def _telegram_text(kind: str, team: dict, details: dict) -> str:
    tier = details.get("tier", team.get("tier", "?"))
    parts = [f"💰 Tortoise Billing: {kind}", f"Team: {team.get('name', team.get('team_id', '?'))} | Tier: {tier}"]
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


def notify_billing_event(kind: str, team: dict, details: dict | None = None) -> None:
    """Send a billing notification over both channels. NEVER raises.

    kind must be in KINDS. team is the Team node dict (name/team_id/tier).
    details may carry subscription_status / message / grace_until / tier.
    """
    if kind not in KINDS:
        logger.warning("billing notify: unknown kind %r ignored", kind)
        return
    details = details or {}

    api_key = _env("RESEND_API_KEY")
    to = _env("BILLING_NOTIFY_TO")
    if not _skip_channel("resend", api_key) and not _skip_channel("resend-recipient", to):
        try:
            subject = f"Tortoise Billing — {kind}"
            body = _email_text(kind, team, details).replace("\n", "<br>")
            _send_resend(api_key, to, subject, f"<pre>{body}</pre>")
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("billing notify: resend failed (%s)", redact_safe(e))

    bot_token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not _skip_channel("telegram", bot_token) and not _skip_channel("telegram-chat", chat_id):
        try:
            telegram_send(bot_token, chat_id, _telegram_text(kind, team, details))
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("billing notify: telegram failed (%s)", redact_safe(e))


def _abuse_email_text(kind: str, team: dict, details: dict) -> str:
    lines = [
        f"Tortoise Security — {kind}",
        "",
        f"Team id: {team.get('team_id', '?')}",
    ]
    if details.get("rule"):
        lines.append(f"Rule: {details['rule']}")
    if details.get("count") is not None:
        lines.append(f"Count: {details['count']} (threshold {details.get('threshold')}"
                     f" per {details.get('window_s')}s)")
    if details.get("country"):
        lines.append(f"Country: {details['country']}")
    if details.get("ip"):
        lines.append(f"IP: {details['ip']}")
    if details.get("scope"):
        lines.append(f"Scope: {details['scope']} ({details.get('id')})")
    if details.get("appeal_url"):
        lines.append(f"Appeal: {details['appeal_url']}")
    return "\n".join(lines)


def notify_abuse(kind: str, team: dict, details: dict | None = None) -> None:
    """Abuse notification over both channels (#308). NEVER raises.

    Recipient: ``team.get('email')`` — missing OR NULL both fall back to the
    BILLING_NOTIFY_TO ops inbox (anon agent-signup teams have no team email;
    the registry team dict has no 'email' key at all, so .get is mandatory).
    Callers in async contexts invoke via asyncio.to_thread (#310 pattern).
    """
    if kind not in KINDS or not kind.startswith("abuse_"):
        logger.warning("abuse notify: unknown kind %r ignored", kind)
        return
    details = details or {}

    api_key = _env("RESEND_API_KEY")
    to = team.get("email") or _env("BILLING_NOTIFY_TO")
    if not _skip_channel("resend", api_key) and not _skip_channel("resend-recipient", to):
        try:
            subject = f"Tortoise Security — {kind}"
            body = _abuse_email_text(kind, team, details).replace("\n", "<br>")
            _send_resend(api_key, to, subject, f"<pre>{body}</pre>")
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("abuse notify: resend failed (%s)", redact_safe(e))

    bot_token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not _skip_channel("telegram", bot_token) and not _skip_channel("telegram-chat", chat_id):
        try:
            parts = [f"🚨 Tortoise Security: {kind}",
                     f"Team: {team.get('team_id', '?')}"]
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
        # backup config or any failure degrades to Resend+Telegram above.
        # Function-level import: notify must never import hosted_api at
        # module level (hosted_api imports notify).
        try:
            from tortoise import hosted_api as _ha
            cfg = _ha._backup_config_safe()
            if cfg is not None:
                store = _ha._alert_store_from(cfg)
                store.open_incident(
                    "abuse_suspended", team.get("team_id") or "_",
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
