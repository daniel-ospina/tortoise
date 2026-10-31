"""The dashboard/auth e2e preview preflight — a POLLED readiness budget (#10305).

The gate answers ONE question — *are the two local `wrangler pages dev`
previews serving?* — before any spec drives them, so a missing preview fails
fast with a single clear message instead of making every test misdiagnose as an
app-behavior regression (#2731).

⛔ It answers that question over a BUDGET, not with one sample. Measured failure
(#10305, CI run 41141786500 job 124361929728): the auth preview reported READY
on `:8836` at 23:21:53.99 (`preview (auth) ready after 4s`), the dashboard
preview booted next to it at 23:21:56.09, and this preflight's SINGLE probe at
23:21:59.36 caught a 500 — so the job reddened with *"local preview server(s)
unreachable"* about a server that had served a 200 five seconds earlier, and the
same tree was GREEN on the same suite four minutes before (run 41140572622, head
`b3bbf2f1c`, byte-identical `website/`, `tests/e2e/` and `.github/`). A 5xx from
a dev server that is still coming up is the exact state `preview-server.sh`'s
own `ready` verb is built to POLL through (240 iterations of `curl -fsS`); this
consumer was the only place in the chain that sampled once.

The comparable practice is the same shape: Playwright's `webServer`
(`docs/src/test-webserver-js.md`, `url` + `timeout` — "How long to wait for the
process to start up and be available in milliseconds") polls the URL until it
answers an expected status within a budget rather than sampling once and
declaring the server absent. This module is that budget for a harness that
boots its servers in CI steps rather than from the test runner.

It lives OUTSIDE `test_session_login_flow.py` for two reasons, both about being
checkable: that module is opt-in (`RUN_DASHBOARD_E2E=1`, module-level skip) and
imports playwright, so a unit test could only reach this logic by setting the
opt-in for the whole process (which would silently UN-skip the other dashboard
specs in the same run). Here it is stdlib-only and importable unconditionally.
"""
from __future__ import annotations

import ipaddress
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

import pytest

#: How long the preflight keeps polling before it declares the previews
#: unreachable. Sized like Playwright's `webServer.timeout` default (60 s) rather
#: than to the observed blip: long enough to ride out a preview that is still
#: warming up or momentarily rebuilding, short enough that a genuinely absent
#: server still fails the job in seconds rather than minutes. Override with
#: `DASHBOARD_E2E_PREFLIGHT_BUDGET` (seconds, float) — the harness's other knobs
#: are env-driven for the same reason (a lane debugging locally should not have
#: to edit the gate).
DEFAULT_BUDGET_SECONDS = 20.0

#: Pause between polls. Small on purpose: the budget is what bounds the wait, so
#: the interval only trades a little request load against reaction time.
POLL_INTERVAL_SECONDS = 0.5

#: How many distinct observations per server the refusal reports. A refusal that
#: names only its LAST sample cannot distinguish "never came up" (all 500s) from
#: "came up and then died" (200 then nothing) — which is precisely the reading
#: this gate got wrong in #10305.
MAX_TRAIL = 4


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """#2731 (review P2): never follow a redirect in the preflight — a local
    preview that 3xx-bounces off-box must not pass a redirect-following check."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def is_local_preview_host(host: str) -> bool:
    """True only for ``localhost`` or a **loopback** IP literal.

    Loopback-only on purpose (#2731 review P2): a guard whose job is to reject
    non-local origins must not accept a DNS name like ``10.evil.com`` (prefix
    match), a LAN host (``10.0.0.5``, ``192.168.1.1``), or the cloud-metadata
    address (``169.254.169.254``). ``urlparse().hostname`` strips the brackets
    off IPv6 literals, so ``::1`` arrives unbracketed here.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _budget_from_env(default: float = DEFAULT_BUDGET_SECONDS) -> float:
    """`DASHBOARD_E2E_PREFLIGHT_BUDGET`, or `default`.

    A malformed or negative value FALLS BACK rather than raising: this runs inside
    a module-scoped autouse fixture, where an exception would replace the one
    clear "previews unreachable" message with a traceback about an env var.
    """
    raw = os.environ.get("DASHBOARD_E2E_PREFLIGHT_BUDGET")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _probe(url: str, opener) -> str:
    """One observation of `url`: ``""`` when it is SERVING, else what was seen.

    Serving means HTTP 200 — not merely "the port accepted a connection" and not
    a 3xx: the specs behind this gate require 200, so a redirect-following or
    connect-only probe would hand them a server they immediately call broken
    (#10283's own class; the same distinction Kubernetes draws between a
    `tcpSocket` and an `httpGet` probe, and this file's sibling pin
    `tests/test_ci_preview_isolation.py::test_ready_requires_the_preview_to_SERVE_not_just_bind`).
    """
    try:
        with opener.open(url, timeout=10) as resp:
            status = getattr(resp, "status", None)
            if status == 200:
                return ""
            return f"HTTP {status}"
    except Exception as exc:  # any error means the preview is not serving
        return f"{type(exc).__name__}: {exc}"


def _static_problem(url: str) -> str:
    """``""`` when `url` is a local http(s) URL this gate may drive, else why not.

    These are request-INDEPENDENT: polling cannot fix a non-loopback host or an
    unparseable authority, so they refuse on the first evaluation instead of
    burning the budget.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
    except ValueError:
        # Malformed authority (e.g. an unbalanced IPv6 bracket) — this must
        # not escape as a per-test ValueError; it is a preflight failure.
        return "unparseable URL"
    if parsed.scheme not in ("http", "https"):
        return (f"unsupported scheme {parsed.scheme!r} "
                "(only http/https previews are probed)")
    if not is_local_preview_host(host):
        return (f"non-loopback host {host!r} — refusing to "
                "drive a non-local origin")
    return ""


def preflight_local_servers(
    targets: Iterable[tuple[str, str]],
    *,
    budget: float | None = None,
    interval: float = POLL_INTERVAL_SECONDS,
    opener=None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Refuse (``pytest.exit``) unless every ``(label, url)`` target is SERVING.

    ``targets`` is ``(label, url)`` pairs — the auth origin and the dashboard
    origin, in the caller's order. Each is polled until it answers 200 or the
    budget is spent; a target that is already serving costs exactly ONE request.

    ``pytest.exit`` (not ``pytest.fail``) is deliberate: a module-scoped autouse
    fixture that fails is re-raised once per collected test, so ``fail`` would
    print the same error 16 times, whereas ``exit`` aborts the session with a
    single message (#2731 review P2).

    The injected ``opener``/``sleep``/``monotonic`` exist so the poll can be
    exercised without a listener or a real clock — see
    ``tests/test_ci_preview_isolation.py``. Nothing about the verdict is
    injectable: the status set, the loopback rule and the refusal are fixed.
    """
    budget = _budget_from_env() if budget is None else budget
    opener = opener or urllib.request.build_opener(_NoRedirect())
    pending = list(targets)
    if not pending:
        return

    trails: dict[str, list[str]] = {label: [] for label, _ in pending}
    refused: list[str] = []
    deadline = monotonic() + budget

    def observe(observations) -> None:
        for label, problem in observations:
            trail = trails[label]
            if not trail or trail[-1] != problem:
                trail.append(problem)
                del trail[:-MAX_TRAIL]

    while True:
        next_pending: list[tuple[str, str]] = []
        observations = []
        for label, url in pending:
            static = _static_problem(url)
            if static:
                refused.append(f"{label} {url} -> {static}")
                continue
            problem = _probe(url, opener)
            observations.append((label, problem))
            if problem:
                next_pending.append((label, url))
        observe(observations)
        pending = next_pending
        if not pending:
            break
        if monotonic() >= deadline:
            break
        sleep(interval)

    if not pending and not refused:
        return

    def describe(label: str, url: str) -> str:
        trail = trails[label]
        if not trail:
            return f"{label} {url} -> no observation"
        if len(trail) == 1:
            return f"{label} {url} -> {trail[0]}"
        return (f"{label} {url} -> {trail[0]} (still not serving after "
                f"{budget:g}s; last: {trail[-1]}; {len(trail)} distinct "
                "observations)")

    unreachable = [describe(label, url) for label, url in pending]
    pytest.exit(
        "dashboard e2e: local preview server(s) unreachable — this suite "
        "drives the LOCAL wrangler previews, never production (#2731). "
        "Start BOTH before running (dist/ is a build artifact since "
        "#3775 — build it first or :8790 serves a missing/stale bundle):\n"
        "  cd website/apps/dashboard && npm ci && npm run build\n"
        "  cd website/apps/dashboard && npx wrangler@4 pages dev dist --port 8790\n"
        "  cd website && npx wrangler@4 pages dev . --port 8788\n"
        f"Polled for {budget:g}s before giving up (#10305: one sample is not "
        "enough — a preview that answered 200 a few seconds ago can 500 while "
        "it warms up):\n"
        "Unreachable:\n"
        + "\n".join(f"  - {line}" for line in unreachable + refused),
        returncode=1,
    )
