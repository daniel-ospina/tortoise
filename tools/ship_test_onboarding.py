#!/usr/bin/env python3
"""#3806 — ship-test instrument: the scripted clean-browser onboarding walk.

*Done = merged. Shipped = deployed and observed.* This is the smallest thing
that can be run per deploy and produce an **observation** rather than an
assertion. It drives a real browser through the real front door on the real
deployment, waits for the server to observe the agent's first write, and records
what each screen resolved to at each step.

Three assertions (the third is the one that matters):

1. **A clean browser can reach signup** — the signup CTA is hittable by a
   top-of-stack click (the #3781 overlay class is invisible to every local
   test).
2. **The walk completes** — signup → wizard → the final screen renders.
3. **"Connected" appears ONLY when the server observed it.** The negative
   direction is the point: before any server-observed connection, no surface
   may claim one. A walk that only checks the positive path passes on a lying
   UI.

Evidence standard: it executes the real browser path against the real
deployment and asserts the RESOLVED state a user would see. No ``--mock``, no
proxy that silently substitutes a different backend. The only permitted
substitution is a locally-served copy of the deployment's own client bundle,
used by ``tests/e2e/test_ship_test_onboarding.py`` for RED/GREEN mutation
evidence (the backend stays the real one).

Usage
-----
::

    # per-deploy observation against the deployed app
    python tools/ship_test_onboarding.py \
        --base-url https://app.premiselabs.co \
        --auth-url https://tortoise.premiselabs.co \
        --api-url  https://api.premiselabs.co \
        --allow-prod --out review-artifacts/ship-test

    # local/self-host target (loopback) needs no --allow-prod
    python tools/ship_test_onboarding.py --auth-url http://127.0.0.1:8788

    # guard self-check: prove the connection-claim probe discriminates
    # (RED on a lying UI, GREEN on a behaviour-identical reformat)
    python tools/ship_test_onboarding.py --mutation-selfcheck

Exit code: 0 when every assertion passed, 1 when any failed (or the walk could
not complete), 2 on a usage/refusal error. The observation directory always
holds ``observation.json`` + step screenshots, pass or fail.

The record carries the timestamp, the **deployed SHA** (the deployment's own
revision, read from the public ``GET /v1/version`` — ``commit_sha`` baked into
the release env at deploy time), the dashboard-side bundle asset as a second
anchor, the instrument's own git SHA, the resolved connection state of every
surface, and the three named assertion booleans.

Not a gate: this is instrument + evidence, deliberately not wired as a blocking
CI gate (the issue scopes it as "not a new gate"). The fast guard tests
(``tests/test_ship_test_onboarding.py``) and the browser guard tests
(``tests/e2e/test_ship_test_onboarding.py``) run in the normal lanes and are
what keep the probe itself honest.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ── the connection vocabulary ───────────────────────────────────────────────
# One vocabulary, shared by the probe, the tests, and the recorded observation.
# A screen is in exactly one of these states; anything unclassifiable is ABSENT
# (the surface did not render, which is its own violation when one was
# expected).
CONNECTED = "connected"
NOT_CONNECTED = "not_connected"
UNAVAILABLE = "unavailable"
ABSENT = "absent"

# Order matters: NOT_CONNECTED is tested first because "Not connected" contains
# "connected". Keep these as lowercased substrings.
_NOT_CONNECTED_MARKERS = (
    "no connection observed yet",
    "not connected",
    "no connection observed",
)
_UNAVAILABLE_MARKERS = (
    "unavailable",
    "couldn't load",
    "could not load",
    "status unavailable",
)
_CONNECTED_MARKERS = (
    "connected ✓",
    "✓ connected",
    "your agent is connected",
)


def classify_connection_text(text: str | None) -> str:
    """Map a screen's connection copy to the shared vocabulary.

    ABSENT means "no connection claim was rendered at all" — distinct from
    NOT_CONNECTED, which is an honest negative claim. The probe treats a
    missing surface as a violation when one was expected.
    """
    if text is None:
        return ABSENT
    t = " ".join(str(text).lower().split())
    if not t:
        return ABSENT
    for m in _NOT_CONNECTED_MARKERS:
        if m in t:
            return NOT_CONNECTED
    for m in _UNAVAILABLE_MARKERS:
        if m in t:
            return UNAVAILABLE
    for m in _CONNECTED_MARKERS:
        if m in t:
            return CONNECTED
    return ABSENT


def claims_connection(text: str | None) -> bool:
    """True iff the text contains ANY connected marker.

    Deliberately INDEPENDENT of the priority order in ``classify_connection_text``
    (which tests negative markers first): a page that renders both "no connection
    observed yet" and "Your agent is connected" must still be caught by the
    negative sweep.
    """
    t = " ".join(str(text or "").lower().split())
    return any(m in t for m in _CONNECTED_MARKERS)


def server_observed(projection: dict | None, *,
                    accept_wire_complete: bool = True) -> bool:
    """True iff the SERVER'S OWN projection records a connection.

    The vocabulary is the SAME the shipped client uses — but the two shipped
    derivations DIFFER, so the caller must pick the one matching the surface
    it is judging:

    * ``accept_wire_complete=True`` (default) matches
      ``overview.js::overviewConnection``: the ``harness-connected`` agent step
      edge, node ``status == 'complete'``, or jsonb ``onboarding_complete``.
      Cases 1/2 of ``onboarding/state.py::resolve_wire_completion`` DO accept
      the legacy wire-complete forms with zero agent step edges (the
      grandfathered cohort), so this is PARITY WITH THE SERVER'S OWN COMPLETION
      CONTRACT — which is what makes the guard's question decidable: "does the
      screen claim more than the server's own projection?".
    * ``accept_wire_complete=False`` matches ``main.jsx::serverHarnessConnected``
      (the WIZARD's derivation), which is EDGE-ONLY. Judging the wizard screen
      with the Overview vocabulary would report a false failure for a
      grandfathered org that honestly renders the wizard's negative.

    The property this instrument proves is the client's honesty, not the
    server's history; a screen that renders exactly what the server reports is
    honest even for a grandfathered org.
    """
    if not isinstance(projection, dict):
        return False
    steps = projection.get("completed_steps")
    if isinstance(steps, list) and "harness-connected" in steps:
        return True
    if not accept_wire_complete:
        return False
    if projection.get("status") == "complete":
        return True
    return projection.get("onboarding_complete") is True


@dataclass
class Verdict:
    """The connection-claim probe's decision — the guard, as data."""

    ok: bool
    rule: str
    ui: str
    observed: bool
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


# The guard's rules, in priority order. A violation is a FAIL — the guard
# exists to catch a lying UI, so "not sure" must never resolve to pass.
def judge(ui: str, projection: dict | None, *, expected_surface: bool = True,
          accept_wire_complete: bool = True) -> Verdict:
    """Decide whether a screen's connection claim is honest.

    Two directions, both REQUIRED:

    * the UI may not claim a connection the server did not observe (the false
      PASS this issue exists to prevent); and
    * a server-observed connection must be shown (a UI that hides the truth is
      its own defect, and the positive direction of the walk).

    ``projection`` is the server's ``/v1/onboarding/state`` payload (the
    ``onboarding`` object), or ``None`` when that read failed.
    ``accept_wire_complete`` picks the shipped derivation that matches the
    surface under judgement (see ``server_observed``): True for the Overview
    card, False for the wizard's final screen.
    """
    observed = server_observed(projection, accept_wire_complete=accept_wire_complete)

    if ui == ABSENT and expected_surface:
        return Verdict(False, "surface-missing", ui, observed,
                       "no connection surface rendered where one was expected")
    if ui == ABSENT and not expected_surface:
        return Verdict(True, "absent-expected", ui, observed,
                       "no connection surface was expected here, and none claimed a connection")
    if ui == CONNECTED and projection is None:
        return Verdict(False, "claim-without-server-read", ui, False,
                       "the screen claimed a connection while the server state was unreadable")
    if ui == CONNECTED and not observed:
        return Verdict(False, "claim-without-observation", ui, False,
                       "the screen claimed a connection the server did not observe")
    if observed and ui != CONNECTED:
        return Verdict(False, "observation-not-shown", ui, True,
                       f"the server observed a connection but the screen resolved to {ui!r}")
    if not observed and ui in (NOT_CONNECTED, UNAVAILABLE):
        return Verdict(True, "honest-negative", ui, False,
                       "no connection claimed before the server observed one")
    if observed and ui == CONNECTED:
        return Verdict(True, "observed-and-shown", ui, True,
                       "the connection is shown and the server observed it")
    return Verdict(False, "unclassified", ui, observed,
                   f"the screen resolved to {ui!r}, which carries no decidable claim")


def verdict_for(neg: Verdict, observed_after: bool, pos: Verdict) -> str:
    """Assemble the walk's verdict from its three measured halves.

    ``passed`` REQUIRES the positive direction to be PROVEN — the server
    observed the agent's write AND the screen showed it. A screen that hides an
    observed connection is a FAILURE, and a positive-direction read that never
    resolved is ``incomplete``; neither may ever be recorded as a pass (the
    direction the guard exists to prevent, and the vacuity this function was
    extracted to make testable).
    """
    if not neg.ok:
        return f"failed: {neg.detail}"
    if not observed_after:
        return ("incomplete: the server never observed the agent's write — "
                "the positive direction was not exercised")
    # `passed` requires an actually-SHOWN connection: `pos.observed` alone is
    # not enough (`judge(ABSENT, observed, expected_surface=False)` is ok AND
    # observed — an absent surface showed nothing).
    if pos.ok and pos.observed and pos.ui == CONNECTED:
        return "passed"
    if pos.rule in ("claim-without-observation", "claim-without-server-read",
                    "observation-not-shown"):
        return f"failed: {pos.detail}"
    return f"incomplete: {pos.detail}"


def connection_verdict(ui: str, surface_kind: str, projection: dict | None,
                       page_text: str | None, *,
                       expected_surface: bool = True) -> Verdict:
    """The walk's per-surface decision, as one PURE function.

    Extracted so the two call-site behaviours cycle 1 got wrong are unit-
    testable without a browser:

    * the ALL-PAGE sweep reads `page_text` — pass the UNTRUNCATED body, never
      the bounded artifact copy, or a claim rendered past the cap hides; and
    * the vocabulary is chosen per surface (`card` → the Overview's
      wire-complete forms, `wizard` → the wizard's edge-only form), so a
      grandfathered org honestly rendering the wizard's negative is not a
      false failure.

    A positive claim anywhere on the page promotes a non-CONNECTED resolved
    state to CONNECTED (the smuggle class T5); an ABSENT surface is judged
    as-is, so it can never be promoted into a claim.
    """
    accept = surface_kind == "card"
    if ui != ABSENT and claims_connection(page_text) and ui != CONNECTED:
        ui = CONNECTED
    return judge(ui, projection, expected_surface=expected_surface,
                 accept_wire_complete=accept)


# ── DOM probes (real browser; the page is the source of truth) ──────────────
OVERVIEW_CONNECTION_SELECTOR = '[aria-label="Connection status"]'


def connection_surface_kind(page, selector: str = OVERVIEW_CONNECTION_SELECTOR) -> str:
    """Which connection surface the active page shows — ``"card"`` (the
    Overview grid), ``"wizard"`` (the wizard's final screen), or ``"none"``.

    The two surfaces use DIFFERENT derivations in the shipped client (the
    Overview accepts the server's wire-complete forms; the wizard requires the
    ``harness-connected`` edge), so the guard must judge each with that
    surface's vocabulary. One definition, used by both the reader and the walk.
    """
    if page.locator(selector).count():
        return "card"
    if page.locator(".welcome-title").count() or page.locator("div.done").count():
        return "wizard"
    return "none"


def read_connection_surface(page, selector: str = OVERVIEW_CONNECTION_SELECTOR) -> str:
    """Resolve the ONE connection status the active surface shows.

    Returns the shared vocabulary, or ABSENT when no connection surface that
    carries a decidable claim is rendered. ABSENT is NOT coerced to
    NOT_CONNECTED: a walk that never reached a connection surface has NOT
    measured the negative direction, and must report that gap rather than
    credit an honest-negative it never observed (the vacuous-pin #3806 exists
    to prevent).

    Surfaces, in order: the Overview connection card (read WHOLE, so the
    graph-down "—" + "Couldn't load" copy resolves as UNAVAILABLE, not ABSENT),
    then the wizard's final screen (title + done body).
    """
    kind = connection_surface_kind(page, selector)
    if kind == "card":
        return classify_connection_text(page.locator(selector).first.inner_text())
    if kind == "wizard":
        title = page.locator(".welcome-title")
        done = page.locator("div.done")
        text = " ".join(
            x for x in (
                title.first.inner_text() if title.count() else "",
                done.first.inner_text() if done.count() else "",
            ) if x
        )
        return classify_connection_text(text) if text.strip() else ABSENT
    return ABSENT


def page_body(page) -> str:
    """The page's visible text, UNTRUNCATED — the DETECTION input.

    Distinct from ``recorded_body`` on purpose: the sweep that catches a
    smuggled positive claim (class T5) must see the whole page, while the copy
    written to disk is bounded and redacted. Conflating the two let a claim
    rendered past the artifact cap hide from the sweep (cycle-1 finding).
    """
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def recorded_body(page) -> str:
    """The page's text as written to the observation: bounded and redacted."""
    return scrub(page_body(page))


def front_door_probe(page, selector: str = "#btn-email") -> dict:
    """Is the signup CTA the element a click actually lands on?

    Measures the DOM top-of-stack at the CTA's centre — an overlay that covers
    the button (the #3781 class) makes ``elementFromPoint`` return the overlay,
    not the button. This is what a real user's click would hit.
    """
    js = """(sel) => {
      const el = document.querySelector(sel);
      if (!el) return { found: false };
      const r = el.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const top = document.elementFromPoint(x, y);
      const desc = (n) => n ? (n.tagName + (n.id ? '#' + n.id : '')) : null;
      return {
        found: true,
        centre: { x, y },
        hittable: !!top && (top === el || el.contains(top)),
        top: desc(top),
        visible: !!(r.width && r.height),
      };
    }"""
    return page.evaluate(js, selector)


# ── the observation record ──────────────────────────────────────────────────
@dataclass
class Step:
    name: str
    url: str = ""
    ui: str = ""
    observed: bool = False
    ok: bool | None = None
    detail: str = ""
    screenshot: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Observation:
    started_at: str
    target: dict
    # The deploy anchors. `deploy_sha` is the deployment's OWN revision, read
    # from the public GET /v1/version (commit_sha baked at deploy time by
    # deploy-hosted.yml) — the literal "deployed SHA" the evidence standard
    # requires. `bundle` is the content-addressed client asset the dashboard
    # served, a second, dashboard-side anchor. `sha` is the instrument's own
    # checkout, i.e. which revision of THIS tool produced the observation.
    deploy_sha: str = ""
    bundle: str = ""
    sha: str = ""
    steps: list = field(default_factory=list)
    assertions: dict = field(default_factory=dict)
    verdict: str = "incomplete"

    def add(self, **kw) -> Step:
        s = Step(**kw)
        self.steps.append(s)
        return s


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    """The instrument's own revision, so an observation is attributable."""
    try:
        import subprocess
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


# Redact the VALUE, not merely the key name: a redaction that leaves
# `"password":"hunter2"` as `"[REDACTED]":"hunter2"` leaks the credential
# while looking scrubbed (caught by tests/test_ship_test_onboarding.py).
_SECRET_KEY_RE = re.compile(
    r'(?P<key>["\']?(?:password|passwd|secret|access_token|refresh_token|session_token'
    r'|api_key|apikey|token)["\']?)'
    r'(?P<sep>\s*[:=]\s*)'
    # A quoted value consumes to its MATCHING quote — pair-aware, so a value
    # containing the other quote char ("pa'ss word") still reaches its close;
    # JSON-escaped quotes included. An unquoted value stops at a delimiter.
    r'(?:(?P<q>["\'])(?:(?!(?P=q))[^\\]|\\.)*(?P=q)|[^\s,}&]+)',
    re.I,
)
# A bare credential by shape (no accompanying key): a pasted tt_/tk_ key or JWT.
# tk_ is a REAL minted prefix (tortoise/auth.py::API_KEY_PREFIXES) — redacting
# only tt_ left scoped/graph keys in the artifact.
_SECRET_TOKEN_RE = re.compile(
    r"(?:tt|tk)_[A-Za-z0-9_]{8,}"
    r"|ey[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]+)?"
)


def _redact_secret_value(match: re.Match) -> str:
    q = match.group("q") or ""
    return f"{match.group('key')}{match.group('sep')}{q}[REDACTED]{q}"


def scrub(text: str, limit: int = 4000) -> str:
    """Keep an artifact, never a credential."""
    t = _SECRET_KEY_RE.sub(_redact_secret_value, str(text or ""))
    t = _SECRET_TOKEN_RE.sub("[REDACTED]", t)
    return t[:limit]


def deployed_bundle(base_url: str) -> str:
    """The deployed client bundle's asset name — the dashboard-side anchor."""
    status, html = _http("GET", base_url.rstrip("/") + "/")
    if status != 200 or not isinstance(html, str):
        return ""
    m = re.search(r"assets/index-[A-Za-z0-9_\-]+\.js", html)
    return m.group(0) if m else ""


def deployed_sha(api_url: str) -> str:
    """The DEPLOYMENT'S OWN revision, from the public ``GET /v1/version``.

    ``commit_sha`` is baked into the release env at deploy time
    (``deploy-hosted.yml``: ``TORTOISE_GIT_SHA=${GITHUB_SHA}``) and the route is
    in ``SKIP_AUTH``, so this needs no credentials. This is the literal
    "deployed SHA" the observation must carry; empty only when the deployment
    predates the route or the deploy staged no sha.
    """
    status, body = _http("GET", api_url.rstrip("/") + "/v1/version")
    if status != 200 or not isinstance(body, dict):
        return ""
    return str(body.get("commit_sha") or "")


# ── HTTP helpers (server truth, no browser) ─────────────────────────────────
def _http(method: str, url: str, *, headers: dict | None = None,
          body: dict | None = None, timeout: int = 30) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    # A browser UA — Cloudflare serves a 403 to the default python-urllib agent.
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except (urllib.error.URLError, OSError):
        # Unreachable target (DNS/TLS/refused) is the per-deploy failure that
        # matters most — it must become a recorded observation, not a traceback
        # with no artifact (the module contract: the dir always holds one).
        return 0, ""


def session_token_from_cookies(cookies: list[dict]) -> str | None:
    """Extract the access token from the ``sb-*-auth-token`` session cookie."""
    for c in cookies:
        if c.get("name", "").startswith("sb-") and c["name"].endswith("-auth-token"):
            try:
                return json.loads(urllib.parse.unquote(c["value"]))["access_token"]
            except Exception:
                continue
    return None


def session_token_from_page(page) -> str | None:
    """Fallback: read the session from the page's cookie jar or localStorage.

    The cross-subdomain bridge writes the parent-domain cookie a beat after the
    redirect; when that has not landed (or the legacy localStorage key is still
    authoritative) this reads what the page itself holds.
    """
    try:
        store = page.evaluate("""() => {
          const out = {};
          try { out.cookie = document.cookie || ''; } catch (e) {}
          try {
            for (let i = 0; i < localStorage.length; i++) {
              const k = localStorage.key(i);
              if (k && k.indexOf('sb-') === 0) out[k] = localStorage.getItem(k);
            }
          } catch (e) {}
          return out;
        }""")
    except Exception:
        return None
    for part in (store.get("cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name.startswith("sb-") and name.endswith("-auth-token"):
            try:
                return json.loads(urllib.parse.unquote(value))["access_token"]
            except Exception:
                pass
    for key, value in store.items():
        if key.startswith("sb-") and key.endswith("-auth-token") and value:
            try:
                return json.loads(value)["access_token"]
            except Exception:
                pass
    return None


def read_projection(api_url: str, token: str, cookies: list[dict]) -> dict | None:
    """Read ``/v1/onboarding/state`` as the server sees it, for the session."""
    cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                           if "premiselabs" in c.get("domain", ""))
    status, body = _http("GET", f"{api_url}/v1/onboarding/state",
                         headers={"Authorization": f"Bearer {token}", "Cookie": cookie_hdr})
    if status != 200 or not isinstance(body, dict):
        return None
    return body.get("onboarding") if isinstance(body.get("onboarding"), dict) else body


def mcp_call(api_url: str, key: str, method: str, params: dict | None = None,
             rid: int = 1) -> tuple[int, str]:
    """One JSON-RPC call against the hosted MCP endpoint (real agent path)."""
    payload = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        payload["params"] = params
    status, body = _http(
        "POST", f"{api_url}/mcp/",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": f"Bearer {key}"},
        body=payload)
    return status, body if isinstance(body, str) else json.dumps(body)


def observe_agent_write(api_url: str, key: str, *, content: str) -> dict:
    """The real server-observed path: MCP initialize → tools/call.

    ``tortoise_create_point`` is the agent's first write; the server's
    ``_maybe_onboarding_auto_complete`` files ``harness-connected`` from it.
    Nothing here writes the onboarding checkpoint directly — that is the point.
    """
    out = {}
    status, body = mcp_call(api_url, key, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "ship-test-3806", "version": "1.0"}})
    out["initialize"] = {"status": status, "body": body[:400]}
    status, body = mcp_call(api_url, key, "tools/call", {
        "name": "tortoise_create_point",
        "arguments": {"content": content, "kind": "statement"}}, rid=2)
    out["tools_call"] = {"status": status, "body": body[:600]}
    return out


# ── the live walk ───────────────────────────────────────────────────────────
def run_walk(args) -> Observation:
    out_dir = Path(args.out)
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    email = args.email or f"ship-test-{int(time.time())}-{uuid.uuid4().hex[:6]}@premiselabs.co"
    password = args.password or f"ShipTest-{uuid.uuid4().hex[:10]}-Aa1!"

    obs = Observation(started_at=_now(), target={
        "dashboard": args.base_url, "auth": args.auth_url, "api": args.api_url})

    try:
        # Lazy so the core (judge/probe/CLI) stays importable without playwright —
        # but GUARDED, so a missing driver still yields a recorded observation.
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        obs.verdict = f"failed: playwright unavailable: {type(exc).__name__}: {exc}"
        return _finish(obs, out_dir)

    def shot(page, name: str) -> str:
        p = shots / f"{len(obs.steps):02d}-{name}.png"
        try:
            page.screenshot(path=str(p), full_page=True)
            return str(p.relative_to(out_dir))
        except Exception as exc:  # a screenshot must never fail the walk
            return f"[screenshot failed: {exc}]"

    with sync_playwright() as pw:
        browser = None
        ctx = None
        page = None
        try:
            # The deploy anchors and the browser launch live INSIDE this guard:
            # an unreachable target or a launch failure must still write an
            # observation (the module contract), not abort with a traceback.
            obs.deploy_sha = deployed_sha(args.api_url)
            obs.bundle = deployed_bundle(args.base_url)
            obs.sha = _git_sha()
            browser = pw.chromium.launch(headless=not args.headed)
            # CLEAN BROWSER: no storage state, no pre-seeded session, no beta-gate
            # flag — exactly what a new user arrives with.
            ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                      locale="en-US")
            page = ctx.new_page()
            signup_responses: list[dict] = []

            def _on_response(resp):
                if "/signup" in resp.url or "auth/v1/signup" in resp.url or "token" in resp.url:
                    signup_responses.append({"url": resp.url, "status": resp.status})

            page.on("response", _on_response)
            # 1 ── the front door
            page.goto(args.auth_url.rstrip("/") + "/auth",
                      wait_until="domcontentloaded", timeout=args.timeout)
            page.wait_for_timeout(args.settle_ms)
            probe = front_door_probe(page)
            obs.assertions["front_door_reachable"] = bool(probe.get("hittable"))
            obs.add(name="front-door", url=page.url,
                    ok=bool(probe.get("hittable")),
                    detail=json.dumps(probe),
                    screenshot=shot(page, "front-door"), extra=probe)
            if not probe.get("hittable"):
                obs.verdict = "failed: signup CTA not hittable in a clean browser"
                return _finish(obs, out_dir)

            # 2 ── signup
            page.click("#btn-email", timeout=args.timeout)
            page.wait_for_timeout(500)
            page.fill("#email", email)
            page.fill("#password", password)
            obs.add(name="signup-form", url=page.url, detail=f"email={email}",
                    screenshot=shot(page, "signup-form"),
                    extra={"email": email})
            page.click('#email-modal button[type="submit"], #btn-submit', timeout=args.timeout)
            page.wait_for_timeout(3000)

            # 3 ── ride the landing into the product (dashboard or wizard)
            deadline = time.time() + args.timeout / 1000
            while time.time() < deadline:
                if "app." in page.url or args.base_url.rstrip("/") in page.url:
                    break
                page.wait_for_timeout(1000)
            obs.add(name="landing-after-signup", url=page.url,
                    detail=json.dumps(signup_responses[-3:]),
                    screenshot=shot(page, "landing"),
                    extra={"body": recorded_body(page)})
            # The session bridge writes the parent-domain cookie a beat after the
            # redirect — poll for it rather than racing the ingest, falling back
            # to what the page's own cookie jar / localStorage holds.
            token = None
            for _ in range(15):
                token = (session_token_from_cookies(ctx.cookies())
                         or session_token_from_page(page))
                if token:
                    break
                page.wait_for_timeout(1000)

            # 4 ── open the wizard and walk it to the final screen (best effort)
            # A first-timer's step 1 is org-create (an input + a button); the
            # generic label loop below cannot advance it.
            org_input = page.locator('input[aria-label="Organization name"]')
            if org_input.count() and org_input.first.is_visible():
                org_input.first.fill(args.org_name or f"Ship Test {int(time.time())}")
                page.locator('button:has-text("Create Organization")').first.click(timeout=args.timeout)
                page.wait_for_timeout(4000)
            # Wait for the product shell to settle before clicking: a "walk"
            # that checks for buttons before React mounts records nothing.
            labels = ("Continue setup", "Continue →", "For my internal setup",
                      "I've set it up — Continue →", "Skip for now")
            for _ in range(20):
                if (any(page.locator(f'button:has-text("{lbl}")').count() for lbl in labels)
                        or page.locator("button.setup-header").count()):
                    break
                page.wait_for_timeout(1000)
            for _ in range(10):
                clicked = False
                for label in labels:
                    btn = page.locator(f'button:has-text("{label}")')
                    if btn.count() and btn.first.is_visible():
                        try:
                            btn.first.click(timeout=4000)
                            clicked = True
                            page.wait_for_timeout(1500)
                            break
                        except Exception:
                            continue
                if not clicked:
                    setup = page.locator("button.setup-header")
                    if setup.count() and setup.first.is_visible():
                        try:
                            setup.first.click(timeout=4000)
                            page.wait_for_timeout(1500)
                            clicked = True
                        except Exception:
                            pass
                if not clicked:
                    break
            obs.add(name="wizard-final", url=page.url,
                    detail=("session token captured" if token else "NO session token captured"),
                    screenshot=shot(page, "wizard-final"), extra={"body": recorded_body(page)})

            # 5 ── the NEGATIVE direction: read the screen + the server truth
            # The negative is only MEASURED if the walk reached a surface that
            # carries a decidable claim. A page with no connection surface is
            # not an honest negative — it is an unmeasured one, and must not be
            # credited as a pass (the vacuous-pin class #3806 exists to prevent).
            cookies = ctx.cookies()
            projection = read_projection(args.api_url, token, cookies) if token else None
            ui = read_connection_surface(page)
            # The UNTRUNCATED body feeds the sweep: scrub()'s cap is for the
            # recorded artifact only. `connection_verdict` picks the surface's
            # own vocabulary and applies the all-page smuggle promotion.
            raw = page_body(page)
            surface = connection_surface_kind(page)
            page_claims = claims_connection(raw)
            obs.assertions["walk_completed"] = ui != ABSENT
            neg = connection_verdict(ui, surface, projection, raw)
            ui = neg.ui
            if ui == ABSENT:
                obs.add(name="before-observation", url=page.url, ui=ui, ok=False,
                        observed=neg.observed,
                        detail="no connection surface reached — the negative direction was not measured",
                        screenshot=shot(page, "before-observation"),
                        extra={"projection": projection, "rule": neg.rule, "body": scrub(raw)})
                obs.verdict = "incomplete: the walk never reached a connection surface"
                return _finish(obs, out_dir)
            obs.add(name="before-observation", url=page.url, ui=ui,
                    observed=neg.observed, ok=neg.ok, detail=neg.detail,
                    screenshot=shot(page, "before-observation"),
                    extra={"projection": projection, "rule": neg.rule,
                           "page_claims_connection": page_claims, "body": scrub(raw)})
            obs.assertions["no_claim_before_observation"] = neg.ok
            if not neg.ok:
                obs.verdict = f"failed: {neg.detail}"
                return _finish(obs, out_dir)

            # 6 ── the server observes a real agent write (MCP, not a client claim)
            observed_after = False
            if not args.skip_agent_write and token:
                key, key_detail = _mint_or_read_key(page, args, token)
                if key:
                    result = observe_agent_write(
                        args.api_url, key,
                        content=f"ship-test #3806 observation {uuid.uuid4().hex[:8]}")
                    # poll the server's projection until it records the edge
                    for _ in range(20):
                        projection = read_projection(args.api_url, token, cookies) or {}
                        if server_observed(projection):
                            break
                        page.wait_for_timeout(1000)
                    observed_after = server_observed(projection)
                    obs.add(name="agent-write", ui="", observed=observed_after,
                            ok=observed_after,
                            detail="MCP tortoise_create_point",
                            extra={"mcp": result, "projection": projection})
                else:
                    obs.add(name="agent-write", ok=False, detail=key_detail)
            else:
                obs.add(name="agent-write", ok=False,
                        detail="no session token — the agent write could not be made")

            # 7 ── the POSITIVE direction: reload and read the Overview again
            page.goto(args.base_url.rstrip("/") + "/",
                      wait_until="domcontentloaded", timeout=args.timeout)
            page.wait_for_timeout(args.settle_ms)
            cookies = ctx.cookies()
            token = session_token_from_cookies(cookies) or token
            projection = read_projection(args.api_url, token, cookies) if token else None
            ui = read_connection_surface(page)
            pos = connection_verdict(ui, connection_surface_kind(page), projection,
                                     page_body(page))
            ui = pos.ui
            obs.add(name="after-observation", url=page.url, ui=ui,
                    observed=pos.observed, ok=pos.ok and pos.observed, detail=pos.detail,
                    screenshot=shot(page, "after-observation"),
                    extra={"projection": projection, "rule": pos.rule, "body": recorded_body(page)})
            # The positive half is only PROVEN when the server observation
            # happened AND the screen shows it. `verdict_for` encodes exactly
            # that: `passed` requires pos.observed, never merely pos.ok (a
            # hidden connection resolves to a judge-OK honest-negative).
            obs.assertions["shown_when_observed"] = bool(pos.ok and pos.observed)
            obs.verdict = verdict_for(neg, observed_after, pos)
            return _finish(obs, out_dir)
        except Exception as exc:
            obs.add(name="error", url=page.url if page else "",
                    ok=False, detail=f"{type(exc).__name__}: {exc}",
                    screenshot=shot(page, "error") if page else "")
            obs.verdict = f"failed: {type(exc).__name__}: {exc}"
            return _finish(obs, out_dir)
        finally:
            if ctx is not None:
                ctx.close()
            if browser is not None:
                browser.close()


def _mint_or_read_key(page, args, token: str) -> tuple[str | None, str]:
    """Obtain an agent key to make the server-observed MCP write.

    Prefers the key the wizard already showed (in-memory, shown once), then
    mints one through the session API. Never writes onboarding state itself.
    Returns (key, detail).
    """
    try:
        for code in page.locator("code").all_inner_texts():
            code = code.strip()
            # BOTH minted prefixes (tortoise/auth.py::API_KEY_PREFIXES). Matching
            # only `tt_` skipped a shown `tk_` scoped/graph key and minted a
            # second one — which at the free tier's cap turns the positive
            # direction into `incomplete`.
            if code.startswith(("tt_", "tk_")):
                return code, "key read from the wizard's shown-once row"
    except Exception:
        pass
    status, body = _http("POST", f"{args.api_url}/v1/team/keys",
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"},
                         body={"name": "ship-test-3806"})
    if status == 200 and isinstance(body, dict):
        key = body.get("key") or body.get("api_key")
        if key:
            return key, "key minted via POST /v1/team/keys"
    return None, f"mint failed: POST /v1/team/keys -> {status} {scrub(json.dumps(body), 200)}"


def _finish(obs: Observation, out_dir: Path) -> Observation:
    path = out_dir / "observation.json"
    path.write_text(json.dumps(asdict(obs), indent=2) + "\n")
    print(f"[ship-test] {obs.verdict}")
    for s in obs.steps:
        mark = "PASS" if s.ok else ("FAIL" if s.ok is False else "--")
        print(f"  {mark:4} {s.name:24} ui={s.ui or '-':14} {s.detail[:90]}")
    print(f"[ship-test] assertions: {obs.assertions}")
    print(f"[ship-test] deployed sha: {obs.deploy_sha or '(unreadable)'}  "
          f"bundle: {obs.bundle or '(unreadable)'}  instrument: {obs.sha or '(unknown)'}")
    print(f"[ship-test] observation → {path}")
    return obs


# ── the guard self-check (mutation evidence, no browser needed) ─────────────
# The guard must go RED on its defect and stay GREEN under a reformat. This
# mode executes the REAL judge() against the defect and the reformat so the
# discrimination is provable in CI without a deployment.
_OBSERVED = {"status": "active", "completed_steps": ["team-named", "harness-connected"]}
_UNOBSERVED = {"status": "active", "completed_steps": ["team-named"]}


def mutation_selfcheck() -> int:
    cases = [
        # name, ui, projection, expect_ok
        ("defect: lies about a connection the server never observed", CONNECTED, _UNOBSERVED, False),
        ("defect: hides a connection the server observed", NOT_CONNECTED, _OBSERVED, False),
        ("defect: claims a connection while the server read failed", CONNECTED, None, False),
        ("defect: connection surface missing", ABSENT, _UNOBSERVED, False),
        ("honest: negative before any observation", NOT_CONNECTED, _UNOBSERVED, True),
        ("honest: negative when the read failed", UNAVAILABLE, None, True),
        ("honest: connected once the server observed it", CONNECTED, _OBSERVED, True),
    ]
    failures = []
    print("[mutation-selfcheck] guard discrimination")
    for name, ui, proj, expect in cases:
        v = judge(ui, proj)
        got = "GREEN" if v.ok else "RED"
        want = "GREEN" if expect else "RED"
        ok = v.ok is expect
        print(f"  {'ok ' if ok else 'BAD'} {got:5} (want {want:5})  {name}")
        if not ok:
            failures.append(name)
    # Behaviour-identical reformat: the SAME claim expressed with different
    # casing/whitespace/ordering of the surrounding copy must not change the
    # verdict. This is the false-positive check a text-scan guard fails.
    reformats = ["Connected ✓", "  CONNECTED   ✓  ", "\n Connected ✓\n",
                 "Your agent is connected to this Organization."]
    for text in reformats:
        ui = classify_connection_text(text)
        v = judge(ui, _UNOBSERVED)
        if v.ok:
            failures.append(f"reformat leaked GREEN: {text!r}")
            print(f"  BAD   GREEN (want RED)  reformat {text!r}")
        else:
            print(f"  ok    RED   (want RED)  reformat {text!r}")
    # Surface-aware vocabulary: the WIZARD is edge-only
    # (main.jsx::serverHarnessConnected), so a grandfathered wire-complete org
    # honestly rendering the wizard's negative must NOT be judged as hiding a
    # connection; the Overview (overview.js::overviewConnection) accepts it.
    grandfather = {"status": "complete", "completed_steps": []}
    wizard_view = judge(NOT_CONNECTED, grandfather, accept_wire_complete=False)
    card_view = judge(CONNECTED, grandfather, accept_wire_complete=True)
    for name, v, expect in (
            ("wizard vocabulary is edge-only (grandfathered org is honest)", wizard_view, True),
            ("overview vocabulary accepts the server's wire-complete form", card_view, True)):
        ok = v.ok is expect
        print(f"  {'ok ' if ok else 'BAD'} {'GREEN' if v.ok else 'RED':5} "
              f"(want {'GREEN' if expect else 'RED':5})  {name}")
        if not ok:
            failures.append(name)
    # The verdict assembly must NEVER report `passed` without the positive
    # direction proven — a hidden connection (observed=True, shown=False) is a
    # failure and an unreadable positive read is incomplete.
    verdict_cases = [
        # name, observed_after, pos, forbidden/failure-condition
        ("hidden connection cannot pass", True, judge(NOT_CONNECTED, _OBSERVED), "passed"),
        ("failed positive read cannot pass", True, judge(NOT_CONNECTED, None), "passed"),
        ("claim without a server read cannot pass", True, judge(CONNECTED, None), "passed"),
        ("no observation cannot pass", False, judge(CONNECTED, _OBSERVED), "passed"),
    ]
    for name, observed_after, pos, forbidden in verdict_cases:
        verdict = verdict_for(judge(NOT_CONNECTED, _UNOBSERVED), observed_after, pos)
        ok = verdict != forbidden
        print(f"  {'ok ' if ok else 'BAD'} {verdict.split(':', 1)[0]:10} "
              f"(not {forbidden!r})  {name}")
        if not ok:
            failures.append(name)
    honest_pass = verdict_for(judge(NOT_CONNECTED, _UNOBSERVED), True, judge(CONNECTED, _OBSERVED))
    ok = honest_pass == "passed"
    print(f"  {'ok ' if ok else 'BAD'} {honest_pass:10} (want 'passed')  "
          "honest walk passes")
    if not ok:
        failures.append("honest walk does not pass")
    if failures:
        print(f"[mutation-selfcheck] FAILED: {len(failures)} case(s): {failures}")
        return 1
    print("[mutation-selfcheck] OK — RED on every defect, GREEN on every honest state, "
          "a behaviour-identical reformat cannot flip a verdict, and `passed` "
          "requires the positive direction to be proven")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────
def _is_loopback(url: str) -> bool:
    import ipaddress
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="#3806 ship-test instrument")
    p.add_argument("--base-url", default=os.environ.get("SHIP_TEST_BASE_URL",
                                                        "https://app.premiselabs.co"))
    p.add_argument("--auth-url", default=os.environ.get("SHIP_TEST_AUTH_URL",
                                                        "https://tortoise.premiselabs.co"))
    p.add_argument("--api-url", default=os.environ.get("SHIP_TEST_API_URL",
                                                       "https://api.premiselabs.co"))
    p.add_argument("--out", default=os.environ.get("SHIP_TEST_OUT",
                                                   "review-artifacts/ship-test"))
    p.add_argument("--email", default=os.environ.get("SHIP_TEST_EMAIL"))
    p.add_argument("--password", default=os.environ.get("SHIP_TEST_PASSWORD"))
    p.add_argument("--org-name", default=os.environ.get("SHIP_TEST_ORG_NAME"))
    p.add_argument("--timeout", type=int, default=45_000, help="per-step ms")
    p.add_argument("--settle-ms", type=int, default=6000)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--skip-agent-write", action="store_true")
    p.add_argument("--allow-prod", action="store_true",
                   default=os.environ.get("SHIP_TEST_ALLOW_PROD") == "1")
    p.add_argument("--mutation-selfcheck", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mutation_selfcheck:
        return mutation_selfcheck()
    targets = [args.base_url, args.auth_url, args.api_url]
    if any(not _is_loopback(u) for u in targets) and not args.allow_prod:
        print("ship-test: non-loopback target — pass --allow-prod (or "
              "SHIP_TEST_ALLOW_PROD=1) to observe a live deployment", file=sys.stderr)
        return 2
    obs = run_walk(args)
    return 0 if obs.verdict == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
