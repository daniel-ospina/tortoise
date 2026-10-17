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
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import tortoise.oauth as oauth
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import _FAULT_CPS, ErrorControlPlane, FakeControlPlane  # noqa: RUF100
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
    distinguishes them — this is what makes the CAS/observation tests possible.
    `times=2`: the non-matching call must NOT consume the injector, and the matching
    shape must still be armed afterwards. (With `times=1` this test is vacuous — the
    injector is spent by the first matching call, so the 'different select' assertion
    passes even if `_take_fault` ignores `select` entirely.)"""
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                  times=2, exc=RuntimeError("observation only"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                 filters=[], json_body={"used_at": None})
    assert cp.query("oauth_codes", method="PATCH", select=["id"], filters=[],
                    json_body={"revoked_at": "T"}) == []      # different select → no fault
    with pytest.raises(RuntimeError):                          # still armed for the real shape
        cp.query("oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                 filters=[], json_body={"used_at": None})


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
    (id='t1'), org_memberships (user_id=_U1 UUID, org_id='t1', status='active')."""
    cp.tables.setdefault("oauth_clients", []).append({
        "id": _CLIENT_ID, "client_name": "test", "redirect_uris": [_REDIRECT],
        "scope": "mcp", "token_endpoint_auth_method": "none",
        "created_at": "2026-01-01T00:00:00+00:00"})
    cp.tables.setdefault("organizations", []).append({
        "id": "t1", "tier": "Team", "suspended_at": None, "flagged_at": None,
        "email": "t@example.com"})
    cp.tables.setdefault("org_memberships", []).append({
        "user_id": _U1, "org_id": "t1", "role": "owner", "status": "active"})


def _live(cp, table: str) -> list[dict]:
    return [r for r in cp.tables.get(table, []) if r.get("revoked_at") is None]


def _s256(verifier: str) -> str:
    """base64url(sha256(verifier)), unpadded — the same transform `_verify_pkce` applies.
    Compare within ONE `_pkce()` pair (it generates a fresh pair per call, so comparing
    two invocations can never hold): `v, c = _pkce(); assert _s256(v) == c`."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                    ).rstrip(b"=").decode()


def _seed_code(cp, code="code-1", *, verifier=None, client_id=_CLIENT_ID,
               user_id=_U1, org_id="t1", redirect_uri=_REDIRECT, used_at=None,
               expires_in=600, code_id=None, redemption_state=None) -> str:
    """Insert an oauth_codes row and RETURN the PKCE verifier (a code seeded without
    its verifier 400s on PKCE before ever reaching the injected fault). Uses the
    production encoders so the hash and timestamp formats match.

    #3027: the row carries `id` and an explicit `redemption_state`, because the
    durable claim filters on both (`redemption_state='unclaimed'`) and the
    `code_id` link addresses the code by id. A row seeded WITH `used_at` is
    `claimed` unless a state is named — the state the claim statement writes — so
    `_restore_code`'s fence and a reconciler's takeover are both exercisable.
    """
    verifier = verifier or _pkce()[0]
    if code_id is None:
        numeric = [r.get("id") for r in cp.tables.get("oauth_codes", [])
                   if isinstance(r.get("id"), int)]
        code_id = (max(numeric) + 1) if numeric else 1
    cp.tables.setdefault("oauth_codes", []).append({
        "id": code_id,
        "code_hash": _sha256(code), "client_id": client_id, "user_id": user_id,
        "org_id": org_id, "redirect_uri": redirect_uri,
        "code_challenge": _s256(verifier), "code_challenge_method": "S256",
        "scope": "mcp", "resource": None,
        "expires_at": _expires_iso(expires_in), "used_at": used_at,
        "redemption_state": redemption_state or (
            "claimed" if used_at is not None else "unclaimed"),
        "redemption_id": None, "redemption_settled_at": None,
        "redemption_note": None,
        "created_at": _expires_iso(0)})
    return verifier


def _seed_refresh_token(cp, token="rt-1", **over) -> tuple[str, str]:
    """Insert an oauth_refresh_tokens row. RETURNS (row_id, plaintext_token) — ONE
    VALUE CANNOT BOTH: `refresh_grant` looks the row up by `token_hash` but lane 3
    and the claim PATCH address it by `id`."""
    token = over.pop("token", token)
    row = {"id": over.pop("id", secrets.token_urlsafe(16)), "token_hash": _sha256(token),
           "client_id": _CLIENT_ID, "user_id": _U1, "org_id": "t1", "scope": "mcp",
           "expires_at": _expires_iso(REFRESH_TOKEN_TTL_S), "revoked_at": None,
           "rotated_from": None, "created_at": _expires_iso(0), **over}
    cp.tables.setdefault("oauth_refresh_tokens", []).append(row)
    return row["id"], token


def _seed_access_token(cp, *, refresh_id: str, code_id=None) -> str:
    """Insert an oauth_access_tokens row with `refresh_token_id=refresh_id` and return
    its `id` (this is what `refresh_grant`'s prev_access SELECT matches)."""
    row_id = secrets.token_urlsafe(16)
    row = {
        "id": row_id, "token_hash": _sha256("at-" + row_id), "client_id": _CLIENT_ID,
        "user_id": _U1, "org_id": "t1", "scope": "mcp",
        "expires_at": _expires_iso(3600), "revoked_at": None,
        "refresh_token_id": refresh_id, "created_at": _expires_iso(0)}
    if code_id is not None:      # #3027: the provenance link, omitted when unused
        row["code_id"] = code_id
    cp.tables.setdefault("oauth_access_tokens", []).append(row)
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


def test_rollback_minted_zero_rows_is_success_not_failure():
    """A never-inserted row must NOT be reported as a failed rollback."""
    cp = FakeControlPlane()
    oauth._rollback_minted(cp, [("oauth_refresh_tokens", "never-existed")], "now",
                           capture=False)                      # must not raise


def test_expiry_forms_agree_and_the_iso_encoders_are_offset_form(monkeypatch):
    """Pin the FORMAT invariant (the fake's `gt` is a lexical compare, and
    `_observe_code`'s expiry check is a `_parse_ts` semantic compare), not an
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
        _seed_code(cp, "c")
        cp.tables["oauth_codes"][0]["expires_at"] = rendered
        results.append(oauth._observe_code(cp, "c")["state"])
    # lexical (the fake's CAS `gt`) and semantic (`_parse_ts`) agree on both forms
    assert results[0] == results[1] == "unclaimed"


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
    """Drive the REAL SupabaseControlPlane and assert the dialect the SETTLE CAS
    relies on (#3027's delivery gate — the one PostgREST CAS left in this path, now
    that the claim itself is SQL inside `redeem_oauth_code`). No live PostgREST is
    needed."""
    cp = SupabaseControlPlane(url=f"http://127.0.0.1:{capture_server.server_port}",
                              service_key="svc")
    capture_server.respond = (200, json.dumps([{"id": 7}]).encode())
    assert oauth._settle_redemption(
        cp, {"id": 7, "redemption_state": oauth.REDEMPTION_CLAIMED,
             "redemption_id": "rid-1"}, oauth.REDEMPTION_MINTED) is True
    cmd, path, headers, _ = capture_server.seen[-1]
    assert cmd == "PATCH"
    assert "id=eq.7" in path and "redemption_state=eq.claimed" in path
    assert "redemption_id=eq.rid-1" in path
    assert headers["Prefer"] == "return=representation"
    assert "Content-Profile" not in headers      # pins that no schema profile is switched

    capture_server.respond = (200, json.dumps(
        [{"used_at": None, "expires_at": "2099-01-01T00:00:00+00:00"}]).encode())
    assert oauth._consume_state(cp, "c") == "unconsumed"
    assert "code_hash=eq." in capture_server.seen[-1][1]


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


def test_empty_body_patch_reads_as_zero_rows(capture_server):
    """PIN THE SEAM #3027's `_observe_code` safety argument rests on.

    A genuine zero-match `PATCH` (with `Prefer: return=representation`) answers 200
    with the JSON array `[]` — a CONTENT-BEARING body. `SupabaseControlPlane.query`
    also maps a 2xx with an EMPTY body to `[]`, so an intermediary that stripped a
    committed claim's body would look exactly like "my claim matched nothing". That
    is the documented residual on `_observe_code` (the retry is still safe — it
    re-runs the claim CAS — but the signal is retryable for a possibly-consumed
    code). This test records the behaviour rather than asserting it is desirable:
    if the seam ever learns to distinguish them, this test is the place to make the
    503 basis strictly stronger.
    """
    cp = SupabaseControlPlane(url=f"http://127.0.0.1:{capture_server.server_port}",
                              service_key="svc")
    capture_server.respond = (200, b"[]")            # genuine zero-match, WITH content
    assert cp.query("oauth_codes", select=["id"], method="PATCH",
                    filters=[("code_hash", "eq", "h")],
                    json_body={"used_at": "T"}) == []
    assert b'"used_at"' in capture_server.seen[-1][3]      # the write WAS sent
    capture_server.respond = (200, b"")              # empty 2xx body — the residual
    assert cp.query("oauth_codes", select=["id"], method="PATCH",
                    filters=[("code_hash", "eq", "h")],
                    json_body={"used_at": "T"}) == []   # ← indistinct from the above


# ── Task 3: the mint RPCs — ONE transaction, so there is nothing to unwind ──
#
# #3025 deleted `_issue_tokens`'s 3-lane taxonomy (rollback / observe / re-arm)
# together with the window it compensated for: the claim and the two token INSERTs
# are now one Postgres function. What is left to pin is that `oauth.py` ROUTES
# through it, that a lost claim writes NOTHING, and that a raise from inside the
# emulated transaction rolls the whole thing back — the property the real SQL is
# built on and the fake must reproduce or every case below is a false negative.

def test_redeem_code_rpc_claims_and_mints_in_one_call():
    cp = FakeControlPlane()
    _seed_code(cp, "rpc-code")
    observed = oauth._observe_code(cp, "rpc-code")
    out = oauth._redeem_code_rpc(cp, observed=observed, code="rpc-code",
                                 scope="mcp")
    assert out["_status"] == "minted"
    assert [fn for fn, _ in cp.rpc_calls] == ["redeem_oauth_code"]
    row = cp.tables["oauth_codes"][0]
    assert row["used_at"] is not None and row["redemption_state"] == "claimed"
    assert row["redemption_id"] == out["_redemption_id"]
    refresh = cp.tables["oauth_refresh_tokens"][0]
    access = cp.tables["oauth_access_tokens"][0]
    assert refresh["id"] == out["_refresh_id"] and access["id"] == out["_access_id"]
    assert refresh["code_id"] == row["id"] and access["code_id"] == row["id"]
    assert access["refresh_token_id"] == refresh["id"]


def test_redeem_code_rpc_lost_claim_writes_nothing():
    """A replay (or a concurrent winner) returns `not_claimable` — and the row is
    untouched. The claim CAS is inside the transaction, so there is no window in
    which a token row could exist without one."""
    cp = FakeControlPlane()
    _seed_code(cp, "rpc-replay")
    observed = oauth._observe_code(cp, "rpc-replay")
    assert oauth._redeem_code_rpc(cp, observed=observed, code="rpc-replay",
                                  scope="mcp")["_status"] == "minted"
    before = copy.deepcopy(cp.tables)
    again = oauth._redeem_code_rpc(cp, observed=observed, code="rpc-replay",
                                   scope="mcp")
    assert again["_status"] == "not_claimable"
    assert cp.tables == before


def test_redeem_code_rpc_burn_records_the_terminal_state_and_mints_nothing():
    """The refusal path: the CALLER validated and refused the code, so the
    function claims it and records `claimed -> burned` (#3027) — in the same
    transaction, with no token row."""
    cp = FakeControlPlane()
    _seed_code(cp, "rpc-burn")
    observed = oauth._observe_code(cp, "rpc-burn")
    out = oauth._redeem_code_rpc(cp, observed=observed, code="rpc-burn",
                                 scope="", burn=True)
    assert out["_status"] == "burned"
    row = cp.tables["oauth_codes"][0]
    assert row["used_at"] is not None
    assert row["redemption_state"] == "burned"
    assert row["redemption_note"] == "terminal"
    assert cp.tables.get("oauth_refresh_tokens", []) == []
    assert cp.tables.get("oauth_access_tokens", []) == []


def test_redeem_code_rpc_mid_function_failure_rolls_the_whole_thing_back():
    """THE property the transaction exists for, asserted at the seam the unit
    tests drive: the refresh INSERT succeeds, the access INSERT fails, and the
    refresh row is GONE and `used_at` is NULL. A fake that applied writes
    incrementally would make every caller-side abort test vacuous."""
    cp = FakeControlPlane()
    _seed_code(cp, "rpc-atomic")
    observed = oauth._observe_code(cp, "rpc-atomic")
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    with pytest.raises(RuntimeError):
        oauth._redeem_code_rpc(cp, observed=observed, code="rpc-atomic", scope="mcp")
    assert cp.tables.get("oauth_refresh_tokens", []) == []    # the insert was undone
    assert cp.tables["oauth_codes"][0]["used_at"] is None
    assert cp.tables["oauth_codes"][0]["redemption_state"] == "unclaimed"


def test_rotate_refresh_rpc_rotates_and_revokes_the_previous_access_row():
    cp = FakeControlPlane()
    rid, _ = _seed_refresh_token(cp, "rt-rpc")
    prev_access = _seed_access_token(cp, refresh_id=rid)
    prev = cp.tables["oauth_refresh_tokens"][0]
    out = oauth._rotate_refresh_rpc(cp, prev=prev)
    assert out and out["refresh_token"] and out["access_token"]
    assert [fn for fn, _ in cp.rpc_calls] == ["rotate_oauth_refresh"]
    assert prev["revoked_at"] is not None
    new = [t for t in cp.tables["oauth_refresh_tokens"] if t["id"] != rid]
    assert len(new) == 1 and new[0]["rotated_from"] == rid
    assert new[0]["revoked_at"] is None
    old_access = next(t for t in cp.tables["oauth_access_tokens"]
                      if t["id"] == prev_access)
    assert old_access["revoked_at"] is not None
    assert len(_live(cp, "oauth_access_tokens")) == 1     # only the new one is live


def test_rotate_refresh_rpc_loser_writes_nothing():
    """A concurrent rotator won the CAS: the loser gets None and leaves NO orphan
    pair — the pair is inserted only after the claim wins, inside the transaction."""
    cp = FakeControlPlane()
    _seed_refresh_token(cp, "rt-loser")
    prev = cp.tables["oauth_refresh_tokens"][0]
    assert oauth._rotate_refresh_rpc(cp, prev=prev) is not None
    before = copy.deepcopy(cp.tables)
    assert oauth._rotate_refresh_rpc(cp, prev=prev) is None
    assert cp.tables == before


def test_the_mint_rpcs_are_read_through_rpc_value_not_rpc():
    """TRANSPORT-SHAPE guard (the #3025 P0).

    `cp.rpc` sends `Prefer: return=minimal`, and PostgREST does NOT echo a
    VOLATILE SECURITY DEFINER function's body under it — measured in production
    twice in this repo (`resolve_signup_token`: "in production the echo is ALWAYS
    empty … the token path 422'd in prod while passing against the fake's
    bare-string rpc"; and `metering_increment`). Both mint RPCs return a jsonb
    envelope the caller must READ, so both must go through `cp.rpc_value`
    (`representation=True`).

    The first draft used `cp.rpc` and passed the entire suite, because the double
    echoed the body anyway. Two things close that: the double now models the
    transport (`FakeControlPlane.rpc` returns the body only when
    `representation=True`), which is why the happy-path tests above would fail on
    a revert — and this test names the requirement, so a reviewer sees WHY.
    """
    cp = FakeControlPlane()
    seen: list[tuple[str, bool]] = []

    def _spy(fn, body=None, *, representation=False):
        seen.append((fn, representation))
        return cp._rpc_dispatch(fn, body)

    cp.rpc = _spy  # type: ignore[method-assign]
    _seed_refresh_token(cp, "rt-rpcvalue")
    prev = cp.tables["oauth_refresh_tokens"][0]
    assert oauth._rotate_refresh_rpc(cp, prev=prev) is not None
    assert seen == [("rotate_oauth_refresh", True)], seen

    seen.clear()
    exchange_cp = FakeControlPlane()
    _seed_base_tables(exchange_cp)
    exchange_cp.rpc = _spy  # type: ignore[method-assign]
    code = "rpcvalue-code"
    verifier = _seed_code(exchange_cp, code)
    observed = oauth._observe_code(exchange_cp, code)
    oauth._redeem_code_rpc(exchange_cp, observed=observed, code=code, scope="mcp")
    assert seen == [("redeem_oauth_code", True)], seen
    assert verifier  # the seeded verifier is irrelevant to the shape under test


def test_an_unreadable_mint_envelope_is_not_collapsed_to_not_claimable():
    """An UNREADABLE envelope must not be read as `not_claimable` (the #3025 P1).

    `not_claimable` is a POSITIVE assertion that the function wrote nothing, and
    a `None`/non-dict result cannot support it: the transaction may have
    committed and only the response been lost. The repo's precedent for exactly
    this envelope is `session_key_mint` (`supabase_control.py`: "the mint may
    ALREADY have committed"), which probes instead of asserting. So the two must
    be distinguished: a parsed `not_claimable` returns `None`, anything else
    raises and leaves the caller's READ-ONLY probe to decide.

    Both RPCs are pinned: the guard is symmetric (`_rotate_refresh_rpc` returns
    None / raises RuntimeError; `_redeem_code_rpc` returns a status dict /
    raises RuntimeError) — and both are asserted on a NON-dict envelope as well
    as on `None`, so a bare scalar body cannot slip through as an unknown status.
    """
    for unreadable in (None, "minted", ["minted"]):
        cp = FakeControlPlane()
        _seed_refresh_token(cp, "rt-unreadable")
        prev = cp.tables["oauth_refresh_tokens"][0]
        cp.rpc_value = lambda fn, body=None, _r=unreadable: _r  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="unreadable envelope"):
            oauth._rotate_refresh_rpc(cp, prev=prev)
        latest = cp.tables["oauth_refresh_tokens"][-1]
        assert latest["id"] == prev["id"], (
            "the presented row is untouched — the raise reports UNKNOWN, not a rotation")

    cp = FakeControlPlane()
    _seed_base_tables(cp)
    code = "unreadable-code"
    _seed_code(cp, code)
    observed = oauth._observe_code(cp, code)
    for unreadable in (None, "minted", ["minted"]):
        cp.rpc_value = lambda fn, body=None, _r=unreadable: _r  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="unreadable envelope"):
            oauth._redeem_code_rpc(cp, observed=observed, code=code, scope="mcp")
    assert cp.tables["oauth_codes"][0]["used_at"] is None


def test_rotate_refresh_rpc_mid_function_failure_does_not_revoke_the_presented_token():
    """The atomicity drill for the rotation: the revoke of the NEW pair's sibling
    fails, so the presented token must be left UNREVOKED and no new row may
    survive — the client's retry then simply succeeds."""
    cp = FakeControlPlane()
    _seed_refresh_token(cp, "rt-atomic")
    prev = cp.tables["oauth_refresh_tokens"][0]
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    with pytest.raises(RuntimeError):
        oauth._rotate_refresh_rpc(cp, prev=prev)
    assert prev["revoked_at"] is None
    assert len(cp.tables["oauth_refresh_tokens"]) == 1
    assert cp.tables.get("oauth_access_tokens", []) == []

# ── Task 4: `exchange_auth_code` — the RPC seam + read-only classification ──
#
# #3025 replaced the claim PATCH with `redeem_oauth_code`. Two failure shapes now
# exist at that seam and they must not be conflated:
#   * the call RAISED (nothing committed, or the response was lost) — the
#     RETRYABILITY of the answer is decided by a READ-ONLY probe, never by the
#     exception itself;
#   * the call RETURNED `not_claimable` — a replay or a lost race, always terminal.

def _fail_redeem(cp, **kw):
    """Fault the mint RPC at the SEAM. `after_commit=False` = the call never ran;
    `after_commit=True` = it committed and the response was lost."""
    cp.fail_rpc("redeem_oauth_code", **kw)


def test_redeem_rpc_raise_pre_commit_is_503_and_the_retry_works(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c1", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    _fail_redeem(cp, after_commit=False)
    r1 = _post_code(tc, cp, "c1", v)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_codes"][0]["used_at"] is None       # observed unconsumed
    assert _post_code(tc, cp, "c1", v).status_code == 200       # retry works


def test_redeem_rpc_committed_then_lost_response_is_terminal_not_retryable(fault_client):
    """The transaction committed and the response never arrived. The probe finds the
    code consumed, so the answer is TERMINAL — the residue is `claimed`, which is
    the reconciler's to resolve, and a retry must not be invited."""
    tc, cp = fault_client
    v = _seed_code(cp, "c2", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    _fail_redeem(cp, after_commit=True)
    r = _post_code(tc, cp, "c2", v)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"   # NOT 503


def test_unobservable_consume_probe_is_terminal(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c3", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    _fail_redeem(cp, after_commit=False)                                 # the call never ran
    cp.fail_query(table="oauth_codes", method="GET",
                  select=["used_at", "expires_at"], times=1)             # the probe
    assert _post_code(tc, cp, "c3", v).json()["error"] == "invalid_grant"


def test_client_auth_failure_during_outage_is_503_not_invalid_grant(fault_client):
    tc, cp = fault_client
    v = _seed_code(cp, "c4", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table="oauth_clients", method="GET", times=1)     # pure read, pre-claim
    r = _post_code(tc, cp, "c4", v)
    assert r.status_code == 503
    assert cp.tables["oauth_codes"][0]["used_at"] is None            # grant NOT burned


def test_pre_claim_org_read_failure_writes_nothing_and_the_retry_succeeds(fault_client):
    """The org-usable read now happens BEFORE the claim (it used to follow it), so a
    control-plane failure there cannot leave a claimed code behind — there is no
    re-arm step because there is nothing to re-arm.
    """
    tc, cp = fault_client
    v = _seed_code(cp, "c5", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    # Scope the fault to the PRODUCTION call shape. A table-wide
    # `organizations` GET fault is racy: the app's startup boot sweep
    # (`tortoise-boot-sweep`) queries organizations with select=["id","name"]
    # and can consume it before the token exchange's `_assert_org_usable`
    # (select=["suspended_at","tier"]). Main scoped the `organizations` arm of
    # test_exactly_one_capture_per_conversion_path for exactly this reason;
    # this site and the one further down still used the table-wide form, so
    # their assertions flipped 503 -> 200 on CI (#4464).
    cp.fail_query(table="organizations", method="GET",
                  select=["suspended_at", "tier"], times=1)
    r1 = _post_code(tc, cp, "c5", v)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_codes"][0]["used_at"] is None
    r2 = _post_code(tc, cp, "c5", v)
    assert r2.status_code == 200 and "access_token" in r2.json()     # the Target's assertion


@pytest.mark.parametrize("table", ["oauth_refresh_tokens", "oauth_access_tokens"])
def test_failure_inside_the_mint_transaction_is_503_and_the_code_redeems_on_retry(
        fault_client, table):
    """A fault INSIDE the emulated transaction: the function rolls back, so the probe
    finds the code unconsumed and the answer is a retryable 503. The fake's rollback
    is what makes this a real assertion rather than a tautology — see
    `test_redeem_code_rpc_mid_function_failure_rolls_the_whole_thing_back`."""
    tc, cp = fault_client
    v = _seed_code(cp, f"m-{table}", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    cp.fail_query(table=table, method="POST", times=1)
    r1 = _post_code(tc, cp, f"m-{table}", v)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_codes"][0]["used_at"] is None       # the rollback held
    assert _live(cp, "oauth_refresh_tokens") == [] and _live(cp, "oauth_access_tokens") == []
    assert _post_code(tc, cp, f"m-{table}", v).status_code == 200


def test_bad_pkce_burns_the_code_and_mints_nothing(fault_client):
    """A validation failure after the claim is recorded as the terminal
    `claimed -> burned` outcome (#3027) INSIDE the same transaction as the claim —
    and no token row is written. The old shape reached this state with a second,
    independent PATCH after the claim; #3025 makes it one statement pair.
    """
    tc, cp = fault_client
    _seed_code(cp, "p1", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    r = _post_code(tc, cp, "p1", verifier="wrong-verifier-wrong-verifier-wrong-verifier")
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    row = cp.tables["oauth_codes"][0]
    assert row["used_at"] is not None and row["redemption_state"] == "burned"
    assert cp.tables.get("oauth_refresh_tokens", []) == []
    assert cp.tables.get("oauth_access_tokens", []) == []



# ── Task 5: `refresh_grant` — wrap the pre-mint reads, map the abort, unmask ──

# #4482: each arm is scoped to the call shape the refresh path ALONE issues.
# `FakeControlPlane._take_fault` consumes the FIRST matching fault, and the boot
# readers — `_sweep_events`->`_iter_registered_orgs`, `_purge_deleted_orgs`,
# `_probe_control_plane` and `_sweep_oauth_retention`, all on daemon workers off
# the TestClient lifespan — query the same tables. A fault whose shape is not
# unique to the refresh path is stolen by one of them, the injected failure never
# reaches the read under test, and the endpoint answers 200 instead of 503
# (`assert (200 == 503)`), nondeterministically. Same class as the #4464 sites in
# this file; same remedy.
@pytest.mark.parametrize("table,select,match", [
    ("oauth_clients", None, None),        # FIRST read on the path — the :726 leak
    # refresh_grant's pre-mint SELECT is the only GET of this table filtering on
    # `token_hash` (the retention sweep filters on `expires_at`), so a shape
    # predicate scopes it without duplicating the production column list.
    ("oauth_refresh_tokens", None,
     lambda t, m, sel, f: m == "GET"
     and any(c == "token_hash" for c, _, _ in (f or []))),
    # _assert_org_usable; the boot sweep's org readers use other select shapes.
    ("organizations", ["suspended_at", "tier"], None),
    ("org_memberships", None, None),     # membership_for_user_org — S4 call site #4
    # NOTE: there is deliberately NO `oauth_access_tokens` arm here. #3025 removed
    # `refresh_grant`'s `prev_access` SELECT — the rotation RPC revokes the previous
    # family's access rows by `refresh_token_id` inside its own transaction, so the
    # read no longer exists to fail.
])
def test_refresh_pre_mint_read_failure_is_503_not_500(fault_client, table, select, match):
    tc, cp = fault_client
    _rid, rt = _seed_refresh_token(cp, "rt-pre")
    cp.fail_query(table=table, method="GET", select=select, match=match, times=1)
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None    # grant untouched
    assert _post_refresh(tc, cp, rt).status_code == 200                  # retry works


def test_refresh_membership_revoke_failure_still_returns_403_invalid_grant(fault_client):
    tc, cp = fault_client
    _rid, rt = _seed_refresh_token(cp, "rt-mem")
    cp.tables["org_memberships"] = []     # `setdefault` would be a NO-OP: the fixture seeded one
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel,
                  times=1, after_mutation=True)   # the revoke commits, then raises
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 403 and r.json()["error"] == "invalid_grant"  # not a 500
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is not None


def test_refresh_suspension_family_revoke_failure_still_returns_403(fault_client):
    tc, cp = fault_client
    _rid, rt = _seed_refresh_token(cp, "rt-susp")
    cp.tables["organizations"][0]["suspended_at"] = "2026-01-01T00:00:00+00:00"
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)  # _revoke_team_family
    assert _post_refresh(tc, cp, rt).status_code == 403


def test_failure_inside_the_rotation_is_503_and_the_retry_succeeds(fault_client):
    """A fault inside the rotation transaction rolls it back, so the presented token
    is still unrevoked and the retry simply works."""
    tc, cp = fault_client
    _rid, rt = _seed_refresh_token(cp, "rt-ok")
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    r1 = _post_refresh(tc, cp, rt)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None
    assert len(cp.tables["oauth_refresh_tokens"]) == 1          # no orphan pair
    r2 = _post_refresh(tc, cp, rt)                              # retry
    assert r2.status_code == 200 and "refresh_token" in r2.json()


def test_rotation_rpc_raise_pre_commit_is_503_and_retry_succeeds(fault_client):
    """The rotation call never ran. The presented token is still unrevoked, the
    READ-ONLY probe sees exactly that, and the answer is retryable rather than
    terminal — the probe, not the exception, decides."""
    tc, cp = fault_client
    _, rt = _seed_refresh_token(cp, "rt-6")
    cp.fail_rpc("rotate_oauth_refresh", after_commit=False)
    r1 = _post_refresh(tc, cp, rt)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None
    assert len(cp.tables["oauth_refresh_tokens"]) == 1          # nothing was minted
    assert _post_refresh(tc, cp, rt).status_code == 200         # the old token still works


def test_rotation_committed_then_lost_response_is_terminal(fault_client):
    """The rotation transaction COMMITTED and its response was lost: the presented
    token is revoked and a live pair exists that the client never saw. Only a
    re-authorization recovers, so the answer is TERMINAL.

    The live-but-undelivered pair is deliberately NOT cleaned up: #3025's own
    Out-of-scope paragraph puts response-delivery loss on a redemption/idempotency
    state, and the rotation carries no such state (that is the code grant's). A
    retry fails on the revoked token, so inviting one would be the untruthful
    signal #2863 removed.
    """
    tc, cp = fault_client
    _rid, rt = _seed_refresh_token(cp, "rt-amb")
    cp.fail_rpc("rotate_oauth_refresh", after_commit=True)
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is not None
    assert len(_live(cp, "oauth_refresh_tokens")) == 1      # the undelivered new pair


def test_prev_access_revoke_failure_aborts_the_rotation_and_the_retry_succeeds(
        fault_client):
    """#3025 folds the previous access-row revoke INTO the rotation transaction (it
    used to be a best-effort trailing write), so a failure there now aborts the
    whole rotation instead of leaving a live-but-undelivered pair beside a
    rotated-away grant. The client's retry simply succeeds — the same
    user-visible promise the old "lane 3" test made ("the client must receive a
    pair it can use"), reached by a strictly safer route."""
    tc, cp = fault_client
    rid, rt = _seed_refresh_token(cp, "rt-7")
    _seed_access_token(cp, refresh_id=rid)          # the row the rotation will revoke
    cp.fail_query(table="oauth_access_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    r1 = _post_refresh(tc, cp, rt)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None   # rolled back
    assert len(_live(cp, "oauth_access_tokens")) == 1     # the old access row is intact
    r2 = _post_refresh(tc, cp, rt)                         # the retry ROTATES
    assert r2.status_code == 200 and "refresh_token" in r2.json()
    assert len(_live(cp, "oauth_access_tokens")) == 1     # only the new pair is live


def test_the_retryability_probe_fails_closed_when_it_cannot_read(fault_client):
    """The probe that decides retryable-503 vs terminal-400 is FAIL-CLOSED.

    `_prev_refresh_unclaimed` is the only thing standing between a failed rotation
    and the untruthful retry signal #2863 removed: an unrevoked row means the whole
    transaction rolled back (a retry is safe); a row it cannot READ is NOT evidence
    of that, so it must NOT be advertised as retryable. Pinning this arm matters
    because the deleted `test_prev_refresh_observation_failure_is_recovered_false`
    covered the same rule for #2863's observation and nothing replaced it: flipping
    the `except` to a fail-open `return True` leaves every OAuth test green
    (measured by mutation during test review).
    """
    tc, cp = fault_client
    _, rt = _seed_refresh_token(cp, "rt-probe-blind")
    cp.fail_rpc("rotate_oauth_refresh", after_commit=False)
    # The mint fails BEFORE dispatch (nothing ran), and then the PROBE read fails
    # too — so the retryability of a state nobody could observe must be refused.
    # Matched by shape, not by a hand-written select literal.
    cp.fail_query(table="oauth_refresh_tokens", method="GET",
                  match=lambda t, m, sel, f: m == "GET" and sel == ["revoked_at"],
                  times=1)
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant", r.text
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None


def test_a_concurrent_rotator_loser_answers_a_terminal_400_at_the_endpoint(
        monkeypatch, fault_client):
    """A rotation whose CAS matched zero rows must reach the boundary as a TERMINAL
    400 — not a 500 and not a 503.

    The wrapper-level race is pinned by `test_rotation_race_single_winner`, which
    calls `_rotate_refresh_rpc` DIRECTLY — so a regression that let `None` fall
    through `refresh_grant`'s `if out is None:` would change the user-visible answer
    (500 → the connector retries a dead grant; 503 → it stampedes) with no test
    noticing. This arm drives it through /oauth/token.
    """
    tc, cp = fault_client
    _, rt = _seed_refresh_token(cp, "rt-loser-endpoint")
    monkeypatch.setattr(oauth, "_rotate_refresh_rpc", lambda _cp, *, prev: None)
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant", r.text
    # The loser's transaction wrote NOTHING: the presented row is untouched and
    # there is no second family.
    assert len(cp.tables["oauth_refresh_tokens"]) == 1
    assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is None


def test_a_lost_claim_race_is_retried_exactly_once(monkeypatch, fault_client):
    """`not_claimable` while the row is STILL `unclaimed` means the claim was lost
    to a request that has not settled — the grant is retried ONCE against the fresh
    row rather than answered terminally.

    Both halves are pinned: the retry HAPPENS (otherwise a benign race becomes a
    spurious invalid_grant), and it happens EXACTLY once (an unbounded retry loop
    here is a mint loop against a degraded control plane). Untested before this
    arm — the test-review pass confirmed no test entered the `continue`.
    """
    tc, cp = fault_client
    verifier = _seed_code(cp, "lost-claim")
    real = oauth._redeem_code_rpc
    calls = []

    def _once(inner, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return {"_status": "not_claimable", "_redemption_id": "stolen"}
        return real(inner, **kw)

    monkeypatch.setattr(oauth, "_redeem_code_rpc", _once)
    r = _post_code(tc, cp, "lost-claim", verifier)
    assert r.status_code == 200 and "access_token" in r.json(), r.text
    assert len(calls) == 2, calls


def test_a_failed_refusal_burn_is_log_only_and_still_answers_the_oauth_error(
        monkeypatch, fault_client):
    """A control-plane failure while RECORDING a refusal must not turn a coherent
    OAuth error into a 500 — and, per the `_log_and_capture` owner table, is
    log-only: it must NOT page Sentry and must not consume the request's one
    capture budget.

    The code simply stays `unclaimed`, so a corrected retry still succeeds — which
    the second request asserts.
    """
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda exc, **kw: calls.append(kw))
    tc, cp = fault_client
    verifier = _seed_code(cp, "burn-fails")
    cp.fail_rpc("redeem_oauth_code", after_commit=False)
    r = _post_code(tc, cp, "burn-fails", "v" * 60)      # wrong PKCE → refusal
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant", r.text
    assert calls == [], calls
    row = cp.tables["oauth_codes"][0]
    assert row["used_at"] is None and row["redemption_state"] == "unclaimed", row
    # The burn never landed, so the CORRECT verifier still redeems.
    r2 = _post_code(tc, cp, "burn-fails", verifier)
    assert r2.status_code == 200 and "access_token" in r2.json(), r2.text


def test_the_refresh_mint_failure_path_captures_exactly_once(monkeypatch, fault_client):
    """The I4 invariant at the capture site #3025 INTRODUCES.

    `refresh_grant`'s mint handler owns the capture that `_issue_tokens` lane 2
    owned before this change (`where="refresh_grant mint"`). The parametrized
    test above cannot reach it — it faults the PRE-mint reads — so without this
    arm the invariant is unpinned at exactly the site the change moved it, which
    is the same class PR #3097 review cycle 1 finding #5 fixed for the older
    arms. The `where` tag is asserted for that same reason: a bare count of 1
    would also hold if the capture came from the global unhandled-exception
    handler (which tags method/path) instead.
    """
    calls = []

    def _cap(exc, **kw):
        calls.append(kw)

    monkeypatch.setattr("tortoise.sentry.capture_exception", _cap)
    tc, cp = fault_client
    _, rt = _seed_refresh_token(cp, "cap-mint-rt")
    cp.fail_rpc("rotate_oauth_refresh", after_commit=False)
    r = _post_refresh(tc, cp, rt)
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"
    assert len(calls) == 1, calls
    assert calls[0].get("tags", {}).get("where") == "refresh_grant mint", calls[0]


def test_an_invalid_resource_burns_the_code_like_every_other_refusal(fault_client):
    """`parse_resource`'s 400 is a TERMINAL refusal like the rest, and it is raised
    AFTER the code is claimed, so it must BURN.

    The pre-#3025 code burned every non-retryable `OAuthError` raised after the
    claim (`if consumed and not isinstance(exc, OAuthTemporarilyUnavailable)`).
    In the #3025 shape the refusal is recorded in the same transaction as the
    claim, so if `parse_resource` were left OUTSIDE the collector the burn would
    depend on WHICH helper raised — the sibling `resource-mismatch` burning while
    `invalid_resource` left the row `unclaimed`. Both branches are pinned here.
    """
    tc, cp = fault_client
    verifier = _seed_code(cp, "bad-resource")
    r = _post_code(tc, cp, "bad-resource", verifier, resource="not-a-valid-resource-uri")
    assert r.status_code == 400 and r.json()["error"] == "invalid_resource", r.text
    row = cp.tables["oauth_codes"][0]
    assert row["redemption_state"] == "burned", row
    assert row["redemption_note"] == "terminal", row
    assert _live(cp, "oauth_access_tokens") == []
    assert _live(cp, "oauth_refresh_tokens") == []
    # And the burn is terminal: the same code cannot be exchanged afterwards.
    r2 = _post_code(tc, cp, "bad-resource", verifier, resource=None)
    assert r2.status_code == 400 and r2.json()["error"] == "invalid_grant", r2.text


@pytest.mark.parametrize("table", ["oauth_clients", "organizations"])
@pytest.mark.parametrize("grant", ["code", "refresh"])
def test_exactly_one_capture_per_conversion_path(monkeypatch, fault_client, table, grant):
    calls = []

    def _cap(exc, **kw):
        calls.append(kw)

    monkeypatch.setattr("tortoise.sentry.capture_exception", _cap)
    tc, cp = fault_client
    # Scope the fault to the PRODUCTION call shape. A table-wide
    # `organizations` GET fault is racy: the app's startup boot sweep
    # (`tortoise-boot-sweep`) queries organizations with select=["id","name"]
    # and can consume it before the token exchange's `_assert_org_usable`
    # (select=["suspended_at","tier"]) — offloading the grant widens that
    # window. FakeControlPlane recommends a shape predicate for exactly this.
    if table == "organizations":
        cp.fail_query(table=table, method="GET",
                      select=["suspended_at", "tier"], times=1)
    else:
        cp.fail_query(table=table, method="GET", times=1)
    if grant == "code":                                        # pre-consume
        v = _seed_code(cp, f"cap-{table}")
        _post_code(tc, cp, f"cap-{table}", v)
    else:                                                      # refresh pre-mint
        _post_refresh(tc, cp, _seed_refresh_token(cp, f"cap-{table}-rt")[1])
    assert len(calls) == 1
    # The capture must come from the oauth CONVERSION path (`where=` tag), not from the
    # pre-existing global unhandled-exception handler (which tags method/path) — without
    # this, the count alone is 1 both pre- and post-fix and pins nothing.
    assert calls[0].get("tags", {}).get("where"), calls[0]


# ── Task 6: POST /oauth/token — typed boundary + coherent last-resort net ────

@pytest.mark.parametrize("fn,data", [
    ("exchange_auth_code", {"grant_type": "authorization_code", "code": "x"}),
    ("refresh_grant", {"grant_type": "refresh_token", "refresh_token": "x"}),
])
def test_unconverted_failure_returns_coherent_500_not_bare_detail(monkeypatch, fault_client, fn, data):
    """Raise from OUTSIDE the dispatch — patching `_verify_client_auth` would be caught by
    Task 4's own constructive-clean arm (503), never reaching this net. The body must drive
    the grant whose function is patched, or the other one runs and never hits the net."""
    monkeypatch.setattr(f"tortoise.oauth.{fn}",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    tc, _ = fault_client
    r = tc.post("/oauth/token", data=data)
    assert r.status_code == 500
    assert r.json().get("error") == "server_error" and "detail" not in r.json()


@pytest.mark.parametrize("grant,data", [
    ("authorization_code", {"code": "x", "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT}),
    ("refresh_token", {"refresh_token": "rt", "client_id": _CLIENT_ID}),
])
def test_boundary_converts_control_plane_failures_to_503_not_500(monkeypatch, grant, data):
    """ErrorControlPlane (every call raises) — the wrap in oauth.py must turn it into a
    503 before the boundary, on BOTH grant types."""
    cp = ErrorControlPlane()
    monkeypatch.setattr("tortoise.hosted_api._oauth_control_plane", lambda: (cp, True))
    with TestClient(app, raise_server_exceptions=False) as tc:
        r = tc.post("/oauth/token", data={"grant_type": grant, **data})
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"


def test_boundary_logs_and_captures_the_unconverted_exception(monkeypatch, caplog, fault_client):
    calls = []
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda exc, **kw: calls.append(exc))
    monkeypatch.setattr("tortoise.oauth.exchange_auth_code",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    tc, _ = fault_client
    with caplog.at_level(logging.WARNING, logger="tortoise.oauth"):
        r = tc.post("/oauth/token", data={"grant_type": "authorization_code", "code": "x"})
    assert r.status_code == 500 and len(calls) == 1 and "oauth/token boundary" in caplog.text


def test_capture_exception_raising_does_not_break_the_typed_error(monkeypatch, fault_client):
    """S6(c): if Sentry itself raises, the typed 503/400 must survive (it must not
    become the bare 500 this task exists to remove)."""
    monkeypatch.setattr("tortoise.sentry.capture_exception",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentry down")))
    tc, cp = fault_client
    v = _seed_code(cp, "sentry", client_id=_CLIENT_ID, redirect_uri=_REDIRECT)
    # Scoped to the production call shape for the same reason as the other two
    # shape-predicated sites in this file — a table-wide `organizations` GET
    # fault races the startup boot sweep's select=["id","name"] query (#4464).
    cp.fail_query(table="organizations", method="GET",
                  select=["suspended_at", "tier"], times=1)
    r = _post_code(tc, cp, "sentry", v)
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"


def test_transient_503_conventions_agree_on_status():
    """Two drivers (OAuth §5.2 body vs the dashboard detail body), one rule: a transient
    control-plane failure is a 503. This pins only the STATUS agreement — the bodies are
    deliberately different, and that divergence is documented on the class."""
    from tortoise.hosted_api import _control_plane_unavailable
    assert _control_plane_unavailable().status_code == 503
    assert oauth.OAuthTemporarilyUnavailable().status == 503
    assert oauth.OAuthTemporarilyUnavailable().body() == {
        "error": "temporarily_unavailable",
        "error_description": "Temporary control-plane failure — retry."}


# ── Control-plane round-trip envelope (#2848, re-measured for #3025) ────────
#
# #3669 made the token grants loop-SAFE (the whole grant is offloaded as a unit).
# It did not make them FAST: both grants are still a SERIES of round-trips, so a
# control plane that is slow-but-alive MULTIPLIES into request latency and can
# cross the OAuth budget without anything being "down".
#
# PINNED HERE (all MEASURED; each test asserts its exact sequence). The #3025
# column is the point of this block: moving the claim + the two token INSERTs
# into ONE RPC did not just make the mint atomic, it REMOVED round-trips — the
# code grant falls 6 -> 5 and the refresh grant 9 -> 5, because the sequence of
# PostgREST writes is now a single function call.
#
#   authorization_code, registry client ........  6 ->  5  (10 s budget)
#   authorization_code, mint ABORT ............. 10 ->  5  (10 s budget)
#   authorization_code, LOST-DELIVERY SETTLE ... 11 ->  7  (10 s budget)
#   refresh_token ..............................  9 ->  5  (30 s budget)
#   refresh_token, mint ABORT .................. 12 ->  6  (30 s budget)
#
# ⛔ The code grant's failure paths are NO LONGER longer than its success path —
# the old property ("unwinding a half-minted pair costs extra round-trips, so the
# worst case lands exactly when the control plane is already slow") is GONE BY
# CONSTRUCTION, not by mistake. There is nothing to unwind: a failure inside the
# mint rolls the transaction back, so the code grant's only extra cost is the
# READ-ONLY retryability probe.
#
# The refresh abort is the one exception, and it is +1 rather than +0: the
# `_prev_refresh_unclaimed` classification probe runs AFTER the poisoned-scope
# branch in `refresh_grant` (that branch raises terminal `invalid_grant` itself,
# so it never reaches the probe), which means the mint's own try/except pays one
# extra READ. The code grant's probe is the LAST call of its series and does not
# extend it. A failure path that is longer than the success path by exactly one
# bounded read is not the pre-#3025 hazard — the hazard was unbounded
# compensation against a degraded control plane. The one remaining LONGER path
# is the LOST-DELIVERY SETTLE (7), which pays the settle PATCH plus the two
# compensating revokes — and it is TERMINAL, so it cannot be retried into a
# stampede.
#
# METHOD NOTE — a shape MEASURED and deliberately NOT pinned, recorded so the
# next lane does not re-derive it:
#
#   * A CIMD client's ("Claude connector") token exchange is the same shape. A CIMD
#     row cannot reach /oauth/token for the first time: both doors that mint a code
#     (GET /oauth/authorize, POST /oauth/consent) call `resolve_client`, which is
#     what provisions the row. Measured in the production order —
#     `validate_authorize_params` then `exchange_auth_code`. The extra round-trip
#     you get by calling `exchange_auth_code` with a hand-seeded code is an
#     ARTIFACT of skipping that prerequisite, not a shape the token budget sees;
#     the provisioning cost is real but is paid on the authorize/consent doors,
#     which are not on the token budget.
#
# These tests pin the serial COUNT and the additivity, and deliberately assert no
# wall-clock SLI, which would be a load-dependent flake rather than a contract.



class _RoundTripCounter:
    """Proxy over the fake control plane: records ``(op, table, method)`` for
    every round-trip the seam exposes through ``query``, ``rpc`` or
    ``rpc_value``, and can delay each one.

    Answers "how many SERIAL round-trips did that request make?" — the question
    any total-latency bound depends on. Writes the test makes straight to
    ``cp.tables`` are deliberately not counted, so the count is the request's,
    not the test's.

    LIMIT, stated because it bounds what the assertions below can promise: only
    those THREE entry points are intercepted. A round-trip a helper made through
    any other attribute would be invisible to both the count and the delay. The
    measured series is exact for the paths pinned here; a new control-plane
    helper added to one of them must be routed through ``_trip`` (or this class
    extended) or the guard silently stops covering it.
    """

    def __init__(self, inner, per_call_s: float = 0.0):
        self._inner = inner
        self.per_call_s = per_call_s
        self.calls: list[str] = []

    def _trip(self, op, fn, *a, **k):
        # The kwarg spelling counts as much as the positional one: a refactor to
        # `cp.query(table=...)` must not record `query:?` (PR #7565 review).
        table = a[0] if a and isinstance(a[0], str) else (
            k.get("table") or k.get("fn") or "?")
        method = k.get("method") or ""
        self.calls.append(f"{op}:{table}{':' + method if method else ''}")
        if self.per_call_s:
            time.sleep(self.per_call_s)
        return fn(*a, **k)

    def query(self, *a, **k):
        return self._trip("query", self._inner.query, *a, **k)

    def rpc(self, *a, **k):
        return self._trip("rpc", self._inner.rpc, *a, **k)

    def rpc_value(self, *a, **k):
        return self._trip("rpc_value", self._inner.rpc_value, *a, **k)

    def __getattr__(self, name):
        # Never proxy dunders or the wrapped plane's own private state: a proxy
        # that forwards `__deepcopy__`/`__getstate__`/`_inner` silently changes
        # how the object is copied or pickled (PR #7565 review).
        if name.startswith("__") or name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)


def _exchange_auth_code(counter, cp, code, client_id=_CLIENT_ID):
    """One authorization-code exchange over `counter`; asserts it succeeded."""
    body = {"grant_type": "authorization_code", "code": code,
            "code_verifier": _seed_code(cp, code, client_id=client_id),
            "client_id": client_id, "redirect_uri": _REDIRECT,
            "resource": None}
    out = oauth.exchange_auth_code(counter, body, "https://tortoise.example")
    assert out.get("access_token"), out


def _assert_serial_additivity(counter, t0, elapsed):
    """The calls are SERIAL, so the cost is additive in the per-call delay.

    A LOWER bound, so it is deterministic on a loaded host: each injected call
    sleeps at least D. If the calls were concurrent the total would be ~D, not
    N*D, so this is also the falsifier for "they are parallel".
    """
    n = len(counter.calls)
    assert elapsed >= n * 0.05, (
        f"{n} serial round-trips at 50 ms each must cost >= {n * 0.05:.2f}s; "
        f"took {elapsed:.3f}s — the calls are NOT serial (or not all counted)")
    return n


def test_authorization_code_exchange_is_a_serial_series_of_round_trips():
    """The authorization-code exchange makes FIVE serial round-trips (MEASURED),
    down from six: #3025 replaced the claim PATCH + two token INSERTs with ONE
    `redeem_oauth_code` call, so the write sequence is no longer a series at all.

    Series: client auth read, the code read (validation + classification), the
    org-usable read, the mint RPC, the #3027 delivery settle.

    The remaining arm is per-call latency, so this still bounds Anthropic's 10 s
    token budget as a function of the control plane rather than of our code.
    Measured by calling ``exchange_auth_code`` DIRECTLY rather than through the
    endpoint, deliberately: the app shares ONE control-plane client with
    background maintenance (the retention sweep reaches ``account_deletions``), so
    a wall-clock delay in an endpoint-level test lets that work interleave and the
    counter then reports calls that are not this request's. A direct call has no
    event loop, so the series is exactly the exchange's own.
    """
    cp = FakeControlPlane()
    _seed_base_tables(cp)
    counter = _RoundTripCounter(cp)
    _exchange_auth_code(counter, cp, "series-code")

    # THE SERIES — each entry is one serial round-trip on the request's path.
    assert counter.calls == [
        "query:oauth_clients",            # _verify_client_auth
        "query:oauth_codes",              # _observe_code (validate + classify)
        "query:organizations",            # _assert_org_usable
        "rpc_value:redeem_oauth_code",          # claim + BOTH inserts, one transaction
        "query:oauth_codes:PATCH",        # _settle_redemption (the delivery gate)
    ], counter.calls

    counter.per_call_s = 0.05
    counter.calls.clear()
    t0 = time.perf_counter()
    _exchange_auth_code(counter, cp, "series-code-2")
    elapsed = time.perf_counter() - t0
    assert counter.calls == [
        "query:oauth_clients", "query:oauth_codes", "query:organizations",
        "rpc_value:redeem_oauth_code", "query:oauth_codes:PATCH",
    ], counter.calls
    _assert_serial_additivity(counter, t0, elapsed)


def test_refresh_grant_is_now_the_SHORTEST_serial_series():
    """The refresh grant makes FIVE serial round-trips — down from NINE. #3025 did
    two things here: the claim + both INSERTs + the previous-access revoke became
    one `rotate_oauth_refresh` call, and the `prev_access` SELECT disappeared
    entirely (the RPC revokes by `refresh_token_id`, so the caller no longer has to
    read the rows first).

    Series: client auth, the presented token's row, the org-usable read, the
    membership read, the rotation RPC.

    Pinned so that a round-trip ADDED here (a quota read, say) is visible — the
    same silent-regression failure the code-grant leg exists for.
    """
    cp = FakeControlPlane()
    _seed_base_tables(cp)
    rid, token = _seed_refresh_token(cp, "series-rt")
    _seed_access_token(cp, refresh_id=rid)
    counter = _RoundTripCounter(cp)

    out = oauth.refresh_grant(
        counter, {"grant_type": "refresh_token", "refresh_token": token,
                  "client_id": _CLIENT_ID}, "https://tortoise.example")
    assert out.get("access_token"), out

    assert counter.calls == [
        "query:oauth_clients",           # _verify_client_auth
        "query:oauth_refresh_tokens",    # the presented token's row
        "query:organizations",           # org still usable
        "query:org_memberships",         # membership not lapsed
        "rpc_value:rotate_oauth_refresh",      # claim + mint + revoke, one transaction
    ], counter.calls

    n = len(counter.calls)
    counter.per_call_s = 0.05
    counter.calls.clear()
    rid2, legacy = _seed_refresh_token(cp, "series-rt-2")
    _seed_access_token(cp, refresh_id=rid2)
    t0 = time.perf_counter()
    out2 = oauth.refresh_grant(
        counter, {"grant_type": "refresh_token", "refresh_token": legacy,
                  "client_id": _CLIENT_ID}, "https://tortoise.example")
    elapsed = time.perf_counter() - t0
    assert out2.get("access_token"), out2
    assert _assert_serial_additivity(counter, t0, elapsed) == n, (
        f"the refresh series changed between runs: {n} then {len(counter.calls)}")


def _fail_access_token_mint(cp):
    """Make the access-token INSERT raise: a failure INSIDE the mint transaction.

    Patches the instance rather than using ``fail_query`` deliberately — this test
    wants the fault to persist, not the ``_FAULT_CPS`` accounting the other tests
    in this file are built around. The emulated RPC rolls the whole thing back.
    """
    original = cp.query

    def _q(table, **kw):
        if table == "oauth_access_tokens" and kw.get("method") == "POST":
            raise RuntimeError("control plane 500")
        return original(table, **kw)

    cp.query = _q


def test_abort_series_is_bounded_on_both_grants():
    """A mint failure is no longer UNBOUNDED — and on the code grant it is no
    longer longer than the success path at all (MEASURED 5 vs 5); that is the
    #3025 change: a failure inside the transaction ROLLS BACK, so there is no
    half-minted pair to unwind and no extra PATCHes to pay for.

    The old shape was the opposite and was pinned deliberately (10 and 12): the
    unwinding cost landed exactly when the control plane was already slow.

    The refresh grant pays exactly ONE extra read (6 vs 5) — the
    `_prev_refresh_unclaimed` probe that classifies the error as retryable or
    terminal. That is bounded and READ-ONLY, which is the property that matters;
    the pin below is written so an extra round-trip cannot hide behind it.

    Both grants still answer a RETRYABLE 503 (the transaction rolled back, so the
    retry is safe).
    """
    # ── authorization_code ──
    cp = FakeControlPlane()
    _seed_base_tables(cp)
    counter = _RoundTripCounter(cp)
    _fail_access_token_mint(cp)
    verifier = _seed_code(cp, "abort-code")

    with pytest.raises(oauth.OAuthTemporarilyUnavailable):
        oauth.exchange_auth_code(
            counter, {"grant_type": "authorization_code", "code": "abort-code",
                      "code_verifier": verifier, "client_id": _CLIENT_ID,
                      "redirect_uri": _REDIRECT, "resource": None},
            "https://tortoise.example")

    assert counter.calls == [
        "query:oauth_clients",           # _verify_client_auth
        "query:oauth_codes",             # _observe_code
        "query:organizations",           # _assert_org_usable
        "rpc_value:redeem_oauth_code",         # the mint — FAILS inside, rolls back
        "query:oauth_codes",             # _consume_state: the READ-ONLY retry probe
    ], counter.calls

    # ── refresh_token ──
    cp2 = FakeControlPlane()
    _seed_base_tables(cp2)
    rid, token = _seed_refresh_token(cp2, "abort-rt")
    _seed_access_token(cp2, refresh_id=rid)
    counter2 = _RoundTripCounter(cp2)
    _fail_access_token_mint(cp2)

    with pytest.raises(oauth.OAuthTemporarilyUnavailable):
        oauth.refresh_grant(
            counter2, {"grant_type": "refresh_token", "refresh_token": token,
                       "client_id": _CLIENT_ID}, "https://tortoise.example")

    assert counter2.calls == [
        "query:oauth_clients",
        "query:oauth_refresh_tokens",
        "query:organizations",
        "query:org_memberships",
        "rpc_value:rotate_oauth_refresh",      # the rotation — FAILS inside, rolls back
        "query:oauth_refresh_tokens",    # _prev_refresh_unclaimed: the retry probe
    ], counter2.calls

    assert len(counter.calls) == 5 and len(counter2.calls) == 6, (
        f"the abort series changed shape: {len(counter.calls)} / {len(counter2.calls)}")
    assert counter2.calls[-1] == "query:oauth_refresh_tokens", (
        "the refresh abort's +1 must be the READ-ONLY retryability probe, not a "
        f"write: {counter2.calls[-1]!r}")


def test_lost_settle_is_still_the_longest_code_grant_series(monkeypatch):
    """The code grant's LONGEST series is still the LOST-DELIVERY SETTLE, MEASURED
    as 7 (down from 11): the five-call success series, then two revokes to
    compensate the pair.

    When the mint RPC SUCCEEDS but ``_settle_redemption`` loses its CAS (#3027's
    reconciler race — a concurrent attempt taking the claim), the request has
    already minted the pair, so it must revoke it and still record the outcome.
    This is the shape a degraded control plane produces, not a contrived race: the
    claim grace is 60 s, and at the per-call latency #2848 observed six serial
    calls already exceed it.

    The OUTCOME is pinned, not just the count, and the assertion is written to
    distinguish the two: ``OAuthTemporarilyUnavailable`` SUBCLASSES ``OAuthError``
    (``oauth.py``), so ``pytest.raises(OAuthError)`` alone would also accept the
    retryable 503. This path is TERMINAL — the client is told to re-run
    authorization rather than retry — which is the difference between a connector
    re-authorizing and a connector stampeding a degraded control plane.

    ⛔ The lost-delivery pair is deliberately NOT cleaned up here — the reconciler
    owns it, and a transaction cannot fix a lost response (#3025's Out-of-scope).
    """
    cp = FakeControlPlane()
    _seed_base_tables(cp)
    real_settle = oauth._settle_redemption
    counter = _RoundTripCounter(cp)

    def _losing_settle(inner, *a, **k):
        real_settle(inner, *a, **k)
        return False

    monkeypatch.setattr(oauth, "_settle_redemption", _losing_settle)

    with pytest.raises(oauth.OAuthError) as ei:
        oauth.exchange_auth_code(
            counter, {"grant_type": "authorization_code", "code": "lost-code",
                      "code_verifier": _seed_code(cp, "lost-code"),
                      "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT,
                      "resource": None},
            "https://tortoise.example")

    # The count is not the whole contract: OAuthTemporarilyUnavailable SUBCLASSES
    # OAuthError, so ``raises(OAuthError)`` would also pass on the retryable 503.
    # Assert the TERMINAL outcome explicitly, or a regression that made this path
    # retryable — inviting a stampede on an already-degraded control plane — would
    # keep the suite green.
    assert not isinstance(ei.value, oauth.OAuthTemporarilyUnavailable), (
        f"the lost-settle path must be terminal, not retryable; got {type(ei.value).__name__}")

    assert counter.calls == [
        "query:oauth_clients",           # _verify_client_auth
        "query:oauth_codes",             # _observe_code
        "query:organizations",           # _assert_org_usable
        "rpc_value:redeem_oauth_code",         # claim + BOTH inserts
        "query:oauth_codes:PATCH",       # _settle_redemption — LOST, and paid anyway
        "query:oauth_refresh_tokens:PATCH",  # compensate the pair
        "query:oauth_access_tokens:PATCH",
    ], counter.calls
    assert len(counter.calls) == 7, (
        f"the lost-settle series changed shape: {len(counter.calls)}")
