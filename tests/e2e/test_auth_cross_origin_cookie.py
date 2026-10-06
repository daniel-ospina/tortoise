"""
Proof for the cross-origin session-cookie constraint (W6 blocker).

Question: can the browser's `__Host-session` cookie authenticate the dashboard's
calls to a sibling host (`api.…` from `app.…`)?

`__Host-` requires: Secure, Path=/, and NO Domain attribute — so the cookie is
HOST-ONLY. The property under test is that a host-only cookie is NOT sent to a
sibling host, and that this holds INDEPENDENT of SameSite.

What was wrong before (#3946) — stated precisely, because the old CONCLUSION was
right and "the test was vacuous" would be the wrong lesson:

  - The fixture did not match the subject the test named. It paired `localhost`
    with `127.0.0.1` (cross-SITE) while asserting a HOST rule, and nothing pinned
    the sibling as same-site — so the case the property is stated about was never
    tested, and a fixture change could silently change the subject while the test
    kept on passing. `docs/auth-architecture.md` §2.1 still states the host-only
    rule ("with **no `Domain` attribute** — the `__Host-` prefix enforces
    host-only"); what issue #3946 records as withdrawn is the *justification
    sentence in this file* ("the cookie scope rule, which is host-based"), which
    the project no longer stands behind as written. (§1.3 is the OPPOSITE rule —
    "The cross-subdomain pattern", the parent-domain session this one rejects.)
  - The observation was not made by a browser. It ran through Playwright's
    `ctx.request`, which reads the browser's cookie JAR but makes the ATTACHMENT
    decision itself: it filters on secure/domain/path ONLY (`Cookie.matches` /
    `filterCookies`, playwright 1.62.0) and is therefore SameSite-BLIND. On a
    SameSite-blind channel a host-only cookie is withheld by host mismatch on ANY
    pair — so the old verdict was right — but "the cookie was withheld" was never
    a *browser*-verified statement, and this is a browser property.

  (Consequence worth recording: the pair's cross-site-ness was INERT on BOTH
  channels, because a host-only cookie is withheld by HOST matching before
  SameSite is ever consulted — so the old test exercised host matching, not a
  "cross-site rule", and the defect is the unasserted premise plus the
  un-browser-verified claim rather than a false green. Note that the SameSite-blind
  instrument is NOT the state the issue was written against: #3946 was filed
  against a renderer-side `fetch` (SameSite-sensitive), and `ctx.request` replaced
  it on 2026-09-22 — so the blindness belongs to the INTERMEDIATE state this
  change supersedes, not to the version the issue described.)

The pair is therefore `app.x.localhost` / `api.x.localhost`: both share the
registrable domain `x.localhost` (same-site), and the test ASSERTS that premise
by observation rather than assuming it.

Trap, measured in Chromium and recorded so it is not rediscovered the hard way:
ONE-level `*.localhost` siblings (`app.localhost` → `api.localhost`) are
CROSS-SITE, so that naive swap would reproduce the very conflation this issue
reports. Two-level `*.x.localhost` IS same-site, `*.localhost` is a trustworthy
origin (so `Secure` cookies are accepted and sent over http), and a `__Host-`
cookie is accepted on it.

Instrument: ONE observation, made by the browser itself, on a page-initiated
top-level navigation to the sibling host. A browser-issued request is subject to
SameSite and carries `Sec-Fetch-*`, so the same-site premise AND the cookie
result are read off the same real request.

Three things are asserted, rather than argued, and each answers a different
question:
  - PREMISE — the sibling navigation is `same-site`. A missing `Sec-Fetch-Site`
    is reported as a MISSING MEASUREMENT, not as a same-site failure, and the
    sign-in flow is asserted to have left the page on the app origin, so the
    premise really is a function of this fixture's hostnames.
  - NON-VACUITY — the sibling request is not cookie-empty: the `Domain`-scoped
    `Secure` control is delivered across hosts to it. (This also shows the
    browser honours `Secure` on a `*.localhost` http origin. A product-side
    `Secure` regression on `__Host-session` is caught EARLIER, by the sign-in
    assertion, because Chromium refuses to store a non-`Secure` `__Host-` — the
    control does not claim that.)
  - THE PROPERTY, BOTH DIRECTIONS — the sibling receives no cookie other than the
    control, and a SIBLING→ISSUING navigation still authenticates. The negative
    half is the W6 blocker; the positive half is what §2.1's single
    session-bearing origin depends on, because the product really does make that
    hop — `website/functions/_middleware.ts` 301s the bare `/auth` and 302s the
    `/auth/*` subtree from `tortoise.…` (a same-site sibling) to `app.…`, and
    `website/_redirects` 301s the BFF pages — so a host-only cookie that failed
    to survive it would loop the user back to login. It is read from PRODUCT code
    rather than a synthetic echo: `GET /api/session` answers 200 only when it can
    read `__Host-session`.

This is asserted by OBSERVATION here, not by argument: the mock echoes the exact
Cookie header a browser request carries to the sibling host.

The sibling host stands in for api.premiselabs.co: the stand-in must differ by
HOSTNAME, not merely by port (cookies have no port component, RFC 6265 §8.5) —
and, per above, must be same-site.

Scope: BOTH directions of the property are asserted here, because the issue names
both. The negative direction (the session credential does not cross to a
same-site sibling) is the W6 blocker. The positive direction (a same-site sibling
reaching the ISSUING host does authenticate) is what `docs/auth-architecture.md`
§2.1's one-session-bearing-origin design depends on — its 301/302 hops from
`tortoise.…` to `app.…` are sibling→issuing navigations — and it is asserted
against product code rather than a synthetic echo.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.e2e.auth.bff_test_helpers import ensure_dashboard_dist

REPO_ROOT = Path(__file__).resolve().parents[2]
# #4054: the BFF moved to the `tortoise-dashboard` project. These suites
# must boot THAT Pages project — serving website/ would answer /auth/*
# with the SPA fallback and no session would ever be minted.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"

APP_PORT = int(os.environ.get("AUTH_CX_APP_PORT", "8993"))
API_PORT = int(os.environ.get("AUTH_CX_API_PORT", "9101"))
# The registrable domain the sibling pair shares — ONE source, so the hosts and
# the positive control cannot drift apart (a drift would leave the premise guard
# passing while the control silently stopped covering the pair).
SITE = "x.localhost"
# The sibling MUST differ by HOSTNAME: cookies have no port component (RFC 6265
# §8.5), so differing by port alone would share the jar and produce a false "the
# cookie WAS sent". It MUST also be same-site — the property is stated about a
# SAME-SITE sibling, so that premise is asserted in the test from the
# `Sec-Fetch-Site` the mock echoes, rather than assumed (#3946).
APP = f"http://app.{SITE}:{APP_PORT}"
API = f"http://api.{SITE}:{API_PORT}"
# The BFF's OWN Supabase calls are made by workerd through the system resolver,
# and `*.localhost` name resolution has no precedent elsewhere in this repo —
# so the server-side binding keeps the loopback literal and never asks a
# resolver for a `*.localhost` name. The sibling host above is used ONLY by the
# browser, which resolves `*.localhost` itself (RFC 6761) and needs no OS
# resolver entry.
API_LOOPBACK = f"http://127.0.0.1:{API_PORT}"

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTH_CLICKTHROUGH") != "1",
    reason="opt-in: set AUTH_CLICKTHROUGH=1",
)


def _stop(proc) -> None:
    """Terminate a process group and REAP it.

    Without the wait, a subsequent module's readiness probe can succeed against
    this dying server and adopt it — green, testing stale code.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        return
    try:
        proc.wait(timeout=15)
    except Exception:
        # Escalate to SIGKILL; the process may already be gone, which is fine.
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        # ...and REAP it: without this second wait the contract above ("stops and
        # REAPS") is false on exactly the path that needs it most.
        with contextlib.suppress(Exception):
            proc.wait(timeout=15)


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _logfile():
    """Capture a child's output to a TEMP FILE, and say why not a pipe.

    A `subprocess.PIPE` with no concurrent reader deadlocks the child as soon as
    it writes past the ~64 KiB pipe buffer, and `wrangler pages dev` is chatty
    enough to do it. Discarding the output entirely (`DEVNULL`, what this fixture
    used to do) is the opposite failure: a boot failure then reports only "failed
    to start", while the actual cause — a taken port, a bad binding, workerd
    unavailable — was written to a stream nobody kept.
    """
    return tempfile.TemporaryFile(mode="w+b")


def _tail(logfile, limit: int = 2000) -> str:
    """The last `limit` bytes a child wrote, for a failure message."""
    with contextlib.suppress(Exception):
        logfile.flush()
        logfile.seek(0)
        return logfile.read()[-limit:].decode("utf-8", "replace")
    return "<child output unavailable>"


@pytest.fixture(scope="module")
def two_origins():
    for binary in ("node", "wrangler"):
        if not shutil.which(binary):
            if os.environ.get("AUTH_ALLOW_NO_TOOLCHAIN") == "1":
                pytest.skip(f"{binary} unavailable (AUTH_ALLOW_NO_TOOLCHAIN=1)")
            pytest.fail(
                f"{binary} not available — this suite is the ONLY place the browser-level\n"
                "session properties are asserted; it must not silently skip. Install it or\n"
                "set AUTH_ALLOW_NO_TOOLCHAIN=1 to opt out explicitly."
            )

    # `wrangler pages dev dist` needs the built root (vite copies public/ into
    # dist/); building here keeps the suite self-sufficient rather than depending
    # on `tests/e2e/auth/` having run first in the same job.
    ensure_dashboard_dist()

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(API_PORT)
    # The app's Supabase calls also go to the "api" origin here.
    mock_log = _logfile()
    api = subprocess.Popen(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env,
        stdout=mock_log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    if not _wait(API_PORT):
        # Stop what we spawned BEFORE failing: a setup failure skips the
        # post-`yield` teardown entirely, so without this the mock outlives the run
        # and can later satisfy another lane's readiness probe (#5049).
        _stop(api)
        pytest.fail(
            f"mock api failed to start on 127.0.0.1:{API_PORT}; "
            f"output:\n{_tail(mock_log)}"
        )

    app_log = _logfile()
    app = subprocess.Popen(
        [
            shutil.which("wrangler"), "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={API_LOOPBACK}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
        # Topology as configuration. Without this, /welcome redirects a
        # signed-in visitor to the real app origin and the test client follows
        # that redirect off-box (403). Binding it locally also exercises the
        # config-not-literal change from SCOPE.md 6.
        "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=app_log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    if not _wait(APP_PORT):
        # `_stop`, not a bare killpg: it REAPS (wait, then escalate to SIGKILL),
        # and a leaked unreaped child is what makes the next run's readiness probe
        # succeed against a corpse (#5049).
        _stop(app)
        _stop(api)
        pytest.fail(
            f"wrangler pages dev failed to start on 127.0.0.1:{APP_PORT}; "
            f"output:\n{_tail(app_log)}"
        )
    time.sleep(2.5)

    # Nothing to hand back: this fixture's job is to boot and tear down the two
    # origins, and the test reads the module constants for their URLs.
    yield

    for p in (app, api):
        _stop(p)
    for log in (app_log, mock_log):
        with contextlib.suppress(Exception):
            log.close()


def test_host_only_session_cookie_is_not_sent_to_a_sibling_host(two_origins, request):
    """The W6 blocker, demonstrated.

    If this FAILS (cookie IS sent), the BFF session can authenticate the
    dashboard's API calls directly and no proxy is needed. A failure here is
    therefore informative in either direction — which is the point of testing it
    rather than reasoning about it.
    """
    import playwright.sync_api as playwright_sync  # hard dep: see the note above
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()

        # Sign in through the real flow so a genuine __Host-session exists.
        signin = page.goto(f"{APP}/auth/start", wait_until="load", timeout=45_000)
        cookies = {c["name"]: c for c in ctx.cookies()}
        assert "__Host-session" in cookies, (
            f"setup failed: no `__Host-session` after sign-in; url={page.url} "
            f"sign-in status={getattr(signin, 'status', None)} "
            f"cookies={[(n, c.get('domain'), c.get('secure')) for n, c in cookies.items()]} "
            f"— read the status before concluding anything about cookie scope: a "
            f"5xx from the auth flow, or a mock that is not the one this run "
            f"booted, sets no cookie at all. A cookie-scope regression is one "
            f"cause among several: a `__Host-` emitted with a Domain attribute, or "
            f"without Secure, is REJECTED by the browser and never reaches the jar"
        )
        # The premise guard reads the site relationship of a navigation STARTED
        # from this page, so pin the source origin too: were the flow to leave the
        # browser elsewhere, `Sec-Fetch-Site` would describe the wrong hop and the
        # guard would misreport it as a non-same-site fixture. Compare parsed
        # ORIGINS, not a string prefix — `startswith` also accepts
        # `http://app.x.localhost:89932/` and `http://app.x.localhost:8993@evil.test/`.
        assert urlparse(page.url)[:2] == urlparse(APP)[:2], (
            f"the sign-in flow left the app origin ({page.url!r} is not {APP!r}) "
            f"— the premise guard below reads the site relationship of a "
            f"navigation STARTED from this page, so it would describe the wrong hop"
        )

        # Ask a DIFFERENT host what Cookie header it receives — in the BROWSER,
        # on ONE page-initiated top-level navigation to the sibling host, so a
        # single real request answers both questions: the Cookie header the
        # browser actually attached, and the `Sec-Fetch-Site` of that request.
        #
        # NOT `page.evaluate(fetch(...))`: the app's responses carry a
        # Content-Security-Policy (#3525) whose `connect-src` cannot list a
        # throwaway sibling host, so a renderer-side FETCH to the sibling host is
        # refused before dispatch and the test would fail for a reason that has
        # nothing to do with cookies. Observed:
        #
        #   Connecting to 'http://api.x.localhost:9101/__mock/echo-cookie'
        #   violates the following Content Security Policy directive:
        #   "connect-src 'self' …" (ABBREVIATED — the emitted directive carries
        #   the full source list). The action has been blocked.
        #   -> TypeError: Failed to fetch
        #
        # A top-level NAVIGATION is not governed by `connect-src`, and it is a
        # real browser request — so it carries `Sec-Fetch-*` and is subject to
        # SameSite, which is what lets the same-site guard below be asserted
        # rather than argued. (The previous version observed through Playwright's
        # `ctx.request` instead, which is SameSite-blind — see the module
        # docstring.)
        #
        # Positive control FIRST: prove the channel actually carries cookies
        # ACROSS hosts to the sibling, so the "not in sent" assertion below cannot
        # pass vacuously. The control is scoped to the SHARED registrable domain
        # (`.x.localhost`), so it is delivered to BOTH `app.x.localhost` and
        # `api.x.localhost` — a cookie scoped to the sibling's own host would
        # prove only that the port is reachable, not that the cross-host jar is
        # consulted.
        ctx.add_cookies([{"name": "domain_probe", "value": "1",
                          "domain": f".{SITE}", "path": "/", "secure": True}])

        # Script-triggered, NECESSARILY: `page.goto` here would measure the wrong
        # thing — a browser-level navigation reports `Sec-Fetch-Site: none`
        # (measured), not the sibling relationship this premise is about — so the
        # window in which a navigation can commit before the `evaluate` reply is
        # read is unavoidable on THIS leg, the one that guards the fixture's
        # subject. (The positive leg carries the same window; see its note.)
        # `expect_navigation` still fails the leg when no navigation happens at all,
        # so the window is recorded rather than papered over with a broad
        # `suppress` that would hide a real failure.
        with page.expect_navigation(timeout=30_000) as nav:
            page.evaluate("(u) => { location.href = u; }",
                          f"{API}/__mock/echo-cookie")
        response = nav.value
        # Read the RESPONSE, not the rendered document: `inner_text("body")`
        # would parse a 404/renamed-route body just fine and then report itself as
        # "the pair is not same-site", which is the wrong diagnosis for a suite
        # whose whole point is trustworthy evidence.
        assert response is not None and response.status == 200, (
            f"expected a 200 echo from {API}/__mock/echo-cookie, got status "
            f"{getattr(response, 'status', None)} at {page.url}"
        )
        echoed = json.loads(response.text())
        sent = echoed.get("cookie") or ""

        # Record the observation for JUnit consumers. pytest prints user
        # properties only into a JUnit XML report, not under the plain `-v`
        # invocation this suite is run with — and a failure already quotes `sent`
        # in its own message, so this is for the CI report, not the diagnosis.
        request.node.user_properties.append(("cookie_sent_to_sibling", sent))

        # Guard 1 — the premise, asserted: this is a SAME-SITE sibling pair.
        # The property is stated about a same-site sibling, so a pair that is
        # merely a different host is not the thing under test. Without this a
        # swap to a cross-site pair (e.g. `app.localhost`/`api.localhost` —
        # measured CROSS-site, so the naive swap is not same-site at all) would
        # change the subject of the test while it kept on passing. It is a
        # function of THIS fixture's hostnames (the source origin is asserted
        # above), so it cannot be failed by a product change; its job is to stop a
        # fixture edit from silently changing the subject.
        #
        # Split from the equality below on purpose: an ABSENT header is a MISSING
        # MEASUREMENT (a mock predating #3946, or a proxy stripping Sec-Fetch-*)
        # and must not be reported as a same-site failure — that would accuse the
        # fixture of the instrument's fault.
        assert echoed.get("secFetchSite") is not None, (
            f"the echo carried no Sec-Fetch-Site ({echoed!r}) — the same-site "
            f"premise cannot be read, so the property below is proven neither "
            f"way. Suspect the instrument (a mock that must echo `secFetchSite`, "
            f"or a proxy stripping Sec-Fetch-*), not the pair"
        )
        assert echoed.get("secFetchSite") == "same-site", (
            f"the sibling pair is NOT same-site (Sec-Fetch-Site="
            f"{echoed.get('secFetchSite')!r}) — this test asserts the HOST rule "
            f"for a same-site sibling, which is the premise #3946 names"
        )

        # Guard 2 — non-vacuity, asserted: the sibling request is not
        # cookie-empty. The control is `Domain`-scoped and `Secure`, so it also
        # shows the browser honours `Secure` on this `*.localhost` http origin.
        # (A product-side `Secure` regression on `__Host-session` is caught
        # earlier, by the sign-in assertion, because Chromium refuses to store a
        # non-`Secure` `__Host-` cookie — Guard 2 does not claim that.)
        assert "domain_probe=1" in sent, (
            f"control failed: the sibling request carried no cross-host cookie "
            f"({sent!r}) — the host-only assertion below would be vacuous"
        )

        # THE PROPERTY — negative direction, asserted on the SET rather than on
        # the single name `__Host-session`. Guard 2 has just PROVED that this
        # channel carries a cookie scoped to this pair's registrable domain to the
        # sibling, so any FURTHER credential scoped the same way arrives here too,
        # and only the control may come through.
        #
        # What this does NOT catch, said plainly so the coverage is not over-read:
        #   - a credential scoped to the PRODUCTION registrable domain
        #     (`Domain=.premiselabs.co` — the pattern docs/auth-architecture.md
        #     §2.1 records for the legacy cohort) is dropped by the browser BEFORE
        #     it enters the jar on a `*.localhost` host, so it cannot be observed
        #     from this fixture at all — measured, not assumed;
        #   - a regression in `__Host-session`'s OWN attributes is caught earlier,
        #     by the sign-in assertion, and never reaches this line.
        received = {c.split("=", 1)[0].strip() for c in sent.split(";") if c.strip()}
        assert received <= {"domain_probe"}, (
            f"the sibling request carried cookie(s) beyond the control "
            f"({sorted(received - {'domain_probe'})!r}; full header {sent!r}). If "
            f"that is reproducible, the BFF session can authenticate "
            f"cross-ORIGIN calls to a sibling host and the W6 proxy is "
            f"unnecessary."
        )

        # THE PROPERTY — positive direction: the half the issue also names, and
        # the one §2.1's single session-bearing origin depends on. Navigating
        # from the SIBLING host to the ISSUING host must still authenticate.
        # Read from PRODUCT code rather than a synthetic echo: `/api/session`
        # answers 200 only when it can read `__Host-session` from the request and
        # find a live session, so a 200 is what proves the cookie took effect across
        # THIS hop. A 401 proves only that the hop did NOT authenticate — the
        # endpoint also 401s a cookie it read and found dead, so a 401 does not by
        # itself prove the cookie was withheld. The assertion therefore branches its
        # DIAGNOSIS on the status, and leans on this fixture's own premise (a
        # session created seconds earlier, never revoked) to name attachment as the
        # cause to check first.
        #
        # The page is ALREADY on the sibling host — that is where the observation
        # above navigated it — so this leg navigates from there, rather than
        # reloading the echo URL first (a second load whose only possible outcome
        # was an unrelated failure with no assertion message).
        #
        # Script-triggered for the same reason as the observation above (this leg
        # models a hop INITIATED from the sibling document), with the same window.
        with page.expect_navigation(timeout=30_000) as back_nav:
            page.evaluate("(u) => { location.href = u; }", f"{APP}/api/session")
        back = back_nav.value
        assert back is not None, "the sibling→issuing navigation produced no response"
        # A 200 ALONE IS NOT THE PROPERTY. The dev server answers 200 with a SPA
        # document at `/` on ANY host it is bound to, and a redirect-to-login also
        # ends in a 200 — measured: pointing this leg at the app root ON THE
        # LOOPBACK HOST (`http://127.0.0.1:<APP_PORT>/`, the host the host-only
        # cookie does NOT cover) passed on an unchanged tree. So first assert the
        # response IS the endpoint, then branch on its status, then require the
        # endpoint's own body: only then does 200 mean the cookie was read and a
        # live row found.
        assert back.url == f"{APP}/api/session", (
            f"the sibling→issuing navigation did not land on the endpoint: it ended "
            f"at {back.url!r}, not {APP}/api/session — a redirect to a login page (or "
            f"any other route) can answer 200 while the session cookie was withheld, "
            f"so a bare 200 is not this property"
        )
        status = back.status
        # Branch the DIAGNOSIS on the status. The endpoint is built for exactly
        # this: `/api/session` reads the cookie FIRST and answers 401 when it is
        # absent, so 503 is reachable only by a request that DID carry the session
        # cookie — and calling that "the cookie did not survive" would send the
        # reader into the cookie scope for a session-STORE fault.
        assert status == 200, (
            {
                401: (
                    f"the sibling→issuing hop did NOT authenticate: "
                    f"{APP}/api/session answered 401, so the session did not take "
                    f"effect across the hop. A 200 requires the endpoint to have "
                    f"read `__Host-session`, and this fixture created that session "
                    f"seconds earlier without revoking it, so the cause to check "
                    f"first is attachment — a host-only `SameSite=Lax` cookie must "
                    f"be sent on a top-level GET navigation to its issuing host. "
                    f"(The endpoint also 401s a cookie it DID read and found dead — "
                    f"unknown/revoked handle, expiry, dead refresh token — so with "
                    f"suspect timing or a suspect store this leg proves only that "
                    f"the hop was NOT authenticated, not which side failed; the "
                    f"sibling echo cannot settle it either, because by the negative "
                    f"property above it never carries `__Host-session`.)"
                ),
                503: (
                    f"the browser DID attach the session cookie: "
                    f"{APP}/api/session answered 503, and this endpoint reads the "
                    f"cookie BEFORE it touches the store, so the fault is the D1 "
                    f"binding, the schema, or `getSession` — not cookie attachment"
                ),
            }.get(
                status,
                f"unexpected status {status} from {APP}/api/session: only a 200 "
                f"proves the session cookie crossed the hop — 401 means the hop "
                f"was not AUTHENTICATED (usually the cookie was withheld, though a "
                f"cookie read and found dead also 401s), 503 means the cookie WAS "
                f"attached and the store failed, and any other status (a runtime "
                f"error, a missing route) proves neither",
            )
        )
        # Parse defensively: a 200 at this URL whose body is not JSON must report
        # THAT, not a JSONDecodeError raised while preparing the assertion.
        try:
            body = back.json()
        except Exception:
            body = None
        assert isinstance(body, dict) and (body.get("user") or {}).get("id"), (
            f"{APP}/api/session answered 200 but its body is not the session "
            f"payload ({body!r}) — a 200 from THIS endpoint means it read "
            f"`__Host-session` and found a live row, so the body must carry the "
            f"user it resolved"
        )

        browser.close()
