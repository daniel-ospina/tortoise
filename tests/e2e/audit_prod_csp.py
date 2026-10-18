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

Exits 0 when no surface reported a CSP violation AND no surface broke the
beacon contract (see BEACON HONESTY above), 1 otherwise, 2 when an https target
is probed without `ALLOW_PROD=1`. Non-CSP console/page errors and failed
requests are printed separately (they never count toward the violation total)
so a completeness claim about "no OTHER failed request" is readable off the
output instead of asserted from a blind instrument.

NOTE: `/admin` (the `ADMIN_CSP` surface) requires an authenticated session, so it
is not in the default URL set; it is covered statically by the `securityHeaders`
guard, which pins its policy.

BEACON HONESTY (#4706/#8334, the #4762 addendum)
------------------------------------------------
A surface with **no** CSP violation can still be broken in a way no policy text
shows. #8334 removed `https://cloudflareinsights.com` from `connect-src` on the
measured basis that under AUTOMATIC injection the beacon's RUM payload goes to
the page's OWN origin (`POST /cdn-cgi/rum`); the third-party host is the
MANUAL-embed default, which the beacon only uses when `data-cf-beacon.version`
is absent. That same-origin property is a property of the **injected tag**, not
of our source, so the policy guard cannot see it. Two ways it silently breaks:

* the zone is switched to "Enable with JS Snippet installation" (or the edge
  stops emitting `version`) → the beacon POSTs cross-origin and
  `connect-src 'self'` refuses it: analytics lost plus a console error; or
* the beacon LOADS and reports nowhere — a failure with no console message at
  all, and therefore invisible to the violation scan above.

So each surface also records its request URLs and its injected-tag presence, and
this script fails on (a) any request to the bare `cloudflareinsights.com` apex
host, or (b) a tag that loaded under a policy that ADMITS it, with no same-origin
`/cdn-cgi/rum` POST observed. `no-tag` (the edge injecting nothing at all) is
reported, never failed: Web Analytics is a zone setting and #4706's keep-or-drop
decision is open. A surface whose NAVIGATION failed is `not-measured` — the run
never saw that document, so a 5xx/timeout is neither a beacon verdict nor part of
the "carried NO injected beacon tag" count.

The host match is EXACT. A substring test also matches
`static.cloudflareinsights.com` — the beacon's own SCRIPT origin, which every
policy must allow — and would red a healthy surface.

The `tag-blocked` arm can only ever SUPPRESS a finding (the violation scan owns a
surface that was actually refused), so it requires an OBSERVED refusal naming the
beacon script rather than a policy-text parse; an unrecognised admit spelling
must never silence `rum-missing` on a beacon that loaded.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

# The edge-injected beacon's SCRIPT origin host (``script-src``) …
BEACON_SCRIPT_HOST = "static.cloudflareinsights.com"
# … the script's own path (the tag is ``…/beacon.min.js/<version>``), used only to
# decide whether a PATH-SCOPED policy source still reaches it …
BEACON_SCRIPT_PATH = "/beacon.min.js"
# … and the same-origin RUM path the beacon POSTs to under automatic injection.
BEACON_RUM_PATH = "/cdn-cgi/rum"
# A surface the run never saw (navigation failed). NOT a beacon verdict: counting
# it as `no-tag` would inflate the "carried NO injected beacon tag" signal.
NOT_MEASURED = "not-measured"
# The scheme the beacon script is served over, and the port it implies — the only
# values that let a scheme-relative / host-source spelling admit it.
BEACON_SCHEME = "https"
BEACON_PORT = "443"
# The MANUAL-embed RUM endpoint's host. Every policy omits it since #8334, so a
# request to it means the tag fell back to the cross-origin default.
MANUAL_RUM_HOST = "cloudflareinsights.com"

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


def _directives(csp: str) -> dict[str, list[str]]:
    """Parse a CSP into ``{directive: [source, …]}``.

    Deliberately minimal: this script only needs to answer "does the policy
    admit the beacon's script origin", and a half-parser that silently returns
    nothing would answer "no" for every policy — the direction that INVENTS a
    finding. An unparseable directive is therefore kept as a bare token list,
    which can only fail to match the host (the `tag-blocked` arm), never make
    one up.
    """
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            out.setdefault(tokens[0].lower(), tokens[1:])
    return out


def _path_part_admits_the_beacon(path: str) -> bool:
    """Does a CSP ``path-part`` reach the beacon's script path?

    CSP matches a path-part on segment boundaries and as a prefix: ``/a`` admits
    ``/a/b`` but not ``/ab``. ``/`` (or no path) admits everything. The beacon's
    exact version segment is not knowable here, so a policy that scopes DEEPER
    into the beacon directory is also accepted — the question is only whether the
    source can reach the script at all.
    """
    if not path or path == "/":
        return True
    probe = f"{BEACON_SCRIPT_PATH}/"
    return probe.startswith(f"{path.rstrip('/')}/") or path.startswith(probe)


def _host_source_admits_the_beacon(token: str) -> bool:
    """Does ONE CSP source expression admit ``https://static.cloudflareinsights.com/…``?

    The WHOLE ``host-source`` grammar (CSP3 §2.3.1), not a short literal list::

        [ scheme "://" ] host [ ":" port ] [ "/" path-part ]

    plus the ``*`` wildcard and the bare ``scheme:`` form (a scheme-source admits
    every URL of that scheme). A scheme-less token (``//host`` or ``host``)
    inherits the page's scheme, which is https here, so it admits the beacon.

    A short whitelist answered `False` for the other legitimate spellings —
    ``https://*.cloudflareinsights.com``, ``//static.cloudflareinsights.com``,
    ``https://static.cloudflareinsights.com:443``, a path-scoped source — and a
    `False` from this predicate was what turned the missing-RUM arm OFF on a
    beacon that had loaded. The grammar is the fix; keywords (``'self'``,
    ``'nonce-…'``, ``'strict-dynamic'``) and other origins stay non-admitting.
    """
    token = token.strip()
    if not token or token.startswith("'"):
        return False
    if token == "*":
        return True
    if token.endswith(":") and "://" not in token:
        # scheme-source: only https admits an https URL
        return token.lower() == f"{BEACON_SCHEME}:"
    rest = token
    if rest.startswith("//"):
        rest = rest[2:]
    elif "://" in rest:
        scheme, _, rest = rest.partition("://")
        if scheme.lower() != BEACON_SCHEME:
            return False
    host_port, sep, tail = rest.partition("/")
    host, _, port = host_port.partition(":")
    if port and port != "*" and port != BEACON_PORT:
        return False
    host = host.lower()
    if not host:
        return False
    if host == "*":
        return True
    if host.startswith("*."):
        # `*.example.com` admits sub-domains but NOT the bare domain itself.
        if host[2:] != BEACON_SCRIPT_HOST and not BEACON_SCRIPT_HOST.endswith(host[1:]):
            return False
    elif host != BEACON_SCRIPT_HOST:
        return False
    return _path_part_admits_the_beacon(f"/{tail}" if sep else "")


def _admits_beacon_script(csp: str) -> bool:
    """Does this policy let the edge-injected beacon SCRIPT load?

    Directive precedence is real CSP semantics, not a preference: an absent
    ``script-src-elem`` falls back to ``script-src``, which falls back to
    ``default-src``. Checking all three as a union would answer `True` for a
    policy whose `script-src-elem` explicitly EXCLUDES the host, which is the
    case the beacon report exists to catch.

    NOTE this answer never SUPPRESSES a finding on its own (see
    ``_beacon_refusal_observed``): the whole host-source grammar is accepted, and
    even an unrecognised spelling can only ADD a `rum-missing` finding, never
    remove one.
    """
    directives = _directives(csp)
    sources: list[str] | None = None
    for name in ("script-src-elem", "script-src", "default-src"):
        if name in directives:
            sources = directives[name]
            break
    if sources is None:
        return False
    return any(_host_source_admits_the_beacon(src) for src in sources)


def _beacon_refusal_observed(violations: list[str]) -> bool:
    """Was a refusal of the beacon SCRIPT actually OBSERVED on this surface?

    `tag-blocked` SUPPRESSES the `rum-missing` finding (the violation scan already
    fails a surface that was refused), so it may only be asserted on evidence — a
    recorded CSP violation naming the beacon script host. A policy-text parse
    alone is not evidence: a wildcard/`//host`/path-scoped admit reads `False` to
    any finite whitelist, and the beacon then loaded and reported nowhere while
    the run printed OK.
    """
    return any(BEACON_SCRIPT_HOST in message.lower() for message in violations)


def _beacon_verdict(
    requests: list[tuple[str, str]],
    csp: str,
    tag_present: bool,
    page_url: str,
    violations: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Classify the beacon contract for ONE surface.

    ``requests`` is ``[(method, url), …]`` as observed by the page (including
    the teardown half — the RUM POST goes out on `load`/`visibilitychange`).
    Returns ``(label, findings)``; a non-empty ``findings`` is a failure. Pure,
    so the contract is pinned against synthetic request lists instead of
    against production.

    ``page_url`` supplies the origin the RUM payload must stay ON: "same-origin"
    means the PAGE's origin, because `connect-src 'self'` admits exactly that —
    accepting any `/cdn-cgi/rum` POST would report `rum-ok` for a payload the
    browser refused to deliver. It must be the LANDED url, not the requested
    one: `https://premiselabs.co/docs/` 301s to `https://tortoise.premiselabs.co/docs`,
    so comparing against the requested host reports `rum-missing` on a healthy
    surface (measured: the only false positive this arm produced).

    ``violations`` is the surface's already-computed violation list (the same one
    the caller prints and counts). It is EVIDENCE, not a hint: a `rum-missing`
    finding is only suppressed as `tag-blocked` when a violation naming the beacon
    script was actually observed.

    Labels: ``no-tag`` (edge injected nothing — reported, not failed),
    ``tag-blocked`` (an OBSERVED refusal; the violation scan owns it),
    ``rum-missing``, ``rum-ok``. A cross-origin RUM request is not a label of its
    own: the label reports the same-origin contract, and the cross-origin request
    arrives as a ``beacon-cross-origin`` FINDING alongside it (so a surface that
    reports same-origin AND leaks one to the manual endpoint is still a failure).
    ``not-measured`` is not produced here at all — it belongs to a surface the run
    never loaded, which ``_classify_surface`` labels before reaching this function.
    """
    findings: list[str] = []
    violations = list(violations or [])
    rum_same_origin = False
    page_host = (urlsplit(page_url).hostname or "").lower()
    for method, url in requests:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        # HOST-EXACT: `static.cloudflareinsights.com` is the script origin we
        # REQUIRE, and an endswith/`in` test would match it here and flip every
        # healthy surface to this finding.
        if host == MANUAL_RUM_HOST:
            findings.append(
                f"[beacon-cross-origin] {method} {url} — the beacon used the "
                "MANUAL-embed RUM endpoint, which no policy admits since #8334"
            )
        elif (
            method.upper() == "POST"
            # PATH-EXACT, not a suffix test: the RUM endpoint is `/cdn-cgi/rum` on
            # the zone root, so `/x/cdn-cgi/rum` is a DIFFERENT endpoint and a
            # `endswith` would count it as the same-origin one.
            and parts.path.rstrip("/") == BEACON_RUM_PATH
            and host == page_host
        ):
            rum_same_origin = True

    if not tag_present:
        # Absence of the tag is a ZONE SETTING (#4706 keep-or-drop is open), not
        # a defect in this repo's policy. Reported so a silent switch-off is
        # visible; never a failure.
        return "no-tag", findings

    if rum_same_origin:
        # The payload was OBSERVED on the page's own origin, so nothing refused
        # the script — the beacon demonstrably ran. No arm can overrule that.
        return "rum-ok", findings

    if not _admits_beacon_script(csp) and _beacon_refusal_observed(violations):
        # BOTH halves are required. The script was refused (the violation scan
        # already fails this surface, so a second finding would double-count one
        # cause) AND the refusal was OBSERVED. A policy-text parse alone can only
        # ever add a FALSE "blocked": it can never discover a violation the scan
        # missed, so without the observation it must not suppress the finding.
        return "tag-blocked", findings

    findings.append(
        "[rum-missing] the beacon LOADED with no same-origin POST "
        f"{BEACON_RUM_PATH} observed — the RUM payload reported nowhere "
        "(analytics lost with no console error)"
        + (
            ""
            if _admits_beacon_script(csp)
            else "; the policy text does not appear to admit the beacon script, but no "
            "refusal was OBSERVED, so this is not reported as blocked"
        )
    )
    return "rum-missing", findings


def _other_errors(console: list[str], failed: list[str]) -> list[str]:
    """Non-CSP console/page errors and failed requests.

    Kept OUT of `_violations`: a network blip or an unrelated app error must not
    read as a CSP violation, and a completeness claim ("the beacon was the only
    failure") is only checkable if this class is visible rather than discarded.
    """
    csp = set(_violations(console, failed))
    return [m for m in console if m not in csp] + [f for f in failed if "csp" not in f.lower()]


def _classify_surface(
    *,
    goto_error: BaseException | None,
    close_error: BaseException | None,
    console: list[str],
    failed: list[str],
    csp: str,
    requests: list[tuple[str, str]],
    tag_present: bool,
    landed_url: str,
) -> tuple[list[str], list[str], str, list[str]]:
    """``(blocked, others, beacon_label, beacon_findings)`` for ONE surface.

    A surface whose NAVIGATION failed is `not-measured`: the run never saw that
    document, so it has no beacon verdict and MUST NOT be counted as "carried NO
    injected beacon tag" — a 5xx or a timeout would otherwise inflate the one
    signal that distinguishes a CHANGED injection format from a disabled zone.
    The surface is still failed (via ``[goto-failed]``); it is just not counted.

    Pure: no browser, no network, so the whole per-surface verdict is testable
    against synthetic evidence instead of production.
    """
    if goto_error is not None:
        return [f"[goto-failed] {goto_error}"], [], NOT_MEASURED, []
    blocked = _violations(console, failed)
    if close_error is not None:
        blocked.append(f"[close-failed] {close_error}")
    # A response with NO policy refuses nothing, so a total CSP loss would
    # otherwise read as `OK NONE` and exit 0. Absence is a failure, not a label.
    if not csp:
        blocked.append("[no-csp] response carried NO Content-Security-Policy")
    others = _other_errors(console, failed)
    # Read AFTER the page ended, for the same reason the violation lists are: the
    # RUM POST goes out on `visibilitychange`.
    beacon_label, beacon_findings = _beacon_verdict(
        requests, csp, tag_present, landed_url, blocked
    )
    return blocked, others, beacon_label, beacon_findings


def _summarize_labels(beacon_labels: list[str]) -> tuple[int, int]:
    """``(no_tag, not_measured)`` from the per-surface labels.

    Derived from the LABEL, not from a second counter incremented beside it, so a
    `not-measured` surface can never be counted as `no-tag`.
    """
    return beacon_labels.count("no-tag"), beacon_labels.count(NOT_MEASURED)


def main(argv: list[str]) -> int:
    urls = argv[1:] or DEFAULT_URLS
    # `.lower()`: a scheme is case-insensitive (RFC 3986), and Chromium navigates
    # `HTTPS://…`, so a case-sensitive test would let an un-opted-in run reach
    # production through an upper-case URL.
    if any(u.lower().startswith("https://") for u in urls) and os.environ.get("ALLOW_PROD") != "1":
        print("refusing https targets without ALLOW_PROD=1 (read-only, but be explicit)")
        return 2

    dirty = 0
    beacon_dirty = 0
    beacon_labels: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for url in urls:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            console: list[str] = []
            failed: list[str] = []
            requests: list[tuple[str, str]] = []
            # Default-arg binding: B023 — the handlers must capture THIS loop
            # iteration's lists, not the loop variable.
            page.on("console", lambda m, c=console: c.append(f"[{m.type}] {m.text}"))
            page.on("pageerror", lambda e, c=console: c.append(f"[pageerror] {e}"))
            page.on("requestfailed", lambda r, f=failed: f.append(f"{r.url} :: {r.failure}"))
            # The beacon contract's evidence: every request the page makes, and
            # whether the edge injected the tag at all.
            page.on("request", lambda r, q=requests: q.append((r.method, r.url)))
            resp = None
            csp = ""
            kind = "?"
            tag_present = False
            # The origin `connect-src 'self'` actually binds is the LANDED one:
            # `premiselabs.co/docs/` 301s to `tortoise.premiselabs.co/docs`.
            landed_url = url
            goto_error: Exception | None = None
            try:
                resp = page.goto(url, wait_until="load", timeout=45_000)
                landed_url = page.url or url
                csp = (resp.headers.get("content-security-policy") or "") if resp is not None else ""
                # `connect-src 'self'` with no third-party host is the strict
                # policy; anything with posthog is the relaxed one.
                kind = "STRICT" if "connect-src 'self'" in csp and "posthog" not in csp else (
                    "RELAXED" if csp else "NONE"
                )
                # Read the INJECTED DOCUMENT, not our source: the edge adds the
                # tag at response time, so this is the only way to tell "the zone
                # stopped injecting" from "the policy stopped admitting it" —
                # and a CSP-blocked script element is still in the DOM, so the
                # check survives the blocked case too.
                tag_present = bool(
                    page.evaluate(
                        "(host) => Array.from(document.querySelectorAll('script[src]'))"
                        ".some((s) => s.src.includes(host))",
                        BEACON_SCRIPT_HOST,
                    )
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
            blocked, others, beacon_label, beacon_findings = _classify_surface(
                goto_error=goto_error,
                close_error=close_error,
                console=console,
                failed=failed,
                csp=csp,
                requests=requests,
                tag_present=tag_present,
                landed_url=landed_url,
            )
            beacon_labels.append(beacon_label)
            # Context cleanup only, AFTER the verdict is computed, so a cleanup
            # failure can never suppress a surface's verdict.
            ctx.close()
            status = "OK" if not blocked else f"{len(blocked)} BLOCKED"
            print(f"{status:>10}  {kind:<8} {url}  {beacon_label}")
            for b in blocked:
                print(f"            - {b[:220]}")
            for f in beacon_findings:
                print(f"            - {f[:220]}")
            # Printed separately, never merged into the verdict.
            for o in others:
                print(f"            (non-CSP error) {o[:200]}")
            dirty += 1 if blocked else 0
            beacon_dirty += 1 if beacon_findings else 0
        browser.close()

    no_tag, not_measured = _summarize_labels(beacon_labels)
    print(f"\n{dirty} of {len(urls)} surface(s) reported a CSP violation")
    print(
        f"{beacon_dirty} of {len(urls)} surface(s) reported a beacon finding "
        f"(the edge-injected RUM must stay same-origin, {BEACON_RUM_PATH})"
    )
    # NOT a failure (#4706's keep-or-drop decision is open), but loud: a CHANGED
    # injection format and a deliberately disabled zone look identical here, and
    # the format change is one of the failure modes #4762 names. Silence would
    # make it indistinguishable from a healthy run.
    print(
        f"{no_tag} of {len(urls)} surface(s) carried NO injected beacon tag "
        "(zone setting, or the edge changed its injection format — #4706/#4762)"
    )
    # A surface the run never saw is NOT part of that count: a 5xx/timeout must not
    # read as "the edge stopped injecting", which is the one signal for a changed
    # injection format. Printed so the omission is visible rather than silent.
    print(
        f"{not_measured} of {len(urls)} surface(s) were NOT MEASURED "
        "(navigation failed — excluded from the beacon-tag count above)"
    )
    return 1 if (dirty or beacon_dirty) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
