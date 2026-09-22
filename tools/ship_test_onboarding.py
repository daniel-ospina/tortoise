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

**Session (#4291).** The instrument holds no SESSION credential. After
#3501/#4054 the session is an opaque **HttpOnly** ``__Host-session`` cookie —
JS-unreadable (that is the point) and host-only (a ``__Host-`` cookie is never
sent to a sibling origin, so it can never be replayed at the API origin). The
walk therefore resolves its session the way the app does: the BROWSER's own
cookie jar (``ctx.request``) against the app origin's own ``/api/session``,
reading the server's truth through the same-origin ``/api/v1`` BFF proxy that
mints the credential server-side. The instrument does obtain an AGENT key (the
credential under test) for the MCP write — minted through the BFF, or supplied
by ``--agent-key`` (CLI only, never an ambient env var).

``--agent-key`` supplies the **agent write** credential ONLY. The server's
truth is ALWAYS read through the walked session's own projection: letting a key
carry that read would let the UI be judged against a different identity's
projection than the browser is signed in as, which is a false pass — the exact
class this instrument exists to prevent.

Exit code: 0 when every assertion passed, 1 when the product was measured and
found wanting, 2 on a usage/refusal error, and **3 when the run could not
exercise the product** — no signed-in session, a failed agent write, an
unreadable server projection, no browser driver, or any other abort before the
instrument was able to measure the product. (Two deliberate exceptions stay at
1: `walk_incomplete`, where the walk ran but never reached a connection surface,
and `positive_not_attempted` under `--skip-agent-write` — both are statements
about what the run observed, not instrument faults.) 1 vs 3 is the loud-failure
guard: the
record's ``reason`` field carries the same split (``instrument_error`` vs
``server_did_not_observe``), so a deploy job can branch on it without parsing
prose. The observation
directory always holds ``observation.json`` + step screenshots, pass or fail.

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


# ── the loud-failure guard (#4291) ──────────────────────────────────────────
# `incomplete` used to cover BOTH "the instrument could not authenticate"
# (instrument/infra) and "the server did not observe the agent's write"
# (product finding). Those read identically in the artifact, which is how a
# probe with no session was recorded as a product result and the positive
# direction went unexercised without anyone noticing. Every run now carries an
# explicit failure CLASS, and the class is a DIFFERENT process exit code.
REASON_INSTRUMENT_ERROR = "instrument_error"
REASON_SERVER_DID_NOT_OBSERVE = "server_did_not_observe"
REASON_POSITIVE_NOT_SHOWN = "positive_not_shown"
REASON_POSITIVE_NOT_ATTEMPTED = "positive_not_attempted"
REASON_WALK_INCOMPLETE = "walk_incomplete"
REASON_WALK_FAILED = "walk_failed"

# The incomplete verdicts as CONSTANTS: `failure_reason` distinguishes them, so
# an ad-hoc string at a call site could silently land in the wrong class.
INCOMPLETE_NO_OBSERVATION = ("incomplete: the server never observed the agent's "
                             "write — the positive direction was not exercised")
INCOMPLETE_NO_SURFACE = "incomplete: the walk never reached a connection surface"
INCOMPLETE_SKIPPED_WRITE = ("incomplete: --skip-agent-write — the positive "
                            "direction was deliberately not exercised")

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INSTRUMENT_ERROR = 3


def instrument_error_verdict(state: str, detail: str = "") -> str:
    """The verdict for a run that could not exercise the product.

    Deliberately NOT a product verdict: the instrument did not measure the
    product, so it has nothing to say about it. ``state`` names which
    precondition failed (a session state, a failed agent write, an unreadable
    projection) and is carried verbatim into the record.
    """
    suffix = f": {detail}" if detail else ""
    return (f"instrument-error: the run could not exercise the product "
            f"({state}{suffix}) — the positive direction was never exercised. "
            "This says nothing about the product.")


def failure_reason(verdict: str, *, session_state: str) -> str:
    """The run's failure CLASS — pure, total, and derived from the verdict.

    A session POSITIVELY resolved as unusable is checked FIRST and returns
    ``instrument_error`` however the verdict was assembled, so an instrument
    fault can never be recorded under a product-facing class. The empty
    ``session_state`` (the walk failed before it reached the session step — a
    front-door assertion, say) is NOT an instrument error: that failure is a
    product finding and keeps its own class.
    """
    if verdict == "passed":
        return ""
    if session_state in UNUSABLE_SESSION_STATES:
        return REASON_INSTRUMENT_ERROR
    # An instrument-error VERDICT is its own class regardless of how the session
    # resolved: a failed agent write or an unreadable projection is an
    # instrument fault even when the session was fine (#4291, one layer down).
    if verdict.startswith("instrument-error:"):
        return REASON_INSTRUMENT_ERROR
    if verdict.startswith("failed:"):
        return REASON_WALK_FAILED
    if verdict == INCOMPLETE_NO_OBSERVATION:
        return REASON_SERVER_DID_NOT_OBSERVE
    if verdict == INCOMPLETE_SKIPPED_WRITE:
        return REASON_POSITIVE_NOT_ATTEMPTED
    if verdict == INCOMPLETE_NO_SURFACE:
        return REASON_WALK_INCOMPLETE
    return REASON_POSITIVE_NOT_SHOWN


def exit_code_for(reason: str) -> int:
    """The process outcome. A run that could not authenticate (3) is a
    DIFFERENT outcome from a run that measured the product and found it wanting
    (1), so a deploy job can branch on it without parsing prose."""
    if reason == REASON_INSTRUMENT_ERROR:
        return EXIT_INSTRUMENT_ERROR
    return EXIT_FAILED


def verdict_for(neg: Verdict, observed_after: bool, pos: Verdict, *,
                session_state: str, skip_agent_write: bool = False) -> str:
    """Assemble the walk's verdict from its measured halves.

    ``session_state`` is REQUIRED, and checked FIRST: a run that could not
    establish a session is an instrument error (#4291) and can never be
    assembled into ``passed``, nor into the product-facing "the server never
    observed" incomplete — those are claims about the product, and a run that
    could not sign in is not entitled to make them.

    ``passed`` REQUIRES the positive direction to be PROVEN — the server
    observed the agent's write AND the screen showed it. A screen that hides an
    observed connection is a FAILURE, and a positive-direction read that never
    resolved is ``incomplete``; neither may ever be recorded as a pass (the
    direction the guard exists to prevent, and the vacuity this function was
    extracted to make testable).
    """
    if session_state != SESSION_SIGNED_IN:
        return instrument_error_verdict(session_state)
    if not neg.ok:
        return f"failed: {neg.detail}"
    # A LYING UI is a product failure however the positive direction was set up:
    # `--skip-agent-write` must not launder a screen that claims a connection
    # nothing observed. This check therefore precedes the skip short-circuit.
    if pos.rule in ("claim-without-observation", "claim-without-server-read",
                    "observation-not-shown"):
        return f"failed: {pos.detail}"
    if skip_agent_write:
        return INCOMPLETE_SKIPPED_WRITE
    if not observed_after:
        return INCOMPLETE_NO_OBSERVATION
    # `passed` requires an actually-SHOWN connection: `pos.observed` alone is
    # not enough (`judge(ABSENT, observed, expected_surface=False)` is ok AND
    # observed — an absent surface showed nothing).
    if pos.ok and pos.observed and pos.ui == CONNECTED:
        return "passed"
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
    # How the run authenticated (`state`, `detail`, `mechanism`) — the evidence
    # for the guard's central distinction, recorded rather than narrated.
    session: dict = field(default_factory=dict)
    # The failure CLASS (#4291). Empty iff `verdict == "passed"`. A run whose
    # class is `instrument_error` measured nothing about the product and exits 3.
    reason: str = ""
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


# ── the session seam (#3501 / #4054) ────────────────────────────────────────
# The app no longer hands the browser a credential. `/api/session` is the single
# source of session truth (SCOPE.md §8.2) and the session itself is an opaque
# **HttpOnly** `__Host-session` cookie: unreadable from JS (that is the point)
# and host-only (a `__Host-` cookie is never sent to a sibling origin, so it can
# never be replayed at the API origin).
#
# The only honest way for the instrument to act as this user is therefore to use
# the browser's own cookie jar against the app's own origin — `ctx.request` IS
# that jar. Re-implementing sign-in, or asking a token out of the page, would
# exercise a different auth path than the one a user takes; reading the retired
# `sb-*-auth-token` is exactly what made the positive direction unexercisable
# (#4291).
SESSION_PATH = "/api/session"
BFF_API_PREFIX = "/api/v1"

SESSION_SIGNED_IN = "signed_in"
SESSION_NOT_SIGNED_IN = "not_signed_in"
SESSION_STORE_UNAVAILABLE = "store_unavailable"
SESSION_UNREACHABLE = "unreachable"

# A session POSITIVELY resolved to one of these is an instrument/infra fault —
# the run measured nothing about the product. The empty state means "the walk
# never got as far as the session step", which is a different thing entirely
# (a front-door assertion can legitimately fail first, and that IS a product
# finding).
UNUSABLE_SESSION_STATES = (SESSION_NOT_SIGNED_IN, SESSION_STORE_UNAVAILABLE,
                           SESSION_UNREACHABLE)

SESSION_MECHANISM = "browser cookie jar → app-origin /api/session → /api/v1 BFF proxy"


def bff_session(ctx, base_url: str) -> tuple[str, str]:
    """Resolve the walk's session on the app origin, as the app itself does.

    Returns ``(state, detail)`` with ``state`` one of the ``SESSION_*``
    constants. The endpoint's own 401/503 split is preserved: 401 is "NOT signed
    in", 503 is "the session store is unreachable" and must never be reported as
    a sign-out (the #3485 class the split exists to prevent).
    """
    try:
        resp = ctx.request.get(base_url.rstrip("/") + SESSION_PATH)
    except Exception as exc:
        return SESSION_UNREACHABLE, f"{type(exc).__name__}: {exc}"
    if resp.status == 200:
        return SESSION_SIGNED_IN, f"200 from {SESSION_PATH}"
    if resp.status == 401:
        return SESSION_NOT_SIGNED_IN, f"401 from {SESSION_PATH} (no session)"
    if resp.status == 503:
        return SESSION_STORE_UNAVAILABLE, f"503 from {SESSION_PATH} (store unreachable)"
    return SESSION_UNREACHABLE, f"{resp.status} from {SESSION_PATH}"


def bff_api(ctx, base_url: str, method: str, path: str,
            body: dict | None = None) -> tuple[int, object]:
    """One call to the same-origin ``/api/v1`` proxy, as this session.

    This is the route the dashboard itself calls: the proxy resolves the session
    cookie server-side, mints/refreshes the credential, and forwards to the API
    origin. The credential never reaches the instrument — which is precisely why
    this is the honest way to read the server's truth for this user.
    """
    url = base_url.rstrip("/") + BFF_API_PREFIX + path
    try:
        if method.upper() == "GET":
            resp = ctx.request.get(url)
        else:
            resp = ctx.request.post(url, data=body if body is not None else {})
    except Exception:
        return 0, None
    try:
        return resp.status, resp.json()
    except Exception:
        return resp.status, None


def read_projection_via_bff(ctx, base_url: str) -> tuple[int, dict | None]:
    """The server's own onboarding projection, read as this session.

    Returns ``(status, projection)``. The STATUS is returned rather than
    swallowed: a read that failed (503 from a degraded store, 401, an
    unreachable origin) must not be silently indistinguishable from a server
    that observed nothing — that conflation is the #4291 class.

    Through the BFF proxy rather than the raw API origin, so the projection the
    instrument judges the screen against is fetched through the SAME endpoint
    the screen itself calls — the comparison is then between the client and its
    own server read, not between two different routes.
    """
    status, body = bff_api(ctx, base_url, "GET", "/onboarding/state")
    if status != 200 or not isinstance(body, dict):
        return status, None
    projection = body.get("onboarding") if isinstance(body.get("onboarding"), dict) else body
    return status, projection


def projection_readable(status: int, projection: dict | None) -> bool:
    """Was the server's truth actually READABLE this time?

    A single named seam for the whole run: ``200`` alone is not enough. A 200
    whose body did not parse to an object (an SPA ``index.html`` fallback, a
    proxy error page served with 200, a scalar/array JSON body) yields no
    projection, and treating that as "the server observed nothing" is exactly
    the #4291 conflation — an instrument fault reported as a product finding.
    """
    return status == 200 and projection is not None


def read_projection(ctx, base_url: str) -> tuple[int, dict | None]:
    """The server's truth, read through the WALKED SESSION and nothing else.

    There is deliberately no key-based alternative here. `--agent-key` supplies
    the agent write's credential; if it also carried this read, the browser (org
    A) could be judged against org B's projection and the instrument would
    report `passed` for a lying UI — a false pass. One identity, one read.
    """
    return read_projection_via_bff(ctx, base_url)


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


def _mcp_payload(body: str) -> dict | None:
    """The JSON-RPC payload from an MCP response.

    The hosted endpoint is FastMCP streamable-HTTP and ``create_http_app``
    never enables ``json_response``, so a real response is an **SSE stream**
    (``text/event-stream``) whose JSON rides on ``data:`` lines —
    ``json.loads(body)`` raises on it. The repo's own tests parse it the same
    way (``tests/test_mcp_http.py::_parse_sse_json``). An instrument that
    assumed plain JSON would mark EVERY real write as failed and report a
    healthy deployment as an instrument fault.

    Frames are separated by a blank line and an event's payload may span
    several ``data:`` lines (spec-legal). Only a frame carrying ``result`` or
    ``error`` is a RESPONSE; anything else (a ``notifications/*`` progress
    frame, a ping) is skipped, so a leading notification cannot shadow the
    response that follows it.
    """
    raw = (body or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return _as_response(parsed)
    for frame in re.split(r"\r?\n\r?\n", raw):
        data = "\n".join(
            line.strip()[len("data:"):].lstrip()
            for line in frame.splitlines() if line.strip().startswith("data:")
        )
        if not data:
            continue
        try:
            parsed = json.loads(data)
        except Exception:
            continue
        response = _as_response(parsed)
        if response is not None:
            return response
    return None


def _as_response(parsed: object) -> dict | None:
    """A JSON-RPC RESPONSE — not a notification or request frame."""
    if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
        return parsed
    return None


def _mcp_result_ok(status: int, body: str) -> bool:
    """Did the MCP `tools/call` actually succeed?

    Every false shape here is a failure of the WRITE — an instrument fault —
    and none is evidence about the product: a non-200, a payload that does not
    parse, a top-level JSON-RPC ``error``, ``result`` missing or not an object,
    and MCP's tool-level failure.

    Tool-level failure arrives in TWO shapes in this repo, and the second one
    hides: ``result.isError`` (HTTP 200 — ``tests/test_mcp_http.py`` asserts
    it), and an error dict carried INSIDE a ``result`` whose ``isError`` is
    false — ``tortoise/mcp_server.py`` returns ``{"error": ..., "code": ...}``
    on an SDK exception (``_safe``) and on a quota refusal (``_quota_gated``),
    and ``_maybe_onboarding_auto_complete`` is NOT called for those, so no
    ``harness-connected`` edge is filed. Treating that as success made a quota
    refusal read as "the server did not observe" — the #4291 conflation.
    """
    if status != 200:
        return False
    payload = _mcp_payload(body)
    if payload is None or "error" in payload:
        return False
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        return False
    structured = result.get("structuredContent")
    return not (isinstance(structured, dict) and structured.get("error"))


def observe_agent_write(api_url: str, key: str, *, content: str) -> dict:
    """The real server-observed path: MCP initialize → tools/call.

    ``tortoise_create_point`` is the agent's first write; the server's
    ``_maybe_onboarding_auto_complete`` files ``harness-connected`` from it.
    Nothing here writes the onboarding checkpoint directly — that is the point.

    ``ok`` reports whether the write ITSELF succeeded, so a failed call (a bad
    or wrong-org key, an MCP outage) is classified as an instrument fault
    instead of being blamed on the server as a non-observation.
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
    out["ok"] = _mcp_result_ok(status, body)
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
    # FAIL-CLOSED DEFAULT (#4291). Until a path has PROVEN that it measured the
    # product, a run says nothing about the product. Every product-facing exit
    # below sets its own reason explicitly; anything that aborts earlier (no
    # playwright driver, a browser that will not launch, an unexpected
    # exception) keeps this class — an instrument fault with exit 3, never a
    # product finding with exit 1.
    obs.reason = REASON_INSTRUMENT_ERROR

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
                # A PRODUCT finding: this ran before any session was resolved,
                # and the CTA is genuinely unhittable in a clean browser.
                obs.verdict = "failed: signup CTA not hittable in a clean browser"
                obs.reason = failure_reason(obs.verdict, session_state="")
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
            # 3b ── the session (#4291). Resolved through the app origin's own
            # `/api/session` with the BROWSER's cookie jar. The poll is for the
            # seam's own settle, not for a token: there is no readable token any
            # more, by design (`__Host-session` is HttpOnly and host-only).
            session_state, session_detail = "", ""
            for _ in range(15):
                session_state, session_detail = bff_session(ctx, args.base_url)
                if session_state != SESSION_NOT_SIGNED_IN:
                    break  # signed in — or a fault that polling cannot improve
                page.wait_for_timeout(1000)
            obs.session = {
                "state": session_state, "detail": session_detail,
                "mechanism": ("browser cookie jar → /api/session → /api/v1 BFF proxy; "
                              "agent key from the explicit --agent-key flag"
                              if args.agent_key else SESSION_MECHANISM)}
            obs.add(name="session", url=page.url,
                    ok=session_state == SESSION_SIGNED_IN,
                    detail=f"{session_state}: {session_detail}",
                    screenshot=shot(page, "session"))
            if session_state != SESSION_SIGNED_IN:
                # LOUD and EARLY: a walk that is not signed in is not measuring
                # the product, so it must not continue and "measure" a negative
                # on a signed-out page, nor blame the server for an observation
                # it never got the chance to make (#4291).
                obs.verdict = instrument_error_verdict(session_state, session_detail)
                return _finish(obs, out_dir)

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
                    detail=f"session {session_state}",
                    screenshot=shot(page, "wizard-final"), extra={"body": recorded_body(page)})

            # 5 ── the NEGATIVE direction: read the screen + the server truth
            # The negative is only MEASURED if the walk reached a surface that
            # carries a decidable claim. A page with no connection surface is
            # not an honest negative — it is an unmeasured one, and must not be
            # credited as a pass (the vacuous-pin class #3806 exists to prevent).
            projection_status, projection = read_projection(ctx, args.base_url)
            if not projection_readable(projection_status, projection):
                # The instrument's OWN read of the server's truth failed. It
                # cannot judge a screen it could not check, and it must not
                # claim a product finding it cannot support (the #4291 class):
                # a transient 503 here would otherwise be reported as the client
                # "claiming a connection while the server state was unreadable".
                obs.add(name="server-read", ok=False, ui="",
                        detail=f"GET /api/v1/onboarding/state -> {projection_status}",
                        screenshot=shot(page, "server-read"))
                obs.verdict = instrument_error_verdict(
                    "projection_unreadable",
                    f"GET /api/v1/onboarding/state -> {projection_status}")
                return _finish(obs, out_dir)
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
                obs.verdict = INCOMPLETE_NO_SURFACE
                obs.reason = failure_reason(obs.verdict,
                                           session_state=session_state)
                return _finish(obs, out_dir)
            obs.add(name="before-observation", url=page.url, ui=ui,
                    observed=neg.observed, ok=neg.ok, detail=neg.detail,
                    screenshot=shot(page, "before-observation"),
                    extra={"projection": projection, "rule": neg.rule,
                           "projection_status": projection_status,
                           "page_claims_connection": page_claims, "body": scrub(raw)})
            obs.assertions["no_claim_before_observation"] = neg.ok
            if not neg.ok:
                obs.verdict = f"failed: {neg.detail}"
                obs.reason = failure_reason(obs.verdict, session_state=session_state)
                return _finish(obs, out_dir)

            # 6 ── the server observes a real agent write (MCP, not a client claim)
            # The session is PROVEN signed-in by here, so the key is either the
            # explicitly-supplied one or one minted through the BFF — never a
            # silent substitution, and never skipped for want of a credential.
            observed_after = False
            if not args.skip_agent_write:
                if args.agent_key:
                    key, key_detail = args.agent_key, "key from the explicit --agent-key flag"
                else:
                    key, key_detail = _mint_or_read_key(page, ctx, args.base_url)
                if key:
                    result = observe_agent_write(
                        args.api_url, key,
                        content=f"ship-test #3806 observation {uuid.uuid4().hex[:8]}")
                    obs.add(name="agent-write", ui="", observed=False,
                            ok=bool(result.get("ok")),
                            detail=f"MCP tortoise_create_point ({key_detail})",
                            extra={"mcp": result})
                    if not result.get("ok"):
                        # The WRITE failed — a bad / wrong-org / graph-bound key,
                        # or an MCP outage. That is an instrument fault, and it
                        # must not be reported as "the server did not observe"
                        # (the #4291 class, one layer down).
                        obs.verdict = instrument_error_verdict(
                            "agent_write_failed", key_detail)
                        return _finish(obs, out_dir)
                    # poll the server's projection until it records the edge.
                    # `saw_200` is tracked SEPARATELY from the LAST read's
                    # status: a transient 503 on the final poll must not turn a
                    # demonstrably-never-observed write into an instrument
                    # fault. The class is decided by whether the server's truth
                    # was EVER readable, not by which read happened to be last.
                    saw_readable = False
                    for _ in range(20):
                        projection_status, projection = read_projection(ctx, args.base_url)
                        saw_readable = saw_readable or projection_readable(
                            projection_status, projection)
                        if server_observed(projection):
                            break
                        page.wait_for_timeout(1000)
                    if not saw_readable:
                        # The server's own truth was NEVER readable (a degraded
                        # session store answers 503 here). Nothing was measured.
                        obs.verdict = instrument_error_verdict(
                            "projection_unreadable",
                            f"GET /api/v1/onboarding/state -> {projection_status}")
                        return _finish(obs, out_dir)
                    observed_after = server_observed(projection)
                    # `ok` stays the WRITE's outcome (that is what the step is
                    # named for); whether the server observed it is `observed`.
                    obs.steps[-1].observed = observed_after
                    obs.steps[-1].extra["projection"] = projection
                    obs.steps[-1].extra["projection_status"] = projection_status
                    obs.steps[-1].extra["poll_readable"] = saw_readable
                else:
                    obs.add(name="agent-write", ok=False, detail=key_detail)
                    obs.verdict = instrument_error_verdict("no_agent_key", key_detail)
                    return _finish(obs, out_dir)

            # 7 ── the POSITIVE direction: reload and read the Overview again
            page.goto(args.base_url.rstrip("/") + "/",
                      wait_until="domcontentloaded", timeout=args.timeout)
            page.wait_for_timeout(args.settle_ms)
            projection_status, projection = read_projection(ctx, args.base_url)
            if not projection_readable(projection_status, projection):
                obs.verdict = instrument_error_verdict(
                    "projection_unreadable",
                    f"GET /api/v1/onboarding/state -> {projection_status}")
                return _finish(obs, out_dir)
            ui = read_connection_surface(page)
            pos = connection_verdict(ui, connection_surface_kind(page), projection,
                                     page_body(page))
            ui = pos.ui
            obs.add(name="after-observation", url=page.url, ui=ui,
                    observed=pos.observed, ok=pos.ok and pos.observed, detail=pos.detail,
                    screenshot=shot(page, "after-observation"),
                    extra={"projection": projection, "rule": pos.rule,
                           "projection_status": projection_status,
                           "body": recorded_body(page)})
            # The positive half is only PROVEN when the server observation
            # happened AND the screen shows it. `verdict_for` encodes exactly
            # that: `passed` requires pos.observed, never merely pos.ok (a
            # hidden connection resolves to a judge-OK honest-negative).
            obs.assertions["shown_when_observed"] = bool(pos.ok and pos.observed)
            obs.verdict = verdict_for(neg, observed_after, pos,
                                      session_state=session_state,
                                      skip_agent_write=args.skip_agent_write)
            obs.reason = failure_reason(obs.verdict, session_state=session_state)
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


def _mint_or_read_key(page, ctx, base_url: str) -> tuple[str | None, str]:
    """Obtain an agent key to make the server-observed MCP write.

    Prefers the key the wizard already showed (in-memory, shown once), then
    mints one through the same-origin BFF proxy as this session — the proxy
    mints the credential server-side, so the instrument still holds nothing.
    Never writes onboarding state itself. Returns (key, detail).
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
    status, body = bff_api(ctx, base_url, "POST", "/team/keys",
                           body={"name": "ship-test-3806"})
    if status == 200 and isinstance(body, dict):
        key = body.get("key") or body.get("api_key")
        if key:
            return key, "key minted via POST /api/v1/team/keys (same-origin BFF)"
    return None, (f"mint failed: POST /api/v1/team/keys -> {status} "
                  f"{scrub(json.dumps(body), 200)}")


def _finish(obs: Observation, out_dir: Path) -> Observation:
    if not obs.reason:
        obs.reason = failure_reason(
            obs.verdict, session_state=(obs.session or {}).get("state", ""))
    path = out_dir / "observation.json"
    path.write_text(json.dumps(asdict(obs), indent=2) + "\n")
    print(f"[ship-test] {obs.verdict}")
    for s in obs.steps:
        mark = "PASS" if s.ok else ("FAIL" if s.ok is False else "--")
        print(f"  {mark:4} {s.name:24} ui={s.ui or '-':14} "
              f"obs={'Y' if s.observed else 'n'} {s.detail[:90]}")
    print(f"[ship-test] assertions: {obs.assertions}")
    print(f"[ship-test] session: {obs.session or '(not reached)'}")
    print(f"[ship-test] deployed sha: {obs.deploy_sha or '(unreadable)'}  "
          f"bundle: {obs.bundle or '(unreadable)'}  instrument: {obs.sha or '(unknown)'}")
    print(f"[ship-test] observation → {path}")
    if obs.reason == REASON_INSTRUMENT_ERROR:
        # LOUD, on stderr, with the exit code: a run that could not exercise
        # the product must never be mistaken for a measurement of it.
        # `incomplete` (exit 1) meant both, which is how #4291 hid.
        print(f"[ship-test] INSTRUMENT ERROR — {obs.reason}: a precondition the "
              f"instrument needs was not met, so this run says NOTHING about the "
              f"product. exit {EXIT_INSTRUMENT_ERROR} (a product finding would "
              f"be exit {EXIT_FAILED})", file=sys.stderr)
    elif obs.reason:
        print(f"[ship-test] reason: {obs.reason} (exit {exit_code_for(obs.reason)})")
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
        verdict = verdict_for(judge(NOT_CONNECTED, _UNOBSERVED), observed_after, pos,
                              session_state=SESSION_SIGNED_IN)
        ok = verdict != forbidden
        print(f"  {'ok ' if ok else 'BAD'} {verdict.split(':', 1)[0]:10} "
              f"(not {forbidden!r})  {name}")
        if not ok:
            failures.append(name)
    honest_pass = verdict_for(judge(NOT_CONNECTED, _UNOBSERVED), True,
                              judge(CONNECTED, _OBSERVED),
                              session_state=SESSION_SIGNED_IN)
    ok = honest_pass == "passed"
    print(f"  {'ok ' if ok else 'BAD'} {honest_pass:10} (want 'passed')  "
          "honest walk passes")
    if not ok:
        failures.append("honest walk does not pass")
    # #4291: the loud-failure guard. An instrument that could not authenticate
    # must never reach `passed`, and must never be filed as a product finding —
    # the class it lands in is a different one AND a different exit code.
    for name, state, pos in (
            ("no session cannot pass", SESSION_NOT_SIGNED_IN, judge(CONNECTED, _OBSERVED)),
            ("session store down cannot pass", SESSION_STORE_UNAVAILABLE,
             judge(CONNECTED, _OBSERVED)),
            ("unreachable session cannot pass", SESSION_UNREACHABLE,
             judge(CONNECTED, _OBSERVED))):
        verdict = verdict_for(judge(NOT_CONNECTED, _UNOBSERVED), True, pos,
                              session_state=state)
        reason = failure_reason(verdict, session_state=state)
        code = exit_code_for(reason)
        ok = (verdict != "passed"
              and not verdict.startswith("incomplete: the server never observed")
              and reason == REASON_INSTRUMENT_ERROR
              and code == EXIT_INSTRUMENT_ERROR)
        print(f"  {'ok ' if ok else 'BAD'} {reason:18} exit={code} "
              f"(instrument error, not a product finding)  {name}")
        if not ok:
            failures.append(name)
    # ...and a run that DID authenticate keeps the product finding, under the
    # exit code that distinguishes it from the instrument error.
    product = verdict_for(judge(NOT_CONNECTED, _UNOBSERVED), False,
                          judge(CONNECTED, _OBSERVED), session_state=SESSION_SIGNED_IN)
    product_reason = failure_reason(product, session_state=SESSION_SIGNED_IN)
    ok = (product_reason == REASON_SERVER_DID_NOT_OBSERVE
          and exit_code_for(product_reason) == EXIT_FAILED)
    print(f"  {'ok ' if ok else 'BAD'} {product_reason:18} exit={EXIT_FAILED} "
          f"(product finding, distinct from the instrument error)  "
          "a signed-in run that observed nothing")
    if not ok:
        failures.append("product finding is not distinct from the instrument error")
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
    p.add_argument("--agent-key", default=None,
                   help="an explicit agent key (tt_/tk_) for the MCP agent write, "
                        "instead of minting one through the session. CLI-only — "
                        "deliberately NOT env-settable, so an ambient variable can "
                        "never switch the write's identity silently. It does NOT "
                        "carry the projection read: the server's truth is always "
                        "read through the walked session, so the UI is never "
                        "judged against a different identity's projection.")
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
        return EXIT_USAGE
    try:
        obs = run_walk(args)
    except Exception as exc:
        # run_walk records everything it observes, so nothing should escape it.
        # If something does (a bad --out, a driver that will not start), it is
        # an instrument fault BY CONSTRUCTION — fail closed with exit 3 rather
        # than a traceback that exits 1 and reads as a product finding.
        print(f"ship-test: INSTRUMENT ERROR — run aborted: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INSTRUMENT_ERROR
    if obs.verdict == "passed":
        return EXIT_PASSED
    return exit_code_for(obs.reason)


if __name__ == "__main__":
    raise SystemExit(main())
