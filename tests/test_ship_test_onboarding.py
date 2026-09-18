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
    assert verdict_for(neg, True, judge(NOT_CONNECTED, OBSERVED)) != "passed"
    assert verdict_for(neg, True, judge(NOT_CONNECTED, None)) != "passed"
    assert verdict_for(neg, True, judge(CONNECTED, None)) != "passed"
    assert verdict_for(neg, False, judge(CONNECTED, OBSERVED)) != "passed"
    # `absent-expected` is ok AND observed: an ABSENT surface showed NOTHING, so
    # it must not be read as a shown connection either.
    absent = judge(ABSENT, OBSERVED, expected_surface=False)
    assert absent.ok is True and absent.observed is True
    assert verdict_for(neg, True, absent) != "passed"
    # the honest walk (observed, then shown) is the ONLY `passed`
    assert verdict_for(neg, True, judge(CONNECTED, OBSERVED)) == "passed"


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
    import types

    import tools.ship_test_onboarding as mod

    class _Codes:
        def all_inner_texts(self):
            return ["tk_0123456789abcdef"]

    class _P:
        def locator(self, sel):
            return _Codes()

    def _no_mint(*a, **k):
        raise AssertionError("must not mint when the wizard already showed a key")

    monkeypatch.setattr(mod, "_http", _no_mint)
    key, detail = _mint_or_read_key(_P(), types.SimpleNamespace(api_url="https://api"), "tok")
    assert key == "tk_0123456789abcdef"
    assert "shown-once" in detail


def test_verdict_carries_the_failing_reason_class() -> None:
    neg = judge(NOT_CONNECTED, UNOBSERVED)
    assert verdict_for(neg, True, judge(NOT_CONNECTED, OBSERVED)).startswith("failed:")
    assert verdict_for(neg, True, judge(NOT_CONNECTED, None)).startswith("incomplete:")
    assert verdict_for(neg, False, judge(NOT_CONNECTED, OBSERVED)).startswith("incomplete:")
    assert verdict_for(judge(CONNECTED, UNOBSERVED), True,
                       judge(CONNECTED, OBSERVED)).startswith("failed:")


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
    monkeypatch.setattr(mod, "_http", lambda *a, **k: (401, {"detail": "no membership"}))
    assert read_projection("https://api", "tok", []) is None


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
    monkeypatch.setattr(mod, "_http", lambda *a, **k: (200, {"onboarding": UNOBSERVED}))
    assert read_projection("https://api", "tok", []) == UNOBSERVED


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
