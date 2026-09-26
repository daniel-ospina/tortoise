#!/usr/bin/env python
"""Audit the DEPLOYED Pages surfaces for CSP violations (#3525).

WHY THIS SCRIPT EXISTS
----------------------
Cloudflare's edge injects a Web Analytics beacon
(``https://static.cloudflareinsights.com/beacon.min.js/<version>``) into HTML
responses **whose request carries ``Accept: text/html``** — not a User-Agent
test. Neither a plain ``curl`` nor a local ``wrangler pages dev`` preview ever
sees the tag, so no local check can tell whether a policy admits it. The first
CSP shipped for #3525 omitted the beacon origin, blocked it in production, and
was caught only by the post-merge ``verify-legal`` job — 8 console-error
failures. This script is the direct, pre-merge/at-any-time detector for that
class: it drives the real surfaces in a real browser and reports every CSP
violation, page error and failed request.

QUICK HEADER-LEVEL PROBE (no browser needed)::

    curl -sH 'Accept: text/html' https://premiselabs.co/ | grep -c cloudflareinsights

USAGE (read-only GETs; refuses https unless ALLOW_PROD=1, matching the sibling
suites in this directory)::

    ALLOW_PROD=1 .venv/bin/python tests/e2e/audit_prod_csp.py
    ALLOW_PROD=1 .venv/bin/python tests/e2e/audit_prod_csp.py https://app.premiselabs.co/

Exits 0 when no surface reported a CSP violation, 1 otherwise. Non-CSP console/
page errors and failed requests are printed separately (they never count toward
the violation total) so a completeness claim about "no OTHER failed request" is
readable off the output instead of asserted from a blind instrument.

NOTE: `/admin` (the `ADMIN_CSP` surface) requires an authenticated session, so it
is not in the default URL set; it is covered statically by the `securityHeaders`
guard, which pins its policy.
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

DEFAULT_URLS = [
    "https://premiselabs.co/",
    "https://premiselabs.co/docs/",
    "https://tortoise.premiselabs.co/",
    # A Function-rendered HTML surface (`blog/[[path]].ts` → `_lib.ts`), where
    # `_headers` does NOT apply and the policy is the marketing `RELAXED_CSP`.
    "https://tortoise.premiselabs.co/blog",
    "https://app.premiselabs.co/",
    "https://app.premiselabs.co/signup",
    "https://app.premiselabs.co/auth",
    "https://app.premiselabs.co/team",
]


def _violations(console: list[str], failed: list[str]) -> list[str]:
    """CSP refusals, from either the console or a failed request.

    Matched on the CSP phrases, NOT a bare ``Refused to`` — Chrome's strict-MIME
    refusal (``Refused to apply style … MIME type``) is a different class and
    must not read as a CSP violation.
    """
    hits = [m for m in console if "Content Security Policy" in m or "violates the following" in m]
    return hits + [f"{f} (request failed)" for f in failed if "csp" in f.lower()]


def _other_errors(console: list[str], failed: list[str]) -> list[str]:
    """Non-CSP console/page errors and failed requests.

    Kept OUT of `_violations`: a network blip or an unrelated app error must not
    read as a CSP violation, and a completeness claim ("the beacon was the only
    failure") is only checkable if this class is visible rather than discarded.
    """
    csp = set(_violations(console, failed))
    return [m for m in console if m not in csp] + [f for f in failed if "csp" not in f.lower()]


def main(argv: list[str]) -> int:
    urls = argv[1:] or DEFAULT_URLS
    # `.lower()`: a scheme is case-insensitive (RFC 3986), and Chromium navigates
    # `HTTPS://…`, so a case-sensitive test would let an un-opted-in run reach
    # production through an upper-case URL.
    if any(u.lower().startswith("https://") for u in urls) and os.environ.get("ALLOW_PROD") != "1":
        print("refusing https targets without ALLOW_PROD=1 (read-only, but be explicit)")
        return 2

    dirty = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for url in urls:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            console: list[str] = []
            failed: list[str] = []
            # Default-arg binding: B023 — the handlers must capture THIS loop
            # iteration's lists, not the loop variable.
            page.on("console", lambda m, c=console: c.append(f"[{m.type}] {m.text}"))
            page.on("pageerror", lambda e, c=console: c.append(f"[pageerror] {e}"))
            page.on("requestfailed", lambda r, f=failed: f.append(f"{r.url} :: {r.failure}"))
            resp = None
            csp = ""
            kind = "?"
            goto_error: Exception | None = None
            try:
                resp = page.goto(url, wait_until="load", timeout=45_000)
                csp = (resp.headers.get("content-security-policy") or "") if resp is not None else ""
                # `connect-src 'self'` with no third-party host is the strict
                # policy; anything with posthog is the relaxed one.
                kind = "STRICT" if "connect-src 'self'" in csp and "posthog" not in csp else (
                    "RELAXED" if csp else "NONE"
                )
                # A bounded settle for post-`load` refusals — but the READ must
                # not happen here. The beacon sends its RUM payload on
                # `load`/`visibilitychange`, and the `visibilitychange` half fires
                # at page teardown; a snapshot taken before the page closes drops
                # every refusal emitted after the settle and prints OK on it. So:
                # settle, close the PAGE, THEN read (see `page.close()` below).
                page.wait_for_timeout(3_500)
            except Exception as exc:
                goto_error = exc
            # End the PAGE first — `page.close()`, NOT `ctx.close()`. The
            # beacon's `navigator.sendBeacon` RUM POST goes out on
            # `load`/`visibilitychange`, and only `page.close()` fires
            # `visibilitychange`/`pagehide`/`unload`; `ctx.close()` teardown fires
            # none of them (measured: a `connect-src` refusal emitted on
            # `visibilitychange` appears in the drained lists after `page.close()`
            # and NOT after `ctx.close()`). So the verdict must be read after the
            # PAGE ends — reading after only the context is closed still drops the
            # teardown class this script exists to detect.
            close_error: Exception | None = None
            try:
                page.close()
            except Exception as exc:  # a crashed page must not skip the verdict
                close_error = exc
            if goto_error is not None:
                blocked = [f"[goto-failed] {goto_error}"]
                others = []
            else:
                blocked = _violations(console, failed)
                if close_error is not None:
                    blocked.append(f"[close-failed] {close_error}")
                # A response with NO policy refuses nothing, so a total CSP loss
                # would otherwise read as `OK NONE` and exit 0. Absence is a
                # failure, not a label.
                if not csp:
                    blocked.append("[no-csp] response carried NO Content-Security-Policy")
                others = _other_errors(console, failed)
            # Context cleanup only, AFTER the verdict is computed, so a cleanup
            # failure can never suppress a surface's verdict.
            ctx.close()
            status = "OK" if not blocked else f"{len(blocked)} BLOCKED"
            print(f"{status:>10}  {kind:<8} {url}")
            for b in blocked:
                print(f"            - {b[:220]}")
            # Printed separately, never merged into the verdict.
            for o in others:
                print(f"            (non-CSP error) {o[:200]}")
            dirty += 1 if blocked else 0
        browser.close()

    print(f"\n{dirty} of {len(urls)} surface(s) reported a CSP violation")
    return 1 if dirty else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
