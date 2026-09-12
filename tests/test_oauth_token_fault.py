"""Fault-injection suite for OAuth grant atomicity (#2863).

Every ``/oauth/token`` write window is made reachable in-process (including
commit-then-lost-response, and failures of a *specific* call site — which a
raise-on-N injector cannot express) so that a compensation failure is loud in
tests instead of an untyped 500 in production.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import tortoise.oauth as oauth
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import _FAULT_CPS, FakeControlPlane  # noqa: RUF100
from tests.test_oauth_mcp import (  # noqa: RUF100
    _U1,
    _enable_supabase,
    _pkce,
)
from tortoise.hosted_api import app
from tortoise.oauth import (  # noqa: RUF100
    REFRESH_TOKEN_TTL_S,
    _expires_iso,
    _sha256,
)
from tortoise.supabase_control import SupabaseControlPlane

FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)

_CLIENT_ID = "client-1"                      # must equal the seeded oauth_clients.id
_REDIRECT = "https://app.example/cb"


# ── Task 1: the fault-injection hook itself ─────────────────────────────────

def test_fault_hook_raises_before_and_after_mutation():
    from tests.fake_control_plane import FakeControlPlane
    cp = FakeControlPlane()
    cp.query("oauth_codes", method="POST", json_body={"code_hash": "h", "used_at": None})

    cp.fail_query(table="oauth_codes", method="GET", times=1, exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])
    assert cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])   # times consumed

    cp.fail_query(table="oauth_codes", method="PATCH", after_mutation=True,
                  exc=RuntimeError("lost response"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at"],
                 filters=[("code_hash", "eq", "h")], json_body={"used_at": "T"})
    assert cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])[0]["used_at"] == "T"


def test_fault_hook_discriminates_call_sites_by_select_shape():
    """The observation SELECT and the consume PATCH share table+method; only `select`
    distinguishes them — this is what makes the CAS/observation tests possible."""
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                  times=1, exc=RuntimeError("observation only"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                 filters=[], json_body={"used_at": None})
    assert cp.query("oauth_codes", method="PATCH", select=["id"], filters=[],
                    json_body={"revoked_at": "T"}) == []      # different select → no fault


# ── Task 1b: the shared fixture layer ───────────────────────────────────────

@pytest.fixture
def fault_client(monkeypatch):
    """(TestClient with raise_server_exceptions=False, cp, seeded). Mirrors `api_client`
    (test_oauth_mcp.py:167) but must NOT reuse it: that fixture yields TestClient(app),
    where raise_server_exceptions defaults True, so an injected fault would propagate
    instead of becoming a response."""
    cp = FakeControlPlane()
    _enable_supabase(monkeypatch, cp)
    _seed_base_tables(cp)
    with tempfile.TemporaryDirectory() as tmpdir, \
         patched_tortoise_sdk(os.path.join(tmpdir, "oauth.db")), \
         TestClient(app, raise_server_exceptions=False) as tc:
        yield tc, cp


@pytest.fixture(autouse=True)
def _no_silent_faults():
    """THE recurrence guard for this whole suite. A fault matcher that does not match
    the real call shape is a test that pins nothing — and it passes. `fail_query`
    registers every control plane it is called on, so bare-`FakeControlPlane` tests are
    covered too (not just `fault_client` ones). Any injector left unfired (or under-fired
    vs its `times`) fails the test loudly."""
    _FAULT_CPS.clear()
    yield
    stale = [f for cp in _FAULT_CPS for f in cp.unfired_faults()]
    if stale:
        pytest.fail(f"fault injectors never fired (stale match): {stale}")


def _seed_base_tables(cp) -> None:
    """oauth_clients (id=_CLIENT_ID, token_endpoint_auth_method='none'), teams
    (id='t1'), team_memberships (user_id=_U1 UUID, team_id='t1', status='active')."""
    cp.tables.setdefault("oauth_clients", []).append({
        "id": _CLIENT_ID, "client_name": "test", "redirect_uris": [_REDIRECT],
        "scope": "mcp", "token_endpoint_auth_method": "none",
        "created_at": "2026-01-01T00:00:00+00:00"})
    cp.tables.setdefault("teams", []).append({
        "id": "t1", "tier": "Team", "suspended_at": None, "flagged_at": None,
        "email": "t@example.com"})
    cp.tables.setdefault("team_memberships", []).append({
        "user_id": _U1, "team_id": "t1", "role": "owner", "status": "active"})


def _live(cp, table: str) -> list[dict]:
    return [r for r in cp.tables.get(table, []) if r.get("revoked_at") is None]


def _s256(verifier: str) -> str:
    """base64url(sha256(verifier)), unpadded — the same transform `_verify_pkce` applies.
    Compare within ONE `_pkce()` pair (it generates a fresh pair per call, so comparing
    two invocations can never hold): `v, c = _pkce(); assert _s256(v) == c`."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                    ).rstrip(b"=").decode()


def _seed_code(cp, code="code-1", *, verifier=None, client_id=_CLIENT_ID,
               user_id=_U1, team_id="t1", redirect_uri=_REDIRECT, used_at=None,
               expires_in=600) -> str:
    """Insert an oauth_codes row and RETURN the PKCE verifier (a code seeded without
    its verifier 400s on PKCE before ever reaching the injected fault). Uses the
    production encoders so the hash and timestamp formats match."""
    verifier = verifier or _pkce()[0]
    cp.tables.setdefault("oauth_codes", []).append({
        "code_hash": _sha256(code), "client_id": client_id, "user_id": user_id,
        "team_id": team_id, "redirect_uri": redirect_uri,
        "code_challenge": _s256(verifier), "code_challenge_method": "S256",
        "scope": "mcp", "resource": None,
        "expires_at": _expires_iso(expires_in), "used_at": used_at,
        "created_at": _expires_iso(0)})
    return verifier


def _seed_refresh_token(cp, token="rt-1", **over) -> tuple[str, str]:
    """Insert an oauth_refresh_tokens row. RETURNS (row_id, plaintext_token) — ONE
    VALUE CANNOT BOTH: `refresh_grant` looks the row up by `token_hash` but lane 3
    and the claim PATCH address it by `id`."""
    token = over.pop("token", token)
    row = {"id": over.pop("id", secrets.token_urlsafe(16)), "token_hash": _sha256(token),
           "client_id": _CLIENT_ID, "user_id": _U1, "team_id": "t1", "scope": "mcp",
           "expires_at": _expires_iso(REFRESH_TOKEN_TTL_S), "revoked_at": None,
           "rotated_from": None, "created_at": _expires_iso(0), **over}
    cp.tables.setdefault("oauth_refresh_tokens", []).append(row)
    return row["id"], token


def _seed_access_token(cp, *, refresh_id: str) -> str:
    """Insert an oauth_access_tokens row with `refresh_token_id=refresh_id` and return
    its `id` (this is what `refresh_grant`'s prev_access SELECT matches)."""
    row_id = secrets.token_urlsafe(16)
    cp.tables.setdefault("oauth_access_tokens", []).append({
        "id": row_id, "token_hash": _sha256("at-" + row_id), "client_id": _CLIENT_ID,
        "user_id": _U1, "team_id": "t1", "scope": "mcp",
        "expires_at": _expires_iso(3600), "revoked_at": None,
        "refresh_token_id": refresh_id, "created_at": _expires_iso(0)})
    return row_id


def _post_code(tc, cp, code, verifier, **over):
    body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT, **over}
    return tc.post("/oauth/token", data=body)


def _post_refresh(tc, cp, token: str, **over):
    return tc.post("/oauth/token", data={"grant_type": "refresh_token",
                                        "refresh_token": token,
                                        "client_id": _CLIENT_ID, **over})


def test_fixture_layer_no_fault_probe(fault_client):
    """The fixtures must not themselves 400/401 — an unseeded base table would make
    every later assertion vacuous."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "probe-code")
    r = _post_code(tc, cp, "probe-code", verifier)
    assert r.status_code == 200, r.text
    _, rt = _seed_refresh_token(cp, "probe-rt")
    r2 = _post_refresh(tc, cp, rt)
    assert r2.status_code == 200, r2.text


# ── Task 2: read-only observation / CAS primitives + the dialect contract ───

@pytest.mark.parametrize("used_at,expires_in,expected", [
    (None, 600, "unconsumed"),
    ("2026-01-01T00:00:00+00:00", 600, "consumed"),
    (None, -10, "consumed"),
])
def test_consume_state_outcomes(used_at, expires_in, expected):
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at=used_at, expires_in=expires_in)
    assert oauth._consume_state(cp, "c") == expected


def test_consume_state_missing_row_is_consumed():
    assert oauth._consume_state(FakeControlPlane(), "nope") == "consumed"


def test_consume_state_unknown_on_raise_and_never_writes():
    cp = FakeControlPlane()
    _seed_code(cp, "c")
    before = copy.deepcopy(cp.tables)
    cp.fail_query(table="oauth_codes", method="GET", times=1, exc=RuntimeError("down"))
    assert oauth._consume_state(cp, "c") == "unknown"
    assert cp.tables == before              # read-only: no clobber of a concurrent claim


def test_restore_code_cas_match_miss_expiry_and_raise():
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at="T1")
    assert oauth._restore_code(cp, "c", "T1") is True
    assert cp.tables["oauth_codes"][0]["used_at"] is None
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at="T1")
    assert oauth._restore_code(cp, "c", "T2") is False                       # CAS miss
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at="T1", expires_in=-10)
    assert oauth._restore_code(cp, "c", "T1") is False                       # over TTL
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at="T1")
    cp.fail_query(table="oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                  times=1, exc=RuntimeError("down"))
    assert oauth._restore_code(cp, "c", "T1") is False                       # raise → terminal


def test_rollback_minted_zero_rows_is_success_not_failure():
    """A never-inserted row must NOT be reported as a failed rollback."""
    cp = FakeControlPlane()
    oauth._rollback_minted(cp, [("oauth_refresh_tokens", "never-existed")], "now",
                           capture=False)                      # must not raise
    assert oauth._mint_observably_clean(cp, [("oauth_refresh_tokens", "never-existed")]) is True


def test_expiry_forms_agree_and_the_iso_encoders_are_offset_form(monkeypatch):
    """Pin the FORMAT invariant (the fake's `gt` is a lexical compare), not an
    absolute timestamp: a hardcoded literal cannot separate the two suffix forms
    because the date/time prefix decides the comparison first. Freeze the clock and
    build a definitely-future instant in both forms."""
    monkeypatch.setattr(oauth, "_now", lambda: FIXED_NOW)
    assert oauth._now_iso().endswith("+00:00") and oauth._expires_iso(60).endswith("+00:00")
    future = FIXED_NOW + timedelta(seconds=600)
    forms = [future.isoformat(), future.strftime("%Y-%m-%dT%H:%M:%S") + "Z"]
    results = []
    for rendered in forms:
        cp = FakeControlPlane()
        _seed_code(cp, "c", used_at="T1")
        cp.tables["oauth_codes"][0]["expires_at"] = rendered
        results.append((oauth._restore_code(cp, "c", "T1"), oauth._consume_state(cp, "c")))
    assert results[0][0] is True and results[1][0] is True      # future in BOTH forms
    assert results[0][1] == results[1][1] == "unconsumed"       # lexical and semantic agree


@pytest.fixture
def capture_server():
    """Start a local HTTPServer, record (command, self.path, headers, body) per request,
    and serve a configurable (status, body) — defaults to a representation list.
    `self.path` is the RAW request target, so the query string is included."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.server.seen.append((self.command, self.path, dict(self.headers), body))
            code, payload = self.server.respond
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        do_GET = do_PATCH = do_POST = _serve

        def log_message(self, *a):      # keep the test output clean
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    httpd.seen, httpd.respond = [], (200, b"[]")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def test_real_seam_encodes_the_cas_dialect(capture_server):
    """Drive the REAL SupabaseControlPlane and assert the dialect the CAS relies on.
    No live PostgREST is needed."""
    cp = SupabaseControlPlane(url=f"http://127.0.0.1:{capture_server.server_port}",
                              service_key="svc")
    captured = "2026-01-01T00:00:00+00:00"
    capture_server.respond = (200, json.dumps(
        [{"used_at": None, "expires_at": "2099-01-01T00:00:00+00:00"}]).encode())
    assert oauth._restore_code(cp, "c", captured) is True        # representation non-empty → True
    cmd, path, headers, _ = capture_server.seen[-1]
    assert cmd == "PATCH"
    assert "code_hash=eq." in path and "used_at=eq." in path and "expires_at=gt." in path
    assert "used_at=eq.2026-01-01T00%3A00%3A00%2B00%3A00" in path   # ':'→%3A, '+'→%2B
    assert headers["Prefer"] == "return=representation"
    assert "Content-Profile" not in headers      # pins that no schema profile is switched

    capture_server.respond = (200, json.dumps([{"used_at": None,
                                                "expires_at": "2099-01-01T00:00:00+00:00"}]).encode())
    oauth._consume_code(cp, "c")
    assert "used_at=is.null" in capture_server.seen[-1][1]


@pytest.mark.parametrize("status,payload,expected", [
    (500, b'{"message":"boom"}', "unknown"),      # S1(a)
    (200, b"<html>not json</html>", "unknown"),   # S1(c) unparseable 2xx
])
def test_real_seam_maps_status_and_unparseable_body(capture_server, status, payload, expected):
    """The fake's injector raises a Python error, so it CANNOT exercise the seam's own
    HTTP branches. Assert the callers surface a typed outcome, never a raw exception."""
    cp = SupabaseControlPlane(url=f"http://127.0.0.1:{capture_server.server_port}",
                              service_key="svc")
    capture_server.respond = (status, payload)
    assert oauth._consume_state(cp, "c") == expected
    assert oauth._restore_code(cp, "c", "T1") is False


# ── Task 3: `_issue_tokens` — 3-lane taxonomy, structural no-leak guarantee ──

def _mint(cp, **over):
    return oauth._issue_tokens(cp, client_id="c1", user_id="u1", team_id="t1",
                               scope="mcp", resource=None, **over)


def test_refresh_insert_failure_rolls_back_and_observes():
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_refresh_tokens", method="POST")
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp)
    assert ei.value.recovered is True       # never inserted → nothing to revoke → clean
    assert _live(cp, "oauth_refresh_tokens") == [] and _live(cp, "oauth_access_tokens") == []


def test_access_insert_failure_removes_the_live_orphan():
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST")
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp)
    assert ei.value.recovered is True
    assert _live(cp, "oauth_refresh_tokens") == []      # TODAY this is the live orphan (matrix row 5)


@pytest.mark.parametrize("table", ["oauth_refresh_tokens", "oauth_access_tokens"])
def test_mint_write_that_committed_then_lost_its_response_is_rolled_back(table):
    cp = FakeControlPlane()
    cp.fail_query(table=table, method="POST", after_mutation=True, times=1)
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp)
    assert ei.value.recovered is True
    assert _live(cp, table) == []           # a live orphan here would be a #3036-class leak


def test_double_fault_reports_recovered_false():
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST")
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)   # the rollback
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp)
    assert ei.value.recovered is False


def test_observation_read_failure_is_terminal_never_fail_open():
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST")                 # trigger
    cp.fail_query(table="oauth_access_tokens", method="GET", select=["id"])   # observation
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp)
    assert ei.value.recovered is False


def test_prev_refresh_observed_revoked_is_recovered_false():
    cp = FakeControlPlane()
    _seed_refresh_token(cp)
    cp.fail_query(table="oauth_access_tokens", method="POST")       # trigger after the claim
    cp.tables["oauth_refresh_tokens"][0]["revoked_at"] = "T"        # claim landed
    with pytest.raises(oauth.OAuthMintAborted) as ei:
        _mint(cp, prev_refresh=cp.tables["oauth_refresh_tokens"][0])
    assert ei.value.recovered is False


def test_loser_rollback_failure_still_raises_invalid_grant(caplog, monkeypatch):
    """Pin lane 1's rollback-failure path. The fault MATCHES BY SHAPE (select-less
    PATCH), never by a hand-written filter literal — and the autouse `_no_silent_faults`
    guard fails this test if the injector never fires."""
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception", lambda exc, **kw: calls.append(exc))
    cp = FakeControlPlane()
    row_id, _ = _seed_refresh_token(cp, "old")
    cp.query("oauth_refresh_tokens", method="PATCH", select=["id"],
             filters=[("id", "eq", row_id)], json_body={"revoked_at": "T"})   # pre-claim → loser
    # Match the ROLLBACK by its select-less shape; a bare match would be taken by the
    # claim PATCH (select=["id"]) and land in lane 2 instead.
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    with caplog.at_level(logging.WARNING, logger="tortoise.oauth"), \
         pytest.raises(oauth.OAuthError) as ei:
        _mint(cp, prev_refresh={"id": row_id})
    assert ei.value.status == 400 and ei.value.error == "invalid_grant"   # NOT OAuthMintAborted
    assert len(calls) == 1 and "loser rollback" in caplog.text   # lane 1 captured exactly once


def test_prev_access_revoke_failure_does_not_fail_a_delivered_pair(caplog):
    cp = FakeControlPlane()
    rid, _ = _seed_refresh_token(cp, "acc-parent")
    acc = _seed_access_token(cp, refresh_id=rid)
    cp.fail_query(table="oauth_access_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)   # lane 3
    with caplog.at_level(logging.WARNING, logger="tortoise.oauth"):
        out = _mint(cp, prev_access_id=acc)
    assert out["access_token"] and out["refresh_token"]          # delivered
    assert "prev-access revoke failed" in caplog.text            # the fault DID fire


@pytest.mark.parametrize("second_fault", ["none", "one_rollback", "both_rollbacks"])
def test_one_capture_per_abort(monkeypatch, second_fault):
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda exc, **kw: calls.append(exc))
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)          # trigger
    if second_fault in ("one_rollback", "both_rollbacks"):
        cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                      match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    if second_fault == "both_rollbacks":
        cp.fail_query(table="oauth_access_tokens", method="PATCH",
                      match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    with pytest.raises(oauth.OAuthMintAborted):
        _mint(cp)
    assert len(calls) == 1          # counts, not presence — the panic case would be 3
