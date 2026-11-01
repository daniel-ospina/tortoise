"""#10088 ask 3 — an authorization response that lands on a MARKETING page must refuse.

WHY THIS EXISTS
---------------
#10088 is the OAuth authorize page's sign-in. Ask 3 of the issue body is the one
request that had **no branch at all** (the verify pass on the issue, recorded
2026-10-31: *"(3) … | **no branch** | grep → no hits; no wrong-origin refusal
exists"*):

> **Return the code to nothing but `/oauth/authorize`**: if the provider falls
> back, the page should refuse to continue and say so ("this sign-in did not
> return to the connector — retry from ChatGPT") rather than hand the user a
> marketing page with a stray `code=` in the URL.

The failure it names was measured live (issue body, D2): GoTrue did not honour
`redirect_to`, substituted the project **Site URL**, and delivered a live
authorization code to a marketing page —

    https://tortoise.premiselabs.co/?code=50605f24-15a1-40f0-8e86-f3722cccce13#

— which renders normally, ignores the parameter, and silently discards the code
(the user sees a product page and nothing happened). Google's docs and this
repo's own record both describe the mechanism: *"If omitted—or not accepted—the
Site URL serves as the default redirect destination"* (Supabase
redirect-urls); the fallback appends the PKCE `?code=` to that Site URL.

WHAT THE FIX IS, AND WHAT IT IS NOT
-----------------------------------
The **return target** half of ask 3 is already delivered and pinned, and this
file does not re-do it: the sign-in leg's provider `redirect_to` is
server-owned, never read from the query, and is `{APP}/auth/callback`
(`website/apps/dashboard/functions/auth/start.ts`; pinned by
`tests/e2e/auth/test_bff_start_provider.py::test_start_ignores_a_client_supplied_redirect_to`
and its `redirect_to == APP/auth/callback` assertion). No provider round trip
is addressed at `api.premiselabs.co` any more — #3524 §9 step 4 removed the
inline `signInWithOAuth`, and `docs/ops/auth-email-flows.md` §3 records the api
origin allow-list entry as a NON-requirement.

What is *missing* is the receiving end. A misdirected response still has to
arrive somewhere, and the somewhere is this Pages project (it serves both
marketing hosts, `tortoise.premiselabs.co` = the Site URL). So the refusal
belongs in `website/functions/_middleware.ts` — the one file every request to
that project passes through — and it must fire **before** the page renders.

This is RFC 9700's rule read from the receiving side: *"clients should verify
that the authorization response was received at the expected redirect URI and
abort the flow when there is a mismatch"*. Aborting means a **named error page
with no credential in it** — not a normal render, not a redirect that forwards
the code somewhere else, and not an echo of the code (a code in a URL leaks
through browser history, `Referer`, CDN logs and analytics; `Referrer-Policy:
no-referrer` is stamped for that reason).

WHY IT IS TESTED BY EXECUTION, NOT BY STRING MATCH
--------------------------------------------------
The middleware is imported and its REAL exported `onRequest` is driven under
Node's type-stripping loader, as `tests/test_admin_origin_redirect.py` does for
the same file. A branch that never runs, or one that returns the refusal text
*inside* a 200 marketing page, passes a grep and fails the user — so the
assertions are on the status, the headers, and the body of the real response.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._verdict import NODE_FLOOR_STRIP_TYPES, require_node_floor

REPO_ROOT = Path(__file__).resolve().parent.parent
MIDDLEWARE = REPO_ROOT / "website" / "functions" / "_middleware.ts"
APP_ORIGIN = "https://app.premiselabs.co"
TORTOISE_HOST = "tortoise.premiselabs.co"
COMPANY_HOST = "premiselabs.co"

#: The sentence the issue asks for, verbatim ("refuse to continue and say so
#: ('this sign-in did not return to the connector — retry from ChatGPT')").
#: Matched as a substring of the rendered body so the page may still wrap it in
#: markup and add its own support line.
NAMED_REFUSAL = "did not return to the connector"

# A distinctive value so a leak is detectable: if the refusal page echoes or
# interpolates the query, this string shows up in the body.
STRAY_CODE = "50605f24-15a1-40f0-8e86-f3722cccce13"

# Drives the REAL exported onRequest. `context.next()` and `ASSETS.fetch` are
# stubs: on the refusal path neither is called, and on the passthrough cases
# they are the observable (a `x-mw: next` header) rather than the filesystem.
_DRIVER = """
const mod = await import(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
const out = [];
for (const c of cases) {
  const req = new Request(c.url, { method: c.method || 'GET', headers: { host: c.host } });
  const ctx = {
    request: req,
    env: { ASSETS: { fetch: async () => new Response('asset', { status: 404 }) } },
    next: async () => new Response('passthrough', { status: 200, headers: { 'x-mw': 'next' } }),
  };
  const res = await mod.onRequest(ctx);
  const body = await res.text();
  out.push({
    status: res.status,
    location: res.headers.get('Location'),
    next: res.headers.get('x-mw'),
    hsts: res.headers.get('Strict-Transport-Security'),
    cacheControl: res.headers.get('Cache-Control'),
    referrerPolicy: res.headers.get('Referrer-Policy'),
    contentType: res.headers.get('Content-Type'),
    body,
  });
}
console.log(JSON.stringify(out));
"""


def _run(cases: list[dict]) -> list[dict]:
    # #4916: a present-but-too-old Node SKIPS rather than REDs. The driver passes
    # `--experimental-strip-types`, so the recorded flag floor (22.7) applies.
    require_node_floor(
        NODE_FLOOR_STRIP_TYPES,
        what="the stray authorization-response refusal driver",
    )
    node = shutil.which("node")
    with tempfile.TemporaryDirectory() as td:
        driver = Path(td) / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [node, "--experimental-strip-types", str(driver), str(MIDDLEWARE),
             json.dumps(cases)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _case(path: str, host: str = TORTOISE_HOST, method: str = "GET") -> dict:
    return {"url": f"https://{host}{path}", "host": host, "method": method}


def _assert_named_refusal(res: dict, where: str) -> None:
    """The response must be a REFUSAL, not a rendered marketing page."""
    assert res["status"] == 400, (
        f"{where}: answered {res['status']}, want 400 — a 200 here IS the defect "
        "(the user is handed the marketing page and the code is silently burned)"
    )
    assert NAMED_REFUSAL in res["body"], (
        f"{where}: the refusal does not say what happened; body was {res['body'][:400]!r}. "
        f"It must name it: {NAMED_REFUSAL!r}"
    )
    assert "text/html" in (res["contentType"] or ""), (
        f"{where}: a refusal a human cannot read (content-type "
        f"{res['contentType']!r}) is a dead end, which is the state the ask replaces"
    )
    # Never forward the credential: this middleware serves no callback, so a
    # Location here would only move the stray code to yet another origin.
    assert res["location"] is None, (
        f"{where}: the stray response was REDIRECTED to {res['location']!r} instead "
        "of refused — forwarding a code the AS never issued for that origin is the "
        "misdirection this guard exists to stop"
    )
    assert res["next"] is None, (
        f"{where}: the request fell through to the page (x-mw=next) — the refusal "
        "must precede the render"
    )
    assert res["hsts"], f"{where}: the constructed response lost the HSTS stamp (#1003)"


@pytest.mark.parametrize("host", [TORTOISE_HOST, COMPANY_HOST])
def test_an_authorization_code_at_the_site_root_is_refused_with_a_named_error(
        host: str) -> None:
    """The measured failure, replayed: the Site-URL fallback appends `?code=`.

    `tortoise.premiselabs.co` IS the project's Site URL (the landing page the
    code was observed on); `premiselabs.co` is the same Pages project and equally
    unable to redeem a code. Neither may render.
    """
    (res,) = _run([_case(f"/?code={STRAY_CODE}", host=host)])
    _assert_named_refusal(res, f"{host}/?code=…")
    assert STRAY_CODE not in res["body"], (
        "the refusal echoed the authorization code into the page body — a code in "
        "a URL leaks via history/Referer/CDN logs, and reflecting it hands it to "
        "every script and extension on the page"
    )
    assert res["cacheControl"] == "no-store", (
        f"the refusal was cacheable ({res['cacheControl']!r}) — a cached refusal on "
        "`/` would be served to the next ordinary visitor"
    )
    assert res["referrerPolicy"] == "no-referrer", (
        "a page that arrived carrying a credential must not send a Referer onward "
        f"(got {res['referrerPolicy']!r})"
    )


def test_the_refusal_is_not_limited_to_the_root_path() -> None:
    """This project serves NO OAuth callback on ANY path.

    The callback moved to the app origin (#4054), which is why the `/auth/*`
    surface 302s there. So a code arriving at `/docs`, `/product`, `/blog` or any
    other page of this project is equally un-redeemable, and a guard keyed to the
    root path only would leave the same silent dead end on every other page the
    Site URL could have been set to.
    """
    cases = [_case(f"/docs?code={STRAY_CODE}"), _case(f"/product?code={STRAY_CODE}")]
    for where, res in zip(["/docs", "/product"], _run(cases)):
        _assert_named_refusal(res, f"{where}?code=…")


def test_the_provider_ERROR_shape_keeps_the_1224_banner_reachable() -> None:
    """The OTHER return shape — GoTrue's `error` — is NOT refused, deliberately.

    `website/product.html:349-355` records the behaviour this preserves:

        <!-- OAuth error banner (#1224): Supabase redirects here with
             ?error=...&error_code=bad_oauth_state when the OAuth state TTL
             expired mid-login — surface it instead of failing silently. -->

    The handler at `website/product.html:510-535` reads `error` / `error_code` /
    `error_description` off this exact URL and shows the retry path. That shape
    carries NO credential, so there is nothing for a refusal to protect — but
    refusing it DOES retire the banner, and answers an expired plain-dashboard
    login with a page whose instruction is "start again from your MCP client
    (ChatGPT: Settings → Connectors)". Ask 3 is about a stray `code`; the error
    shape already does what the ask asks ("surface it instead of failing
    silently"). So this pins it as reachable: a re-added error-shape arm reds
    here, and the reviewer's finding (PR #10514) is `DONE` rather than re-made.
    """
    # Both documented spellings: the #1224 comment's `error_code=bad_oauth_state`
    # (state TTL expired) and a provider-side failure carrying
    # `error_description`. Either is a legitimate arrival on this page.
    paths = [
        "/?error=access_denied&error_code=bad_oauth_state",
        "/?error=server_error&error_description=Unable+to+exchange+external+code",
    ]
    for where, res in zip(paths, _run([_case(p) for p in paths])):
        assert res["status"] == 200, (
            f"{where}: answered {res['status']}, want 200 — the error shape is not the "
            "authorization-response shape (it carries no code) and `product.html`'s "
            "#1224 banner is the recorded handler for it; refusing it here leaves the "
            "documented trigger of that banner unreachable"
        )
        assert NAMED_REFUSAL not in res["body"], (
            f"{where}: rendered the stray-return refusal — the #1224 banner's documented "
            "trigger (?error=…&error_code=bad_oauth_state) must keep reaching the page"
        )


def test_the_moved_auth_surface_still_redirects_and_is_NOT_refused() -> None:
    """A deliberate exemption: `/auth/callback` keeps its 302 to the app origin.

    The legacy `tortoise.premiselabs.co/auth/callback` allow-list entry is
    recorded in `docs/ops/auth-email-flows.md` §3 and its removal is the OPEN
    audit #8680 — a live decision this lane does not own. Pinned behaviour from
    #4054/#4346 (`tests/test_admin_origin_redirect.py`) is therefore preserved
    exactly: the code is forwarded to the origin that owns the flow cookie,
    which is where the exchange can actually happen.
    """
    (res,) = _run([_case(f"/auth/callback?code={STRAY_CODE}")])
    assert res["status"] == 302, (
        f"/auth/callback answered {res['status']} — the moved auth surface must keep "
        "its 302 to the app origin (a refusal here would break the staged #8680 audit)"
    )
    assert res["location"] == f"{APP_ORIGIN}/auth/callback?code={STRAY_CODE}", res


@pytest.mark.parametrize(
    "path",
    [
        # `code_challenge` is the PKCE verifier's hash, NOT an authorization code —
        # a substring match on "code" would refuse a perfectly ordinary request.
        "/?code_challenge=abc123&code_challenge_method=S256",
        # A bare `error` param is not the OAuth response shape (GoTrue always sends
        # `error_description`/`error_code` with it) and a marketing link may carry
        # one; refusing it would be an over-block, which fails closed on the user.
        "/?error=newsletter_failed",
        # The ordinary case must be untouched.
        "/?utm_source=hn&utm_campaign=launch",
    ],
)
def test_ordinary_marketing_requests_are_not_refused(path: str) -> None:
    # A pass-through can take either shape in this middleware: `context.next()`
    # (the `x-mw: next` stub) for a non-root path, or the ASSETS rewrite that
    # serves product.html at `/` on the tortoise host. Both mean "not refused",
    # which is the property under test — asserting the mechanism would pin the
    # router's shape instead of the guard's scope.
    (res,) = _run([_case(path)])
    assert res["status"] == 200, (
        f"{path} answered {res['status']} — the guard over-blocked an ordinary "
        f"marketing request (body {res['body'][:200]!r})"
    )
    assert NAMED_REFUSAL not in res["body"], (
        f"{path} was answered with the stray-return refusal — `code_challenge` is a "
        "PKCE verifier hash and a bare `error` is not the authorization-response "
        "shape, so this is an over-block that fails closed on a real visitor"
    )
