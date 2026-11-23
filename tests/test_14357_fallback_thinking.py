"""#14357 — the extraction fallback must run NON-THINKING, exactly like the primary.

The defect these tests pin: ``_build_single`` built the ``openrouter`` (and
``venice``) pool member as a bare adapter, i.e. with ``disable_reasoning=False``,
so a call that FAILED OVER ran ``deepseek/deepseek-v4-flash`` at the model's
default (thinking: high) — the failure mode this same module records as
*"1500/1500 reasoning tokens, finish_reason=length, ZERO output content"*.

⛔ Every WIRE-BODY assertion below is on the body (the request ``complete()``
actually posts), never on the constructor: the class default
(``disable_reasoning=False``) is exactly where this rotted, so an assertion on
the constructor would have passed forever while the fallback stayed thinking-on.
That contract is about the suppression tests ABOVE the
"the yield guard: an empty extraction never reads as no facts" heading. The
tests below that heading are the OTHER subject — the yield guard in
``extractor_v2._complete`` (its raise, its no-retry decision, and the census
entry) — and they are not wire-body assertions.

⛔ Cite that boundary by the heading, never by a line number or a case count:
this file grows, and a pinned ordinal went three tests stale in one round.
"""
from __future__ import annotations

import inspect

import pytest
import requests

from tortoise import extractor_v2 as v2
from tortoise.model_adapters import (
    _reset_failover_cooldown,
    build_extractor_model,
    build_reader_model,
)

EXTRACT_MODEL = "deepseek/deepseek-v4-flash"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    _reset_failover_cooldown()
    for k in ("TORTOISE_EXTRACTOR_PROVIDER", "DEEPSEEK_API_KEY",
              "OPENROUTER_API_KEY", "VENICE_API_KEY",
              "TORTOISE_EXTRACT_MODEL", "TORTOISE_JSON_MODE",
              "TORTOISE_EXTRACTOR_MAX_TOKENS",
              "TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN",
              # ``test_reader_lane_wire_body_is_not_suppressed`` is the one test
              # here that goes through ``resolve_reader_provider``, which reads
              # TORTOISE_ASK_PROVIDER and RAISES on a value outside
              # ``_ASK_PROVIDERS`` — an ambient value would red this file for a
              # reason that is not the diff (``tests/conftest.py``
              # ``_PROVIDER_ROUTING_ENVS`` declares the same hazard).
              "TORTOISE_ASK_MODEL", "TORTOISE_ASK_PROVIDER",
              "TORTOISE_DB_URI", "TORTOISE_API_URL"):
        monkeypatch.delenv(k, raising=False)
    yield
    _reset_failover_cooldown()


def _recorder():
    """Patch the HTTP layer and record every wire body posted.

    Returns ``(bodies, fake_post)`` where ``bodies`` is a list of
    ``(url, body)``. Same technique as ``test_model_adapters_routing``'s
    ``_fake_post_logger`` — the body is the artifact under test.
    """
    bodies: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def _fake_post(self_or_url, *args, **kwargs):
        url = args[0] if args else self_or_url
        bodies.append((url, kwargs.get("json", {})))
        return _Resp()

    return bodies, _fake_post


# ── the three pool members, wire bodies side by side ──────────────────────

def _three_member_bodies(monkeypatch, model_id: str = EXTRACT_MODEL, *,
                         builder=build_extractor_model, max_tokens: int = 4000):
    """Build the PRODUCTION 3-provider pool and post one call on each member.

    Returns ``{provider: body}`` — the wire body each pool member sends for the
    same call, produced by the same builder production uses.
    """
    for env, key in (("DEEPSEEK_API_KEY", "ds"),
                     ("OPENROUTER_API_KEY", "or"),
                     ("VENICE_API_KEY", "vn")):
        monkeypatch.setenv(env, key)
    model = builder(model_id, max_tokens=max_tokens, temperature=0.0)
    assert len(model.providers) == 3, "expected the 3-member rotation pool"

    out: dict[str, dict] = {}
    for adapter in model.providers:
        bodies, fake = _recorder()
        monkeypatch.setattr(requests.sessions.Session, "post", fake)
        adapter.complete(system="sys", user="usr")
        (url, body), = bodies
        out[adapter.provider] = body
        assert url == adapter.base_url
    return out


def test_all_three_pool_members_suppress_thinking(monkeypatch):
    """The primary and BOTH fallbacks carry an explicit suppression.

    Before #14357 only ``deepseek-direct`` did; ``openrouter`` and ``venice``
    sent no reasoning field at all. This is the assertion the class default
    could never make.
    """
    bodies = _three_member_bodies(monkeypatch)

    # primary — the direct route's documented toggle.
    assert bodies["deepseek-direct"]["thinking"] == {"type": "disabled"}

    # openrouter member — OpenRouter's own documented field
    # (``reasoning.effort`` whose enum includes "none").
    assert bodies["openrouter"]["reasoning"] == {"effort": "none"}

    # venice member — Venice's native toggle, not OpenRouter's object.
    assert bodies["venice"]["venice_parameters"] == {"disable_thinking": True}
    assert "reasoning" not in bodies["venice"], (
        "venice suppresses through venice_parameters.disable_thinking — one "
        "field per provider, or it is ambiguous which one is load-bearing")

    # every member names the SAME model, so the three are comparable
    for body in bodies.values():
        assert body["model"].endswith("deepseek-v4-flash")


def test_v4_pro_pool_members_agree_and_keep_the_default(monkeypatch):
    """The flip side of the same invariant (#1790 scope guard).

    ``deepseek-v4-pro`` keeps its default reasoning on ALL THREE members — the
    gate is the flash family, not "the fallback". A fix that suppressed the
    fallback unconditionally would newly DISAGREE with its own primary here.
    """
    bodies = _three_member_bodies(monkeypatch, "deepseek/deepseek-v4-pro")
    assert "thinking" not in bodies["deepseek-direct"]
    assert "reasoning" not in bodies["openrouter"]
    assert "venice_parameters" not in bodies["venice"]


def test_the_lane_gate_cannot_be_re_defaulted_into_existence():
    """The lane gate is structural, so pin the STRUCTURE.

    ``_build_single``'s docstring says ``suppress_reasoning`` is required and
    keyword-only on purpose — *"a default would let the next caller silently land
    back on the thinking-on body #14357 fixed"*. Measured before this test: adding
    ``= False`` to the annotation left 131 tests green, i.e. the one edit the
    docstring forbids was invisible to the suite.
    """
    from tortoise.model_adapters import _build_single

    # Both halves, pinned on the SIGNATURE: a call-site assertion cannot
    # distinguish "keyword-only" from "too many positionals" (`max_tokens` and
    # `temperature` already sit after the `*`), so a mutant that moves the flag
    # before the star left the previous version of this test green.
    param = inspect.signature(_build_single).parameters["suppress_reasoning"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, param.kind
    assert param.default is inspect.Parameter.empty, param.default
    with pytest.raises(TypeError):
        _build_single("openrouter", EXTRACT_MODEL, max_tokens=1,
                      temperature=0.0)


def test_the_family_gate_matches_the_family_not_one_literal():
    """#4129: the gate keys on the flash FAMILY, not on one exact id.

    ``deepseek-v4-flash`` is RETIRED — api.deepseek.com answers 200 to it and
    silently serves ``deepseek-flash`` — and a literal-only gate is what let that
    rename re-enable thinking with no failing test to say so (the recorded
    #4129 finding, whose predicate ``analyze._is_deepseek_flash_family`` this
    mirrors). A literal-only gate here means EVERY pool member sends no
    suppression field for the served id — i.e. the pre-#14357 body, silently.
    """
    from tortoise.model_adapters import _runs_non_thinking

    for family_id in ("deepseek-v4-flash", "deepseek-flash",
                      "deepseek/deepseek-v4-flash",
                      "deepseek/deepseek-flash",
                      "deepseek-v4-flash-0731",           # the dated build
                      "deepseek/deepseek-v4-flash:free",  # the OpenRouter suffix
                      ):
        assert _runs_non_thinking(family_id) is True, family_id
    for outside_id in ("deepseek-v4-pro", "deepseek/deepseek-v4-pro",
                       "deepseek/deepseek-v4-pro-0731", "gpt-4o", ""):
        assert _runs_non_thinking(outside_id) is False, outside_id


def test_a_registry_family_id_gets_the_suppressed_body(monkeypatch):
    """End-to-end for the same family gate: a wire id the gate used to MISS

    (``deepseek-flash`` — the id the provider actually serves for the retired
    one) now gets the suppressed body on the two hop members."""
    bodies = _three_member_bodies(monkeypatch, "deepseek/deepseek-flash")
    assert bodies["deepseek-direct"]["thinking"] == {"type": "disabled"}
    assert bodies["openrouter"]["reasoning"] == {"effort": "none"}
    assert bodies["venice"]["venice_parameters"] == {"disable_thinking": True}


def test_failover_call_is_the_suppressed_one(monkeypatch):
    """THE defect shape: the primary 429s, the fallback serves the call.

    The fallback's request is the one that used to run thinking-on — the route
    nobody watches, taken exactly when the primary is in trouble.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    model = build_extractor_model(EXTRACT_MODEL, max_tokens=4000,
                                  temperature=0.0)
    assert [p.provider for p in (model.primary, model.fallback)] == [
        "deepseek-direct", "openrouter"]

    bodies, _fake = _recorder()

    class _R429:
        status_code = 429
        text = "rate limited"

    def _failover_post(self_or_url, *args, **kwargs):
        url = args[0] if args else self_or_url
        bodies.append((url, kwargs.get("json", {})))
        if "api.deepseek.com" in url:
            raise requests.HTTPError("429 rate limited", response=_R429())
        return _Resp200()

    class _Resp200:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(requests.sessions.Session, "post", _failover_post)
    assert model.complete(system="s", user="u") == "ok"
    assert model.failover_used is True
    assert model.last_route == "openrouter"
    served_url, served_body = bodies[-1]
    assert served_url == "https://openrouter.ai/api/v1/chat/completions"
    assert served_body["reasoning"] == {"effort": "none"}


def test_keyless_build_suppresses_too(monkeypatch):
    """A bare environment takes the ``["openrouter"]`` pool and NOTHING else —
    so the sole member is the very adapter that used to run thinking-ON."""
    bodies, fake = _recorder()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    model = build_extractor_model(EXTRACT_MODEL, max_tokens=4000,
                                  temperature=0.0)
    model.complete(system="s", user="u")
    (url, body), = bodies
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert body["reasoning"] == {"effort": "none"}


# ── the ask lane is OUT of #14357's scope ─────────────────────────

def test_reader_lane_wire_body_is_not_suppressed(monkeypatch):
    """The reader SHARES ``_build_routing_model`` but not this fix's scope.

    ``build_reader_model`` defaults to the same flash family, so a lane-blind
    suppression silently rewrote the ask lane's serving config — a product
    change #14357 never names (a 500-token answer is not the extraction
    collapse) and nothing measured. The lane gate keeps the two HOP members
    byte-identical: no ``reasoning`` on the openrouter member, no
    ``venice_parameters`` on venice. (The ``deepseek-direct`` member is not a
    hop member: it hardcodes its toggle, and its family gate was widened here —
    the assertion below holds for the canonical id before and after.)

    Red on the lane-blind builder this test was added against: both bodies
    carried their suppression field.
    """
    bodies = _three_member_bodies(monkeypatch, builder=build_reader_model,
                                  max_tokens=500)
    for provider in ("openrouter", "venice"):
        assert "reasoning" not in bodies[provider], (
            f"the ask lane's {provider} member gained a reasoning field — "
            "#14357 is scoped to the extractor")
        assert "venice_parameters" not in bodies[provider], (
            f"the ask lane's {provider} member gained a suppression field — "
            "#14357 is scoped to the extractor")
    # unchanged pre-existing behaviour: the DIRECT member hardcodes its toggle
    # in ``DeepSeekDirectModel.complete``, so that assertion holds before and
    # after #14357 and documents WHICH member the fix ever moved.
    assert bodies["deepseek-direct"]["thinking"] == {"type": "disabled"}


def test_thinking_budget_still_wins_over_suppression(monkeypatch):
    """Precedence unchanged: a bounded reasoning budget is a deliberate spend,
    not a suppression — it is preserved on the openrouter member."""
    from tortoise.model_adapters import OpenRouterModel
    bodies, fake = _recorder()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    OpenRouterModel("deepseek/deepseek-v4-flash", max_tokens=500,
                    thinking_budget=2000, disable_reasoning=True
                    ).complete(system="s", user="u")
    (_, body), = bodies
    assert body["reasoning"] == {"max_tokens": 2000}


# ── the yield guard: an empty extraction never reads as "no facts" ────────

class _Collapse:
    """The recorded failure mode: the whole budget went to hidden reasoning."""

    last_finish_reason = None
    last_prompt_tokens = 0

    def __init__(self):
        self.calls = 0
        self.last_completion_tokens = 1500

    def complete(self, *, system: str, user: str, max_tokens=None):
        self.calls += 1
        self.last_finish_reason = "length"
        return ""


def test_empty_length_response_raises_the_yield_guard(monkeypatch):
    from tortoise import extractor_v2 as v2

    monkeypatch.setattr(v2, "_COMPLETE_RETRIES", 2)
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    m = _Collapse()
    stats: dict = {}
    with pytest.raises(v2._ReasoningCollapse) as ei:
        v2._complete(m, "s", "u", max_tokens=1500, stats=stats)
    # NO retry: an identical prompt at temperature 0 collapses identically.
    assert m.calls == 1
    assert stats["last_class"] == "reasoning_collapse"
    assert stats["truncated"] is True
    assert v2._is_fatal_error(ei.value) is True
    census: dict = {}
    v2._bump_census(census, ei.value)
    assert census == {"reasoning_collapse": 1}
    # the message names the SERVING route so a failover is investigable
    assert "reasoning" in str(ei.value)
    # …and that claim is only pinned if the INTERPOLATION is asserted: the prose
    # above contains "hidden reasoning tokens" unconditionally, so asserting on
    # the word alone stays green after the route/model fields are dropped.
    assert "route=" in str(ei.value), str(ei.value)
    assert "model=" in str(ei.value), str(ei.value)


def test_complete_docstring_names_the_yield_guard_raise():
    """The function's OWN contract names the raise a caller must handle.

    ``_complete`` raises ``_ReasoningCollapse`` on the collapse shape and that
    raise is fatal BY TYPE — no retry, and a census class the eval gate vetoes
    on. The behaviour was documented on the exception class and at the guard
    site, but not in ``_complete``'s docstring, which described only
    retry/backoff: a caller reading the contract it calls saw a policy that
    suggests every failure is retried, and nothing that warned a call can come
    back as a fatal, censused raise.
    """
    doc = v2._complete.__doc__ or ""
    assert "_ReasoningCollapse" in doc, doc
    assert "reasoning_collapse" in doc, doc


def test_escalated_collapse_stays_fatal_not_a_recoverable_truncation(monkeypatch):
    """#14357 — a collapse on the ESCALATED call is the collapse, not a
    truncated parse.

    The escalation ladder's abort arm re-parses the retained base response and,
    failing that, raises ``_ParseError(truncated=True)``. An escalated
    ``_ReasoningCollapse`` before this fix fell into that wrap, so the census
    recorded ``truncated_parse_error`` — a class the eval gate treats as
    RECOVERABLE (rate-limits) — for a call that produced ZERO content. The
    census is what decides whether that gate vetoes, so the wrap silently
    re-opened the fail-open this PR closes.
    """
    class _EscCollapses:
        last_finish_reason = "length"
        last_prompt_tokens = 10

        def __init__(self):
            self.calls = 0
            self.last_completion_tokens = 1500

        def complete(self, *, system: str, user: str, max_tokens=None):
            self.calls += 1
            self.last_finish_reason = "length"
            if (max_tokens or 0) >= 32000:
                return ""  # the ESCALATED budget collapsed too
            # Base: truncated NON-EMPTY with NO recoverable head, so the abort
            # arm's canonical-then-rung-4 re-parse finds nothing to fall back
            # to — the only shape that reaches the wrap this test guards.
            return '{"entities": [{"name": "a"'

    m = _EscCollapses()
    census: dict = {}
    with pytest.raises(v2._ReasoningCollapse):
        try:
            v2.run_s2(m, "STORY", stats={})
        except BaseException as e:  # record the class, then re-raise
            v2._bump_census(census, e)
            raise
    assert census == {"reasoning_collapse": 1}, census
    assert m.calls == 2  # base + one escalated call, no third


def test_reasoning_collapse_is_not_a_recoverable_census_class():
    """The FAIL-CLOSED half of #14357's contract, pinned where it lives.

    ``_ReasoningCollapse``'s docstring and the PR body both claim the longmem
    eval gate VETOES on ``reasoning_collapse`` rather than rate-limiting it.
    That is a property of ``tools/longmem_eval/report.py``, a file this PR does
    not otherwise touch — so without this assertion a one-line edit there
    re-opens the exact fail-open #14357 closes (a zero-content extraction
    certified as recoverable), with the suite green. Measured before this test:
    adding ``"reasoning_collapse"`` to ``RECOVERABLE_CENSUS_CLASSES`` flipped
    the grade ``hard`` -> ``recoverable`` and redded nothing (163 passed).
    """
    from tools.longmem_eval import report

    assert "reasoning_collapse" not in report.RECOVERABLE_CENSUS_CLASSES
    assert report._outcome_grade(
        {"valid": False, "error_classes": {"reasoning_collapse": 1}}
    ) == "hard"
    # …and the recoverable side of the same seam still behaves, so the pin
    # above is distinguishing the two, not just asserting a constant.
    assert report._outcome_grade(
        {"valid": False, "error_classes": {"truncated_parse_error": 1}}
    ) == "recoverable"


def test_non_empty_length_response_is_still_returned():
    """A truncated REAL answer is not the collapse — the escalation ladder
    (#2134 Task 4) owns that shape and must keep receiving it."""
    class _Truncated:
        last_finish_reason = "length"
        last_prompt_tokens = 10
        last_completion_tokens = 1500

        def complete(self, *, system: str, user: str, max_tokens=None):
            return "a real narrative that got cut"

    assert v2._complete(_Truncated(), "s", "u", max_tokens=1500) == \
        "a real narrative that got cut"


def test_empty_stop_response_is_still_returned():
    """An empty answer at ``finish_reason=stop`` is a legitimate empty answer
    (the two-phase prompt abstains in writing) — NOT a reasoning burn."""
    class _EmptyStop:
        last_finish_reason = "stop"
        last_prompt_tokens = 10
        last_completion_tokens = 0

        def complete(self, *, system: str, user: str, max_tokens=None):
            return ""

    assert v2._complete(_EmptyStop(), "s", "u", max_tokens=1500) == ""


def test_empty_length_base_still_runs_the_escalation_rung():
    """#14357 review (P1) — the yield guard must not delete the #2134 rung.

    ``_complete`` raises ``_ReasoningCollapse`` on empty content at
    ``finish_reason="length"`` BEFORE ``_complete_parsed`` can read ``finish``,
    so an unguarded raise made that response fatal at the BASE budget and the
    one-shot escalation rung never ran — while PRE-#14357 it did, because the
    rung keys on ``finish == "length"`` and not on content. A collapse at the
    S2/S4 16K cap is precisely the shape a 32K budget can recover, so that was
    a silently deleted recovery path.

    Its sibling ``test_escalated_collapse_stays_fatal_not_a_recoverable_
    truncation`` pins the other half: a collapse that survives the escalation
    is still ``reasoning_collapse``, never a recoverable truncation.
    """
    class _BaseCollapsesEscRecovers:
        last_finish_reason = "length"
        last_prompt_tokens = 10
        last_completion_tokens = 1500

        def __init__(self):
            self.budgets: list[int] = []

        def complete(self, *, system: str, user: str, max_tokens=None):
            self.budgets.append(max_tokens)
            if (max_tokens or 0) >= 32000:
                self.last_finish_reason = "stop"
                self.last_completion_tokens = 12
                return '{"entities": [{"name": "a", "kind": "object"}]}'
            self.last_finish_reason = "length"
            self.last_completion_tokens = 1500
            return ""  # the collapse: no content at all, finish=length

    m = _BaseCollapsesEscRecovers()
    stats: dict = {}
    parsed = v2._complete_parsed(m, "SYSTEM", "USER",
                                 max_tokens=16000, stats=stats)
    # base at the S2/S4 cap, then ONE escalated call at the 32K default — the
    # rung ran, and it took no third call to succeed.
    assert m.budgets == [16000, 32000], m.budgets
    assert parsed["entities"][0]["name"] == "a", parsed
    assert stats["recovery"]["escalated"] == 1, stats.get("recovery")


def test_collapsed_extraction_yields_no_document_and_is_recorded(monkeypatch):
    """End to end: a collapsed S1 is a RECORDED failure that extracts nothing.

    The bug this closes: the same outcome (zero points) was reached silently,
    i.e. indistinguishable from "the document had no facts in it".
    """
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)

    class _CollapseModel(_Collapse):
        def complete(self, *, system: str, user: str, max_tokens=None):
            super().complete(system=system, user=user, max_tokens=max_tokens)
            return ""

    out = v2.extract_session_v2(_CollapseModel(), [
        {"role": "user", "content": "we decided X"},
        {"role": "assistant", "content": "the plan is durable"},
    ])
    assert out["error_census"]["reasoning_collapse"] == 1
    assert any("S1 chunk failed" in e for e in out["errors"])
    assert out["embed_list"] == {}
    assert out["stats"]["llm"]["truncated"] == 1
