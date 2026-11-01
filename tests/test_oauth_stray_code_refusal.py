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
(the user sees a product page and nothing happened). Supabase's redirect-URLs
doc states half the mechanism verbatim — *"The Site URL in URL Configuration
defines the default redirect URL when no `redirectTo` is specified in the code"*
— and the other half ("or the `redirectTo` was not ACCEPTED and the Site URL was
substituted for it") is this project's own measurement, not a quotation: a VALID
code arriving on the Site URL is GoTrue's allow-list-rejection signature (issue
#10088, 2026-10-26 re-measurement comment). The fallback appends the PKCE
`?code=` to that Site URL, which is the URL above.

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

This is RFC 9700 §4.4.2.2's rule read from the receiving side, quoted
verbatim: *"Clients MUST check that the authorization response was received from
the correct issuer by comparing the distinct redirection URI for the issuer to
the URI where the authorization response was received on. If there is a
mismatch, the client MUST abort the flow."* Aborting means a **named error page
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
import re
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
    csp: res.headers.get('Content-Security-Policy'),
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
    # The CSP is the property that makes the refusal safe to SERVE at all: the
    # page arrives carrying a credential, so it must make no outbound request —
    # including the analytics/beacon origins the marketing document's
    # `RELAXED_CSP` deliberately allows. Asserted on the RESPONSE, not on the
    # source: a source scan that only pins which policy NAMES exist in the file
    # cannot see the two stamps being swapped, and measured, swapping them left
    # this whole suite green while a page carrying an authorization code shipped
    # with the analytics policy (PR #10514 review, finding 2).
    assert (res["csp"] or "").startswith("default-src 'none'"), (
        f"{where}: the refusal was served with CSP {res['csp']!r}, want the "
        "no-resource policy `default-src 'none'…` (`STRAY_RETURN_CSP`) — any other "
        "value lets the page beacon the URL it was handed"
    )
    assert "http" not in (res["csp"] or "") and "*" not in (res["csp"] or ""), (
        f"{where}: the refusal's CSP names a remote origin or a wildcard "
        f"({res['csp']!r}) — this page must fetch nothing at all"
    )


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


@pytest.mark.parametrize(
    "host,path",
    [
        # Every renderable path class, on both marketing hosts. `/docs`, `/faq`
        # and `/signin` are TORTOISE_ONLY and `/blog` is the blog prefix rule — on
        # the company host all four are reached THROUGH a query-preserving 301, so
        # pinning only the tortoise host is what let PR #10514 review finding 1
        # stand for three rounds.
        (TORTOISE_HOST, "/docs"),
        (TORTOISE_HOST, "/product"),
        (TORTOISE_HOST, "/blog"),
        (TORTOISE_HOST, "/signin"),
        (COMPANY_HOST, "/docs"),
        (COMPANY_HOST, "/blog"),
        (COMPANY_HOST, "/signin"),
        (COMPANY_HOST, "/faq"),
    ],
)
def test_the_refusal_is_not_limited_to_the_root_path(host: str, path: str) -> None:
    """This project serves NO OAuth callback on ANY path, on EITHER host.

    The callback moved to the app origin (#4054), which is why the `/auth/*`
    surface 302s there. So a code arriving at `/docs`, `/product`, `/blog` or any
    other page of this project is equally un-redeemable, and a guard keyed to the
    root path only would leave the same silent dead end on every other page the
    Site URL could have been set to.

    ⛔ PARAMETRIZED BY HOST, and that is the point (PR #10514 review, finding 1):
    on `premiselabs.co` these paths returned the host-consolidation **301**
    BEFORE the guard ran, carrying the code in its `Location`
    (`premiselabs.co/docs?code=…` → `301 → tortoise.premiselabs.co/docs?code=…`),
    and a tortoise-host-only parametrization could not see it. The 301s belong to
    the SEO consolidation decision and their query-preserving shape is pinned by
    `tests/test_admin_origin_redirect.py`; what changed is that the guard now runs
    AHEAD of them, so no `Location` this project constructs carries a credential.
    """
    (res,) = _run([_case(f"{path}?code={STRAY_CODE}", host=host)])
    _assert_named_refusal(res, f"{host}{path}?code=…")
    # Restated here because it is the finding: a 301 out of the consolidation
    # block is exactly the forwarding this guard exists to prevent.
    assert res["location"] is None, (
        f"{host}{path}?code=… was forwarded to {res['location']!r} instead of refused "
        "— the company-host consolidation 301 preserves `url.search`, so a guard "
        "placed below it puts the code in a second URL (and CDN log) for nothing"
    )


def test_a_company_host_product_404_still_precedes_the_guard() -> None:
    """The one path where a stray code is answered before any guard — recorded, not fixed.

    `premiselabs.co/product` (+`.html`) is 404'd at the very top of the middleware
    because that page exists only on the tortoise host; that branch precedes the
    refusal. It forwards nothing and renders nothing, so the credential neither
    leaks nor is silently burned by a marketing page — but it is the one shape
    where the answer is a bare 404 rather than the named refusal, and the issue's
    residual list records it. Pinned so a future reordering is a visible change
    rather than a silent one.
    """
    (res,) = _run([_case(f"/product?code={STRAY_CODE}", host=COMPANY_HOST)])
    assert res["status"] == 404, (
        f"premiselabs.co/product?code=… answered {res['status']}, want 404 — either "
        "the host-only 404 branch moved (record the new shape) or the page started "
        "rendering on the company host"
    )
    assert res["location"] is None, res
    assert STRAY_CODE not in res["body"], "the 404 echoed the authorization code"


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
    for where, res in zip(paths, _run([_case(p) for p in paths]), strict=True):
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


@pytest.mark.parametrize("host", [TORTOISE_HOST, COMPANY_HOST])
def test_the_moved_auth_surface_still_redirects_and_is_NOT_refused(host: str) -> None:
    """A deliberate exemption: `/auth/callback` keeps its 302 to the app origin.

    The legacy `tortoise.premiselabs.co/auth/callback` allow-list entry is
    recorded in `docs/ops/auth-email-flows.md` §3 and its removal is the OPEN
    audit #8680 — a live decision this lane does not own. Pinned behaviour from
    #4054/#4346 (`tests/test_admin_origin_redirect.py`) is therefore preserved
    exactly: the code is forwarded to the origin that owns the flow cookie,
    which is where the exchange can actually happen.

    Both hosts, because the refusal was hoisted ABOVE the branches that return
    these 30x (PR #10514 review, finding 1) — an exemption hole would turn this
    pinned redirect into a 400 on either of them.
    """
    (res,) = _run([_case(f"/auth/callback?code={STRAY_CODE}", host=host)])
    assert res["status"] == 302, (
        f"/auth/callback answered {res['status']} — the moved auth surface must keep "
        "its 302 to the app origin (a refusal here would break the staged #8680 audit)"
    )
    assert res["location"] == f"{APP_ORIGIN}/auth/callback?code={STRAY_CODE}", res


@pytest.mark.parametrize(
    "host,path,status,app_path",
    [
        # The auth surface (#4054): a 301 to the app origin, query preserved.
        # `/auth.html` is the raw-asset spelling the 301 normalizes onto `/auth`.
        (TORTOISE_HOST, "/auth", 301, "/auth"),
        (COMPANY_HOST, "/auth", 301, "/auth"),
        (TORTOISE_HOST, "/auth.html", 301, "/auth"),
        # The operator console (#4171/#4409): 302, and `/admin/blog` collapses
        # onto `/admin` while keeping the query.
        (TORTOISE_HOST, "/admin", 302, "/admin"),
        (COMPANY_HOST, "/admin", 302, "/admin"),
        (TORTOISE_HOST, "/admin/blog", 302, "/admin"),
        (COMPANY_HOST, "/admin/blog", 302, "/admin"),
    ],
)
def test_the_pinned_redirect_surfaces_are_exempt_and_keep_their_status_and_query(
        host: str, path: str, status: int, app_path: str) -> None:
    """`forwardsCredential` is the axle the whole guard turns on, and it was unpinned.

    Every shape it exempts (`APP_ONLY` — which is `/auth`, `/auth.html`,
    `/signup`, `/welcome`, `/invite-accept` — the `/auth/` prefix, and the three
    `/admin` spellings) had exactly ONE representative in this suite:
    `/auth/callback`, under `test_the_moved_auth_surface_still_redirects_and_is_NOT_refused`.

    PR #10514 review, finding 1 (P2) — MEASURED, not argued: a mutant that keeps
    only the `/auth/` prefix and drops `APP_ONLY.has(path)` and all three `/admin`
    alternatives refuses `/auth?code=…`, `/admin?code=…` and a company-host
    `/signup?code=…` with a **400**, and **reds nothing** in this suite. Those are
    live behaviours, not hypotheticals: a refusal on `/admin` breaks the operator
    console, on `/signup` breaks the signup funnel, and on `/auth` turns a stale
    bookmark or a pre-cutover email link into the dead end #4328's rationale
    (`_middleware.ts:360-365`) exists to avoid. `tests/test_admin_origin_redirect.py`
    drives the same paths but never with `?code=`, so it cannot see an over-block here.

    So these assert the PRE-EXISTING answer (the pinned 30x, query preserved),
    never the refusal — the exemption exists to preserve those decisions
    (#4054/#4346, #4171/#4409), which is why a code on them is forwarded.
    """
    (res,) = _run([_case(f"{path}?code={STRAY_CODE}", host=host)])
    assert res["status"] == status, (
        f"{host}{path}?code=… answered {res['status']}, want {status} — the exemption in "
        "`forwardsCredential` must preserve the pinned redirect for this shape; a 400 "
        f"here is an over-block on a real visitor (body {res['body'][:200]!r})"
    )
    assert NAMED_REFUSAL not in res["body"], (
        f"{host}{path}?code=… was answered with the stray-return refusal — the guard "
        "over-blocked a surface whose redirect is a pinned decision"
    )
    assert res["location"] == f"{APP_ORIGIN}{app_path}?code={STRAY_CODE}", (
        f"{host}{path}?code=… was sent to {res['location']!r}, want "
        f"{APP_ORIGIN}{app_path}?code=… — the query must survive the hop (invite "
        "`?token=`, recovery `?type=recovery` and the #1224 `?error=` all rely on it)"
    )


@pytest.mark.parametrize("path", ["/signup", "/welcome", "/invite-accept"])
def test_an_app_only_page_forwards_on_the_company_host_and_the_BACKSTOP_refuses_elsewhere(
        path: str) -> None:
    """The two halves of the second guard, neither of which the suite could see.

    (a) `APP_ONLY` is one of the four shapes `forwardsCredential` exempts, so a
    company-host `/signup?code=…` keeps its #4054 301 to `app.premiselabs.co`
    instead of being refused — the signup funnel. (b) On a host the
    consolidation block does not handle, the same path is exempt from the
    PRIMARY guard and reaches the BACKSTOP at `_middleware.ts:433-444`, which
    refuses it. PR #10514 review, finding 1: deleting the whole 789-character
    backstop block changed **0** of the suite's 17 cases, so the branch the
    comment credits with order-independence was deletable machinery.

    (b) is what pins it: without the backstop the request falls through to
    `context.next()` and this 400 becomes the 200 marketing render.
    """
    company, tortoise = _run([
        _case(f"{path}?code={STRAY_CODE}", host=COMPANY_HOST),
        _case(f"{path}?code={STRAY_CODE}", host=TORTOISE_HOST),
    ])
    assert company["status"] == 301, (
        f"{COMPANY_HOST}{path}?code=… answered {company['status']}, want 301 — the "
        "`APP_ONLY` arm of the exemption must keep the company-host hand-off to the "
        "app origin (a refusal here breaks the signup funnel)"
    )
    assert company["location"] == f"{APP_ORIGIN}{path}?code={STRAY_CODE}", company
    _assert_named_refusal(tortoise, f"{TORTOISE_HOST}{path}?code=…")


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
    # The refusal policy must not leak onto the ordinary path either: an
    # over-broad stamp would serve every marketing page from a `default-src
    # 'none'` policy and break the site (the reason `RELAXED_CSP` exists). This
    # is the counterpart of the swap mutation in `_assert_named_refusal`.
    assert "default-src 'none'" not in (res["csp"] or ""), (
        f"{path} was served the REFUSAL's `default-src 'none'` policy "
        f"({res['csp']!r}) — the stray-return stamp has leaked onto an ordinary "
        "marketing request"
    )


# ── The docstring's `_middleware.ts` citations must resolve ─────────────────────
# This PR's artifacts have needed four citation corrections across review rounds,
# all found by a reviewer reading two files side by side — which is the one thing
# CI does not do, and an uncited claim reads as settled. `website_architecture.md`
# anchored on `:432` (a blank line) and now names `:442`; `config/ci-surfaces.yml`
# named two `ci_selection.py` ranges that hold unrelated code; and the two ranges
# corrected above pointed at a neighbouring comment rather than the one the
# sentence describes. These guards are the cheap substitute for that reading: a
# citation must land on real, non-blank source, and the one the prose quotes *as*
# a construct must land on that construct — so a re-numbered branch reds here
# instead of sending the next reader to an unrelated comment. The routing record
# (`website_architecture.md`) had drifted the same way with nothing to red on, so
# it is scanned too.

_MIDDLEWARE_CITATION = re.compile(r"_middleware\.ts:(\d+)(?:-(\d+))?")

#: The routing record a lane reads for host routing — `_middleware.ts`'s own header
#: sends a reader there, and so does the doc. It is in the SAME drift class as the
#: citations above and was **unpinned**, which is how it stayed wrong: the
#: `/auth`/`/auth.html` unconditional 301 was cited as `:59-62`, which at this head
#: is the stray-code header block, so a lane auditing the OPEN #8680 allow-list
#: entry followed the pointer onto a comment about the `/auth/*` 302, learned
#: nothing about the 301, and — because a resolving pointer reads as a checked one
#: — recorded the claim as verified when it was not.
ARCH_DOC = REPO_ROOT / "website" / "website_architecture.md"


def _paragraph_of(path: Path, needle: str) -> str:
    """The blank-line-delimited paragraph of `path` that contains `needle`."""
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n[ \t]*\n", text):
        if needle in block:
            return block
    raise AssertionError(
        f"no paragraph of {path.name} contains {needle!r} — the claim these "
        "citations support is gone, so the guard over it has no subject"
    )


def _doc_paragraph(needle: str) -> str:
    """The blank-line-delimited paragraph of THIS module that contains `needle`."""
    return _paragraph_of(Path(__file__), needle)


def _cited_middleware_text(paragraph: str) -> str:
    """The `_middleware.ts` source cited by `paragraph`, for the ranges it names."""
    lines = MIDDLEWARE.read_text(encoding="utf-8").splitlines()
    spans = []
    for match in _MIDDLEWARE_CITATION.finditer(paragraph):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        assert 1 <= start <= end <= len(lines), (
            f"{match.group(0)} is out of bounds — {MIDDLEWARE.name} has {len(lines)} lines"
        )
        spans.append("\n".join(lines[start - 1:end]))
    assert spans, f"the paragraph cites no `_middleware.ts` line range:\n{paragraph}"
    return "\n".join(spans)


def test_the_backstop_citation_names_the_backstop_check() -> None:
    """The sentence's BACKSTOP citation must point at the backstop, not near it.

    The citation shipped naming a range that ended before the block it described,
    so a reader following it landed on the `/admin` 302 branch above — a branch
    that *returns*, i.e. the opposite of the property the sentence asserts.
    """
    cited = _cited_middleware_text(_doc_paragraph("BACKSTOP at"))
    assert "isStrayAuthorizationCode(" in cited, (
        "the docstring cites `_middleware.ts` for the BACKSTOP but the cited lines do "
        f"not contain the backstop check; they are:\n{cited}"
    )


def test_the_4328_citation_names_the_4328_rationale() -> None:
    """`#4328's rationale` must resolve to the comment that actually records it.

    #4328 is the "NOT a 404, deliberately" decision. Citing the BFF-move comment
    above it instead is how a reader concludes the dead-end argument was made
    about a branch it was never made about.
    """
    cited = _cited_middleware_text(_doc_paragraph("#4328's rationale"))
    assert "#4328" in cited, (
        "the docstring cites `_middleware.ts` as #4328's rationale but the cited lines "
        f"do not mention #4328; they are:\n{cited}"
    )


def test_the_routing_record_cites_the_auth_301_branch_it_names() -> None:
    """`website_architecture.md` must point AT the unconditional `/auth` 301.

    The routing record says `/auth` and `/auth.html` "have their own
    **unconditional** 301 below, so for those two the branch is not what
    preserves the redirect on `tortoise.*`/previews" — the sentence is read by
    the OPEN #8680 audit, which has to decide whether that 301 really is
    unconditional. It cited `:59-62`, the stray-code header block, so the audit
    landed on prose about the `/auth/*` 302: the pointer resolved, nothing about
    the 301 was there, and a resolving pointer reads as a checked one.
    """
    doc = ARCH_DOC.read_text(encoding="utf-8").splitlines()
    claim = next(
        (
            i
            for i, line in enumerate(doc)
            if "unconditional** 301 below" in line
        ),
        None,
    )
    assert claim is not None, (
        "the routing record no longer carries the `/auth`-301 claim this citation "
        f"supports — {ARCH_DOC.name} has been reworded, so the guard has no subject"
    )
    # The claim wraps across lines; take it and its continuation lines up to the
    # next bullet or blank line, so the citation that ANSWERS the claim is the one
    # joined to it (the neighbouring bullet cites the backstop at `:442`).
    window = [doc[claim]]
    for line in doc[claim + 1:]:
        if not line.strip() or line.lstrip().startswith("-") or line.lstrip().startswith("_"):
            break
        window.append(line)
    cited = _cited_middleware_text("\n".join(window))
    assert 'url.pathname === "/auth"' in cited, (
        "the routing record cites `_middleware.ts` for the `/auth`/`/auth.html` "
        "unconditional 301, but the cited lines do not contain that branch; they are:\n"
        f"{cited}"
    )
    assert "status: 301" in cited, (
        "the cited lines name the `/auth` branch but not the 301 it is invoked to "
        f"establish; they are:\n{cited}"
    )


def test_no_middleware_citation_in_the_routing_record_lands_off_its_construct() -> None:
    """Every `_middleware.ts` citation in the routing record resolves and is non-blank.

    The sibling guard below pins THIS file; the routing record needed its own,
    because it is the artifact that sends a reader to `_middleware.ts` in the
    first place and its citations had drifted twice (`:432`, a blank line, and
    the `:59-62` above) with nothing to red on.
    """
    lines = MIDDLEWARE.read_text(encoding="utf-8").splitlines()
    citations = list(_MIDDLEWARE_CITATION.finditer(ARCH_DOC.read_text(encoding="utf-8")))
    assert len(citations) >= 3, (
        f"{ARCH_DOC.name} cites `_middleware.ts` by line in at least three places "
        "(the backstop, twice, and the `/auth` 301) — found "
        f"{len(citations)}; the citations were deleted rather than corrected, which "
        "loses the pointer instead of fixing it"
    )
    for match in citations:
        start = int(match.group(1))
        end = int(match.group(2) or start)
        assert 1 <= start <= end <= len(lines), (
            f"{match.group(0)} (in {ARCH_DOC.name}) is out of bounds — "
            f"{MIDDLEWARE.name} has {len(lines)} lines"
        )
        for n in (start, end):
            assert lines[n - 1].strip(), (
                f"{match.group(0)} (in {ARCH_DOC.name}) anchors on line {n} of "
                f"{MIDDLEWARE.name}, which is blank — a blank anchor sends the reader "
                "nowhere and reads as a checked pointer"
            )


def test_no_middleware_citation_in_this_file_lands_on_a_blank_line() -> None:
    """Every citation resolves, and none anchors on a blank line (the `:432` shape)."""
    text = Path(__file__).read_text(encoding="utf-8")
    lines = MIDDLEWARE.read_text(encoding="utf-8").splitlines()
    citations = list(_MIDDLEWARE_CITATION.finditer(text))
    assert len(citations) >= 2, (
        "this file no longer cites `_middleware.ts` by line — the citations were deleted "
        "rather than corrected, which loses the pointer instead of fixing it"
    )
    for match in citations:
        start = int(match.group(1))
        end = int(match.group(2) or start)
        assert 1 <= start <= end <= len(lines), (
            f"{match.group(0)} is out of bounds — {MIDDLEWARE.name} has {len(lines)} lines"
        )
        for n in (start, end):
            assert lines[n - 1].strip(), (
                f"{match.group(0)} anchors on line {n} of {MIDDLEWARE.name}, which is blank "
                "— a blank anchor sends the reader nowhere and reads as a checked pointer"
            )
