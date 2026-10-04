"""#3026 — the browser-facing and client-facing OAuth endpoints need a typed
transient-failure boundary.

`/oauth/token` got one in #2863. These five did not: each caught only
`OAuthError`, so a control-plane `RuntimeError` escaped to the global handler and
the consumer received a bare ``500 {"detail": "Internal server error"}`` — a body
neither the consent page's JS nor an OAuth client can act on.

This module reuses #2863's fault-injection harness deliberately:
`ErrorControlPlane` (every `query`/`rpc` raises, matching the fail-closed
`RuntimeError` contract in `supabase_control.py`) and the `_no_silent_faults`
autouse guard, so a matcher that stops matching cannot leave a green test that
pins nothing.
"""
from __future__ import annotations

import logging

import pytest

from tests.fake_control_plane import ErrorControlPlane
from tests.test_oauth_mcp import _U1
from tests.test_oauth_token_fault import (  # noqa: RUF100
    _CLIENT_ID,
    _REDIRECT,
    _no_silent_faults,  # noqa: F401  (autouse stale-injector guard, this module too)
)
from tests.test_oauth_token_fault import (
    fault_client as fault_client,  # noqa: RUF100  (the fixture, re-exported for this module)
)

# The five endpoints #3026 names, with the request each needs to reach its
# control-plane call. `check` is what the consumer relies on, so it is asserted
# for every one of them rather than for a representative.
AUTHORIZE_PARAMS = {
    "client_id": _CLIENT_ID,
    "redirect_uri": _REDIRECT,
    "response_type": "code",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "code_challenge_method": "S256",
}

CONSENT_BODY = {
    "client_id": _CLIENT_ID,
    "redirect_uri": _REDIRECT,
    "response_type": "code",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "code_challenge_method": "S256",
}


def _fail_over_control_plane(monkeypatch) -> None:
    """Every control-plane call raises, exactly as an outage does."""
    monkeypatch.setattr(
        "tortoise.hosted_api._oauth_control_plane",
        lambda: (ErrorControlPlane(), True))


def _stub_session(monkeypatch) -> None:
    """`/oauth/consent/preview` verifies the session JWT BEFORE it touches the
    control plane, so the boundary is only reachable with a resolved user. The
    same stub, and the same reason, as `test_oauth_mcp.py::session_user`."""

    async def _fake_session(request):
        return {"user_id": _U1, "email": "u@example.com"}

    monkeypatch.setattr("tortoise.hosted_api.verify_session_jwt", _fake_session)


def _assert_typed_transient(response) -> None:
    """The consumer contract, asserted in ONE place so every endpoint is held to
    the same one: a 503 whose body is the RFC 6749 §5.2 error shape.

    `detail` must be ABSENT: that is FastAPI's shape, and the consent page reads
    `payload.error_description || payload.error` — a `detail` body renders as a
    generic failure with no cause and no remedy, which is the same
    unactionable-body defect #3026 reports, one status code better.
    """
    assert response.status_code == 503, response.text
    body = response.json()
    assert body.get("error") == "temporarily_unavailable", body
    assert body.get("error_description"), body
    assert "detail" not in body, (
        "the OAuth consumers parse the RFC 6749 §5.2 body, never FastAPI's "
        f"`detail` shape: {body!r}")


# ── One boundary per endpoint ───────────────────────────────────────────────

def test_authorize_control_plane_failure_is_a_typed_503(monkeypatch, fault_client):
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    _assert_typed_transient(tc.get("/oauth/authorize", params=AUTHORIZE_PARAMS))


def test_consent_preview_control_plane_failure_is_a_typed_503(monkeypatch, fault_client):
    _stub_session(monkeypatch)
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    _assert_typed_transient(tc.get("/oauth/consent/preview", params={"resource": ""}))


def test_consent_control_plane_failure_is_a_typed_503(monkeypatch, fault_client):
    _stub_session(monkeypatch)
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    _assert_typed_transient(tc.post("/oauth/consent", json=CONSENT_BODY))


def test_revoke_control_plane_failure_is_a_typed_503(monkeypatch, fault_client):
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    _assert_typed_transient(tc.post("/oauth/revoke", data={"token": "x"}))


def test_register_control_plane_failure_is_a_typed_503(monkeypatch, fault_client):
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    _assert_typed_transient(tc.post("/register", json={
        "client_name": "x", "redirect_uris": [_REDIRECT]}))


# ── The boundary is a last-resort net, NOT a retryable classification ───────

@pytest.mark.parametrize("label,patch,path,payload", [
    ("authorize", "tortoise.oauth.validate_authorize_params", "/oauth/authorize",
     {"params": AUTHORIZE_PARAMS}),
    ("consent-preview", "tortoise.oauth.consent_preview", "/oauth/consent/preview",
     {"params": {"resource": ""}}),
    ("revoke", "tortoise.oauth.revoke_token", "/oauth/revoke", {"data": {"token": "x"}}),
    ("register", "tortoise.oauth.register_client", "/register",
     {"json": {"client_name": "x", "redirect_uris": [_REDIRECT]}}),
])
def test_a_genuine_bug_keeps_the_honest_500(monkeypatch, fault_client,
                                            label, patch, path, payload):
    """A non-`RuntimeError` is a BUG, not an outage. Calling it
    `temporarily_unavailable` would put every client into a retry loop for a
    defect that no retry can fix — so it keeps `/oauth/token`'s honest 500
    `server_error`, which is the whole reason the boundary discriminates."""
    if label == "consent-preview":
        _stub_session(monkeypatch)

    def _boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(patch, _boom)
    tc, _ = fault_client
    response = getattr(tc, "get" if payload.get("params") else "post")(path, **payload)
    assert response.status_code == 500, response.text
    body = response.json()
    assert body.get("error") == "server_error", body
    assert "detail" not in body, body


def test_control_plane_acquisition_failure_is_bounded(monkeypatch, fault_client):
    """The FIRST call on every one of these endpoints is `_oauth_control_plane()`,
    and it raises `RuntimeError` when the control plane is unconfigured
    (`SupabaseControlPlane.__init__`, "not configured"). It sat OUTSIDE every
    `try`, so it was the one path the boundary could not reach — the bare 500
    #3026 names, still reachable after the first cut of this fix. Found in
    review precisely because the other tests monkeypatch `_oauth_control_plane`
    and so masked it.
    """
    def _unconfigured():
        raise RuntimeError("Supabase control plane not configured")

    monkeypatch.setattr("tortoise.hosted_api._oauth_control_plane", _unconfigured)
    tc, _ = fault_client
    _assert_typed_transient(tc.post("/oauth/revoke", data={"token": "x"}))
    _assert_typed_transient(tc.get("/oauth/authorize", params=AUTHORIZE_PARAMS))


def test_consent_mint_boundary_is_reachable_and_typed(monkeypatch, fault_client):
    """The params boundary fires FIRST, so `issue_auth_code`'s own boundary needs
    its own test — the 503 test above never reaches it (found in review). This
    drives a VALID authorize request through the real seeded control plane so
    `validate_authorize_params` SUCCEEDS, then fails the mint.
    """
    _stub_session(monkeypatch)

    def _boom(*args, **kwargs):
        raise RuntimeError("Supabase unreachable (simulated)")

    monkeypatch.setattr("tortoise.oauth.issue_auth_code", _boom)
    tc, _ = fault_client
    _assert_typed_transient(tc.post("/oauth/consent", json=CONSENT_BODY))


def test_the_boundary_logs_and_captures_the_unconverted_failure(monkeypatch, caplog,
                                                               fault_client):
    """Observability: a control-plane failure swallowed silently would hide an
    outage behind a 503 nobody is paged for — the failure #2240 is about. The
    boundary must leave one WARNING and one capture behind."""
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda exc, **kw: calls.append(exc))
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    with caplog.at_level(logging.WARNING, logger="tortoise.oauth"):
        response = tc.post("/oauth/revoke", data={"token": "x"})
    assert response.status_code == 503
    assert len(calls) == 1, f"expected exactly one capture, got {len(calls)}"
    assert "oauth/revoke" in caplog.text, caplog.text


def test_capture_raising_does_not_break_the_typed_response(monkeypatch, fault_client):
    """Sentry itself must never turn the typed 503 back into the bare 500 this
    task removes — the same guard #2863 pinned for `/oauth/token`."""
    def _sentry_down(*args, **kwargs):
        raise RuntimeError("sentry down")

    monkeypatch.setattr("tortoise.sentry.capture_exception", _sentry_down)
    _fail_over_control_plane(monkeypatch)
    tc, _ = fault_client
    _assert_typed_transient(tc.post("/oauth/revoke", data={"token": "x"}))
