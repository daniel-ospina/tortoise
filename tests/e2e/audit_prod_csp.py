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

Exits 0 when no surface reported a CSP violation, 1 otherwise.
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

DEFAULT_URLS = [
    "https://premiselabs.co/",
    "https://premiselabs.co/docs/",
    "https://tortoise.premiselabs.co/",
    "https://app.premiselabs.co/",
    "https://app.premiselabs.co/signup",
    "https://app.premiselabs.co/auth",
    "https://app.premiselabs.co/team",
]


def _violations(console: list[str], failed: list[str]) -> list[str]:
    """CSP refusals, from either the console or a failed request."""
    hits = [m for m in console if "Content Security Policy" in m or "Refused to" in m]
    return hits + [f"{f} (request failed)" for f in failed if "csp" in f.lower()]


def main(argv: list[str]) -> int:
    urls = argv[1:] or DEFAULT_URLS
    if any(u.startswith("https://") for u in urls) and os.environ.get("ALLOW_PROD") != "1":
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
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                blocked = _violations(console, failed)
                if resp is not None:
                    csp = resp.headers.get("content-security-policy") or ""
                    # `connect-src 'self'` with no third-party host is the strict
                    # policy; anything with posthog is the relaxed one.
                    kind = "STRICT" if "connect-src 'self'" in csp and "posthog" not in csp else (
                        "RELAXED" if csp else "NONE"
                    )
                else:
                    kind = "?"
                page.wait_for_timeout(3_500)
            except Exception as exc:
                blocked = [f"[goto-failed] {exc}"]
                kind = "?"
            status = "OK" if not blocked else f"{len(blocked)} BLOCKED"
            print(f"{status:>10}  {kind:<8} {url}")
            for b in blocked:
                print(f"            - {b[:220]}")
            dirty += 1 if blocked else 0
            ctx.close()
        browser.close()

    print(f"\n{dirty} of {len(urls)} surface(s) reported a CSP violation")
    return 1 if dirty else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
