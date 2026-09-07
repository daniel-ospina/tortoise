"""HTTP tests for POST /internal/onboarding-email (#2406).

Supabase-mode journey via TestClient + in-memory FakeControlPlane (zero
network); the Resend send is monkeypatched. Covers the dedupe matrix
(marker replay, in-flight gate, real concurrency), the fail-soft contract
(provider/control-plane/sender failures never 5xx the edge fn and never
stamp the marker), personalization passthrough, and the skip matrix
(registry mode, unknown team, null email).

See docs/scoping/2026-09-06-2406-onboarding-call-email.md (Chosen approach B).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from fastapi.testclient import TestClient  # noqa: I001

import tortoise.email_notify as email_notify
import tortoise.supabase_control as sc
from tortoise import hosted_api
from tortoise.hosted_api import app

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane

_INTERNAL_KEY = "test-internal-shared-secret-xyz"
_INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}

TEAM_ID = "team-free-001"
EMAIL = "daniel@premiselabs.co"


def _seed_team(fake: FakeControlPlane, *, team_id: str = TEAM_ID,
               email: str | None = EMAIL, name: str = "Acme",
               marker: str | None = None) -> None:
    row: dict = {"id": team_id, "name": name, "tier": "free",
                 "graph_name": f"team_{team_id}"}
    if email is not None:
        row["email"] = email
    if marker is not None:
        row["onboarding_email_sent_at"] = marker
    fake.seed("teams", [row])


@pytest.fixture
def client_and_fake(monkeypatch) -> tuple[TestClient, FakeControlPlane]:
    """Supabase-mode TestClient over the real app with the fake control plane
    + the internal key configured (mirrors test_auth_flip.rest_client)."""
    fake = FakeControlPlane()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "onboarding-email.db")
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, fake


@pytest.fixture(autouse=True)
def _reset_inflight():
    hosted_api._inflight_onboarding_emails.clear()
    yield
    hosted_api._inflight_onboarding_emails.clear()


class TestSendOnboardingEmail:
    def test_sends_personalized_email_and_stamps_marker(self, client_and_fake,
                                                        monkeypatch):
        """Happy path: sender awaited with the team email + person display_name
        + org name; provider accept stamps the marker in the SAME request."""
        tc, fake = client_and_fake
        _seed_team(fake)
        sent = []

        async def fake_send(email, display_name, team_name, team_id):
            sent.append((email, display_name, team_name, team_id))
            return {"status": "sent", "message_id": "msg_1"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID, "display_name": "Daniel Ospina"},
                    headers=_INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "sent"
        assert body["message_id"] == "msg_1"
        assert sent == [(EMAIL, "Daniel Ospina", "Acme", TEAM_ID)]
        team = fake.tables["teams"][0]
        assert team["onboarding_email_sent_at"] is not None

    def test_personalization_defaults_when_display_name_absent(
            self, client_and_fake, monkeypatch):
        """display_name absent → None passed through (greeting derives from
        the email local-part server-side)."""
        tc, fake = client_and_fake
        _seed_team(fake, email="daniel.ospina@gmail.com")
        sent = []

        async def fake_send(email, display_name, team_name, team_id):
            sent.append((email, display_name, team_name, team_id))
            return {"status": "sent", "message_id": "m"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID},
                    headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "sent"
        assert sent == [("daniel.ospina@gmail.com", None, "Acme", TEAM_ID)]

    def test_replay_after_marker_no_second_send(self, client_and_fake,
                                                monkeypatch):
        """Sequential replay (re-provision / repeat POST): the marker read
        gate skips — sender invoked once for the whole process."""
        tc, fake = client_and_fake
        _seed_team(fake)
        calls = {"n": 0}

        async def fake_send(*a, **k):
            calls["n"] += 1
            return {"status": "sent", "message_id": "m"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        first = tc.post("/internal/onboarding-email",
                        json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert first.json()["status"] == "sent"
        second = tc.post("/internal/onboarding-email",
                         json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert second.status_code == 200
        assert second.json() == {"status": "already_sent"}
        assert calls["n"] == 1  # never double-sent

    def test_in_flight_gate_skips_concurrent_post(self, client_and_fake,
                                                  monkeypatch):
        """The in-flight gate closes the marker-read TOCTOU: a second POST
        while the first send is in flight skips without invoking the sender."""
        tc, fake = client_and_fake
        _seed_team(fake)
        hosted_api._inflight_onboarding_emails.add(TEAM_ID)
        calls = {"n": 0}

        async def fake_send(*a, **k):
            calls["n"] += 1
            return {"status": "sent", "message_id": "m"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json() == {"status": "in_flight"}
        assert calls["n"] == 0
        # Once the in-flight send finishes, a later POST still sends (the
        # in-flight entry is removed on completion — no permanent lock-out).
        hosted_api._inflight_onboarding_emails.discard(TEAM_ID)
        r2 = tc.post("/internal/onboarding-email",
                     json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r2.json()["status"] == "sent"
        assert calls["n"] == 1

    def test_concurrent_posts_send_exactly_once(self, client_and_fake,
                                                monkeypatch):
        """Real concurrency (two threads): the second POST observes either
        the in-flight gate or the stamped marker — the sender is invoked
        exactly once and at most one email is sent."""
        tc, fake = client_and_fake
        _seed_team(fake)
        calls = []
        entered = threading.Event()
        results: list[dict] = []

        async def fake_send(email, display_name, team_name, team_id):
            calls.append(team_id)
            entered.set()
            await asyncio.sleep(0.4)  # hold the first send in flight
            return {"status": "sent", "message_id": "m"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)

        def _post():
            r = tc.post("/internal/onboarding-email",
                        json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
            results.append(r.json())

        t = threading.Thread(target=_post)
        t.start()
        assert entered.wait(2.0), "first send never entered the sender"
        # Second POST races the in-flight first send.
        r2 = tc.post("/internal/onboarding-email",
                     json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        t.join(5.0)
        assert not t.is_alive()
        statuses = [r2.json()["status"], results[0]["status"]]
        assert "sent" in statuses
        assert len(calls) == 1, "sender must be invoked exactly once"
        # The racer saw the in-flight gate OR the marker — never a second send.
        for s in statuses:
            assert s in ("sent", "in_flight", "already_sent"), s

    def test_provider_failure_fail_soft_no_marker_then_retry_succeeds(
            self, client_and_fake, monkeypatch):
        """Provider failure → {status: failed} (never 5xx), marker NOT
        stamped; a subsequent POST (edge-fn retry) succeeds and stamps."""
        tc, fake = client_and_fake
        _seed_team(fake)
        outcomes = iter(["failed", "sent"])

        async def fake_send(*a, **k):
            nxt = next(outcomes)
            return {"status": nxt, "message_id": "m" if nxt == "sent" else None}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r1 = tc.post("/internal/onboarding-email",
                     json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "failed"
        assert fake.tables["teams"][0].get("onboarding_email_sent_at") is None
        r2 = tc.post("/internal/onboarding-email",
                     json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r2.json()["status"] == "sent"
        assert fake.tables["teams"][0]["onboarding_email_sent_at"] is not None

    def test_sender_exception_fail_soft_never_5xx(self, client_and_fake,
                                                  monkeypatch):
        """A raising sender (belt-and-braces — the sender never raises by
        contract) still resolves to a structured failed, never a 500."""
        tc, fake = client_and_fake
        _seed_team(fake)

        async def boom(*a, **k):
            raise RuntimeError("resend down")

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email", boom)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "failed"
        assert fake.tables["teams"][0].get("onboarding_email_sent_at") is None

    def test_control_plane_failure_fail_soft(self, client_and_fake,
                                             monkeypatch):
        """A control-plane outage resolves to failed (never a surprise 500 to
        the edge fn) and never stamps the marker."""
        tc, _ = client_and_fake
        monkeypatch.setattr(sc, "get_control_plane",
                            lambda: ErrorControlPlane())
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "failed"


class TestOnboardingEmailSkipMatrix:
    def test_unknown_team_skipped(self, client_and_fake, monkeypatch):
        tc, _ = client_and_fake
        calls = {"n": 0}

        async def fake_send(*a, **k):
            calls["n"] += 1
            return {"status": "sent"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": "no-such-team"}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "skipped"
        assert calls["n"] == 0

    def test_null_email_skipped(self, client_and_fake, monkeypatch):
        """teams.email NULL (Q5 / agent / legacy lanes — not first-org human
        signups) → skipped no-op, never emailed."""
        tc, fake = client_and_fake
        _seed_team(fake, email=None)
        calls = {"n": 0}

        async def fake_send(*a, **k):
            calls["n"] += 1
            return {"status": "sent"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "skipped"
        assert calls["n"] == 0

    def test_marker_already_set_skipped(self, client_and_fake, monkeypatch):
        tc, fake = client_and_fake
        _seed_team(fake, marker="2026-09-06T10:00:00+00:00")
        calls = {"n": 0}

        async def fake_send(*a, **k):
            calls["n"] += 1
            return {"status": "sent"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json() == {"status": "already_sent"}
        assert calls["n"] == 0

    def test_registry_mode_skipped(self, client_and_fake, monkeypatch):
        """Selfhost/registry mode (no hosted users) → skipped no-op."""
        tc, _ = client_and_fake
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
        calls = {"n": 0}

        async def fake_send(*a, **k):
            calls["n"] += 1
            return {"status": "sent"}

        monkeypatch.setattr(email_notify, "send_onboarding_offer_email",
                            fake_send)
        r = tc.post("/internal/onboarding-email",
                    json={"team_id": TEAM_ID}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "skipped"
        assert calls["n"] == 0

    def test_missing_team_id_400(self, client_and_fake):
        tc, _ = client_and_fake
        r = tc.post("/internal/onboarding-email",
                    json={}, headers=_INTERNAL_HEADERS)
        assert r.status_code == 400

    def test_requires_internal_auth(self, client_and_fake):
        tc, _ = client_and_fake
        assert tc.post("/internal/onboarding-email",
                       json={"team_id": TEAM_ID}).status_code == 401
        assert tc.post("/internal/onboarding-email",
                       json={"team_id": TEAM_ID},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
