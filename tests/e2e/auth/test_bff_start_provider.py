"""
Provider selection for `/auth/start` (#4054): a strict allowlist, never a passthrough.

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev`) with the REAL D1
binding and the REAL S256 PKCE exchange against the shared mock Supabase. So
"GoTrue was called", "the flow row was written" and "the method is s256" are
OBSERVED rather than inferred from a status code.

HARNESS NOTE — why this is a separate file
------------------------------------------
The auth Functions live at `website/apps/dashboard/functions` (moved there by
#4054). `tests/e2e/auth/test_bff_flow.py` still stands wrangler up from
`website/` — the marketing site — where `/auth/*` no longer exists: its own
`test_start_sets_bound_httponly_flow_cookie` returns 404 on an unmodified
checkout. That file also covers `/blog/api/purge`, which only exists under
`website/functions`, so its fixture cannot be repointed at the dashboard without
losing that half. This suite therefore uses the corrected dashboard harness
already established in this directory by `test_link_flow.py`, and reuses the
shared `mock_supabase.mjs` (which implements the authorize hop and validates the
PKCE verifier for real).
"""
from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pytest
from bff_test_helpers import d1_sqlite_files, pick_free_port, require_toolchain, stop, wait_for_port

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_supabase.mjs"

# Outside every range the sibling suites claim (8790-8801, 8970-8971,
# 8980-8981, 8995, 8997-8998, 9002-9003).
APP_PORT = int(os.environ.get("AUTH_START_APP_PORT", "9004"))
MOCK_PORT = int(os.environ.get("AUTH_START_MOCK_PORT", "9005"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


class Proc:
    def __init__(self, argv, cwd, env=None):
        self.p = subprocess.Popen(
            argv, cwd=cwd, env=env or os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def stop(self):
        stop(self.p)


def _d1_sqlite() -> Path:
    """Newest local D1 database file, waiting for wrangler to create it."""
    deadline = time.time() + 30
    while time.time() < deadline:
        files = sorted(
            d1_sqlite_files(DASHBOARD_DIR),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if files:
            return files[0]
        time.sleep(0.3)
    raise RuntimeError(f"no D1 sqlite appeared under {DASHBOARD_DIR}")


def _flow_row(flow_id: str) -> dict | None:
    db = _d1_sqlite()
    con = sqlite3.connect(db, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT flow_id,kind,verifier,expires_at FROM auth_flows WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT, "app and mock must not share a port"
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = Proc([node, str(MOCK)], cwd=str(MOCK.parent), env=mock_env)
    try:
        if not wait_for_port(MOCK_PORT):
            raise RuntimeError(f"mock never opened {MOCK_PORT}")
    except RuntimeError:
        mock.stop()
        pytest.fail("mock supabase failed to start")

    # `wrangler pages dev <dir>` from the repo root does not discover
    # `<dir>/functions`, so cwd is the site dir and the argv is ".".
    app = Proc(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
            "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
    )
    try:
        if not wait_for_port(APP_PORT):
            raise RuntimeError(f"app never opened {APP_PORT}")
    except RuntimeError:
        app.stop()
        mock.stop()
        out = b""
        with contextlib.suppress(Exception):
            out = app.p.stdout.read() if app.p.stdout else b""
        pytest.fail(f"pages dev failed to start; output:\n{out.decode('utf-8', 'replace')[-3000:]}")
    time.sleep(2.0)  # let the function bundler finish

    yield {"app": APP, "mock": MOCK_URL}
    app.stop()
    mock.stop()


# ---------------------------------------------------------------------------
# Request + observation helpers
# ---------------------------------------------------------------------------
class _LocalhostSecurePolicy(http.cookiejar.DefaultCookiePolicy):
    """Accept `Secure` cookies over http on loopback.

    Browsers treat http://127.0.0.1 as a secure context and store Secure cookies
    there; Python's cookiejar does not. Without this every `__Host-` cookie is
    silently dropped and the tests fail for a reason unrelated to the app.
    """

    def return_ok_secure(self, cookie, request):
        host = request.get_full_url() or ""
        if host.startswith("http://127.0.0.1") or host.startswith("http://localhost"):
            return True
        return super().return_ok_secure(cookie, request)


class Jar:
    """Cookie jar with redirect-following disabled, recording the hop chain."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar(policy=_LocalhostSecurePolicy())
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def get(self, url, follow=False):
        req = urllib.request.Request(url, method="GET")
        if follow:
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self.jar),
                urllib.request.HTTPRedirectHandler(),
            )
        else:
            op = self.opener
        try:
            with op.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace"), _headers(r)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), _headers(e)

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
    """Headers as a plain dict, PRESERVING duplicate values (Set-Cookie)."""
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


def _mock_paths(reset: bool = False) -> list[str]:
    """Paths the mock upstream has actually been asked for.

    Observation, not inference: a 400 from our own handler and a 400 produced
    after an upstream call look identical from the response alone, so "GoTrue was
    NOT called" has to be read off the upstream's own request log.
    """
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/paths", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["paths"]


def _authorize(headers: dict) -> tuple[str, dict]:
    loc = headers.get("Location", "")
    assert loc, f"expected a Location header, got: {headers}"
    return loc, parse_qs(urlparse(loc).query)


# ---------------------------------------------------------------------------
# Contract: an allowlisted provider is forwarded with the PKCE contract intact
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["github", "google"])
def test_start_provider_targets_gotrue_with_s256(stack, provider):
    j = Jar()
    status, body, headers = j.get(f"{APP}/auth/start?provider={provider}", follow=False)
    assert status == 302, f"provider={provider} must redirect, got {status} {body}"

    loc, q = _authorize(headers)
    assert loc.startswith(f"{MOCK_URL}/auth/v1/authorize"), loc
    assert q.get("provider") == [provider], f"provider not forwarded to GoTrue: {q}"
    # The PKCE contract is unchanged by the new parameter — S256 only, never plain.
    assert q.get("code_challenge"), f"PKCE challenge missing for {provider}: {q}"
    assert q.get("code_challenge_method") == ["s256"], q
    assert q.get("redirect_to") == [f"{APP}/auth/callback"], q

    # The flow cookie is the binding half of the class-8 mitigation.
    assert "__Host-authflow=" in headers.get("Set-Cookie", ""), (
        f"flow cookie missing: {headers.get('Set-Cookie')}"
    )


def test_start_provider_absent_defaults_to_email(stack):
    """Today's behaviour, preserved exactly: no param -> provider=email."""
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302, f"absent provider must still work, got {status}"
    _, q = _authorize(headers)
    assert q.get("provider") == ["email"], f"absent provider must default to email, got {q}"
    assert q.get("code_challenge_method") == ["s256"], q


# ---------------------------------------------------------------------------
# Contract: anything else is refused with a 400 and never reaches GoTrue
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        "RANDOM",  # unknown
        "<script>alert(1)</script>",  # injection attempt
        "",  # present but EMPTY — not the same as absent
        "GITHUB",  # case is not normalised
        "github2",  # prefix attack against a naive startsWith check
        "email,github",  # multi-value smuggling
        "github ",  # trailing space
        "github&redirect_to=https://evil.example",  # param smuggling
    ],
)
def test_start_rejects_invalid_provider_with_400(stack, bad):
    j = Jar()
    _mock_paths(reset=True)
    status, body, headers = j.get(
        f"{APP}/auth/start?provider={quote(bad, safe='')}", follow=False
    )
    assert status == 400, f"provider={bad!r} must be 400, got {status} {body}"
    assert json.loads(body).get("error") == "unsupported_provider", body
    # Rejected means nothing was minted and nothing was forwarded upstream.
    assert "__Host-authflow=" not in headers.get("Set-Cookie", ""), (
        f"a rejected provider minted a flow cookie: {headers.get('Set-Cookie')}"
    )
    assert "Location" not in headers, f"a rejected provider redirected: {headers}"
    assert "/auth/v1/authorize" not in _mock_paths(), (
        f"GoTrue was called for a rejected provider {bad!r}"
    )


def test_start_ignores_a_client_supplied_redirect_to(stack):
    """No open redirect: `redirect_to` is server-owned, never read from the query."""
    j = Jar()
    status, _, headers = j.get(
        f"{APP}/auth/start?provider=github"
        "&redirect_to=https%3A%2F%2Fevil.example%2Fsteal",
        follow=False,
    )
    assert status == 302
    _, q = _authorize(headers)
    assert q.get("redirect_to") == [f"{APP}/auth/callback"], (
        f"a client-controlled redirect_to leaked into the authorize URL: {q}"
    )


# ---------------------------------------------------------------------------
# Contract: the flow row is written, and `kind` stays orthogonal to `provider`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["github", "google"])
def test_start_provider_writes_a_matching_flow_row(stack, provider):
    """The positive case: a live `auth_flows` row, bound to the flow cookie.

    Asserted BOTH ways, so neither half can hide a regression:
      - directly, by reading the D1 row the cookie names
      - end-to-end, by completing the S256 exchange (the mock recomputes the
        verifier, so a session can only be minted from a correct row)
    """
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start?provider={provider}", follow=False)
    assert status == 302
    flow_id = j.cookie("__Host-authflow")
    assert flow_id, f"flow cookie must be set for provider={provider}"

    row = _flow_row(flow_id)
    assert row is not None, (
        f"provider={provider} produced no `auth_flows` row — the INSERT did not run"
    )
    assert row["verifier"], f"the row's PKCE verifier must be present: {row}"
    # `kind` is the FLOW kind (signin/recovery — consumed by confirm.ts), not the
    # OAuth provider. Provider selects the GoTrue identity flow; it must NOT
    # overwrite the flow kind, or recovery detection breaks.
    assert row["kind"] == "signin", (
        f"provider must not clobber the flow kind, got kind={row['kind']!r}"
    )

    status, _, headers = j.get(headers["Location"], follow=False)  # GoTrue authorize
    assert status == 302, f"authorize hop failed for {provider}: {status}"
    status, _, headers = j.get(headers["Location"], follow=False)  # /auth/callback
    assert status == 302, f"callback hop failed for {provider}: {status}"
    assert "__Host-session=" in headers.get("Set-Cookie", ""), (
        f"no session for provider={provider} — the auth_flows row is missing or "
        f"its verifier does not match: {headers.get('Set-Cookie')}"
    )


def test_start_provider_does_not_clobber_an_explicit_flow_kind(stack):
    """`kind=recovery` + `provider=github` must stay retrieval-safe."""
    j = Jar()
    status, _, headers = j.get(
        f"{APP}/auth/start?kind=recovery&provider=github", follow=False
    )
    assert status == 302
    _, q = _authorize(headers)
    assert q.get("provider") == ["github"], q

    flow_id = j.cookie("__Host-authflow")
    assert flow_id
    row = _flow_row(flow_id)
    assert row is not None
    assert row["kind"] == "recovery", (
        f"an explicit flow kind must survive provider selection, got {row['kind']!r}"
    )


# ---------------------------------------------------------------------------
# Contract: the POST alias is untouched
# ---------------------------------------------------------------------------
def test_start_post_alias_still_accepts_a_provider(stack):
    """`onRequestPost` is the same handler; adding a param must not break it."""
    req = urllib.request.Request(f"{APP}/auth/start?provider=google", method="POST", data=b"")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, headers = r.status, _headers(r)
    except urllib.error.HTTPError as e:
        status, headers = e.code, _headers(e)

    assert status == 302, f"POST alias must redirect, got {status}"
    _, q = _authorize(headers)
    assert q.get("provider") == ["google"], q


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
