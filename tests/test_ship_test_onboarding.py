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

import pytest

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
    """The two surfaces use DIFFERENT derivations, so the walk must know which
    one it judged."""
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


# ── the per-surface decision seam (cycle-2: the call sites needed coverage) ──

def test_connection_verdict_promotes_a_smuggled_positive_claim() -> None:
    """T5: a negative card must not hide a positive claim elsewhere on the
    page. The promotion lived at an untested call site; it lives here now."""
    v = connection_verdict(NOT_CONNECTED, "card", UNOBSERVED,
                           "No connection observed yet — but your agent is connected.")
    assert v.ui == CONNECTED
    assert v.ok is False
    assert v.rule == "claim-without-observation"


def test_connection_verdict_sweeps_the_untruncated_body() -> None:
    """The sweep must read the UNTRUNCATED text: a claim rendered past the
    artifact cap is still a claim (cycle-1 finding)."""
    page = "x" * 5000 + " Your agent is connected to this Organization."
    assert claims_connection(recorded_body(_StubPage({"body": [page]}))) is False
    assert claims_connection(page) is True
    v = connection_verdict(NOT_CONNECTED, "card", UNOBSERVED, page)
    assert v.ui == CONNECTED and v.ok is False


def test_page_body_is_untruncated_but_the_recorded_copy_is_bounded() -> None:
    """The two inputs are deliberately different functions."""
    page = _StubPage({"body": ["y" * 9000]})
    assert len(page_body(page)) == 9000
    assert len(recorded_body(page)) == 4000


def test_connection_verdict_judges_each_surface_with_its_own_vocabulary() -> None:
    """F3: the wizard is edge-only, the Overview accepts the wire-complete
    forms. The same server truth must resolve differently per surface."""
    grandfathered = {"status": "complete", "completed_steps": []}
    wizard = connection_verdict(NOT_CONNECTED, "wizard", grandfathered,
                                "No connection observed yet")
    card = connection_verdict(CONNECTED, "card", grandfathered, "Connected ✓")
    assert wizard.ok is True and wizard.rule == "honest-negative"
    assert card.ok is True and card.rule == "observed-and-shown"
    # a wizard claiming a connection its own edge-only derivation cannot see:
    assert connection_verdict(CONNECTED, "wizard", grandfathered, "Connected ✓").ok is False


def test_connection_verdict_never_promotes_an_absent_surface_into_a_claim() -> None:
    v = connection_verdict(ABSENT, "card", UNOBSERVED, "your agent is connected")
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
    """PARITY DECISION, pinned explicitly. The shipped client derives connected
    from the same three forms (`overview.js::overviewConnection`), and
    `onboarding/state.py::resolve_wire_completion` accepts the grandfathered
    wire-complete forms (node status complete, or jsonb onboarding_complete with
    zero agent edges) for the legacy cohort. The guard asks "does the screen
    claim more than the server's own projection?", so it accepts exactly what
    the server accepts — a UI rendering what the server reports is honest even
    for a grandfathered org. This test pins that the grandfathered form is
    accepted WITH NO STEP EDGE, so the choice is visible rather than implied."""
    assert server_observed({"status": "complete"}) is True
    assert server_observed({"onboarding_complete": True}) is True
    assert server_observed({"status": "complete", "completed_steps": []}) is True
    # ... and the guard therefore passes a screen that mirrors it.
    assert judge(CONNECTED, {"status": "complete", "completed_steps": []}).ok is True


def test_server_observation_can_be_restricted_to_the_wizards_edge_only_form() -> None:
    """The two shipped derivations DIFFER: the wizard requires the
    `harness-connected` edge (`main.jsx::serverHarnessConnected`). Judging the
    wizard screen with the Overview vocabulary reports a FALSE failure for a
    grandfathered org that honestly renders the wizard's negative."""
    grandfather = {"status": "complete", "completed_steps": []}
    assert server_observed(grandfather, accept_wire_complete=False) is False
    # the wizard honestly says "No connection observed yet" for that org:
    assert judge(NOT_CONNECTED, grandfather, accept_wire_complete=False).ok is True
    # the edge-only form still counts:
    assert server_observed({"completed_steps": ["harness-connected"]},
                            accept_wire_complete=False) is True
    # and the Overview vocabulary still requires a shown connection:
    assert judge(NOT_CONNECTED, grandfather, accept_wire_complete=True).ok is False


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

    src = inspect.getsource(mod.run_walk)
    session_return = src.index("if session_state != SESSION_SIGNED_IN:")
    write_call = src.index("observe_agent_write(")
    assert session_return < write_call, (
        "the session gate must precede the agent write in run_walk")


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
    """run_walk must return on a failed write — not fall through to the verdict
    assembly that would blame the server."""
    import inspect

    import tools.ship_test_onboarding as mod

    src = inspect.getsource(mod.run_walk)
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
    `judge()` has a `claim-without-server-read` rule, but run_walk must classify
    a failed read as an instrument fault FIRST: a transient 503 on the
    instrument's request would otherwise brand a truthful, connection-showing
    client as a liar."""
    import inspect

    import tools.ship_test_onboarding as mod

    src = inspect.getsource(mod.run_walk)
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

    src = inspect.getsource(mod.run_walk)
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

    def onboarding_state_calls(self):
        return [c for c in self.calls if c[1].endswith("/onboarding/state")]


class _FakeBrowser:
    def __init__(self, ctx):
        self._ctx = ctx

    def new_context(self, **k):
        return self._ctx

    def close(self):
        pass


class _FakePage:
    def __init__(self, base_url):
        self._base = base_url.rstrip("/")
        self.url = self._base + "/welcome"

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
        class _L:
            def count(self):
                return 0

            @property
            def first(self):
                return self

            def all_inner_texts(self):
                return []

            def is_visible(self):
                return False

            def inner_text(self):
                return ""

            def fill(self, *a, **k):
                pass

            def click(self, *a, **k):
                pass

        return _L()


class _FakeCtx:
    def __init__(self, plan, base_url):
        self.request = _FakeRequester(plan)
        self.cookies = []          # the instrument must never read the jar
        self._base = base_url

    def new_page(self):
        return _FakePage(self._base)

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, ctx):
        self._ctx = ctx

    def launch(self, **k):
        return _FakeBrowser(self._ctx)

    def new_context(self, **k):
        return self._ctx


class _FakeSyncPlaywright:
    def __init__(self, ctx):
        self.chromium = _FakeChromium(ctx)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run_fake_walk(monkeypatch, tmp_path, *, plan, ui_sequence,
                   mcp_tools_call, surface="card", skip_write=False):
    """Execute the real `run_walk` against a fake browser. Returns the record."""
    import sys
    import types

    import tools.ship_test_onboarding as mod

    base = "https://app.premiselabs.co"
    ctx = _FakeCtx(plan, base)
    fake_sync = types.ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: _FakeSyncPlaywright(ctx)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    # (leaf stubs: things that would otherwise hit the network or need a page)
    monkeypatch.setattr(mod, "_git_sha", lambda: "deadbeef")
    monkeypatch.setattr(mod, "deployed_sha", lambda api_url: "9605f5f249")
    monkeypatch.setattr(mod, "deployed_bundle", lambda base_url: "assets/index-x.js")
    monkeypatch.setattr(mod, "front_door_probe", lambda page, **k: {"hittable": True})
    monkeypatch.setattr(mod, "connection_surface_kind", lambda page, **k: surface)
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

    args = mod.build_parser().parse_args([
        "--base-url", base, "--auth-url", "https://tortoise.premiselabs.co",
        "--api-url", "https://api.premiselabs.co", "--allow-prod",
        "--timeout", "800", "--settle-ms", "0",
        "--out", str(tmp_path / "ship-test"), "--skip-agent-write" if skip_write else "--email",
    ] + ([] if skip_write else ["ship@premiselabs.co"]))
    obs = mod.run_walk(args)
    return obs, ctx, mod


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
    # the explicit key was used for the WRITE...
    assert written_with["key"] == "tt_explicit"
    # ...and no key was minted, and the truth came only from the session
    assert not any(c[1].endswith("/team/keys") for c in ctx.request.calls)
    for _method, url, _data in ctx.request.onboarding_state_calls():
        assert url.startswith(base + "/api/v1/onboarding/state"), url
    assert "agent-key" in obs.session["mechanism"]
