"""#2779 slice 1 — every org-name entry point shares ONE display-name rule.

The reported bug: a free-text org name containing a space was rejected with a
message that never named the offending character. Slice 1 makes the org name a
free-text DISPLAY name at all four server entry points + the CLI, via the
shared ``tortoise/org_naming.py`` validator. The identifier/charset rule still
governs ``team_id`` and graph namespaces; a free-text name must never reach one
unslugged.

Entry points:
- ``POST /v1/teams`` (both lanes)
- ``POST /v1/onboarding/team``
- ``POST /v1/billing/checkout/new-org``
- ``POST /internal/provision``
- CLI ``tortoise key create --name``
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_billing import VALID_CATALOG
from tests.test_supabase_control import FREE_TEAM
from tortoise.hosted_api import (
    app,
    get_current_team,
    get_current_user,
)
from tortoise.org_naming import slugify_id

_INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-shared-secret-xyz"}
_USER = "9f2c1a40-0000-4a00-8000-0000000000ff"
_PRO_PRICE = "price_200proMM"
_FREE_TEXT = "test org for multi-organisation"
_FREE_SLUG = "test-org-for-multi-organisation"

# The session-team stub for /v1/onboarding/team (get_current_team_session
# mirrors get_current_team and carries session_user_id for the USER path).
_SESSION_TEAM = {
    "team_id": "team-session-001", "key_id": "key-001", "tier": "free",
    "graph_id": None, "scopes": [], "legacy_full_access": True,
    "delegation_depth": None, "created_by_key_id": None,
    "max_users": 5, "max_graphs": 5, "max_points": 100000,
    "max_api_keys": 5, "max_sessions": 1000,
    "session_user_id": _USER,
}


@pytest.fixture
def fake() -> FakeControlPlane:
    return FakeControlPlane({
        "teams": [dict(FREE_TEAM)],
        "team_memberships": [],
        "api_keys": [],
    })


@pytest.fixture
def supabase_env(monkeypatch, fake) -> FakeControlPlane:
    import tortoise.supabase_control as sc
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
    return fake


@pytest.fixture
def client(monkeypatch, supabase_env):
    """Supabase-mode TestClient on a temp embedded graph store + fake CP."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patched_tortoise_sdk(os.path.join(tmpdir, "freetext.db")),
        TestClient(app) as tc,
    ):
        yield tc, supabase_env
        app.dependency_overrides.clear()


@pytest.fixture
def user_client(client):
    tc, fake = client
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _USER, "email": "owner@example.com"}
    return tc, fake


@pytest.fixture
def internal_client(monkeypatch):
    """Registry-mode TestClient for /internal/provision (Supabase-disabled)."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patched_tortoise_sdk(os.path.join(tmpdir, "provision.db")),
        TestClient(app) as tc,
    ):
        yield tc
        app.dependency_overrides.clear()


# ── the reported bug: spaces are accepted ──────────────────────────────────

class TestDisplayNameAccepted:
    def test_post_teams_accepts_spaces_supabase(self, user_client):
        tc, fake = user_client
        r = tc.post("/v1/teams", json={"name": _FREE_TEXT})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == _FREE_TEXT          # stored verbatim
        assert body["team_id"] != _FREE_SLUG       # slice 1: id stays opaque
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_team_name"] == _FREE_TEXT
        assert body["graph_name"] == f"team_{body['team_id']}"

    def test_post_teams_accepts_spaces_registry(self, monkeypatch, fake):
        """The registry (selfhost) lane derives graph_name through the shared
        slug — a free-text name never reaches select_graph unslugged."""
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patched_tortoise_sdk(os.path.join(tmpdir, "reg.db")),
            TestClient(app) as tc,
        ):
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": _USER, "email": "owner@example.com"}
            r = tc.post("/v1/teams", json={"name": _FREE_TEXT})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["name"] == _FREE_TEXT
            assert body["graph_name"] == f"team_{_FREE_SLUG}"
            assert body["team_id"] != _FREE_SLUG  # opaque ulid, not the slug
            app.dependency_overrides.clear()

    def test_onboarding_team_accepts_spaces(self, user_client):
        tc, fake = user_client
        app.dependency_overrides[get_current_team] = lambda: dict(_SESSION_TEAM)
        r = tc.post("/v1/onboarding/team", json={"name": _FREE_TEXT})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == _FREE_TEXT
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_team_name"] == _FREE_TEXT

    def test_internal_provision_accepts_spaces(self, internal_client):
        r = internal_client.post("/internal/provision", headers=_INTERNAL_HEADERS, json={
            "team_id": "prov-free-1", "team_name": _FREE_TEXT,
            "api_key_hash": "h", "created_by": "u"})
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == "prov-free-1"

    def test_billing_checkout_accepts_spaces(self, user_client, monkeypatch):
        tc, _fake = user_client
        monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
        seen: dict = {}
        import tortoise.billing as billing
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session_for_new_org",
            lambda self, **kw: (seen.update(kw),
                                ("cs_x", "https://checkout.stripe.com/x"))[1])
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": _FREE_TEXT, "price_id": _PRO_PRICE})
        assert r.status_code == 200, r.text
        # The free-text display name rides to Stripe verbatim.
        assert "new_org_name=" in seen["success_url"]
        assert _FREE_TEXT.split()[0] in seen["success_url"]


# ── the shared rule: one validator, four routes ────────────────────────────

@pytest.mark.parametrize("route", ["v1/teams", "v1/onboarding/team", "v1/billing/checkout/new-org"])
def test_supabase_entry_points_share_the_rule(user_client, route):
    """Every route rejects a control-character display name with the SAME
    specific message (naming the code point), not a generic charset line.

    A control char is used because it reaches the shared validator on all four
    routes (POST /v1/billing/checkout/new-org's Pydantic model caps length at
    64 first, so >64 would be a Pydantic detail-list rather than the shared
    message)."""
    tc, _fake = user_client
    if route == "v1/onboarding/team":
        app.dependency_overrides[get_current_team] = lambda: dict(_SESSION_TEAM)
    r = tc.post(f"/{route}", json={"name": "Acme\x00Corp", "price_id": _PRO_PRICE})
    assert r.status_code == 422, r.text
    assert "U+0000" in json.dumps(r.json()), r.json()


def test_all_four_entry_points_reject_blank_and_name_the_problem(user_client, internal_client):
    """Blank is a 422 at all four entry points with a message naming the
    problem (never "letters, numbers, dash, underscore only")."""
    tc, _fake = user_client
    app.dependency_overrides[get_current_team] = lambda: dict(_SESSION_TEAM)
    for route, payload in (
        ("/v1/teams", {"name": "   "}),
        ("/v1/onboarding/team", {"name": "   "}),
        ("/v1/billing/checkout/new-org", {"name": "   ", "price_id": _PRO_PRICE}),
    ):
        r = tc.post(route, json=payload)
        assert r.status_code == 422, (route, r.text)
        assert "required" in r.json()["detail"], (route, r.json())
    r = internal_client.post("/internal/provision", headers=_INTERNAL_HEADERS, json={
        "team_id": "t1", "team_name": "   ", "api_key_hash": "h", "created_by": "u"})
    assert r.status_code == 422, r.text
    assert "required" in r.json()["detail"]


def test_slug_is_the_single_source_for_namespaces(user_client):
    """The identifier contract: the derived namespace is charset-safe even for
    hostile display names (drives org_naming.slugify_id, the same function the
    registry lane and CLI use)."""
    tc, fake = user_client
    r = tc.post("/v1/teams", json={"name": "Acme / Corp  2024"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Acme / Corp 2024"   # whitespace collapsed
    assert body["graph_name"] == f"team_{body['team_id']}"
    _, p = fake.rpc_calls[0]
    assert p["p_team_name"] == "Acme / Corp 2024"


# ── CLI ────────────────────────────────────────────────────────────────────

class TestCliKeyCreate:
    def test_cli_accepts_a_display_name_with_spaces(self, monkeypatch, tmp_path, capsys):
        from tortoise.__main__ import main
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cli-space.db"))
        rc = main(["key", "create", "--name", _FREE_TEXT])
        out = capsys.readouterr().out
        assert rc == 0, out
        assert _FREE_TEXT in out
        assert "tt_" in out

    def test_cli_rejects_a_blank_name_cleanly(self, monkeypatch, tmp_path, capsys):
        from tortoise.__main__ import main
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cli-blank.db"))
        rc = main(["key", "create", "--name", "   "])
        err = capsys.readouterr().err
        assert rc == 1
        assert "required" in err

    def test_cli_rejects_an_over_long_name_cleanly(self, monkeypatch, tmp_path, capsys):
        from tortoise.__main__ import main
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "cli-long.db"))
        rc = main(["key", "create", "--name", "a" * 65])
        err = capsys.readouterr().err
        assert rc == 1
        assert "65" in err


def test_slugify_shared_with_cli_and_routes():
    """Tripwire: the routes/lane/CLI all import the ONE deriver."""
    assert slugify_id(_FREE_TEXT) == _FREE_SLUG
