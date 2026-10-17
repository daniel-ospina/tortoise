"""#4685: completing a connector OAuth leaves EXACTLY ONE connector row.

The defect these pin: ``POST /v1/connectors/github/auth`` promised a connector,
but the GitHub callback it shares with onboarding consumed only
``created_at``/``org``/``org_id`` from its CSRF state and stored the token on
the ORG — so a completed OAuth left ``GET /v1/connectors`` EMPTY. The wiring
now consumes the flow's connector intent from that state, and the migration's
``idx_connectors_org_source`` (UNIQUE on ``(org_id, source_type)``) settles the
semantics the issue left open: a connector is per ORG+SOURCE — not per sync and
not per OAuth attempt — so a repeat OAuth ROTATES the existing row in place.

REDs on: dropping ``connector_source_type`` from ``connector_auth``'s state
(``GET /v1/connectors`` returns ``[]`` after a completed flow — the pre-#4685
behaviour), or making ``_upsert_oauth_connector`` call ``connector_create``
unconditionally on the repeat (the fake's unique parity raises 409 → 500, and
the row's ``config`` is lost).

The upstream is faked (token exchange + the ``GET /user`` login); everything
else — the CSRF state, the callback, the seam, the projection — is real.
"""
from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tortoise import supabase_control as sc

ORG = "org-oauth-connector"


@pytest.fixture
def flow(tmp_path, monkeypatch):
    """TestClient + an EMPTY connector store, with the GitHub upstream faked.

    No live Supabase and no live GitHub: the handlers resolve
    ``is_supabase_enabled`` / ``get_control_plane`` lazily from
    ``tortoise.supabase_control`` at call time, so patching the SOURCE module
    is enough. ``token`` is mutable so one test can rotate the credential
    between two completions.
    """
    cp = FakeControlPlane(tables={"connectors": []})
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)

    import tortoise.hosted_api as ha
    from tortoise.indexer.github_indexer import GitHubIndexer

    token = {"value": "gho-first"}

    async def _fake_exchange(code):
        return token["value"]

    monkeypatch.setattr(ha, "_exchange_github_token", _fake_exchange)

    async def fake_login(self):
        return "acme-user"

    monkeypatch.setattr(GitHubIndexer, "current_login", fake_login)
    # The onboarding-state write and the first-index spawn are OTHER writes
    # with their own tests (test_github_connect.py); this file is about the
    # connector row the completion owes. A NON-new job also means no indexing
    # task is spawned.
    monkeypatch.setattr(ha, "_update_onboarding_state", lambda *a, **k: None)
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda tid, kind="github": ("job-1", False))

    from tortoise.hosted_api import app, get_current_org_session_ungated
    app.dependency_overrides[get_current_org_session_ungated] = \
        lambda: {"org_id": ORG}
    with patched_tortoise_sdk(str(tmp_path / "connector-oauth.db")):
        try:
            with TestClient(app) as tc:
                yield tc, cp, token
        finally:
            app.dependency_overrides.clear()


def _complete_connector_oauth(tc) -> None:
    """Start ``/v1/connectors/github/auth`` and complete its callback."""
    started = tc.post("/v1/connectors/github/auth")
    assert started.status_code == 200, started.text
    state = started.json()["state"]
    done = tc.get(f"/v1/onboarding/github/callback?code=code&state={state}",
                  follow_redirects=False)
    assert done.status_code == 302, done.text
    assert "github=connected" in done.headers["location"], done.headers


def _credential(row: dict) -> dict:
    """Decrypt one stored connector credential into its JSON object."""
    from tortoise.crypto import decrypt_token
    return json.loads(decrypt_token(row["credential_enc"]))


def test_completed_oauth_creates_exactly_one_visible_connector(flow) -> None:
    """The flow's whole promise: auth URL in, ONE connector row out.

    REDs on: the callback ignoring ``connector_source_type`` — the list below
    is then ``[]``, which is exactly the pre-#4685 state this pins.
    """
    tc, cp, token = flow
    assert tc.get("/v1/connectors").json()["connectors"] == []  # precondition

    token["value"] = "gho-first"
    _complete_connector_oauth(tc)

    listed = tc.get("/v1/connectors").json()["connectors"]
    assert len(listed) == 1, listed
    assert listed[0]["org_id"] == ORG
    assert listed[0]["source_type"] == "github"
    # The API projection is not the store: the ciphertext never reaches a caller.
    assert "credential_enc" not in listed[0]

    stored = cp.tables["connectors"]
    assert len(stored) == 1, stored
    uuid.UUID(stored[0]["id"])  # the migration's DEFAULT gen_random_uuid()
    assert stored[0]["org_id"] == ORG and stored[0]["source_type"] == "github"
    assert _credential(stored[0]) == {
        "access_token": "gho-first", "token_type": "bearer"}


def test_repeat_oauth_rotates_in_place_and_keeps_the_scope_config(flow) -> None:
    """A second OAuth for the same org+source rotates; it never adds a row.

    ``idx_connectors_org_source`` is UNIQUE on ``(org_id, source_type)``, so a
    per-attempt connector is not merely wrong, it is unrepresentable — and the
    user's repo scope (``config``), set between the two completions the way the
    connector UI sets it, must survive the credential rotation.

    REDs on: ``connector_create`` on the repeat (409 → 500 and no rotation) or
    recreating the row (the scope is lost).
    """
    tc, cp, token = flow
    token["value"] = "gho-first"
    _complete_connector_oauth(tc)
    connector_id = cp.tables["connectors"][0]["id"]
    patched = tc.patch(f"/v1/connectors/{connector_id}",
                       json={"config": {"repos": ["acme/alpha"]}})
    assert patched.status_code == 200, patched.text

    token["value"] = "gho-rotated"
    _complete_connector_oauth(tc)

    stored = cp.tables["connectors"]
    assert len(stored) == 1, "a repeat OAuth must not add a second connector"
    assert stored[0]["id"] == connector_id, "the SAME row must be rotated"
    assert stored[0]["config"] == {"repos": ["acme/alpha"]}, \
        "the user's scope config must survive a credential rotation"
    assert _credential(stored[0])["access_token"] == "gho-rotated"


def test_a_lost_insert_race_converges_on_the_existing_row(flow, monkeypatch) -> None:
    """Two completions racing the INSERT still leave ONE rotated row.

    ``idx_connectors_org_source`` makes the loser's INSERT a 23505 → PostgREST
    409. The winner's row already keeps this flow's promise, so the loser must
    converge on it — a 500 on a second, legitimate completion would be a false
    failure. The first ``connector_by_org_source`` read is blinded to model the
    losing completion's pre-insert view; the second read is the real one.

    REDs on: letting the 409 escape (the callback answers 500), or dropping the
    converge arm's rotate (still one row, but the loser's credential never
    lands).
    """
    tc, cp, token = flow
    token["value"] = "gho-first"
    _complete_connector_oauth(tc)
    connector_id = cp.tables["connectors"][0]["id"]

    real_by_source = sc.connector_by_org_source
    reads = {"n": 0}

    def _blind_first_read(cp_, org_id, source_type):
        reads["n"] += 1
        if reads["n"] == 1:
            return None
        return real_by_source(cp_, org_id, source_type)

    monkeypatch.setattr(sc, "connector_by_org_source", _blind_first_read)

    token["value"] = "gho-rotated"
    _complete_connector_oauth(tc)

    stored = cp.tables["connectors"]
    assert len(stored) == 1, stored
    assert stored[0]["id"] == connector_id
    assert _credential(stored[0])["access_token"] == "gho-rotated"
    assert reads["n"] >= 2, "the 409 arm must re-read the winner's row"


def test_onboarding_connect_leaves_the_connector_store_untouched(flow) -> None:
    """The onboarding entry point is DELIBERATELY not wired (#4685 decision).

    ``POST /v1/onboarding/github/connect`` keeps its own contract — token on the
    org + first index — and the connector row is the CONNECTOR flow's promise.
    Both entry points share one callback, so this pins the boundary: a later
    "unify them" change has to be a visible decision, not a silent side effect
    of the code path they share.
    """
    tc, cp, _token = flow
    started = tc.post("/v1/onboarding/github/connect", json={})
    assert started.status_code == 200, started.text
    state = started.json()["state"]
    done = tc.get(f"/v1/onboarding/github/callback?code=code&state={state}",
                  follow_redirects=False)
    assert done.status_code == 302, done.text
    assert done.headers["location"].endswith("?github=connected"), done.headers
    assert cp.tables["connectors"] == []
    assert tc.get("/v1/connectors").json()["connectors"] == []


def test_selfhost_completion_writes_no_connector_row(flow, monkeypatch) -> None:
    """No connector store ⇒ no connector write, and no failure either.

    Self-host answers 501 on every ``/v1/connectors`` sibling (there is no
    connector store there), so the completion must skip the row rather than
    invent one nothing can read back. The flow still completes.
    """
    tc, cp, _token = flow
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)

    started = tc.post("/v1/connectors/github/auth")
    assert started.status_code == 200, started.text
    state = started.json()["state"]
    done = tc.get(f"/v1/onboarding/github/callback?code=code&state={state}",
                  follow_redirects=False)
    assert done.status_code == 302, done.text
    assert cp.tables["connectors"] == []
