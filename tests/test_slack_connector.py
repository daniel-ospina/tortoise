"""Tests for Slack connector — message/thread → EventRecorded mapping + webhook sig."""
from __future__ import annotations

import json
import pytest
from tortoise.connectors.slack import SlackConnector, _ts_to_iso, _verify_slack_sig


# ── Message → Event mapping ──────────────────────────────────────

def test_message_to_event_basic():
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C01"})
    msg = {
        "text": "Hello world",
        "ts": "1690000000.123456",
        "user": "U01",
    }
    ev = sc._message_to_event(msg)
    assert ev is not None
    assert ev["type"] == "EventRecorded"
    assert ev["eventKind"] == "slack.message"
    assert ev["eventId"].startswith("slack-msg-C01-1690000000-123456")
    assert ev["subject"] == "slack:C01:U01"
    assert ev["object"] == "Hello world"
    assert ev["source"] == "slack:C01"
    assert ev["participants"] == ["U01"]
    assert ev["parentEvent"] is None


def test_message_to_event_thread_reply():
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C02"})
    reply = {
        "text": "Good point",
        "ts": "1690000001.999999",
        "user": "U02",
    }
    ev = sc._message_to_event(reply, parent_ts="1690000000.000000")
    assert ev is not None
    assert ev["eventKind"] == "slack.message.thread_reply"
    assert ev["parentEvent"] is not None
    assert "1690000000-000000" in ev["parentEvent"]


def test_message_to_event_thread_reply_from_msg():
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C03"})
    msg = {
        "text": "Reply in thread",
        "ts": "1690000002.000000",
        "user": "U03",
        "thread_ts": "1690000000.000000",
    }
    ev = sc._message_to_event(msg)
    assert ev is not None
    assert ev["parentEvent"] is None  # not a reply itself


def test_message_to_event_skips_empty():
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C"})
    assert sc._message_to_event({"text": "", "ts": "1.0"}) is None
    assert sc._message_to_event({"text": "x", "ts": ""}) is None


def test_message_to_event_truncates_long_object():
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C"})
    long_text = "x" * 300
    ev = sc._message_to_event({"text": long_text, "ts": "1.1", "user": "U"})
    assert ev is not None
    assert len(ev["object"]) == 200


def test_message_to_event_unknown_user():
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C"})
    ev = sc._message_to_event({"text": "hi", "ts": "1.2"})
    assert ev is not None
    assert ev["subject"] == "slack:C:unknown"


# ── Timestamp conversion ─────────────────────────────────────────

def test_ts_to_iso():
    result = _ts_to_iso("1690000000.000000")
    assert result.startswith("2023-07-22")
    assert "T" in result


def test_ts_to_iso_invalid_returns_now():
    result = _ts_to_iso("not-a-ts")
    assert result  # non-empty, ends with Z
    assert "T" in result


# ── Polling ──────────────────────────────────────────────────────

def test_poll_empty_config_returns_empty():
    sc = SlackConnector(config={"token": "", "channel_id": ""})
    assert sc.poll() == []

    sc2 = SlackConnector(config={"token": "x", "channel_id": ""})
    assert sc2.poll() == []


# ── Webhook signature ────────────────────────────────────────────

def test_verify_slack_sig_valid():
    import hmac, hashlib, time
    secret = b"slacksecret"
    timestamp = str(int(time.time()))
    body = b'{"event":{"type":"message"}}'
    base = f"v0:{timestamp}:{body.decode()}"
    sig = hmac.new(secret, base.encode(), hashlib.sha256).hexdigest()
    assert _verify_slack_sig(secret, timestamp, f"v0={sig}", body)


def test_verify_slack_sig_invalid():
    assert not _verify_slack_sig(b"x", "1111111111", "v0=bad", b"{}")
    assert not _verify_slack_sig(b"x", "", "v0=x", b"{}")
    assert not _verify_slack_sig(b"x", "1111111111", "", b"{}")


def test_verify_slack_sig_old_timestamp():
    import time
    old_ts = str(int(time.time()) - 600)  # 10 min old
    assert not _verify_slack_sig(b"x", old_ts, "v0=anything", b"{}")


# ── Webhook start/stop ──────────────────────────────────────────

def test_webhook_disabled_when_port_zero():
    sc = SlackConnector(config={"token": "x", "webhook_port": 0})
    port = sc.start_webhook()
    assert port == 0


def test_webhook_url_verification():
    """Slack URL verification challenge returns 200 with challenge text."""
    import urllib.request
    sc = SlackConnector(config={
        "token": "xoxb-test",
        "webhook_port": 18999,
        "signing_secret": "",
    })
    port = sc.start_webhook()
    assert port == 18999

    try:
        payload = json.dumps({
            "type": "url_verification",
            "challenge": "test-challenge-abc",
        }).encode()
        req = urllib.request.Request(
            "http://localhost:18999/",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert r.read().decode() == "test-challenge-abc"
    finally:
        sc.stop_webhook()


# ── #331: webhook double-start resource leak + swallowed exceptions ──

def test_webhook_start_is_idempotent():
    """#331: starting twice must not double-bind the socket (pre-fix:
    second HTTPServer(...) raised Address already in use / orphaned thread)."""
    import urllib.request
    sc = SlackConnector(config={
        "token": "xoxb-test",
        "webhook_port": 18995,
        "signing_secret": "",
    })
    port1 = sc.start_webhook()
    server1 = sc._server
    port2 = sc.start_webhook()  # double-start — must be idempotent
    assert port1 == port2
    assert sc._server is server1, "second start must not replace the server"

    # Original socket must still be serving (url_verification challenge)
    payload = json.dumps({"type": "url_verification",
                          "challenge": "challenge-abc"}).encode()
    req = urllib.request.Request(
        f"http://localhost:{port1}/", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert r.read().decode() == "challenge-abc"
    sc.stop_webhook()

    # stop + restart must work (socket fully released)
    port3 = sc.start_webhook()
    assert port3 == port1
    sc.stop_webhook()


def test_webhook_processing_error_returns_500():
    """#331: an exception while processing an event must be LOGGED and
    answered with HTTP 500 — not silently dropped."""
    import urllib.error
    import urllib.request

    def boom(ev):
        raise RuntimeError("downstream failed")

    sc = SlackConnector(config={
        "token": "xoxb-test",
        "webhook_port": 18994,
        "signing_secret": "",
    })
    port = sc.start_webhook(on_event=boom)
    try:
        payload = json.dumps({
            "type": "event_callback",
            "event": {"type": "message", "text": "hello",
                      "ts": "1690000000.000001", "user": "u1"},
        }).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/", data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected HTTP 500"
        except urllib.error.HTTPError as e:
            assert e.code == 500
        except urllib.error.URLError:
            assert False, "handler must respond with 500, not drop the connection"
    finally:
        sc.stop_webhook()
