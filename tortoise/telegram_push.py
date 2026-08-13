"""Telegram push notifications — dual-channel alert sink (human-facing leg).

Standalone module: sends Telegram Bot API messages via httpx. Callers inject
bot_token + chat_id explicitly — no implicit env reads or global state. The
module is alert-kind-agnostic: any system (DR/backup, deploy health, CI
failures) can reuse it by calling ``send_message`` directly.

Per the Telegram Bot API:
- sendMessage auth via bot token in URL path
- Rate limit: 30 msg/s (irrelevant at our volume)
- chat_id is the target (private chat with the bot, group, or channel)

Integration pattern (from hosted_api.py):
    from tortoise.telegram_push import send_message
    def push_telegram(text: str) -> None:
        send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramPushError(RuntimeError):
    """Telegram API call failed — caller should handle (best-effort push)."""


def send_message(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    timeout: float = 15.0,
) -> dict:
    """Send a Telegram message via Bot API sendMessage.

    Args:
        bot_token: Telegram bot token (from @BotFather).
        chat_id: Target chat ID (private chat, group, or channel).
        text: Message text (plain text — no markdown escaping).
        timeout: HTTP request timeout in seconds.

    Returns:
        Parsed JSON response dict from Telegram.

    Raises:
        TelegramPushError: On any HTTP or API error.
    """
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, str] = {"chat_id": str(chat_id), "text": text}
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise TelegramPushError(
                f"telegram api returned ok=false: {body.get('description', 'unknown')}"
            )
        return body
    except httpx.HTTPStatusError as e:
        raise TelegramPushError(
            f"telegram api {e.response.status_code}: {e.response.text[:300]}"
        ) from e
    except httpx.RequestError as e:
        raise TelegramPushError(f"telegram request failed: {e}") from e
