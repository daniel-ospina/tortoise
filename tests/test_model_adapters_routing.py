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
    assert resolve_extractor_provider() == ("deepseek-direct", "openrouter")


def test_resolve_openrouter_with_both_keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    assert resolve_extractor_provider() == ("openrouter", "deepseek-direct")


def test_resolve_deepseek_direct_without_fallback(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "deepseek-direct")
    assert resolve_extractor_provider() == ("deepseek-direct", None)


def test_resolve_openrouter_without_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    assert resolve_extractor_provider() == ("openrouter", None)


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
    assert resolve_extractor_provider() == ("deepseek-direct", "openrouter")


def test_resolve_unset_deepseek_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    assert resolve_extractor_provider() == ("deepseek-direct", None)


def test_resolve_unset_openrouter_only(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert resolve_extractor_provider() == ("openrouter", None)


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

    def _fake_post(url, **kwargs):
        log.append((url, kwargs.get("json", {})))
        return _FakeResp()

    return log, _fake_post


def test_direct_route_sends_bare_model_id(monkeypatch):
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    model = build_extractor_model("deepseek/deepseek-v4-flash",
                                  max_tokens=4000, temperature=0.0)
    out = model.complete(system="s", user="u")
    assert out == "ok"
    url, body = log[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert body["model"] == "deepseek-v4-flash"          # bare id on direct
    assert body["max_tokens"] == 4000


def test_openrouter_route_sends_family_prefixed_model_id(monkeypatch):
    log, fake = _fake_post_logger()
    monkeypatch.setattr(requests, "post", fake)
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
    monkeypatch.setattr(requests, "post", fake)
    model = build_extractor_model()
    assert model.provider == "openrouter"
    assert model.fallback is None
    model.complete(system="s", user="u")
    assert log[0][0] == "https://openrouter.ai/api/v1/chat/completions"


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


def test_registry_key_normalized_to_real_model_id():
    """Pilot #1549: the eval CLI passes MODELS registry keys ('deepseek-flash-direct')
    to build_extractor_model; they must normalize to the real API model id or the
    suffix reaches DeepSeek as the model name (HTTP 400 on every S1 call)."""
    m = build_extractor_model("deepseek-flash-direct")
    assert m.primary.id == "deepseek-v4-flash"  # the API-facing id is what matters
    m2 = build_extractor_model("deepseek-v4-pro-direct")
    assert m2.primary.id == "deepseek-v4-pro"
    # raw specs pass through untouched
    m3 = build_extractor_model("deepseek/deepseek-v4-flash")
    assert m3.primary.id == "deepseek/deepseek-v4-flash"
