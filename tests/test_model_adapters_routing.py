"""Provider routing unit tests (#1530 P2).

Pins the D2 resolution table (TORTOISE_EXTRACTOR_PROVIDER + key inference,
explicit-keyless fail-closed, invalid value), the D6 model-id wire
normalization (direct = bare id, openrouter = family-prefixed), and the
RoutingModel failover contract (D4/D5): failover on transient, NO failover on
fatal 4xx, sticky forward-only per extraction, in-process cooldown flap guard.
"""
from __future__ import annotations

import pytest
import requests

from tortoise.model_adapters import (
    MODELS,
    RotatingModel,
    RoutingModel,
    _primary_in_cooldown,
    _reset_failover_cooldown,
    build_extractor_model,
    resolve_extractor_provider,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    _reset_failover_cooldown()
    for k in ("TORTOISE_EXTRACTOR_PROVIDER", "DEEPSEEK_API_KEY",
              "OPENROUTER_API_KEY", "VENICE_API_KEY",
              "TORTOISE_EXTRACT_MODEL",
              "TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN",
              "TORTOISE_ASK_MODEL", "TORTOISE_ASK_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    yield
    _reset_failover_cooldown()


# ── resolve_extractor_provider — the full D2 table ─────────────────────────

def test_resolve_deepseek_direct_with_both_keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "deepseek-direct")
    assert resolve_extractor_provider() == ("deepseek-direct", ["deepseek-direct", "openrouter"])


def test_resolve_openrouter_with_both_keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    assert resolve_extractor_provider() == ("openrouter", ["openrouter", "deepseek-direct"])


def test_resolve_deepseek_direct_without_fallback(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "deepseek-direct")
    assert resolve_extractor_provider() == ("deepseek-direct", ["deepseek-direct"])


def test_resolve_openrouter_without_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    assert resolve_extractor_provider() == ("openrouter", ["openrouter"])


def test_resolve_explicit_deepseek_missing_key_fails_closed(monkeypatch):
    """Explicit provider names a key that isn't set → ValueError, never
    silently route elsewhere."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")  # wrong key present
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "deepseek-direct")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        resolve_extractor_provider()


def test_resolve_explicit_openrouter_missing_key_fails_closed(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")  # wrong key present
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        resolve_extractor_provider()


def test_resolve_invalid_value_lists_valid(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="deepseek-direct \\| openrouter"):
        resolve_extractor_provider()


def test_resolve_unset_infers_deepseek_primary(monkeypatch):
    """Unset TORTOISE_EXTRACTOR_PROVIDER + both keys → deepseek-direct primary
    (owner-confirmed production decision, epic #1509 00-scope item 6)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert resolve_extractor_provider() == ("deepseek-direct", ["deepseek-direct", "openrouter"])


def test_resolve_unset_deepseek_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    assert resolve_extractor_provider() == ("deepseek-direct", ["deepseek-direct"])


def test_resolve_unset_openrouter_only(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert resolve_extractor_provider() == ("openrouter", ["openrouter"])


def test_resolve_unset_no_keys(monkeypatch):
    """No keys → (None, None) — the caller gate fails closed as today."""
    assert resolve_extractor_provider() == (None, None)


def test_resolve_case_insensitive(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "DeepSeek-Direct")
    assert resolve_extractor_provider()[0] == "deepseek-direct"


# ── model-id wire normalization (D6) ────────────────────────────────────────

def _fake_post_logger():
    """Monkeypatches requests.post; returns the call log [(url, body)]."""
    log = []

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def _fake_post(self_or_url, *args, **kwargs):
        if args:  # noqa: SIM108
            url = args[0]
        else:
            url = self_or_url
        log.append((url, kwargs.get("json", {})))
        return _FakeResp()

    return log, _fake_post


def test_direct_route_sends_nonreasoning_model_id(monkeypatch):
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = build_extractor_model("deepseek/deepseek-v4-flash",
                                  max_tokens=4000, temperature=0.0)
    out = model.complete(system="s", user="u")
    assert out == "ok"
    url, body = log[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    # #1790: the direct route sends the current documented flash id with
    # thinking explicitly disabled — 'deepseek-v4-flash' reasons by DEFAULT
    # (thinking: high) and collapses to empty output; the legacy
    # non-reasoning alias was retired upstream 2026-07-24 (still served
    # during the transition).
    assert body["model"] == "deepseek-v4-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 4000


def test_direct_v4pro_does_not_disable_thinking(monkeypatch):
    """#1790 scope guard: the thinking-disable is flash-family only —
    'deepseek-v4-pro' keeps its default (no collapse evidence, pending
    verification)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = build_extractor_model("deepseek-v4-pro-direct")
    model.complete(system="s", user="u")
    body = log[0][1]
    assert body["model"] == "deepseek-v4-pro"
    assert "thinking" not in body


def test_flash_direct_registry_model_disables_thinking(monkeypatch):
    """#1790 drift catch: the REGISTRY ``deepseek-flash-direct`` entry (the
    eval harness' actual production path) must reach the thinking-disable
    gate. A future id rename in MODELS without updating the gate fails this
    — prefixed ids (e.g. "deepseek/deepseek-v4-flash") must not bypass it.
    The v4-pro sibling must NOT carry thinking (flash-family only)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = MODELS["deepseek-flash-direct"]()
    out = model.complete(system="s", user="u")
    assert out == "ok"
    body = log[0][1]
    assert body["model"] == "deepseek-v4-flash"
    assert body["thinking"] == {"type": "disabled"}

    # v4-pro sibling: flash-family only — no thinking key.
    log2, fake2 = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake2)
    MODELS["deepseek-v4-pro-direct"]().complete(system="s", user="u")
    body2 = log2[0][1]
    assert body2["model"] == "deepseek-v4-pro"
    assert "thinking" not in body2

    # prefixed direct construction must reach the same gate (the router
    # strips prefixes at _build_single, but a directly-built adapter with a
    # prefixed id must not bypass it) — this assertion fails under a
    # literal `self.id == "deepseek-v4-flash"` gate (mutation-verified).
    from tortoise.model_adapters import DeepSeekDirectModel
    log3, fake3 = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake3)
    prefixed = DeepSeekDirectModel("deepseek/deepseek-v4-flash")
    prefixed.complete(system="s", user="u")
    body3 = log3[0][1]
    assert body3["thinking"] == {"type": "disabled"}


def test_deepseek_direct_json_mode_default_on(monkeypatch):
    """#1746 (D6): JSON-mode parity on the direct path — the request body
    carries ``response_format`` under the default TORTOISE_JSON_MODE=1
    WHEN the prompt requests JSON (the S2/S4 extractor prompts say
    "JSON object" — #1782 gates on this: DeepSeek 400s otherwise)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    model = build_extractor_model("deepseek/deepseek-v4-flash")
    model.complete(system="Return a JSON object per the contract", user="u")
    assert log[0][1]["response_format"] == {"type": "json_object"}


def test_json_mode_omitted_for_non_json_prompt(monkeypatch):
    """#1782: json_object mode is NOT sent when the prompt doesn't contain
    the text "json" — DeepSeek returns HTTP 400 for that combination (the
    preflight billing probe / ping prompts have no "json"). The mode must be
    prompt-gated, not unconditional under TORTOISE_JSON_MODE=1."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    model = build_extractor_model("deepseek/deepseek-v4-flash")
    model.complete(system="You are a story summarizer.", user="Tell me a story.")
    assert "response_format" not in log[0][1]


def test_json_mode_gate_case_insensitive(monkeypatch):
    """#1782: the gate is case-insensitive — the S2/S4 prompts say
    "JSON object" (uppercase); the probe prompts omit it entirely."""
    from tortoise.model_adapters import _prompt_requests_json
    assert _prompt_requests_json("Return a JSON object", "u") is True
    assert _prompt_requests_json("json output expected", "u") is True
    assert _prompt_requests_json("You are a summarizer.", "Tell a story.") is False
    assert _prompt_requests_json(None, None) is False


def test_should_send_json_mode_env_gate(monkeypatch):
    """#1782: pin the env branch of the 2x2 gate matrix directly on
    ``_should_send_json_mode`` — prompt-requests-JSON x env-flag.
    env=0 alone must suppress the mode even when the prompt asks for
    JSON; env unset/1 must enable it; a non-JSON prompt never sends it."""
    from tortoise.model_adapters import _should_send_json_mode
    monkeypatch.setenv("TORTOISE_JSON_MODE", "0")
    assert _should_send_json_mode("Return a JSON object", "u") is False
    monkeypatch.setenv("TORTOISE_JSON_MODE", "1")
    assert _should_send_json_mode("Return a JSON object", "u") is True
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    assert _should_send_json_mode("Return a JSON object", "u") is True
    assert _should_send_json_mode("You are a summarizer.", "Tell a story.") is False


def test_deepseek_direct_json_mode_disabled(monkeypatch):
    """#1746 (D6): TORTOISE_JSON_MODE=0 omits ``response_format`` entirely
    (the documented escape hatch). The prompt REQUESTS JSON so the absence
    is attributable to the env toggle alone, not the prompt gate — mirrors
    test_deepseek_direct_json_mode_default_on (env is the only delta)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_JSON_MODE", "0")
    model = build_extractor_model("deepseek/deepseek-v4-flash")
    model.complete(system="Return a JSON object per the contract", user="u")
    assert "response_format" not in log[0][1]


def test_openrouter_route_sends_family_prefixed_model_id(monkeypatch):
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    model = build_extractor_model("deepseek/deepseek-v4-flash",
                                  max_tokens=None, temperature=0.0)
    model.complete(system="s", user="u")
    url, body = log[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "deepseek/deepseek-v4-flash"  # unchanged


def test_no_key_lenient_build_defaults_to_openrouter(monkeypatch):
    """D3: no keys → lenient single OpenRouter adapter (back-compat for
    direct callers / TestModelAdapterBounds)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    model = build_extractor_model()
    assert model.provider == "openrouter"
    assert model.fallback is None
    model.complete(system="s", user="u")
    assert log[0][0] == "https://openrouter.ai/api/v1/chat/completions"


def test_openrouter_json_mode_default_on(monkeypatch):
    """#1782: the OpenRouter path carries ``response_format`` under the
    default TORTOISE_JSON_MODE=1 WHEN the prompt requests JSON — pins the
    gate on OpenRouterModel.complete itself (the DeepSeek-only tests would
    stay green if the OpenRouter gate were missing, and the RoutingModel
    preflight-probe failover would silently carry json_object on non-JSON
    prompts — the exact drift class #1782 repairs)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")  # no DEEPSEEK key → OpenRouter primary
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    model = build_extractor_model("deepseek/deepseek-v4-flash")
    model.complete(system="Return a JSON object per the contract", user="u")
    assert log[0][0] == "https://openrouter.ai/api/v1/chat/completions"
    assert log[0][1]["response_format"] == {"type": "json_object"}


def test_openrouter_json_mode_omitted_for_non_json_prompt(monkeypatch):
    """#1782: the OpenRouter path omits ``response_format`` on probe-shaped
    non-JSON prompts (the preflight probe / ping) — json_object must not
    leak through the OpenRouter fallback lane either."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.delenv("TORTOISE_JSON_MODE", raising=False)
    model = build_extractor_model("deepseek/deepseek-v4-flash")
    model.complete(system="You are a story summarizer.", user="Tell me a story.")
    assert log[0][0] == "https://openrouter.ai/api/v1/chat/completions"
    assert "response_format" not in log[0][1]


def test_default_model_id_from_env(monkeypatch):
    monkeypatch.setenv("TORTOISE_EXTRACT_MODEL", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = build_extractor_model()
    assert model.primary.id == "deepseek-v4-pro"          # stripped for direct


# ── RoutingModel failover contract (D4/D5) ──────────────────────────────────

class _StubAdapter:
    """complete() stub with a call counter + scripted behavior."""
    def __init__(self, provider: str):
        self.provider = provider
        self.calls = 0
        self._exc = None

    def fail_with(self, exc: BaseException):
        self._exc = exc
        return self

    def complete(self, *, system, user, max_tokens: int | None = None):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return f"{self.provider}:{system}:{user}"


def test_failover_on_transient_uses_fallback(monkeypatch):
    _reset_failover_cooldown()
    primary = _StubAdapter("deepseek-direct").fail_with(requests.ConnectionError("down"))
    fallback = _StubAdapter("openrouter")
    model = RoutingModel(primary, fallback, cooldown_s=0)
    out = model.complete(system="s", user="u")
    assert out.startswith("openrouter:")
    assert primary.calls == 1
    assert fallback.calls == 1
    assert model.last_route == "openrouter"
    assert model.route == "openrouter"
    assert model.failover_used is True
    assert model.errors and "ConnectionError" in model.errors[0]


def test_fatal_4xx_no_failover(monkeypatch):
    """FATAL (401/402/403) → exception propagates, fallback NEVER called."""
    for status in (401, 402, 403):
        _reset_failover_cooldown()
        err = requests.HTTPError(f"HTTP {status}")
        err.response = type("R", (), {"status_code": status})()
        primary = _StubAdapter("deepseek-direct").fail_with(err)
        fallback = _StubAdapter("openrouter")
        model = RoutingModel(primary, fallback, cooldown_s=0)
        with pytest.raises(requests.HTTPError):
            model.complete(system="s", user="u")
        assert fallback.calls == 0, f"fallback must never run on {status}"
        assert model.failover_used is False


def test_fatal_config_4xx_no_failover(monkeypatch):
    """FATAL_CONFIG (400/404/unknown 4xx) → no retry, no failover."""
    for status in (400, 404, 422):
        _reset_failover_cooldown()
        err = requests.HTTPError(f"HTTP {status}")
        err.response = type("R", (), {"status_code": status})()
        primary = _StubAdapter("deepseek-direct").fail_with(err)
        fallback = _StubAdapter("openrouter")
        model = RoutingModel(primary, fallback, cooldown_s=0)
        with pytest.raises(requests.HTTPError):
            model.complete(system="s", user="u")
        assert fallback.calls == 0


def test_no_fallback_transient_reraised(monkeypatch):
    _reset_failover_cooldown()
    primary = _StubAdapter("openrouter").fail_with(requests.Timeout("slow"))
    model = RoutingModel(primary, fallback=None, cooldown_s=0)
    with pytest.raises(requests.Timeout):
        model.complete(system="s", user="u")


def test_sticky_forward_only_after_failover(monkeypatch):
    """D5: after failover, subsequent calls stay on the fallback — no primary
    re-try mid-extraction (no flip-flop)."""
    _reset_failover_cooldown()
    primary = _StubAdapter("deepseek-direct").fail_with(requests.ConnectionError("down"))
    fallback = _StubAdapter("openrouter")
    model = RoutingModel(primary, fallback, cooldown_s=0)
    model.complete(system="s", user="u")   # failover
    assert model.route == "openrouter"
    model.complete(system="s", user="u")   # must NOT go back to primary
    model.complete(system="s", user="u")
    assert primary.calls == 1, "primary must never be re-tried after failover"
    assert fallback.calls == 3


def test_cooldown_skips_primary(monkeypatch):
    """A primary in the process-local cooldown window is skipped (fallback
    used directly) — the #1350 flap protection."""
    _reset_failover_cooldown()
    primary = _StubAdapter("deepseek-direct").fail_with(requests.ConnectionError("down"))
    fallback = _StubAdapter("openrouter")
    # Transient failure + failover writes the cooldown note for the primary.
    model = RoutingModel(primary, fallback, cooldown_s=3600)
    model.complete(system="s", user="u")
    assert model.failover_used is True
    assert _primary_in_cooldown("deepseek-direct", 3600) is True
    # A NEW model in the same process skips the dead primary outright.
    fresh = _StubAdapter("deepseek-direct")
    model3 = RoutingModel(fresh, fallback, cooldown_s=3600)
    model3.complete(system="s", user="u")
    assert fresh.calls == 0, "primary in cooldown must be skipped"
    assert model3.route == "openrouter"


def test_cooldown_zero_disables(monkeypatch):
    """cooldown_s=0 disables the flap guard — the primary is always tried."""
    _reset_failover_cooldown()
    primary = _StubAdapter("deepseek-direct").fail_with(requests.ConnectionError("down"))
    fallback = _StubAdapter("openrouter")
    RoutingModel(primary, fallback, cooldown_s=0).complete(system="s", user="u")
    assert _primary_in_cooldown("deepseek-direct", 0) is False
    fresh = _StubAdapter("deepseek-direct")
    model = RoutingModel(fresh, fallback, cooldown_s=0)
    model.complete(system="s", user="u")
    assert fresh.calls == 1


def test_build_extractor_model_returns_routing_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    model = build_extractor_model()
    assert isinstance(model, RoutingModel)
    assert model.provider == "deepseek-direct"
    assert model.fallback is not None
    assert model.fallback.provider == "openrouter"


def test_registry_key_normalized_to_real_model_id(monkeypatch):
    """Pilot #1549: the eval CLI passes MODELS registry keys ('deepseek-flash-direct')
    to build_extractor_model; they must normalize to a real API model id or the
    suffix reaches DeepSeek as the model name (HTTP 400 on every S1 call)."""
    # Pilot #1549 (2026-08-25) + #1790: the direct-API flash wire id is the
    # current documented 'deepseek-v4-flash' with thinking explicitly
    # disabled (the legacy non-reasoning alias was retired upstream
    # 2026-07-24 — still served during the transition — but v4-flash
    # reasons by default and collapses to empty output on non-trivial S1
    # prompts otherwise: 1500/1500 reasoning tokens, finish=length, zero
    # content).
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")  # the pilot's direct route
    m = build_extractor_model("deepseek-flash-direct")
    # family-prefixed key → direct route strips → flash id on the wire
    assert m.primary.id == "deepseek-v4-flash"
    m2 = build_extractor_model("deepseek-v4-pro-direct")
    assert m2.primary.id == "deepseek-v4-pro"  # sibling direct entry unchanged
    # v4-pro keys are also family-prefixed: every pool lane gets a valid id
    # (bare ids 404 on the OpenRouter lane → fatal → pool-kill, #1549 class)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("VENICE_API_KEY", "vz")
    pool = build_extractor_model("deepseek-v4-pro-direct")
    assert {p.provider: p.id for p in pool.providers} == {
        "venice": "deepseek-v4-pro",
        "openrouter": "deepseek/deepseek-v4-pro",
        "deepseek-direct": "deepseek-v4-pro",
    }


def test_default_build_uses_nonreasoning_direct_id(monkeypatch):
    """Pilot #1549 (P1) + #1790: the DEFAULT production path —
    TORTOISE_EXTRACT_MODEL unset → 'deepseek/deepseek-v4-flash' — must not
    collapse. The direct route sends 'deepseek-v4-flash' with thinking
    disabled on the wire (sdk._model_adapter and the eval CLI both land
    here; the legacy alias was retired upstream
    2026-07-24 — still served during the transition, #1790)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = build_extractor_model()
    assert model.provider == "deepseek-direct"
    assert model.primary.id == "deepseek-v4-flash"


def test_three_provider_rotation_pool(monkeypatch):
    """Pilot #1549: with a Venice key, build_extractor_model returns the
    RotatingModel pool [deepseek-direct, openrouter, venice]. Each lane gets a
    VALID wire id for its provider: venice serves its documented catalog id,
    openrouter needs the family-prefixed id, the direct lane sends the flash
    id with thinking disabled (#1790 — the legacy alias was
    retired upstream 2026-07-24, still served during the transition)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("VENICE_API_KEY", "vz")
    m = build_extractor_model("deepseek-flash-direct")
    from tortoise.model_adapters import DeepSeekDirectModel, RotatingModel, VeniceModel
    assert isinstance(m, RotatingModel)
    # Scale-optimized order (pilot #1549 research): venice (1000 RPM backbone)
    # first, openrouter (cheapest lane), deepseek-direct (spare).
    assert [p.provider for p in m.providers] == ["venice", "openrouter", "deepseek-direct"]
    assert {p.provider: p.id for p in m.providers} == {
        "venice": "deepseek-v4-flash",            # documented venice catalog id
        "openrouter": "deepseek/deepseek-v4-flash",  # valid family-prefixed id (#1790)
        "deepseek-direct": "deepseek-v4-flash",      # flash id + thinking disabled (#1790)
    }
    assert isinstance(m.providers[2], DeepSeekDirectModel)
    assert isinstance(m.providers[0], VeniceModel)
    assert m.weights == [0.5, 0.35, 0.15]


def test_rotation_round_robin_and_cooldown(monkeypatch):
    """The pool rotates through providers and cooldowns a failing one."""
    from tortoise.model_adapters import RotatingModel

    class _P:
        def __init__(self, name, fail=False):
            self.provider = name; self.fail = fail; self.last_finish_reason = None  # noqa: E702
            self.calls = 0
        def complete(self, **kw):
            self.calls += 1
            if self.fail:
                raise RuntimeError("boom")
            return f"ok-{self.provider}"
        def close(self): pass

    a, b, c = _P("a"), _P("b"), _P("c")
    pool = RotatingModel([a, b, c], cooldown_s=0)  # weighted rotation
    served = set()
    # 48 rounds, not 12: _pick() is a weighted random draw, so a short
    # sequence can skip a bucket (~2.2% of the time with 12) — the tier-2
    # (b) leg hit it twice. 48 rounds keep the rotation intent while making
    # the all-providers-served assert deterministic (P(miss) ~1e-8).
    for _ in range(48):
        out = pool.complete(system="s", user="u")
        served.add(out)
    assert served == {"ok-a", "ok-b", "ok-c"}  # all providers get served
    # a failing provider is skipped + cooldowned. Epic #1647 (PR #1684
    # CI-fix): _pick() uses random.random() — with [bad, a] the failing
    # provider is only tried ~50% of the time, making the cooldown assert
    # flaky (pre-existing; surfaced by the docker lane). Force the order:
    # give bad the whole weight so it is ALWAYS picked first, fails, and is
    # cooldowned before a answers.
    bad = _P("bad", fail=True)
    # Epic #1647 (PR #1684 CI-fix): _pick() is random-weighted — the old
    # assert depended on bad being tried before a (~50% flaky). Force the
    # first pick to be bad deterministically: patch random to land in bad's
    # half (r<0.5 with the default [0.5,0.5] weights), then restore. bad
    # fails → cooldowned → a answers on the retry (r>=0.5 next).
    import random as _random
    _orig_random = _random.random
    try:
        # deterministic sequence: r<0.5 → bad picked first (fails, cooldown),
        # r>=0.5 → a answers on the retry
        _seq = iter([0.4, 0.9])
        _random.random = lambda: next(_seq)
        p2 = RotatingModel([bad, a], cooldown_s=10)
        assert p2.complete(system="s", user="u") == "ok-a"
    finally:
        _random.random = _orig_random
    assert bad.provider in p2._cooldowns
    # weighting: a 0.8-weighted provider dominates the share
    heavy = _P("heavy")
    p3 = RotatingModel([heavy, a], cooldown_s=0, weights=[0.8, 0.2])
    counts = {"ok-heavy": 0, "ok-a": 0}
    for _ in range(50):
        counts[pool3_out(p3)] += 1
    assert counts["ok-heavy"] > counts["ok-a"]


def pool3_out(pool):
    return pool.complete(system="s", user="u")


# ── RotatingModel 402-billing rotation (#1951) ──────────────────────────────

def _http_error(status: int) -> requests.HTTPError:
    """requests.HTTPError duck-carrying an HTTP status — the classifier's
    ``_http_status`` reads ``response.status_code``."""
    err = requests.HTTPError(f"HTTP {status}")
    err.response = type("R", (), {"status_code": status})()
    return err


def _rotating_rng(monkeypatch, values):
    """Deterministically force ``RotatingModel._pick`` outcomes: patch
    ``random.random`` with a CYCLIC sequence so under-consumption (a pick
    that lands on a cooldowned provider → ``continue``, or an extra retry
    iteration) never raises ``StopIteration`` — a broken test then fails
    with a descriptive assertion, not a raw traceback. Restored by the
    caller's ``finally`` (the patched value is returned for that)."""
    import itertools
    import random as _random

    _orig = _random.random
    _cycle = itertools.cycle(values)
    monkeypatch.setattr(_random, "random", lambda: next(_cycle))
    return _orig


class _RotatingStub:
    """RotatingModel adapter stub: raises a scripted HTTP error for the
    first ``fails`` calls, then returns ``ok-<provider>`` (mirrors
    _StubAdapter's counter + scripted-exc shape)."""
    def __init__(self, provider: str, fail_status: int | None = None,
                 fails: int = 1):
        self.provider = provider
        self.fail_status = fail_status
        self.fails = fails
        self.calls = 0
        self.last_finish_reason = None

    def complete(self, *, system, user, max_tokens: int | None = None):
        self.calls += 1
        if self.fail_status is not None and self.calls <= self.fails:
            raise _http_error(self.fail_status)
        return f"ok-{self.provider}"


def test_rotation_on_402_billing_cooldowns_and_uses_alternative(monkeypatch):
    """#1951 target 1: HTTP 402 (billing exhausted) on provider A → cooldown
    A and rotate to B; B's success is returned — the run continues, not
    dead. A is never re-tried while in its cooldown window."""
    import random as _random

    from tortoise.model_adapters import RotatingModel

    a = _RotatingStub("a", fail_status=402)
    b = _RotatingStub("b")
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])  # A picked first → B answers
    try:
        pool = RotatingModel([a, b], cooldown_s=300)
        out = pool.complete(system="s", user="u")
    finally:
        _random.random = _orig
    assert out == "ok-b"
    assert a.calls == 1, "402ing provider must not be retried in cooldown"
    assert b.calls == 1
    assert "a" in pool._cooldowns, "402 must cooldown that provider"
    assert pool.route == "b"
    assert pool.errors and "HTTPError" in pool.errors[0]


@pytest.mark.timeout(10)  # an unbounded-loop regression must fail fast, not hang CI
def test_402_cooldown_skips_provider_on_next_call(monkeypatch):
    """#1951: the cooldown written by a 402 is respected on a SUBSEQUENT
    call — the second call's first pick lands on A again (still inside the
    300s window) and the cooldown-skip branch fires: A is skipped outright
    and B keeps serving. Deleting the skip branch makes ``a.calls`` 2."""
    import random as _random

    from tortoise.model_adapters import RotatingModel

    a = _RotatingStub("a", fail_status=402)
    b = _RotatingStub("b")
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])  # picks: A(402)→B, then A(skip)→B
    try:
        pool = RotatingModel([a, b], cooldown_s=300)
        assert pool.complete(system="s", user="u") == "ok-b"   # A 402s → B
        assert pool.complete(system="s", user="u") == "ok-b"   # A in cooldown → B
    finally:
        _random.random = _orig
    assert a.calls == 1, "A must never be re-tried inside its 402 cooldown"
    assert b.calls == 2


def test_auth_and_config_4xx_still_fatal_no_rotation(monkeypatch):
    """#1951: auth AND config failures stay fatal — 401/403 (credentials)
    and config 4xx (400/404/422, request-shape bugs) re-raise immediately;
    the alternative provider is NEVER tried and no cooldown is written
    (rotation would retry the same config bug on every lane)."""
    import random as _random

    from tortoise.model_adapters import RotatingModel

    for status in (400, 401, 403, 404, 422):
        a = _RotatingStub("a", fail_status=status)
        b = _RotatingStub("b")
        _orig = _rotating_rng(monkeypatch, [0.1])  # A always picked first
        try:
            pool = RotatingModel([a, b], cooldown_s=300)
            with pytest.raises(requests.HTTPError):
                pool.complete(system="s", user="u")
        finally:
            _random.random = _orig
        assert b.calls == 0, f"no rotation on {status}"
        assert not pool._cooldowns, f"no cooldown written on {status}"


@pytest.mark.timeout(10)  # an unbounded-loop regression must fail fast, not hang CI
def test_402_single_provider_raises_loud(monkeypatch):
    """#1951 target 3: a 402 with NO alternative provider fails loudly —
    immediate raise, no cooldown written, no infinite rotation loop. The
    SECOND call must re-raise the HTTPError (the provider is tried again
    after the fail-fast), NOT the all-in-cooldown RuntimeError — pins the
    ``n == 1`` early raise (deleting the guard turns call 2 into
    ``RuntimeError`` and writes a cooldown on call 1)."""
    from tortoise.model_adapters import RotatingModel

    a = _RotatingStub("a", fail_status=402, fails=10)  # keeps 402ing across calls
    pool = RotatingModel([a], cooldown_s=300)
    with pytest.raises(requests.HTTPError):
        pool.complete(system="s", user="u")
    assert a.calls == 1, "single provider: one attempt, then raise — no retry loop"
    assert not pool._cooldowns, "fail-fast: no cooldown written on the immediate raise"
    with pytest.raises(requests.HTTPError):
        pool.complete(system="s", user="u")
    assert a.calls == 2, "a fresh call re-attempts the single provider (no stale cooldown)"


@pytest.mark.timeout(10)  # an unbounded-loop regression must fail fast, not hang CI
def test_402_all_providers_dead_raises_bounded(monkeypatch):
    """#1951 no-infinite-loop bound, n≥2: BOTH providers 402 → the bounded
    n*3 loop cooldowns each once, spends the rest of its attempts skipping
    cooldowned lanes, and re-raises the last 402 loudly. Total real
    attempts = 2 (≤ 6 bound) — no retry storm, no hang. A SECOND call with
    both lanes still cooldowned raises the all-in-cooldown RuntimeError
    (bounded, no hang) — the retry-continuity contract."""
    import random as _random

    from tortoise.model_adapters import RotatingModel

    a = _RotatingStub("a", fail_status=402)
    b = _RotatingStub("b", fail_status=402)
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])  # A(402)→B(402)→skips…
    try:
        pool = RotatingModel([a, b], cooldown_s=300)
        with pytest.raises(requests.HTTPError):
            pool.complete(system="s", user="u")
        with pytest.raises(RuntimeError, match="all 2 providers in cooldown"):
            pool.complete(system="s", user="u")
    finally:
        _random.random = _orig
    assert a.calls == 1, "each provider 402s at most once per call"
    assert b.calls == 1


def test_fatal_after_billing_rotation_preserves_cooldown(monkeypatch):
    """#1951 ordering invariant: once a 402 has cooldowned A and rotation
    lands on B, a FATAL error on B re-raises immediately — the fatal raise
    happens BEFORE any cooldown write, so B is not cooldowned, A's earlier
    cooldown is preserved, and only A's 402 is recorded (B's fatal adds no
    error entry). A regression that moved the cooldown write above the
    fatal check fails this."""
    import random as _random

    from tortoise.model_adapters import RotatingModel

    a = _RotatingStub("a", fail_status=402)
    b = _RotatingStub("b", fail_status=401)
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])  # A(402→cooldown), then B(401→fatal)
    try:
        pool = RotatingModel([a, b], cooldown_s=300)
        with pytest.raises(requests.HTTPError):
            pool.complete(system="s", user="u")
    finally:
        _random.random = _orig
    assert a.calls == 1 and b.calls == 1
    assert "a" in pool._cooldowns, "A's 402 cooldown survives the fatal raise"
    assert "b" not in pool._cooldowns, "fatal on B must not write a cooldown"
    assert len(pool.errors) == 1, "only A's 402 is recorded — fatal adds no entry"
    assert "a" in pool.errors[0]


def test_build_extractor_pool_survives_provider_402(monkeypatch):
    """#1951 integration: the production 3-provider pool (venice/openrouter/
    deepseek-direct) survives a 402 on one lane — the 402ing lane is
    cooldowned, a healthy lane answers, and the call RETURNS content. A
    SECOND call in the same run keeps serving the healthy lane (venice
    still in cooldown, never re-tried) — the run survives a mid-run 402.
    The pre-fix policy raised on the first 402 and killed the extraction
    run (reval3: 33/50 questions lost to fatal_402_billing)."""
    import random as _random

    from tortoise.model_adapters import RotatingModel

    venice_calls: list[str] = []

    class _Resp:
        def __init__(self, url):
            self._url = url

        def raise_for_status(self):
            if "venice" in self._url:
                venice_calls.append(self._url)
                raise _http_error(402)

        def json(self):
            return {"choices": [{"message": {"content": "ok-rotated"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def _fake_post(session, url, *args, **kwargs):
        return _Resp(url)

    monkeypatch.setattr(requests.sessions.Session, "post", _fake_post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("VENICE_API_KEY", "vz")
    pool = build_extractor_model("deepseek-flash-direct")
    assert isinstance(pool, RotatingModel)
    assert [p.provider for p in pool.providers] == [
        "venice", "openrouter", "deepseek-direct"]
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])
    try:
        # venice (weight 0.5) picked first → 402 → cooldown; the retry lands
        # on deepseek-direct (weights 0.5/0.35/0.15, r=0.9 → lane 2). The
        # second call's first pick is venice again → cooldown-skip → the
        # healthy lane answers: same-run continuity through the real pool.
        out1 = pool.complete(system="s", user="u")
        out2 = pool.complete(system="s", user="u")
    finally:
        _random.random = _orig
    assert out1 == out2 == "ok-rotated"
    assert "venice" in pool._cooldowns
    assert pool.route == "deepseek-direct"
    assert len(venice_calls) == 1, "venice 402s once, then stays cooldowned"
    assert [p.provider for p in pool.providers] == [
        "venice", "openrouter", "deepseek-direct"]  # pool membership unchanged


# ── #1987 Task 3: json_mode structural pin + build_reader_model ────────────

def _body_capture(monkeypatch):
    """Monkeypatch requests.post; returns (log, fake_post) capturing request
    bodies so tests can assert on response_format presence."""
    log = []

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }

    def _fake_post(self_or_url, *args, **kwargs):
        if args:  # noqa: SIM108
            url = args[0]
        else:
            url = self_or_url
        log.append((url, kwargs.get("json", {})))
        return _FakeResp()

    monkeypatch.setattr(requests.sessions.Session, "post", _fake_post)
    return log


_JSON_PROMPT = "Return a JSON object with the schedule."


def test_json_mode_pin_ask_lane_never_sends_response_format(monkeypatch):
    """(a) ask-lane model (json_mode=False) + prompt containing 'json' +
    TORTOISE_JSON_MODE=1 → NO response_format — the structural pin beats the
    content heuristic (the hazard class: retrieved memory mentioning 'json')."""
    monkeypatch.setenv("TORTOISE_JSON_MODE", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    log = _body_capture(monkeypatch)
    from tortoise.model_adapters import build_reader_model
    model = build_reader_model()
    model.complete(system=_JSON_PROMPT, user="memory mentions json parsing")
    bodies = [b for _, b in log]
    assert bodies, "a call must have been made"
    assert all("response_format" not in b for b in bodies)


def test_json_mode_pin_extraction_lane_unchanged(monkeypatch):
    """(b) extraction-lane model (default None) + same prompt →
    response_format present (default behavior unchanged)."""
    monkeypatch.setenv("TORTOISE_JSON_MODE", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    log = _body_capture(monkeypatch)
    model = build_extractor_model()
    model.complete(system=_JSON_PROMPT, user="extract JSON")
    bodies = [b for _, b in log]
    assert bodies
    assert any("response_format" in b for b in bodies)


def test_json_mode_true_always_sends(monkeypatch):
    """(c) json_mode=True → always sends when the prompt requests JSON
    (even with TORTOISE_JSON_MODE unset→default 1)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    log = _body_capture(monkeypatch)
    from tortoise.model_adapters import build_extractor_model
    model = build_extractor_model(json_mode=True)
    model.complete(system=_JSON_PROMPT, user="x")
    bodies = [b for _, b in log]
    assert bodies and all("response_format" in b for b in bodies)


def test_build_reader_model_resolves_env_and_reports_spec(monkeypatch):
    """(d) build_reader_model(model_id=None) resolves TORTOISE_ASK_MODEL
    (fallback deepseek/deepseek-v4-flash) and RoutingModel.model reports the
    resolved spec id."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    # env override — the RESOLVED SPEC post-normalization is the bare wire
    # id on the direct lane (deepseek-direct primary when both keys set and
    # TORTOISE_EXTRACTOR_PROVIDER unset): ``model`` reports the serving
    # lane's wire id, NOT the family-prefixed input.
    monkeypatch.setenv("TORTOISE_ASK_MODEL", "deepseek/deepseek-v4-pro")
    m = build_reader_model()
    assert isinstance(m, RoutingModel)
    assert m.primary.id == "deepseek-v4-pro"  # direct lane bare wire id
    assert m.model == "deepseek-v4-pro"
    # fallback default
    monkeypatch.delenv("TORTOISE_ASK_MODEL")
    m2 = build_reader_model()
    assert m2.primary.id == "deepseek-v4-flash"
    assert m2.model == "deepseek-v4-flash"


# ── #2069: provider-capability ask-lane routing ────────────────────────────


def test_reader_pool_deepseek_spec_deepseek_primary(monkeypatch):
    """(#2069) A deepseek-family spec (default lane) keeps the
    deepseek-direct primary with both keys — the (b) smoke regression pin."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model()
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "deepseek-direct"
    assert m.fallback is not None and m.fallback.provider == "openrouter"
    assert m.model == "deepseek-v4-flash"


def test_reader_pool_qwen_spec_openrouter_only(monkeypatch):
    """(#2069) A qwen-family spec builds an OpenRouter-ONLY pool — the
    deepseek-direct adapter is structurally absent (a deepseek-direct 400
    on the qwen spec becomes impossible), even with DEEPSEEK_API_KEY set."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("VENICE_API_KEY", "vz")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model("qwen/qwen3.8-max")
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "openrouter"
    assert m.fallback is None  # openrouter is the ONLY servable provider
    assert m.primary.id == "qwen/qwen3.8-max"
    assert m.model == "qwen/qwen3.8-max"


def test_reader_bare_qwen_registry_key_normalizes_to_openrouter(monkeypatch):
    """(#2069) A bare ``qwen3.8-max`` MODELS key (passed through unmapped by
    ``_REGISTRY_KEY_TO_ID``) normalizes via ``_ASK_MODELS_KEY_SPECS`` to
    ``qwen/qwen3.8-max`` BEFORE the family parse — the pool is
    OpenRouter-only, NEVER deepseek-direct."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model("qwen3.8-max")
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "openrouter"
    assert m.fallback is None
    assert m.primary.id == "qwen/qwen3.8-max"
    assert m.model == "qwen/qwen3.8-max"


def test_reader_bare_upstage_and_anthropic_keys_normalize(monkeypatch):
    """(#2069) The other bare non-deepseek MODELS keys normalize to their
    family-prefixed specs (OpenRouter-only pools)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    for bare, prefixed in [("solar-pro4", "upstage/solar-pro4"),
                           ("claude-opus-5", "anthropic/claude-opus-5")]:
        m = build_reader_model(bare)
        assert isinstance(m, RoutingModel)
        assert m.primary.provider == "openrouter"
        assert m.primary.id == prefixed
        assert m.model == prefixed


def test_reader_unknown_bare_non_deepseek_key_fails_loud(monkeypatch):
    """(#2069) A bare non-deepseek id absent from ``_ASK_MODELS_KEY_SPECS``
    fails LOUD naming the family-prefixed form — never silently routed to
    deepseek-direct."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match="family-prefixed form"):
        build_reader_model("some-fancy-model")


def test_reader_unknown_family_prefix_fails_loud(monkeypatch):
    """(#2069) An unknown family prefix (a typo'd family, a future family
    not yet mapped) → ValueError naming the family — NEVER "→ all" (it
    must not fall through to deepseek-direct and re-introduce the defect)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match="unknown model family 'mistral'"):
        build_reader_model("mistral/mistral-large")


def test_reader_colon_form_rejected(monkeypatch):
    """(#2069) The eval lane's ``provider:model`` colon form (the documented
    ``TORTOISE_LME_READER_MODEL`` value) is REJECTED on the ask lane with a
    ValueError pointing at the family-prefixed form — it must not fall into
    unknown-prefix handling (openrouter:qwen → deepseek 400 → 502)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match=r"family-prefixed.*product form"):
        build_reader_model("openrouter:qwen/qwen3.8-max")


def test_reader_registry_deepseek_key_backcompat(monkeypatch):
    """(#2069) ``deepseek-flash-direct`` (the eval harness registry key)
    normalizes to the deepseek family — back-compat preserved."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model("deepseek-flash-direct")
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "deepseek-direct"
    assert m.primary.id == "deepseek-v4-flash"


def test_reader_empty_intersection_fails_loud_naming_key(monkeypatch):
    """(#2069) A qwen spec with NO OPENROUTER_API_KEY in auto mode →
    build-time ValueError naming the missing key (fail-fast, NOT a silent
    misbuild that 401s at call time)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")  # wrong key present
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_reader_model("qwen/qwen3.8-max")
    # bare qwen registry key takes the same path
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_reader_model("qwen3.8-max")


def test_reader_auto_no_keys_deepseek_lenient_openrouter(monkeypatch):
    """(#2069) The deepseek family preserves the extraction lane's lenient
    no-key default — a single OpenRouter adapter (no-key ask behavior
    unchanged)."""
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model()
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "openrouter"
    assert m.fallback is None
    assert m.primary.id == "deepseek/deepseek-v4-flash"


def test_ask_provider_openrouter_forces_deepseek_spec(monkeypatch):
    """(#2069) ``TORTOISE_ASK_PROVIDER=openrouter`` forces the OpenRouter
    primary even for deepseek-family specs."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_ASK_PROVIDER", "openrouter")
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model()
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "openrouter"
    assert m.fallback is not None and m.fallback.provider == "deepseek-direct"
    assert m.primary.id == "deepseek/deepseek-v4-flash"


def test_ask_provider_explicit_without_key_fails_closed(monkeypatch):
    """(#2069) Explicit TORTOISE_ASK_PROVIDER whose key is absent →
    ValueError naming the key (fail-closed, mirror
    resolve_extractor_provider)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_ASK_PROVIDER", "openrouter")
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_reader_model()


def test_ask_provider_invalid_value_lists_valid(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_ASK_PROVIDER", "anthropic")
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match="auto \\| deepseek-direct \\| openrouter \\| venice"):
        build_reader_model()


def test_ask_provider_capability_mismatch_fails_loud(monkeypatch):
    """(#2069) Explicit ``TORTOISE_ASK_PROVIDER=venice`` with a qwen spec —
    venice cannot serve the qwen family → empty-intersection ValueError
    (never a silent pool build)."""
    monkeypatch.setenv("VENICE_API_KEY", "vz")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_ASK_PROVIDER", "venice")
    from tortoise.model_adapters import build_reader_model
    with pytest.raises(ValueError, match=r"cannot serve 'qwen/qwen3.8-max'"):
        build_reader_model("qwen/qwen3.8-max")


def test_reader_qwen_request_hits_openrouter_wire(monkeypatch):
    """(#2069 real-factory fake-transport) TORTOISE_ASK_MODEL=qwen/
    qwen3.8-max → the request hits the OpenRouter base_url with
    ``model: qwen/qwen3.8-max``; exactly ONE complete(); the response
    ``model/provider/route`` report the openrouter lane."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_ASK_MODEL", "qwen/qwen3.8-max")
    log = _body_capture(monkeypatch)
    from tortoise.model_adapters import RoutingModel, build_reader_model
    m = build_reader_model()
    assert isinstance(m, RoutingModel)
    out = m.complete(system="s", user="u")
    assert out == "ok"
    assert len(log) == 1  # exactly one complete() — no dead primary call
    url, body = log[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "qwen/qwen3.8-max"
    assert "response_format" not in body  # json_mode=False structural pin
    assert m.provider == "openrouter"
    assert m.route == "openrouter"
    assert m.model == "qwen/qwen3.8-max"


def test_extractor_lane_unchanged(monkeypatch):
    """(#2069) build_extractor_model is byte-identical: default path keeps
    the deepseek-direct primary with both keys; no-keys still degrades to a
    single OpenRouter adapter."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    from tortoise.model_adapters import RoutingModel, build_extractor_model
    m = build_extractor_model()
    assert isinstance(m, RoutingModel)
    assert m.primary.provider == "deepseek-direct"
    assert m.fallback is not None and m.fallback.provider == "openrouter"
    # no keys → lenient single OpenRouter adapter (unchanged D3 default)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    m2 = build_extractor_model()
    assert m2.primary.provider == "openrouter"
    assert m2.fallback is None


class _TokensAdapter(_StubAdapter):
    """Stub adapter carrying the per-call usage + id attributes the wrapper
    forwards (mirrors the real adapters' complete()-written fields)."""

    def __init__(self, provider: str, wire_id: str):
        super().__init__(provider)
        self.id = wire_id
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    def complete(self, *, system, user, max_tokens: int | None = None):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        self.last_prompt_tokens = self.calls * 10
        self.last_completion_tokens = self.calls * 2
        return f"{self.provider}:ok"


def test_wrapper_forwards_usage_and_model(monkeypatch):
    """(e) BOTH RoutingModel and RotatingModel surface the serving adapter's
    per-call last_completion_tokens/last_prompt_tokens via forwards and
    model reports the RESOLVED SPEC id (not a raw serving-adapter identity)."""
    from tortoise.model_adapters import RotatingModel, RoutingModel
    # RoutingModel
    primary = _TokensAdapter("deepseek-direct", "deepseek-v4-flash")
    fallback = _TokensAdapter("openrouter", "deepseek/deepseek-v4-flash")
    m = RoutingModel(primary, fallback)
    m.complete(system="s", user="u")
    assert m.last_prompt_tokens == 10
    assert m.last_completion_tokens == 2
    assert m.model == "deepseek-v4-flash"
    m.complete(system="s", user="u")
    assert m.last_prompt_tokens == 20
    assert m.last_completion_tokens == 4
    # RotatingModel — fresh adapters with DISTINCT per-call token values so
    # the forward is assertable regardless of which lane the weighted
    # round-robin picks.
    rp = _TokensAdapter("deepseek-direct", "deepseek-v4-flash")
    rp.last_prompt_tokens = 0
    rf = _TokensAdapter("openrouter", "deepseek/deepseek-v4-flash")
    rf.last_prompt_tokens = 0
    r = RotatingModel([rp, rf])
    r.complete(system="s", user="u")
    assert r.last_prompt_tokens == 10 and r.last_completion_tokens == 2
    assert r.model in ("deepseek-v4-flash", "deepseek/deepseek-v4-flash")
    # the forward matches the serving lane's id (the resolved spec)
    serving = rp if rp.calls else rf
    assert r.model == serving.id


def _http_err(status: int):
    err = requests.HTTPError(f"HTTP {status}")
    err.response = type("R", (), {"status_code": status})()
    return err


def test_failover_policy_pin(monkeypatch):
    """(f) 402 on RoutingModel → re-raised (fatal, no failover); 402 on
    RotatingModel with >=2 providers → rotates; 429 on RoutingModel WITH a
    fallback → FAILS OVER (transient); 429 without fallback → re-raised."""
    from tortoise.model_adapters import RotatingModel, RoutingModel
    # 402 fatal on RoutingModel
    primary = _StubAdapter("deepseek-direct").fail_with(_http_err(402))
    m = RoutingModel(primary, _StubAdapter("openrouter"))
    with pytest.raises(requests.HTTPError):
        m.complete(system="s", user="u")
    # 402 on RotatingModel n>=2 rotates: the forced RNG picks venice first
    # (its 402 is consumed exactly once), cooldown 60s skips it on the retry
    # so the fallback answers deterministically.
    p1 = _StubAdapter("venice").fail_with(_http_err(402))
    p2 = _StubAdapter("openrouter")
    rm = RotatingModel([p1, p2], cooldown_s=60)
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])  # venice → openrouter
    try:
        assert rm.complete(system="s", user="u") == "openrouter:s:u"
    finally:
        _orig = _rotating_rng(monkeypatch, [])
        import random as _r
        _r.random = _orig
    assert p1.provider in rm._cooldowns
    assert rm.route == "openrouter"
    # 429 with fallback → failover
    p3 = _StubAdapter("deepseek-direct").fail_with(_http_err(429))
    m2 = RoutingModel(p3, _StubAdapter("openrouter"), cooldown_s=0)
    out = m2.complete(system="s", user="u")
    assert out.startswith("openrouter:")
    assert m2.route == "openrouter"
    # 429 without fallback → re-raised (surfaces as a reader failure → 502)
    p4 = _StubAdapter("openrouter").fail_with(_http_err(429))
    m3 = RoutingModel(p4, None, cooldown_s=0)
    with pytest.raises(requests.HTTPError):
        m3.complete(system="s", user="u")


def test_concurrent_rotation_state(monkeypatch):
    """(g) N threads calling complete() on a shared RotatingModel where the
    first K calls to one provider raise 402 → every call succeeds, the 402
    count is exactly K, and no call is lost (rotation handles the race)."""
    import threading

    from tortoise.model_adapters import RotatingModel

    class _BillingOnce(_TokensAdapter):
        def __init__(self, provider: str, wire_id: str, k: int):
            super().__init__(provider, wire_id)
            self.remaining = k
            self._lock = threading.Lock()

        def complete(self, *, system, user, max_tokens: int | None = None):
            with self._lock:
                if self.remaining > 0:
                    self.remaining -= 1
                    raise _http_err(402)
            self.calls += 1
            return "ok"

    p1 = _BillingOnce("venice", "deepseek-v4-flash", k=3)
    p2 = _TokensAdapter("openrouter", "deepseek/deepseek-v4-flash")
    rm = RotatingModel([p1, p2], cooldown_s=0)
    barrier = threading.Barrier(6)
    results: list[str] = []

    def _worker():
        barrier.wait()
        try:
            results.append(rm.complete(system="s", user="u"))
        except Exception as e:
            results.append(f"ERR:{e}")

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 6
    assert all(r == "ok" or r.endswith(":ok") for r in results), results
    # the 3 scripted 402s were consumed exactly once each (no skipped or
    # repeated provider state) and every thread got an answer
    assert p1.calls + p2.calls == 6, (p1.calls, p2.calls)


def test_rotation_recovery(monkeypatch):
    """(h) rotation-recovery: a provider fails → rotates → the NEXT ask
    succeeds (eventually on the primary again when it recovers)."""
    from tortoise.model_adapters import RotatingModel

    class _FailingOnce(_TokensAdapter):
        def __init__(self, provider: str, wire_id: str):
            super().__init__(provider, wire_id)
            self.failed = False

        def complete(self, *, system, user, max_tokens: int | None = None):
            if not self.failed:
                self.failed = True
                raise _http_err(402)
            return super().complete(system=system, user=user)

    p1 = _FailingOnce("venice", "deepseek-v4-flash")
    p2 = _TokensAdapter("openrouter", "deepseek/deepseek-v4-flash")
    rm = RotatingModel([p1, p2], cooldown_s=60)
    _orig = _rotating_rng(monkeypatch, [0.1, 0.9])
    try:
        out1 = rm.complete(system="s", user="u")
        # venice 402s once → cooldown 60s → openrouter answers.
        assert out1 == "openrouter:ok", out1
        assert p1.provider in rm._cooldowns
        # subsequent call: venice still cooldowned → fallback again (the
        # rotation window keeps the run moving while the lane recovers)
        out2 = rm.complete(system="s", user="u")
        assert out2 == "openrouter:ok", out2
        assert rm.last_prompt_tokens > 0  # usage forward read after recovery
    finally:
        import random as _r
        _r.random = _orig



# ── #2339: external deadline-abort → note_stall failover ────────────────
# The extractor's per-call deadline kills a hung read in a worker thread and
# raises TimeoutError OUTSIDE the wrapper's complete() — RoutingModel never
# sees an exception, so the normal in-complete failover never fired and every
# retry re-hit the stalled primary (whole sessions died empty). note_stall()
# is the wrapper-side signal the deadline path fires: cooldown + interrupt
# ONLY the stalled provider so the next complete() routes to the fallback.

class _ClosingStub:
    """_StubAdapter + a close() recorder (deadline interrupt semantics)."""
    def __init__(self, provider: str):
        self.provider = provider
        self.calls = 0
        self.close_calls = 0

    def complete(self, *, system, user, max_tokens: int | None = None):
        self.calls += 1
        return f"{self.provider}:{system}:{user}"

    def close(self):
        self.close_calls += 1


def test_deadline_stall_routes_next_call_to_fallback():
    """After an external note_stall on the primary, the NEXT complete() runs
    on the fallback and stays there (D5 forward-only) — the healthy provider
    carries the session."""
    _reset_failover_cooldown()
    primary = _ClosingStub("deepseek-direct")
    fallback = _ClosingStub("openrouter")
    model = RoutingModel(primary, fallback, cooldown_s=300)
    assert model.complete(system="s", user="u").startswith("deepseek-direct:")
    assert primary.calls == 1 and fallback.calls == 0
    # The extractor's deadline path calls note_stall() (fire-and-forget).
    model.note_stall()
    assert primary.close_calls == 1      # only the stalled provider interrupted
    assert fallback.close_calls == 0
    assert model.errors and "stalled" in model.errors[0]
    out = model.complete(system="s", user="u")
    assert out.startswith("openrouter:"), out
    assert primary.calls == 1            # primary never re-hit
    assert fallback.calls == 1
    assert model.route == "openrouter" and model.failover_used is True
    # Sticky: a later call still avoids the stalled primary.
    out2 = model.complete(system="s2", user="u2")
    assert out2.startswith("openrouter:")
    assert primary.calls == 1


def test_note_stall_no_fallback_keeps_primary():
    """Without a fallback, note_stall() must not raise and the primary is
    still retried (the pre-#2339 behavior for single-provider pools)."""
    _reset_failover_cooldown()
    primary = _ClosingStub("openrouter")
    model = RoutingModel(primary, fallback=None, cooldown_s=300)
    model.note_stall()  # must not raise
    assert model.complete(system="s", user="u").startswith("openrouter:")
    assert primary.calls == 1


def test_note_stall_targets_named_provider():
    """note_stall(provider=...) cools the NAMED adapter, not the primary —
    AND a fallback-targeted stall must NOT flip sticky failover (that would
    lock the session onto the wedged fallback forward-only)."""
    _reset_failover_cooldown()
    primary = _ClosingStub("deepseek-direct")
    fallback = _ClosingStub("openrouter")
    model = RoutingModel(primary, fallback, cooldown_s=300)
    # Fallback stall (post-failover edge): named note cools the fallback only.
    model.note_stall(provider="openrouter")
    assert fallback.close_calls == 1
    assert primary.close_calls == 0
    assert model._failed_over is False, "fallback stall must not flip failover"
    out = model.complete(system="s", user="u")  # primary still tried first
    assert out.startswith("deepseek-direct:"), out
    assert primary.calls == 1


def test_rotating_note_stall_cooldowns_active_provider(monkeypatch):
    """RotatingModel.note_stall() cools the WEDGED provider (the one
    in-flight when a deadline kills the worker) — never the stale ``route``
    from the last success. Scenario: b serves a call fine (route=b); the
    NEXT pick lands on a, which wedges; note_stall must cool a, not b."""
    import random as _random
    import threading
    import time as _time

    wedge = threading.Event()
    entered = threading.Event()

    class _Wedge:
        provider = "deepseek-direct"
        calls = 0
        close_calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            entered.set()      # deterministic: we are INSIDE the call now
            wedge.wait(5.0)    # wedged until released (deadline would kill us)
            return "deepseek-direct:late"

        def close(self):
            self.close_calls += 1

    a = _Wedge()
    b = _ClosingStub("openrouter")
    model = RotatingModel([a, b], cooldown_s=300)
    # 1) b serves a call fine (route=b, b is the last SUCCESS).
    monkeypatch.setattr(_random, "random", lambda: 0.9)  # picks b (idx 1)
    assert model.complete(system="s", user="u").startswith("openrouter:")
    assert model.route == "openrouter"
    # 2) next pick lands on a (idx 0), which wedges in a worker thread.
    monkeypatch.setattr(_random, "random", lambda: 0.1)  # picks a (idx 0)
    box = {}
    def _run():
        try:
            box["out"] = model.complete(system="s", user="u")
        except Exception as e:
            box["exc"] = e
    t = threading.Thread(target=_run)
    t.start()
    assert entered.wait(5.0), "worker must enter a.complete"
    _time.sleep(0.05)  # let the wrapper set _in_flight before note_stall
    assert model._in_flight is a, "a must be the in-flight adapter"
    # 3) the extractor's deadline path fires note_stall() from the caller
    #    thread while a is wedged.
    model.note_stall()
    assert a.close_calls == 1            # the WEDGED adapter interrupted
    assert b.close_calls == 0            # the healthy last-success untouched
    assert model._cooldowns.get("deepseek-direct", 0) > _time.time()
    assert "deepseek-direct" not in model._cooldowns or (
        model._cooldowns.get("openrouter", 0) <= _time.time())
    # 4) release the wedge; the in-flight call finishes late (returns a's
    #    content — the kill happened at the wrapper boundary in production).
    wedge.set()
    t.join(timeout=5)
    # 5) subsequent calls SKIP the cooled a (cooldown is unconditional) and
    #    rotate onto b: alternate picks — 0.1 selects a first (cooled →
    #    skipped → loop continues), then 0.9 selects b (serves).
    seq = iter([0.1, 0.9, 0.1, 0.9])
    monkeypatch.setattr(_random, "random", lambda: next(seq))
    out = model.complete(system="s", user="u")
    assert out.startswith("openrouter:"), out
    assert a.calls <= 1, "cooled provider must not be re-called after the stall"
