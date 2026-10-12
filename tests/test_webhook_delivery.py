"""#2081 — outbound webhook delivery for the session-event stream (epic #2080 W4 phase 2).

The PUSH half of the delivery path. Pins the contracts the feature is sold on:

* registration is an SSRF-gated, capped, graph-scoped write that returns the
  signing secret ONCE (encrypted at rest, never re-read);
* dispatch is at-least-once and ORDERED per subscription (the cursor only
  advances past a 2xx), with retry/backoff, an attempt ceiling that DISABLES a
  dead subscription, and one receipt per attempt;
* the push path performs NO EP work (a payload is read from ``:GraphEvent`` —
  the "no per-push recompute" indicator);
* the kill switch stops egress without breaking registration;
* the REST surface (POST/GET/DELETE + receipts) is auth-fail-closed and
  422s a rejected target.

Engine tests use the URI-aware ``sdk_factory`` fixture (embedded or docker);
the REST tests mirror ``tests/test_hosted_volunteer_context.py``'s
auth-override harness.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

pytest.importorskip("cryptography")  # the at-rest encryption dependency

from tortoise import webhook_delivery as wd
from tortoise.security import validate_webhook_url


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """A REAL Fernet key per test — the secret is encrypted with it."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _dispatch_on(monkeypatch):
    """Egress ON by default in tests; the kill-switch test overrides it."""
    monkeypatch.delenv("TORTOISE_WEBHOOK_DISPATCH", raising=False)


class FakeTransport:
    """Records every POST and replays a scripted status/error per call.

    A script entry is an int (HTTP status) or ``"err"`` (a transport failure,
    i.e. ``(None, error)``). The script is consumed in order; when it runs out
    the default is 200.
    """

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if item == "err":
            return None, "connection refused"
        if isinstance(item, Exception):
            raise item
        return item, None


def _seed(sdk_factory, n=2):
    """Seed ``n`` durable ``:GraphEvent`` rows — the stream dispatch reads.

    Written through ``event_store`` (the same writer a real mutation uses)
    rather than ``create_point``: this file is about DELIVERY, and a real point
    write loads the embedding model, which costs minutes on a cold runner for
    no coverage here (``tests/test_event_store.py`` already pins emission).
    """
    from tortoise import event_store
    from tortoise.ids import ulid

    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    for i in range(n):
        event_store.append_event(proj, event_store.next_seq(proj), "PointAdded",
                                 {"id": f"pt_{i}"}, f"evt-{ulid()}")
    return sdk


# ── registration ──────────────────────────────────────────────────────────

def test_create_returns_secret_once_and_reads_never_carry_it(sdk_factory):
    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()
    created = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                     types=["PointAdded"],
                                     description="pilot app")
    assert created["secret"].startswith("whsec_")
    assert created["secret_set"] is True
    assert created["active"] is True and created["cursor"] == 0

    for record in (wd.get_subscription(proj, created["sub_id"]),
                   *wd.list_subscriptions(proj)):
        assert "secret" not in record, record
        assert record["secret_set"] is True
        assert record["types"] == ["PointAdded"]


def test_secret_is_encrypted_at_rest(sdk_factory):
    from tortoise.crypto import decrypt_token

    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()
    created = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    stored = proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) RETURN s.secret",
        params={"id": created["sub_id"]},
    ).result_set[0][0]
    assert stored != created["secret"], "the signing secret is stored in the clear"
    assert decrypt_token(stored) == created["secret"]


@pytest.mark.parametrize("url", [
    "http://localhost/cb",
    "http://localhost./cb",              # trailing dot: an FQDN spelling of loopback
    "http://LOCALHOST.:8080/cb",         # ... and case-insensitive with a port
    "http://127.1/cb",                   # short form: numeric, but ipaddress rejects it
    "http://0x7f.1/cb",                  # ... and the same address in hex octets
    "http://0x7f.0.0.1/cb",
    "http://127.0x1/cb",
    "http://0x7f.0x0.0x0.0x1/cb",
    "http://2130706433/cb",              # decimal form of 127.0.0.1
    "http://0177.0.0.1/cb",              # leading-zero (octal-looking) octet
    "http://0/cb",                       # 0.0.0.0
    "http://127.0.0.1/cb",
    "http://[::1]/cb",
    "https://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://10.0.0.5/cb",
    "http://192.168.1.7/cb",
    "http://db/cb",                      # single-label container name
    "https://hooks.internal/cb",
    "file:///etc/passwd",
    "ftp://hooks.example.com/cb",
    "gopher://hooks.example.com/",
    "https://user:pw@hooks.example.com/cb",
])
def test_registration_rejects_local_and_non_http_targets(sdk_factory, url):
    sdk = _seed(sdk_factory, n=0)
    with pytest.raises(ValueError):
        wd.create_subscription(sdk._get_proj(), url=url)
    assert validate_webhook_url  # the guard is a public, reusable primitive


def test_public_https_target_is_accepted(sdk_factory):
    sdk = _seed(sdk_factory, n=0)
    created = wd.create_subscription(
        sdk._get_proj(), url="https://hooks.example.com/tortoise")
    assert created["url"] == "https://hooks.example.com/tortoise"


def test_guard_covers_every_host_the_shared_loopback_predicate_refuses():
    """Containment vs ``config.LOOPBACK_HOSTS`` — the single-source rule.

    ``security.py`` is stdlib-only and cannot import that constant, so this is
    the pin that keeps the two from diverging as the shared set grows: every
    host the redirect predicate calls loopback must be refused as a webhook
    target.
    """
    from tortoise.config import LOOPBACK_HOSTS

    assert LOOPBACK_HOSTS, "the shared loopback set went empty — enumeration broke"
    for host in sorted(LOOPBACK_HOSTS):
        url = f"http://[{host}]/x" if ":" in host else f"http://{host}/x"
        with pytest.raises(ValueError):
            validate_webhook_url(url)


def test_unknown_event_type_is_rejected(sdk_factory):
    sdk = _seed(sdk_factory, n=0)
    with pytest.raises(ValueError, match="unknown event type"):
        wd.create_subscription(sdk._get_proj(),
                               url="https://hooks.example.com/cb",
                               types=["NotARealEvent"])


def test_per_graph_subscription_cap(sdk_factory):
    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()
    for i in range(wd.MAX_SUBSCRIPTIONS):
        wd.create_subscription(proj, url=f"https://hooks.example.com/{i}")
    with pytest.raises(ValueError, match="subscriptions"):
        wd.create_subscription(proj, url="https://hooks.example.com/over")


def test_delete_removes_subscription_and_receipts(sdk_factory):
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    wd.dispatch_pending(proj, transport=FakeTransport([200]))
    assert wd.read_receipts(proj, sub["sub_id"])
    assert wd.delete_subscription(proj, sub["sub_id"]) is True
    assert wd.get_subscription(proj, sub["sub_id"]) is None
    assert wd.read_receipts(proj, sub["sub_id"]) == []
    assert wd.delete_subscription(proj, "sub_missing") is False


# ── dispatch: ordering, at-least-once, receipts ───────────────────────────

def test_dispatch_delivers_in_seq_order_and_advances_the_cursor(sdk_factory):
    sdk = _seed(sdk_factory, n=3)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"])
    transport = FakeTransport([200, 200, 200])
    summary = wd.dispatch_pending(proj, transport=transport)
    assert summary["delivered"] == 3 and summary["failed"] == 0
    seqs = [json.loads(call[1])["event"]["seq"] for call in transport.calls]
    assert seqs == [1, 2, 3]
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == 3
    receipts = wd.read_receipts(proj, sub["sub_id"])
    assert [r["status"] for r in receipts] == ["delivered"] * 3


def test_delivery_envelope_and_signature_verify_with_the_returned_secret(sdk_factory):
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    transport = FakeTransport([200])
    wd.dispatch_pending(proj, transport=transport)
    url, body, headers = transport.calls[0]
    assert url == "https://hooks.example.com/cb"
    envelope = json.loads(body)
    assert envelope["subscription_id"] == sub["sub_id"]
    assert envelope["delivery_id"] == f"{sub['sub_id']}:1"
    assert envelope["event"]["type"] == "PointAdded"
    assert envelope["event"]["payload"]["id"]
    assert headers["X-Tortoise-Delivery"] == f"{sub['sub_id']}:1"
    assert headers["X-Tortoise-Event"] == "PointAdded"
    # The consumer-side half: the RETURNED secret verifies the REAL delivery.
    sig = headers["X-Tortoise-Signature"]
    ts = int(sig.split("t=", 1)[1].split(",", 1)[0])
    assert wd.verify_signature(sub["secret"], sig, body, now=ts) is True
    assert wd.verify_signature("whsec_wrong", sig, body, now=ts) is False


def test_signature_helper_rejects_tamper_and_stale_timestamps():
    secret, ts, body = "whsec_test", 1_700_000_000, b'{"a":1}'
    header = wd.signature_header(secret, ts, body)
    assert wd.verify_signature(secret, header, body, now=ts) is True
    assert wd.verify_signature(secret, header, b'{"a":2}', now=ts) is False
    assert wd.verify_signature("whsec_other", header, body, now=ts) is False
    assert wd.verify_signature(secret, header, body,
                               now=ts + wd.SIGNATURE_TOLERANCE_S + 1) is False
    assert wd.verify_signature(secret, "garbage", body, now=ts) is False
    assert wd.verify_signature(secret, "", body, now=ts) is False


def test_only_matching_types_are_pushed(sdk_factory):
    from tortoise import event_store

    sdk = _seed(sdk_factory, n=2)
    proj = sdk._get_proj()
    event_store.append_event(proj, event_store.next_seq(proj), "PointRetracted",
                             {"id": "pt_0"}, "evt-retract-1")
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointRetracted"])
    transport = FakeTransport([200, 200])
    summary = wd.dispatch_pending(proj, transport=transport)
    assert summary["delivered"] == 1
    assert json.loads(transport.calls[0][1])["event"]["type"] == "PointRetracted"
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == 3


def test_failure_stops_the_batch_at_the_head_of_line(sdk_factory):
    """A failed event is retried FIRST — the next event is not overtaken."""
    sdk = _seed(sdk_factory, n=2)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    transport = FakeTransport([500])
    summary = wd.dispatch_pending(proj, transport=transport)
    assert summary["failed"] == 1 and summary["delivered"] == 0
    assert len(transport.calls) == 1, "the second event was pushed past a failure"
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == 0


def test_failure_backs_off_then_retries_the_same_event(sdk_factory):
    import datetime

    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    base = datetime.datetime.now(datetime.UTC)
    first = wd.dispatch_pending(proj, transport=FakeTransport([503]), now=base)
    assert first["failed"] == 1
    record = wd.get_subscription(proj, sub["sub_id"])
    assert record["attempts"] == 1 and record["active"] is True
    expected = base + datetime.timedelta(seconds=wd.backoff_seconds(1))
    assert record["next_attempt_at"][:19] == expected.isoformat()[:19]
    assert record["last_error"] == "HTTP 503"
    assert [r["status"] for r in wd.read_receipts(proj, sub["sub_id"])] == ["retrying"]

    # Before the backoff elapses: not due → nothing attempted.
    early = FakeTransport([200])
    assert wd.dispatch_pending(proj, transport=early,
                               now=base + datetime.timedelta(seconds=5))[
                                   "subscriptions"] == 0
    assert early.calls == []

    # After it: the SAME event is retried (at-least-once) and only then advances.
    late = FakeTransport([200])
    wd.dispatch_pending(proj, transport=late,
                        now=base + datetime.timedelta(seconds=31))
    assert json.loads(late.calls[0][1])["event"]["seq"] == 1
    record = wd.get_subscription(proj, sub["sub_id"])
    assert record["cursor"] == 1 and record["attempts"] == 0
    assert [r["status"] for r in wd.read_receipts(proj, sub["sub_id"])] == [
        "delivered", "retrying"]


def test_backoff_is_bounded_and_exponential():
    assert [wd.backoff_seconds(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert wd.backoff_seconds(50) == wd.MAX_BACKOFF_S
    assert wd.backoff_seconds(0) == wd.BASE_BACKOFF_S


def test_exhausted_subscription_is_disabled_not_retried_forever(sdk_factory, monkeypatch):
    import datetime

    monkeypatch.setenv("TORTOISE_WEBHOOK_MAX_ATTEMPTS", "3")
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    base = datetime.datetime.now(datetime.UTC)
    for i in range(3):
        summary = wd.dispatch_pending(
            proj, transport=FakeTransport([503]),
            now=base + datetime.timedelta(hours=i))
        assert summary["failed"] == 1
    record = wd.get_subscription(proj, sub["sub_id"])
    assert record["attempts"] == 3
    assert record["active"] is False and record["disabled_at"]
    assert "3 consecutive delivery failures" in record["disabled_reason"]
    assert [r["status"] for r in wd.read_receipts(proj, sub["sub_id"])] == [
        "exhausted", "retrying", "retrying"]
    # Disabled is terminal for dispatch — even long after any backoff.
    after = FakeTransport([200])
    assert wd.dispatch_pending(proj, transport=after,
                               now=base + datetime.timedelta(days=30))[
                                   "subscriptions"] == 0
    assert after.calls == []


def test_transport_error_is_a_receipt_never_a_raise(sdk_factory):
    import datetime

    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    base = datetime.datetime.now(datetime.UTC)
    summary = wd.dispatch_pending(proj, transport=FakeTransport(["err"]), now=base)
    assert summary["failed"] == 1
    receipt = wd.read_receipts(proj, sub["sub_id"])[0]
    assert receipt["status"] == "retrying" and receipt["error"] == "connection refused"
    # A transport that RAISES is also a failed delivery, not an exception.
    # (Past the backoff the first failure armed, or the pass is not due.)
    later = FakeTransport([RuntimeError("socket exploded")])
    assert wd.dispatch_pending(
        proj, transport=later,
        now=base + datetime.timedelta(seconds=wd.backoff_seconds(1) + 1),
    )["failed"] == 1
    assert wd.read_receipts(proj, sub["sub_id"])[0]["status"] == "retrying"


def test_kill_switch_stops_egress_but_registration_still_works(
        sdk_factory, monkeypatch):
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    monkeypatch.setenv("TORTOISE_WEBHOOK_DISPATCH", "0")
    transport = FakeTransport([200])
    summary = wd.dispatch_pending(proj, transport=transport)
    assert summary["enabled"] is False and transport.calls == []
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == 0
    monkeypatch.setenv("TORTOISE_WEBHOOK_DISPATCH", "1")
    assert wd.dispatch_pending(proj, transport=FakeTransport([200]))[
        "delivered"] == 1


def test_no_subscriptions_means_no_graph_write(sdk_factory):
    """A dream-triggered pass on a graph with no subscriptions is a pure no-op."""
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()

    def _counts():
        rows = proj.g.query(
            "MATCH (n) WHERE NOT n:GraphEventMeta RETURN count(n)"
        ).result_set
        return int(rows[0][0])
    before = _counts()
    summary = wd.dispatch_pending(proj, transport=FakeTransport([200]))
    assert summary["subscriptions"] == 0
    assert _counts() == before


def test_push_path_recomputes_no_beliefs(sdk_factory, monkeypatch):
    """Indicator 2: the pushed payload is READ from the event store — the push
    path calls the EP engine ZERO times. Any belief computation here means the
    precompute claim is false, so the EP entry point is booby-trapped."""
    import tortoise.ep as ep_mod

    def _boom(*a, **k):
        raise AssertionError("the push path called the EP engine")

    monkeypatch.setattr(ep_mod.TortoiseEP, "compute_confidence", _boom)
    sdk = _seed(sdk_factory, n=2)
    proj = sdk._get_proj()
    wd.create_subscription(proj, url="https://hooks.example.com/cb")
    assert wd.dispatch_pending(proj, transport=FakeTransport([200, 200]))[
        "delivered"] == 2


# ── the dream-cycle dispatch hook ─────────────────────────────────────────

def test_dream_hook_is_best_effort_and_kill_switched(monkeypatch):
    """#2081: the hook that runs the push at the end of a tenant's dream.

    Pins the two properties that keep it safe to call from `_dream_worker`:
    a dispatcher fault is SWALLOWED and logged (never a dream failure, never a
    fatal), and the kill switch short-circuits BEFORE any graph/SDK is built.
    """
    import asyncio

    import tortoise.hosted_api as ha

    built: list[dict] = []

    class FakeSDK:
        def _get_proj(self):
            return object()

        def close(self):
            built.append({"closed": True})

    def _make_sdk(**kwargs):
        built.append(kwargs)
        return FakeSDK()

    monkeypatch.setattr(ha, "_make_sdk", _make_sdk)

    def _boom(*a, **k):
        raise RuntimeError("dispatcher exploded")

    monkeypatch.setattr(wd, "dispatch_pending", _boom)
    asyncio.run(ha._dispatch_webhooks("team-x"))  # must NOT propagate
    assert any("namespace" in row for row in built), built
    assert any(row.get("closed") for row in built), "the SDK leaked open"

    built.clear()
    monkeypatch.setenv("TORTOISE_WEBHOOK_DISPATCH", "0")
    asyncio.run(ha._dispatch_webhooks("team-x", "g_custom"))
    assert built == [], "the kill switch still built an SDK / opened a graph"


# ── REST surface ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    """Auth-override harness (mirrors tests/test_hosted_volunteer_context.py)."""
    import tempfile

    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app, get_current_org

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_org] = lambda: dict(
            {"org_id": "team-001", "tier": "free", "key_id": None})
        _orig_init = ha_mod.TortoiseSDK.__init__

        def _patched_init(self, db_path_arg=None, *, namespace=None,
                          graph_name=None, **kwargs):
            _orig_init(self, db_path, namespace=namespace, graph_name=graph_name)

        ha_mod.TortoiseSDK.__init__ = _patched_init
        os.environ["TORTOISE_DB_PATH"] = db_path
        ha_mod._FALLBACK_KEEPALIVE.clear()
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig_init
            app.dependency_overrides.clear()


def test_rest_registration_lifecycle(client):
    created = client.post("/v1/context/subscriptions",
                          json={"url": "https://hooks.example.com/cb",
                                "types": ["PointAdded"],
                                "description": "pilot"})
    assert created.status_code == 201, created.text
    body = created.json()
    sub_id = body["sub_id"]
    assert body["secret"].startswith("whsec_") and body["active"] is True

    listed = client.get("/v1/context/subscriptions")
    assert listed.status_code == 200
    subs = listed.json()["subscriptions"]
    assert [s["sub_id"] for s in subs] == [sub_id]
    assert "secret" not in subs[0] and subs[0]["secret_set"] is True

    # A fresh subscription has no receipts yet.
    empty = client.get(f"/v1/context/subscriptions/{sub_id}/deliveries")
    assert empty.status_code == 200 and empty.json()["deliveries"] == []

    deleted = client.delete(f"/v1/context/subscriptions/{sub_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get("/v1/context/subscriptions").json()["subscriptions"] == []
    assert client.delete(f"/v1/context/subscriptions/{sub_id}").status_code == 404
    assert client.get(
        "/v1/context/subscriptions/sub_missing/deliveries").status_code == 404


def test_rest_rejects_ssrf_target_and_unknown_type_and_bad_body(client):
    bad_url = client.post("/v1/context/subscriptions",
                          json={"url": "http://127.0.0.1:8080/cb"})
    assert bad_url.status_code == 422, bad_url.text
    assert "globally routable" in json.dumps(bad_url.json())

    bad_type = client.post("/v1/context/subscriptions",
                           json={"url": "https://hooks.example.com/cb",
                                 "types": ["Nope"]})
    assert bad_type.status_code == 422
    assert "unknown event type" in json.dumps(bad_type.json())

    missing_url = client.post("/v1/context/subscriptions", json={})
    assert missing_url.status_code == 422


def test_rest_requires_auth(client):
    """No dependency override → the REAL auth dependency → 401, never a
    subscription. Guards the fail-closed half of a write surface."""
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    app.dependency_overrides.clear()
    with TestClient(app) as tc:
        r = tc.post("/v1/context/subscriptions",
                    json={"url": "https://hooks.example.com/cb"})
        assert r.status_code == 401, r.text
        assert tc.get("/v1/context/subscriptions").status_code == 401
        assert tc.delete("/v1/context/subscriptions/sub_x").status_code == 401
        assert tc.get("/v1/context/subscriptions/sub_x/deliveries").status_code == 401
    assert ha_mod  # imported for the harness parity above
