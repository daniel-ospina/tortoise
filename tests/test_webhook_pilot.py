"""#8102 — the pilot consumer on the REAL push path (indicator 4 of #2081).

`integrations/webhooks/pilot/app.py` is the pilot third-party app: its only
memory input is an inbound webhook POST. These tests pin the three indicators of
#8102's O/I/T against the SHIPPED dispatcher (`webhook_delivery.dispatch_pending`
with its real `httpx` transport) and a REAL loopback HTTP listener — not a fake
transport, so the headers, the envelope and the signature all travel the wire:

1. a push is received without any poll path (the app has no poll code — pinned
   structurally, so a future "convenience" read cannot quietly replace it);
2. a real signature is accepted and a tampered body (and a stale capture) is
   refused with 401 and no state change;
3. a FORCED re-delivery (cursor reset — the at-least-once contract in practice)
   produces 0 duplicated effects, across an app restart too.

Registration runs through the real `security.validate_webhook_url` SSRF gate
against a public URL shape; the stored target is then repointed at the loopback
listener, because the gate refuses loopback **by design** and must not grow an
exemption for a test.

The engine side uses the URI-aware ``sdk_factory`` fixture (embedded or docker),
mirroring ``tests/test_webhook_delivery.py``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import httpx
import pytest

pytest.importorskip("cryptography")  # the at-rest encryption dependency

from tortoise import webhook_delivery as wd
from tortoise.ids import ulid

PILOT_DIR = Path(__file__).resolve().parent.parent / "integrations" / "webhooks" / "pilot"


def _load_pilot():
    """Import the pilot app under a unique module name (no sys.path pollution)."""
    spec = importlib.util.spec_from_file_location(
        "tortoise_webhook_pilot_app_for_tests", PILOT_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PILOT = _load_pilot()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """A REAL Fernet key per test — the subscription secret is encrypted at rest."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _dispatch_on(monkeypatch):
    monkeypatch.delenv("TORTOISE_WEBHOOK_DISPATCH", raising=False)


def _seed(sdk_factory, n=2):
    """Seed ``n`` durable :GraphEvent rows through the real writer."""
    from tortoise import event_store

    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    ids = []
    for i in range(n):
        event_id = f"evt-{ulid()}"
        event_store.append_event(proj, event_store.next_seq(proj), "PointAdded",
                                 {"id": f"pt_{i}", "content": f"claim {i}"},
                                 event_id)
        ids.append(event_id)
    return sdk, ids


@pytest.fixture
def wired(sdk_factory, tmp_path):
    """A registered subscription + a live pilot app listening on loopback."""
    sdk, event_ids = _seed(sdk_factory, 2)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/pilot",
                                 types=["PointAdded"], description="pilot test")
    app = PILOT.PilotApp(secret=sub["secret"], state_dir=tmp_path / "app",
                         expected_subscription_id=sub["sub_id"])
    server = PILOT.serve(app, host="127.0.0.1", port=0).start()
    # Repoint ONLY the stored target: registration above ran through the real
    # SSRF gate (a loopback url would have been refused there, correctly).
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.url=$url",
        params={"id": sub["sub_id"], "url": server.url})
    env = SimpleNamespace(sdk=sdk, proj=proj, sub=sub, app=app, server=server,
                          url=server.url, event_ids=event_ids,
                          state_dir=tmp_path / "app")
    try:
        yield env
    finally:
        server.stop()
        app.close()


def _post(url: str, body: str, signature: str | None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers[PILOT.SIGNATURE_HEADER] = signature
    resp = httpx.post(url, content=body.encode("utf-8"), headers=headers,
                      timeout=10, follow_redirects=False)
    return resp.status_code, resp.json()


def _last_accepted(state_dir: Path) -> dict:
    records = [json.loads(line) for line in
               (state_dir / "received.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    for record in reversed(records):
        if record.get("status") == 200 and record.get("duplicate") is False:
            return record
    raise AssertionError("the pilot app logged no accepted delivery")


# ── indicator 1: content arrives by push, and there is no poll path ───────

def test_pilot_receives_a_real_push_and_injects_it_into_a_turn(wired):
    summary = wd.dispatch_pending(wired.proj)

    assert summary["delivered"] == 2, summary
    stats = wired.app.stats()
    assert stats["accepted"] == 2 and stats["events"] == 2, stats
    assert stats["rejected"] == 0

    turn = wired.app.next_turn("what changed?")
    assert turn["turn"] == 1
    for event_id in wired.event_ids:
        assert event_id in turn["prompt"]
    assert turn["prompt"].startswith("You are a memory-aware assistant")
    assert wired.app.store.recent(1)[0]["payload"]["content"].startswith("claim")


def test_the_pilot_app_has_no_poll_path():
    """Structural pin: a poll fallback would make indicator 1 unfalsifiable.

    Parsed, not grepped: the module DOCSTRING names the pull path (#2081's
    framing) and must be allowed to, while no executable reference may exist.
    """
    import ast

    tree = ast.parse((PILOT_DIR / "app.py").read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    names, literals = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value not in docstrings):
            literals.append(node.value)
    assert not names & {"read_after", "events_poll", "event_store",
                        "TortoiseSDK", "cursor"}, (
        f"the pilot app must learn about events ONLY by push; poll symbols: "
        f"{sorted(names & {'read_after', 'events_poll', 'event_store', 'TortoiseSDK', 'cursor'})}")
    assert not [s for s in literals if "/v1/events" in s], (
        "the pilot app must not call the pull endpoint")


# ── indicator 2: real accepted, tampered / stale refused ─────────────────

def test_tampered_body_is_refused_and_injects_nothing(wired):
    wd.dispatch_pending(wired.proj)
    accepted = _last_accepted(wired.state_dir)
    tampered = accepted["body"].replace('"content":', '"contentx":', 1)
    assert tampered != accepted["body"]

    status, payload = _post(wired.url, tampered, accepted["signature"])

    assert status == 401 and payload["error"] == "bad-signature", payload
    stats = wired.app.stats()
    assert stats["events"] == 2 and stats["rejected"] == 1, stats


def test_stale_capture_is_refused(wired):
    """A captured request is not replayable forever (the timestamp tolerance)."""
    wd.dispatch_pending(wired.proj)
    accepted = _last_accepted(wired.state_dir)
    stale_ts = int(time.time()) - (wd.SIGNATURE_TOLERANCE_S + 60)
    stale_signature = wd.signature_header(wired.sub["secret"], stale_ts,
                                          accepted["body"].encode("utf-8"))

    status, payload = _post(wired.url, accepted["body"], stale_signature)

    assert status == 401 and payload["error"] == "bad-signature", payload


def test_missing_signature_and_foreign_subscription_are_refused(wired):
    wd.dispatch_pending(wired.proj)
    accepted = _last_accepted(wired.state_dir)

    status, payload = _post(wired.url, accepted["body"], None)
    assert status == 401 and payload["error"] == "missing-signature", payload

    envelope = json.loads(accepted["body"])
    envelope["subscription_id"] = "sub_someone_else"
    foreign_body = json.dumps(envelope, sort_keys=True)
    signature = wd.signature_header(wired.sub["secret"], int(time.time()),
                                    foreign_body.encode("utf-8"))
    status, payload = _post(wired.url, foreign_body, signature)
    assert status == 403, payload
    assert wired.app.stats()["events"] == 2


# ── indicator 3: forced re-delivery, 0 duplicated effects ────────────────

def test_forced_redelivery_injects_nothing_twice(wired):
    first = wd.dispatch_pending(wired.proj)
    assert first["delivered"] == 2, first
    # The at-least-once case, forced: the cursor goes back to 0 as a crash
    # between the POST and the cursor write would leave it.
    wired.proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.cursor=0",
        params={"id": wired.sub["sub_id"]})
    replay = wd.dispatch_pending(wired.proj)

    assert replay["delivered"] == 2, replay
    stats = wired.app.stats()
    assert stats["duplicates"] == 2 and stats["events"] == 2, stats
    block = wired.app.memory_block()
    assert block.count("seq ") == 2, block
    # Both attempts are receipted: the sender's record and the consumer's agree.
    receipts = wd.read_receipts(wired.proj, wired.sub["sub_id"], limit=20)
    assert [r["status"] for r in receipts] == ["delivered"] * 4, receipts


def test_dedupe_survives_an_app_restart(wired):
    wd.dispatch_pending(wired.proj)
    accepted = _last_accepted(wired.state_dir)

    restarted = PILOT.PilotApp(secret=wired.sub["secret"],
                               state_dir=wired.state_dir,
                               expected_subscription_id=wired.sub["sub_id"])
    try:
        status, payload = _post(wired.url, accepted["body"],
                                accepted["signature"])
        assert status == 200 and payload["duplicate"] is True, payload
        assert restarted.stats()["events"] == 2, restarted.stats()
    finally:
        restarted.close()


# ── app-side behaviours ──────────────────────────────────────────────────

def test_memory_block_is_bounded(wired, tmp_path):
    bounded = PILOT.PilotApp(secret=wired.sub["secret"],
                             state_dir=tmp_path / "bounded",
                             max_block_events=1)
    try:
        wd.dispatch_pending(wired.proj)  # populate the shared subscription
        # Feed the SAME two events into the bounded instance: the other app
        # already consumed them, so replay them from its audit trail.
        for record in wired.state_dir.joinpath("received.jsonl").read_text(
                encoding="utf-8").splitlines():
            entry = json.loads(record)
            if entry.get("status") != 200:
                continue
            status, _ = bounded.handle_delivery(
                {PILOT.SIGNATURE_HEADER: entry["signature"]},
                entry["body"].encode("utf-8"))
            assert status == 200
        assert bounded.store.count() == 2
        block = bounded.memory_block()
        assert block.count("seq ") == 1, block  # only the newest event
        assert "claim 1" in block
    finally:
        bounded.close()


def test_http_surface_refuses_unknown_paths_and_serves_stats(wired):
    assert httpx.get(wired.url.replace(PILOT.WEBHOOK_PATH, "/healthz"),
                     timeout=5).json()["ok"] is True
    assert httpx.get(wired.url.replace(PILOT.WEBHOOK_PATH, "/stats"),
                     timeout=5).json()["events"] == 0
    assert httpx.post(wired.url + "/nope", content=b"{}", timeout=5).status_code == 404
