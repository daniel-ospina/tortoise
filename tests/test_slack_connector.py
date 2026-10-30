"""Tests for Slack connector — message/thread → EventRecorded mapping + webhook sig."""
from __future__ import annotations  # noqa: I001

import json
import socket
import pytest  # noqa: F401
from tortoise.connectors.slack import SlackConnector, _ts_to_iso, _verify_slack_sig


# The webhook legs must never demand a HOST-GLOBAL port be free. They used to
# hardcode 18994/18995/18999, and 18995 was named by two of them at once:
#
#   * under `-n 4` (the shards run xdist with 4 workers) the two 18995 legs are
#     co-scheduled and the second `HTTPServer.bind` raises
#     `OSError: [Errno 98] Address already in use` — measured in CI at
#     `840fd3078` (run 41007224545, job `test (g)` 123914139669), where BOTH
#     legs red together;
#   * and a PEER lane's leftover pytest process holding 18995 reds them for any
#     unrelated PR on a shared self-hosted runner (measured locally: pid 4040760
#     from a previous day's shard was still bound to 127.0.0.1:18995).
#
# `_free_port()` is the same helper `tests/test_monitoring.py` and
# `tests/test_write_doors_9068.py` already use: ask the OS for a port nobody
# holds instead of asserting one is free.
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    assert ev["sourceKind"] == "slack_message"
    # #388: permalink enrichment is ingest-scoped — the pure mapper carries no
    # sourceUrl (projection falls back to the container-level slack:C01 key)
    assert "sourceUrl" not in ev


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


# ── #388: permalink-with-fallback enrichment (ingest-scoped) ───────────

def test_enrich_source_urls_attaches_permalink():
    """#388: ingest() enriches slack events with per-message permalinks via
    chat.getPermalink; poll() alone stays API-call-free."""
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C01"})
    events = [sc._message_to_event({
        "text": "hello", "ts": "1690000000.123456", "user": "U01"
    })]

    class _FakeClient:
        def chat_getPermalink(self, channel=None, message_ts=None):
            return {"permalink": f"https://ws.slack.com/archives/{channel}/p{message_ts}"}

    sc._client = lambda: _FakeClient()
    sc._enrich_source_urls(events)
    assert events[0]["sourceUrl"] == (
        "https://ws.slack.com/archives/C01/p1690000000.123456")


def test_enrich_source_urls_falls_back_when_permalink_fails():
    """#388: permalink failure/absence leaves sourceUrl unset — the projection
    falls back to the container-level `slack:{channel}` Source key."""
    sc = SlackConnector(config={"token": "xoxb-test", "channel_id": "C01"})
    events = [sc._message_to_event({
        "text": "hello", "ts": "1690000000.123456", "user": "U01"
    })]

    class _BoomClient:
        def chat_getPermalink(self, channel=None, message_ts=None):
            raise RuntimeError("api down")

    sc._client = lambda: _BoomClient()
    sc._enrich_source_urls(events)
    assert "sourceUrl" not in events[0]


def test_enrich_source_urls_noop_without_config():
    """#388: no token/channel → enrichment is a no-op (no client, no calls)."""
    sc = SlackConnector(config={"token": "", "channel_id": ""})
    sc._client = lambda: (_ for _ in ()).throw(AssertionError("must not init"))
    sc._enrich_source_urls([{"sourceKind": "slack_message"}])  # no crash


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
    import hmac, hashlib, time  # noqa: E401, I001
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
    wanted = _free_port()
    sc = SlackConnector(config={
        "token": "xoxb-test",
        "webhook_port": wanted,
        "signing_secret": "",
    })
    port = sc.start_webhook()
    assert port == wanted, "the connector must serve on the port it was configured with"

    try:
        payload = json.dumps({
            "type": "url_verification",
            "challenge": "test-challenge-abc",
        }).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/",
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
        "webhook_port": _free_port(),
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
        "webhook_port": _free_port(),
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
            assert False, "expected HTTP 500"  # noqa: B011
        except urllib.error.HTTPError as e:
            assert e.code == 500
        except urllib.error.URLError:
            assert False, "handler must respond with 500, not drop the connection"  # noqa: B011
    finally:
        sc.stop_webhook()


def test_webhook_non_dict_payload_returns_400():
    """#331 (review r4): valid JSON that is not an object (array/string)
    must get HTTP 400 — not an AttributeError-dropped connection."""
    import urllib.error
    import urllib.request

    sc = SlackConnector(config={
        "token": "xoxb-test",
        "webhook_port": _free_port(),
        "signing_secret": "",
    })
    port = sc.start_webhook()
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}/", data=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected HTTP 400"  # noqa: B011
        except urllib.error.HTTPError as e:
            assert e.code == 400
        except urllib.error.URLError:
            assert False, "handler must respond with 400, not drop the connection"  # noqa: B011
    finally:
        sc.stop_webhook()


def test_the_legacy_fixed_ports_being_taken_cannot_red_these_legs():
    """A port this file used to hardcode cannot red the suite when it is taken.

    The four webhook legs above name 18994/18995/18999 in their history, and
    18995 twice at once. This leg OCCUPIES all three (when the host lets it) and
    then starts a webhook, asserting it comes up and serves anyway.

    FAIL VALUE, MEASURED: point ``_free_port()`` at the constant 18995 (the
    value the old legs shared) and this leg reds with
    ``OSError: [Errno 98] Address already in use`` at ``HTTPServer.bind`` — the
    exact CI failure of SHARD ``test (g)`` at ``840fd3078`` (job 123914139669),
    where ``test_webhook_start_is_idempotent`` and
    ``test_webhook_non_dict_payload_returns_400`` red together because xdist
    co-scheduled two legs that both demanded 18995.
    """
    import urllib.request

    legacy = (18994, 18995, 18999)
    blockers: list[socket.socket] = []
    for port in legacy:
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.listen(1)
            blockers.append(s)
        except OSError:  # pragma: no cover - a peer already holds it, which is the point
            s.close()
    try:
        sc = SlackConnector(config={
            "token": "xoxb-test",
            "webhook_port": _free_port(),
            "signing_secret": "",
        })
        port = sc.start_webhook()
        try:
            assert port and port not in legacy, (
                f"the webhook came up on a legacy host-global port ({port}) — "
                "this file must allocate its port, never demand one"
            )
            payload = json.dumps({
                "type": "url_verification",
                "challenge": "collision-probe",
            }).encode()
            req = urllib.request.Request(
                f"http://localhost:{port}/",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                assert r.status == 200
                assert r.read().decode() == "collision-probe"
        finally:
            sc.stop_webhook()
    finally:
        for s in blockers:
            s.close()
