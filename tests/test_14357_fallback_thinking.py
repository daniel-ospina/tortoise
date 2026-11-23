"""#14357 — the extraction fallback must run NON-THINKING, exactly like the primary.

The defect these tests pin: ``_build_single`` built the ``openrouter`` (and
``venice``) pool member as a bare adapter, i.e. with ``disable_reasoning=False``,
so a call that FAILED OVER ran ``deepseek/deepseek-v4-flash`` at the model's
default (thinking: high) — the failure mode this same module records as
*"1500/1500 reasoning tokens, finish_reason=length, ZERO output content"*.

⛔ Every assertion below is on the WIRE BODY (the request ``complete()``
actually posts), never on the constructor: the class default
(``disable_reasoning=False``) is exactly where this rotted, so an assertion on
the constructor would have passed forever while the fallback stayed thinking-on.
"""
from __future__ import annotations

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
    so on that path the thinking-on adapter is the ONLY adapter."""
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
    collapse) and nothing measured. The lane gate keeps it byte-identical: no
    ``reasoning`` on the openrouter member, no ``venice_parameters`` on venice.

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
