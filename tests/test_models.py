"""Model backend tests — build_request, parse_response, _headers, __init__,
and the #4129 served-model-substitution report.

No network calls (the one transport test drives a mock urlopen).
.venv/bin/python tests/test_models.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.models import OpenAICompatModel, OllamaModel  # noqa: E402, I001, RUF100
from tortoise.model_adapters import (
    DeepSeekDirectModel,
    OpenRouterModel,
)

# ---------------------------------------------------------------------------
# OpenAICompatModel
# ---------------------------------------------------------------------------


def test_openai_init_defaults():
    m = OpenAICompatModel(id="deepseek-chat", base_url="https://api.deepseek.com")
    assert m.id == "deepseek-chat"
    assert m.base_url == "https://api.deepseek.com"
    assert m.api_key_env == "OPENAI_API_KEY"
    assert m.temperature == 0.0
    assert m.timeout == 60
    print("PASS test_openai_init_defaults")


def test_openai_init_custom():
    m = OpenAICompatModel(id="gemini-flash", base_url="https://generativelanguage.googleapis.com/v1beta/",
                          api_key_env="GEMINI_API_KEY", temperature=0.7, timeout=120)
    assert m.id == "gemini-flash"
    assert m.api_key_env == "GEMINI_API_KEY"
    assert m.temperature == 0.7
    assert m.timeout == 120
    print("PASS test_openai_init_custom")


def test_openai_base_url_strips_trailing_slash():
    m = OpenAICompatModel(id="x", base_url="http://localhost:8080/v1//")
    assert m.base_url == "http://localhost:8080/v1"
    print("PASS test_openai_base_url_strips_trailing_slash")


def test_openai_build_request():
    m = OpenAICompatModel(id="deepseek-chat", base_url="https://api.deepseek.com",
                          temperature=0.3)
    req = m.build_request(system="You are helpful.", user="Hello!")
    assert req["model"] == "deepseek-chat"
    assert req["temperature"] == 0.3
    assert req["response_format"] == {"type": "json_object"}
    msgs = req["messages"]
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "You are helpful."}
    assert msgs[1] == {"role": "user", "content": "Hello!"}
    print("PASS test_openai_build_request")


def test_openai_parse_response():
    data = {"choices": [{"message": {"content": "{\"x\": 1}"}}]}
    assert OpenAICompatModel.parse_response(data) == "{\"x\": 1}"
    print("PASS test_openai_parse_response")


# ---------------------------------------------------------------------------
# #4129 — a provider accepts a RETIRED model id and silently serves another, so
# the configured model is not the model used. The guard reports it; the tests
# below pin both the policy and the WIRING (a guard nobody calls is no guard).
# ---------------------------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _capture_substitution_warnings():
    from tortoise import models as _m

    handler = _Collect()
    _m._logger.addHandler(handler)
    previous_level = _m._logger.level
    _m._logger.setLevel(logging.WARNING)
    _m._substituted_models.clear()
    return _m, handler, previous_level


def _release_substitution_warnings(_m, handler, previous_level):
    """Restore BOTH the handler and the level — this file is imported into a
    shared pytest process, so a level left at WARNING would silence the
    `tortoise.models` logger for every later test in the same run."""
    _m._logger.removeHandler(handler)
    _m._logger.setLevel(previous_level)
    _m._substituted_models.clear()


def test_substitution_capture_helpers_restore_logger_state():
    """The capture helpers must leave the shared `tortoise.models` logger
    exactly as they found it. This file is imported into a shared pytest
    process, so a level left at WARNING would silence that logger for every
    later test in the same run.

    The baseline is set EXPLICITLY rather than read from the logger: this file
    also runs standalone (`_run_all` sorts by name, so the transport test above
    runs first), and a polluted baseline would make this test pass without
    anything being restored."""
    from tortoise import models as _m

    original = _m._logger.level
    baseline = logging.INFO  # distinguishable from the capture's WARNING
    _m._logger.setLevel(baseline)
    try:
        before_handlers = list(_m._logger.handlers)
        captured, handler, previous = _capture_substitution_warnings()
        assert previous == baseline, (previous, baseline)
        assert _m._logger.level == logging.WARNING  # the capture is active
        _release_substitution_warnings(captured, handler, previous)
        assert _m._logger.level == baseline, (_m._logger.level, baseline)
        assert _m._logger.handlers == before_handlers
    finally:
        _m._logger.setLevel(original)
    print("PASS test_substitution_capture_helpers_restore_logger_state")


def test_model_substitution_warns_once_and_never_raises():
    """A divergent served id warns exactly once per pair; every non-divergent
    shape (agreement, None, empty, non-str) is silent. Never raises — a provider
    that legitimately normalizes an alias must not fail a capture."""
    _m, handler, previous_level = _capture_substitution_warnings()
    try:
        _m._warn_on_model_substitution("deepseek-chat", "deepseek-flash")
        assert len(handler.messages) == 1, handler.messages
        assert "deepseek-flash" in handler.messages[0]
        assert "deepseek-chat" in handler.messages[0]

        # The SAME pair again is silent: a long capture path calls this per
        # request, and a per-call warning would be its own defect.
        _m._warn_on_model_substitution("deepseek-chat", "deepseek-flash")
        assert len(handler.messages) == 1, handler.messages

        for served in ("deepseek-flash", None, "", 7, {}):
            _m._warn_on_model_substitution("deepseek-flash", served)
        assert len(handler.messages) == 1, handler.messages

        # A DIFFERENT pair is a different finding and must still be reported.
        _m._warn_on_model_substitution("gpt-4o-mini", "gpt-4o")
        assert len(handler.messages) == 2, handler.messages

        # ``requested`` is documented as a str; a non-hashable value must not
        # raise before the early return (the once-per-pair set would).
        _m._warn_on_model_substitution([], "deepseek-flash")
        _m._warn_on_model_substitution({"a": 1}, "deepseek-flash")
        assert len(handler.messages) == 2, handler.messages
    finally:
        _release_substitution_warnings(_m, handler, previous_level)
    print("PASS test_model_substitution_warns_once_and_never_raises")


def test_complete_reports_a_substituted_model():
    """Wiring: `complete()` must actually inspect the response's `model`, not
    merely have a guard available. Drives a mock transport (no network) and
    asserts the substituted served id is reported."""
    _m, handler, previous_level = _capture_substitution_warnings()
    old_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-test-4129"
    body = json.dumps({
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "{}"}}],
    }).encode()
    resp = mock.MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = body
    try:
        with mock.patch.object(_m.urllib.request, "urlopen", return_value=resp):
            content = OpenAICompatModel(
                id="deepseek-chat",
                base_url="https://api.example.com/v1",
            ).complete(system="s", user="u")
        assert content == "{}"
        assert any("deepseek-flash" in s for s in handler.messages), handler.messages
    finally:
        _release_substitution_warnings(_m, handler, previous_level)
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key
    print("PASS test_complete_reports_a_substituted_model")


def test_openrouter_complete_reports_a_substituted_model():
    """#4129 wiring, SECOND lane: ``OpenRouterModel`` has its OWN ``complete``
    body, so the guard on ``OpenAICompatModel.complete`` never ran for it.
    Drives a mock session (no network) and asserts the substitution is
    reported — the assertion fails if the wiring is removed, which is the
    whole point (the guard existing somewhere is not the behaviour)."""
    _m, handler, previous_level = _capture_substitution_warnings()
    resp = mock.MagicMock()
    resp.json.return_value = {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {},
    }
    adapter = OpenRouterModel("deepseek-chat")
    try:
        with mock.patch.object(adapter._session, "post", return_value=resp):
            content = adapter.complete(system="s", user="u")
        assert content == "{}"
        assert any("deepseek-flash" in s for s in handler.messages), handler.messages
    finally:
        _release_substitution_warnings(_m, handler, previous_level)
    print("PASS test_openrouter_complete_reports_a_substituted_model")


def test_deepseek_direct_complete_reports_a_substituted_model():
    """#4129: the direct route's own ``complete`` body bypasses BOTH the
    OpenAI-compat and the OpenRouter paths, so it must observe the served id
    itself. A subtest of the fix above would not catch its regression."""
    _m, handler, previous_level = _capture_substitution_warnings()
    resp = mock.MagicMock()
    resp.json.return_value = {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {},
    }
    adapter = DeepSeekDirectModel("deepseek-chat")
    try:
        with mock.patch.object(adapter._session, "post", return_value=resp):
            content = adapter.complete(system="s", user="u")
        assert content == "{}"
        assert any("deepseek-flash" in s for s in handler.messages), handler.messages
    finally:
        _release_substitution_warnings(_m, handler, previous_level)
    print("PASS test_deepseek_direct_complete_reports_a_substituted_model")


def test_session_llm_deepseek_default_is_not_the_retired_id():
    """#4129 acceptance criterion 1 — the repair is protected ONLY by this
    test. The provider ACCEPTS the retired id and silently serves a different
    model, so no runtime check downstream of the response can tell that the
    default went stale again; a "tidy" revert would otherwise pass CI. The
    expected value was verified against GET /models on 2026-10-05, which
    serves exactly ["deepseek-flash", "deepseek-v4-pro"]."""
    from tortoise.sdk import _SESSION_LLM_DEFAULT_MODELS as defaults

    assert defaults["deepseek"] == "deepseek-flash"
    assert "chat" not in defaults["deepseek"]
    print("PASS test_session_llm_deepseek_default_is_not_the_retired_id")


def test_a_raising_log_handler_cannot_fail_a_capture():
    """An observer must never flip a call outcome — `_emit_usage_sink` follows
    that rule, and this report is the same kind of observer. `_logger.warning`
    is therefore suppressed: a handler that raises must not escape `complete()`
    (which would fail the capture AND skip the usage sink)."""
    class _Boom(logging.Handler):
        def emit(self, record):
            raise RuntimeError("handler boom")

    _m, handler, previous_level = _capture_substitution_warnings()
    boom = _Boom()
    _m._logger.addHandler(boom)
    old_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-test-4129"
    body = json.dumps({
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "{}"}}],
    }).encode()
    resp = mock.MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = body
    try:
        with mock.patch.object(_m.urllib.request, "urlopen", return_value=resp):
            content = OpenAICompatModel(
                id="deepseek-chat",
                base_url="https://api.example.com/v1",
            ).complete(system="s", user="u")
        assert handler.messages, "the guard did not fire — test is vacuous"
        assert content == "{}"
    finally:
        _m._logger.removeHandler(boom)
        _release_substitution_warnings(_m, handler, previous_level)
        if old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_key
    print("PASS test_a_raising_log_handler_cannot_fail_a_capture")


def test_the_substitution_memo_cannot_grow_without_bound():
    """The once-per-pair dedup assumes a STABLE served id, but this guard exists
    because the provider may misbehave — a provider returning a new id per
    response would otherwise grow the memo without bound and defeat the dedup.
    The cap must keep the polarity safe: a repeated warning is acceptable, a
    missed one is not."""
    from tortoise import models as _m

    previous = set(_m._substituted_models)
    try:
        _m._substituted_models.clear()
        for i in range(_m._SUBSTITUTED_MEMO_CAP * 3 + 7):
            _m._warn_on_model_substitution("deepseek-chat", f"served-{i}")
        assert len(_m._substituted_models) <= _m._SUBSTITUTED_MEMO_CAP, len(_m._substituted_models)
        # The cap must not silence the guard altogether.
        _m._substituted_models.clear()
        _m._warn_on_model_substitution("deepseek-chat", "deepseek-flash")
        assert _m._substituted_models, "the guard went silent at the cap"
    finally:
        _m._substituted_models.clear()
        _m._substituted_models.update(previous)
    print("PASS test_the_substitution_memo_cannot_grow_without_bound")


def test_a_hostile_str_subclass_cannot_raise_through_the_guard():
    """The contract is NEVER RAISES, so the predicate accepts only an exact
    `str` on each side — a subclass with a hostile `__eq__`/`__hash__` would
    otherwise raise from the comparison or the memo, inside every `complete()`."""
    _m, handler, previous_level = _capture_substitution_warnings()
    try:
        class _Hostile(str):
            def __eq__(self, other):
                raise RuntimeError("hostile __eq__")

            def __hash__(self):
                raise RuntimeError("hostile __hash__")

        _m._warn_on_model_substitution(_Hostile("deepseek-chat"), "deepseek-flash")
        _m._warn_on_model_substitution("deepseek-chat", _Hostile("deepseek-flash"))
        assert handler.messages == [], handler.messages
    finally:
        _release_substitution_warnings(_m, handler, previous_level)
    print("PASS test_a_hostile_str_subclass_cannot_raise_through_the_guard")


def test_the_substitution_message_is_true_for_a_dated_pin():
    """A provider pinning an alias to a dated build is NOT substituting a
    different model — and `gpt-4o-mini` -> `gpt-4o-mini-2024-07-18` is the
    shipped `openai` default here. The report must not assert 'the configured
    model is NOT the model being used' without naming that benign case, or the
    single operator signal this guard creates is untruthful on a healthy
    config. Every divergence is still reported: no shape rule separates a pin
    from a real version bump (`claude-sonnet-4` -> `claude-sonnet-4-5`)."""
    _m, handler, previous_level = _capture_substitution_warnings()
    try:
        _m._warn_on_model_substitution("gpt-4o-mini", "gpt-4o-mini-2024-07-18")
        _m._warn_on_model_substitution("claude-sonnet-4", "claude-sonnet-4-5")
        assert len(handler.messages) == 2, handler.messages
        for msg in handler.messages:
            assert "dated build" in msg, msg
            assert "set a served id" in msg, msg
    finally:
        _release_substitution_warnings(_m, handler, previous_level)
    print("PASS test_the_substitution_message_is_true_for_a_dated_pin")


def test_analyzer_default_is_a_served_deepseek_id():
    """Anti-revert, same shape as the session default: `analyze.llm_classify`
    POSTs its id straight to api.deepseek.com with no substitution report, so a
    retired id there is silent. `deepseek-v4-flash` is exactly that — the
    provider answers 200 and serves `deepseek-flash` (#4129)."""
    from tortoise.analyze import _LLM_PROVIDERS

    url, model = _LLM_PROVIDERS["DEEPSEEK_API_KEY"]
    assert "api.deepseek.com" in url, url
    assert model == "deepseek-flash", model
    assert model not in ("deepseek-chat", "deepseek-v4-flash"), model
    print("PASS test_analyzer_default_is_a_served_deepseek_id")


def test_openai_headers_no_key():
    m = OpenAICompatModel(id="x", base_url="http://localhost", api_key_env=None)
    h = m._headers()
    assert h == {"Content-Type": "application/json"}
    print("PASS test_openai_headers_no_key")


def test_openai_headers_missing_env():
    m = OpenAICompatModel(id="deepseek-chat", base_url="http://localhost",
                          api_key_env="OPENAI_API_KEY")
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        err = None
        try:
            m._headers()
        except RuntimeError as e:
            err = str(e)
        assert err is not None
        assert "OPENAI_API_KEY" in err
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved
    print("PASS test_openai_headers_missing_env")


def test_openai_headers_present():
    m = OpenAICompatModel(id="x", base_url="http://localhost",
                          api_key_env="TORT_TEST_KEY")
    os.environ["TORT_TEST_KEY"] = "sk-fake"
    try:
        h = m._headers()
        assert h["Content-Type"] == "application/json"
        assert h["Authorization"] == "Bearer sk-fake"
    finally:
        del os.environ["TORT_TEST_KEY"]
    print("PASS test_openai_headers_present")


# ---------------------------------------------------------------------------
# OllamaModel
# ---------------------------------------------------------------------------


def test_ollama_init_defaults():
    m = OllamaModel(id="qwen3:0.6b")
    assert m.id == "qwen3:0.6b"
    assert m.base_url == "http://localhost:11434"
    assert m.timeout == 300
    assert m.think is False
    print("PASS test_ollama_init_defaults")


def test_ollama_init_custom():
    m = OllamaModel(id="qwen3:14b", base_url="http://192.168.1.50:11434/",
                    timeout=600, think=True)
    assert m.id == "qwen3:14b"
    assert m.base_url == "http://192.168.1.50:11434"
    assert m.timeout == 600
    assert m.think is True
    print("PASS test_ollama_init_custom")


def test_ollama_base_url_strips_trailing_slash():
    m = OllamaModel(id="x", base_url="http://localhost:11434///")
    assert m.base_url == "http://localhost:11434"
    print("PASS test_ollama_base_url_strips_trailing_slash")


def test_ollama_build_request_no_think():
    m = OllamaModel(id="qwen3:0.6b")
    req = m.build_request(system="Be concise.", user="What is 2+2?")
    assert req["model"] == "qwen3:0.6b"
    assert req["think"] is False
    assert req["stream"] is False
    assert req["format"] == "json"
    msgs = req["messages"]
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "Be concise."}
    assert msgs[1] == {"role": "user", "content": "What is 2+2?"}
    print("PASS test_ollama_build_request_no_think")


def test_ollama_build_request_think():
    m = OllamaModel(id="qwen3:14b", think=True)
    req = m.build_request(system="Reason carefully.", user="Explain gravity.")
    assert req["model"] == "qwen3:14b"
    assert req["think"] is True
    assert req["stream"] is False
    assert req["format"] == "json"
    assert req["messages"][0]["role"] == "system"
    assert req["messages"][1]["role"] == "user"
    print("PASS test_ollama_build_request_think")


def test_ollama_parse_response():
    data = {"message": {"content": "the answer is 4"}}
    assert OllamaModel.parse_response(data) == "the answer is 4"
    print("PASS test_ollama_parse_response")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall model tests passed")


if __name__ == "__main__":
    _run_all()
