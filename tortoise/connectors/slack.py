"""P1-5 #6977 / GAP-20 #7008: Slack connector.

Polls Slack channels for messages and threads, maps them to EventRecorded JSONL.
Requires: slack-sdk (pip install slack-sdk).
"""
from __future__ import annotations

import json
import hmac
import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SlackConnector:
    """Poll Slack channels/threads via Slack SDK + optional Events API webhook."""

    def __init__(self, config: dict[str, Any] | None = None, api=None):
        cfg = config or {}
        # Env vars take precedence over config for secrets (#324)
        # os.environ.get returns None when not set; explicit None check
        # so we can distinguish "not set" from "set to empty string"
        env_token = os.environ.get("SLACK_BOT_TOKEN")
        self.token = env_token if env_token is not None else cfg.get("token", "")
        env_signing = os.environ.get("SLACK_SIGNING_SECRET")
        self.signing_secret = (
            env_signing if env_signing is not None else cfg.get("signing_secret", "")
        )
        self.channel_id = cfg.get("channel_id", "")
        self.limit = int(cfg.get("limit", 100))
        self.days = int(cfg.get("days", 7))
        self.webhook_port = int(cfg.get("webhook_port", 0))
        self.api = api
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        if not self.token:
            logger.warning(
                "SLACK_BOT_TOKEN not set — Slack connector will be a no-op. "
                "Set SLACK_BOT_TOKEN env var or 'token' in connector config."
            )

    def _client(self):
        """Lazy-init Slack WebClient (imports only if used)."""
        from slack_sdk import WebClient
        return WebClient(token=self.token)

    # ── Polling ────────────────────────────────────────────────────

    def poll(self) -> list[dict]:
        """Fetch messages from configured channel → EventRecorded dicts."""
        if not self.token or not self.channel_id:
            return []

        client = self._client()
        events: list[dict] = []

        # Calculate oldest timestamp for the time window
        oldest = str(time.time() - self.days * 86400)

        try:
            result = client.conversations_history(
                channel=self.channel_id,
                limit=self.limit,
                oldest=oldest,
            )
            for msg in result.get("messages", []):
                ev = self._message_to_event(msg)
                if ev:
                    events.append(ev)

                # Fetch thread replies if any
                if msg.get("thread_ts") and msg.get("reply_count", 0) > 0:
                    thread = client.conversations_replies(
                        channel=self.channel_id,
                        ts=msg["thread_ts"],
                        limit=min(self.limit, 50),
                    )
                    for reply in thread.get("messages", []):
                        if reply.get("ts") != msg.get("thread_ts"):
                            rev = self._message_to_event(reply, parent_ts=msg["thread_ts"])
                            if rev:
                                events.append(rev)
        except (ConnectionError, TimeoutError, OSError):
            pass  # ponytail: network issues → return empty, don't crash

        return events

    def ingest(self, proj) -> int:
        """Poll + apply to projection. Returns count of applied events."""
        events = self.poll()
        count = 0
        for ev in events:
            proj.apply(ev)
            count += 1
        return count

    # ── Webhook (Events API) ───────────────────────────────────────

    def start_webhook(self, on_event: Callable[[dict], None] | None = None) -> int:
        if not self.webhook_port:
            return 0

        # #331: double-start must be a no-op — a second HTTPServer on the
        # same port raises Address already in use and orphans the first
        # server + its thread (socket + thread leak).
        # #331 (review r2): a DEAD serve_forever thread is not a running
        # server — close its stale socket so the re-bind below succeeds
        # (previously: silent no-op with nothing serving).
        if self._server is not None:
            if self._thread is not None and self._thread.is_alive():
                return self.webhook_port
            try:
                self._server.server_close()
            except OSError:
                pass
            self._server = None
            self._thread = None

        secret = self.signing_secret.encode() if self.signing_secret else None
        connector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)

                # Verify Slack signature
                if secret:
                    ts = self.headers.get("X-Slack-Request-Timestamp", "")
                    sig = self.headers.get("X-Slack-Signature", "")
                    if not _verify_slack_sig(secret, ts, sig, body):
                        self.send_response(403)
                        self.end_headers()
                        return

                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                # #331 (review r4): non-dict JSON (array/string body) must
                # get a 400 — a payload.get() below would AttributeError and
                # drop the connection (blind Slack retries, no trace).
                if not isinstance(payload, dict):
                    self.send_response(400)
                    self.end_headers()
                    return

                # Slack URL verification challenge
                if payload.get("type") == "url_verification":
                    challenge = payload.get("challenge", "")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(challenge.encode())
                    return

                # Handle event callbacks
                event_data = payload.get("event", {})
                try:
                    if event_data.get("type") == "message" and "subtype" not in event_data:
                        ev = connector._message_to_event(event_data)
                        if ev:
                            if on_event:
                                on_event(ev)
                            if connector.api:
                                connector.api.get_proj().apply(ev)
                except Exception:
                    # #331: processing failures must be visible and answered
                    # with HTTP 500 — a silently dropped connection makes
                    # Slack retry blindly with no server-side trace.
                    logger.exception("Slack webhook processing failed")
                    self.send_response(500)
                    self.end_headers()
                    return

                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", self.webhook_port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.webhook_port

    def stop_webhook(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            finally:
                # #331: shutdown() stops serve_forever but does NOT release
                # the listening socket — without server_close() the port
                # stays bound and stop→restart fails with EADDRINUSE.
                self._server.server_close()
                self._server = None
                self._thread = None

    # ── Event mapping ──────────────────────────────────────────────

    def _message_to_event(self, msg: dict, parent_ts: str | None = None) -> dict | None:
        """Map Slack message → EventRecorded dict."""
        text = msg.get("text", "")
        ts = msg.get("ts", "")
        if not text or not ts:
            return None

        user = msg.get("user", "unknown")
        thread_ts = parent_ts or msg.get("thread_ts")
        channel = self.channel_id or msg.get("channel", "")

        return {
            "type": "EventRecorded",
            "eventId": f"slack-msg-{channel}-{ts.replace('.', '-')}",
            "eventKind": "slack.message.thread_reply" if parent_ts else "slack.message",
            "subject": f"slack:{channel}:{user}",
            "object": text[:200],
            "startedAt": _ts_to_iso(ts),
            "endedAt": None,
            "source": f"slack:{channel}",
            "sourceKind": "slack_message",
            "participants": [user],
            "parentEvent": f"slack-msg-{channel}-{thread_ts.replace('.', '-')}" if parent_ts else None,
        }


def _ts_to_iso(ts: str) -> str:
    """Convert Slack timestamp (e.g. '1690000000.123456') to ISO format."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return _now_iso()


def _verify_slack_sig(secret: bytes, timestamp: str, sig: str, body: bytes) -> bool:
    """Verify Slack request signature (HMAC-SHA256)."""
    if not timestamp or not sig:
        return False
    # Reject old requests (> 5 min)
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    expected = hmac.new(secret, base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={expected}", sig)
