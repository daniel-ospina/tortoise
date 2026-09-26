"""#3806 — the connection-claim guard's executable tests.

WHY THIS FILE EXISTS. The ship-test instrument's third assertion is the one
that matters: **"Connected" appears only when the server observed it.** A guard
for that property pinned as source text is a false PASS — a rename or a
reformat is behaviour-identical and must not move the verdict, while a UI that
lies must be caught. This file executes the instrument's REAL decision code
(``tools.ship_test_onboarding.judge`` / ``classify_connection_text`` /
``claims_connection`` / ``server_observed`` / ``read_connection_surface`` — the
same functions the live browser walk calls) against the defect and the
reformat.

The browser-executing half (a real clean browser against the real committed
dashboard bundle, plus the wire observation that the client issues no
``harness-connected`` write) lives in
``tests/e2e/test_ship_test_onboarding.py``.

Run: TORTOISE_TEST_CARVE_OUT=1 python -m pytest tests/test_ship_test_onboarding.py -v
"""
from __future__ import annotations

import ast
import inspect as _inspect
import os
import signal
import textwrap
import threading
import time

import pytest

import tools.ship_test_onboarding as _mod
from tools.ship_test_onboarding import (
    ABSENT,
    CONNECTED,
    NOT_CONNECTED,
    SESSION_SIGNED_IN,
    UNAVAILABLE,
    _is_loopback,
    _mint_or_read_key,
    claims_connection,
    classify_connection_text,
    connection_surface_kind,
    connection_verdict,
    deployed_sha,
    judge,
    main,
    observe_agent_write,
    page_body,
    read_connection_surface,
    read_projection,
    recorded_body,
    scrub,
    server_observed,
    verdict_for,
)

# The two server truths the guard must separate. `completed_steps` is the
# canonical projection the dashboard reads.
OBSERVED = {"status": "active", "completed_steps": ["team-named", "harness-connected"]}
UNOBSERVED = {"status": "active", "completed_steps": ["team-named"]}


# ── the negative direction (the false-PASS this issue exists to prevent) ────

def test_no_connection_claimed_before_the_server_observed_one() -> None:
    """Journey: signup → wizard → Overview, before the agent's first write.
    The honest negative state must pass the guard."""
    v = judge(NOT_CONNECTED, UNOBSERVED)
    assert v.ok is True, v.detail
    assert v.observed is False
    assert v.rule == "honest-negative"


def test_a_lying_screen_that_claims_an_unobserved_connection_is_rejected() -> None:
    """The defect, as a unit: the client renders "Connected ✓" while the
    server projection has no `harness-connected` edge. The guard MUST go RED
    — this is the false PASS the instrument is built to prevent."""
    v = judge(CONNECTED, UNOBSERVED)
    assert v.ok is False, "a UI claiming an unobserved connection must be rejected"
    assert v.rule == "claim-without-observation"
    assert v.observed is False


def test_connection_claimed_while_the_server_read_failed_is_rejected() -> None:
    """An unreadable server state is NOT evidence of a connection — a UI that
    renders "Connected ✓" over a failed read is asserting, not observing."""
    v = judge(CONNECTED, None)
    assert v.ok is False
    assert v.rule == "claim-without-server-read"


def test_missing_connection_surface_is_rejected() -> None:
    """ABSENT (the surface did not render) is not an honest negative — the
    walk expected a connection surface and got none."""
    v = judge(ABSENT, UNOBSERVED)
    assert v.ok is False
    assert v.rule == "surface-missing"


def test_absent_surface_is_accepted_only_when_none_was_expected() -> None:
    """`expected_surface=False` is the pre-first-point re-entry surface: it
    carries no connection claim and there is nothing to resolve. It must pass,
    and the rule must say so (not fall through to the catch-all)."""
    v = judge(ABSENT, UNOBSERVED, expected_surface=False)
    assert v.ok is True
    assert v.rule == "absent-expected"


# ── the positive direction (a UI that hides an observed connection) ─────────

def test_connection_shown_once_the_server_observed_it() -> None:
    """After the server records the agent's first write, the screen must show
    the connection."""
    v = judge(CONNECTED, OBSERVED)
    assert v.ok is True, v.detail
    assert v.observed is True
    assert v.rule == "observed-and-shown"


def test_a_screen_that_hides_the_server_observed_connection_is_rejected() -> None:
    """The other direction: the server observed it and the UI denies it. Also
    a defect — the walk's positive half would otherwise be vacuous."""
    v = judge(NOT_CONNECTED, OBSERVED)
    assert v.ok is False
    assert v.rule == "observation-not-shown"


def test_unavailable_while_the_server_observed_a_connection_is_rejected() -> None:
    """`unavailable` is only honest when the read actually failed; rendering
    it over a successful observation hides the truth."""
    v = judge(UNAVAILABLE, OBSERVED)
    assert v.ok is False


def test_unavailable_before_any_observation_is_the_honest_negative() -> None:
    """A graph-down read on an unobserved org is honest: it claims nothing."""
    v = judge(UNAVAILABLE, UNOBSERVED)
    assert v.ok is True
    assert v.rule == "honest-negative"


def test_a_jagged_ui_value_cannot_silently_pass() -> None:
    """A value outside the vocabulary (a bug in a future reader) must be RED,
    never fall through to a pass."""
    v = judge("maybe", UNOBSERVED)
    assert v.ok is False
    assert v.rule == "unclassified"


# ── the classifier maps the real screen copy to one vocabulary ──────────────

@pytest.mark.parametrize("text,expected", [
    # the wizard's observed-negative heading (#3428/#2937 wording)
    ("Setup paused — no connection observed yet", NOT_CONNECTED),
    ("No connection observed yet", NOT_CONNECTED),
    # the Overview card's honest negative
    ("Not connected", NOT_CONNECTED),
    # the Overview card's observed-positive
    ("Connected ✓", CONNECTED),
    ("✓ Connected", CONNECTED),
    ("Your agent is connected to this Organization.", CONNECTED),
    # graph-down
    ("Unavailable", UNAVAILABLE),
    ("— Couldn't load — refresh to retry.", UNAVAILABLE),
    # nothing decidable
    ("", ABSENT),
    (None, ABSENT),
])
def test_classifier_maps_real_screen_copy(text, expected) -> None:
    """Every string the product actually renders must land in exactly one
    state. "Not connected" must never be read as CONNECTED (substring trap)."""
    assert classify_connection_text(text) == expected


def test_not_connected_does_not_contain_match_connected() -> None:
    """The substring trap, pinned directly: 'Not connected' and
    'No connection observed' both contain the token 'connected'."""
    assert classify_connection_text("Not connected") == NOT_CONNECTED
    assert classify_connection_text("no connection observed yet") == NOT_CONNECTED


# ── the page-wide connected sweep (independent of the priority order) ───────

def test_claims_connection_sees_a_connected_marker_even_beside_a_negative_one() -> None:
    """The negative sweep must NOT be defeated by a page carrying both a
    negative and a positive claim: `classify_connection_text` tests negatives
    first, so only a CONNECTED-only check can catch the smuggled claim."""
    both = "No connection observed yet — but your agent is connected."
    assert classify_connection_text(both) == NOT_CONNECTED
    assert claims_connection(both) is True


@pytest.mark.parametrize("text,expected", [
    ("Not connected", False),
    ("Setup paused — no connection observed yet", False),
    ("Run the setup command — your agent confirms the connection there.", False),
    ("Connected ✓", True),
    ("✓ Connected", True),
    ("Your agent is connected to this Organization.", True),
    ("", False),
])
def test_claims_connection_matches_the_connected_vocabulary(text, expected) -> None:
    assert claims_connection(text) is expected


# ── the DOM reader (which surface produced the claim) ───────────────────────

class _StubLocator:
    def __init__(self, texts: list[str]):
        self._texts = texts

    def count(self) -> int:
        return len(self._texts)

    @property
    def first(self) -> _StubLocator:
        return self

    def inner_text(self) -> str:
        return self._texts[0]


class _StubPage:
    """Minimal duck-typed Page: selector → list of matching node texts."""

    def __init__(self, nodes: dict[str, list[str]]):
        self._nodes = nodes

    def locator(self, selector: str) -> _StubLocator:
        return _StubLocator(self._nodes.get(selector, []))

    def inner_text(self, selector: str) -> str:
        return self._nodes.get(selector, [""])[0]


_CARD = '[aria-label="Connection status"]'


def test_reader_resolves_the_overview_card_whole() -> None:
    """The card is read WHOLE, so the graph-down shape ("—" + the failed-read
    detail) resolves as UNAVAILABLE rather than an unclassifiable "—"."""
    assert read_connection_surface(_StubPage({_CARD: ["Not connected Connection status"]})) \
        == NOT_CONNECTED
    assert read_connection_surface(
        _StubPage({_CARD: ["Connected ✓ Connection status Your agent is connected."]})) == CONNECTED
    assert read_connection_surface(
        _StubPage({_CARD: ["— Connection status Couldn't load — refresh to retry."]})) == UNAVAILABLE


def test_reader_falls_back_to_the_wizard_final_screen() -> None:
    page = _StubPage({
        ".welcome-title": ["No connection observed yet"],
        "div.done": ["We haven't seen your agent's first write through its Tortoise tools yet"],
    })
    assert read_connection_surface(page) == NOT_CONNECTED
    page = _StubPage({".welcome-title": ["You're all set"], "div.done": ["✓ Connected"]})
    assert read_connection_surface(page) == CONNECTED


def test_reader_returns_absent_when_no_connection_surface_rendered() -> None:
    """The vacuous-pass guard: ABSENT is NOT coerced to an honest negative. A
    walk that reached no decidable surface must report the gap."""
    assert read_connection_surface(_StubPage({})) == ABSENT
    assert read_connection_surface(_StubPage({".welcome-title": ["Create your Organization"]})) \
        == ABSENT


def test_reader_reports_which_surface_produced_the_state() -> None:
    """The two surfaces render the state in different DOM nodes, so the walk
    must know WHERE it read it. #4646: they now share ONE derivation
    (``connectionObservation.js::harnessConnectionObserved``), so this reports
    the location — it does not select a vocabulary."""
    assert connection_surface_kind(_StubPage({_CARD: ["Not connected"]})) == "card"
    assert connection_surface_kind(_StubPage({".welcome-title": ["Create your Organization"]})) \
        == "wizard"
    assert connection_surface_kind(_StubPage({"div.done": ["all set"]})) == "wizard"
    assert connection_surface_kind(_StubPage({})) == "none"


# ── the verdict assembly (the fail-open the review caught) ──────────────────

def test_verdict_is_never_passed_without_the_positive_direction_proven() -> None:
    """`passed` must require the server observation AND the screen showing it.
    A hidden connection resolves to a judge-OK honest-negative, and a step-7
    read that failed resolves to a judge-OK honest-negative too — an assembly
    that keyed on `pos.ok` alone reported BOTH as `passed` (the fail-open)."""
    neg = judge(NOT_CONNECTED, UNOBSERVED)
    signed = dict(session_state=SESSION_SIGNED_IN)
    assert verdict_for(neg, True, judge(NOT_CONNECTED, OBSERVED), **signed) != "passed"
    assert verdict_for(neg, True, judge(NOT_CONNECTED, None), **signed) != "passed"
    assert verdict_for(neg, True, judge(CONNECTED, None), **signed) != "passed"
    assert verdict_for(neg, False, judge(CONNECTED, OBSERVED), **signed) != "passed"
    # `absent-expected` is ok AND observed: an ABSENT surface showed NOTHING, so
    # it must not be read as a shown connection either.
    absent = judge(ABSENT, OBSERVED, expected_surface=False)
    assert absent.ok is True and absent.observed is True
    assert verdict_for(neg, True, absent, **signed) != "passed"
    # the honest walk (observed, then shown) is the ONLY `passed`
    assert verdict_for(neg, True, judge(CONNECTED, OBSERVED), **signed) == "passed"


# ── the connection-verdict seam (cycle-2: the call sites needed coverage) ───

def test_connection_verdict_promotes_a_smuggled_positive_claim() -> None:
    """T5: a negative card must not hide a positive claim elsewhere on the
    page. The promotion lived at an untested call site; it lives here now.

    #4646 round 6: the UNAVAILABLE case is asserted separately because narrowing
    the promotion to `ui == NOT_CONNECTED` left the whole suite green while a
    graph-down screen that also claims a connection was laundered into
    `honest-negative` — the instrument's own read can be fine while the client
    renders its outage card."""
    v = connection_verdict(NOT_CONNECTED, UNOBSERVED,
                           "No connection observed yet — but your agent is connected.")
    assert v.ui == CONNECTED
    assert v.ok is False
    assert v.rule == "claim-without-observation"
    u = connection_verdict(UNAVAILABLE, UNOBSERVED,
                           "Couldn't load — refresh to retry. Your agent is connected.")
    assert u.ui == CONNECTED and u.ok is False
    assert u.rule == "claim-without-observation"


def test_connection_verdict_sweeps_the_untruncated_body() -> None:
    """The sweep must read the UNTRUNCATED text: a claim rendered past the
    artifact cap is still a claim (cycle-1 finding)."""
    page = "x" * 5000 + " Your agent is connected to this Organization."
    assert claims_connection(recorded_body(_StubPage({"body": [page]}))) is False
    assert claims_connection(page) is True
    v = connection_verdict(NOT_CONNECTED, UNOBSERVED, page)
    assert v.ui == CONNECTED and v.ok is False


def test_page_body_is_untruncated_but_the_recorded_copy_is_bounded() -> None:
    """The two inputs are deliberately different functions."""
    page = _StubPage({"body": ["y" * 9000]})
    assert len(page_body(page)) == 9000
    assert len(recorded_body(page)) == 4000


def test_connection_verdict_uses_the_one_edge_only_vocabulary() -> None:
    """#4646: the shipped client has ONE edge-only connection predicate
    (`connectionObservation.js::harnessConnectionObserved`, consumed by
    `overview.js` and `main.jsx`), so the guard applies that one vocabulary — and
    `connection_verdict` deliberately takes NO surface argument, so a surface
    cannot select one. A card claiming a connection for a wire-complete org with
    NO observed edge is now CAUGHT (it passed while the card accepted the
    wire-complete forms). The walk records WHICH DOM surface produced the state
    separately (see `test_reader_reports_which_surface_produced_the_state`)."""
    grandfathered = {"status": "complete", "completed_steps": []}
    assert connection_verdict(NOT_CONNECTED, grandfathered,
                              "No connection observed yet").rule == "honest-negative"
    # a claim on an org whose connection the server never observed:
    v = connection_verdict(CONNECTED, grandfathered, "Connected ✓")
    assert v.ok is False and v.rule == "claim-without-observation"
    # the observed edge is still accepted, and still required to be shown:
    observed = {"completed_steps": ["harness-connected"]}
    assert connection_verdict(CONNECTED, observed, "Connected ✓").ok is True
    assert connection_verdict(NOT_CONNECTED, observed,
                              "No connection observed yet").ok is False


def test_connection_verdict_never_promotes_an_absent_surface_into_a_claim() -> None:
    v = connection_verdict(ABSENT, UNOBSERVED, "your agent is connected")
    assert v.ui == ABSENT and v.rule == "surface-missing" and v.ok is False


def test_mint_prefers_a_shown_tk_key_over_minting_a_second(monkeypatch) -> None:
    """`tk_` is a real minted prefix; recognising only `tt_` skipped a shown
    scoped key and minted another (which at the free tier's cap turns the
    positive direction into `incomplete`)."""
    import tools.ship_test_onboarding as mod

    class _Codes:
        def all_inner_texts(self):
            return ["tk_0123456789abcdef"]

    class _P:
        def locator(self, sel):
            return _Codes()

    def _no_mint(*a, **k):
        raise AssertionError("must not mint when the wizard already showed a key")

    monkeypatch.setattr(mod, "bff_api", _no_mint)
    key, detail = _mint_or_read_key(_P(), object(), "https://app")
    assert key == "tk_0123456789abcdef"
    assert "shown-once" in detail


def test_verdict_carries_the_failing_reason_class() -> None:
    neg = judge(NOT_CONNECTED, UNOBSERVED)
    signed = dict(session_state=SESSION_SIGNED_IN)
    assert verdict_for(neg, True, judge(NOT_CONNECTED, OBSERVED), **signed).startswith("failed:")
    assert verdict_for(neg, True, judge(NOT_CONNECTED, None), **signed).startswith("incomplete:")
    # A screen that HIDES a connection the server observed is a FAILURE, and it
    # is judged as one even when the run's own positive read did not resolve:
    # the lying-UI rules precede the "never resolved" short-circuit (that order
    # is what stops `--skip-agent-write` laundering a lying screen).
    assert verdict_for(neg, False, judge(NOT_CONNECTED, OBSERVED), **signed).startswith("failed:")
    assert verdict_for(judge(CONNECTED, UNOBSERVED), True,
                       judge(CONNECTED, OBSERVED), **signed).startswith("failed:")


# ── the server-observation reader ───────────────────────────────────────────

def test_server_observation_reads_the_canonical_step_edge() -> None:
    assert server_observed({"completed_steps": ["harness-connected"]}) is True
    assert server_observed({"completed_steps": ["team-named"]}) is False


def test_server_observation_accepts_the_servers_own_wire_complete_forms() -> None:
    """PARITY DECISION, pinned explicitly — SERVER-side. The server's own
    COMPLETION rule (`onboarding/state.py::resolve_wire_completion`) accepts the
    grandfathered wire-complete forms (node status complete, or jsonb
    onboarding_complete with zero agent edges). #4646: that is NOT the shipped
    client's connection predicate — the card is edge-only now, exactly like the
    wizard — so the guard never judges a screen with it. The probe is therefore
    spelled out explicitly instead of being the default."""
    assert server_observed({"status": "complete"}, accept_wire_complete=True) is True
    assert server_observed({"onboarding_complete": True}, accept_wire_complete=True) is True
    assert server_observed({"status": "complete", "completed_steps": []},
                           accept_wire_complete=True) is True
    # Its BOUNDARY, pinned so the difference is explicit rather than implied: the
    # probe is a NAMED-FORM check and does NOT re-implement
    # `resolve_wire_completion`'s zero-agent-edge condition (`_NON_AGENT_STEPS` =
    # team-named, connection-written). On a REAL projection that condition is
    # already applied, because the served `onboarding_complete` IS that
    # function's output (`hosted_api.py`) — so this shape is NOT server-
    # producible. It pins what the probe does with a hand-built dict, and that
    # leaving the rule to the server is deliberate. Do NOT "tighten" it by
    # duplicating `_NON_AGENT_STEPS` here: that is a third copy of a server
    # constant to drift.
    assert server_observed({"status": "active", "onboarding_complete": True,
                            "completed_steps": ["first-points-filed"]},
                           accept_wire_complete=True) is True
    # ... while the CONNECTION question (the default, and what a screen is judged
    # against) is edge-only, so the server observed nothing for that org:
    assert server_observed({"status": "complete", "completed_steps": []}) is False
    assert server_observed({"onboarding_complete": True}) is False
    # and a screen claiming a connection for it is a false claim:
    assert judge(CONNECTED, {"status": "complete", "completed_steps": []}).ok is False


def test_the_connection_vocabulary_is_edge_only_and_the_wire_complete_probe_is_explicit() -> None:
    """#4646: the shipped client's ONE connection predicate is edge-only, so the
    guard's default is edge-only for BOTH surfaces; the wire-complete forms are
    reachable only through the explicit server-contract probe. Before #4646 the
    card accepted the wire-complete forms, so this test pinned a per-surface
    split — the split is gone, and with it the false failure it existed to
    prevent: an honest card negative is now simply honest."""
    grandfather = {"status": "complete", "completed_steps": []}
    assert server_observed(grandfather) is False
    assert server_observed(grandfather, accept_wire_complete=True) is True
    # the honest negative for that org is GREEN:
    assert judge(NOT_CONNECTED, grandfather).ok is True
    # the observed edge still counts:
    assert server_observed({"completed_steps": ["harness-connected"]}) is True
    # and a claim with no observed edge is still RED:
    assert judge(CONNECTED, grandfather).ok is False


def test_server_observation_is_false_for_a_missing_or_malformed_projection() -> None:
    assert server_observed(None) is False
    assert server_observed({}) is False
    assert server_observed({"completed_steps": "harness-connected"}) is False  # not a list


def test_server_observation_survives_the_graph_down_shape() -> None:
    """`onboarding/state.py::flow_unavailable()` emits EVERY key as the literal
    "unavailable" (including onboarding_complete) — reading it must be False
    and must not crash."""
    graph_down = {"status": "unavailable", "completed_steps": "unavailable",
                  "onboarding_complete": "unavailable", "fork": "unavailable"}
    assert server_observed(graph_down) is False
    assert judge(UNAVAILABLE, graph_down).ok is True


# ── the honest readers the live walk depends on ─────────────────────────────

def test_scrub_redacts_every_credential_shape() -> None:
    """The observation artifact is written to disk — scrub is the only barrier
    between a session and a leaked credential."""
    raw = ('{"password":"Hunter2","access_token":"ey.abc.def",'
           '"api_key":"tt_abcdef0123456789","refresh_token":"rt-1"}')
    out = scrub(raw)
    for secret in ("Hunter2", "ey.abc.def", "tt_abcdef0123456789", "rt-1"):
        assert secret not in out, f"scrub leaked {secret!r}"
    assert len(scrub("x" * 9000, limit=100)) == 100


def test_scrub_redacts_the_shapes_the_first_pass_missed() -> None:
    """Two real gaps found in review: the product mints `tk_` keys
    (`tortoise/auth.py::API_KEY_PREFIXES`), and a secret may contain a space,
    comma, `}` or `&` — the earlier value class stopped at those characters and
    left the secret verbatim."""
    assert "tk_0123456789abcdef" not in scrub("Your key: tk_0123456789abcdef — keep it safe")
    assert "hunter 2" not in scrub('{"password":"hunter 2"}')
    assert "a,b}c&d" not in scrub('{"access_token":"a,b}c&d"}')
    assert "hunter2" not in scrub("password=hunter2&next=1")
    # the OTHER quote character inside a quoted value must not end the match
    # early (the second review pass caught this one).
    assert "pa'ss word" not in scrub('{"password":"pa\'ss word"}')
    assert "a'b,c" not in scrub('{"access_token":"a\'b,c"}')
    # the non-secret surroundings survive, so the artifact is still readable
    kept = scrub('{"org":"acme","api_key":"tt_abcdefgh12345678"}')
    assert '"org":"acme"' in kept


def test_deployed_sha_reads_the_deployments_own_revision(monkeypatch) -> None:
    """#3806's evidence standard names the deployed SHA; the deployment exposes
    it at the public `GET /v1/version` (`commit_sha`, baked at deploy time)."""
    import tools.ship_test_onboarding as mod
    seen = {}

    def fake_http(method, url, **kw):
        seen["url"] = url
        return 200, {"version": "0.9.0", "commit_sha": "abc1234"}

    monkeypatch.setattr(mod, "_http", fake_http)
    assert deployed_sha("https://api.premiselabs.co/") == "abc1234"
    assert seen["url"] == "https://api.premiselabs.co/v1/version"


def test_deployed_sha_is_empty_when_the_route_is_unavailable(monkeypatch) -> None:
    import tools.ship_test_onboarding as mod
    monkeypatch.setattr(mod, "_http", lambda *a, **k: (404, {"detail": "not found"}))
    assert deployed_sha("https://api.premiselabs.co") == ""
    monkeypatch.setattr(mod, "_http", lambda *a, **k: (200, {"commit_sha": None}))
    assert deployed_sha("https://api.premiselabs.co") == ""


def test_read_projection_returns_none_on_a_failed_read(monkeypatch) -> None:
    import tools.ship_test_onboarding as mod
    monkeypatch.setattr(mod, "bff_api", lambda *a, **k: (401, {"error": "not_signed_in"}))
    status, projection = read_projection(object(), "https://app")
    assert projection is None
    # the STATUS survives: a failed read must not be indistinguishable from a
    # server that observed nothing (the #4291 conflation, one layer down)
    assert status == 401


def test_http_returns_a_clean_failure_on_an_unreachable_target(monkeypatch) -> None:
    """An unreachable deployment is the per-deploy failure that matters most;
    it must surface as a VALUE, not an exception, so run_walk still records an
    observation instead of dying with a traceback and no artifact."""
    import urllib.error
    import urllib.request

    import tools.ship_test_onboarding as mod

    def boom(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert mod._http("GET", "https://nope.invalid/") == (0, "")


def test_read_projection_unwraps_the_onboarding_object(monkeypatch) -> None:
    import tools.ship_test_onboarding as mod
    monkeypatch.setattr(mod, "bff_api", lambda *a, **k: (200, {"onboarding": UNOBSERVED}))
    status, projection = read_projection(object(), "https://app")
    assert status == 200 and projection == UNOBSERVED


# ── the session seam (#4291) ────────────────────────────────────────────────
# The instrument must authenticate the way the app does after #3501/#4054:
# through the app origin's own session, using the BROWSER's cookie jar. These
# pin that mechanism, and pin that no readable-token path can silently return.

class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Requester:
    """The recorder — Playwright's `ctx.request` (an APIRequestContext that
    shares the context's cookie jar)."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def _call(self, method, url, data=None):
        self.calls.append((method, url, data))
        return self.responses.get(url, _Resp(404, {"error": "not_found"}))

    def get(self, url):
        return self._call("GET", url)

    def post(self, url, data=None):
        return self._call("POST", url, data)

    def delete(self, url):
        return self._call("DELETE", url)


class _RequestCtx:
    """A BrowserContext stand-in: `.request` shares its cookie jar."""

    def __init__(self, responses=None):
        self.cookies = []          # the jar; never read by the instrument
        self.request = _Requester(responses)


def test_the_session_is_resolved_on_the_app_origin_through_the_browsers_jar() -> None:
    """The walk must act as the signed-in user via the app origin's OWN
    session endpoint, on the browser's cookie jar — not by reading a token out
    of the page (the retired `sb-*-auth-token` path that made the positive
    direction unexercisable, #4291)."""
    import tools.ship_test_onboarding as mod

    ctx = _RequestCtx({"https://app.premiselabs.co/api/session": _Resp(200, {"user": {}})})
    state, _ = mod.bff_session(ctx, "https://app.premiselabs.co")
    assert state == mod.SESSION_SIGNED_IN
    assert ctx.request.calls == [("GET", "https://app.premiselabs.co/api/session", None)]


def test_the_retired_readable_token_path_is_gone_from_the_instrument() -> None:
    """A guard against silent rot back to the readable cookie/localStorage
    token: the extraction helpers must not exist, and the source must carry no
    live `sb-*-auth-token` read."""
    import inspect

    import tools.ship_test_onboarding as mod

    assert not hasattr(mod, "session_token_from_cookies")
    assert not hasattr(mod, "session_token_from_page")
    src = inspect.getsource(mod)
    assert "localStorage" not in src, "a localStorage read is back in the instrument"
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        assert "auth-token" not in code, f"a live sb-*-auth-token read returned: {line!r}"


def test_the_session_endpoints_own_401_401_503_split_is_preserved() -> None:
    """401 is "NOT signed in"; 503 is "the store is unreachable". Reporting a
    503 as a sign-out is the #3485 class the endpoint's split exists to kill."""
    import tools.ship_test_onboarding as mod

    base = "https://app.premiselabs.co"
    for status, expected in ((401, mod.SESSION_NOT_SIGNED_IN),
                             (503, mod.SESSION_STORE_UNAVAILABLE),
                             (500, mod.SESSION_UNREACHABLE),
                             (200, mod.SESSION_SIGNED_IN)):
        ctx = _RequestCtx({base + "/api/session": _Resp(status, {"user": {}})})
        state, detail = mod.bff_session(ctx, base)
        assert state == expected, f"{status} resolved to {state!r}"
        assert detail, "a session state must carry its evidence"

    class _Boom:
        class request:  # mirrors Playwright's attribute name
            @staticmethod
            def get(url):
                raise OSError("connection refused")

    state, detail = mod.bff_session(_Boom(), base)
    assert state == mod.SESSION_UNREACHABLE and "refused" in detail


def test_the_projection_read_goes_through_the_same_origin_bff_proxy() -> None:
    """The server truth is read through the route the SCREEN itself calls, so
    the comparison is client-vs-its-own-server-read — not two different routes,
    and never another identity's projection."""
    import tools.ship_test_onboarding as mod

    ctx = _RequestCtx({"https://app/api/v1/onboarding/state": _Resp(200, {"onboarding": OBSERVED})})
    status, projection = mod.read_projection(ctx, "https://app")
    assert (status, projection) == (200, OBSERVED)
    assert ctx.request.calls == [("GET", "https://app/api/v1/onboarding/state", None)]


def test_the_agent_key_can_never_carry_the_projection_read() -> None:
    """THE P0 FROM THE INDEPENDENT REVIEW. An earlier revision let
    ``--agent-key`` supply the projection read too. With a key for org B while
    the browser walks org A, a lying org-A UI would be judged against org-B's
    projection — `observed-and-shown` — and the run would report `passed`
    without ever consulting the walked session's own server truth. The read must
    have exactly ONE identity: the walked session."""
    import inspect

    import tools.ship_test_onboarding as mod

    # no key parameter exists on the read at all, so no call site can pass one
    params = inspect.signature(mod.read_projection).parameters
    assert "agent_key" not in params and "api_url" not in params
    assert not hasattr(mod, "read_projection_with_key"), (
        "a key-based projection read is back — it can decouple the screen from "
        "the identity whose server truth judges it")
    # and the read is performed by the context's own request (the session)
    ctx = _RequestCtx({"https://app/api/v1/onboarding/state": _Resp(200, {"onboarding": UNOBSERVED})})
    assert mod.read_projection(ctx, "https://app") == (200, UNOBSERVED)
    assert ctx.request.calls == [("GET", "https://app/api/v1/onboarding/state", None)]


def test_the_agent_key_flag_is_cli_only_and_never_ambiently_injected() -> None:
    """`--agent-key` claims to be explicit, so it must not be settable from the
    environment: an ambient SHIP_TEST_AGENT_KEY would switch the agent write's
    identity with nothing on the command line saying so."""
    import inspect

    import tools.ship_test_onboarding as mod

    default = mod.build_parser().get_default("agent_key")
    assert default is None, f"--agent-key has an ambient default: {default!r}"
    assert "SHIP_TEST_AGENT_KEY" not in inspect.getsource(mod.build_parser)


# ── the loud-failure guard (#4291) ─────────────────────────────────────────

def test_a_run_without_a_session_can_never_be_filed_as_a_product_finding() -> None:
    """THE #4291 guard. The old instrument could not authenticate and then
    recorded `incomplete: the server never observed the agent's write` — a
    PRODUCT finding — for what was an instrument fault. For EVERY unusable
    session state, even with a perfect positive result available, the verdict
    must be an instrument error, the class must be `instrument_error`, and the
    exit code must be 3, not 1."""
    import tools.ship_test_onboarding as mod

    neg = judge(NOT_CONNECTED, UNOBSERVED)
    perfect_positive = judge(CONNECTED, OBSERVED)
    for state in mod.UNUSABLE_SESSION_STATES:
        verdict = verdict_for(neg, True, perfect_positive, session_state=state)
        assert verdict.startswith("instrument-error:"), verdict
        assert "never observed the agent's write" not in verdict
        assert verdict != "passed"
        reason = mod.failure_reason(verdict, session_state=state)
        assert reason == mod.REASON_INSTRUMENT_ERROR
        assert mod.exit_code_for(reason) == mod.EXIT_INSTRUMENT_ERROR
        assert mod.EXIT_INSTRUMENT_ERROR != mod.EXIT_FAILED


def test_the_session_state_is_required_so_a_call_site_cannot_forget_it() -> None:
    """`session_state` is a REQUIRED keyword: a call site that forgets it is a
    TypeError, never a silently product-verdict-producing run."""
    import inspect

    import tools.ship_test_onboarding as mod

    params = inspect.signature(mod.verdict_for).parameters
    assert params["session_state"].default is inspect.Parameter.empty
    assert params["session_state"].kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        mod.verdict_for(judge(NOT_CONNECTED, UNOBSERVED), True,
                        judge(CONNECTED, OBSERVED))  # type: ignore[call-arg]


def test_the_reported_4291_verdict_is_now_read_as_an_instrument_error() -> None:
    """The exact shape of the reported run — session unusable, walk finished,
    the server unobserved — must now read as an INSTRUMENT error with exit 3,
    while the same walk WITH a session keeps the product finding at exit 1."""
    import tools.ship_test_onboarding as mod

    neg = judge(NOT_CONNECTED, UNOBSERVED)
    pos = judge(NOT_CONNECTED, UNOBSERVED)   # nothing to show: observed=False

    broken = verdict_for(neg, False, pos, session_state=mod.SESSION_NOT_SIGNED_IN)
    assert broken.startswith("instrument-error:")
    assert mod.failure_reason(broken, session_state=mod.SESSION_NOT_SIGNED_IN) \
        == mod.REASON_INSTRUMENT_ERROR

    product = verdict_for(neg, False, pos, session_state=mod.SESSION_SIGNED_IN)
    assert product == mod.INCOMPLETE_NO_OBSERVATION
    product_reason = mod.failure_reason(product, session_state=mod.SESSION_SIGNED_IN)
    assert product_reason == mod.REASON_SERVER_DID_NOT_OBSERVE
    # the two are distinguishable WITHOUT parsing prose, and by exit code
    assert mod.exit_code_for(product_reason) == mod.EXIT_FAILED
    assert mod.exit_code_for(mod.REASON_INSTRUMENT_ERROR) == mod.EXIT_INSTRUMENT_ERROR


def test_a_failure_before_the_session_step_is_still_a_product_finding() -> None:
    """The front-door assertion can legitimately fail before any session is
    resolved (the #3781 overlay class). That failure says something REAL about
    the product, so it must keep its product class and exit 1 — the guard must
    not sweep it into the instrument-error bucket."""
    import tools.ship_test_onboarding as mod

    verdict = "failed: signup CTA not hittable in a clean browser"
    reason = mod.failure_reason(verdict, session_state="")
    assert reason == mod.REASON_WALK_FAILED
    assert mod.exit_code_for(reason) == mod.EXIT_FAILED


def test_skip_agent_write_is_never_recorded_as_a_server_side_no_observation() -> None:
    """`--skip-agent-write` is an instrument choice, not a product finding: the
    write was never attempted, so nothing may be blamed on the server."""
    import tools.ship_test_onboarding as mod

    verdict = verdict_for(judge(NOT_CONNECTED, UNOBSERVED), False,
                          judge(NOT_CONNECTED, UNOBSERVED),
                          session_state=mod.SESSION_SIGNED_IN, skip_agent_write=True)
    assert verdict == mod.INCOMPLETE_SKIPPED_WRITE
    assert mod.failure_reason(verdict, session_state=mod.SESSION_SIGNED_IN) \
        == mod.REASON_POSITIVE_NOT_ATTEMPTED


def test_main_maps_an_instrument_error_to_its_own_exit_code(monkeypatch) -> None:
    """End to end through the CLI seam: a run that could not authenticate exits
    3; a run that measured the product and found it wanting exits 1."""
    import tools.ship_test_onboarding as mod

    def _run(reason, verdict):
        def _fake_walk(_args):
            obs = mod.Observation(started_at="now", target={})
            obs.verdict, obs.reason = verdict, reason
            return obs

        monkeypatch.setattr(mod, "run_walk", _fake_walk)
        return mod.main(["--skip-agent-write", "--allow-prod"])

    assert _run(mod.REASON_INSTRUMENT_ERROR, "instrument-error: no session") \
        == mod.EXIT_INSTRUMENT_ERROR
    assert _run(mod.REASON_SERVER_DID_NOT_OBSERVE, mod.INCOMPLETE_NO_OBSERVATION) \
        == mod.EXIT_FAILED


def test_the_agent_write_is_never_attempted_without_a_proven_session() -> None:
    """A walk that is not signed in returns BEFORE the agent-write step, so no
    production org, key, or point can be created by an unauthenticated run."""
    import inspect

    import tools.ship_test_onboarding as mod

    src = inspect.getsource(mod._walk)
    session_return = src.index("if session_state != SESSION_SIGNED_IN:")
    write_call = src.index("observe_agent_write(")
    assert session_return < write_call, (
        "the session gate must precede the agent write in _walk")


# ── the guard covers the OTHER instrument faults too (review findings 2 & 3) ─
# A session that resolved is not the only precondition the instrument needs. A
# failed agent write, an unreadable server projection, and a missing browser
# driver are all instrument faults — each of them used to be graded as a
# product finding, which is the #4291 conflation surviving one layer down.

def test_a_failed_agent_write_is_an_instrument_fault_not_a_server_non_observation() -> None:
    """A bad / wrong-org / graph-bound key or an MCP outage makes the MCP call
    fail. That must NOT be recorded as "the server did not observe"."""
    import tools.ship_test_onboarding as mod

    assert mod._mcp_result_ok(200, '{"result": {}}') is True
    assert mod._mcp_result_ok(403, '{"detail": "graph-bound key"}') is False
    assert mod._mcp_result_ok(200, '{"error": {"code": -32600}}') is False
    assert mod._mcp_result_ok(200, "not json") is False
    assert mod._mcp_result_ok(0, "") is False
    # MCP reports a TOOL-level failure with HTTP 200 + result.isError — the
    # repo's own tests assert that shape (tests/test_mcp_http.py).
    assert mod._mcp_result_ok(
        200, '{"jsonrpc":"2.0","id":2,"result":{"isError":true,"content":[]}}'
    ) is False
    # ...and the SECOND failure shape, which hides: an error dict carried inside
    # a result whose isError is FALSE (tortoise/mcp_server.py::_safe and
    # _quota_gated return this, and _maybe_onboarding_auto_complete is not
    # called for it, so no harness-connected edge is filed).
    quota = ('{"jsonrpc":"2.0","id":2,"result":{"isError":false,'
             '"structuredContent":{"error":"team limits missing max_points",'
             '"code":-32007},"content":[]}}')
    assert mod._mcp_result_ok(200, quota) is False
    assert mod._mcp_result_ok(200, "event: message\r\ndata: " + quota + "\r\n\r\n") is False
    # result must be an OBJECT, not a scalar
    assert mod._mcp_result_ok(200, '{"jsonrpc":"2.0","id":2,"result":"oops"}') is False

    verdict = mod.instrument_error_verdict("agent_write_failed", "mint failed")
    assert verdict.startswith("instrument-error:")
    assert "never observed the agent" not in verdict
    assert mod.failure_reason(verdict, session_state=mod.SESSION_SIGNED_IN) \
        == mod.REASON_INSTRUMENT_ERROR


def test_the_real_mcp_response_is_sse_framed_and_must_still_parse() -> None:
    """THE REVIEW'S P0. The hosted endpoint is FastMCP streamable-HTTP with
    ``json_response`` unset, so a REAL response is an SSE stream whose JSON
    rides on ``data:`` lines — ``json.loads(body)`` raises on it. An
    instrument that assumed plain JSON would mark every real write as failed
    and report a healthy deployment as `agent_write_failed` (exit 3), i.e. the
    positive direction would again never be exercised.

    The frame below is the shape the repo's own MCP tests parse
    (``tests/test_mcp_http.py::_parse_sse_json``).
    """
    import tools.ship_test_onboarding as mod

    sse_ok = ('event: message\r\ndata: {"jsonrpc":"2.0","id":2,'
              '"result":{"content":[{"type":"text","text":"created"}]}}\r\n\r\n')
    assert mod._mcp_payload(sse_ok) == {
        "jsonrpc": "2.0", "id": 2,
        "result": {"content": [{"type": "text", "text": "created"}]}}
    assert mod._mcp_result_ok(200, sse_ok) is True
    # a ping/comment frame carries no JSON at all — not a success
    assert mod._mcp_payload(": ping - 2026-09-20 06:44:18\r\n\r\n") is None
    assert mod._mcp_result_ok(200, ": ping\r\n\r\n") is False
    # a leading NOTIFICATION frame must not shadow the response that follows
    notif = ('event: message\r\ndata: {"jsonrpc":"2.0",'
             '"method":"notifications/progress","params":{}}\r\n\r\n')
    assert mod._mcp_payload(notif + sse_ok) == {
        "jsonrpc": "2.0", "id": 2,
        "result": {"content": [{"type": "text", "text": "created"}]}}
    assert mod._mcp_result_ok(200, notif + sse_ok) is True
    # ...and a lone notification is not a response at all
    assert mod._mcp_payload(notif) is None
    # a spec-legal multi-line data: payload is reassembled
    multiline = 'event: message\r\ndata: {"jsonrpc":"2.0","id":2,\r\ndata: "result":{}}\r\n\r\n'
    assert mod._mcp_payload(multiline) == {"jsonrpc": "2.0", "id": 2, "result": {}}
    # and an SSE-framed tool error is still a failure
    sse_err = ('event: message\r\ndata: {"jsonrpc":"2.0","id":2,'
               '"result":{"isError":true,"content":[]}}\r\n\r\n')
    assert mod._mcp_result_ok(200, sse_err) is False


def test_a_run_walk_write_failure_returns_an_instrument_error_before_judging() -> None:
    """`_walk` must return on a failed write — not fall through to the verdict
    assembly that would blame the server."""
    import inspect

    import tools.ship_test_onboarding as mod

    src = inspect.getsource(mod._walk)
    assert "if not result.get(\"ok\"):" in src
    assert src.index("if not result.get(\"ok\"):") < src.index(
        "if not saw_readable:"), "the write check must precede the read check"
    # the poll classifies on whether the server's truth was EVER readable, not
    # on which read happened to be last
    assert "saw_readable = saw_readable or projection_readable(" in src


def test_an_unreadable_projection_is_an_instrument_fault() -> None:
    """A degraded session store answers 503 on /api/v1 while /api/session still
    answers 200 — the session resolves, every projection read fails, and the run
    would otherwise report `server_did_not_observe`. It must be loud instead."""
    import tools.ship_test_onboarding as mod

    verdict = mod.instrument_error_verdict("projection_unreadable",
                                           "GET /api/v1/onboarding/state -> 503")
    assert verdict.startswith("instrument-error:")
    assert mod.failure_reason(verdict, session_state=mod.SESSION_SIGNED_IN) \
        == mod.REASON_INSTRUMENT_ERROR
    assert mod.exit_code_for(mod.failure_reason(verdict, session_state=mod.SESSION_SIGNED_IN)) \
        == mod.EXIT_INSTRUMENT_ERROR


def test_an_unreadable_instrument_read_is_never_reported_as_a_lying_ui() -> None:
    """The instrument's own read failing cannot license a product finding.
    `judge()` has a `claim-without-server-read` rule, but `_walk` must classify
    a failed read as an instrument fault FIRST: a transient 503 on the
    instrument's request would otherwise brand a truthful, connection-showing
    client as a liar."""
    import inspect

    import tools.ship_test_onboarding as mod

    src = inspect.getsource(mod._walk)
    # three guards: step 5, the poll loop (all-fail), step 7
    assert src.count("if not projection_readable(") == 2
    assert src.count("if not saw_readable:") == 1
    for guard in ("if not projection_readable(", "if not saw_readable:"):
        assert src.index(guard) < src.index("obs.verdict = verdict_for(")


def test_a_200_with_an_unparseable_body_is_an_unreadable_read() -> None:
    """A REVIEW FINDING: `200` alone is not readable. A 200 whose body did not
    parse to an OBJECT (an SPA `index.html` fallback, a proxy error page served
    with 200, `[]`/`null`/a scalar) yields no projection, and the run must
    classify that as an instrument fault — not as "the server observed
    nothing"."""
    import tools.ship_test_onboarding as mod

    assert mod.projection_readable(200, {"completed_steps": []}) is True
    assert mod.projection_readable(200, {}) is True
    for status, projection in ((200, None), (503, None), (401, None), (0, None)):
        assert mod.projection_readable(status, projection) is False

    # and the real reader reports exactly that pair for a non-object body
    class _Bad:
        class request:
            @staticmethod
            def get(url):
                class _R:
                    status = 200

                    @staticmethod
                    def json():
                        return ["not", "an", "object"]

                return _R()

    assert mod.read_projection(_Bad(), "https://app") == (200, None)
    assert mod.projection_readable(200, None) is False


def test_skip_agent_write_cannot_launder_a_lying_ui() -> None:
    """`--skip-agent-write` skips the WRITE, not the judgement: a step-7 screen
    that claims a connection nothing observed is still a product FAILURE, not
    the `positive_not_attempted` incomplete."""
    import tools.ship_test_onboarding as mod

    neg = judge(NOT_CONNECTED, UNOBSERVED)
    lying = judge(CONNECTED, UNOBSERVED)   # claims a connection; nothing observed
    assert lying.rule == "claim-without-observation"
    verdict = verdict_for(neg, False, lying, session_state=mod.SESSION_SIGNED_IN,
                          skip_agent_write=True)
    assert verdict.startswith("failed:"), verdict
    assert verdict != mod.INCOMPLETE_SKIPPED_WRITE
    # and the honest skip is still the skipped-write class
    honest = verdict_for(neg, False, judge(NOT_CONNECTED, UNOBSERVED),
                         session_state=mod.SESSION_SIGNED_IN, skip_agent_write=True)
    assert honest == mod.INCOMPLETE_SKIPPED_WRITE


def test_the_default_reason_is_fail_closed() -> None:
    """A run that aborts before it proves it measured the product (no playwright
    driver, a browser that will not launch, an unexpected exception) must be
    filed as an instrument fault, never as a product finding."""
    import inspect

    import tools.ship_test_onboarding as mod

    src = inspect.getsource(mod._start_driver)
    default_at = src.index("obs.reason = REASON_INSTRUMENT_ERROR")
    assert default_at < src.index("from playwright.sync_api import"), (
        "the fail-closed reason must be set before anything can abort")
    obs = mod.Observation(started_at="now", target={})
    obs.reason = mod.REASON_INSTRUMENT_ERROR
    obs.verdict = "failed: playwright unavailable: ImportError"
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR


def test_observe_agent_write_uses_the_real_mcp_agent_path(monkeypatch) -> None:
    """The server's observation is produced by an MCP `tortoise_create_point`
    call — never by writing the onboarding checkpoint."""
    import tools.ship_test_onboarding as mod
    calls = []

    def fake_mcp(api_url, key, method, params=None, rid=1):
        calls.append((method, params, rid))
        return 200, '{"result": {}}'

    monkeypatch.setattr(mod, "mcp_call", fake_mcp)
    out = observe_agent_write("https://api", "tt_key", content="hello")
    methods = [c[0] for c in calls]
    assert methods == ["initialize", "tools/call"]
    assert calls[1][1]["name"] == "tortoise_create_point"
    for _, params, _ in calls:
        assert not params or "step" not in params, \
            "the instrument must never write the onboarding checkpoint itself"
    assert out["initialize"]["status"] == 200


# ── the CLI's safety contract ───────────────────────────────────────────────

@pytest.mark.parametrize("url,loopback", [
    ("http://localhost:8788", True),
    ("http://127.0.0.1:8788", True),
    ("http://[::1]:8788", True),
    ("http://10.0.0.5:8788", False),
    ("http://169.254.169.254/", False),
    ("https://app.premiselabs.co", False),
    ("https://api.premiselabs.co", False),
])
def test_is_loopback_only_accepts_a_loopback_host(url, loopback) -> None:
    assert _is_loopback(url) is loopback


def test_cli_refuses_a_non_loopback_target_without_allow_prod(capsys) -> None:
    """Pointing the instrument at production without the explicit hatch must
    exit 2 and run no walk."""
    rc = main(["--auth-url", "https://tortoise.premiselabs.co",
               "--base-url", "https://app.premiselabs.co"])
    assert rc == 2
    assert "non-loopback" in capsys.readouterr().err


def test_cli_mutation_selfcheck_exits_zero() -> None:
    assert main(["--mutation-selfcheck"]) == 0


# ── RED/GREEN: a behaviour-identical reformat must not move the verdict ─────

@pytest.mark.parametrize("reformat", [
    "Connected ✓",
    "  CONNECTED   ✓  ",
    "\n\tConnected ✓\n",
    "Your agent is connected to this Organization.",
])
def test_behaviour_identical_reformat_cannot_flip_a_lying_ui_green(reformat) -> None:
    """The guard's anti-text-scan property, executed: the SAME lie spelled
    differently must still be RED. A guard that only matched the literal
    "Connected ✓" would leak every one of these."""
    ui = classify_connection_text(reformat)
    assert ui == CONNECTED, f"the reformat must classify as CONNECTED: {reformat!r}"
    v = judge(ui, UNOBSERVED)
    assert v.ok is False, f"a reformat leaked GREEN: {reformat!r}"


def test_guard_is_not_vacuous_every_rule_can_fail() -> None:
    """A guard whose rules can never fire is a false PASS. Executing every RED
    rule proves each is reachable."""
    violations = {
        judge(CONNECTED, UNOBSERVED).rule,
        judge(CONNECTED, None).rule,
        judge(ABSENT, UNOBSERVED).rule,
        judge(NOT_CONNECTED, OBSERVED).rule,
        judge("maybe", UNOBSERVED).rule,
    }
    assert violations == {
        "claim-without-observation", "claim-without-server-read",
        "surface-missing", "observation-not-shown", "unclassified",
    }


# ── the walk itself, executed (#4291 review: call-site pins) ────────────────
# The fast lane never ran `run_walk`, so the two regressions that matter most —
# judging the screen against a KEY-based projection read, and grading a failed
# write as `server_did_not_observe` — were reintroducible while every source
# assertion stayed green. These tests execute the REAL `run_walk` with a fake
# browser, so the call site (which read it uses, in what order, with what
# credential) is behaviourally pinned rather than grepped.
#
# The readers that need a live page are stubbed; the seams under test
# (`bff_session`, `bff_api`, `read_projection`, `projection_readable`,
# `observe_agent_write`, `_mcp_payload`, `verdict_for`, `failure_reason`) are the
# real ones.

SESSION_OK = {"user": {"id": "u-1", "email": "ship@premiselabs.co"}}


class _Resp2:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeRequester:
    """The fake browser context's request channel, recording every call."""

    def __init__(self, plan):
        self.plan = plan          # (method, path) -> list of (status, payload)
        self.calls = []

    def _one(self, method, url, data=None):
        self.calls.append((method, url, data))
        path = "/" + url.split("://", 1)[1].split("/", 1)[1] if "://" in url else url
        queue = self.plan.get((method, path)) or self.plan.get(("ANY", path))
        if not queue:
            return _Resp2(404, {"error": "not_found"})
        return _Resp2(*queue.pop(0)) if len(queue) > 1 else _Resp2(*queue[0])

    def get(self, url):
        return self._one("GET", url)

    def post(self, url, data=None):
        return self._one("POST", url, data)

    def delete(self, url):
        return self._one("DELETE", url)

    def onboarding_state_calls(self):
        return [c for c in self.calls if c[1].endswith("/onboarding/state")]


# ── the teardown harness: a driver lifecycle, and the seams it is driven by ─
# The bounded teardown is a pair of concurrent agents (the owning thread running
# the closes, a watchdog thread taking the rungs), so the harness that proves it
# has to be able to WEDGE, RAISE, and BE SIGNALLED — and to say WHY a wedged call
# was released. The five seams the tool exposes (`_driver_pid_and_starttime`,
# `_send_signal`, `_child_identity`, `_reap`, `_monotonic`) are installed from the
# shared `_DriverHarness` below, which also carries the driver-event sink: an
# instance-only attribute would have no handle, because the fake playwright is
# built inside a lambda.
_WEDGE_TIMEOUT = 10.0
_SIGNAL_NAMES = {signal.SIGTERM: "sigterm", signal.SIGKILL: "sigkill"}


class _Wedge:
    """A close() that does not return until a signal (or its own bound) releases
    it — the E7/E11 pair, faked. `released_by` names the releaser, so a mutant
    that never signals is distinguishable from one that does."""

    def __init__(self, harness, *, release_on=None, timeout=None):
        self._harness = harness
        self._event = threading.Event()
        self.release_on = release_on      # None => ANY signal releases it
        self.released_by = None
        self.timeout = _WEDGE_TIMEOUT if timeout is None else timeout
        harness.wedges.append(self)

    def release(self, signum):
        if self.release_on is not None and signum not in self.release_on:
            return
        self.released_by = _SIGNAL_NAMES.get(signum, str(signum))
        self._event.set()

    def wait(self):
        if not self._event.wait(self.timeout):
            # Self-release, which MUST be distinguishable from a release by a
            # signal: otherwise a watchdog that never fires passes AC1.
            self.released_by = "timeout"


class _DriverHarness:
    """One fake run's shared state: the driver-event sink, the seam recorders,
    and the wedges the signal seam releases."""

    def __init__(self):
        self.driver_events = []
        self.signals = []            # (pid, signum), in the order sent
        self.signal_times = []       # the clock seam's reading at each signal
        self.wedges = []
        self.driver = (4242, "fake-lstart", _mod.DRIVER_ENUM_FOUND)
        # A tuple (ppid, start_time) the RE-check sees instead, so a DIFFERENT
        # PARENT and a DIFFERENT START TIME are both expressible (pid reuse).
        self.identity_override = None
        self.signal_raises = False            # a signal seam that raises, for the guard
        self.identity_reads = []
        self.reaps = []
        self.clock_reads = []
        self.teardown_start = None
        self.out_dir = None          # set by the walk helper, for the exit capture
        self.exits = []              # exit codes the abandon path passed to `_exit_now`
        self.exit_docs = []          # the on-disk document as it was at that moment

    def enumerate_driver(self):
        self.teardown_start = self.clock()
        return self.driver

    def exit_now(self, code):
        """The last-resort exit, as a SEAM so the suite survives it.

        In production `os._exit` ends the process here, so the blocked main thread
        never matters. In the suite there is no process to end, so this records
        the call, captures the document the abandon path wrote, and releases the
        wedge — otherwise the fake's main thread would stay blocked forever.
        """
        import json

        self.exits.append(code)
        try:
            self.exit_docs.append(json.loads(
                (self.out_dir / "observation.json").read_text())["browser_teardown"])
        except Exception:
            self.exit_docs.append(None)
        for wedge in list(self.wedges):
            wedge.release(signal.SIGKILL)

    def send_signal(self, pid, signum):
        if self.signal_raises:
            raise RuntimeError("signal seam failed")
        self.signals.append((pid, signum))
        self.signal_times.append(self.clock())
        for wedge in list(self.wedges):
            wedge.release(signum)

    def child_identity(self, pid):
        """The TOCTOU re-check's ONE reader: the process's whole identity.

        Defaults to what the enumerator recorded — this run as the parent, the
        driver's start time — so the re-check passes by construction; the
        override makes it disagree on EITHER field, which is the pid-reuse
        simulation.
        """
        self.identity_reads.append(pid)
        if self.identity_override is not None:
            return self.identity_override
        return (os.getpid(), self.driver[1])

    def reap(self, pid):
        self.reaps.append(pid)

    def clock(self):
        now = time.monotonic()
        self.clock_reads.append(now)
        return now


class _FakeBrowser:
    """The real `Browser`: `close()` closes the browser AND everything it owns — a
    context still open is force-closed (`ctx_reaped#<serial>`), not leaked — and a
    second `close()` has no effect (a launched browser re-sends and swallows the
    target-closed error). Contexts are tracked so the force-close is VISIBLE and
    distinguishable from the instrument's own `ctx_closed#<serial>`."""

    def __init__(self, ctx, new_context_raises=False, browser_close_wedges=False,
                 browser_close_raises=False, wedge_release_on=None,
                 wedge_timeout=None):
        self._ctx = ctx
        self._owned = []           # only contexts it actually created
        self._used = False
        self._closed = False
        self._new_context_raises = new_context_raises
        self._browser_close_raises = browser_close_raises
        self._wedge = (_Wedge(ctx.harness, release_on=wedge_release_on,
                              timeout=wedge_timeout)
                       if browser_close_wedges else None)
        ctx.browser = self
        ctx.events.append("launch")

    def new_context(self, **k):
        if self._closed:
            raise RuntimeError("browser is closed")
        if self._new_context_raises:
            raise RuntimeError("context creation failed")
        if self._used:
            # A second call is a DISTINCT sibling, so a context the instrument
            # abandons is visible in the log instead of being an invisible return
            # of the object it already has.
            ctx = self._ctx.sibling()
        else:
            self._used = True
            ctx = self._ctx
        self._owned.append(ctx)
        self._ctx.events.append(f"ctx_created#{ctx.serial}")
        return ctx

    def close(self):
        if self._closed:
            return
        if self._wedge is not None:
            # E7/E11: `Browser.close()` is a timeout-less `send`, so against a
            # frozen driver it blocks until the driver is killed.
            self._wedge.wait()
        if self._browser_close_raises:
            raise RuntimeError("browser close failed")
        self._closed = True
        for owned in self._owned:      # force-close, as Browser.close() really does
            owned._reap()
        self._ctx.events.append("browser_closed")


class _FakePage:
    def __init__(self, base_url, org_create=False, org_click_raises=False):
        self._base = base_url.rstrip("/")
        self.url = self._base + "/welcome"
        # OPT-IN: only a walk that is meant to exercise the org-create step
        # reports the wizard's org-name input as visible. Off by default, so
        # every pre-existing test keeps the behaviour it was written against.
        self.org_create = org_create
        self.org_click_raises = org_click_raises
        # The wizard write, recorded: a test can then prove that the name the run
        # WRITES is the name teardown MATCHES against (they are one value).
        self.fills = []

    def on(self, *a, **k):
        pass

    def goto(self, url, **k):
        # A fake browser always "lands" in the product: the real signup flow
        # redirects to the app origin, so the walk's landing wait must not spin.
        self.url = self._base + "/welcome"

    def wait_for_timeout(self, ms):
        pass

    def click(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass

    def evaluate(self, *a, **k):
        return {}

    def inner_text(self, *a, **k):
        return ""

    def screenshot(self, *a, **k):
        pass

    def locator(self, sel):
        page = self
        # SELECTOR-SPECIFIC: only the org-name input is faked as visible, so the
        # walk's button loop and `_mint_or_read_key`'s `code` search keep the
        # behaviour they have always had.
        is_org_input = sel == 'input[aria-label="Organization name"]'
        is_create_btn = "Create Organization" in sel

        class _L:
            def count(self):
                return 1 if (is_org_input and page.org_create) else 0

            @property
            def first(self):
                return self

            def all_inner_texts(self):
                return []

            def is_visible(self):
                return bool(is_org_input and page.org_create)

            def inner_text(self):
                return ""

            def fill(self, value, *a, **k):
                if is_org_input and page.org_create:
                    page.fills.append((sel, value))

            def click(self, *a, **k):
                # A click that raises: the create POST may or may not have been
                # dispatched, which is exactly why the walk flags the attempt
                # BEFORE it clicks.
                if is_create_btn and page.org_click_raises:
                    raise RuntimeError("create click failed")

        return _L()


class _FakeCtx:
    _serial = 0

    def __init__(self, plan, base_url, org_create=False, org_click_raises=False,
                 shares=None, harness=None, ctx_close_wedges=False,
                 ctx_close_raises=False, wedge_release_on=None,
                 wedge_timeout=None):
        # A sibling context (a second `browser.new_context()`) SHARES the request
        # recorder and the event sink, so it is a distinct object whose missing
        # close is visible, while the test's handle keeps seeing every request.
        # Each context carries a unique SERIAL, so a leak cannot be balanced out by
        # closing another one twice — an `id()` can be reused after its object is
        # collected, and two contexts can carry the same one.
        _FakeCtx._serial += 1
        self.serial = _FakeCtx._serial
        self.request = shares.request if shares else _FakeRequester(plan)
        self.cookies = []          # the instrument must never read the jar
        self.events = shares.events if shares else []
        self.harness = harness if harness is not None else _DriverHarness()
        self._base = base_url
        self._org_create = org_create
        self._org_click_raises = org_click_raises
        self._ctx_close_raises = ctx_close_raises
        self._wedge = (_Wedge(self.harness, release_on=wedge_release_on,
                              timeout=wedge_timeout) if ctx_close_wedges else None)
        self.page = None
        self.browser = None        # set by the browser that creates it
        self._closed = False

    def new_page(self):
        self.page = _FakePage(self._base, self._org_create, self._org_click_raises)
        return self.page

    def close(self):
        # The real `BrowserContext.close()` returns early when it is already
        # closing or closed, so a second call is a no-op, not a failure.
        if self._closed:
            return
        if self._wedge is not None:
            self._wedge.wait()
        if self._ctx_close_raises:
            raise RuntimeError("context close failed")
        self._closed = True
        self.events.append(f"ctx_closed#{self.serial}")

    def _reap(self):
        """What `Browser.close()` does to a context the run left open."""
        if self._closed:
            return
        self._closed = True
        self.events.append(f"ctx_reaped#{self.serial}")

    def sibling(self):
        return _FakeCtx(None, self._base, self._org_create, self._org_click_raises,
                        shares=self)


class _FakeChromium:
    def __init__(self, ctx, launch_raises=False, new_context_raises=False,
                 browser_close_wedges=False, browser_close_raises=False,
                 wedge_release_on=None, wedge_timeout=None):
        self._ctx = ctx
        self._launch_raises = launch_raises
        self._new_context_raises = new_context_raises
        self._browser_close_wedges = browser_close_wedges
        self._browser_close_raises = browser_close_raises
        self._wedge_release_on = wedge_release_on
        self._wedge_timeout = wedge_timeout

    def launch(self, **k):
        if self._launch_raises:
            raise RuntimeError("browser launch failed")
        return _FakeBrowser(self._ctx, self._new_context_raises,
                            self._browser_close_wedges,
                            self._browser_close_raises,
                            self._wedge_release_on, self._wedge_timeout)


class _FakeSyncPlaywright:
    """The driver lifecycle, faked. `start()`/`stop()` record on a SEPARATE sink:
    `ctx.events[-1] == "browser_closed"` is asserted by the reap pin, so a driver
    event landing there would red it. The `with` form still works."""

    def __init__(self, ctx, launch_raises=False, new_context_raises=False,
                 start_raises=False, browser_close_wedges=False,
                 browser_close_raises=False, stop_wedges=False,
                 wedge_release_on=None, wedge_timeout=None,
                 stop_raises_base=False):
        self._ctx = ctx
        self._start_raises = start_raises
        self._stop_raises_base = stop_raises_base
        self._wedge = (_Wedge(ctx.harness, release_on=wedge_release_on,
                              timeout=wedge_timeout) if stop_wedges else None)
        self.chromium = _FakeChromium(ctx, launch_raises, new_context_raises,
                                      browser_close_wedges, browser_close_raises,
                                      wedge_release_on, wedge_timeout)

    def start(self):
        if self._start_raises:
            raise RuntimeError("driver start failed")
        self._ctx.harness.driver_events.append("driver_started")
        return self

    def stop(self):
        if self._wedge is not None:
            self._wedge.wait()
        if self._stop_raises_base:
            # A BaseException, deliberately not an Exception: the instrument's own
            # teardown must complete and RECORD it rather than lose the run.
            raise KeyboardInterrupt("driver stop was killed")
        self._ctx.harness.driver_events.append("driver_stopped")

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
        return False


def _run_fake_walk(monkeypatch, tmp_path, *, plan, ui_sequence,
                   mcp_tools_call, surface="card", surface_sequence=None, skip_write=False,
                   org_create=False, org_click_raises=False, org_name=None,
                   keep_org=False, front_door_hittable=True,
                   playwright_available=True, launch_raises=False,
                   new_context_raises=False, start_raises=False,
                   ctx_close_wedges=False, ctx_close_raises=False,
                   browser_close_wedges=False, browser_close_raises=False,
                   stop_wedges=False, driver_absent=False,
                   enum_identity_unreadable=False, identity_override=None,
                   wedge_release_on=None,
                   wedge_timeout=None, expect_reaped=True, signal_raises=False,
                   stop_raises_base=False, harness_sink=None):
    """Execute the real `run_walk` against a fake browser. Returns the record.

    `front_door_hittable=False` makes the front-door probe REPORT the signup CTA
    as not hittable, driving the pre-session product finding;
    `playwright_available=False` makes the driver import fail, which is the
    fail-closed default path; `new_context_raises=True` makes the driver start and
    then refuse a context. The teardown knobs wedge/raise/withhold the driver so
    the bounded teardown can be driven from the fake.
    """
    import sys
    import types

    import tools.ship_test_onboarding as mod

    base = "https://app.premiselabs.co"
    harness = _DriverHarness()
    if harness_sink is not None:
        # A handle is needed by tests whose `run_walk` raises before returning
        # (an unfinalized run), where the ctx is never handed back.
        harness_sink.append(harness)
    if driver_absent:
        harness.driver = (None, None, mod.DRIVER_ENUM_NO_CANDIDATE)
    if enum_identity_unreadable:
        # An enumerated, marker-matching child whose identity could not be read:
        # the record must NOT claim "no child was enumerated".
        harness.driver = (None, None, mod.DRIVER_ENUM_IDENTITY_UNREADABLE)
    harness.identity_override = identity_override
    harness.signal_raises = signal_raises
    ctx = _FakeCtx(plan, base, org_create=org_create,
                   org_click_raises=org_click_raises, harness=harness,
                   ctx_close_wedges=ctx_close_wedges,
                   ctx_close_raises=ctx_close_raises,
                   wedge_release_on=wedge_release_on,
                   wedge_timeout=wedge_timeout)
    if playwright_available:
        fake_sync = types.ModuleType("playwright.sync_api")
        fake_sync.sync_playwright = lambda: _FakeSyncPlaywright(
            ctx, launch_raises, new_context_raises, start_raises,
            browser_close_wedges, browser_close_raises, stop_wedges,
            wedge_release_on, wedge_timeout, stop_raises_base)
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    else:
        # `None` in sys.modules makes `from playwright.sync_api import ...` raise
        # ImportError — the driver-missing path, without touching the disk.
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    # (leaf stubs: things that would otherwise hit the network or need a page)
    monkeypatch.setattr(mod, "_git_sha", lambda: "deadbeef")
    monkeypatch.setattr(mod, "deployed_sha", lambda api_url: "9605f5f249")
    monkeypatch.setattr(mod, "deployed_bundle", lambda base_url: "assets/index-x.js")
    monkeypatch.setattr(mod, "front_door_probe",
                        lambda page, **k: {"hittable": front_door_hittable})
    # `surface_sequence` feeds a DIFFERENT answer per call, so the two verdict
    # reads can be distinguished. A constant stub cannot catch a stale re-read
    # (step 7 reusing step 5's value) or a per-site constant (#4646 round 5).
    _surfaces = list(surface_sequence) if surface_sequence else [surface]
    monkeypatch.setattr(mod, "connection_surface_kind",
                        lambda page, **k: _surfaces.pop(0) if len(_surfaces) > 1
                        else _surfaces[0])
    monkeypatch.setattr(mod, "page_body", lambda page: "")
    monkeypatch.setattr(mod, "recorded_body", lambda page: "")
    ui_values = list(ui_sequence)

    def _reader(page, **k):
        return ui_values.pop(0) if ui_values else ABSENT

    monkeypatch.setattr(mod, "read_connection_surface", _reader)

    def _mcp_call(api_url, key, method, params=None, rid=1):
        if method == "initialize":
            return 200, 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\r\n\r\n'
        return 200, mcp_tools_call

    monkeypatch.setattr(mod, "mcp_call", _mcp_call)

    # The SIX teardown seams: recorded here, replaced nowhere else, so the
    # teardown's process-facing reads/writes are the fake's.
    monkeypatch.setattr(mod, "_driver_pid_and_starttime", harness.enumerate_driver)
    monkeypatch.setattr(mod, "_send_signal", harness.send_signal)
    monkeypatch.setattr(mod, "_child_identity", harness.child_identity)
    monkeypatch.setattr(mod, "_reap", harness.reap)
    monkeypatch.setattr(mod, "_monotonic", harness.clock)
    monkeypatch.setattr(mod, "_exit_now", harness.exit_now)
    harness.out_dir = tmp_path / "ship-test"

    args = mod.build_parser().parse_args([
        "--base-url", base, "--auth-url", "https://tortoise.premiselabs.co",
        "--api-url", "https://api.premiselabs.co", "--allow-prod",
        "--timeout", "800", "--settle-ms", "0",
        "--out", str(tmp_path / "ship-test"), "--skip-agent-write" if skip_write else "--email",
    ] + ([] if skip_write else ["ship@premiselabs.co"])
      + (["--org-name", org_name] if org_name else [])
      + (["--keep-org"] if keep_org else []))
    obs = mod.run_walk(args)
    # BEHAVIOURAL single-exit check: every path the suite exercises must land a
    # teardown block in the artifact. A future exit routed past `_finalize`
    # falsifies this for the whole suite instead of for one hand-written case.
    assert obs.teardown, "run_walk exited without recording teardown"
    if expect_reaped:
        _assert_browser_reaped(ctx)
    return obs, ctx, mod


def test_the_fake_driver_records_start_and_stop() -> None:
    """The driver lifecycle has its OWN sink: `ctx.events[-1] == "browser_closed"`
    is asserted by the reap pin, so a driver event landing on the browser sink
    would red it."""
    ctx = _FakeCtx({}, "https://app.premiselabs.co")
    pw = _FakeSyncPlaywright(ctx)
    assert pw.start() is pw
    pw.stop()
    assert ctx.harness.driver_events == ["driver_started", "driver_stopped"]
    assert ctx.events == [], "driver events must not land on the browser sink"


def test_the_teardown_seams_are_declared() -> None:
    """Every process-facing read/write the bounded teardown makes is a
    module-level indirection point, so the fake harness can RECORD what it asked
    for without spawning a driver."""
    import tools.ship_test_onboarding as mod

    for name in ("_driver_pid_and_starttime", "_send_signal", "_child_identity",
                 "_reap", "_monotonic"):
        assert callable(getattr(mod, name)), name
    assert mod._monotonic() > 0
    # a fabricated pid must not raise ECHILD out of the reap seam
    assert mod._reap(2**30) is None


_SESSION_200 = [(200, {"user": {"id": "u-1"}})]
_PROJ_UNOBSERVED = (200, {"onboarding": UNOBSERVED})
_PROJ_OBSERVED = (200, {"onboarding": OBSERVED})
_MCP_OK = ('event: message\r\ndata: {"jsonrpc":"2.0","id":2,"result":'
           '{"content":[{"type":"text","text":"created"}]}}\r\n\r\n')
_MCP_QUOTA_REFUSAL = (
    'event: message\r\ndata: {"jsonrpc":"2.0","id":2,"result":{"isError":false,'
    '"structuredContent":{"error":"team limits missing max_points","code":-32007},'
    '"content":[]}}\r\n\r\n')


def test_walk_happy_path_passes_and_only_ever_reads_through_the_session(monkeypatch, tmp_path):
    """A full walk with a healthy server must PASS — and every projection read it
    makes must go through the walked session's own BFF path, with no key and no
    credential of its own, so the screen is judged against its own identity."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, _PROJ_OBSERVED,
                                             _PROJ_OBSERVED],
    }
    obs, ctx, _ = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan,
        ui_sequence=[NOT_CONNECTED, CONNECTED], mcp_tools_call=_MCP_OK)

    assert obs.verdict == "passed", (obs.verdict, obs.session, obs.reason)
    assert obs.reason == ""
    assert obs.assertions == {"front_door_reachable": True, "walk_completed": True,
                              "no_claim_before_observation": True,
                              "shown_when_observed": True}
    assert obs.session["state"] == "signed_in"
    calls = ctx.request.onboarding_state_calls()
    assert len(calls) == 3
    for _method, url, data in calls:
        assert url.startswith("https://app.premiselabs.co/api/v1/onboarding/state"), url
        assert data is None
    # the instrument never read the browser's cookie jar directly
    assert ctx.cookies == []


def test_walk_records_which_dom_surface_produced_each_verdict(monkeypatch, tmp_path):
    """#4646: the walk records the surface it judged (`extra["surface"]`). No
    other test distinguishes the recorded values, so a dropped key, a hard-coded
    'card', or a STALE RE-READ would pass unnoticed. The two reads are given
    DIFFERENT surfaces here (step 5 judges the wizard's final screen; step 7
    reloads `/` into the Overview and judges the card), so a constant — or step 7
    reusing step 5's value — cannot satisfy both assertions."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, _PROJ_OBSERVED,
                                             _PROJ_OBSERVED],
    }
    obs, _ctx, _ = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, surface_sequence=["wizard", "card"],
        ui_sequence=[NOT_CONNECTED, CONNECTED], mcp_tools_call=_MCP_OK)
    assert obs.verdict == "passed", (obs.verdict, obs.reason)
    by_name = {s.name: s for s in obs.steps}
    assert by_name["before-observation"].extra["surface"] == "wizard"
    assert by_name["after-observation"].extra["surface"] == "card", (
        "step 7 must re-read the surface, not reuse step 5's")


def test_walk_is_judged_edge_only_on_a_wire_complete_org(monkeypatch, tmp_path):
    """#4646 round 7/8: the walk's call site must use the ONE edge-only vocabulary,
    on EVERY surface.

    A wire-complete org with no observed edge is rendered HONESTLY by the
    edge-only client as the observation phrase, so the rule at both connection
    steps must be `honest-negative`. Re-introducing a per-surface vocabulary at
    the walk's CALL SITE (the pre-#4646 `accept = surface_kind == "card"`) made
    that honest screen `observation-not-shown` — a FAILED walk on exit 1 — with
    the whole unit suite and both self-checks green, because every other walk
    fixture is either unobserved-and-edge-less or observed-with-the-edge. The
    switch is keyed on the surface, so BOTH polarities are pinned: the card run
    and the wizard run (step 5 IS the wizard read). This is the regression the
    round-2 self-check rows used to catch before the surface parameter was
    removed, pinned here where it is still reachable."""
    grandfather = (200, {"onboarding": {"status": "complete", "completed_steps": []}})
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [grandfather] * 40,
    }
    for surface in ("card", "wizard"):
        obs, ctx, mod = _run_fake_walk(
            monkeypatch, tmp_path / surface, plan=plan, surface=surface,
            ui_sequence=[NOT_CONNECTED, NOT_CONNECTED], mcp_tools_call=_MCP_OK)
        by_name = {s.name: s for s in obs.steps}
        assert by_name["after-observation"].extra["rule"] == "honest-negative", \
            (surface, by_name["after-observation"].extra)
        # ... and the walk must NOT blame the product for the honest negative. The
        # `rule` above is the SCREEN's verdict; these pin the WALK's own reason,
        # which is computed from the separate post-write `server_observed` poll —
        # widening THAT probe turns this honest run into `positive_not_shown`
        # ("the screen hid a connection the server observed") while every other
        # assertion, and the self-check, still pass.
        assert obs.reason == mod.REASON_SERVER_DID_NOT_OBSERVE, (surface, obs.reason)
        assert obs.verdict == mod.INCOMPLETE_NO_OBSERVATION, (surface, obs.verdict)
        assert obs.steps[-1].observed is False, (surface, obs.steps[-1].observed)
        # ... nor may it call the honest run a product failure:
        assert not obs.verdict.startswith("failed:"), (surface, obs.verdict, obs.reason)


def test_walk_waits_for_the_observed_edge_after_a_wire_complete_read(monkeypatch, tmp_path):
    """#4646 round 10: the post-write poll must wait for the EDGE, not for completion.

    The poll's break criterion is the same question as the probe that decides the
    walk's reason, so a wire-complete acceptance reintroduced THERE ends the wait
    on the first read for an org that never observed the edge. Today the MCP write
    cannot file that edge for a grandfathered org, so this is latent rather than
    reachable — but the site rides on a default, and the flip is exactly the
    per-surface widening this instrument exists to refuse. Pin the WAIT: read #1
    and #2 are wire-complete AND edge-less, read #3 carries the edge, so a
    completion-keyed break records `observed` from read #1 and reports the honest
    `server_did_not_observe` for a run that did in fact observe."""
    wc_no_edge = (200, {"onboarding": {"status": "complete", "completed_steps": [],
                                      "onboarding_complete": True}})
    wc_edge = (200, {"onboarding": {"status": "complete",
                                    "completed_steps": ["harness-connected"],
                                    "onboarding_complete": True}})
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"):
            [wc_no_edge, wc_no_edge, wc_edge] + [wc_edge] * 40,
    }
    obs, _ctx, _ = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, surface="card",
        ui_sequence=[NOT_CONNECTED, CONNECTED], mcp_tools_call=_MCP_OK)
    by_name = {s.name: s for s in obs.steps}
    assert by_name["agent-write"].observed is True, (obs.verdict, obs.reason)
    assert obs.verdict == "passed", (obs.verdict, obs.reason)


def test_walk_without_a_session_is_an_instrument_error_and_writes_nothing(monkeypatch, tmp_path):
    """No session ⇒ exit-3 class, and NO agent write, NO key mint, NO org."""
    plan = {("GET", "/api/session"): [(401, {"error": "not_signed_in"})],
            ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED]}
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED],
        mcp_tools_call=_MCP_OK)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert mod.failure_reason(obs.verdict, session_state=obs.session["state"]) \
        == mod.REASON_INSTRUMENT_ERROR
    assert obs.session["state"] == "not_signed_in"
    assert ("POST", "/api/v1/team/keys") not in [(c[0], c[1]) for c in ctx.request.calls]
    assert not any(c[0] == "POST" for c in ctx.request.calls)
    assert obs.session["detail"].startswith("401")
    # the recorded teardown STATUS, not mere truthiness: this test passes no
    # `--keep-org`, so the status recorded for this pre-baseline exit is
    # `not_reached` (#4843).
    assert obs.teardown["status"] == mod.TEARDOWN_NOT_REACHED


def test_walk_reports_a_failed_write_as_an_instrument_error_not_a_non_observation(
        monkeypatch, tmp_path):
    """A quota refusal / SDK error comes back with HTTP 200 and an error dict
    INSIDE the result. It is a failed WRITE, and the run must say so loudly
    instead of reporting `server_did_not_observe` (the #4291 conflation)."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED],
    }
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED],
        mcp_tools_call=_MCP_QUOTA_REFUSAL)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert "never observed the agent" not in obs.verdict
    assert mod.failure_reason(obs.verdict, session_state=obs.session["state"]) \
        == mod.REASON_INSTRUMENT_ERROR
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    # it returned BEFORE the poll: no post-write projection read was needed
    assert len(ctx.request.onboarding_state_calls()) == 1
    assert obs.steps[-1].name == "agent-write"
    # the STATUS, not merely truthiness: this exit is post-create, and a
    # re-pointed/emptied teardown state records the truthy, non-residue
    # `not_reached`, which reports an unreaped org as clean.
    assert obs.teardown["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE


def test_walk_reports_a_never_readable_projection_as_an_instrument_error(
        monkeypatch, tmp_path):
    """Every post-write read failing (degraded store) is an instrument fault."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, (503, None)],
    }
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED],
        mcp_tools_call=_MCP_OK)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert "projection_unreadable" in obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    # PIN THE CALL SITE, not just the verdict: the step-7 read produces the SAME
    # verdict, reason and teardown status, so neutering the poll guard would
    # leave this test green. `poll_readable` is set on the write step only when
    # control gets PAST the poll guard.
    assert "poll_readable" not in obs.steps[-1].extra, obs.steps[-1].extra
    # The harness serves no org-list route, so the recorded state must be the
    # fail-closed one. Asserting the STATUS (not just that teardown is truthy) is
    # what catches a teardown state that reached this exit degraded — a re-bound
    # or emptied object records the truthy, non-residue `not_reached`, which
    # reports an unreaped org as clean.
    assert obs.teardown["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE


def test_walk_reports_a_200_but_unparseable_projection_as_an_instrument_error(
        monkeypatch, tmp_path):
    """A 200 whose body is not an object (SPA fallback / proxy error page) is
    NOT \"the server observed nothing\" — it is an unreadable read."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, (200, None)],
    }
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED],
        mcp_tools_call=_MCP_OK)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert "projection_unreadable" in obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    # PIN THE CALL SITE, not just the verdict: the step-7 read produces the SAME
    # verdict, reason and teardown status, so neutering the poll guard would
    # leave this test green. `poll_readable` is set on the write step only when
    # control gets PAST the poll guard.
    assert "poll_readable" not in obs.steps[-1].extra, obs.steps[-1].extra
    # ...and the recorded teardown status, for the same reason as the
    # never-readable case above.
    assert obs.teardown["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE


# ── the exits nothing reached (#4843) ───────────────────────────────────────
# A marker `raise` above each of these left the whole suite green before this
# change. Three are POST-create — the create-attempt flag and the click are at
# `:1313`–`:1314` — so each is a path on which this run may have created an org,
# and each records `obs.teardown` from whatever teardown state reached it. With
# no test, a degraded teardown state there (a re-bound or emptied object, an
# in-place field assignment) is invisible to the suite AND flips a residue state
# to the truthy, deliberately-non-residue `not_reached` — an unreaped org
# reported as a clean run (the #4291 conflation). So each asserts the recorded
# teardown STATUS, not merely that `obs.teardown` is truthy: `not_reached`
# satisfies truthiness.
#
# The last two are PRE-create: no org can exist yet, and these tests pass no
# `--keep-org`, so the recorded status is the clean `TEARDOWN_NOT_REACHED`
# rather than a residue alarm.

def test_the_teardown_statuses_are_classified_as_residue_or_clean() -> None:
    """The classifier the statuses get their residue/clean meaning from: a
    `baseline_unavailable`
    teardown WARNS — a live org may remain — while `not_reached` is deliberately
    NOT a residue state, so it raises no false alarm."""
    assert _mod.TEARDOWN_BASELINE_UNAVAILABLE in _mod.TEARDOWN_RESIDUE_STATES
    assert _mod.TEARDOWN_NOT_REACHED not in _mod.TEARDOWN_RESIDUE_STATES


def _assert_teardown_recorded_fail_closed(obs, mod) -> None:
    """These harnesses serve no org-list route, so `GET /api/v1/organizations`
    404s and the recorded status is the fail-closed `baseline_unavailable` — a
    residue state. Specific enough that any OTHER state a degraded teardown
    object would record (notably the truthy, non-residue `not_reached`) reds."""
    assert obs.teardown, "this exit must carry the teardown state"
    assert obs.teardown["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE, (
        f"expected the fail-closed state, got {obs.teardown!r}")


def _assert_not_the_generic_error_handler(obs, name: str) -> None:
    """No `error` step, and the walk's last step is `name`: it did not exit
    through the walk's generic `except Exception` handler, which appends an
    `error` step and returns immediately (so it is always the LAST step).

    DEFENCE IN DEPTH, not the discriminator. Each of these tests already fails
    on a marker raise without it — through a verdict or reason assertion, or (at
    the claim-failure exit, which sets its own reason before returning) through
    the step-identity assertion.
    """
    assert not [s for s in obs.steps if s.name == "error"], (
        f"exited through the error handler, not the {name!r} step: {obs.steps}")
    assert obs.steps[-1].name == name, obs.steps[-1].name


def _assert_browser_reaped(ctx) -> None:
    """Nothing the run launched is left unrecorded as closed: exactly one browser,
    closed LAST, and every context it created is settled — closed by the
    instrument itself
    (`ctx_closed#<serial>`) or force-closed by the browser's own close
    (`ctx_reaped#<serial>`), which is what the real object does with the contexts it
    owns. A run that never launched closes nothing.

    This is the no-leak property, so it does not require the graceful shape: the
    instrument's own explicit context close is pinned once, by
    `test_the_walk_closes_its_own_context_before_the_browser`.
    """
    events = ctx.events
    if "launch" not in events:
        assert events == [], events
        return
    created = [e.split("#", 1)[1] for e in events if e.startswith("ctx_created#")]
    settled = [e.split("#", 1)[1] for e in events
               if e.startswith(("ctx_closed#", "ctx_reaped#"))]
    assert sorted(settled) == sorted(created), (
        f"still open: {sorted(set(created) - set(settled))}; "
        f"settled but never created: {sorted(set(settled) - set(created))}; "
        f"events={events}")
    assert events.count("launch") == 1, events
    assert events.count("browser_closed") == 1, events
    assert events[-1] == "browser_closed", events


@pytest.mark.parametrize("events,accepted", [
    (["launch", "ctx_created#1", "ctx_closed#1", "browser_closed"], True),
    # launch succeeded, then the driver refused a context: nothing was created
    (["launch", "browser_closed"], True),
    # one context, force-closed by the browser rather than closed by the run
    (["launch", "ctx_created#1", "ctx_reaped#1", "browser_closed"], True),
    # two contexts, both settled: the multiset the harness never compares live
    (["launch", "ctx_created#1", "ctx_created#2", "ctx_closed#2",
      "ctx_reaped#1", "browser_closed"], True),
    # one of two left open
    (["launch", "ctx_created#1", "ctx_created#2", "ctx_closed#1",
      "browser_closed"], False),
    # a second browser, one of them never closed
    (["launch", "launch", "ctx_created#1", "ctx_closed#1", "browser_closed"], False),
    # the browser is never closed: the leak this pin exists for
    (["launch", "ctx_created#1", "ctx_closed#1"], False),
    # the browser is closed before the context it owns
    (["launch", "browser_closed", "ctx_created#1", "ctx_closed#1"], False),
])
def test_the_reap_pin_gates_only_a_settled_run(events, accepted):
    """The pin's contract, exercised directly on constructed event logs, including
    shapes the tool cannot currently produce (a force-reaped context, two contexts,
    a browser closed before its own context)."""
    ctx = _FakeCtx({}, "https://app.premiselabs.co")
    ctx.events.extend(events)
    if accepted:
        _assert_browser_reaped(ctx)
    else:
        with pytest.raises(AssertionError):
            _assert_browser_reaped(ctx)


def test_walk_that_never_reaches_a_connection_surface_is_incomplete_no_surface(
        monkeypatch, tmp_path):
    """The screen renders no connection surface at all, so the NEGATIVE
    direction was never measured: `INCOMPLETE_NO_SURFACE` (exit 1), never a
    product failure — and it stops before the agent write."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED],
    }
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[ABSENT], surface="card",
        mcp_tools_call=_MCP_OK, org_create=True, org_name=_RUN_ORG)

    assert obs.verdict == mod.INCOMPLETE_NO_SURFACE
    assert mod.exit_code_for(obs.reason) == mod.EXIT_FAILED
    # the CREATE really happened (the premise that makes this exit post-create),
    # and it stopped at the read: no key minted, no agent write
    assert ctx.page.fills == [('input[aria-label="Organization name"]', _RUN_ORG)]
    assert ctx.request.onboarding_state_calls(), "the step-5 read never happened"
    assert not any(c[0] == "POST" for c in ctx.request.calls)
    _assert_not_the_generic_error_handler(obs, "before-observation")
    # ...and the step is this exit's, not the claim-failure exit's: that one
    # carries `page_claims_connection`, this one does not.
    assert "page_claims_connection" not in obs.steps[-1].extra
    # The ABSENT branch records the surface too — and the harness answers `card`
    # here on purpose, so a hard-coded `"none"` at that site fails (the `none`
    # VALUE is pinned by the reader test instead).
    assert obs.steps[-1].extra["surface"] == "card", obs.steps[-1].extra
    _assert_teardown_recorded_fail_closed(obs, mod)


def test_walk_whose_screen_claims_a_connection_the_server_never_saw_is_a_product_failure(
        monkeypatch, tmp_path):
    """The screen says "Connected" while the server's own projection shows no
    observation. That is the `no_claim_before_observation` assertion failing —
    the product finding this instrument exists to make — and it is distinct from
    the absent-surface class directly above, which records no such assertion at
    all."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED],
    }
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[CONNECTED],
        mcp_tools_call=_MCP_OK, org_create=True, org_name=_RUN_ORG)

    assert obs.verdict.startswith("failed:"), obs.verdict
    assert obs.assertions.get("no_claim_before_observation") is False
    assert mod.exit_code_for(obs.reason) == mod.EXIT_FAILED
    assert ctx.page.fills == [('input[aria-label="Organization name"]', _RUN_ORG)]
    assert ctx.request.onboarding_state_calls(), "the step-5 read never happened"
    assert not any(c[0] == "POST" for c in ctx.request.calls)
    _assert_not_the_generic_error_handler(obs, "before-observation")
    # the exit's OWN step: the claim-failure branch records the page's claim,
    # the absent-surface branch above does not.
    assert "page_claims_connection" in obs.steps[-1].extra
    _assert_teardown_recorded_fail_closed(obs, mod)


def test_walk_without_a_mintable_key_is_an_instrument_error(monkeypatch, tmp_path):
    """The write step has no credential: the wizard showed no `tt_`/`tk_` key and
    the BFF refuses to mint one. The run cannot make the server-observed write,
    so it is an INSTRUMENT fault (exit 3) — never `server_did_not_observe`, which
    would blame the product for a credential the instrument never got."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED],
        ("POST", "/api/v1/team/keys"): [(403, {"error": "forbidden"})],
    }
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED],
        mcp_tools_call=_MCP_OK, org_create=True, org_name=_RUN_ORG)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert "no_agent_key" in obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    # the CREATE really happened (the premise that makes this exit post-create)…
    assert ctx.page.fills == [('input[aria-label="Organization name"]', _RUN_ORG)]
    # …and the refusal really was the mint call, not a missing call
    assert [c for c in ctx.request.calls if c[0] == "POST"], ctx.request.calls
    assert "mint failed" in obs.steps[-1].detail, obs.steps[-1].detail
    _assert_not_the_generic_error_handler(obs, "agent-write")
    _assert_teardown_recorded_fail_closed(obs, mod)


def test_walk_reports_a_signup_cta_that_is_not_hittable_as_a_product_finding(
        monkeypatch, tmp_path):
    """The front-door probe reports the signup CTA as not hittable. This runs
    BEFORE any session is resolved, so it is a PRODUCT finding (exit 1), not an
    instrument fault.
    No org can exist yet and this test passes no `--keep-org`, so the recorded
    status is the clean `not_reached` (which is deliberately NOT a residue
    state)."""
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={("GET", "/api/session"): _SESSION_200},
        ui_sequence=[], mcp_tools_call=_MCP_OK, front_door_hittable=False)

    assert obs.verdict.startswith("failed:"), obs.verdict
    assert "not hittable" in obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_FAILED
    assert obs.teardown["status"] == mod.TEARDOWN_NOT_REACHED


def test_walk_without_the_driver_records_a_fail_closed_observation(
        monkeypatch, tmp_path):
    """The playwright import fails: the driver is missing. That must still
    produce a RECORDED, fail-closed observation rather than a traceback — the
    verdict `failed: playwright unavailable`, an instrument exit code, and a
    recorded teardown status of `not_reached` — no browser context ever existed,
    and this test passes no `--keep-org`."""
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        playwright_available=False)

    assert obs.verdict.startswith("failed: playwright unavailable"), obs.verdict
    # the exception type is named, not swallowed
    assert "ModuleNotFoundError" in obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    assert obs.teardown["status"] == mod.TEARDOWN_NOT_REACHED


def test_a_browser_launch_that_fails_is_recorded_and_nothing_is_left_open(
        monkeypatch, tmp_path):
    """`pw.chromium.launch` raising lands in the same fail-closed class
    (instrument error, exit 3) as a missing driver, through the walk body's own
    `except` — and there is no browser or context to close, so the harness's reap
    assertion sees an empty event list."""
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        launch_raises=True)

    assert obs.verdict.startswith("failed: RuntimeError"), obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    assert obs.teardown["status"] == mod.TEARDOWN_NOT_REACHED


def test_the_fakes_distinguish_a_forced_close_from_the_instruments_own():
    """Layer 2 (`test_the_walk_closes_its_own_context_before_the_browser`) rests on
    this distinction: a context the BROWSER closes for a run must be recorded as
    `ctx_reaped#<serial>`, never `ctx_closed#<serial>` — or that test passes on a
    run that never closed its own context."""
    base = "https://app.premiselabs.co"
    abandoned = _FakeCtx({}, base)
    browser = _FakeBrowser(abandoned)
    browser.new_context()
    browser.close()
    assert abandoned.events == ["launch", f"ctx_created#{abandoned.serial}",
                               f"ctx_reaped#{abandoned.serial}",
                               "browser_closed"], abandoned.events

    closed = _FakeCtx({}, base)
    second = _FakeBrowser(closed)
    second.new_context()
    closed.close()
    second.close()
    assert f"ctx_closed#{closed.serial}" in closed.events, closed.events
    assert f"ctx_reaped#{closed.serial}" not in closed.events, closed.events


def test_a_context_that_cannot_be_created_still_closes_the_browser(monkeypatch, tmp_path):
    """The driver starts and then refuses a context: the same fail-closed class
    (instrument error, exit 3) and the same walk-body `except` that catches a
    launch failure. Nothing was created, so the browser is all there is to close —
    and the run closes it."""
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        new_context_raises=True)

    assert obs.verdict.startswith("failed: RuntimeError"), obs.verdict
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    assert obs.teardown["status"] == mod.TEARDOWN_NOT_REACHED
    assert ctx.events == ["launch", "browser_closed"], ctx.events


def test_the_walk_closes_its_own_context_before_the_browser(monkeypatch, tmp_path):
    """The instrument's teardown SHAPE, pinned once: it closes the context it
    created, itself. `Browser.close()` force-closes an open context by itself and a
    second context close is a no-op, so this is a shape standard, not a leak guard —
    which is why the no-leak pin above must not require it."""
    plan = {("GET", "/api/session"): [(401, {"error": "not_signed_in"})]}
    obs, ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[], mcp_tools_call=_MCP_OK)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert f"ctx_closed#{ctx.serial}" in ctx.events, ctx.events
    assert "browser_closed" in ctx.events, ctx.events


def test_walk_skip_agent_write_cannot_launder_a_lying_ui(monkeypatch, tmp_path):
    """With --skip-agent-write a screen that claims a connection is still a
    product FAILURE (exit 1), not the `positive_not_attempted` incomplete."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED],
    }
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED, CONNECTED],
        mcp_tools_call=_MCP_OK, skip_write=True)

    assert obs.verdict.startswith("failed:"), obs.verdict
    assert obs.verdict != mod.INCOMPLETE_SKIPPED_WRITE
    assert mod.exit_code_for(obs.reason) == mod.EXIT_FAILED


def test_walk_skip_agent_write_honest_run_is_the_skipped_class(monkeypatch, tmp_path):
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED],
    }
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED, NOT_CONNECTED],
        mcp_tools_call=_MCP_OK, skip_write=True)

    assert obs.verdict == mod.INCOMPLETE_SKIPPED_WRITE
    assert mod.failure_reason(obs.verdict, session_state=obs.session["state"]) \
        == mod.REASON_POSITIVE_NOT_ATTEMPTED
    assert mod.exit_code_for(obs.reason) == mod.EXIT_FAILED


def test_main_returns_three_for_a_walk_that_never_authenticated(monkeypatch, tmp_path):
    """End to end through the CLI: an instrument fault exits 3, not 1."""
    import sys
    import types

    import tools.ship_test_onboarding as mod

    base = "https://app.premiselabs.co"
    ctx = _FakeCtx({("GET", "/api/session"): [(401, {"error": "not_signed_in"})]}, base)
    fake_sync = types.ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: _FakeSyncPlaywright(ctx)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    monkeypatch.setattr(mod, "deployed_sha", lambda api_url: "9605f5f249")
    monkeypatch.setattr(mod, "deployed_bundle", lambda base_url: "")
    monkeypatch.setattr(mod, "front_door_probe", lambda page, **k: {"hittable": True})
    monkeypatch.setattr(mod, "read_connection_surface", lambda page, **k: NOT_CONNECTED)

    rc = mod.main(["--base-url", base, "--auth-url", base, "--api-url", base,
                   "--allow-prod", "--out", str(tmp_path / "o")])
    assert rc == mod.EXIT_INSTRUMENT_ERROR == 3
    _assert_browser_reaped(ctx)


# ── the call sites the poll-only tests missed (#4291 review cycle 4) ────────
# The walk's unreadable-projection guards live at THREE points (step 5, the poll,
# step 7); only the poll was executed, so neutering step 5 or step 7 kept every
# test green while turning an instrument fault back into the product-facing
# `server_did_not_observe`. These execute those two call sites.

def test_walk_step5_unreadable_projection_is_an_instrument_error(monkeypatch, tmp_path):
    """The instrument cannot read the server's truth BEFORE the write either.
    It must not proceed to judge a screen against a projection it never read."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [(503, None)],
    }
    obs, ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED],
        mcp_tools_call=_MCP_OK)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert "never observed the agent" not in obs.verdict
    assert "projection_unreadable" in obs.verdict
    assert mod.failure_reason(obs.verdict, session_state=obs.session["state"]) \
        == mod.REASON_INSTRUMENT_ERROR
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    # it stopped at the read: no key was minted and nothing was written
    assert not any(c[0] == "POST" for c in ctx.request.calls)
    # ...and the recorded teardown STATUS (not merely that teardown is truthy):
    # this exit is reached only here, so it is the one place that can catch a
    # teardown state which arrived at it re-bound or emptied — such a state
    # records the truthy, non-residue `not_reached`, which would report an
    # unreaped org as clean.
    assert obs.teardown["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE


def test_walk_step7_unreadable_projection_is_an_instrument_error(monkeypatch, tmp_path):
    """The POST-observation read fails too (200 with a non-object body — an SPA
    fallback, a proxy error page). The run observed the write, so it must not
    report the product as having failed to show the connection — nor blame the
    server. It is an unreadable read, and that is an instrument fault."""
    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, _PROJ_OBSERVED,
                                             (200, None)],
    }
    obs, _ctx, mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED, CONNECTED],
        mcp_tools_call=_MCP_OK)

    assert obs.verdict.startswith("instrument-error:"), obs.verdict
    assert "never observed the agent" not in obs.verdict
    assert "projection_unreadable" in obs.verdict
    assert obs.reason == mod.REASON_INSTRUMENT_ERROR
    assert mod.exit_code_for(obs.reason) == mod.EXIT_INSTRUMENT_ERROR
    # the observation is NOT recorded as shown, and not as a product failure
    assert obs.assertions.get("shown_when_observed") is None
    # ...and the teardown STATUS, for the same reason as the exits above.
    assert obs.teardown["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE


def test_walk_with_an_explicit_agent_key_still_reads_the_truth_through_the_session(
        monkeypatch, tmp_path):
    """`--agent-key` supplies the WRITE credential only. The projection must
    still come from the walked session's own BFF path — a key-scoped read here
    is the cross-identity false pass, and it must be caught if it returns."""
    import tools.ship_test_onboarding as mod

    plan = {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, _PROJ_OBSERVED,
                                             _PROJ_OBSERVED],
    }
    base = "https://app.premiselabs.co"
    ctx = _FakeCtx(plan, base)

    import sys
    import types

    fake_sync = types.ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: _FakeSyncPlaywright(ctx)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    monkeypatch.setattr(mod, "deployed_sha", lambda api_url: "9605f5f249")
    monkeypatch.setattr(mod, "deployed_bundle", lambda base_url: "")
    monkeypatch.setattr(mod, "front_door_probe", lambda page, **k: {"hittable": True})
    monkeypatch.setattr(mod, "connection_surface_kind", lambda page, **k: "card")
    monkeypatch.setattr(mod, "page_body", lambda page: "")
    monkeypatch.setattr(mod, "recorded_body", lambda page: "")
    ui_values = [NOT_CONNECTED, CONNECTED]
    monkeypatch.setattr(mod, "read_connection_surface",
                        lambda page, **k: ui_values.pop(0))
    written_with = {}

    def _mcp_call(api_url, key, method, params=None, rid=1):
        if method == "tools/call":
            written_with["key"] = key
        return 200, _MCP_OK

    monkeypatch.setattr(mod, "mcp_call", _mcp_call)

    args = mod.build_parser().parse_args([
        "--base-url", base, "--auth-url", base, "--api-url", base, "--allow-prod",
        "--timeout", "800", "--settle-ms", "0", "--out", str(tmp_path / "o"),
        "--email", "ship@premiselabs.co", "--agent-key", "tt_explicit",
    ])
    obs = mod.run_walk(args)

    assert obs.verdict == "passed", (obs.verdict, obs.reason)
    # this test drives run_walk directly rather than through _run_fake_walk, so
    # it must make the single-exit guarantee explicit itself
    assert obs.teardown, "this exit must carry the teardown state"
    # this harness serves no org-list route, so the baseline read 404s and the
    # walk honestly records the fail-closed state: no baseline, no delete
    assert obs.teardown["status"] == _mod.TEARDOWN_BASELINE_UNAVAILABLE
    # the explicit key was used for the WRITE...
    assert written_with["key"] == "tt_explicit"
    # ...and no key was minted, and the truth came only from the session
    assert not any(c[1].endswith("/team/keys") for c in ctx.request.calls)
    for _method, url, _data in ctx.request.onboarding_state_calls():
        assert url.startswith(base + "/api/v1/onboarding/state"), url
    assert "agent-key" in obs.session["mechanism"]
    _assert_browser_reaped(ctx)


# ── #4319 — the run reaps the org it created ────────────────────────────────
#
# This is DESTRUCTIVE code, so these tests are the CONTROL, not a smoke test:
# each one pins a class from the issue's declared threat surface. The identity
# proof is DIFFERENTIAL, so the org-list reads arrive in call order and the plan
# supplies them as a queue:
#
#   1. the BASELINE, read before the wizard can create anything
#   2. the teardown-time read (`after`)
#   3. the post-DELETE confirmation read
#
# `_FakeRequester` pops a queue until one entry remains, then repeats that entry
# — so "the org is gone" is expressed as the LAST entry.
_ORG_ROUTE = "/api/v1/organizations"
_RUN_ORG = "Ship Test 123"
_AFTER_ROW = {"org_id": "org-1", "org_name": _RUN_ORG}

def _happy_base():
    """A FRESH plan every call: `_FakeRequester` mutates the entry lists (it pops
    them), so a shared module-level dict would leak one test's consumption into
    the next one's reads."""
    return {
        ("GET", "/api/session"): _SESSION_200,
        ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED, _PROJ_OBSERVED,
                                              _PROJ_OBSERVED],
        ("POST", "/api/v1/team/keys"): [(200, {"key": "tt_minted"})],
    }


_DELETE_OK = [(202, {"status": "delete_scheduled", "org_id": "org-1",
                     "grace_hours": 168,
                     "hard_delete_after": "2026-10-01T00:00:00Z"})]


def _run_teardown_walk(monkeypatch, tmp_path, *, reads, delete=None,
                       org_create=True, org_click_raises=False, skip_write=False,
                       org_name=_RUN_ORG, base=None, ui=None):
    """A walk whose org-list reads and DELETE are supplied by the caller."""
    plan = base if base is not None else _happy_base()
    plan[("GET", _ORG_ROUTE)] = reads
    if delete is not None:
        plan[("DELETE", _ORG_ROUTE + "/org-1")] = delete
    return _run_fake_walk(
        monkeypatch, tmp_path, plan=plan,
        ui_sequence=ui or [NOT_CONNECTED, CONNECTED],
        mcp_tools_call=_MCP_OK, org_create=org_create,
        org_click_raises=org_click_raises, skip_write=skip_write,
        org_name=org_name)


def _delete_calls(ctx):
    return [c for c in ctx.request.calls if c[0] == "DELETE"]


def test_the_walk_reaps_the_org_it_created_and_records_the_deletion(
        monkeypatch, tmp_path):
    """The happy path: baseline empty (a fresh account), the run's org listed at
    teardown, gone on the confirmation read ⇒ `deleted`, and the DELETE went out
    as a DELETE on the app origin's own proxy to the id the walked session's own
    list carried — never a constructed id."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [_AFTER_ROW]), (200, [])],
        delete=_DELETE_OK)

    assert obs.verdict == "passed", (obs.verdict, obs.reason)
    assert obs.teardown["status"] == _mod.TEARDOWN_DELETED
    assert obs.teardown["org_id"] == "org-1"
    assert obs.teardown["hard_delete_after"] == "2026-10-01T00:00:00Z"
    assert _delete_calls(ctx) == [
        ("DELETE", "https://app.premiselabs.co/api/v1/organizations/org-1", None)]
    # no POST was smuggled against the org path (the old `bff_api` fallback)
    assert not [c for c in ctx.request.calls
                if c[0] == "POST" and "/organizations/" in c[1]]


def test_the_name_the_run_writes_is_the_name_teardown_matches(monkeypatch, tmp_path):
    """One value, two uses: the name filled into the wizard is the name compared
    at teardown, so the two cannot drift."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [_AFTER_ROW]), (200, [])],
        delete=_DELETE_OK)
    assert ctx.page.fills == [('input[aria-label="Organization name"]', _RUN_ORG)]
    assert obs.teardown["org_name"] == _RUN_ORG


def test_a_same_named_pre_existing_org_is_never_a_teardown_candidate(
        monkeypatch, tmp_path):
    """T6 — the differential proof, in the ONLY shape that can tell it apart from
    a name match: a pre-existing org that carries this run's exact name. It is in
    the baseline, so it is not a candidate — a name-only selector would delete
    it. (Mutation-checked: dropping the set difference makes this RED.)"""
    old = {"org_id": "org-old", "org_name": _RUN_ORG}
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(200, [old]), (200, [old]), (200, [old])],
        org_name=_RUN_ORG)
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_LISTED
    assert _delete_calls(ctx) == []


def test_a_run_org_appearing_beside_a_pre_existing_one_is_the_one_deleted(
        monkeypatch, tmp_path):
    """The INCLUDE half of the differential: a NON-EMPTY baseline must not
    suppress the run's own org — the shape every run on an existing account has.
    (Mutation-checked: an implementation that only finds the run's org when the
    baseline is empty turns this RED.)"""
    old = {"org_id": "org-old", "org_name": "Something Else"}
    base = _happy_base()
    base[("DELETE", _ORG_ROUTE + "/org-1")] = _DELETE_OK
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(200, [old]), (200, [old, _AFTER_ROW]), (200, [old])],
        base=base)
    assert obs.teardown["status"] == _mod.TEARDOWN_DELETED
    assert obs.teardown["org_id"] == "org-1"
    assert [c[1] for c in _delete_calls(ctx)] == [
        "https://app.premiselabs.co/api/v1/organizations/org-1"]


def test_an_org_that_appeared_without_a_create_attempt_is_not_this_runs_to_delete(
        monkeypatch, tmp_path):
    """A set difference is not an identity proof: an org that joined this
    session's list without this run ever asking for one is refused, and recorded
    as residue rather than as a clean bill."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(200, []), (200, [_AFTER_ROW]), (200, [])],
        org_create=False)
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_ATTEMPTED
    assert obs.teardown["status"] in _mod.TEARDOWN_RESIDUE_STATES
    assert _delete_calls(ctx) == []


def test_a_numerically_looking_name_cannot_ride_a_name_match(monkeypatch, tmp_path):
    """T1 — the single candidate's name must EQUAL the name this run wrote. A
    foreign-named org is never deleted, even as the only candidate."""
    foreign = {"org_id": "org-x", "org_name": "Ship Test 1234 (someone else)"}
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [foreign]), (200, [])],
        delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_NAME_MISMATCH
    assert _delete_calls(ctx) == []


def test_two_new_orgs_are_ambiguous_and_delete_nothing(monkeypatch, tmp_path):
    """T2 — ambiguity is refused, never guessed at."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(200, []),
               (200, [_AFTER_ROW, {"org_id": "org-2", "org_name": _RUN_ORG}]),
               (200, [])],
        delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_AMBIGUOUS
    assert _delete_calls(ctx) == []


def test_an_unreadable_baseline_disables_teardown(monkeypatch, tmp_path):
    """T3 — no baseline means the identity is UNPROVEN. The run fails closed
    (residue) instead of deleting on a guess."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(503, {"error": "upstream_unavailable", "upstream_status": 429}),
               (200, [_AFTER_ROW]), (200, [])],
        delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_BASELINE_UNAVAILABLE
    assert _delete_calls(ctx) == []


def test_a_run_with_no_session_issues_no_org_request_at_all(monkeypatch, tmp_path):
    """T3 — a walk that never signed in never touched an org."""
    plan = {("GET", "/api/session"): [(401, {"error": "not_signed_in"})],
            ("GET", "/api/v1/onboarding/state"): [_PROJ_UNOBSERVED]}
    obs, ctx, _ = _run_fake_walk(monkeypatch, tmp_path, plan=plan,
                                 ui_sequence=[NOT_CONNECTED], mcp_tools_call=_MCP_OK)
    # the run never got as far as the baseline, so this is NOT "the org list was
    # unreadable" — claiming that would raise a residue alarm for a run that
    # could not have created anything
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_REACHED
    assert obs.teardown["status"] not in _mod.TEARDOWN_RESIDUE_STATES
    assert not [c for c in ctx.request.calls if "organizations" in c[1]]


def test_a_2xx_that_leaves_the_org_listed_is_not_a_deletion(monkeypatch, tmp_path):
    """T4 — verify the ARTIFACT, not the send: a 202 with no removal on the
    confirmation read is `not_confirmed`, never `deleted`."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(200, []), (200, [_AFTER_ROW]), (200, [_AFTER_ROW])],
        delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_CONFIRMED
    assert len(_delete_calls(ctx)) == 1


def test_an_unreadable_confirmation_is_not_a_confirmation(monkeypatch, tmp_path):
    """T4b — `org_id not in (ids or [])` is TRUE when the read failed. An
    unreadable confirmation must never be recorded as a deletion."""
    obs, _ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path,
        reads=[(200, []), (200, [_AFTER_ROW]),
               (503, {"error": "upstream_unavailable"})],
        delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_CONFIRMED
    assert obs.teardown["verify_status"] == 503


def test_a_refused_delete_is_recorded_with_its_upstream_status(
        monkeypatch, tmp_path):
    """T9 — a rate-limited upstream arrives as a proxied 503 that still carries
    `upstream_status`; the run records it and never turns it into a product
    result."""
    obs, _ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [_AFTER_ROW]), (200, [])],
        delete=[(503, {"error": "upstream_unavailable", "upstream_status": 429})])
    assert obs.teardown["status"] == _mod.TEARDOWN_HTTP_REFUSED
    assert obs.teardown["upstream_status"] == 429
    assert obs.verdict == "passed"
    assert obs.reason == ""


def test_a_run_that_created_nothing_issues_no_delete(monkeypatch, tmp_path):
    """Nothing was created ⇒ nothing to reap, and no request is made."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [])], org_create=False,
        delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_SKIPPED_NO_ORG
    assert _delete_calls(ctx) == []


def test_a_create_attempt_that_is_not_yet_listed_is_residue_not_clean(
        monkeypatch, tmp_path):
    """T6b — an empty candidate set AFTER a recorded create attempt is not
    'nothing to do': the list may simply not have caught up. Suspect residue,
    never a clean bill of health."""
    obs, _ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, []), (200, [])],
        org_create=True, delete=_DELETE_OK)
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_LISTED
    assert obs.teardown["status"] in _mod.TEARDOWN_RESIDUE_STATES


def test_a_create_click_that_raises_still_flags_the_residue(monkeypatch, tmp_path):
    """The attempt flag is set BEFORE the click, so a click that raises after
    dispatching the create cannot hide a created org — and the walk still writes
    its artifact through the `except`-site funnel."""
    obs, ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, []), (200, [])],
        org_create=True, org_click_raises=True, delete=_DELETE_OK)
    assert obs.verdict.startswith("failed:"), obs.verdict
    assert obs.teardown["status"] == _mod.TEARDOWN_NOT_LISTED
    assert _delete_calls(ctx) == []
    # the artifact was still written, with the teardown block in it
    artifact = (tmp_path / "ship-test" / "observation.json").read_text()
    assert '"teardown"' in artifact and _mod.TEARDOWN_NOT_LISTED in artifact


def test_teardown_cannot_change_the_verdict_or_lose_the_artifact(
        monkeypatch, tmp_path):
    """T5 — the #4291 conflation guard, in both directions. A cleanup that
    RAISES must not flip a pass, must not propagate, and must not cost the
    artifact."""
    import json

    def _boom(obs, td):
        raise RuntimeError("cleanup exploded")

    monkeypatch.setattr(_mod, "_run_teardown", _boom)
    obs, _ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, []), (200, [])])
    assert obs.verdict == "passed", obs.verdict
    assert obs.reason == ""
    assert obs.teardown["status"] == _mod.TEARDOWN_FAILED
    written = json.loads((tmp_path / "ship-test" / "observation.json").read_text())
    assert written["verdict"] == "passed"
    assert written["teardown"]["status"] == _mod.TEARDOWN_FAILED


def test_a_passed_run_with_a_failed_teardown_still_exits_zero(monkeypatch):
    """The exit code is the PRODUCT's outcome. Cleanup must never move it — in
    either direction."""
    obs = _mod.Observation(started_at="t", target={})
    obs.verdict = "passed"
    obs.teardown = {"status": _mod.TEARDOWN_FAILED, "detail": "boom"}
    monkeypatch.setattr(_mod, "run_walk", lambda args: obs)
    assert _mod.main(["--allow-prod"]) == _mod.EXIT_PASSED


def test_a_successful_teardown_cannot_rescue_a_failing_verdict(
        monkeypatch, tmp_path):
    """T5's other direction: cleanup is not the product. A failing verdict and
    its reason survive a clean deletion."""
    base = _happy_base()
    # one entry repeats: the server never observes anything, so the skipped run
    # stays the `positive_not_attempted` class rather than becoming a failure
    base[("GET", "/api/v1/onboarding/state")] = [_PROJ_UNOBSERVED]
    obs, ctx, mod = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [_AFTER_ROW]), (200, [])],
        delete=_DELETE_OK, skip_write=True, ui=[NOT_CONNECTED, NOT_CONNECTED],
        base=base)
    assert obs.verdict == mod.INCOMPLETE_SKIPPED_WRITE
    assert mod.exit_code_for(obs.reason) == mod.EXIT_FAILED
    assert obs.teardown["status"] == _mod.TEARDOWN_DELETED
    assert len(_delete_calls(ctx)) == 1


def test_keep_org_records_a_deliberate_residue_and_never_deletes(
        monkeypatch, tmp_path):
    """T7's explicit form: the one supported way to leave the org behind, and it
    is recorded as such rather than silently skipped."""
    plan = _happy_base()
    plan[("GET", _ORG_ROUTE)] = [(200, []), (200, [_AFTER_ROW]), (200, [])]
    plan[("DELETE", _ORG_ROUTE + "/org-1")] = _DELETE_OK
    obs, ctx, _ = _run_fake_walk(
        monkeypatch, tmp_path, plan=plan, ui_sequence=[NOT_CONNECTED, CONNECTED],
        mcp_tools_call=_MCP_OK, org_create=True, org_name=_RUN_ORG, keep_org=True)
    assert obs.teardown["status"] == _mod.TEARDOWN_KEPT
    assert _delete_calls(ctx) == []


def test_no_exit_from_the_walk_writes_the_artifact_without_teardown():
    """The single-writer / single-teardown property is why the artifact can be
    trusted.

    A REFACTOR GUARD, not an obfuscation proof — say plainly what it checks and
    what it deliberately does not. It is parsed from ASTs rather than
    text-scanned, so a behaviour-identical reformat (a wrapped call, `_finish (…)`
    with a space, a renamed local) cannot false-red it, which is the false-red a
    literal-text count produces.

    THE INVARIANT (AC9): the AUTHORITATIVE writer is spelled exactly once, in
    `run_walk`, AFTER the bounded teardown; the ONE teardown is a statement of the
    `finally` that encloses the `_walk` call; and no `_walk` exit other than
    `_finalize`'s single PRE-TEARDOWN write serializes anything.

    What it checks:
      (a) `run_walk` spells `_finish` exactly once, and that call is AFTER the
          `try`/`finally` holding `_teardown_browser`;
      (b) `run_walk` spells `_teardown_browser` exactly once, in the `finally`
          body of the try whose body holds the `_walk(` call;
      (c) `run_walk` does not spell `_finalize`, and none of the three functions
          calls a bare `getattr`/`globals`/`eval`/`exec`/`vars` (the
          string-built-name route to a writer);
      (d) that ONE `td` — built once, by unpacking `_build_observation` — is
          passed to `_walk` and to the teardown, and every `_finalize(` call in
          `_walk` passes three positional args whose third is `_walk`'s `td`
          PARAMETER, the same name at every site;
      (e) `_walk` spells neither `_finish` nor `_write_observation`, and
          `_finalize` spells `_finish` never and `_write_observation` exactly once.

    WHY the arity/identity half matters: `_finalize`'s third parameter defaults to
    None, so `_finalize(obs, out_dir)` and `_finalize(obs, out_dir, None)` write
    the artifact with an EMPTY teardown block; and re-pointing the teardown local
    at a second `Teardown(...)` yields `ctx is None`, so `_run_teardown` records
    the truthy, deliberately-non-residue `not_reached` for an org this run never
    reaped (the #4291 conflation).

    NOT checked here, by construction — a source assertion cannot be an
    adversarial proof, and extending it just moves the boundary. The Store-context
    count sees Name-bindings, so NON-Name ones escape it: `match … case _ as td`,
    `import … as td`, `except … as td`. And further forms get past the whole half:
    an alias of `_finish`/`_finalize`, attribute-form `getattr`, and mutating the
    teardown object's fields in place.

    What covers those forms is the recorded teardown STATUS. Every `_finalize`
    exit in `_walk` is executed by at least one test, and at least one of the
    tests reaching each exit asserts the status — not merely that `obs.teardown`
    is truthy, since `not_reached` satisfies truthiness and a truthiness-only
    assert cannot see a residue state degrade into a clean one. Five of them were
    reached by no test at all until #4843.

    SCOPE: `_finalize` exits only. Two abort paths sit OUTSIDE `run_walk`'s
    try/except and write no artifact at all — an `--out` that cannot be created,
    and a driver that will not start — and they are now covered directly
    (#4875), not by this guard.
    """
    walk_tree = ast.parse(textwrap.dedent(_inspect.getsource(_mod.run_walk)))
    body_tree = ast.parse(textwrap.dedent(_inspect.getsource(_mod._walk)))
    finalize_tree = ast.parse(textwrap.dedent(_inspect.getsource(_mod._finalize)))

    def _names(tree):
        found = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        found |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        return found

    def _calls(tree, name):
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == name]

    dynamic = {"getattr", "globals", "eval", "exec", "vars"}
    for label, tree in (("run_walk", walk_tree), ("_walk", body_tree),
                        ("_finalize", finalize_tree)):
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) in dynamic], (
            f"{label} must not call a bare {sorted(dynamic)} — the "
            "string-built-name route to a writer")

    # (a) exactly one authoritative writer, which runs after the teardown.
    finishes = _calls(walk_tree, "_finish")
    assert len(finishes) == 1, (
        f"run_walk must spell _finish exactly once; got {len(finishes)}")
    # (b) exactly one teardown, in the `finally` of the try that holds `_walk`.
    teardowns = _calls(walk_tree, "_teardown_browser")
    assert len(teardowns) == 1, (
        "run_walk must enter the browser teardown exactly once; got "
        f"{len(teardowns)}")
    walk_calls = _calls(walk_tree, "_walk")
    assert len(walk_calls) == 1, (
        f"run_walk must call _walk exactly once; got {len(walk_calls)}")
    enclosing = [
        node for node in ast.walk(walk_tree)
        if isinstance(node, ast.Try)
        and any(call in ast.walk(node) for call in walk_calls)
        and any(expr in ast.walk(node) for expr in teardowns)
    ]
    assert len(enclosing) == 1, "the teardown must live in the try around _walk"
    assert any(teardowns[0] in ast.walk(stmt) for stmt in enclosing[0].finalbody), (
        "the teardown must be a statement of the `finally` body")
    assert any(walk_calls[0] in ast.walk(stmt) for stmt in enclosing[0].body), (
        "the try's body must contain the _walk call")
    # (c) the pre-teardown writer is `_walk`'s funnel, never `run_walk`'s.
    assert not _calls(walk_tree, "_finalize"), (
        "_finalize must not be called from run_walk")
    assert finishes[0].lineno > teardowns[0].lineno, (
        "the authoritative write must run after the bounded teardown")

    # (d) the ONE teardown state, built once and passed unchanged.
    walk_params = list(_inspect.signature(_mod._walk).parameters)
    assert "td" in walk_params, "_walk must take the teardown state as a parameter"
    stores = [n.id for n in ast.walk(walk_tree)
              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
    assert stores.count("td") == 1, (
        f"run_walk binds 'td' {stores.count('td')} times; the teardown state must "
        "be built once and passed unchanged")
    assert [a.id for a in walk_calls[0].args if isinstance(a, ast.Name)].count("td") == 1, (
        "run_walk must pass the teardown state to _walk")
    assert (len(teardowns[0].args) >= 2 and isinstance(teardowns[0].args[1], ast.Name)
            and teardowns[0].args[1].id == "td"), (
        "the teardown must be handed the run's own teardown state")
    finalize_calls = _calls(body_tree, "_finalize")
    assert finalize_calls, "no _finalize exit found in _walk"
    assert len(finalize_calls) == 11, (
        "_walk must keep its 11 _finalize exits (the import guard's moved to "
        f"_start_driver); got {len(finalize_calls)}")
    assert {len(c.args) for c in finalize_calls} == {3}, (
        "every _finalize call in _walk must pass the teardown state; got "
        f"argument counts {sorted(len(c.args) for c in finalize_calls)}")
    assert all(isinstance(c.args[2], ast.Name) and c.args[2].id == "td"
               for c in finalize_calls), (
        "every _finalize call in _walk must pass _walk's own `td` parameter, not "
        "a default, a literal or an unrelated name")

    # (e) no OTHER writer anywhere in the walk, and one pre-teardown write.
    assert "_finish" not in _names(body_tree), "_walk must not spell _finish"
    assert "_write_observation" not in _names(body_tree), (
        "_walk must not spell the writer; its exits go through _finalize")
    assert not _calls(finalize_tree, "_finish"), (
        "_finalize must not print through _finish")
    assert len(_calls(finalize_tree, "_write_observation")) == 1, (
        "_finalize must write the pre-teardown document exactly once")


def test_an_org_name_the_product_would_refuse_is_rejected_before_any_browser(
        monkeypatch, capsys):
    """Teardown matches the name this run WROTE, so a name the product would
    refuse would make the created org silently unreapable. Exit 2, and the walk
    is never started. HERMETIC: `run_walk` is stubbed, so a guard regression
    cannot reach the network from the test suite."""
    started = []
    monkeypatch.setattr(_mod, "run_walk", lambda args: started.append(args))
    assert _mod.main(["--allow-prod", "--org-name", "bad.name"]) == _mod.EXIT_USAGE
    assert "invalid --org-name" in capsys.readouterr().err
    assert _mod.main(["--allow-prod", "--org-name", "Foo\nBar"]) == _mod.EXIT_USAGE
    assert "invalid --org-name" in capsys.readouterr().err
    assert _mod.main(["--allow-prod", "--org-name", "x" * 65]) == _mod.EXIT_USAGE
    assert "invalid --org-name" in capsys.readouterr().err
    assert started == []


def test_a_padded_org_name_is_trimmed_to_what_the_product_stores(monkeypatch):
    """Both the wizard and tenant-provision TRIM before validating and storing,
    so the name teardown matches must be the trimmed one. A `$`-anchored match
    (or no trim) would accept `"Foo "` / `"Foo\n"` and then hunt for a name the
    product never stored. HERMETIC: `run_walk` is stubbed."""
    seen = {}

    def _fake(args):
        seen["org_name"] = args.org_name
        obs = _mod.Observation(started_at="t", target={})
        obs.verdict = "passed"
        obs.teardown = {"status": _mod.TEARDOWN_SKIPPED_NO_ORG}
        return obs

    monkeypatch.setattr(_mod, "run_walk", _fake)
    for raw in ("  Foo  ", "Foo\n", "Foo "):
        assert _mod.main(["--allow-prod", "--org-name", raw]) == _mod.EXIT_PASSED
        assert seen["org_name"] == "Foo"


def test_keep_org_is_cli_only_and_never_ambiently_injected():
    """One ambient variable must never turn teardown off for every run."""
    assert _mod.build_parser().parse_args([]).keep_org is False
    assert "SHIP_TEST_KEEP_ORG" not in _inspect.getsource(_mod.build_parser)


def test_bff_api_sends_delete_as_delete_and_refuses_an_unknown_verb():
    """`bff_api` used to send ANY non-GET as POST, which would have downgraded
    the teardown's DELETE into a wrong-method request."""
    base = "https://app.premiselabs.co"
    ctx = _FakeCtx({("DELETE", "/api/v1/organizations/org-1"):
                    [(202, {"org_id": "org-1"})]}, base)
    status, body = _mod.bff_api(ctx, base, "DELETE", "/organizations/org-1")
    assert (status, body) == (202, {"org_id": "org-1"})
    assert [c[0] for c in ctx.request.calls] == ["DELETE"]
    with pytest.raises(ValueError):
        _mod.bff_api(ctx, base, "PUT", "/organizations/org-1")


@pytest.mark.parametrize("body,expected", [
    ([], {}),
    ([{"org_id": "a", "org_name": "A"}], {"a": "A"}),
    ({"organizations": [{"org_id": "a", "org_name": "A"}]}, {"a": "A"}),
    ({"unexpected": []}, None),
    ([{"no_org_id": 1}], None),
    ("nope", None),
    (None, None),
])
def test_read_org_ids_reads_only_a_recognized_org_list(body, expected):
    """An unrecognized shape parsed as \"no orgs\" would silently switch the
    identity proof off (baseline) and silently confirm a deletion (verify)."""
    base = "https://app.premiselabs.co"
    ctx = _FakeCtx({("GET", "/api/v1/organizations"): [(200, body)]}, base)
    status, ids, _upstream = _mod.read_org_ids(ctx, base)
    assert status == 200
    assert ids == expected


def test_read_org_ids_reports_a_proxied_upstream_status_and_fails_closed():
    base = "https://app.premiselabs.co"
    ctx = _FakeCtx({("GET", "/api/v1/organizations"):
                    [(503, {"error": "upstream_unavailable", "upstream_status": 429})]},
                   base)
    assert _mod.read_org_ids(ctx, base) == (503, None, 429)


# ── #4907 — the browser teardown's record, bound and single verdict print ────

def test_a_completed_run_records_the_browser_teardown_block(monkeypatch, tmp_path):
    """The record has a declared home ON DISK, not just in memory: `asdict`
    serializes DECLARED fields only, so an undeclared attribute is silently
    dropped from the artifact. UPGRADED by the bound's tests to assert the VALUE:
    a run that ENTERED and COMPLETED the teardown is never `not_run` on disk."""
    import json

    import tools.ship_test_onboarding as mod

    _obs, _ctx, _ = _run_teardown_walk(
        monkeypatch, tmp_path, reads=[(200, []), (200, [])], org_create=False)
    written = json.loads((tmp_path / "ship-test" / "observation.json").read_text())
    block = written["browser_teardown"]
    assert set(block) == {"outcome", "closes", "detail"}, block
    assert block["outcome"] in mod.BROWSER_TEARDOWN_OUTCOMES, block
    assert isinstance(block["closes"], list)
    # THE VALUE, not merely the shape: this run's teardown ran, so `not_run` is
    # NOT an acceptable value for it.
    assert block["outcome"] == mod.BROWSER_TEARDOWN_CLEAN, block
    assert [c["name"] for c in block["closes"]] == ["context", "browser", "playwright"]
    assert {c["how"] for c in block["closes"]} == {"closed"}
    assert block["detail"] == ""


def test_the_browser_teardown_vocabulary_is_closed() -> None:
    """The closed set the artifact may carry. `passed` is deliberately absent:
    the browser teardown is never a product verdict."""
    import tools.ship_test_onboarding as mod

    assert mod.BROWSER_TEARDOWN_OUTCOMES == (
        "not_run", "clean", "close_error", "watchdog_kill", "driver_absent",
        "abandoned")
    assert len(set(mod.BROWSER_TEARDOWN_OUTCOMES)) == 6
    assert "passed" not in mod.BROWSER_TEARDOWN_OUTCOMES
    for name in ("BROWSER_TEARDOWN_NOT_RUN", "BROWSER_TEARDOWN_CLEAN",
                 "BROWSER_TEARDOWN_CLOSE_ERROR", "BROWSER_TEARDOWN_WATCHDOG_KILL",
                 "BROWSER_TEARDOWN_DRIVER_ABSENT", "BROWSER_TEARDOWN_ABANDONED"):
        assert getattr(mod, name) in mod.BROWSER_TEARDOWN_OUTCOMES


def test_the_atomic_write_leaves_no_temp_file_behind_when_it_fails(
        monkeypatch, tmp_path) -> None:
    """The write is `tmp` + `os.replace`, so a failure must not leave the tmp for
    the next run to trip over — or for a reader to mistake for the artifact. The
    gate found the cleanup missing; nothing covered it."""
    import os

    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    out_dir = tmp_path / "ship-test"
    out_dir.mkdir(parents=True, exist_ok=True)

    def _boom(src, dst):
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        mod._write_observation(obs, out_dir)
    leftovers = sorted(p.name for p in out_dir.iterdir())
    assert leftovers == [], f"the failed write left {leftovers} behind"
    assert not (out_dir / "observation.json").exists()


def test_the_atomic_write_ignores_a_symlink_at_the_old_predictable_temp_name(
        tmp_path) -> None:
    """FIX 5. The old temp name was predictable (``.<name>.<pid>.tmp``) and
    ``write_text`` FOLLOWS a symlink, so a link planted there was written through.
    The ``mkstemp`` temp is unguessable and ``os.replace`` renames onto the
    artifact path, never through the planted link's target (the #4098 class
    documented in ``tools/branch_reaper.py::_write_text_safe``)."""
    import os

    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    out_dir = tmp_path / "ship-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    planted = out_dir / f".observation.json.{os.getpid()}.tmp"
    planted.symlink_to(victim)

    mod._write_observation(obs, out_dir)

    assert victim.read_text() == "untouched", "the planted link was written through"
    assert (out_dir / "observation.json").is_file()


def test_a_symlinked_observation_leaf_is_refused(tmp_path) -> None:
    """FIX 5. A symlink planted AT the artifact path must be refused, not followed:
    ``write_text`` would truncate its target, and the artifact is written into an
    operator-controlled ``--out`` that defaults inside the checkout."""
    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    out_dir = tmp_path / "ship-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "victim.json"
    victim.write_text("untouched")
    (out_dir / "observation.json").symlink_to(victim)

    with pytest.raises(OSError):
        mod._write_observation(obs, out_dir)
    assert victim.read_text() == "untouched"
    assert (out_dir / "observation.json").is_symlink()


def test_the_printed_verdict_is_scrubbed(capsys, tmp_path) -> None:
    """FIX 8e. The verdict can carry free text assembled from an exception
    message, so the shared print path scrubs it — the abandon path prints through
    the same helper."""
    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    obs.verdict = "failed: RuntimeError: tt_SEKRIT1234 refused"
    mod._print_summary(obs, tmp_path / "observation.json")
    out = capsys.readouterr().out
    assert "tt_SEKRIT1234" not in out, out
    assert "[REDACTED]" in out, out


def test_a_closing_failure_detail_is_scrubbed() -> None:
    """FIX 8e. A closer's exception message is free text and may echo a credential;
    it is scrubbed like every other recorded site."""
    import tools.ship_test_onboarding as mod

    class _Raising:
        def close(self):
            raise RuntimeError("session_token=tt_SEKRIT1234 refused")

    class _Driver:
        def stop(self):
            pass

    td = mod.Teardown(ctx=_Raising(), browser=_Raising())
    closes = []
    mod._close_all(_Driver(), td, closes)
    assert "tt_SEKRIT1234" not in str(closes), closes
    assert "[REDACTED]" in str(closes), closes


def test_the_abandon_detail_is_scrubbed(monkeypatch, tmp_path) -> None:
    """FIX 8e. The abandon record's detail is recorded free text like any other, so
    it is scrubbed before it lands in the artifact."""
    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    obs.verdict = "passed"
    record = mod._browser_teardown_record()
    exits = []
    monkeypatch.setattr(mod, "_exit_now", lambda code: exits.append(code))
    mod._abandon(obs, record, "tt_SEKRIT1234", tmp_path)
    assert "tt_SEKRIT1234" not in record["detail"], record["detail"]
    assert "[REDACTED]" in record["detail"], record["detail"]
    assert exits == [mod.EXIT_PASSED], exits


def test_a_teardown_that_raises_a_base_exception_completes_and_is_recorded(
        monkeypatch, tmp_path) -> None:
    """FIX 8i. The instrument's OWN teardown can raise a `BaseException` (a
    `KeyboardInterrupt` during `pw.stop()`): the run must still complete, the
    closer's failure recorded, and the verdict left alone. This is the REAL knob
    (the killed-inside-the-window test substitutes `_teardown_browser` wholesale
    and so cannot exercise this)."""
    obs, _ctx, _mod = _happy_bound_run(monkeypatch, tmp_path, stop_raises_base=True)
    assert obs.verdict == "passed", (obs.verdict, obs.reason)
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "close_error", block
    assert [c["name"] for c in block["closes"]] == ["context", "browser", "playwright"]
    assert block["closes"][2]["how"] == "close_error", block["closes"]
    assert "KeyboardInterrupt" in block["closes"][2]["detail"], block["closes"]


def test_the_production_enumerator_returns_only_this_processs_own_marked_child():
    """AC4's safety clause at the PRODUCTION seam, not the fake's.

    Every other teardown test stubs `_driver_pid_and_starttime`, so the filter
    that decides what may EVER be signalled — `ppid == os.getpid()` plus the
    driver marker — would otherwise be pinned against the real process table by
    nothing. This is that ONE live test. It spawns a REAL child whose argv
    carries the marker, polls the enumeration inside a short DEADLINE (a bounded
    condition re-check, not a fixed sleep), and requires the whole identity
    (parent pid AND start time) to re-read identically from `_child_identity` —
    the pair the watchdog's re-check compares. The real table here holds exactly
    one marked child, so this test pins the OWN-CHILD half against it; the
    MARKER half (a marked child winning over an earlier unmarked one) is pinned
    deterministically at the `ps`-output seam below.

    Every OTHER claim — which candidate wins over an earlier unmarked one, the
    parent filter, the non-positive-pid refusal, the untruncated `ps` query — is
    pinned deterministically at the `ps`-OUTPUT seam below, because a venue
    whose `ps` renders the table differently (the CI runner truncates the last
    column) must not decide whether this instrument
    is correct. The marker is the LAST argv token on purpose: that is the position
    the venue truncation cuts, so this test also exercises the `-ww` fix on a
    real table rather than merely on a fake one."""
    import os
    import subprocess
    import sys

    import tools.ship_test_onboarding as mod

    if mod._ps_binary() is None:
        pytest.skip(
            f"the platform has no absolute `ps` at {mod._PS_BIN}: there is no "
            "live process table for the enumerator to read")

    marker = mod.TEARDOWN_DRIVER_MARKERS[0]
    driver = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", marker])
    try:
        deadline = time.monotonic() + 10
        pid, started, status = None, None, None
        while time.monotonic() < deadline:
            pid, started, status = mod._driver_pid_and_starttime()
            if pid is not None:
                break
            time.sleep(0.1)
        assert pid == driver.pid, (
            f"enumerated {pid}; expected the MARKED child {driver.pid}")
        assert status == mod.DRIVER_ENUM_FOUND, status
        assert started, "no start time was carried alongside the pid"
        assert mod._child_identity(pid) == (os.getpid(), started), (
            "the whole identity (parent pid AND start time) must re-read "
            "identically in ONE read, or the TOCTOU re-check would silently "
            "refuse to signal the real driver")
    finally:
        driver.kill()
        driver.wait()


class _PS:
    """The `subprocess.run` return the `ps` readers need: a `.stdout`."""

    def __init__(self, stdout=""):
        self.stdout = stdout


@pytest.mark.parametrize("lstart,rendering", [
    ("Thu Jan  1 00:00:00 2026", "the C padding (day-of-month is space-padded)"),
    ("2026年 1月 1日 00時00分00秒", "a 4-token locale rendering"),
    ("Чт янв 1 00:00:00 2026 MSK", "a 6-token locale rendering"),
])
def test_the_enumerators_start_time_comes_from_the_same_reader_as_the_recheck(
        monkeypatch, lstart, rendering) -> None:
    """FIX A. `%c`'s token COUNT is locale-dependent — `LC_ALL=ja_JP.UTF-8`
    renders 4 tokens, `LC_ALL=ru_RU.UTF-8` 6 — so a positional reconstruction of
    `lstart` from the enumeration line either reads the wrong field or truncates
    the time. Then every rung's TOCTOU re-check refuses, the driver is never
    signalled, and a healthy run falls to `_abandon` while the Chromium tree is
    orphaned. The enumerator therefore NEVER reconstructs the time from its own
    `ps` line: the whole identity comes from `_child_identity` — the SAME reader
    the re-check calls — so the enumerated value and the re-read are equal BY
    CONSTRUCTION for every rendering, and the ppid travels in the SAME read."""
    import os

    import tools.ship_test_onboarding as mod

    def _fake_run(argv, **k):
        if "pid=,ppid=,command=" in argv:
            # the enumerator's selection: the command is the LAST field and no
            # `lstart` appears anywhere in it, so there is nothing to slice.
            return _PS(stdout=f"4242 {os.getpid()} python playwright run-driver")
        # the ONE identity read: ppid first, then the locale-rendered start time
        return _PS(stdout=f"{os.getpid()} {lstart}\n")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    pid, started, status = mod._driver_pid_and_starttime()
    assert pid == 4242, pid
    assert status == mod.DRIVER_ENUM_FOUND, status
    assert started == mod._norm_start_time(lstart), (
        f"{rendering}: the enumerator must carry the identity read's own start "
        f"time; got {started!r}")
    assert mod._child_identity(4242) == (os.getpid(), started), (
        f"{rendering}: the re-check must read the same whole identity")


@pytest.mark.parametrize("bad_pid", ["0", "-1"])
def test_a_nonpositive_pid_is_never_returned_as_the_driver(monkeypatch, bad_pid) -> None:
    """FIX 2. `os.kill(-1, SIGKILL)` signals every process this uid may signal, so
    a field-split that misread the pid must never become the signal target."""
    import os

    import tools.ship_test_onboarding as mod

    line = f"{bad_pid} {os.getpid()} Thu Jan  1 00:00:00 2026 python run-driver"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _PS(stdout=line))
    assert mod._driver_pid_and_starttime() == (
        None, None, mod.DRIVER_ENUM_NO_CANDIDATE)


def test_the_enumerator_picks_the_marked_own_child_not_an_earlier_unmarked_one(
        monkeypatch) -> None:
    """The marker filter, pinned DETERMINISTICALLY at the `ps`-output seam.

    The live-table test can only fail a "returned the first child" mutant when
    the venue's `ps` happens to expose both children in a stable order, so the
    preference is pinned here instead, on a fake table whose pids are
    DELIBERATELY NON-MONOTONIC: an unmarked own child first, the marked child
    the enumerator must return (pid 5000 — the MIDDLE of the marked pids, so no
    pid-order coincidence yields it), then an unmarked child and two more marked
    ones (2000, 9000). The enumerator must return the FIRST MARKED child in
    table order. That one table reddens a mutant that drops the marker guard or
    returns the first parseable child (1111), one that returns the last
    candidate (9000), and one that iterates in pid order — sorted (2000) or
    reverse-sorted (9000)."""
    import os

    import tools.ship_test_onboarding as mod

    def _fake_run(argv, **k):
        if "pid=,ppid=,command=" in argv:
            return _PS(stdout=(
                f"1111 {os.getpid()} python -c import time; time.sleep(30)\n"
                f"5000 {os.getpid()} python -c import time; time.sleep(30) "
                f"run-driver\n"
                f"3333 {os.getpid()} python -c import time; time.sleep(30)\n"
                f"2000 {os.getpid()} python -c import time; time.sleep(30) "
                f"run-driver\n"
                f"9000 {os.getpid()} python -c import time; time.sleep(30) "
                f"run-driver\n"))
        return _PS(stdout=f"{os.getpid()} Thu Jan  1 00:00:00 2026\n")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    assert mod._driver_pid_and_starttime() == (
        5000, mod._norm_start_time("Thu Jan  1 00:00:00 2026"),
        mod.DRIVER_ENUM_FOUND)


def test_the_enumerator_ignores_a_marked_process_that_is_not_its_own_child(
        monkeypatch) -> None:
    """The parent filter, pinned at the same `ps`-output seam.

    `os.kill` on a marker-matching process that is NOT this run's child would
    signal a stranger's Playwright driver (or a pid reused by one). A process
    whose command carries the marker but whose parent is another pid is listed
    FIRST; the enumerator must skip it and take THIS process's child. A mutant
    that drops the `ppid != me` guard returns 4242 here and is RED."""
    import os

    import tools.ship_test_onboarding as mod

    stranger = os.getpid() + 1

    def _fake_run(argv, **k):
        if "pid=,ppid=,command=" in argv:
            return _PS(stdout=(
                f"4242 {stranger} python playwright run-driver\n"
                f"4343 {os.getpid()} python playwright run-driver\n"))
        return _PS(stdout=f"{os.getpid()} Thu Jan  1 00:00:00 2026\n")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    pid, _started, status = mod._driver_pid_and_starttime()
    assert (pid, status) == (4343, mod.DRIVER_ENUM_FOUND), (pid, status)


def test_the_enumerator_rejects_a_pid_whose_identity_re_read_names_another_parent(
        monkeypatch) -> None:
    """The POST-read parent re-verification, pinned at the `ps`-output seam.

    The in-table filter (`ppid != me`) and the identity re-read's `ppid` are two
    different reads. Between them the real child can exit and its pid be REUSED
    by a process with a different parent: the enumeration saw `ppid == me`, the
    identity read names someone else. That second read is the value the
    watchdog's TOCTOU re-check compares, so such a pid must NOT be signalled —
    and must not be reported as `no_candidate` either, since a marker-matching
    child WAS enumerated. A mutant that drops `identity[0] != me` returns
    4242/`found` here and is RED."""
    import os

    import tools.ship_test_onboarding as mod

    def _fake_run(argv, **k):
        if "pid=,ppid=,command=" in argv:
            return _PS(stdout=(
                f"4242 {os.getpid()} python playwright run-driver\n"))
        return _PS(stdout=f"{os.getpid() + 1} Thu Jan  1 00:00:00 2026\n")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    assert mod._driver_pid_and_starttime() == (
        None, None, mod.DRIVER_ENUM_IDENTITY_UNREADABLE)


def test_both_ps_reads_ask_for_an_untruncated_field(monkeypatch) -> None:
    """The CI-only defect of #4956, pinned without a live process table.

    `command` is the unbounded `ps` field, and a host can truncate its LAST
    column: the CI runner did, and the hostedtoolcache interpreter path alone is
    ~49 characters, which puts a trailing `run-driver` marker past the width the
    runner cut at (80 columns). The marker then disappears, the enumerator finds
    no candidate, and a healthy run abandons with its Chromium tree live. BOTH
    readers therefore ask for unlimited width: dropping `-ww`
    from the enumeration reddens this test, and dropping it from the identity
    read would let a truncated start time collapse two processes into one
    identity."""
    import tools.ship_test_onboarding as mod

    seen = []

    def _fake_run(argv, **k):
        seen.append(list(argv))
        return _PS(stdout="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    mod._driver_pid_and_starttime()
    mod._child_identity(1)
    assert len(seen) == 2, seen
    for argv in seen:
        assert argv[0] == mod._PS_BIN, argv
        assert "-ww" in argv, argv


def test_the_ps_binary_is_absolute_and_a_missing_one_refuses(monkeypatch) -> None:
    """FIX 2. Both `ps` readers run the ABSOLUTE binary, so a PATH-planted `ps`
    cannot choose the pid the watchdog signals; when that binary is absent the
    reader refuses (no pid, so nothing is signalled) rather than falling back."""
    import os

    import tools.ship_test_onboarding as mod

    assert os.path.isabs(mod._PS_BIN), mod._PS_BIN
    calls = []

    def _fake_run(argv, **k):
        calls.append(argv)
        return _PS(stdout="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    mod._driver_pid_and_starttime()
    mod._child_identity(1)
    assert len(calls) == 2, calls
    assert all(c[0] == mod._PS_BIN for c in calls), calls

    monkeypatch.setattr(mod, "_PS_BIN", "/nonexistent/ps")
    assert mod._driver_pid_and_starttime() == (
        None, None, mod.DRIVER_ENUM_NO_CANDIDATE)
    assert mod._child_identity(1) is None


def test_the_ps_read_is_budgeted_inside_the_ladder_slack(monkeypatch) -> None:
    """FIX 8d. The last rung is at 3B/4, so B/4 of slack remains; the re-read's own
    bound must sit inside that slack, and both `ps` readers must use it, or a slow
    `ps` could push the abandon past the bound."""
    import tools.ship_test_onboarding as mod

    assert 0 < mod._ps_timeout() <= mod.TEARDOWN_BOUND_S / 4
    # ...and the cap tracks the BOUND, not merely the absolute constant
    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    assert mod._ps_timeout() == mod.TEARDOWN_BOUND_S / 4, mod._ps_timeout()
    seen = []

    def _fake_run(argv, **k):
        seen.append(k.get("timeout"))
        return _PS(stdout="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    mod._driver_pid_and_starttime()
    mod._child_identity(1)
    assert seen == [mod._ps_timeout(), mod._ps_timeout()], seen


def test_the_identity_read_is_retried_once_before_giving_up(monkeypatch) -> None:
    """FIX B. The identity read is the one read that can fail transiently (a `ps`
    timeout, a momentary exit race), so it is retried ONCE: a single failed read
    is not evidence about the child. The retry is pinned by COUNT, so a mutant
    that gives up on the first `None` (or loops forever) is RED."""
    import os

    import tools.ship_test_onboarding as mod

    reads = []

    def _flaky(pid):
        reads.append(pid)
        return None if len(reads) == 1 else (os.getpid(), "fake-lstart")

    monkeypatch.setattr(mod, "_child_identity", _flaky)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: _PS(stdout=f"4242 {os.getpid()} python run-driver"))
    assert mod._driver_pid_and_starttime() == (
        4242, "fake-lstart", mod.DRIVER_ENUM_FOUND)
    assert reads == [4242, 4242], reads


def test_an_unreadable_identity_is_reported_distinctly_from_no_candidate(
        monkeypatch) -> None:
    """FIX B. When a marker-matching child WAS enumerated but its identity could
    not be read (or no longer verified as this process's child), the enumerator
    must not report `no_candidate`: the record's `detail` has to name WHICH
    nothing it was. The read is attempted twice (the retry), then the status is
    `identity_unreadable` — a DIFFERENT status from `no_candidate` for the same
    `(None, None)` pid/start pair."""
    import os

    import tools.ship_test_onboarding as mod

    reads = []

    def _unreadable(pid):
        reads.append(pid)
        return None

    monkeypatch.setattr(mod, "_child_identity", _unreadable)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: _PS(stdout=f"4242 {os.getpid()} python run-driver"))
    assert mod._driver_pid_and_starttime() == (
        None, None, mod.DRIVER_ENUM_IDENTITY_UNREADABLE)
    assert reads == [4242, 4242], reads


def test_the_run_exit_code_has_one_definition_for_main_and_the_abandon_path():
    """`exit_code_for(obs.reason)` is NOT the run's exit code: a PASSED run has an
    empty reason, which `exit_code_for` scores as a failure. The abandon path used
    it directly, so a passing run that had to abandon its teardown exited 1 — the
    cleanup fault moving the exit code, exactly what #4319 forbids. A verification
    pass caught it, and the test above had asserted the same wrong function, so it
    was green ON the bug. These are the three cases, and the structural half pins
    that `main` cannot drift back off the shared definition."""
    import tools.ship_test_onboarding as mod

    passed = mod.Observation(started_at=mod._now(), target={})
    passed.verdict = "passed"
    assert mod.run_exit_code(passed) == mod.EXIT_PASSED

    failed = mod.Observation(started_at=mod._now(), target={})
    failed.verdict = "failed: positive_not_shown"
    failed.reason = "positive_not_shown"
    assert mod.run_exit_code(failed) == mod.EXIT_FAILED

    broken = mod.Observation(started_at=mod._now(), target={})
    broken.verdict = "instrument-error: no driver"
    broken.reason = mod.REASON_INSTRUMENT_ERROR
    assert mod.run_exit_code(broken) == mod.EXIT_INSTRUMENT_ERROR

    assert "return run_exit_code(obs)" in _inspect.getsource(mod.main), (
        "main must return the shared definition, so it cannot drift from the "
        "teardown's abandon path")


def test_the_teardown_bound_is_pinned() -> None:
    """An inflated bound is not a bound: the value is pinned, and the pin is a
    RANGE, so an inflated bound is as RED as a zero one. The first rung is B/2,
    so the value must clear the measured healthy teardown (~2.6 s) with room to
    spare — a bound near it is the false-alarm class this pin exists to catch."""
    import tools.ship_test_onboarding as mod

    assert mod.TEARDOWN_BOUND_S == 30.0
    assert 0 < mod.TEARDOWN_BOUND_S <= 60


def test_the_verdict_is_printed_exactly_once(capsys, monkeypatch, tmp_path):
    """The pre-teardown write is print-free and `_finish` is the single print
    site, so the verdict appears exactly ONCE on stdout — and it is the SAME
    verdict the file carries, because both are produced from the finished record
    after the bounded teardown."""
    import json

    import tools.ship_test_onboarding as mod

    obs, _ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan=_happy_base(),
        ui_sequence=[NOT_CONNECTED, CONNECTED], mcp_tools_call=_MCP_OK)
    captured = capsys.readouterr()
    written = json.loads((tmp_path / "ship-test" / "observation.json").read_text())
    assert obs.verdict == "passed", (obs.verdict, obs.reason)
    assert captured.out.count("[ship-test] passed") == 1, captured.out
    assert written["verdict"] == obs.verdict
    assert written["browser_teardown"]["outcome"] == mod.BROWSER_TEARDOWN_CLEAN
    assert "BROWSER TEARDOWN" not in captured.err


def test_a_non_clean_browser_teardown_warns_on_stderr_and_never_moves_the_verdict(
        capsys, monkeypatch, tmp_path):
    """AC7. A teardown fault is LOUD and never verdict-affecting — the same rule
    as the org residue warning (#4319), applied to the browser. The line is
    written by `_finish`, the single print site, from the same finalized record
    the file carries."""
    import json

    _obs, _ctx, _mod = _happy_bound_run(monkeypatch, tmp_path, ctx_close_raises=True)
    captured = capsys.readouterr()
    written = json.loads((tmp_path / "ship-test" / "observation.json").read_text())
    assert written["verdict"] == "passed"
    assert written["reason"] == ""
    assert written["browser_teardown"]["outcome"] == "close_error"
    assert "BROWSER TEARDOWN" in captured.err
    assert "close_error" in captured.err
    assert "does not change the verdict" in captured.err
    assert captured.out.count("[ship-test] passed") == 1


def test_the_runbook_discloses_the_bound_the_vocabulary_and_the_residue() -> None:
    """AC7. The residual risk and the record are WRITTEN DOWN, not merely
    implemented — a doc test, so the runbook cannot silently lose them. The
    residue is named by issue (#4928), the vocabulary term by term."""
    from pathlib import Path

    import tools.ship_test_onboarding as mod

    doc = (Path(__file__).resolve().parent.parent
           / "docs/runbook/3806-ship-test-instrument.md").read_text()
    # the bound, and its VALUE
    assert "TEARDOWN_BOUND_S" in doc
    assert str(mod.TEARDOWN_BOUND_S) in doc, "the runbook does not state the bound's value"
    # the field, and the closed vocabulary term by term
    assert "browser_teardown" in doc
    for term in mod.BROWSER_TEARDOWN_OUTCOMES:
        assert term in doc, f"the runbook does not name {term!r}"
    # the E10 residue, named by issue
    assert "#4928" in doc
    assert "orphan" in doc.lower()
    # ...and the window that produces `not_run`
    assert "not_run" in doc
    assert "killed" in doc.lower() or "dies" in doc.lower()
    # FIX E: the runbook carries the SAME qualification as the module docstring,
    # so a reader cannot take the twice-write universal away from either place.
    doc_flat = " ".join(doc.split())
    assert "run whose walk settled into the pre-teardown write" in doc_flat, (
        "the runbook must qualify the twice-write to a settled walk")
    # FIX 8f + FIX E: the MODULE docstring states the same closed vocabulary and
    # qualifies the twice-write to a run whose walk SETTLED into the pre-teardown
    # write (not every run that merely enters the teardown).
    module_flat = " ".join((mod.__doc__ or "").split())
    for term in mod.BROWSER_TEARDOWN_OUTCOMES:
        assert term in module_flat, f"the module docstring does not name {term!r}"
    assert "run whose walk settled into the pre-teardown write" in module_flat, (
        "the module docstring must qualify the twice-write to a settled walk")


def test_the_module_docstring_exception_list_is_exhaustive() -> None:
    """FIX C. The "the observation directory always holds ..." promise named ONE
    deliberate exception (#4875), so a walk body that raises before it settles —
    which writes NO document, as
    `test_a_walk_that_raises_before_it_settles_writes_no_document` measures —
    read as a contradiction of the universal. The parenthetical now names every
    abort that writes nothing, so the exception list is exhaustive."""
    import tools.ship_test_onboarding as mod

    flat = " ".join((mod.__doc__ or "").split())
    assert "with deliberate exceptions (#4875)" in flat, flat
    assert "an ``--out`` that cannot be created" in flat, flat
    assert "a driver that will not start" in flat, flat
    assert "a walk body that raises before it settles" in flat, flat
    assert "The exception list is exhaustive." in flat, flat


def test_the_enumerator_docstring_does_not_claim_a_whole_field_guard() -> None:
    """FIX D. The enumerator's marker guard is a SUBSTRING test on the command
    field (`marker in parts[2]`), but the docstring claimed a "whole-field
    match" — a claim stronger than the code. The claim is now the code's own,
    and this pins the wording so a future edit cannot silently restore the
    stronger one without either matching whole tokens (and adding the test that
    proves a mere substring is rejected) or reddening here."""
    import tools.ship_test_onboarding as mod

    doc = " ".join((mod._driver_pid_and_starttime.__doc__ or "").split())
    assert "matched ANYWHERE in the command field" in doc, doc
    assert "a substring test, not a whole-field one" in doc, doc
    assert "by a whole-field match" not in doc, doc
    # ...and the guard really is the substring test the docstring now describes.
    source = _inspect.getsource(mod._driver_pid_and_starttime)
    assert "marker in parts[2]" in source, source


# ── #4875 — the aborts that write NO artifact, and the guard that does ───────
# Each abort raises OUT of `run_walk` before any writer exists, so none may be
# folded into the funnel: `_finalize` now WRITES, so routing an abort through it
# would manufacture an artifact the abort path never had. They were covered by no
# test at all before #4907.

def test_an_out_that_cannot_be_created_writes_no_artifact(monkeypatch, tmp_path):
    """#4875 abort (1/2). `shots.mkdir` raising on an uncreatable `--out` is a
    pre-observation abort: exit 3, and NO `observation.json` anywhere. The
    module's "the directory always holds one" promise does not extend to a run
    that never created the observation."""
    import tools.ship_test_onboarding as mod

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    rc = mod.main(["--base-url", "https://app.premiselabs.co",
                   "--auth-url", "https://tortoise.premiselabs.co",
                   "--api-url", "https://api.premiselabs.co", "--allow-prod",
                   "--out", str(blocker / "ship-test")])
    assert rc == mod.EXIT_INSTRUMENT_ERROR
    assert not list(tmp_path.rglob("observation.json"))


def test_a_driver_that_will_not_start_writes_no_artifact(monkeypatch, tmp_path):
    """#4875 abort (2/2). `sync_playwright().start()` raising is NOT the import
    guard (which records and returns): it propagates before any writer exists, so
    the run exits 3 with no artifact. The screenshots directory is all that
    exists — it was created before the driver."""
    import sys
    import types

    import tools.ship_test_onboarding as mod

    out = tmp_path / "ship-test"
    ctx = _FakeCtx({}, "https://app.premiselabs.co")
    fake_sync = types.ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: _FakeSyncPlaywright(ctx, start_raises=True)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    rc = mod.main(["--base-url", "https://app.premiselabs.co",
                   "--auth-url", "https://tortoise.premiselabs.co",
                   "--api-url", "https://api.premiselabs.co", "--allow-prod",
                   "--out", str(out)])
    assert rc == mod.EXIT_INSTRUMENT_ERROR
    assert not (out / "observation.json").exists()
    assert (out / "screenshots").is_dir(), "mkdir precedes the driver"


def test_the_import_guard_path_writes_not_run_and_enters_no_browser_teardown(
        monkeypatch, tmp_path):
    """The THIRD pre-driver path is not an abort. The playwright import guard
    returns through `_start_driver` with no browser at all: it still writes a
    complete artifact (the module's promise), records the ORG teardown as
    `not_reached`, and its `browser_teardown.outcome` is `not_run` while the
    browser-teardown site was NEVER entered (zero invocations)."""
    import json

    import tools.ship_test_onboarding as mod

    calls = []
    original = mod._teardown_browser

    def _counting(*a, **k):
        calls.append(a)
        return original(*a, **k)

    monkeypatch.setattr(mod, "_teardown_browser", _counting)
    obs, _ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        playwright_available=False)
    assert obs.verdict.startswith("failed: playwright unavailable"), obs.verdict
    assert obs.teardown["status"] == mod.TEARDOWN_NOT_REACHED
    written = json.loads((tmp_path / "ship-test" / "observation.json").read_text())
    assert written["browser_teardown"]["outcome"] == "not_run"
    assert written["browser_teardown"]["closes"] == []
    assert calls == [], "the import-guard path must not enter a browser teardown"


def test_a_walk_that_raises_before_it_settles_writes_no_document(
        monkeypatch, tmp_path):
    """FIX E. The "written atomically twice" claim was a false universal. A walk
    body that raises enters `run_walk`'s `finally` (so the teardown runs) but never
    reaches `_finalize`, so no pre-teardown copy is written — and the propagating
    exception skips `_finish`. Measured: ZERO documents. The claim is qualified to
    a walk whose body SETTLED, and this is the measurement it is qualified to."""
    import tools.ship_test_onboarding as mod

    def _raising_walk(*a, **k):
        raise RuntimeError("the walk body blew up before it settled")

    monkeypatch.setattr(mod, "_walk", _raising_walk)
    with pytest.raises(RuntimeError):
        _run_fake_walk(monkeypatch, tmp_path, plan=_happy_base(),
                       ui_sequence=[], mcp_tools_call=_MCP_OK)
    assert not list(tmp_path.rglob("observation.json")), (
        "a walk that raised before it settled wrote a document, contradicting "
        "the qualified twice-write claim")


# ── #4907 — the BOUND: the teardown cannot block forever with a browser live ─
# Every test here drives the real `run_walk` against a fake that can wedge, raise
# and be signalled, and every one reads `tmp_path/"ship-test"/"observation.json"`
# FROM DISK and asserts the recorded VALUE. The in-memory object is never the
# subject: the record only earns its keep if it is serialized.

def _browser_teardown_from_disk(tmp_path) -> dict:
    import json

    return json.loads(
        (tmp_path / "ship-test" / "observation.json").read_text())["browser_teardown"]


def _happy_bound_run(monkeypatch, tmp_path, **kw):
    """A run that completes the walk (so it PASSED) and then enters the teardown
    with whichever teardown knobs the test needs."""
    return _run_fake_walk(
        monkeypatch, tmp_path, plan=_happy_base(),
        ui_sequence=[NOT_CONNECTED, CONNECTED], mcp_tools_call=_MCP_OK, **kw)


def test_the_ladder_is_pure_and_valued_at_half_and_three_quarters() -> None:
    """The bound's ARITHMETIC as a value. The CI self-check exercises the same
    function against five mutants; this pins the exact rungs so a drift in the
    formula is visible in the fast lane too. `_ladder` takes NO clock and no
    process — it is data."""
    import tools.ship_test_onboarding as mod

    ladder = mod._ladder(mod.TEARDOWN_BOUND_S)
    assert ladder == [(mod.TEARDOWN_BOUND_S / 2, signal.SIGTERM),
                      (3 * mod.TEARDOWN_BOUND_S / 4, signal.SIGKILL)]
    assert mod._ladder_is_sound(ladder, mod.TEARDOWN_BOUND_S) is True
    assert mod._ladder_is_sound(
        [(0.0, signal.SIGTERM), (0.0, signal.SIGKILL)], mod.TEARDOWN_BOUND_S) is False


@pytest.mark.timeout(60)
def test_a_wedged_context_close_returns_within_the_bound_and_records_watchdog_kill(
        monkeypatch, tmp_path):
    """AC1. A context close that never returns against an unresponsive driver is
    the E7 hang (measured: >20s and never returned). The run must return INSIDE
    the bound, and the artifact ON DISK must say the teardown needed the
    watchdog. The wedge records WHY it was released — `sigkill` — so a mutant that
    never signals cannot pass by way of the wedge's own self-release."""
    import time

    import tools.ship_test_onboarding as mod

    # The production bound is 30 s; the wedge tests drive the LADDER on a short
    # bound so the suite stays fast. The value-pin test asserts the production
    # constant itself.
    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    started = time.monotonic()
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        wedge_release_on=(signal.SIGKILL,))
    elapsed = time.monotonic() - started
    harness = ctx.harness
    # A GENEROUS smoke margin, deliberately: `elapsed` covers the whole fake walk
    # AND the teardown, so a tight wall-clock bound is flaky under fleet load (it
    # failed once in a full-suite run beside a loaded box). The PROPERTY is pinned
    # deterministically by the four assertions below — the wedge is released ONLY
    # by a signal, so a watchdog that never fired would hang the run outright, and
    # the release cause, the target pid and the on-disk outcome are exact.
    assert elapsed < 10 * mod.TEARDOWN_BOUND_S, (
        f"the teardown did not return inside the bound: {elapsed:.2f}s")
    assert harness.wedges[0].released_by == "sigkill", (
        f"released by {harness.wedges[0].released_by!r}: the watchdog did not "
        "deliver the kill that releases a blocked close")
    assert (4242, signal.SIGKILL) in harness.signals, harness.signals
    assert {pid for pid, _ in harness.signals} == {4242}
    assert set(harness.reaps) == {4242}
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "watchdog_kill", block
    assert block["outcome"] != mod.BROWSER_TEARDOWN_NOT_RUN


@pytest.mark.timeout(60)
def test_a_wedged_browser_close_on_the_new_context_path_is_bounded(
        monkeypatch, tmp_path):
    """AC1b/AC5. `new_context` failed but the LAUNCH succeeded, so the browser is
    live while no context exists — and `browser.close()` is itself the
    timeout-less `send` that blocks. It must be inside the watchdog window
    exactly like the context close."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        new_context_raises=True, browser_close_wedges=True,
        wedge_release_on=(signal.SIGKILL,))
    assert ctx.harness.wedges[0].released_by == "sigkill"
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "watchdog_kill", block
    assert [c["name"] for c in block["closes"]] == ["browser", "playwright"]


@pytest.mark.timeout(60)
def test_a_wedged_pw_stop_is_bounded_and_forced(monkeypatch, tmp_path):
    """E7b: `pw.stop()` is no escape either. On the no-browser path a wedged stop
    is the ONLY thing blocking, and the bound must cover it."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        launch_raises=True, stop_wedges=True,
        wedge_release_on=(signal.SIGKILL,))
    harness = ctx.harness
    assert harness.wedges[0].released_by == "sigkill"
    assert harness.signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "watchdog_kill", block


def test_a_close_that_raises_is_recorded_with_the_closer_and_the_exception(
        monkeypatch, tmp_path):
    """AC2. The one case where the teardown did not do its job is the one case it
    used to `suppress`. It is recorded — WHICH closer, and the exception — and the
    verdict and reason are untouched: cleanup is not the product. The remaining
    closers still run (the browser close is what reaps the Chromium tree)."""
    obs, _ctx, _mod = _happy_bound_run(monkeypatch, tmp_path, ctx_close_raises=True)
    assert obs.verdict == "passed", (obs.verdict, obs.reason)
    assert obs.reason == ""
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "close_error", block
    assert [c["name"] for c in block["closes"]] == ["context", "browser", "playwright"]
    assert block["closes"][0]["how"] == "close_error"
    assert "RuntimeError: context close failed" in block["closes"][0]["detail"]
    assert block["closes"][1]["how"] == "closed"


def test_a_browser_close_that_raises_is_recorded_and_still_stops_the_driver(
        monkeypatch, tmp_path):
    """AC2's other closer. `browser.close()` raising must not skip `pw.stop()` —
    the driver stop is what the bound rests on — and the recorded closer names the
    browser. (`expect_reaped=False`: a raise means `browser_closed` is never
    recorded, which is the honest state — that is the leak the record exists to
    make visible.)"""
    obs, _ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, browser_close_raises=True, expect_reaped=False)
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "close_error", block
    assert [c["name"] for c in block["closes"]] == ["context", "browser", "playwright"]
    assert block["closes"][1]["how"] == "close_error"
    assert "RuntimeError: browser close failed" in block["closes"][1]["detail"]
    assert block["closes"][2]["how"] == "closed", "a raising browser close must not skip pw.stop()"
    assert obs.verdict == "passed"


def test_a_healthy_teardown_records_clean_and_sends_no_signal(monkeypatch, tmp_path):
    """AC3. The bound must cost a healthy run nothing: the on-disk record says
    `clean`, every closer completed, and NO signal was sent at all. A watchdog
    that fires at once reddens this (and the two no-browser paths below)."""
    obs, ctx, _mod = _happy_bound_run(monkeypatch, tmp_path)
    assert obs.verdict == "passed"
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "clean", block
    assert [c["name"] for c in block["closes"]] == ["context", "browser", "playwright"]
    assert {c["how"] for c in block["closes"]} == {"closed"}
    assert ctx.harness.signals == [], ctx.harness.signals
    assert ctx.harness.reaps == [], ctx.harness.reaps


@pytest.mark.timeout(60)
def test_the_watchdog_signals_only_the_enumerated_driver_child(monkeypatch, tmp_path):
    """AC4. The watchdog holds ONE pid, obtained by enumerating THIS run's own
    direct children. Every signal it sends goes to that pid and no other — the
    safety invariant that lets the kill be automatic."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        wedge_release_on=(signal.SIGKILL,))
    harness = ctx.harness
    assert harness.signals, "the wedge needed the watchdog"
    assert {pid for pid, _ in harness.signals} == {4242}, harness.signals
    assert all(s in (signal.SIGTERM, signal.SIGKILL) for _, s in harness.signals)


@pytest.mark.timeout(60)
def test_no_driver_child_means_no_signal_and_driver_absent(monkeypatch, tmp_path):
    """AC4. With no driver child enumerated the watchdog signals NOTHING — a kill
    with no target must not guess — and when the close then releases on its own
    the artifact says `driver_absent`.

    The wedge is timed to land BETWEEN the rungs (after B/2, before 3B/4), so the
    watchdog has fired but the close finishes without help: that is the only
    sequence in which `driver_absent` is the honest record. A close that never
    releases takes the `abandoned` path instead — see the next test."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True, driver_absent=True,
        wedge_timeout=mod.TEARDOWN_BOUND_S * 0.6)
    harness = ctx.harness
    assert harness.signals == [], harness.signals
    assert harness.reaps == []
    assert harness.exits == [], "the run self-terminated unnecessarily"
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "driver_absent", block
    # FIX 8c: the no-child case says so, and does not claim a re-check refusal.
    assert "no driver child was enumerated" in block["detail"], block["detail"]
    assert "identity could not be read" not in block["detail"], block["detail"]


@pytest.mark.timeout(60)
def test_an_enumerated_child_with_an_unreadable_identity_is_not_reported_as_absent(
        monkeypatch, tmp_path):
    """FIX B. `driver_absent` covers two DIFFERENT nothings, and the record must
    name which: a run where a marker-matching child WAS enumerated but its
    identity could not be read must not claim "no driver child was enumerated" —
    that reports a real candidate as absent. The outcome vocabulary is unchanged
    (both are `driver_absent`); only `detail` differs, so the OTHER message is
    pinned by `test_no_driver_child_means_no_signal_and_driver_absent`."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        enum_identity_unreadable=True,
        wedge_timeout=mod.TEARDOWN_BOUND_S * 0.6)
    assert ctx.harness.signals == [], ctx.harness.signals
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "driver_absent", block
    assert "identity could not be read" in block["detail"], block["detail"]
    assert "no driver child was enumerated" not in block["detail"], block["detail"]


@pytest.mark.timeout(120)
def test_a_close_that_never_releases_abandons_the_run_inside_the_bound(
        monkeypatch, tmp_path):
    """AC1 (the hard half). The ladder is released by a SIGNAL, so with no child
    enumerated there is nothing to release a close that never returns; the bound
    then has to come from the process itself. The gate reproduced the defect this
    test pins: with the no-child seams and a never-returning close, the run stayed
    blocked past the bound (30s against B=5).

    The record at that moment is `abandoned`, written from the watchdog thread;
    the exit code is the RUN'S OWN (a cleanup fault may not move the verdict,
    #4319), and nothing was signalled or reaped."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    start = time.monotonic()
    obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True, driver_absent=True,
        wedge_timeout=60.0)
    elapsed = time.monotonic() - start
    harness = ctx.harness
    assert harness.signals == [] and harness.reaps == []
    assert harness.exits == [mod.EXIT_PASSED], (
        f"a PASSED run whose teardown abandoned must still exit {mod.EXIT_PASSED} "
        f"(#4319: a cleanup fault may not move the exit code); got {harness.exits} "
        f"for verdict {obs.verdict!r} / reason {obs.reason!r}")
    assert harness.exit_docs[0] is not None, "nothing was on disk at abandon time"
    assert harness.exit_docs[0]["outcome"] == "abandoned", harness.exit_docs[0]
    # the ladder was SPENT before it gave up, and the run still came in bounded
    # The lower bound is the point (the ladder was SPENT before giving up); the
    # upper one is a generous smoke margin, because `elapsed` spans the whole fake
    # walk and a loaded box can stretch it — the deterministic evidence is the
    # recorded exit, the on-disk `abandoned` document and the empty signal list.
    assert elapsed >= 3 * mod.TEARDOWN_BOUND_S / 4 - 0.3, elapsed
    assert elapsed < 10 * mod.TEARDOWN_BOUND_S, elapsed


@pytest.mark.timeout(60)
def test_a_stale_start_time_is_not_signalled(monkeypatch, tmp_path):
    """AC4/TOCTOU. A bare pid is racy against reuse, so the whole identity is
    RE-READ as one value immediately before every rung. When its start time
    disagrees with the enumeration the watchdog signals nothing — a reused pid is
    not this run's child.

    On the real path the close is still blocked at the last rung, so the refusal
    is observed at the LAST RESORT: the run abandons (`abandoned`, with the
    `_exit_now` evidence captured at that moment). The wedge is released only by
    the fake exit seam, so nothing here depends on a self-release by timeout, and
    deleting `_abandon` removes the exit call — which reddens this test.
    """
    import os

    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        identity_override=(os.getpid(), "a different start time"),
        wedge_timeout=mod.TEARDOWN_BOUND_S)
    harness = ctx.harness
    assert harness.signals == [], harness.signals
    # re-read once per rung, and the pid it inspected is the ENUMERATED one
    assert harness.identity_reads == [4242, 4242], harness.identity_reads
    # the last resort ran, with the run's own exit code
    assert harness.exits == [mod.EXIT_PASSED], harness.exits
    assert harness.exit_docs[0] is not None, "nothing was on disk at abandon time"
    assert harness.exit_docs[0]["outcome"] == "abandoned", harness.exit_docs[0]
    # ...and the record distinguishes a REFUSED child from "no child enumerated"
    # (FIX 8c): the on-disk record after the main thread completes.
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "driver_absent", block
    assert "refused by the identity re-check" in block["detail"], block["detail"]


@pytest.mark.timeout(60)
def test_a_reused_pid_with_a_different_ppid_is_not_signalled(monkeypatch, tmp_path):
    """FIX A's pid-reuse half. The identity is parent pid AND start time, read in
    ONE `ps`; the re-check must require BOTH to match. A stub whose re-read
    reports our own child's start time but a DIFFERENT parent is a pid that was
    reused by some other process (or whose parentage changed): nothing may be
    signalled.

    Same shape as the stale-start-time test: the ladder is spent and the run is
    bounded through `_abandon`. The assertion the change exists for is that the
    recorded outcome is NOT `watchdog_kill` — a single-field (start-time-only)
    re-check would signal the reused pid and record `watchdog_kill`."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        identity_override=(os.getpid() + 1, "fake-lstart"),
        wedge_timeout=mod.TEARDOWN_BOUND_S)
    harness = ctx.harness
    assert harness.signals == [], harness.signals
    assert harness.identity_reads == [4242, 4242], harness.identity_reads
    assert harness.exits == [mod.EXIT_PASSED], harness.exits
    assert harness.exit_docs[0] is not None, "nothing was on disk at abandon time"
    assert harness.exit_docs[0]["outcome"] == "abandoned", harness.exit_docs[0]
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] != "watchdog_kill", block
    assert block["outcome"] == "driver_absent", block
    assert "refused by the identity re-check" in block["detail"], block["detail"]


def test_a_healthy_run_records_clean_with_no_signal_inside_the_first_rung(
        monkeypatch, tmp_path):
    """FIX 3's false-alarm half. A healthy real teardown takes ~2.6 s and the
    first rung is at B/2, so at the production bound the graceful window is ~6x
    the healthy path: a healthy run records `clean` and sends no signal at all.
    The margin is asserted AS A VALUE, so a bound lowered back onto the healthy
    path (the live false alarm: every healthy run recorded `watchdog_kill` and
    took a SIGTERM) is RED here."""
    import tools.ship_test_onboarding as mod

    assert mod.TEARDOWN_BOUND_S / 2 >= 5 * 2.6, (
        f"the first rung ({mod.TEARDOWN_BOUND_S / 2}s) must clear the measured "
        f"healthy teardown (~2.6s) with margin")
    obs, ctx, _mod = _happy_bound_run(monkeypatch, tmp_path)
    assert obs.verdict == "passed"
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "clean", block
    assert ctx.harness.signals == [], ctx.harness.signals
    assert ctx.harness.exits == [], ctx.harness.exits


@pytest.mark.timeout(60)
def test_the_abandon_path_still_prints_the_authoritative_summary(
        capsys, monkeypatch, tmp_path):
    """FIX 4. `_abandon` exits before `_finish`, so it shares the print path: the
    verdict line, the per-step summary and the `observation → path` line a CI job
    keys on are all printed from the record the abandon wrote.

    Called DIRECTLY: on the real path the fake `_exit_now` returns and `_finish`
    prints a second copy, which would mask a silent `_abandon`."""
    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={"dashboard": "x"})
    obs.verdict = "passed"
    obs.add(name="front-door", ok=True, detail="hittable")
    record = mod._browser_teardown_record()
    exits = []
    monkeypatch.setattr(mod, "_exit_now", lambda code: exits.append(code))
    mod._abandon(obs, record, None, tmp_path)
    captured = capsys.readouterr()
    assert "[ship-test] passed" in captured.out, captured.out
    assert "  PASS front-door" in captured.out, captured.out
    assert "[ship-test] observation →" in captured.out, captured.out
    assert exits == [mod.EXIT_PASSED], exits
    assert record["outcome"] == "abandoned", record


@pytest.mark.timeout(60)
def test_the_abandon_artifact_claim_is_conditional_on_the_write(
        capsys, monkeypatch, tmp_path):
    """FIX 4. The abandon path may only claim the artifact records the outcome when
    the write actually succeeded; when it failed, the stderr line says so, so a
    lost write is never reported as a recorded one."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    real = mod._write_observation
    calls = []

    def _failing_on_abandon(obs, out_dir):
        calls.append(1)
        if len(calls) == 2:          # 1 = pre-teardown write, 2 = the abandon's
            raise OSError("disk gone")
        return real(obs, out_dir)

    monkeypatch.setattr(mod, "_write_observation", _failing_on_abandon)
    _obs, _ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True, driver_absent=True,
        wedge_timeout=60.0)
    captured = capsys.readouterr()
    assert "could NOT be written" in captured.err, captured.err


def test_the_summary_names_the_artifact_only_when_it_was_written(
        capsys, tmp_path) -> None:
    """FIX D. `_abandon` prints the shared summary even when its write failed, so
    an unconditional `observation → <path>` line names an artifact that does not
    exist while stderr says otherwise — the contradiction the summary exists to
    prevent. Both branches are pinned: written names the path, not-written says so
    on stdout AND stderr."""
    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    obs.verdict = "passed"

    mod._print_summary(obs, tmp_path / "observation.json")
    captured = capsys.readouterr()
    assert "[ship-test] observation →" in captured.out, captured.out
    assert "NOT WRITTEN" not in captured.out, captured.out
    assert captured.err == "", captured.err

    mod._print_summary(obs, tmp_path / "observation.json",
                       artifact_written=False)
    captured = capsys.readouterr()
    assert "[ship-test] observation →" not in captured.out, captured.out
    assert "observation NOT WRITTEN" in captured.out, captured.out
    assert "could NOT be written" in captured.err, captured.err


def test_an_abandoned_run_still_warns_about_residue_on_stderr(
        capsys, monkeypatch, tmp_path) -> None:
    """FIX C. `_abandon` never reaches `_finish`, so a run that left a live org
    AND abandoned its teardown used to exit with NO residue warning — contradicting
    the runbook's "every status except deleted/skipped_no_org/not_reached is warned
    on stderr". The side-effect warnings now live in the SHARED print path, so the
    abandon exit carries both the residue alert and the browser-teardown alert."""
    import tools.ship_test_onboarding as mod

    obs = mod.Observation(started_at=mod._now(), target={})
    obs.verdict = "passed"
    obs.teardown = {"status": mod.TEARDOWN_BASELINE_UNAVAILABLE}
    # the record IS `obs.browser_teardown`, as at the real call site, so the
    # shared side-effect warning sees the `abandoned` outcome `_abandon` records
    record = obs.browser_teardown
    exits = []
    monkeypatch.setattr(mod, "_exit_now", lambda code: exits.append(code))
    mod._abandon(obs, record, None, tmp_path)
    captured = capsys.readouterr()
    assert "[ship-test] RESIDUE —" in captured.err, captured.err
    assert mod.TEARDOWN_BASELINE_UNAVAILABLE in captured.err, captured.err
    assert "[ship-test] BROWSER TEARDOWN — abandoned" in captured.err, captured.err
    # ...and the warnings are warnings only: the exit code is the run's own.
    assert exits == [mod.EXIT_PASSED], exits


@pytest.mark.timeout(60)
def test_a_finalization_that_raises_still_reaches_the_exit(
        monkeypatch, tmp_path) -> None:
    """FIX B. `_abandon` runs on the WATCHDOG thread while the main thread is
    blocked in a timeout-less `close()`, so `os._exit` is the only thing that
    bounds the run. A `BrokenPipeError` from a CI-closed log pipe (or a `TypeError`
    in a step detail) escaping the prints would kill the watchdog and leave the main
    thread blocked forever — defeating the guarantee this change exists to give.
    The finalization is suppressed and the exit sits in a `finally`."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    real = mod._print_summary
    raised = []

    def _first_summary_raises(*a, **k):
        if not raised:               # the abandon's summary, on the watchdog
            raised.append(1)
            raise BrokenPipeError("the CI log pipe was closed")
        return real(*a, **k)         # `_finish`'s summary, once the wedge releases

    monkeypatch.setattr(mod, "_print_summary", _first_summary_raises)
    sink = []
    _happy_bound_run(monkeypatch, tmp_path, ctx_close_wedges=True,
                     driver_absent=True,
                     wedge_timeout=mod.TEARDOWN_BOUND_S * 1.5,
                     harness_sink=sink)
    harness = sink[0]
    assert raised, "the raising summary path was never exercised"
    assert harness.exits, (
        "the finalization raised and the last-resort exit was skipped, so the "
        "main thread would block forever")
    assert harness.exits == [mod.EXIT_PASSED], harness.exits


@pytest.mark.timeout(60)
def test_a_signal_seam_that_raises_still_reaches_abandon(monkeypatch, tmp_path):
    """FIX 6. `os.kill` can raise (a vanished child, a permission refusal). The
    watchdog thread must not die on it: the ladder continues, no exception escapes
    the thread, and the last resort still bounds the run."""
    import threading

    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    escaped = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True, signal_raises=True,
        wedge_timeout=mod.TEARDOWN_BOUND_S * 1.5)
    harness = ctx.harness
    assert harness.signals == [], harness.signals
    assert harness.exits == [mod.EXIT_PASSED], harness.exits
    assert harness.exit_docs[0] is not None
    assert harness.exit_docs[0]["outcome"] == "abandoned", harness.exit_docs[0]
    assert escaped == [], escaped


@pytest.mark.timeout(60)
def test_a_raising_close_is_reported_as_close_error_even_after_a_rung_fired(
        monkeypatch, tmp_path):
    """FIX 8b. An attempted close that RAISED is `close_error` — the specific,
    actionable record — and must not be masked by `driver_absent` because a rung
    fired on the way. With no child enumerated no signal was sent, so the only
    fault is the raising close."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True, ctx_close_raises=True,
        driver_absent=True, wedge_timeout=mod.TEARDOWN_BOUND_S * 0.6)
    harness = ctx.harness
    assert harness.signals == [], harness.signals
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "close_error", block
    assert "context close failed" in block["detail"], block["detail"]


@pytest.mark.timeout(60)
def test_an_unfinalized_record_abandons_as_an_instrument_error(
        monkeypatch, tmp_path):
    """FIX 8a. An unfinalized record — `_walk` escaped before `_finalize` could
    write the pre-teardown document — is an instrument fault, and `main` returns
    `EXIT_INSTRUMENT_ERROR` for it. The teardown's last resort must exit the same
    way rather than scoring the record's non-`passed` verdict as a product code.

    Driven with a write that always fails: `_finalize` raises, `obs.reason` is
    never set, and the run's verdict is the walk's error text — the shape that
    would exit 1 the product way without the mapping."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)

    def _write_always_fails(obs, out_dir):
        raise OSError("disk gone")

    monkeypatch.setattr(mod, "_write_observation", _write_always_fails)
    sink = []
    with pytest.raises(OSError):
        _happy_bound_run(monkeypatch, tmp_path, ctx_close_wedges=True,
                         driver_absent=True, wedge_timeout=60.0,
                         harness_sink=sink)
    harness = sink[0]
    assert harness.exits == [mod.EXIT_INSTRUMENT_ERROR], harness.exits


@pytest.mark.timeout(60)
def test_the_ladder_takes_the_rungs_at_half_and_three_quarters_of_the_bound(
        monkeypatch, tmp_path):
    """AC5. The rungs are taken ON THE CLOCK SEAM, at B/2 and 3B/4 — not merely
    twice. A collapse of both rungs onto one delay reddens the second bound; a
    ladder that never fires reddens the first (no signal at all)."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        wedge_release_on=(signal.SIGKILL,))
    harness = ctx.harness
    assert len(harness.signal_times) == 2, harness.signal_times
    t0, t1 = harness.signal_times
    start = harness.teardown_start
    assert t0 - start >= mod.TEARDOWN_BOUND_S / 2 * 0.9, (t0 - start, start)
    assert t1 - start >= 3 * mod.TEARDOWN_BOUND_S / 4 * 0.9, (t1 - start, start)
    assert t0 < t1, "SIGTERM must be taken strictly before SIGKILL"


@pytest.mark.timeout(60)
def test_the_ladder_sends_at_most_one_sigterm_and_one_sigkill(monkeypatch, tmp_path):
    """AC5. At most one of each — the ladder may legitimately send BOTH, so a test
    asserting "one signal" would be wrong; a ladder that fires twice per rung
    would send four. The signalled child is reaped once per signal."""
    import tools.ship_test_onboarding as mod

    monkeypatch.setattr(mod, "TEARDOWN_BOUND_S", 1.0)
    _obs, ctx, _mod = _happy_bound_run(
        monkeypatch, tmp_path, ctx_close_wedges=True,
        wedge_release_on=(signal.SIGKILL,))
    assert ctx.harness.signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)], \
        ctx.harness.signals
    assert ctx.harness.reaps == [4242, 4242], ctx.harness.reaps


def test_the_browser_teardown_is_entered_exactly_once(monkeypatch, tmp_path):
    """AC5. Entered EXACTLY ONCE on a path that reaches it — counted
    behaviourally, since a source count cannot see a call reached twice."""
    import tools.ship_test_onboarding as mod

    calls = []
    original = mod._teardown_browser

    def _counting(*a, **k):
        calls.append(a)
        return original(*a, **k)

    monkeypatch.setattr(mod, "_teardown_browser", _counting)
    _happy_bound_run(monkeypatch, tmp_path)
    assert len(calls) == 1, calls


def test_a_browser_that_never_launched_is_bounded_by_pw_stop(monkeypatch, tmp_path):
    """AC5, no-browser path 1. `launch_raises`: there is NO browser and NO
    context, so the only action is the bounded `pw.stop()`. It completes, records
    `clean`, and sends no signal — the bound does not fire on a healthy path."""
    _obs, ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        launch_raises=True)
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "clean", block
    assert [c["name"] for c in block["closes"]] == ["playwright"]
    assert ctx.harness.signals == []


def test_a_browser_whose_context_failed_is_bounded_and_closes_the_browser(
        monkeypatch, tmp_path):
    """AC5, no-browser path 2 (`new_context_raises`): the launch SUCCEEDED, so
    `browser` is live while `new_context` failed. A healthy close, so `clean` and
    no signal — but the browser is what gets closed."""
    _obs, ctx, _mod = _run_fake_walk(
        monkeypatch, tmp_path, plan={}, ui_sequence=[], mcp_tools_call=_MCP_OK,
        new_context_raises=True)
    block = _browser_teardown_from_disk(tmp_path)
    assert block["outcome"] == "clean", block
    assert [c["name"] for c in block["closes"]] == ["browser", "playwright"]
    assert ctx.harness.signals == []


def test_a_kill_inside_the_teardown_window_leaves_a_complete_not_run_document(
        monkeypatch, tmp_path):
    """AC8. A run killed INSIDE the ≤B window — simulated by a `BaseException`
    out of the teardown, deliberately DISTINCT from AC2's raising close — leaves
    on disk a complete, parsable document whose `browser_teardown.outcome` is
    `not_run`. That is the docstring's promise holding exactly where it matters
    most, and it is why the pre-teardown write exists."""
    import json

    import tools.ship_test_onboarding as mod

    def _killed(pw, td, obs, out_dir):
        raise KeyboardInterrupt("run killed inside the teardown window")

    monkeypatch.setattr(mod, "_teardown_browser", _killed)
    with pytest.raises(KeyboardInterrupt):
        _happy_bound_run(monkeypatch, tmp_path)
    written = json.loads((tmp_path / "ship-test" / "observation.json").read_text())
    assert written["browser_teardown"]["outcome"] == "not_run"
    assert written["browser_teardown"]["closes"] == []
    # ...and the document is COMPLETE, not a fragment: the org replay and the
    # verdict are already there.
    assert written["verdict"] == "passed"
    assert written["teardown"]["status"] == mod.TEARDOWN_BASELINE_UNAVAILABLE


# ── #4907 — the acceptance criteria, each with the test that proves it ───────
# A NAMED map, so a criterion cannot silently lose its covering test (the failure
# mode a prose-only claim has). It is the honest form of "every AC is proven":
# the test asserts the NAMES exist, and the names are the tests above.
ACCEPTANCE_CRITERIA = {
    "AC1 wedge, persisted": [
        "test_a_wedged_context_close_returns_within_the_bound_and_records_watchdog_kill",
        "test_a_wedged_browser_close_on_the_new_context_path_is_bounded",
        "test_a_wedged_pw_stop_is_bounded_and_forced",
    ],
    "AC2 raising close, persisted": [
        "test_a_close_that_raises_is_recorded_with_the_closer_and_the_exception",
        "test_a_browser_close_that_raises_is_recorded_and_still_stops_the_driver",
    ],
    "AC3 healthy run, persisted": [
        "test_a_healthy_teardown_records_clean_and_sends_no_signal",
        "test_a_completed_run_records_the_browser_teardown_block",
    ],
    "AC4 own child only": [
        "test_the_watchdog_signals_only_the_enumerated_driver_child",
        "test_no_driver_child_means_no_signal_and_driver_absent",
        "test_a_stale_start_time_is_not_signalled",
        "test_a_reused_pid_with_a_different_ppid_is_not_signalled",
    ],
    "AC5 entered once; the ladder once each": [
        "test_the_browser_teardown_is_entered_exactly_once",
        "test_the_ladder_sends_at_most_one_sigterm_and_one_sigkill",
        "test_the_ladder_takes_the_rungs_at_half_and_three_quarters_of_the_bound",
        "test_a_browser_that_never_launched_is_bounded_by_pw_stop",
        "test_a_browser_whose_context_failed_is_bounded_and_closes_the_browser",
    ],
    "AC6 no regression; harness + mutation-verified": [
        "test_cli_mutation_selfcheck_exits_zero",
        "test_the_teardown_seams_are_declared",
        "test_the_fake_driver_records_start_and_stop",
    ],
    "AC7 E10 disclosed, not closed": [
        "test_the_runbook_discloses_the_bound_the_vocabulary_and_the_residue",
    ],
    "AC8 writer pinned; every former write site accounted for": [
        "test_a_kill_inside_the_teardown_window_leaves_a_complete_not_run_document",
        "test_the_import_guard_path_writes_not_run_and_enters_no_browser_teardown",
        "test_the_verdict_is_printed_exactly_once",
        "test_a_non_clean_browser_teardown_warns_on_stderr_and_never_moves_the_verdict",
        "test_an_out_that_cannot_be_created_writes_no_artifact",
        "test_a_driver_that_will_not_start_writes_no_artifact",
    ],
    "AC9 structural guard re-specified": [
        "test_no_exit_from_the_walk_writes_the_artifact_without_teardown",
    ],
}


def test_every_acceptance_criterion_names_a_proving_test() -> None:
    """All NINE criteria, each with at least one test that exists. This is what
    "every AC is proven" can mean in code: the map cannot rot into names that were
    renamed away, and no criterion can be dropped without a red test."""
    assert len(ACCEPTANCE_CRITERIA) == 9, sorted(ACCEPTANCE_CRITERIA)
    for ac, tests in ACCEPTANCE_CRITERIA.items():
        assert tests, f"{ac} names no test"
        for name in tests:
            assert callable(globals().get(name)), f"{ac}: {name} does not exist"


def test_every_finalize_exit_in_the_walk_is_named_by_a_test() -> None:
    """The completeness half: an exit in `_walk` that no test reaches is a path
    whose funnel call is unproven — how five exits hid until #4843. The number is
    pinned (11; the import guard's moved to `_start_driver`), and the structural
    guard asserts every one of them passes the same `td`."""
    import ast
    import inspect

    import tools.ship_test_onboarding as mod

    tree = ast.parse(inspect.getsource(mod._walk))
    exits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_finalize"]
    assert len(exits) == 11, f"_walk has {len(exits)} _finalize exits, expected 11"
    # ...and the record is proven in BOTH directions ON DISK: one named test pins
    # a completed run's outcome as NOT `not_run`, another pins the killed-window
    # document AS `not_run`. One without the other cannot discriminate "the
    # teardown ran" from "the teardown never finished".
    assert ("test_a_completed_run_records_the_browser_teardown_block"
            in ACCEPTANCE_CRITERIA["AC3 healthy run, persisted"])
    assert ("test_a_kill_inside_the_teardown_window_leaves_a_complete_not_run_document"
            in ACCEPTANCE_CRITERIA["AC8 writer pinned; every former write site accounted for"])
