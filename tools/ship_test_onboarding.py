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

    # inspect a FAILED run's org instead of reaping it (explicit residue)
    python tools/ship_test_onboarding.py --keep-org --auth-url http://127.0.0.1:8788

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
directory always holds ``observation.json`` + step screenshots, pass or fail —
with deliberate exceptions (#4875), each asserted by name in the fast suite: an
abort that predates the observation itself (an ``--out`` that cannot be created,
a driver that will not start) raises before any writer exists and therefore
writes nothing, and a walk body that raises before it settles writes no
pre-teardown copy at all (its exception propagates past the authoritative
write). The exception list is exhaustive.

**Browser teardown (#4907).** The run owns the Playwright driver's lifecycle
(``start()`` + an explicit teardown) rather than inheriting it, and the browser
teardown is BOUNDED (``TEARDOWN_BOUND_S``): a watchdog signals only the run's
own driver child — enumerated as a direct child with its start time, re-checked
before signalling, reaped after — which is what releases a close blocked
against an unresponsive driver. The outcome is recorded in ``observation.json``
as the ``browser_teardown`` block (``not_run`` / ``clean`` / ``close_error`` /
``watchdog_kill`` / ``driver_absent`` / ``abandoned``), and it is written
atomically twice — but only for a run whose walk settled into the pre-teardown
write: a complete pre-teardown copy whose outcome is ``not_run`` (so a run
killed inside the window still leaves a diagnostic) and the authoritative copy
after the teardown. That is a qualification, not a universal: a walk body that
raises before it settles writes no pre-teardown copy, and its exception
propagates past the authoritative write. A run killed inside the window with
the driver unresponsive still orphans that driver and its Chromium children —
no in-process code runs after the kill — which is disclosed as **#4928**, not
claimed closed.

**Teardown (#4319).** Every per-deploy run creates a real production org
(``Ship Test <epoch>-<hex4>``); the run **reaps it by default**, and the outcome is
recorded in ``observation.json`` as the ``teardown`` block. The org is proven to
be this run's by a **differential proof of creation** — the walked session's own
org list is read before the wizard can create anything and again at teardown, so
this run's orgs are exactly the set difference — never by its name, which a
fixed ``--org-name`` or a reused account could match on an org this run did not
create. Deletion goes through the same-origin ``/api/v1`` BFF proxy as the
session's owner, and ``deleted`` is only recorded after a readable re-read shows
the org gone (a 2xx is not proof). ``--keep-org`` opts out and leaves an
explicit, countable residue. **Teardown never changes the verdict or the exit
code** — a cleanup fault is residue, not a product finding, and it is printed
loudly on stderr (the ``#4291`` guard, both directions).

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
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
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


def run_exit_code(obs: Observation) -> int:
    """The exit code THIS run earned — the ONE definition, shared by `main` and
    by the teardown's last resort.

    `exit_code_for(obs.reason)` alone is NOT it: a PASSED run has an empty reason
    (`failure_reason` returns "" for `passed`), which `exit_code_for` scores as a
    failure. Both callers go through here, so a cleanup fault cannot move the
    exit code (#4319).
    """
    if obs.verdict == "passed":
        return EXIT_PASSED
    return exit_code_for(obs.reason)


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


# ── the BROWSER teardown's own record and bound (#4907) ──────────────────────
# DISTINCT from the org reaper above: `obs.teardown` is the ORG teardown's outcome
# (it drives the residue warning and ~40 existing assertions), while this is the
# BROWSER teardown's. The vocabulary is CLOSED, and `not_run` is what the
# pre-teardown document carries: a run killed inside the bounded window still
# lands a complete, parsable artifact that says the teardown never finished.
#
# A `pw.stop()` failure is `close_error`, alongside a context/browser close that
# raises. `close_error` therefore means "a close the run attempted did not
# complete", whichever closer it was — the `closes` list names it.
BROWSER_TEARDOWN_NOT_RUN = "not_run"
BROWSER_TEARDOWN_CLEAN = "clean"
BROWSER_TEARDOWN_CLOSE_ERROR = "close_error"
BROWSER_TEARDOWN_WATCHDOG_KILL = "watchdog_kill"
BROWSER_TEARDOWN_DRIVER_ABSENT = "driver_absent"
# The ladder was spent and a close was STILL blocked, with no enumerated child to
# release it: the run terminated itself rather than hang forever. Distinct from
# `driver_absent` (no child, but the close returned) — this one means the
# process's own exit was the bound.
BROWSER_TEARDOWN_ABANDONED = "abandoned"
BROWSER_TEARDOWN_OUTCOMES = (
    BROWSER_TEARDOWN_NOT_RUN, BROWSER_TEARDOWN_CLEAN, BROWSER_TEARDOWN_CLOSE_ERROR,
    BROWSER_TEARDOWN_WATCHDOG_KILL, BROWSER_TEARDOWN_DRIVER_ABSENT,
    BROWSER_TEARDOWN_ABANDONED,
)

# The teardown budget, in seconds. The watchdog's rungs are timed off it and the
# teardown returns by it; the pin (`0 < TEARDOWN_BOUND_S <= 60`) lives in the
# test that owns the constant, because an inflated bound is not a bound. The
# first rung is B/2, so the budget must clear twice the healthy teardown
# (~2.6 s measured): at 30 s the graceful window is ~6x the healthy path, so a
# healthy run never reaches a rung, while a close that never returns is still
# hard-bounded.
TEARDOWN_BOUND_S = 30.0

# The `ps` reads the watchdog makes are bounded too, so a wedged `ps` cannot
# spend the teardown's slack. This is the intended absolute cap; the effective
# bound is `_ps_timeout()`, which also caps it at the ladder's own slack (B/4).
_PS_TIMEOUT_S = 1.0

# The process reader is an ABSOLUTE path, never a PATH lookup: this instrument
# runs with an operator-controlled environment, and a PATH-planted `ps` would
# decide which pid the watchdog signals. When the absolute binary is absent the
# reader refuses (no pid is enumerated, so nothing is signalled) rather than
# falling back — a refusal leaves a bounded run with a recorded outcome, which
# is strictly safer than signalling by the output of an unverified binary.
_PS_BIN = "/bin/ps"

# Both `ps` selections ask for UNLIMITED output width. A host's `ps` can
# truncate its LAST column, and the CI runner did exactly that (the same
# truncation class fixed for the embedded reaper in #1365): the
# `command` field is unbounded, so on a runner whose interpreter path alone is
# ~49 characters a real driver's trailing `run-driver` marker is cut off, no
# candidate matches, the driver is never signalled, and a healthy run abandons
# with its Chromium tree live. `ppid=,lstart=` is short enough not to be cut at
# 80 columns, but a truncated start time would collapse two different processes
# into one identity — the pid reuse the atomic read exists to defeat — so the
# reader takes `-ww` too and neither read depends on the venue's width.
_PS_UNLIMITED_WIDTH = "-ww"


def _browser_teardown_record() -> dict:
    """The pre-teardown document's shape: `not_run`, no closes, no detail."""
    return {"outcome": BROWSER_TEARDOWN_NOT_RUN, "closes": [], "detail": ""}


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
    # The run's own cleanup outcome (#4319): a JSON-only summary whose `status`
    # is one of the TEARDOWN_* constants. Declared HERE because the atomic writer
    # serializes with `dataclasses.asdict`, which emits declared fields only —
    # an undeclared attribute is silently dropped from the artifact.
    teardown: dict = field(default_factory=dict)
    # The BROWSER teardown's outcome (#4907). Declared for the same reason as
    # `teardown` — `asdict` emits declared fields only — and pinned to the closed
    # vocabulary above. `not_run` until a teardown actually completes.
    browser_teardown: dict = field(default_factory=_browser_teardown_record)
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


# The key names whose VALUE is a credential, wherever a key/value pair appears
# — free text or a structured dict. `_QUERY_SECRET_PARAMS` is the WIDER
# vocabulary used once a `?`/`&`/`#` proves a query parameter; this list is the
# narrower one safe to apply to ARBITRARY text and to a dict KEY, where a name
# like `code` would hit `status_code=200` or a JSON-RPC `{"code": -32602}`.
# The unambiguous query names are listed in BOTH, so a structured
# `{"id_token": …}` is redacted too. `session` is deliberately NOT here: it is
# an `Observation` FIELD (the session record), so a structural `session` key is
# the record itself, not a credential — its query carrier is still covered by
# the whole-query drop and by `_QUERY_SECRET_PARAMS`.
# STATED BOUNDARY: a value ends at whitespace, a `,`/`&`/`}` delimiter, a
# quote, a backslash, or a STRUCTURED value's `{`/`[` (handled by the
# structural pass, not by swallowing the value as text). A credential
# containing one of those raw characters
# (invalid in a URL query anyway) is therefore only PARTIALLY dropped. The
# alternative — consuming them — is what eats the JSON structure these values
# are embedded in, which is worse: it is caught by the "structure survives"
# tests, and the tolerated residue differs only in the value's tail.
# The issue's query-parameter names that are NOT also free-text key names: a
# `?code=`/`&key=`/`#session=` names a credential carrier, while the same word
# inside a recorded body may be diagnostic (`{"code": …}` — JSON-RPC) or a
# recorded field (`session`), so these are only redacted behind a query lead.
# ...derived from the QUERY vocabulary below, minus the free-text key names,
# so a name can never be in one and missing from the other (review cycle 15:
# `sig` was).
_SECRET_KEY_NAMES = ("password", "passwd", "secret", "access_token", "refresh_token",
                     "session_token", "id_token", "signature",
                     "api_key", "apikey", "token")
# A bracketed VALUE in a serialized body, JSON-SHAPED: its content is a run of
# non-quote characters or COMPLETE quoted strings. A lone quote — the enclosing
# JSON string's own close — can therefore never be swallowed, while a realistic
# value (`["tok"]`, `{"access_token": ["tok"]}`) still matches whole.
_STRUCT_VALUE = (
    r'(?:\[(?:[^\[\]"\\]|"(?:[^"\\]|\\.)*")*\]'
    r'|\{(?:[^{}"\\]|"(?:[^"\\]|\\.)*")*\})'
)

# A run that OWNS its internal whitespace must not cross into the NEXT pair:
# a whitespace-delimited `name<sep>` STARTS a new credential, and swallowing
# it left that pair's VALUE verbatim beside the marker (`id_token:"5 x
# token='CANARY'` leaked CANARY). The lookahead ends the run at the next
# pair, so the pass below still sees it.
_KEY_PAIR_AHEAD = (r"(?!\s[?&#]?(?:" + "|".join(_SECRET_KEY_NAMES)
                   + r")\s*[:=])")
_SECRET_KEY_RE = re.compile(
    r'(?P<key>(?:\\{0,32}["\'])?(?:' + "|".join(_SECRET_KEY_NAMES) + r')(?:\\{0,32}["\'])?)'
    r'(?P<sep>\s*[:=]\s*)'
    # A quoted value consumes to its MATCHING quote — pair-aware, so a value
    # containing the other quote char ("pa'ss word") still reaches its close.
    # Only the FIRST character is delimiter-restricted; a value may hold a
    # `;`/`:`/`)`/`]`/`{`/`[` in its TAIL — HEAD consumed those, so stopping at
    # them was a fail-open REGRESSION (`password=x[S3cret` left the tail).
    r'(?:'
    r'(?P<esc>\\{0,32})(?P<q>["\'])'
    r'(?P<tval>(?:(?!(?P=q))[^\\,;:)\]}&"\'\s]|\\.)'
    r'(?:(?!(?P=q))[^\\]|\\.)*)(?P=esc)(?P=q)'
    # ...or a TERMINATED quoted value whose FIRST character is a delimiter or
    # whitespace (`{"password": " S3CRET"}`): the branch above cannot start on
    # one, so without this the value was left verbatim (HEAD redacted it). It is
    # lower priority than the pair-aware branch. The lookahead keeps the
    # VALUELESS-key shape (`?token=", "status": 200`) off it: a RUN of
    # delimiters, because a CLOSE or separator before it (`?token="}, "id": 2}`)
    # is the same shape, and reading the pair's closing quote as the value's
    # opener ate the following key and left an unparseable body.
    r'|(?P<esc2>\\{0,32})(?P<q2>["\'])(?!(?:\s*[,;:}\]\{]\s*)+["\'])'
    r'(?P<tval2>(?:(?!(?P=q2))[^\\]|\\.)*)(?P=esc2)(?P=q2)'
    # ...or an UNTERMINATED quote (a truncated URL, an exception message) is
    # still a value. This branch sits AFTER both terminated ones — an
    # unterminated read must never pre-empt a value that DOES have a closer
    # (`{"access_token": " a\\q7"}` was cut at the backslash when it came
    # first), and no "no-closer-here" lookahead can enforce that, because the
    # closer may be several escape-units away. `oq` takes a non-empty
    # token-shaped run, `oq2` a whitespace/delimiter-led one; without them a
    # pair whose value began on a space and had NO close (`{"password":
    # " SECRET77`, a cut body) matched no branch at all and survived verbatim.
    # The runs hold NO closing quote (that is branch 2's job) and take INTERNAL
    # whitespace: a pair committed to being unterminated owns everything to the
    # enclosing structure, and stopping at the first space left the value's tail
    # behind (`{"password": ", hunter2` kept `hunter2`).
    r'|(?P<esc3>\\{0,32})(?P<q3>["\'])'
    r'(?:(?P<oq>[^\s,;:)\]}&"\'\\{[]+(?:' + _KEY_PAIR_AHEAD
    + r'[^,}&"\'\\])*)'
    r'|(?P<oq2>(?!\s*[,;:}\]{]\s*["\'])\s*[^"\'}\]&\\]+))'
    # ...a bracketed value is EMPTIED (`[]`/`{}`) rather than quoted: the value
    # goes, and the marker cannot be quoted here without corrupting an enclosing
    # JSON string. ...and an unquoted value treats a backslash-escape as a UNIT,
    # so a `\"` inside it neither ends it at the quote nor loses the escape that
    # keeps the serialized string balanced.
    r'|(?P<struct>' + _STRUCT_VALUE + r")"
    r'|(?P<plain>(?:\\[^"\']|[^\s,}&"\'\\{[])+(?:[^\s,}&"\'\\]*)?)'
    r')',
    re.I,
)
# The same vocabulary as a WHOLE key name, for a structured dict entry: a value
# under `{"access_token": …}` is a credential even though no `=`/`:` sits
# between them in any single string.
_SECRET_KEY_NAME_RE = re.compile(
    r"^(?:" + "|".join(_SECRET_KEY_NAMES) + r")$", re.I)


def _is_secret_key(key) -> bool:
    """Does this dict KEY name a credential, whatever its value's type?"""
    return (isinstance(key, str)
            and bool(_SECRET_KEY_NAME_RE.match(key.strip().strip("\"'"))))
# A bare credential by shape (no accompanying key): a pasted tt_/tk_ key or JWT.
# tk_ is a REAL minted prefix (tortoise/auth.py::API_KEY_PREFIXES) — redacting
# only tt_ left scoped/graph keys in the artifact.
_SECRET_TOKEN_RE = re.compile(
    r"(?:tt|tk)_[A-Za-z0-9_]{8,}"
    r"|ey[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]+)?"
)
# A JSON SCALAR — a number, `null`, `true`, `false`. An unquoted value under a
# sensitive key may be one of these inside a recorded JSON BODY, where the
# replacement must still be a valid JSON value for that body to re-parse.
_JSON_SCALAR_RE = re.compile(
    r"(?:-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|null|true|false)\Z", re.I)






def _quote_states(text: str) -> list:
    """``states[i]`` = the quote holding position ``i`` (None outside), in ONE pass.

    The per-match scan this replaces was O(n) PER MATCH, so a quote-dense field
    — browser- and exception-controlled — was quadratic: a 40 KB
    `'token="' * N` field took ~45 s, which is a stall vector the instrument
    cannot afford. Built once per pass, a lookup is O(1), and the semantics are
    exactly the scan's: ``states[i]`` is the state BEFORE consuming ``text[i]``,
    with a backslash-escape run kept inside the string it sits in.
    """
    n = min(len(text), _SCRUB_TEXT_LIMIT)
    states: list = [None] * (n + 1)
    quote = None
    i = 0
    while i < n:
        ch = text[i]
        states[i] = quote
        if ch == "\\":
            if i + 1 <= n:
                states[i + 1] = quote
            i += 2
            continue
        if quote is None:
            if ch in "\"'":
                quote = ch
        elif ch == quote:
            quote = None
        i += 1
    if n <= len(text):
        states[n] = quote
    return states


def _enclosing_quote(text: str, pos: int):
    """The quote character of the string ``pos`` sits inside, or None.

    One forward scan from the start of the field, because a quote's meaning is
    decided by the string it sits in and by the field's own escaping: a `\\"`
    is CONTENT, a bare `"` opens or closes. A fixed-width lookbehind cannot see
    that. Kept for a single lookup; a pass over many positions uses
    `_quote_states` once, which is the SAME state at O(1) per lookup.
    """
    return _quote_states(text[:pos])[-1] if pos > 0 else None


def _at_json_value_position(match: re.Match, states=None) -> bool:
    """Does this match sit where a JSON VALUE may sit?

    The same `key: scalar` text means two different things: in `{"token": null}`
    the marker must be a STRING (a bare `[REDACTED]` is not valid JSON), while in
    `{"note": "retried, token: 3 times"}` — a `:` inside a recorded body's
    STRING — adding quotes closes the string early and breaks the body. The tell
    is therefore twofold: the pair must NOT sit inside the field's own quoting,
    and the character before the KEY must be `{`, `[` or `,` (behind any
    quoting/escaping). An ESCAPED key quote (`{\\"token\\": null}`) belongs to a
    body that was itself serialized as a string, so it is judged by the walk
    alone: its escapes are the inner document's, and skipping them reaches the
    inner structure.
    """
    # The VALUE's position, bound unconditionally: the value-anchored fallback at
    # the end of this function runs for an ESCAPED key too, and binding `at`
    # inside the branch below left it unbound there — an `UnboundLocalError` on
    # valid JSON, which aborted the artifact write, the opposite of the guarantee
    # this pass exists to keep.
    at = (match.start("plain") if match.group("plain") is not None
          else match.start())
    if not (match.group("key") or "").startswith("\\"):
        # The string state is read at the VALUE, not at the match start: a key
        # match can begin INSIDE a string and still carry a real JSON value
        # (`{" secret": null}` — the walk matches `secret"` inside the key
        # string, but the scalar sits after that string closed). Judging by the
        # match start returned False there and emitted a BARE marker into a JSON
        # value position — valid JSON in, invalid JSON out. What the state has
        # to distinguish is free TEXT (`"retried, token: 3 times"`, where the
        # scalar is inside the string and quotes would close it early).
        if states is not None:
            here = states[at] if at < len(states) else None
        else:
            here = _enclosing_quote(match.string, at)
        if here:
            return False
    i = match.start() - 1
    while i >= 0 and match.string[i] in " \t\r\n\\\"'":
        i -= 1
    if i < 0 or match.string[i] in "{[],":
        return True
    # A key match can begin INSIDE a string and still precede a real JSON value —
    # `{"  client_secret": 5}` matches `secret"` within the key string, so the
    # position is not a key START and the walk above stops on `_`. Read the pair
    # from the VALUE instead: a `:` immediately before it, and a structural
    # opener behind the key. Guarded by the string state, so free text (whose
    # value sits inside a string) has already returned above.
    if (states is not None and match.start() < len(states)
            and states[match.start()]):
        j = at - 1
        while j >= 0 and match.string[j] in " \t\r\n":
            j -= 1
        if j >= 0 and match.string[j] == ":":
            j -= 1
            while j >= 0 and match.string[j] not in "{[,:":
                j -= 1
            return j >= 0 and match.string[j] in "{[,:"
    return False


def _marker_leads(rest: str) -> bool:
    """Does ``rest`` open with our own marker — possibly behind the quote (and
    escape run) an UNTERMINATED quoted value keeps?

    The walk writes the artifact twice, so the pass must be a FIXED POINT: a
    second pass sees ``…token=<esc>"[REDACTED]``, where the escape run ALONE
    looks like an unquoted value. Recognising it as our own output is what
    stops the marker being duplicated on the second write (review cycle 11).
    """
    if rest.startswith(_URL_REDACTED):
        return True
    body = rest.lstrip("\\")
    return body[:1] in "\"'" and body[1:].startswith(_URL_REDACTED)


def _redact_secret_value(match: re.Match, states=None) -> str:
    head = f"{match.group('key')}{match.group('sep')}"
    esc = match.group("esc") or ""
    q = match.group("q") or ""
    if q:
        # Our own output must be a FIXED POINT (a settled walk writes twice).
        if match.string.startswith(f"{head}{esc}{q}{_URL_REDACTED}", match.start()):
            return match.group(0)
        # The opening quote (and its escape run) is KEPT, and the closing quote
        # only when the value actually had one — an unterminated value is
        # redacted in place rather than silently completed into a malformed pair.
        close = f"{esc}{q}" if match.group("tval") is not None else ""
        return f"{head}{esc}{q}{_URL_REDACTED}{close}"
    if match.group("q3"):
        # A quote that opened a value NOTHING closes (a cut body, a truncated
        # URL): the opening quote is kept and no close is invented — completing
        # the pair would fabricate a value the record never had.
        esc3 = match.group("esc3") or ""
        q3 = match.group("q3")
        if match.string.startswith(f"{head}{esc3}{q3}{_URL_REDACTED}",
                                   match.start()):
            return match.group(0)
        return f"{head}{esc3}{q3}{_URL_REDACTED}"
    if match.group("q2"):
        # A terminated quoted value that started on a delimiter/whitespace:
        # the markers keep the quotes so the pair stays balanced. Our OWN
        # settled output (`token:"}?code=[REDACTED]"`) is a fixed point.
        if _QUERY_MARKER_TAIL_RE.match(match.group("tval2") or ""):
            return match.group(0)
        q2 = match.group("q2")
        esc2 = match.group("esc2") or ""
        if match.string.startswith(f"{head}{esc2}{q2}{_URL_REDACTED}",
                                   match.start()):
            return match.group(0)
        return f"{head}{esc2}{q2}{_URL_REDACTED}{esc2}{q2}"
    struct = match.group("struct")
    if struct:
        # An EMPTY structure is this branch's own settled output (`[]`/`{}`), and
        # an already-emitted marker in brackets is the query pass's: re-redacting
        # either would make the pass non-idempotent.
        if struct in ("[]", "{}", _URL_REDACTED):
            return match.group(0)
        return f"{head}{'[]' if struct[0] == '[' else '{}'}"
    plain = match.group("plain")
    if plain is not None and not plain.strip("\\") \
            and _marker_leads(match.string[match.end():]):
        # An escape RUN that leads an already-emitted marker is not a value:
        # the `plain` branch would strip the escapes and re-redact the marker
        # on the second write (review cycle 11).
        return match.group(0)
    if plain is not None and ":" in match.group("sep") \
            and _JSON_SCALAR_RE.match(plain) \
            and _at_json_value_position(match, states):
        # A JSON PAIR (`{"token": null}`) whose value is a scalar, not a
        # credential: the marker replaces it AS A STRING, so the record stays
        # parseable. Only at a JSON VALUE position — an `=` assignment, or a
        # `:` inside a recorded body's STRING (`"retried token: 3 times"`), is
        # free text where adding quotes corrupts the body (review cycles 10/16).
        # The quotes carry the body's OWN escaping, so a double-serialized
        # record (`{\"token\": null}`) keeps its inner body parseable too.
        esc = re.match(r"\\*", match.group("key")).group(0)
        return f'{head}{esc}"{_URL_REDACTED}{esc}"'
    return f"{head}{_URL_REDACTED}"


def _redact_tokens(text: str) -> str:
    """The shape-only pass: a bare ``tt_``/``tk_`` key or JWT redacted."""
    return _SECRET_TOKEN_RE.sub("[REDACTED]", text)


# #5002 — a recorded URL is a credential carrier. The signup flow lands on
# `…?code=<oauth code>` (or `…#access_token=…`), and a one-time/signed link
# carries its own token, so the URL is the one recorded value the free-text
# passes above cannot fully cover: `_SECRET_KEY_RE` redacts by parameter NAME,
# and a signed link's names are chosen by the SIGNER, not by us.
#
# The query string of a URL the matchers CAN classify is DROPPED WHOLE rather
# than filtered by name — a name allow-list protects exactly the names someone
# thought of. Scheme, host and path survive, so the record still says WHERE the
# flow landed, and the query's PRESENCE survives as `?[REDACTED]`, so it still
# says the redirect carried one. Userinfo is dropped too (an authority can carry
# `user:pass`). Only a credential reference with NO url shape left to classify
# (`?code=…` / `#session=…` in bare prose) falls back to the named list below.
# Dropping is IDEMPOTENT, which matters because a settled walk writes the
# record twice (see `_write_observation`).
_URL_REDACTED = "[REDACTED]"

# Our own settled output for a query value, seen from OUTSIDE as a quoted value
# (`token:"}?code=[REDACTED]"` — the query pass ran first): only a query NAME
# bound to the marker, with nothing but punctuation in front, is skipped — a
# value merely ENDING in the marker (` SECRET[REDACTED]`, `}SECRET?code=…`)
# must still be redacted.
_QUERY_MARKER_TAIL_RE = re.compile(
    r"^[^\w?#&\s\"']*[?&#][^&#\s\"'=]*=" + re.escape(_URL_REDACTED) + r"$")
# A URL inside free text. Stops at whitespace, at `)`/`>` (the end of a link in
# prose or a tag), at a quote, and at a BACKSLASH — which may be the escape of
# the very quote that ends the enclosing JSON string, so consuming it would
# leave that quote bare and break the serialization.
_URL_IN_TEXT_RE = re.compile(r"(?:https?|ftps?|file|wss?)://[^\s\"'<>\\)\\]+", re.I)
# After a URL's query/fragment has been DROPPED, a QUOTED value that followed
# its `=` is left stranded (`…?[REDACTED]"SECRET"`): the URL matcher stops at
# the quote, so the credential sits outside it. This removes that remnant
# (plain or JSON-escaped). It is anchored to the marker this module itself
# writes and bounded to a token-shaped value, so a JSON separator
# (`…?[REDACTED]","status": 200`) is never touched — and, being anchored, it
# cannot rescan the tail the way a `https?://…[?#]…=` prefix search does.
# A backslash inside the value body is only ever consumed as part of an ESCAPE
# PAIR, never alone: a lone one may be the escape of the string's closing
# quote, and eating it would unbalance the JSON.
_ORPHAN_QUOTED_VALUE_RE = re.compile(
    r"(?P<marker>[?#]" + re.escape(_URL_REDACTED) + r")"
    r"(?P<esc>\\{0,32})(?P<q>[\"'])"
    r"(?:(?P<tval>(?:(?!(?P=q))[^\s,{}\\\\]|\\.)*)(?P=esc)(?P=q)"
    r"|(?P<oq>[^\s,;:)\]}{\\\"']+))",
)


def _redact_orphan_value(match: re.Match[str]) -> str:
    """Keep the marker and the opening quote; drop the value."""
    esc, q = match.group("esc"), match.group("q")
    head = match.group("marker")
    if match.string.startswith(f"{head}{esc}{q}{_URL_REDACTED}", match.start()):
        return match.group(0)          # already redacted — a fixed point
    close = f"{esc}{q}" if match.group("tval") is not None else ""
    return f"{head}{esc}{q}{_URL_REDACTED}{close}"
# The reference SHAPES the absolute matcher does not claim, but which still
# carry a query/fragment: a protocol-relative `//host/…` (which also covers any
# UNLISTED `scheme://host/…` from its `//`), a root/path-relative `/path?…` (or
# `/path#…`), and a non-http scheme. Claiming them means their WHOLE
# query/fragment is dropped, so a parameter name the SIGNER chose
# (`X-Amz-Signature`, `X-Goog-Signature`) is covered without the named list
# below having to know it. The scheme is NOT a `[a-z][a-z0-9+.-]*` prefix: that
# backtracks quadratically over a long letter run with no `://` after it.
_RELATIVE_URL_IN_TEXT_RE = re.compile(
    r"//[^\s\"'<>\\)\\]+"
    r"|/[^\s\"'<>\\)?#\\]*(?:\?[^\s\"'<>\\)#\\]*)?(?:#[^\s\"'<>\\)\\]*)?",
    re.I,
)

# The named query parameters the issue lists, applied to whatever the two
# matchers above did NOT classify — a bare `?code=…`/`#session=…` with no URL
# shape at all. In free text these names are too broad to redact outright
# (`status_code=200` is diagnostic and must survive), but after a `?`, `&` or
# `#` they are a query/fragment parameter and never prose. Longest-first so a
# prefix alternative cannot win.
_QUERY_SECRET_PARAMS = (
    "access_token", "refresh_token", "session_token", "id_token",
    "api_key", "apikey", "signature", "password", "passwd",
    "token", "session", "secret", "code", "sig", "key",
)
_QUERY_ONLY_NAMES = tuple(
    n for n in _QUERY_SECRET_PARAMS if n not in _SECRET_KEY_NAMES)
_QUERY_PAIR_AHEAD = (r"(?!\s[?&#]?(?:" + "|".join(_QUERY_SECRET_PARAMS)
                     + r")\s*[:=])")

# A `key="` whose quote CLOSES the string the pair sits in has no value at all.
# `_SECRET_KEY_RE` and `_QUERY_SECRET_RE` cannot see that: every branch reads the
# quote as the value's OPENER, and the one that WINS is the one whose run
# reaches the next quote — so `{"a": ["https://h/cb?token=", 1, 2]}` lost the
# whole `, 1, 2]` tail and the following key with it. Which quote is a closer is
# decided by the string state at that position, and no fixed-width lookbehind
# can see it, so it is resolved HERE, before the regexes: the pair gets a marker
# and the quote is left exactly where it is. An ESCAPED quote is content
# (`…?code=\"SEC\"`), never a closer, and a quote that OPENS a value (the
# state at `{"token": "x"}` is None) is left to the regexes.
_CLOSER_PAIR_RE = re.compile(
    r"(?:(?P<lead>[?&#])(?P<qkey>" + "|".join(_QUERY_ONLY_NAMES) + r")|"
    r"(?P<key>(?:\\{0,32}[\"'])?(?:" + "|".join(_SECRET_KEY_NAMES)
    + r")(?:\\{0,32}[\"'])?))"
    r"(?P<sep>\s*[:=]\s*)(?P<esc>\\{0,32})(?P<q>[\"'])", re.I)


def _redact_enclosing_closer(text: str) -> str:
    """A `key="`/`?code="` whose quote CLOSES the enclosing string has no value.

    Called before both name passes, so a valueless pair can never be read across
    the JSON structure that follows it (see `_CLOSER_PAIR_RE`). The quote state
    is built ONCE for the whole field — a scan per match made a quote-dense
    field quadratic.
    """
    states = _quote_states(text)
    limit = len(states) - 1

    def repl(m: re.Match) -> str:
        if m.group("esc"):
            return m.group(0)          # an escaped quote is CONTENT, not a closer
        qpos = m.start("q")
        if qpos >= limit:
            return m.group(0)          # past the pass's own bound: not classified
        if states[qpos] != m.group("q"):
            return m.group(0)          # not inside a string of its own kind
        # The pair has no value only when JSON STRUCTURE follows the closer
        # (`}, "id": 2`, `, 1, 2]`). A value-shaped run — even one behind
        # whitespace, as in `" CANARY"` — is a value the passes below can still
        # redact, and writing a marker here would strand it beside the marker.
        i = m.end()
        while i < limit and m.string[i] in " \t\r\n":
            i += 1
        if i >= limit:
            pass                       # end of field: nothing follows to redact
        elif m.string[i] not in ",}]:":
            return m.group(0)          # a value-shaped run follows
        else:
            # A structural character follows — but a BARE value standing between
            # it and an unbalanced quote (`"token=",CANARY"`) is malformed, and
            # the passes below redact it, so leave the pair whole. Structure
            # DIRECTLY after it — a quote, or a nested `{`/`[`/`,`/`:` — is a new
            # key or element, so the pair really is valueless. Judging by "any
            # quote within 257 chars" instead read a nested object's key quote as
            # that bare value and left `["?token=", {"a": 1}]` to be corrupted.
            # A `{`/`[` IMMEDIATELY after the closer stays value-shaped: a
            # BRACKET value is emptied by the passes below, and calling it
            # structure here wrote the marker in FRONT of a value that then
            # survived beside it (`"#access_token = "[CANARY]"`).
            k = i + 1
            while k < limit and m.string[k] in " \t\r\n":
                k += 1
            if k < limit and m.string[k] not in "\"'{[,:":
                stop = min(limit, k + 256)
                while k < stop and m.string[k] not in ",}]\"":
                    k += 1
                if k < limit and m.string[k] == "\"":
                    return m.group(0)
        # The closing quote is RE-EMITTED: it belongs to the enclosing string,
        # not to the pair, so the marker goes before it.
        return (f"{m.string[m.start():m.start('esc')]}{_URL_REDACTED}"
                f"{m.group('esc')}{m.group('q')}")
    return _CLOSER_PAIR_RE.sub(repl, text)


_QUERY_SECRET_RE = re.compile(
    r"(?P<lead>[?&#])"
    r"(?P<key>" + "|".join(_QUERY_SECRET_PARAMS) + r")"
    # The separator carries any GAP before the value: `?code= "SEC"` is a
    # credential with whitespace BETWEEN `=` and the quote, which the named pass
    # covers for its own vocabulary but the query-only names had no cover for
    # (review cycle 15).
    r"(?P<sep>=\s*)"
    # A quoted value (plain or JSON-escaped) consumed as a unit; else the
    # unquoted run up to a delimiter — with a backslash-escape taken as a UNIT
    # so a `\"` in the value neither ends it nor loses its escape.
    # The quoted run is whitespace-bounded AND must end at a value boundary, so a
    # JSON snippet like `"…?code=", "status": 200` is not read across its
    # structure. An UNTERMINATED quote (a truncated URL, an exception message)
    # must not fall through to the empty unquoted match and leave the value
    # behind, so `oq` takes a NON-EMPTY TOKEN-SHAPED run to the boundary with
    # the opening quote kept. Non-empty, and no delimiter/brace/quote in the
    # class, is what keeps a JSON string's CLOSING quote (`"…?token=", "status"`)
    # or a following key from being read as part of an unterminated value.
    # The quoted run consumes to its MATCHING quote, whatever it holds: the same
    # run must cover a value that legitimately contains JSON punctuation
    # (`?code=\":hunter2\"`), and narrowing it below a VALUE-shaped set let the
    # shorter alternatives win with a PREFIX of the value — an unterminated
    # marker with the credential's tail standing beside it. The valueless-pair
    # shape (`?token=", "status"`) is held off this branch by the `q2` lookahead
    # below and by `_redact_enclosing_closer`, which are the guards for it.
    r"(?:(?P<esc>\\{0,32})(?P<q>[\"'])"
    r"(?:(?P<tval>(?!(?P=q))[^\s\\]|\\.)*(?P=esc)(?P=q)"
    r"(?=[\s,;:)\]}&\"']|$)|(?P<oq>[^\s,;:)\]}{\\\"']+(?:" + _QUERY_PAIR_AHEAD + r"[^,}&\"\'\\])*)|(?P<oq2>(?!\s*[,;:]\s*[\"\'\[{])\s*[^\s\"\'}\]]+))"
    # The unquoted run treats a backslash-escape as a unit, takes LEADING
    # WHITESPACE (a URL truncated at a space still names its value), allows
    # `<`/`>` (the matchers stop at an angle bracket, which would otherwise
    # leave the value as the next token — `…?code=<SECRET`, review cycle 11),
    # and allows a CLOSING bracket (`?code=]x`) — while a value-OPENING
    # bracket stays structure, handled by the branch below, because swallowing
    # it would corrupt an embedded body.
    # ...or a TERMINATED quoted value starting on a delimiter/whitespace
    # (`?code=" S3CRET"`), which the branch above cannot start on — while the
    # same RUN of delimiters before a quote (`?code=", "status"`) is the
    # string's own structure and keeps the branch off it.
    r'|(?P<esc2>\\{0,32})(?P<q2>["\'])(?!(?:\s*[,;:}\]\{]\s*)+["\'])'
    r'(?P<tval2>(?:(?!(?P=q2))[^\\]|\\.)*)(?P=esc2)(?P=q2)'
    r"|(?P<struct>" + _STRUCT_VALUE + r")"
    # ...or an UNMATCHED opening bracket RUN (`?code=[x`) — the value may start
    # with one, and a PAIRED run never reaches here (the branch above wins). It
    # stops at a quote, so it cannot swallow an enclosing JSON string's close.
    r"|(?P<open>[\[{][^&#\s\"\'\\{}\[\]]*)"
    # The unquoted tail stops at the NEXT-PARAM and FRAGMENT delimiters
    # (`&`, `#`) as well: consuming them took the fragment's own `#` with it, so
    # the record lost the fact that the redirect carried one
    # (`…?code=X#access_token=Y` came out `?[REDACTED]` instead of
    # `?[REDACTED]#[REDACTED]`).
    r"|(?P<uval>\s*(?:\\[^\"\']|[^&#\s\"\'\\\{\[])*(?:" + _QUERY_PAIR_AHEAD + r"[^,&#}\"\'\\])*))",
    re.I,
)


def _redact_query_secret(match: re.Match) -> str:
    head = f"{match.group('lead')}{match.group('key')}{match.group('sep')}"
    if match.group("q"):
        # The opening quote (and its JSON escape, if any) is KEPT — and the
        # closing quote only when the value actually had one, so an
        # unterminated value is redacted in place rather than silently
        # completed into a malformed pair.
        esc, q = match.group("esc"), match.group("q")
        if match.string.startswith(f"{head}{esc}{q}{_URL_REDACTED}", match.start()):
            return match.group(0)      # already redacted — a fixed point
        close = f"{esc}{q}" if match.group("tval") is not None else ""
        return f"{head}{esc}{q}{_URL_REDACTED}{close}"
    if match.group("q2"):
        q2 = match.group("q2")
        esc2 = match.group("esc2") or ""
        if match.string.startswith(f"{head}{esc2}{q2}{_URL_REDACTED}",
                                   match.start()):
            return match.group(0)
        return f"{head}{esc2}{q2}{_URL_REDACTED}{esc2}{q2}"
    if match.group("open"):
        # An unmatched opening bracket run: EMPTIED to the bare marker (no
        # quotes, so an enclosing JSON string survives).
        return f"{head}{_URL_REDACTED}"
    struct = match.group("struct")
    if struct:
        # A bracketed value is EMPTIED to the marker — no quotes, so it can
        # never corrupt an enclosing JSON string, and the credential goes.
        return f"{head}{_URL_REDACTED}"
    uval = match.group("uval")
    if uval is not None and not uval.strip("\\") \
            and _marker_leads(match.string[match.end():]):
        # A run of ESCAPE characters alone is not a value: when the marker
        # follows it, this is our own settled output for an UNTERMINATED
        # quoted value, and taking the run as the value again would duplicate
        # the marker on the second write.
        return match.group(0)
    return f"{head}{_URL_REDACTED}"


def scrub_url(url: str, limit: int = 2000) -> str:
    """A recorded URL with its credential-carrying parts REMOVED.

    Drops userinfo, the query string and the fragment; keeps scheme, host and
    path. ``_redact_tokens`` then covers a credential that rode in the PATH
    (a one-time ``/verify/tt_…`` link). Built on the module's existing
    redaction — the free-text pass cannot know every credential-bearing query
    parameter name, which is why the whole query goes, not just its values.

    ``limit`` bounds the HEAD (scheme/host/path), and the markers are appended
    AFTER the cut, so the result is IDEMPOTENT: a truncated `…?` alone would
    re-scrub to a different string on the walk's second write. The markers may
    therefore push the result slightly past ``limit`` — a credential-free read
    is worth a few bytes.
    """
    raw = str(url or "")
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        # A malformed URL (an unbracketed/typo'd IPv6 authority) still cannot
        # keep its query: `urlsplit` refuses it, so the drop is done by hand
        # rather than falling through with the credential intact.
        # The same userinfo drop the parsed path does (an authority can carry
        # `user:pass`, and a malformed one must not be the way it survives),
        # and the same `?[REDACTED]` marker, so the record still says the
        # redirect carried a query.
        head = raw.split("?", 1)[0].split("#", 1)[0]
        suffix = ("?" + _URL_REDACTED if "?" in raw else "") \
            + ("#" + _URL_REDACTED if "#" in raw else "")
        authority, slash, rest = head.partition("//")
        if slash:
            rest = rest.rpartition("@")[2]
            head = authority + slash + rest
        head = _redact_tokens(head)
        if len(head) + len(suffix) > limit:
            head = head[:max(0, limit - len(suffix))]
        return head + suffix
    if not parts.query and not parts.fragment and "@" not in parts.netloc:
        # NOTHING to drop: return the ORIGINAL text. Rebuilding it through
        # `urlunsplit` is not a round trip for every form (`//` becomes ''), and
        # that normalisation made a second pass over already-scrubbed text
        # produce a different string (review cycle 10).
        return _redact_tokens(raw[:limit])
    netloc = parts.netloc.rpartition("@")[2] if "@" in parts.netloc else parts.netloc
    head = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    suffix = ("?" + _URL_REDACTED if parts.query else "") \
        + ("#" + _URL_REDACTED if parts.fragment else "")
    head = _redact_tokens(head)
    if len(head) + len(suffix) > limit:
        head = head[:max(0, limit - len(suffix))]
    return head + suffix


def scrub_urls(text: str) -> str:
    """``scrub_url`` applied to every URL (or URL-shaped reference) in a text.

    The ``signup_responses`` record rides inside a step's ``detail`` as a JSON
    STRING, so a sweep that only visited a field NAMED ``url`` would miss it.
    Applied by ``scrub`` (so every existing call site gains URL safety) and by
    the one serializer (``_scrub_record``), which is the guarantee.

    The absolute matcher runs first, then the relative/non-http SHAPES (their
    whole query goes too), then a quoted value stranded by a dropped query, and
    finally the named parameters for a bare `?code=…` / `#session=…` that has
    no URL shape at all.
    """
    # The query pass's own `key="`-closes-a-string case is resolved first: this
    # is the entry point `_scrub_text` and every direct caller reach.
    t = _redact_enclosing_closer(str(text or ""))
    # The named-param pass runs FIRST. A URL matcher STOPS at a quote,
    # backslash, `<`, `>` or space, so it can consume the `?code=` LEAD and
    # rewrite only the query — after which the named pass has nothing to match
    # and the value survives (`…?code=\SECRET`, review cycle 11). The value is
    # redacted here, on the raw text, before any rewrite can hide the pair.
    t = _QUERY_SECRET_RE.sub(_redact_query_secret, t)
    t = _URL_IN_TEXT_RE.sub(lambda m: scrub_url(m.group(0)), t)
    t = _RELATIVE_URL_IN_TEXT_RE.sub(lambda m: scrub_url(m.group(0)), t)
    return _ORPHAN_QUOTED_VALUE_RE.sub(_redact_orphan_value, t)


# The serializer's own bound. The URL matchers are not linear on pathological
# input (`"https://" * 8000`, or a lone unterminated `?code="`), and the fields
# they run over are browser/exception controlled — a megabyte page body must not
# stall the walk. A credential past this point is DROPPED (the string is cut),
# never scanned for, so bounding is safe as well as fast.
_SCRUB_TEXT_LIMIT = 40_000


def _bracket_end(text: str, start: int):
    """The index of the bracket that CLOSES the one at ``start`` (or None).

    A hand-written scan, because nesting is not a regular language: no regex can
    take `[["tok"]]` or `{"a": {"b": "tok"}}` as ONE value, and matching a
    single-level bracket only would let a nested secret ride through the
    serializer to the uploaded artifact (review cycle 12 — a fail-open
    regression of the pre-fix control, which redacted it). Escape runs and
    quoted runs are skipped as units, so a re-serialized body's `\\"tok\\"`
    counts as content and a `]` inside a string does not close the value.
    """
    pairs = {"[": "]", "{": "}"}
    stack: list[str] = []
    i, n = start, len(text[:_SCRUB_TEXT_LIMIT])
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2                       # an escape pair is a unit
            continue
        if ch in "\"'":
            quote, i = ch, i + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                i += 1
                if text[i - 1] == quote:
                    break
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in "]}":
            if not stack or stack.pop() != ch:
                return None
            if not stack:
                return i
        i += 1
    return None


_JSON_TAIL_RE = re.compile(r'''^[\s"'}\],:]*$''')


def _is_json(text: str) -> bool:
    """Is this field a JSON document? A valid one holds its brackets inside
    STRINGS, so an unmatched bracket may not eat the rest of it."""
    try:
        json.loads(text)
    except Exception:
        return False
    return True


def _unbalanced_end(text: str, start: int, valid: bool) -> int:
    """Where an UNMATCHED bracket value ends: the end of the field, OR the start
    of the string that closes the one the bracket sits in.

    Emptying to the end is right for a field CUT mid-container (or a broken one):
    everything after an unmatched `[` is textually inside it, so a credential
    later in the field is still removed, and there is no document left to
    preserve. But a field that IS a JSON document holds its brackets inside
    STRINGS — an unmatched one there is PROSE, and eating the closing `"}` would
    break a body that was fine (review cycle 13/14). For those, stop at the
    string's own close: a quote whose remainder is a JSON continuation (`,`, `}`,
    `]`) or the end of the field.
    """
    if not valid:
        return len(text)
    k, n = start + 1, len(text)
    while k < n:
        if text[k] == "\\":
            # An escape PAIR is a unit: the character an odd backslash run
            # escapes is CONTENT, and reading its quote as the string's close
            # returned an index that deleted the backslash and left the quote
            # bare — valid JSON in, invalid JSON out.
            k += 2
            continue
        if text[k] in "\"'":
            rest = text[k + 1:].lstrip()
            if not rest or rest[0] in ",}]" or _JSON_TAIL_RE.match(text[k:]):
                return k
        k += 1
    return n


def _empty_bracket_values(text: str, names, require_lead: bool) -> str:
    """Empty a bracketed VALUE under a secret-named key, in place.

    `{"token": [1, 2]}` and `?token=[1, 2]` both hold the credential INSIDE the
    brackets, so the value is emptied to `[]`/`{}` — valid JSON of the same
    shape, never a quoted marker (which would corrupt the string it sits in) and
    never left intact (which is how a nested list reached the artifact).
    ``require_lead`` needs a `?`/`&`/`#` before the name, so this can cover the
    query-only names (`code`, `key`, `session`) without firing on a body's
    `{"code": …}`.
    """
    lead = r"(?P<lead>[?&#])" if require_lead else r"(?P<lead>[?&#])?"
    pattern = re.compile(
        lead + r"(?P<key>" + "|".join(names) + r")(?:\\{0,32}[\"'])?"
        r"\s*[:=]\s*(?P<open>[\[{])", re.I)
    out, pos, balanced = [], 0, None
    for m in pattern.finditer(text):
        if m.start() < pos:
            continue
        if require_lead and not m.group("lead"):
            continue
        start = m.end() - 1
        end = _bracket_end(text, start)
        if end is None:
            if balanced is None:
                balanced = _is_json(text)
            # An UNTERMINATED bracket — a field CUT mid-value (the caller's cap,
            # review cycle 13), or a truncated body. How far it reaches is
            # decided by `_unbalanced_end`: to the end of a broken field
            # (everything after an unmatched `[` is textually inside it, and
            # leaving it to the regex passes leaked — neither `plain` nor `oq`
            # takes a bracket, and `struct` needs a close), or only to the end
            # of the STRING it sits in when the field is valid JSON, whose
            # brackets are inside strings. A scan that emptied to the end in
            # that case destroyed the body (review cycle 14).
            end = _unbalanced_end(text, start, balanced)
            out.append(text[pos:start])
            out.append("[]" if text[start] == "[" else "{}")
            pos = end
            if end >= len(text):
                break
            continue
        if text[start:end + 1] == _URL_REDACTED:
            continue                    # our own marker: a fixed point
        out.append(text[pos:start])
        out.append("[]" if text[start] == "[" else "{}")
        pos = end + 1
    out.append(text[pos:])
    return "".join(out)


def _scrub_text(text: str) -> str:
    """The full redaction pass — the serializer's own entry point.

    ``scrub`` is this plus the caller's artifact cap; the serializer uses this
    so a value redaction is never skipped merely because a string was not
    routed through `scrub` at its call site. Its own ``_SCRUB_TEXT_LIMIT``
    bound applies FIRST, so the nonlinear URL matchers never see unbounded
    browser/exception text.
    """
    t = _redact_enclosing_closer(str(text or "")[:_SCRUB_TEXT_LIMIT])
    # A bracketed value is emptied FIRST, by a balanced scan: the passes below
    # are regexes and cannot take a nested container as one value, so a
    # `[["tok"]]` would otherwise be left whole and reach the artifact.
    t = _empty_bracket_values(t, _SECRET_KEY_NAMES, require_lead=False)
    t = _empty_bracket_values(t, _QUERY_ONLY_NAMES, require_lead=True)
    # Built ONCE for the whole field: the state array is what keeps the
    # value-position check from re-scanning per match (which made a
    # quote-dense field quadratic).
    states = _quote_states(t)
    t = _SECRET_KEY_RE.sub(lambda m: _redact_secret_value(m, states), t)
    t = _redact_tokens(t)
    t = scrub_urls(t)
    # ...again on the FINAL text: a URL rewrite can delete a `[`, which changes
    # a bracket's balance, and a fixed point requires the pair to be judged on
    # the text that is actually written (`token:[/#[)]` — review cycle 13).
    t = _empty_bracket_values(t, _SECRET_KEY_NAMES, require_lead=False)
    return _empty_bracket_values(t, _QUERY_ONLY_NAMES, require_lead=True)


def scrub(text: str, limit: int = 4000) -> str:
    """Keep an artifact, never a credential.

    The REDACTION runs first and the caller's cap is applied LAST. Cutting first
    split a JSON container, and a value whose close fell past the cut was left
    unterminated — which is exactly the shape the passes cannot match, so a
    credential BEFORE the cut reached the artifact (review cycle 13; the
    pre-fix control redacted and then cut). The stall is still bounded: the
    passes run on at most `_SCRUB_TEXT_LIMIT` characters, applied first inside
    `_scrub_text`, and a credential past that bound is dropped.
    """
    return _scrub_text(str(text or ""))[:limit]


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

# The product's own org-name rule, kept in sync with the server
# (`supabase/functions/tenant-provision`: ORG_NAME_RE) and the client
# (`website/apps/dashboard/src/wizardFlow.js::orgNameError`). Both sides TRIM
# first, so this is matched with `fullmatch` against a STRIPPED value — a
# `$`-anchored `match` accepts `"Foo\n"`, and a raw `"Foo "` would be stored as
# `"Foo"`, after which teardown would look for a name that cannot exist and
# leave the created org unreapable. Teardown matches the name this run WROTE, so
# a name the product would rewrite or refuse is rejected up front (exit 2)
# instead of surfacing later as a `name_mismatch`.
#
# DELIBERATELY STRICTER THAN THE PRODUCT AT EXACTLY ONE CODE POINT: Python's
# `str.strip()` and JS's `String.prototype.trim()` disagree on U+FEFF (JS trims
# a BOM, Python does not). `"--org-name \ufeffFoo"` is therefore refused here
# though the product would accept it as `"Foo"`. That is the fail-closed
# direction — no org is created, so there is no residue and nothing left
# unreapable — and DROPPING the strictness would be the unsafe direction, so it
# is recorded rather than papered over with a hand-rolled, drift-prone
# WhiteSpace set. The other direction ("Foo\x85", "Foo\x1c": Python trims,
# JS does not) is harmless, because the instrument types the STRIPPED value and
# the product re-trims what it is given.
ORG_NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}")


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
    verb = method.upper()
    if verb not in ("GET", "POST", "DELETE"):
        # Fail closed. This used to send ANY non-GET as a POST, so a caller
        # asking for DELETE was silently downgraded into a POST against the
        # same path — a wrong-method request the caller still read as a
        # DELETE. An unknown verb is a bug, not something to guess at.
        raise ValueError(f"bff_api: unsupported method {method!r}")
    try:
        if verb == "GET":
            resp = ctx.request.get(url)
        elif verb == "DELETE":
            resp = ctx.request.delete(url)
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


# ── the run's own cleanup (#4319) ───────────────────────────────────────────
# Every per-deploy run creates a REAL production org (`Ship Test <epoch>-<hex4>`) and,
# before this, nothing removed it. The instrument now reaps the org it created —
# as its owner, through the SAME walked session and the SAME origin's BFF proxy
# it already uses — and records the outcome in the observation.
#
# The identity of "the org this run created" is NOT the name. A name match is
# evidence of a name: a fixed `--org-name`, a reused account, or the provisioning
# lane's own upsert could each put this run's name on an org this run never
# created, and the instrument would delete a third party's org. The proof is
# DIFFERENTIAL: the walked session's own org list is read once BEFORE the wizard
# can create anything (`before`) and once at teardown (`after`), so this run's
# orgs are exactly `after - before`. That set must be exactly one org — the name
# is then a second, independent check, never the identity itself.
TEARDOWN_DELETED = "deleted"
TEARDOWN_SKIPPED_NO_ORG = "skipped_no_org"
TEARDOWN_NOT_LISTED = "not_listed"
TEARDOWN_NOT_ATTEMPTED = "not_attempted"
TEARDOWN_KEPT = "kept_by_flag"
TEARDOWN_NOT_REACHED = "not_reached"
TEARDOWN_BASELINE_UNAVAILABLE = "baseline_unavailable"
TEARDOWN_LIST_UNREADABLE = "list_unreadable"
TEARDOWN_AMBIGUOUS = "ambiguous"
TEARDOWN_NAME_MISMATCH = "name_mismatch"
TEARDOWN_HTTP_REFUSED = "http_refused"
TEARDOWN_NOT_CONFIRMED = "not_confirmed"
TEARDOWN_FAILED = "failed"

# The states that mean a live org MAY remain in the target tenant, and so get the
# loud stderr residue warning. `deleted` / `skipped_no_org` / `not_reached` are
# clean — warning on those would be a false alarm, and a false residue alarm is
# the #4291 conflation in reverse.
TEARDOWN_RESIDUE_STATES = (
    TEARDOWN_NOT_LISTED, TEARDOWN_NOT_ATTEMPTED, TEARDOWN_KEPT,
    TEARDOWN_BASELINE_UNAVAILABLE, TEARDOWN_LIST_UNREADABLE, TEARDOWN_AMBIGUOUS,
    TEARDOWN_NAME_MISMATCH, TEARDOWN_HTTP_REFUSED, TEARDOWN_NOT_CONFIRMED,
    TEARDOWN_FAILED,
)


@dataclass
class Teardown:
    """Cleanup state for one run.

    Deliberately NOT serialized — it holds a live request channel. What lands in
    the artifact is the JSON-only summary built by `_run_teardown`.
    """

    org_name: str = ""          # the ONE name this run wrote into the wizard
    base_url: str = ""
    keep: bool = False
    ctx: object = None          # the browser context, once one exists
    # The Browser object, once one was launched (#4907). `browser` used to be a
    # `_walk` local; the ONE teardown site now lives in `run_walk`, so it must be
    # reachable from there. `Teardown` is never serialized (`asdict` is used only
    # on `Verdict` and `Observation`), so holding a live object here is safe.
    browser: object = None
    # The browser teardown's one-shot guard (#4907). Distinct from `done`, which
    # is the ORG reaper's.
    browser_done: bool = False
    # True ONLY after a RECOGNIZED baseline read. A fresh account's correct
    # baseline is a readable EMPTY list; "no baseline" is not "empty", it is
    # "unproven", and an unproven identity must never delete anything.
    enabled: bool = False
    # True once the baseline read was ATTEMPTED. Distinguishes "the list was
    # unreadable" (a real, attributable residue risk) from "the run exited
    # before it ever looked" (which cannot have created anything and must not
    # raise a residue alarm).
    baseline_attempted: bool = False
    reason: str = ""
    before: dict = field(default_factory=dict)   # org_id -> org_name
    # Set immediately BEFORE the create click: a click that raises after
    # dispatching the create must still leave this run's residue visible here.
    create_attempted: bool = False
    done: bool = False
    # True once `_walk` settled into a written pre-teardown document. An
    # unfinalized record (a `BaseException` escaping `_walk`) means `main`
    # returns `EXIT_INSTRUMENT_ERROR`, so the teardown's last resort must exit
    # the same way rather than scoring the default record as a product code.
    finalized: bool = False


def _org_rows(body: object) -> list | None:
    """The org rows of a GET /api/v1/organizations body, or None if unrecognized.

    The live endpoint answers with a BARE list of rows carrying `org_id`. Any
    other shape — a dict with an unexpected key, a list of non-dicts, a row with
    no `org_id` — is NOT recognized. That matters in both directions: a shape
    drift parsed as "no orgs" would silently switch the identity proof off on
    the baseline side and silently confirm a deletion on the verify side.
    Unrecognized ⇒ None ⇒ every caller fails closed.
    """
    rows = body.get("organizations") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or not row.get("org_id"):
            return None
    return rows


def _upstream_status(body: object) -> int | None:
    """The proxy's own `upstream_status` (an upstream 429 arrives as a 503)."""
    if isinstance(body, dict) and isinstance(body.get("upstream_status"), int):
        return body["upstream_status"]
    return None


def read_org_ids(ctx, base_url: str) -> tuple[int, dict | None, int | None]:
    """The walked session's own orgs as ``{org_id: org_name}``.

    Returns ``(status, ids_or_None, upstream_status)``. ``None`` ids means the
    answer was not a recognizable org list, or the read failed — callers treat
    both as "not readable" (fail closed), never as "no orgs".
    """
    status, body = bff_api(ctx, base_url, "GET", "/organizations")
    if status != 200:
        return status, None, _upstream_status(body)
    rows = _org_rows(body)
    if rows is None:
        return status, None, None
    return status, {str(r["org_id"]): str(r.get("org_name") or "") for r in rows}, None


def _run_teardown(obs: Observation, td: Teardown) -> None:
    """Reap the org THIS run created, and record what happened. Never raises.

    The ordering IS the safety argument: prove the org is this run's
    (differential), delete it as its owner through the same origin, then VERIFY
    THE ARTIFACT — a 2xx is not proof, so `deleted` is recorded only after a
    READABLE re-read shows the org gone from the walked session's own list.
    """
    if td.done:
        # One-shot: the ORG teardown is reached from more than one exit, and a
        # second pass would find the org already gone and overwrite recorded
        # evidence with a clean-looking status.
        return
    td.done = True

    if td.keep:
        obs.teardown = {"status": TEARDOWN_KEPT, "org_name": td.org_name,
                        "detail": "--keep-org: the residue is deliberate and countable"}
        return
    if td.ctx is None or not td.baseline_attempted:
        # No baseline was ever READ. Nothing in this run can be attributed to
        # it, and the org is only created by the wizard click that comes AFTER
        # the baseline — so claiming "the org list was unreadable" here would be
        # a residue alarm for a run that never got far enough to create one.
        obs.teardown = {
            "status": TEARDOWN_NOT_REACHED,
            "detail": ("the walk never reached a browser context" if td.ctx is None
                       else "the walk exited before the cleanup baseline was read")}
        return
    if not td.enabled:
        obs.teardown = {"status": TEARDOWN_BASELINE_UNAVAILABLE,
                        "org_name": td.org_name, "detail": td.reason}
        return

    status, after, upstream = read_org_ids(td.ctx, td.base_url)
    if after is None:
        obs.teardown = {"status": TEARDOWN_LIST_UNREADABLE, "org_name": td.org_name,
                        "http_status": status, "upstream_status": upstream}
        return

    created = {oid: name for oid, name in after.items() if oid not in td.before}
    if not created:
        if td.create_attempted:
            # An empty candidate set AFTER a recorded create attempt is not
            # "nothing to do": the create may have landed on a list that has not
            # caught up yet. Suspect residue, never a clean bill of health.
            obs.teardown = {"status": TEARDOWN_NOT_LISTED, "org_name": td.org_name,
                            "before_count": len(td.before), "after_count": len(after)}
        else:
            obs.teardown = {"status": TEARDOWN_SKIPPED_NO_ORG,
                            "org_name": td.org_name}
        return
    if len(created) > 1:
        obs.teardown = {"status": TEARDOWN_AMBIGUOUS, "org_name": td.org_name,
                        "created_ids": sorted(created)}
        return
    if not td.create_attempted:
        # The set difference alone is NOT an identity proof: an org that joined
        # this session's own list without this run EVER asking for one is not
        # this run's to delete. (It appeared after the baseline, so it is in the
        # same disposable account — but "same account" is not "created by this
        # run".) Refuse, and say so as residue rather than as a clean bill.
        obs.teardown = {"status": TEARDOWN_NOT_ATTEMPTED, "org_name": td.org_name,
                        "created_ids": sorted(created)}
        return

    org_id, listed_name = next(iter(created.items()))
    if listed_name != td.org_name:
        obs.teardown = {"status": TEARDOWN_NAME_MISMATCH, "org_id": org_id,
                        "listed_name": listed_name, "org_name": td.org_name}
        return

    status, body = bff_api(td.ctx, td.base_url, "DELETE", f"/organizations/{org_id}")
    upstream = _upstream_status(body)
    if status not in (200, 202):
        obs.teardown = {"status": TEARDOWN_HTTP_REFUSED, "org_id": org_id,
                        "http_status": status, "upstream_status": upstream,
                        "detail": scrub(json.dumps(body), 200)}
        return

    # VERIFY THE ARTIFACT, not the send. The confirm read is authoritative, and
    # an UNREADABLE confirm is NOT a confirmation.
    v_status, verify, v_upstream = read_org_ids(td.ctx, td.base_url)
    if verify is None or org_id in verify:
        obs.teardown = {"status": TEARDOWN_NOT_CONFIRMED, "org_id": org_id,
                        "delete_status": status, "verify_status": v_status,
                        "verify_upstream_status": v_upstream}
        return

    fields = body if isinstance(body, dict) else {}
    obs.teardown = {"status": TEARDOWN_DELETED, "org_id": org_id,
                    "org_name": listed_name, "delete_status": status,
                    "grace_hours": fields.get("grace_hours"),
                    "hard_delete_after": fields.get("hard_delete_after")}


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


# ── the teardown seams ──────────────────────────────────────────────────────
# Module-level indirection points for the teardown's process-facing reads and
# writes. The production bodies below are the only ones that touch the OS; the
# bound is exercised by a fake harness that spawns no real Playwright driver, so
# every one of them is replaceable. A test can then RECORD what the teardown
# asked for (which pid, which signal, which rung) rather than infer it.
TEARDOWN_DRIVER_MARKERS = ("run-driver", "playwright")


def _norm_start_time(text: str) -> str:
    """Collapse whitespace so `lstart` renderings of one process compare equal.

    ``ps`` renders ``lstart`` as ``%c``, whose day-of-month field is SPACE-padded
    (``Thu Jan  1 00:00:00 2026``) and whose token COUNT is LOCALE-DEPENDENT
    (``LC_ALL=ja_JP.UTF-8`` renders four tokens, ``LC_ALL=ru_RU.UTF-8`` six).
    Both the enumeration and the TOCTOU re-check take their start time from
    `_child_identity` and pass it through this ONE normal form, so the two
    compare like with like BY CONSTRUCTION, whatever the locale. Neither read
    reconstructs the time from a positional slice, which is what made the token
    count load-bearing.
    """
    return " ".join(str(text).split())


def _ps_timeout() -> float:
    """The `ps` read's own bound: never more than the ladder's slack.

    The last rung is at 3B/4, so a re-read bounded by B/4 cannot push the
    abandon past the bound.
    """
    return min(_PS_TIMEOUT_S, TEARDOWN_BOUND_S / 4)


def _ps_binary() -> str | None:
    """The absolute ``ps``, or ``None`` when it is absent.

    ``None`` is a deliberate refusal: no pid is enumerated, so no signal is sent.
    Falling back to a PATH ``ps`` would let a planted binary choose the pid the
    watchdog kills.
    """
    if os.path.isabs(_PS_BIN) and os.path.exists(_PS_BIN):
        return _PS_BIN
    return None


# Why the enumerator returned no driver. The OUTCOME vocabulary stays the closed
# six above (`driver_absent` is the outcome for both "not found" reasons); this
# names WHICH nothing it was, so `browser_teardown.detail` can name it instead
# of reporting an enumerated-but-unverifiable child as "no child was
# enumerated".
DRIVER_ENUM_FOUND = "found"
DRIVER_ENUM_NO_CANDIDATE = "no_candidate"
DRIVER_ENUM_IDENTITY_UNREADABLE = "identity_unreadable"


def _child_identity(pid: int) -> tuple[int, str] | None:
    """One process's whole identity, read ATOMICALLY: ``(ppid, start_time)``.

    ONE `ps` selection returns both fields, so the pid and the start time can
    never come from two different processes: a pid reused between two separate
    reads would otherwise yield the SUCCESSOR's start time, the watchdog's
    re-check would re-read that same value and pass, and ``os.kill`` would
    SIGKILL a process that is not this run's child — the exact false positive the
    start time exists to prevent.

    ``ppid`` is the FIRST whitespace-delimited field; the start time is the REST
    of the line, normalised — never a positional token slice, because ``lstart``
    renders as ``%c`` whose token COUNT is locale-dependent (four in ``ja_JP``,
    six in ``ru_RU``).

    ``None`` means the identity could not be read at all this time — ``ps`` was
    absent or timed out, the process exited, or the output was unrecognizable. A
    partial identity is never returned.

    The read takes `-ww` (unlimited width, see `_PS_UNLIMITED_WIDTH`): a start
    time truncated at the venue's column width would make two different
    processes compare equal, which is the pid reuse this atomic read exists to
    defeat — and the re-check compares the SAME reader, so it would agree with
    the truncated value rather than catch it.
    """
    ps = _ps_binary()
    if ps is None:
        return None
    try:
        out = subprocess.run([ps, _PS_UNLIMITED_WIDTH, "-o", "ppid=,lstart=",
                              "-p", str(pid)],
                             capture_output=True, text=True,
                             timeout=_ps_timeout()).stdout
    except Exception:
        return None
    parts = out.split(None, 1)
    if len(parts) < 2:
        return None
    try:
        ppid = int(parts[0])
    except ValueError:
        return None
    start = _norm_start_time(parts[1])
    if not start:
        return None
    return ppid, start


def _driver_pid_and_starttime() -> tuple[int | None, str | None, str]:
    """This run's OWN Playwright driver child: ``(pid, start_time, status)``.

    Enumerated once, at teardown start, as a direct child of THIS process: the
    sync API spawns the driver as a direct child, and E8 measured that killing it
    takes the whole Chromium tree with it.

    ``command`` is the LAST field of the `ps` selection and the driver marker is
    matched ANYWHERE in the command field (a substring test, not a whole-field
    one), so the start time is NOT reconstructed from the enumeration line. Each
    candidate that passes the parent/marker filter is then identified by
    `_child_identity` — the SAME reader the TOCTOU re-check calls — and accepted
    only when that read reports THIS process as its parent. So the value the
    watchdog compares against is the value this enumerator returned, BY
    CONSTRUCTION, in ONE read.

    ``status`` is one of the ``DRIVER_ENUM_*`` constants. It distinguishes "no
    candidate was found" from "a marker-matching child was enumerated but its
    identity could not be read (or no longer verified as this process's child)":
    the OUTCOME vocabulary is unaffected — both are `driver_absent` — but the
    record's `detail` must not call the second one "no child was enumerated".
    The identity read is retried ONCE before giving up, because a single `ps`
    timeout is not evidence about the child.

    The selection takes `-ww` (unlimited width, see `_PS_UNLIMITED_WIDTH`). It
    is load-bearing here: `command` is the unbounded field, and a host that
    truncates it hides the marker of any driver whose
    command line is longer than that width — the CI runner's interpreter path
    alone is ~49 characters, so a trailing ``run-driver`` is past the cut. The
    enumerator then finds no candidate, the driver is never signalled, and a
    healthy run abandons with its Chromium tree live (the same truncation class
    as the embedded reaper's #1365).

    A non-positive pid is never returned: ``os.kill(-1, SIGKILL)`` signals every
    process this uid may signal, so a misread pid must never become the signal
    target.
    """
    me = os.getpid()
    ps = _ps_binary()
    if ps is None:
        return None, None, DRIVER_ENUM_NO_CANDIDATE
    try:
        out = subprocess.run([ps, _PS_UNLIMITED_WIDTH, "-axo",
                              "pid=,ppid=,command="],
                             capture_output=True, text=True,
                             timeout=_ps_timeout()).stdout
    except Exception:
        return None, None, DRIVER_ENUM_NO_CANDIDATE
    unverified = False
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pid <= 0 or ppid != me:
            continue
        if not any(marker in parts[2] for marker in TEARDOWN_DRIVER_MARKERS):
            continue
        identity = _child_identity(pid)
        if identity is None:
            identity = _child_identity(pid)      # one retry, then give up
        if identity is None or identity[0] != me:
            # The marker-matching child could not be verified as this run's child
            # (it exited, `ps` failed, or the pid was reused between the
            # enumeration and the identity read). It must NOT be signalled — and
            # it must NOT be reported as "no candidate found" either.
            unverified = True
            continue
        return pid, identity[1], DRIVER_ENUM_FOUND
    if unverified:
        return None, None, DRIVER_ENUM_IDENTITY_UNREADABLE
    return None, None, DRIVER_ENUM_NO_CANDIDATE


def _send_signal(pid: int, signum: int) -> None:
    os.kill(pid, signum)


def _reap(pid: int) -> None:
    """Reap a signalled child, so the kill leaves no zombie."""
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)


def _monotonic() -> float:
    return time.monotonic()


def _ladder(bound: float) -> list[tuple[float, int]]:
    """The watchdog's rungs, as DATA: ``(delay_seconds, signal)``, in order.

    PURE, so the ARITHMETIC is provable in CI without a browser and without a
    clock (`mutation_selfcheck` exercises it). The shape is deliberate: a
    graceful SIGTERM at half the budget leaves a teardown that can still finish
    half the bound to do it, and SIGKILL at three quarters leaves a quarter as
    slack before the bound expires. The kill is what releases a close blocked on
    an unresponsive driver (E11), so the rungs have to be inside the bound.
    """
    return [(bound / 2, signal.SIGTERM), (3 * bound / 4, signal.SIGKILL)]


def _ladder_is_sound(ladder: list[tuple[float, int]], bound: float) -> bool:
    """The ladder's contract as a PREDICATE, so a mutant is a value.

    Exactly two rungs; delays strictly increasing and strictly inside the bound;
    SIGTERM before SIGKILL; at most one of each; and a bound that is actually a
    bound (``0 < bound <= 60``).
    """
    if not (0 < bound <= 60) or len(ladder) != 2:
        return False
    (d1, s1), (d2, s2) = ladder
    return s1 == signal.SIGTERM and s2 == signal.SIGKILL and 0 < d1 < d2 < bound


_SIGNAL_LABELS = {signal.SIGTERM: "SIGTERM", signal.SIGKILL: "SIGKILL"}


def _wait_until(deadline: float, done: threading.Event) -> bool:
    """Wait for ``deadline`` unless ``done`` is set first.

    True iff the deadline arrived while the teardown was still running. Polled
    with a bounded sleep so a healthy run is released in milliseconds rather than
    waiting out the bound: the whole reason the watchdog does not cost every run
    its budget.
    """
    while not done.is_set():
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return True
        done.wait(min(0.05, remaining))
    return False


# ── the live walk ───────────────────────────────────────────────────────────
def run_walk(args) -> Observation:
    """The run, top to bottom: prep → driver → walk → bounded teardown → write.

    THE PINNED SHAPE. `run_walk` owns the lifecycle and the write; `_walk` owns
    the browser-scoped body. The teardown is entered exactly ONCE, as a statement
    of the only `finally` around the `_walk` call, so it runs for every `_walk`
    return and for an exception out of it. `_finish` is the ONE authoritative
    write+print site and runs AFTER that teardown, so stdout and the file agree.

    Two aborts deliberately sit OUTSIDE the `try` and write NO artifact (#4875):
    `shots.mkdir` raising (an `--out` that cannot be created) and
    `sync_playwright().start()` raising. The playwright IMPORT guard is not one of
    them — it returns through `_start_driver` and still writes.
    """
    out_dir = Path(args.out)
    shots = out_dir / "screenshots"
    # #4875 abort (1/2): raises before any writer exists, so NO artifact is
    # written. The module's "the directory always holds observation.json"
    # promise does not extend to an abort that predates the observation.
    shots.mkdir(parents=True, exist_ok=True)

    email, password = _run_credentials(args)
    obs, td = _build_observation(args, email, password)
    # #4875 abort (2/2): a driver that will not START raises out of here, before
    # any writer exists. Distinct from the import guard below.
    obs, pw = _start_driver(obs, out_dir, td)
    if pw is not None:
        try:
            obs = _walk(pw, args, obs, td, out_dir, shots, email, password)
        finally:
            # THE ONE TEARDOWN SITE, entered exactly once. On a walk that SETTLED
            # it runs after the pre-teardown document has been written by
            # `_finalize`, so a run killed inside it leaves a complete artifact
            # whose `browser_teardown.outcome` is `not_run`; a walk body that
            # raised before it settled wrote no such copy.
            _teardown_browser(pw, td, obs, out_dir)
    # THE ONE AUTHORITATIVE WRITE+PRINT SITE: after the teardown, for every
    # returning path. A `_walk` exception propagates past it (the `finally` has
    # already torn the browser down), which is the killed-inside-the-window case.
    _finish(obs, out_dir)
    return obs


def _run_credentials(args) -> tuple[str, str]:
    """The disposable identity this run signs up — computed ONCE, here.

    One computation, so the address the wizard is typed and the address the
    artifact records cannot drift.
    """
    email = args.email or f"ship-test-{int(time.time())}-{uuid.uuid4().hex[:6]}@premiselabs.co"
    password = args.password or f"ShipTest-{uuid.uuid4().hex[:10]}-Aa1!"
    return email, password


def _build_observation(args, email, password) -> tuple[Observation, Teardown]:
    """The record and the run's cleanup state, built BEFORE the driver exists.

    Both are built here because every exit funnels through them, including the
    ones that predate a browser context. The org NAME is computed once here and
    is BOTH what the wizard is told and what teardown compares against, so the
    two cannot drift.

    ``email``/``password`` are the identity `_run_credentials` computed for this
    run, taken as parameters so the record and the walk share ONE computation;
    this function does not re-derive them.
    """
    obs = Observation(started_at=_now(), target={
        "dashboard": args.base_url, "auth": args.auth_url, "api": args.api_url})
    td = Teardown(org_name=args.org_name
                  or f"Ship Test {int(time.time())}-{uuid.uuid4().hex[:4]}",
                  base_url=args.base_url, keep=args.keep_org)
    return obs, td


def _start_driver(obs, out_dir, td) -> tuple[Observation, object | None]:
    """Import and start the driver, or record the fail-closed import-guard exit.

    FAIL-CLOSED DEFAULT (#4291). Until a path has PROVEN that it measured the
    product, the run says nothing about the product; the default is set HERE,
    before the import, so it precedes everything that can abort. Every
    product-facing exit in `_walk` sets its own reason explicitly.

    A MISSING driver returns `(obs, None)` — it does not raise — after recording
    the ORG teardown through `_run_teardown_safely` (`not_reached`: no browser
    context ever existed). The caller's single `_finish` then writes the
    artifact, so this path costs no second writer.

    `sync_playwright().start()` RAISING is a different thing and is deliberately
    NOT caught: it propagates out of `run_walk` before any writer exists, so that
    abort writes no artifact at all (#4875). The import must stay in its own
    narrow `try` for the two to remain distinguishable.
    """
    obs.reason = REASON_INSTRUMENT_ERROR
    try:
        # Lazy so the core (judge/probe/CLI) stays importable without playwright —
        # but GUARDED, so a missing driver still yields a recorded observation.
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        obs.verdict = f"failed: playwright unavailable: {type(exc).__name__}: {exc}"
        _run_teardown_safely(obs, td)
        return obs, None
    return obs, sync_playwright().start()


def _walk(pw, args, obs, td, out_dir, shots, email, password) -> Observation:
    """The browser-scoped body of the walk: launch → signup → judge.

    Every `return` here exits through `_finalize`, which runs the ORG teardown
    and writes the PRE-TEARDOWN document (complete, with
    `browser_teardown.outcome == "not_run"`).

    The browser's own lifecycle is deliberately NOT here: the bounded teardown
    lives in `run_walk`'s `finally`, the only site that runs for every `_walk`
    return AND for an exception out of this function. `td.browser` is set at
    launch so that site can close what this function opened.
    """
    browser = None
    ctx = None
    page = None

    def shot(page, name: str) -> str:
        p = shots / f"{len(obs.steps):02d}-{name}.png"
        try:
            page.screenshot(path=str(p), full_page=True)
            return str(p.relative_to(out_dir))
        except Exception as exc:  # a screenshot must never fail the walk
            return f"[screenshot failed: {exc}]"

    try:
        # The deploy anchors and the browser launch live INSIDE this guard:
        # an unreachable target or a launch failure must still write an
        # observation (the module contract), not abort with a traceback.
        obs.deploy_sha = deployed_sha(args.api_url)
        obs.bundle = deployed_bundle(args.base_url)
        obs.sha = _git_sha()
        browser = pw.chromium.launch(headless=not args.headed)
        td.browser = browser    # for the one teardown site in run_walk
        # CLEAN BROWSER: no storage state, no pre-seeded session, no beta-gate
        # flag — exactly what a new user arrives with.
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  locale="en-US")
        td.ctx = ctx
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
            return _finalize(obs, out_dir, td)

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
            return _finalize(obs, out_dir, td)

        # 3c ── the cleanup BASELINE (#4319). Read the walked session's own
        # org list BEFORE the wizard can create anything: this run's orgs are
        # exactly `after - before`, which is a proof of CREATION, while a
        # name is not (a fixed --org-name or a reused account could match an
        # org this run never created). An unreadable baseline leaves teardown
        # DISABLED — it fails closed (residue) rather than deleting on an
        # unproven identity.
        b_status, before_ids, b_upstream = read_org_ids(ctx, args.base_url)
        td.baseline_attempted = True
        if before_ids is None:
            td.reason = (f"baseline GET /api/v1/organizations -> {b_status}"
                         + (f" (upstream {b_upstream})" if b_upstream else ""))
        else:
            td.enabled = True
            td.before = before_ids

        # 4 ── open the wizard and walk it to the final screen (best effort)
        # A first-timer's step 1 is org-create (an input + a button); the
        # generic label loop below cannot advance it.
        org_input = page.locator('input[aria-label="Organization name"]')
        if org_input.count() and org_input.first.is_visible():
            org_input.first.fill(td.org_name)
            # Set BEFORE the click: the click dispatches the create, and a
            # click that raises after dispatching it must still leave this
            # run's residue visible (an empty candidate set then reads as
            # SUSPECT, not as "nothing to do").
            td.create_attempted = True
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
            return _finalize(obs, out_dir, td)
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
            return _finalize(obs, out_dir, td)
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
            return _finalize(obs, out_dir, td)

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
                    return _finalize(obs, out_dir, td)
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
                    return _finalize(obs, out_dir, td)
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
                return _finalize(obs, out_dir, td)

        # 7 ── the POSITIVE direction: reload and read the Overview again
        page.goto(args.base_url.rstrip("/") + "/",
                  wait_until="domcontentloaded", timeout=args.timeout)
        page.wait_for_timeout(args.settle_ms)
        projection_status, projection = read_projection(ctx, args.base_url)
        if not projection_readable(projection_status, projection):
            obs.verdict = instrument_error_verdict(
                "projection_unreadable",
                f"GET /api/v1/onboarding/state -> {projection_status}")
            return _finalize(obs, out_dir, td)
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
        return _finalize(obs, out_dir, td)
    except Exception as exc:
        obs.add(name="error", url=page.url if page else "",
                ok=False, detail=f"{type(exc).__name__}: {exc}",
                screenshot=shot(page, "error") if page else "")
        obs.verdict = f"failed: {type(exc).__name__}: {exc}"
        return _finalize(obs, out_dir, td)


def _close_all(pw, td, closes: list) -> bool:
    """Close the context, the browser and the driver, recording each step.

    Every closer runs even after one fails, and no failure may skip the rest:
    the browser is what reaps the Chromium tree (E8), so a context close that
    raises must not stop the browser close. Returns True if any closer failed.
    """
    failed = False
    for name, closer in (("context", td.ctx), ("browser", td.browser)):
        if closer is None:
            continue
        try:
            closer.close()
            closes.append({"name": name, "how": "closed", "detail": ""})
        except BaseException as exc:
            failed = True
            closes.append({"name": name, "how": "close_error",
                           "detail": scrub(f"{type(exc).__name__}: {exc}")})
    try:
        pw.stop()
        closes.append({"name": "playwright", "how": "closed", "detail": ""})
    except BaseException as exc:
        failed = True
        closes.append({"name": "playwright", "how": "close_error",
                       "detail": scrub(f"{type(exc).__name__}: {exc}")})
    return failed


def _teardown_browser(pw, td, obs, out_dir) -> None:
    """Close the browser this run owns, BOUNDED. The ONE teardown site's body.

    Close the context → close the browser → `pw.stop()`, recording each step into
    `obs.browser_teardown`, with a watchdog armed for the whole of it. The closes
    are unbounded in the API (`Browser.close()` is a timeout-less `send`) and
    cannot be interrupted, so the BOUND comes from outside them: a thread takes
    the `_ladder` rungs on the clock seam and signals the run's OWN Playwright
    driver child, which is what releases a blocked close (E11).

    The bound does NOT depend on a child being enumerable: when the ladder is
    spent and a close is still blocked, `_abandon` writes the record and exits
    the process with the run's own exit code. That last resort is what makes the
    bound hold where no child was enumerated, since a signal is the only thing
    that releases a wedged close.

    The budget starts BEFORE the enumerator runs, so a slow `ps` cannot spend the
    teardown's own slack, and the rung's own re-read is bounded by the ladder's
    slack (`_ps_timeout`) so it cannot push the abandon past the bound.

    The watchdog touches NO Playwright object — only `os.kill` through the signal
    seam and a record — so the sync API's thread-affinity rule is not violated.

    Safety, by construction: the pid is enumerated ONCE, as a direct child of
    this process, together with its parent pid and start time — read as ONE
    identity in a single `ps` call, and re-read as that same whole value
    immediately before every rung (a bare pid is racy against reuse, and a start
    time read separately can belong to the process that reused the pid). With no
    child enumerated the watchdog signals nothing, and the outcome is
    `driver_absent` if the close then returns. Only after a signal is the child
    reaped. Each failure is RECORDED and cannot change the verdict or the exit
    code: cleanup is not the product (#4319's rule, applied to the browser).

    One-shot by construction — `run_walk` spells the call exactly once — plus this
    guard, so a re-entry cannot close the same objects or arm a second watchdog.
    """
    if td.browser_done:
        return
    td.browser_done = True
    record = obs.browser_teardown
    closes = record["closes"]

    started = _monotonic()
    bound = TEARDOWN_BOUND_S
    driver_pid, driver_start, enum_status = _driver_pid_and_starttime()
    me = os.getpid()
    signals: list[tuple[int, int]] = []
    refused_reuse: list[bool] = []
    signal_failed: list[bool] = []
    fired = threading.Event()
    done = threading.Event()

    def _watchdog() -> None:
        try:
            for delay, signum in _ladder(bound):
                if not _wait_until(started + delay, done):
                    return                 # the closes finished before this rung
                fired.set()
                if driver_pid is None:
                    continue               # nothing of ours to signal
                # The identity is re-read as ONE value — parent pid AND start
                # time from a single `ps` — so a pid reused since the enumeration
                # cannot present its successor's start time and pass.
                try:
                    identity = _child_identity(driver_pid)
                except BaseException:
                    identity = None
                if identity is None or identity != (me, driver_start):
                    refused_reuse.append(True)
                    continue               # the pid is no longer our child
                try:
                    _send_signal(driver_pid, signum)
                except BaseException:
                    # A failing signal seam must not kill this thread: the run is
                    # still bounded by the last resort in the `finally` below.
                    signal_failed.append(True)
                    continue
                signals.append((driver_pid, signum))
                with contextlib.suppress(BaseException):
                    _reap(driver_pid)
        finally:
            # THE LAST RESORT: every rung is spent (or unusable) and the teardown
            # is still blocked.
            if not done.is_set():
                _abandon(obs, record, driver_pid, out_dir,
                         enum_status=enum_status, finalized=td.finalized)

    watchdog = threading.Thread(target=_watchdog, daemon=True)
    watchdog.start()
    try:
        failed = _close_all(pw, td, closes)
    finally:
        done.set()
        # A healthy run must not wait out the bound: `done` releases the ladder's
        # own wait, so this join returns in milliseconds.
        watchdog.join(timeout=1.0)

    if failed:
        # An attempted close that RAISED is `close_error` — the specific,
        # actionable record — and must never be masked by a rung having fired (or
        # by the watchdog having signalled) on the way.
        record["outcome"] = BROWSER_TEARDOWN_CLOSE_ERROR
        record["detail"] = "; ".join(c["detail"] for c in closes
                                      if c["how"] == "close_error")
    elif signals:
        record["outcome"] = BROWSER_TEARDOWN_WATCHDOG_KILL
        record["detail"] = ("the driver did not release the close; signalled "
                            + ", ".join(f"{p} {_SIGNAL_LABELS.get(s, s)}"
                                        for p, s in signals))
    elif fired.is_set():
        record["outcome"] = BROWSER_TEARDOWN_DRIVER_ABSENT
        if driver_pid is None:
            if enum_status == DRIVER_ENUM_IDENTITY_UNREADABLE:
                record["detail"] = ("the teardown needed the watchdog; an "
                                    "enumerated driver child's identity could not "
                                    "be read as this run's child, so nothing was "
                                    "signalled")
            else:
                record["detail"] = ("the teardown needed the watchdog, and no driver "
                                    "child was enumerated to signal")
        elif refused_reuse:
            record["detail"] = (f"the teardown needed the watchdog; the enumerated "
                                f"driver child {driver_pid} was refused by the "
                                f"identity re-check, so nothing was signalled")
        elif signal_failed:
            record["detail"] = (f"the teardown needed the watchdog; signalling the "
                                f"enumerated driver child {driver_pid} failed, so "
                                f"nothing was delivered")
        else:
            record["detail"] = (f"the teardown needed the watchdog; no signal was "
                                f"sent for the enumerated driver child "
                                f"{driver_pid}")
    else:
        record["outcome"] = BROWSER_TEARDOWN_CLEAN
    if record["detail"]:
        record["detail"] = scrub(record["detail"])


def _exit_now(code: int) -> None:
    """Terminate the process without unwinding. A SEAM, so a test can observe it.

    `os._exit`, not `sys.exit`: the main thread is blocked in a C-level wait, so
    an exception-based exit could not run from this thread. This is the only exit
    that works while another thread is stuck.
    """
    os._exit(code)


def _abandon(obs, record, driver_pid, out_dir, *,
             enum_status: str = DRIVER_ENUM_NO_CANDIDATE,
             finalized: bool = True) -> None:
    """The ladder is spent and a close is still blocked: end the run, bounded.

    Called from the watchdog thread with the main thread stuck in a timeout-less
    `close()`. Nothing here touches a Playwright object.

    The exit code is the run's OWN, computed exactly as `_finish` would, because
    a cleanup fault must never move the verdict (#4319). An UNFINALIZED run — one
    where a `BaseException` escaped `_walk` before it settled — exits
    `EXIT_INSTRUMENT_ERROR`, which is what `main` returns for it. When the walk
    settled the pre-teardown document is already on disk; the authoritative one
    is written here (when the write succeeds), so a run that had to abandon its
    teardown still says what it measured and why the browser was not released.

    Because this runs on the watchdog thread while the main thread is blocked in
    a timeout-less `close()`, the finalization and the exit below are the LAST
    chance the process has to end itself: nothing there may raise past the exit.
    """
    record["outcome"] = BROWSER_TEARDOWN_ABANDONED
    if driver_pid is not None:
        why = f"driver child {driver_pid} had not released the close"
    elif enum_status == DRIVER_ENUM_IDENTITY_UNREADABLE:
        why = ("an enumerated driver child's identity could not be read as this "
               "run's child")
    else:
        why = "no driver child enumerated to release it"
    record["detail"] = scrub(
        "the ladder was spent with the teardown still blocked and " + why
        + "; the run terminated itself so it could not hang forever")
    if not obs.reason:
        obs.reason = failure_reason(
            obs.verdict, session_state=(obs.session or {}).get("state", ""))
    wrote = False
    try:
        _write_observation(obs, out_dir)
        wrote = True
    except BaseException:
        pass
    try:
        # THE SHARED PRINT PATH, with the write's outcome so the `observation →`
        # line cannot name an artifact that is not there. It also carries the
        # non-clean cleanup warnings (the residue alert and the browser-teardown
        # alert), which `_finish` prints too — this is the ONLY exit an
        # abandoned run will take, so those warnings must live here as well.
        _print_summary(obs, out_dir / "observation.json",
                       artifact_written=wrote)
        sys.stdout.flush()
        sys.stderr.flush()
    except BaseException:
        # A closed CI log pipe (`BrokenPipeError`) or a malformed step detail
        # (`TypeError`) must not kill this thread and skip the exit below.
        pass
    finally:
        # THE EXIT IS UNCONDITIONAL. The main thread is blocked in a timeout-less
        # `close()`, so this is the only thing that bounds the run; a `finally`
        # makes it unreachable-by-any-exception, not merely intended.
        _exit_now(run_exit_code(obs) if finalized else EXIT_INSTRUMENT_ERROR)


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


def _scrub_record(node):
    """Return a redacted COPY of a record's structure — the #5002 guarantee.

    Applied to ``asdict(obs)`` BEFORE ``json.dumps``, at the one serializer, so
    every write path is covered without a per-call-site list: the visited-URL
    fields, the ``signup_responses`` JSON embedded in a step's ``detail`` (a
    JSON STRING), a URL inside ``extra`` or an exception message, and anything
    a future step records — including a URL used as a dict KEY.

    Two things the string passes alone cannot do:

    * a credential can be a STRUCTURED pair (`{"access_token": "…"}`), where
      no single string holds key and value together. A dict entry whose KEY
      names a secret therefore has its value redacted whatever its type, which
      also keeps the record valid JSON (the replacement is a string).
    * scrubbing BEFORE serialization means a value that is itself a JSON body
      still holds real quotes, so its own key/value pairs are redactable; after
      ``json.dumps`` they would be escaped and invisible.

    Returning a copy (never mutating) keeps the caller's record intact for the
    print path, which redacts its own rendering. Idempotent, because a call
    site may already have scrubbed a value and this pass scrubs again.
    """
    if isinstance(node, str):
        return _scrub_text(node)
    if isinstance(node, dict):
        out: dict = {}
        for key, value in node.items():
            new_key = _scrub_text(key) if isinstance(key, str) else key
            if isinstance(new_key, str):
                # A scrubbed key must not collide with an existing one and
                # silently drop an entry.
                new_key = _unique_key(out, new_key)
            out[new_key] = "[REDACTED]" if _is_secret_key(key) else _scrub_record(value)
        return out
    if isinstance(node, list):
        return [_scrub_record(value) for value in node]
    if isinstance(node, tuple):
        return tuple(_scrub_record(value) for value in node)
    return node


def _unique_key(mapping: dict, key: str) -> str:
    """``key``, suffixed if a redaction made it collide with an existing one."""
    if key not in mapping:
        return key
    n = 2
    while f"{key}#{n}" in mapping:
        n += 1
    return f"{key}#{n}"


def _write_observation(obs: Observation, out_dir: Path) -> Path:
    """Serialize the observation ATOMICALLY: ``mkstemp`` + ``os.replace``.

    A run whose walk settled into the pre-teardown write writes the document
    TWICE — once pre-teardown (the `not_run` fallback, so a run killed inside the
    bounded window still leaves a complete, parsable artifact) and once after it
    (authoritative); a walk body that raises before it settles writes neither,
    because its exception propagates past the authoritative write. A
    truncate-in-place write lets a reader see a half-written file between the
    two; an atomic replace cannot. Deliberately PRINT-FREE: the verdict is
    printed once, by `_finish`, so the two writes cannot produce two verdict
    lines.

    The temp is created with ``tempfile.mkstemp`` — an unguessable name, opened
    ``O_EXCL`` — and the destination leaf is refused when it is a symlink, so a
    link planted at a predictable temp name (or at the artifact path) is never
    followed. ``Path.write_text`` follows a symlink (``O_CREAT|O_TRUNC``), the
    #4098 class also documented in ``tools/branch_reaper.py`` and
    ``tools/embedded_evidence.py``.

    The record is redacted BEFORE it is serialized (#5002): this is the ONE
    serializer, so the guarantee cannot be bypassed by a new call site, and
    ``json.dumps`` runs last so the written document is valid JSON by
    construction.
    """
    path = out_dir / "observation.json"
    if path.is_symlink():
        raise OSError(f"refusing to write through a symlinked observation: {path}")
    payload = json.dumps(_scrub_record(asdict(obs)), indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload + "\n")
        os.replace(tmp, path)
    except BaseException:
        # Never leave the tmp behind for the NEXT run to trip over (or for a
        # reader to mistake for the artifact). The repo's convention for the
        # atomic-write pair, e.g. tools/embedded_evidence.py (~#4585).
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return path


def _warn_side_effects(obs: Observation) -> None:
    """The non-clean cleanup warnings: LOUD on stderr, never verdict-affecting.

    Called from the SHARED print path, so a run that ABANDONED its teardown —
    which never reaches `_finish` — still warns about the org it may have left
    live and the browser it could not release. Both obey #4319: cleanup is not
    the product, so neither changes the verdict or the exit code.
    """
    outcome = obs.browser_teardown.get("outcome")
    if outcome not in (BROWSER_TEARDOWN_NOT_RUN, BROWSER_TEARDOWN_CLEAN):
        print(f"[ship-test] BROWSER TEARDOWN — {outcome}: the browser this run owned "
              f"was not released cleanly"
              f" ({scrub(str(obs.browser_teardown.get('detail') or 'no detail'))})."
              f" That is a CLEANUP fault: it does not change the verdict"
              f" ({scrub(obs.verdict)}) or the exit code.", file=sys.stderr)
    if obs.teardown.get("status") in TEARDOWN_RESIDUE_STATES:
        print(f"[ship-test] RESIDUE — teardown {obs.teardown.get('status')}:"
              f" this run may have left a live org behind in the target tenant."
              f" That is a CLEANUP fault: it does not change the verdict"
              f" ({scrub(obs.verdict)}) or the exit code.", file=sys.stderr)


def _print_summary(obs: Observation, path: Path, *,
                   artifact_written: bool = True) -> None:
    """The run's authoritative stdout summary — the ONE print path.

    Shared by `_finish` (the normal exit) and `_abandon` (the bounded last
    resort), so a run that had to end its own teardown still prints the verdict
    line, the per-step summary, the reason/exit line, the ``observation → path``
    line a CI job keys on, and the non-clean cleanup warnings. The verdict is
    scrubbed: it can carry free text assembled from an exception message.

    The record is redacted HERE too, independently of the write: a run whose
    write RAISED (the symlink refusal, a full disk) still prints redacted, so
    the CI log cannot leak what the missing artifact would not have (#5002).
    The structured fields are redacted as structures, then rendered — so a
    credential under a sensitive KEY is caught whichever its value's type, and
    the printed shape is unchanged.

    ``artifact_written`` is what makes the ``observation → path`` line HONEST:
    `_abandon` may have failed to write, and naming an artifact that does not
    exist while stderr contradicts it is exactly the disagreement this path
    exists to avoid. When it is false the line says the artifact was NOT written,
    on both streams.
    """
    print(f"[ship-test] {scrub(obs.verdict)}")
    for s in obs.steps:
        mark = "PASS" if s.ok else ("FAIL" if s.ok is False else "--")
        print(f"  {mark:4} {s.name:24} ui={s.ui or '-':14} "
              f"obs={'Y' if s.observed else 'n'} {_scrub_text(s.detail)[:90]}")
    print(f"[ship-test] assertions: {scrub(repr(_scrub_record(obs.assertions)))}")
    print(f"[ship-test] session: "
          f"{scrub(repr(_scrub_record(obs.session))) if obs.session else '(not reached)'}")
    print(f"[ship-test] deployed sha: {obs.deploy_sha or '(unreadable)'}  "
          f"bundle: {obs.bundle or '(unreadable)'}  instrument: {obs.sha or '(unknown)'}")
    if obs.teardown:
        print(f"[ship-test] teardown: {scrub(repr(_scrub_record(obs.teardown)))}")
    if artifact_written:
        print(f"[ship-test] observation → {path}")
    else:
        print(f"[ship-test] observation NOT WRITTEN → {path}")
        print(f"[ship-test] WARNING — the observation artifact could NOT be "
              f"written ({path}), so this run's own record is incomplete.",
              file=sys.stderr)
    _warn_side_effects(obs)
    if obs.reason == REASON_INSTRUMENT_ERROR:
        # LOUD, on stderr, with the exit code: a run that could not exercise
        # the product must never be mistaken for a measurement of it.
        # `incomplete` (exit 1) meant both, which is how #4291 hid.
        print(f"[ship-test] INSTRUMENT ERROR — {scrub(obs.reason)}: a precondition the "
              f"instrument needs was not met, so this run says NOTHING about the "
              f"product. exit {EXIT_INSTRUMENT_ERROR} (a product finding would "
              f"be exit {EXIT_FAILED})", file=sys.stderr)
    elif obs.reason:
        print(f"[ship-test] reason: {scrub(obs.reason)} "
              f"(exit {exit_code_for(obs.reason)})")


def _finish(obs: Observation, out_dir: Path) -> Observation:
    """The ONE authoritative write+print site.

    The write comes FIRST, so stdout and the file are produced from the same
    finalized record and cannot disagree; the verdict line and the non-clean
    cleanup warnings are printed exactly once, by the shared print path, after
    the bounded teardown has run.
    """
    if not obs.reason:
        obs.reason = failure_reason(
            obs.verdict, session_state=(obs.session or {}).get("state", ""))
    path = _write_observation(obs, out_dir)
    _print_summary(obs, path)
    return obs


def _run_teardown_safely(obs: Observation, td: Teardown | None) -> None:
    """Run the ORG teardown, never raising.

    Extracted so the import-guard exit in `_start_driver` and `_finalize` share
    one body: a failed cleanup is residue, never a product finding, and never a
    reason to lose the artifact (the #4291 conflation, both directions).
    """
    if td is None:
        return
    try:
        _run_teardown(obs, td)
    except Exception as exc:
        obs.teardown = {"status": TEARDOWN_FAILED,
                        "detail": f"{type(exc).__name__}: {exc}"}


def _finalize(obs: Observation, out_dir: Path,
              td: Teardown | None = None) -> Observation:
    """`_walk`'s SINGLE exit: the ORG teardown, then the PRE-TEARDOWN write.

    Every `return` in `_walk` funnels through here, so the artifact carries the
    org-teardown outcome on every path where a browser context existed (i.e. where
    an org could have been created) — and it is COMPLETE and PARSABLE before the
    bounded browser teardown starts, with `browser_teardown.outcome == "not_run"`.
    A run killed inside that window therefore still leaves a diagnostic (#4907);
    the authoritative value replaces it in `run_walk`'s single `_finish`.

    Deliberately does NOT print and does NOT call `_finish`: the authoritative
    write and the ONE verdict print belong to `run_walk`, after the teardown.
    """
    _run_teardown_safely(obs, td)
    _write_observation(obs, out_dir)
    if td is not None:
        # The run SETTLED into a written pre-teardown document. An unfinalized
        # record means `_walk` escaped via a `BaseException`; `main` returns
        # `EXIT_INSTRUMENT_ERROR` for it, and the teardown's last resort must
        # exit the same way rather than scoring the default record as a product
        # code.
        td.finalized = True
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
    # #4907: the teardown bound's ARITHMETIC, as values. The CI half proves the
    # ladder; that the watchdog USES it (rather than hardcoding the same delays)
    # is proven by the fast suite's clock-seam assertions. No browser either way.
    real_ladder = _ladder(TEARDOWN_BOUND_S)
    ok = _ladder_is_sound(real_ladder, TEARDOWN_BOUND_S)
    print(f"  {'ok ' if ok else 'BAD'} ladder {real_ladder}  "
          "the rungs sit strictly inside the bound")
    if not ok:
        failures.append("the teardown ladder's rungs are not inside the bound")
    for name, mutant in (
            ("reversed (SIGKILL first)",
             [(3 * TEARDOWN_BOUND_S / 4, signal.SIGKILL),
              (TEARDOWN_BOUND_S / 2, signal.SIGTERM)]),
            ("a rung outside the bound",
             [(TEARDOWN_BOUND_S / 2, signal.SIGTERM),
              (TEARDOWN_BOUND_S + 1, signal.SIGKILL)]),
            ("three rungs", [*real_ladder, (TEARDOWN_BOUND_S, signal.SIGKILL)]),
            ("the same signal twice",
             [(TEARDOWN_BOUND_S / 2, signal.SIGTERM),
              (3 * TEARDOWN_BOUND_S / 4, signal.SIGTERM)]),
            ("a bound that is not one",
             [(0.0, signal.SIGTERM), (0.0, signal.SIGKILL)])):
        sound = _ladder_is_sound(mutant, TEARDOWN_BOUND_S)
        print(f"  {'ok ' if not sound else 'BAD'} {'GREEN' if not sound else 'RED':5} "
              f"(want RED)  ladder mutant: {name}")
        if sound:
            failures.append(f"ladder mutant read as sound: {name}")
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
    p.add_argument("--keep-org", action="store_true", default=False,
                   help="do NOT reap the org this run created. Teardown is ON by "
                        "default (#4319): the run deletes the org it created "
                        "through the walked session's own BFF proxy, once its "
                        "identity is PROVEN by a before/after diff of that "
                        "session's own org list. Use this only to inspect a "
                        "FAILED run's org — it leaves an explicit, countable "
                        "residue. CLI-only, deliberately NOT env-settable: one "
                        "ambient variable must never be able to turn teardown off "
                        "for every run.")
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
    if args.org_name is not None:
        # Validate what the PRODUCT will actually store: both the wizard and
        # tenant-provision trim first.
        org_name = args.org_name.strip()
        if org_name and not ORG_NAME_RE.fullmatch(org_name):
            print("ship-test: invalid --org-name — the product accepts letters, "
                  "numbers, space, dash and underscore, starting with a letter or "
                  "number, at most 64 characters (the rule both the wizard and "
                  "tenant-provision apply). A name outside that rule is rewritten "
                  "or refused server-side, which would make the org this run "
                  "created unreapable.", file=sys.stderr)
            return EXIT_USAGE
        args.org_name = org_name or None
    try:
        obs = run_walk(args)
    except Exception as exc:
        # run_walk records everything it observes, so nothing should escape it.
        # If something does (a bad --out, a driver that will not start), it is
        # an instrument fault BY CONSTRUCTION — fail closed with exit 3 rather
        # than a traceback that exits 1 and reads as a product finding.
        print(f"ship-test: INSTRUMENT ERROR — run aborted: "
              f"{scrub(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
        return EXIT_INSTRUMENT_ERROR
    return run_exit_code(obs)


if __name__ == "__main__":
    raise SystemExit(main())
