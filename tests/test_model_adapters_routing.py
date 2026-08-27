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
              "OPENROUTER_API_KEY", "TORTOISE_EXTRACT_MODEL",
              "TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN"):
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
    # Pilot #1549: the direct route must send the NON-reasoning id —
    # 'deepseek-v4-flash' reasons by default and collapses to empty output.
    assert body["model"] == "deepseek-chat"
    assert body["max_tokens"] == 4000


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


def test_deepseek_direct_json_mode_disabled(monkeypatch):
    """#1746 (D6): TORTOISE_JSON_MODE=0 omits ``response_format`` entirely
    (the documented escape hatch)."""
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_JSON_MODE", "0")
    model = build_extractor_model("deepseek/deepseek-v4-flash")
    model.complete(system="s", user="u")
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
    # Pilot #1549 run (2026-08-25): the direct-API flash id is the NON-reasoning
    # 'deepseek-chat' — api.deepseek.com's 'deepseek-v4-flash' reasons by
    # default and collapses to empty output on non-trivial S1 prompts
    # (1500/1500 reasoning tokens, finish=length, zero content).
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")  # the pilot's direct route
    m = build_extractor_model("deepseek-flash-direct")
    # family-prefixed key → direct route strips → non-reasoning id on the wire
    assert m.primary.id == "deepseek-chat"
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
    """Pilot #1549 (P1): the DEFAULT production path — TORTOISE_EXTRACT_MODEL
    unset → 'deepseek/deepseek-v4-flash' — must not collapse. The direct route
    sends the non-reasoning 'deepseek-chat' on the wire (sdk._model_adapter and
    the eval CLI both land here)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = build_extractor_model()
    assert model.provider == "deepseek-direct"
    assert model.primary.id == "deepseek-chat"


def test_three_provider_rotation_pool(monkeypatch):
    """Pilot #1549: with a Venice key, build_extractor_model returns the
    RotatingModel pool [deepseek-direct, openrouter, venice]. Each lane gets a
    VALID wire id for its provider: venice serves its documented catalog id
    (chat unverified there), openrouter needs the family-prefixed id, the
    direct lane sends the non-reasoning 'deepseek-chat' (#1549 collapse fix)."""
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
        "venice": "deepseek-v4-flash",       # documented venice catalog id
        "openrouter": "deepseek/deepseek-chat",  # valid family-prefixed id
        "deepseek-direct": "deepseek-chat",      # non-reasoning direct id (#1549)
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
