"""Usage-capture seam tests (#2185 Task 1).

Pins the additive ``usage_sink`` capture seam on the real chat adapters:

- ``OpenRouterModel.complete`` / ``DeepSeekDirectModel.complete`` (its own
  body) / ``VeniceModel`` (inherits OpenRouterModel) fire the sink at the
  response-parse point with RESPONSE-LOCAL usage (the per-attempt billed
  totals incl. cache-detail fields when the provider sends them).
- ``OpenAICompatModel.complete`` (urllib transport, shared product class)
  fires the same contract but carries NO ``.provider`` attribute and gains
  NO last_* mirrors (#2185 A1 / Am 9 — provider is bound at registration,
  never on this class).
- ``extractor_v2._call_once`` runs the daemon-thread model call inside a
  ``contextvars.copy_context()`` so a caller-set context (the eval's
  question key) is visible to ``complete()`` in the child thread
  (contextvars do NOT propagate to new threads — repo precedent
  quota.py:739; #2185 A8).

Default state is a no-op: ``usage_sink`` is None on construction and no
code path touches it when unset — every existing adapter/mirror behavior
stays byte-identical.
"""
from __future__ import annotations

import contextvars
import json
import math
import urllib.request

import pytest
import requests

from tortoise.extractor_v2 import _call_once
from tortoise.model_adapters import (
    DeepSeekDirectModel,
    OpenRouterModel,
    VeniceModel,
)
from tortoise.models import OpenAICompatModel

# ── stub transport helpers (repo convention: monkeypatch Session.post) ──────

def _fake_post_logger(usage=None):
    """Monkeypatches requests.sessions.Session.post; returns (log, fake)."""
    log = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}],
                "usage": usage if usage is not None else {
                    "prompt_tokens": 11, "completion_tokens": 7,
                    "prompt_cache_hit_tokens": 5,
                },
            }

    def fake(self_or_url, *args, **kwargs):
        log.append(kwargs.get("json", {}))
        return _Resp()

    return log, fake


def _record_sink():
    calls: list[dict] = []
    return calls, lambda **kw: calls.append(kw)


# ── sink default state ──────────────────────────────────────────────────────

def test_usage_sink_defaults_none_on_all_adapters():
    assert OpenRouterModel("deepseek/deepseek-v4-flash").usage_sink is None
    assert DeepSeekDirectModel("deepseek-v4-flash").usage_sink is None
    assert VeniceModel("deepseek/deepseek-v4-flash").usage_sink is None
    assert OpenAICompatModel(
        id="gpt-4o-2024-08-06", base_url="https://api.openai.com/v1",
        api_key_env=None).usage_sink is None


# ── judge lane A3 wiring (LLMJudge.provider → collector registration) ────────

def test_llmjudge_provider_registers_judge_lane_under_resolved_provider():
    """#2185 A3 (VGATE P1 regression): LLMJudge must carry the resolved
    endpoint provider so run.py's attach registers the judge lane under
    ``openai`` (NOT "unknown") — OfficialJudgeModel itself has no .provider,
    and an unattributed judge lane prices at $0 / priced:false despite
    gpt-4o-2024-08-06 being in PRICING_MAP."""
    from tools.longmem_eval import usage as lme_usage
    from tools.longmem_eval.judge import LLMJudge, MockJudge

    c = lme_usage.get_collector()
    c.reset()

    class _FiringJudgeModel:
        """Mirror of OfficialJudgeModel's fire contract: sink payload has
        provider None + model id; LLMJudge.complete(user=...) is the call
        shape."""

        id = "gpt-4o-2024-08-06"

        def __init__(self):
            self.usage_sink = None

        def complete(self, *, user, **kw):
            sink = getattr(self, "usage_sink", None)
            if sink is not None:
                sink(provider=None, model_id=self.id,
                     usage={"prompt_tokens": 5, "completion_tokens": 1},
                     usage_present=True)
            return "yes"

    model = _FiringJudgeModel()
    judge = LLMJudge(model, model_id="gpt-4o-2024-08-06",
                     model_spec="openai:gpt-4o-2024-08-06",
                     provider="openai")
    assert judge.provider == "openai"
    # run.py attach contract: bind judge._model under judge.provider.
    assert c.attach(getattr(judge, "_model", None), stage="judge",
                    provider=getattr(judge, "provider", None)) == 1
    lme_usage.set_question_key("q9")
    assert judge.judge(question_type="single-session-fact", question="q",
                       answer="a", hypothesis="h",
                       abstention=True) is True
    env = c.drain_question("q9")
    # A3: the lane is (judge, openai, gpt-4o-2024-08-06) — priceable, not
    # the unattributed (judge, unknown, ...) the VGATE reproduced.
    assert env["by_stage"]["judge"]["openai"]["gpt-4o-2024-08-06"][
        "calls"] == 1
    assert "unknown" not in env["by_stage"]["judge"]
    # MockJudge carries no provider + no complete()-bearing _model.
    mock = MockJudge()
    assert getattr(mock, "provider", None) is None
    assert c.attach(getattr(mock, "_model", None), stage="judge",
                    provider=getattr(mock, "provider", None)) == 0


def test_build_judge_sets_resolved_provider(monkeypatch):
    """#2185 A3: build_judge threads the RESOLVED provider (not the spec's
    bare name, not None) onto LLMJudge — mirrors build_reader."""
    from tools.longmem_eval.judge import LLMJudge, build_judge

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-validated")
    monkeypatch.setattr(
        "tools.longmem_eval.judge._resolve_provider",
        lambda named=None: ("openai", "https://api.openai.com/v1",
                            "OPENAI_API_KEY"))
    judge = build_judge(spec="openai:gpt-4o-2024-08-06")
    assert isinstance(judge, LLMJudge)
    assert judge.provider == "openai"


# ── OpenRouterModel ─────────────────────────────────────────────────────────

def test_openrouter_sink_fires_with_response_local_usage(monkeypatch):
    _, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    calls, sink = _record_sink()
    model = OpenRouterModel("deepseek/deepseek-v4-flash")
    model.usage_sink = sink
    out = model.complete(system="s", user="u")
    assert out == "ok"
    assert len(calls) == 1, "sink must fire exactly once per response"
    payload = calls[0]
    assert payload["provider"] == "openrouter"  # class attr on the adapter
    assert payload["model_id"] == "deepseek/deepseek-v4-flash"
    assert payload["usage"] == {
        "prompt_tokens": 11, "completion_tokens": 7,
        "prompt_cache_hit_tokens": 5,
    }
    assert payload["usage_present"] is True


def test_openrouter_no_sink_keeps_mirrors(monkeypatch):
    _, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    model = OpenRouterModel("deepseek/deepseek-v4-flash")  # no sink set
    out = model.complete(system="s", user="u")
    assert out == "ok"
    assert model.last_prompt_tokens == 11
    assert model.last_completion_tokens == 7


def test_raising_sink_never_flips_call_outcome(monkeypatch):
    """Security review P2: the seam is exception-safe — a raising/poisoned
    sink must degrade to a silent no-op at the fire site, never flip an
    otherwise-valid LLM call into a failure/retry."""
    _, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)

    class _Boom:
        def __call__(self, **kw):
            raise RuntimeError("metering observer exploded")

    model = OpenRouterModel("deepseek/deepseek-v4-flash")
    model.usage_sink = _Boom()
    out = model.complete(system="s", user="u")
    assert out == "ok"  # the call SUCCEEDS despite the raising sink
    assert model.last_prompt_tokens == 11

    # a poison usage DICT (NaN) also cannot raise through the sink — the
    # collector sanitizer is the choke point (list usage crashes the
    # PRE-EXISTING mirror code on the product path — pre-#2185 behavior,
    # out of seam scope; see models.py OpenAICompatModel.parse_response).
    poison = OpenRouterModel("deepseek/deepseek-v4-flash")
    calls, sink = _record_sink()
    poison.usage_sink = sink

    class _PoisonResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": float("nan")}}

    monkeypatch.setattr(requests.sessions.Session, "post",
                        lambda *a, **k: _PoisonResp())
    out = poison.complete(system="s", user="u")
    assert out == "ok"
    assert calls[0]["usage_present"] is True
    assert math.isnan(calls[0]["usage"]["prompt_tokens"])


# ── DeepSeekDirectModel (its OWN complete body — must not be missed) ────────

def test_deepseek_direct_sink_fires(monkeypatch):
    _, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    calls, sink = _record_sink()
    model = DeepSeekDirectModel("deepseek-v4-flash")
    model.usage_sink = sink
    out = model.complete(system="s", user="u")
    assert out == "ok"
    assert len(calls) == 1
    assert calls[0]["provider"] == "deepseek-direct"
    assert calls[0]["model_id"] == "deepseek-v4-flash"
    assert calls[0]["usage"]["completion_tokens"] == 7
    assert calls[0]["usage_present"] is True


# ── VeniceModel (inherits OpenRouterModel.complete) ─────────────────────────

def test_venice_sink_inherited(monkeypatch):
    _, fake = _fake_post_logger()
    monkeypatch.setattr(requests.sessions.Session, "post", fake)
    calls, sink = _record_sink()
    model = VeniceModel("deepseek/deepseek-v4-flash")
    model.usage_sink = sink
    out = model.complete(system="s", user="u")
    assert out == "ok"
    assert len(calls) == 1
    assert calls[0]["provider"] == "venice"


# ── OpenAICompatModel (shared product class — sink attr, NO mirrors) ────────

def test_openai_compat_model_sink_fires_no_mirrors(monkeypatch):
    data = json.dumps({
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return data

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: _Resp())
    calls, sink = _record_sink()
    model = OpenAICompatModel(
        id="gpt-4o-2024-08-06", base_url="https://api.openai.com/v1",
        api_key_env=None)
    model.usage_sink = sink
    out = model.complete(system="s", user="u")
    assert out == "ok"
    assert len(calls) == 1
    payload = calls[0]
    # #2185 A1: OpenAICompatModel carries NO .provider — the payload provider
    # is None and the harness binds provider at registration time instead.
    assert payload["provider"] is None
    assert payload["model_id"] == "gpt-4o-2024-08-06"
    assert payload["usage"] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert payload["usage_present"] is True
    # Am 9: NO last_* mirrors on the shared product class.
    assert not hasattr(model, "last_prompt_tokens")
    assert not hasattr(model, "last_completion_tokens")


def test_openai_compat_model_no_sink_noop(monkeypatch):
    data = json.dumps({
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return data

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: _Resp())
    model = OpenAICompatModel(
        id="gpt-4o-2024-08-06", base_url="https://api.openai.com/v1",
        api_key_env=None)
    assert model.complete(system="s", user="u") == "ok"  # no sink, no crash


# ── OfficialJudgeModel (eval-owned exact judge shape — same seam) ──────────

def test_official_judge_sink_fires(monkeypatch):
    data = json.dumps({
        "choices": [{"message": {"content": "yes"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 1},
    }).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return data

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: _Resp())
    from tools.longmem_eval.judge import OfficialJudgeModel

    calls, sink = _record_sink()
    model = OfficialJudgeModel(
        id="gpt-4o-2024-08-06", base_url="https://api.openai.com/v1",
        api_key_env=None)
    assert model.usage_sink is None  # default no-op
    model.usage_sink = sink
    out = model.complete(user="probe")
    assert out == "yes"
    assert len(calls) == 1
    payload = calls[0]
    # A1: no .provider on the model — payload None, harness binds later.
    assert payload["provider"] is None
    assert payload["model_id"] == "gpt-4o-2024-08-06"
    assert payload["usage"] == {"prompt_tokens": 20, "completion_tokens": 1}
    assert payload["usage_present"] is True


def test_official_judge_no_sink_noop(monkeypatch):
    data = json.dumps({
        "choices": [{"message": {"content": "no"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 1},
    }).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return data

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: _Resp())
    from tools.longmem_eval.judge import OfficialJudgeModel

    model = OfficialJudgeModel(
        id="gpt-4o-2024-08-06", base_url="https://api.openai.com/v1",
        api_key_env=None)
    assert model.complete(user="probe") == "no"


# ── extractor_v2._call_once context propagation (F4 / #2185 A8) ─────────────

def test_call_once_runs_model_in_caller_context():
    """The daemon-thread model call must see the caller's contextvars.

    contextvars do NOT propagate to new threads in CPython — _call_once must
    capture ``contextvars.copy_context()`` before ``Thread.start()`` and run
    the body under ``ctx.run(...)`` (repo precedent quota.py:739).
    """
    cv = contextvars.ContextVar("lme_seam_ctx_key", default="unset")
    seen: dict = {}

    class StubModel:
        last_finish_reason = "stop"

        def complete(self, *, system, user, **kw):
            seen["ctx_value"] = cv.get()
            return "ok"

    cv.set("caller-value")
    resp, finish, ptok, ctok = _call_once(
        StubModel(), "s", "u", deadline_s=30, max_tokens=None, stats=None)
    assert ptok == 0 and ctok == 0  # #2134 Task 0: no token attrs -> 0
    assert resp == "ok"
    assert finish == "stop"
    assert seen["ctx_value"] == "caller-value", (
        "daemon thread saw the default context — _call_once must run the "
        "model call inside contextvars.copy_context()")
    # caller context untouched after the call
    assert cv.get() == "caller-value"


def test_call_once_ctx_does_not_leak_between_sequential_calls():
    """Each call snapshots the caller context at call time — a value set
    between calls is seen by the second call only."""
    cv = contextvars.ContextVar("lme_seam_ctx_key2", default="unset")
    seen: list[str] = []

    class StubModel:
        last_finish_reason = "stop"

        def complete(self, *, system, user, **kw):
            seen.append(cv.get())
            return "ok"

    cv.set("q1")
    _call_once(StubModel(), "s", "u", deadline_s=30, max_tokens=None,
               stats=None)
    cv.set("q2")
    _call_once(StubModel(), "s", "u", deadline_s=30, max_tokens=None,
               stats=None)
    assert seen == ["q1", "q2"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
