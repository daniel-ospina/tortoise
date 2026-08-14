"""Transactional email — Resend sender for team invitations (#307).

Invitations-only (2026-08-13): key-recovery email was tied to #265 (client-side
encryption), now deferred — no customer-held keys, nothing to recover.

Design (from docs/scoping/2026-08-13-307-email-notifications-scope.md, Approach A):
- Async best-effort send: the invite email is the ONLY automated token-delivery
  path (dashboard discards the token), but a send failure must NEVER fail the
  mint — schedule via asyncio.create_task, log a redacted WARNING on failure.
- Provider-accept semantics: ``email_sent_at`` is stamped only on Resend 200
  (provider accepted) — never "delivered" (bounces happen later).
- Env-gated: RESEND_API_KEY / RESEND_FROM_EMAIL / EMAIL_LINK_BASE_URL; absent
  key → channel skipped with a once-per-process log.
- Send budget (#1138): Resend free tier is 100 emails/day / 3,000/month — a
  bulk invite blast must never silently exhaust it (budget/rate 429s). We
  track an in-process estimate of sends per UTC day + month and hard-stop
  (skip + loud warning) when the env-tunable cap is reached. A slot is
  RESERVED at schedule time — closing the burst TOCTOU where N>cap concurrent
  schedules all passed the check before any provider POST completed — and
  refunded when the provider rejects/fails the POST. The guard covers the
  INVITE path only (billing/abuse notifications share the Resend account but
  are not counted) and is per-process (N replicas × cap).
- Secrets never logged: ``redact_safe`` on exception paths.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timezone

import httpx

from tortoise.notify import redact_safe

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

# Shared concurrency cap for all sends (volume is tiny; this bounds blast radius
# if a send storms during a replay — not a rate limiter).
_send_semaphore = asyncio.Semaphore(4)

# Pending best-effort sends, drained at shutdown (hosted_api._lifespan).
_pending_email_tasks: set[asyncio.Task] = set()

_skip_logged: set[str] = set()


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _from_address() -> str:
    return _env("RESEND_FROM_EMAIL") or "noreply@premiselabs.co"


def _email_link_base() -> str:
    return _env("EMAIL_LINK_BASE_URL") or "https://tortoise.premiselabs.co"


def _skip_channel(channel: str, secret: str | None) -> bool:
    if secret is None:
        if channel not in _skip_logged:
            _skip_logged.add(channel)
            logger.warning("email notify: %s channel skipped — secret not set", channel)
        return True
    return False


# ── Send budget (#1138) ──────────────────────────────────────────────────────
# Resend free tier: 100 emails/day, 3,000/month (budget/rate 429s beyond).
# A bulk invite blast (Team-tier bulk invite endpoint, burst of signups) must
# never silently exhaust the shared budget mid-blast. Guard = in-process
# estimate of sends per UTC day + month; when either env-tunable cap is
# reached, further sends are SKIPPED with a loud warning.
# Reservation semantics (P1 review-fix): a slot is RESERVED when the send is
# scheduled (send_invite_email) and REFUNDED if the provider rejects/fails the
# POST — counting only on provider accept left a TOCTOU where a burst of N>cap
# concurrent schedules all passed the check before any POST completed. Counters
# reset on process restart, so they bound a single process's burst — NOT an
# absolute usage meter (upgrade/usage-API monitoring is a separate concern).
# Scope: INVITE path only — billing/abuse notifications share the Resend
# account but are not counted; per-process (N replicas × cap).
_send_counts_day: int = 0
_send_counts_month: int = 0
_send_counts_day_period: str = ""   # "YYYY-MM-DD" UTC — day counter resets on day change
_send_counts_month_period: str = ""  # "YYYY-MM" UTC — month counter resets on month change


def _budget_limit(env_name: str, default: int) -> int:
    raw = _env(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("email notify: invalid %s=%r — using default %s", env_name, raw, default)
        return default
    if value < 0:
        # int(-1) → max(0,-1) → 0 would be a SILENT kill switch; fall back loud.
        logger.warning("email notify: negative %s=%r — using default %s", env_name, raw, default)
        return default
    return value


def _reset_budget_if_new_period() -> None:
    # Day and month counters roll over independently: the month counter must
    # ACCUMULATE across days (it is the 3,000/month free-tier guard) and only
    # reset at month change — resetting it at every day rollover would make it
    # equal the day counter and the monthly guard dead (#1138 review-fix).
    global _send_counts_day, _send_counts_month
    global _send_counts_day_period, _send_counts_month_period
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    if day != _send_counts_day_period:
        _send_counts_day = 0
        _send_counts_day_period = day
    if month != _send_counts_month_period:
        _send_counts_month = 0
        _send_counts_month_period = month


def _budget_exceeded() -> tuple[bool, str | None]:
    """(True, reason) when the estimated daily/monthly send budget is spent."""
    _reset_budget_if_new_period()
    day_cap = _budget_limit("RESEND_SEND_BUDGET_DAILY", 100)
    month_cap = _budget_limit("RESEND_SEND_BUDGET_MONTHLY", 3000)
    if _send_counts_day >= day_cap:
        return True, f"daily budget reached ({_send_counts_day}/{day_cap}, RESEND_SEND_BUDGET_DAILY)"
    if _send_counts_month >= month_cap:
        return True, f"monthly budget reached ({_send_counts_month}/{month_cap}, RESEND_SEND_BUDGET_MONTHLY)"
    return False, None


def _reserve_send() -> None:
    """Reserve one budget slot at schedule time (#1138 P1 review-fix).

    The guard must count a send when it is SCHEDULED, not when the provider
    POST completes — otherwise a burst of N>cap concurrent schedules passes
    the check before any POST finishes (TOCTOU) and every one goes out. The
    reservation is rolled back by :func:`_refund_send` on provider failure.
    """
    _reset_budget_if_new_period()
    global _send_counts_day, _send_counts_month
    _send_counts_day += 1
    _send_counts_month += 1


def _refund_send() -> None:
    """Roll back a reserved slot after the provider rejected/failed the POST.

    Clamped at 0: a period rollover between reserve and refund (e.g. 23:59:59.9
    → 00:00:00.1) already reset the counters — never decrement below zero.
    """
    global _send_counts_day, _send_counts_month
    _send_counts_day = max(0, _send_counts_day - 1)
    _send_counts_month = max(0, _send_counts_month - 1)


# ── Templates ────────────────────────────────────────────────────────────────


def _invite_html(team_name: str, role: str, link: str) -> str:
    tn = html.escape(team_name)
    rl = html.escape(role)
    lk = html.escape(link, quote=True)
    return f"""\
<div style="background:#060b14;padding:32px 16px;font-family:Helvetica,Arial,sans-serif;">
  <div style="max-width:480px;margin:0 auto;background:#0d1a2d;border:1px solid #1e293b;border-radius:12px;padding:28px;">
    <h2 style="color:#e2e8f0;margin:0 0 8px;">You're invited to {tn}</h2>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 20px;">You've been invited to join the team's memory graph in Tortoise as <strong style="color:#cbd5e1;">{rl}</strong>.</p>
    <a href="{lk}" style="display:inline-block;background:#06b6d4;color:#04121a;text-decoration:none;font-weight:600;padding:12px 24px;border-radius:8px;">Accept invitation</a>
    <p style="color:#64748b;font-size:12px;margin:24px 0 0;">This invitation expires in 7 days and can only be used once. If you weren't expecting this, you can ignore it.</p>
  </div>
</div>"""


def _invite_text(team_name: str, role: str, link: str) -> str:
    return (
        f"You're invited to {team_name}\n\n"
        f"You've been invited to join the team's memory graph in Tortoise as {role}.\n\n"
        f"Accept: {link}\n\n"
        "This invitation expires in 7 days and can only be used once."
    )


# ── Low-level send ───────────────────────────────────────────────────────────


def _is_transient(err: Exception) -> bool:
    """429/408/5xx/timeout/network are transient; 4xx are permanent."""
    if isinstance(err, httpx.TimeoutException):
        return True
    if isinstance(err, httpx.HTTPStatusError):
        return err.response.status_code in (408, 429) or err.response.status_code >= 500
    return isinstance(err, (httpx.NetworkError, httpx.TransportError))


async def _send_resend(to: str, subject: str, html_body: str, text_body: str,
                       idempotency_key: str | None = None) -> dict:
    """POST to Resend /emails. Raises on failure (caller decides retry)."""
    api_key = _env("RESEND_API_KEY")
    if api_key is None:
        raise RuntimeError("RESEND_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "tortoise-api/0.1",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    payload = {
        "from": _from_address(),
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }

    async with _send_semaphore:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(RESEND_URL, headers=headers, json=payload)
            resp.raise_for_status()
            # #1138: budget slot was already RESERVED at schedule time in
            # send_invite_email (P1 TOCTOU fix); on failure the caller refunds.
            return resp.json()


def _build_invite_link(token: str) -> str:
    return f"{_email_link_base()}/invite-accept.html?token={token}"


async def _send_invite_attempt(invitee_email: str, team_name: str, role: str,
                               token: str, invitation_id: str,
                               on_sent) -> None:
    """One attempt + one 0.5s retry on transient-only. Never raises."""
    link = _build_invite_link(token)
    subject = f"You're invited to {team_name}"
    for attempt in (0, 1):
        try:
            result = await _send_resend(
                invitee_email, subject,
                _invite_html(team_name, role, link),
                _invite_text(team_name, role, link),
                idempotency_key=f"invite:{invitation_id}",
            )
            message_id = (result or {}).get("id")
            try:
                if callable(on_sent):
                    on_sent(message_id)
            except Exception:  # noqa: BLE001 — stamping failure must not crash the send task
                logger.warning("email notify: on_sent callback failed for invite %s", invitation_id)
            logger.info("email notify: invite email accepted by provider (invite %s, msg %s)",
                        invitation_id, message_id)
            return
        except Exception as e:  # noqa: BLE001 — never raise out of the task
            last_err = e
            if attempt == 0 and _is_transient(e):
                await asyncio.sleep(0.5)
                continue
            break
    logger.warning("email notify: invite email failed for %s (%s)",
                   invitation_id, redact_safe(last_err))
    _refund_send()  # #1138 P1: provider rejected/failed the POST — free the slot


def send_invite_email(team_name: str, invitee_email: str, role: str,
                      token: str, invitation_id: str, on_sent=None) -> None:
    """Schedule the invite email best-effort (async). NEVER raises.

    Caller passes the plaintext invite token ONCE (hash-only at rest). The
    task is tracked in ``_pending_email_tasks`` for shutdown drain.
    """
    api_key = _env("RESEND_API_KEY")
    if _skip_channel("resend", api_key):
        return

    # #1138: hard-stop before scheduling when the send budget is exhausted — a
    # bulk blast must never silently 429 past the free-tier cap. Skip loud.
    # A slot is RESERVED here (synchronously, before create_task) — closing the
    # TOCTOU where N>cap concurrent schedules all passed the check before any
    # provider POST completed; _refund_send() gives it back on failure.
    exceeded, reason = _budget_exceeded()
    if exceeded:
        logger.warning(
            "email notify: invite email for %s (%s) SKIPPED — send budget exhausted (%s)",
            invitation_id, invitee_email, reason,
        )
        return
    _reserve_send()

    task = asyncio.create_task(
        _send_invite_attempt(invitee_email, team_name, role, token, invitation_id, on_sent)
    )
    _pending_email_tasks.add(task)
    task.add_done_callback(_pending_email_tasks.discard)


async def drain_pending_sends(timeout: float = 2.0) -> None:
    """Await in-flight best-effort sends (shutdown). Never raises."""
    if not _pending_email_tasks:
        return
    done, pending = await asyncio.wait(_pending_email_tasks, timeout=timeout)
    for t in pending:
        t.cancel()
