"""Model backends for the extractor stages.

A Model is a text-in / text-out completer. Stages own the prompts and parsing, so
swapping a model is a config change, not a code change — which is what lets us
benchmark "what we can get away with" per stage (cheap model for point extraction,
large reasoning model for relations).

`OpenAICompatModel` covers DeepSeek, Gemini (OpenAI-compat endpoint), and local
Ollama with one adapter. Its transport (`complete`) is thin and untested without a
network/key; the tested surface is `build_request` / `parse_response`.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import urllib.request
from typing import Protocol, runtime_checkable

_logger = logging.getLogger(__name__)

#: #4129: providers ACCEPT a retired model id and silently serve a different
#: model. api.deepseek.com returns 200 for ``deepseek-chat`` and serves
#: ``deepseek-flash``, so the configured model was not the model used and no
#: response field was ever inspected to notice. Warn ONCE per
#: (requested, served) pair — a long capture path may call this thousands of
#: times, and a per-call warning would be its own defect.
#:
#: A dated pin of the requested id (``gpt-4o-mini`` ->
#: ``gpt-4o-mini-2024-07-18``, the shipped ``openai`` default below) is BENIGN
#: and the message says so — but it is deliberately NOT special-cased into
#: silence or a lower level. No shape rule separates a harmless pin from a real
#: version bump: ``claude-sonnet-4`` -> ``claude-sonnet-4-5`` is a DIFFERENT
#: model with the same ``-<digits>`` form, so classifying on shape re-opens,
#: silently, the substitution this guard exists to expose (#4129). The cost of
#: not doing so is bounded by the once-per-pair rule.
_substituted_models: set[tuple[str, str]] = set()

#: Cap for that memo. The once-per-pair rule assumes the served id is stable,
#: but this guard exists BECAUSE the provider may misbehave — so a provider
#: returning a new id on every response would otherwise grow the set without
#: bound and defeat the dedup it provides. Clearing at the cap keeps the
#: polarity safe: the worst case is a repeated warning, never a missed one.
_SUBSTITUTED_MEMO_CAP = 256


def _warn_on_model_substitution(requested: str, served: object) -> None:
    """Warn when the provider served a model other than the one requested.

    Never raises a propagating ``Exception``. This runs inside every
    ``complete()``, so the predicate accepts only an exact ``str`` on each side —
    keeping a subclass with a hostile ``__eq__``/``__hash__`` away from the
    comparison and the memo — and the emit is suppressed, because a raising log
    handler would otherwise fail the capture and skip ``_emit_usage_sink``; that
    seam follows the same "an observer must never flip a call outcome" rule.
    ``BaseException`` (``KeyboardInterrupt``/``SystemExit``) still propagates, by
    design and as it does at that seam.
    """
    if type(requested) is not str or type(served) is not str:
        return
    if not served or served == requested:
        return
    pair = (requested, served)
    if pair in _substituted_models:
        return
    with contextlib.suppress(Exception):
        _logger.warning(
            "model substitution: requested %r but the provider served %r — the "
            "provider accepted one model id and ran another. This is expected "
            "when it pins the id to a dated build, and is otherwise the "
            "configured model NOT being the model used, with cost and quality "
            "attributed to a model nobody chose; if that is not intended, set "
            "a served id",
            requested,
            served,
        )
        # Memoise only AFTER the emit. If a log handler raises, the pair must
        # stay un-memoised so the next call reports it AGAIN — suppressing the
        # raise must not also suppress the FINDING for the process lifetime.
        # The polarity of the cap is chosen the same way: a repeated warning is
        # acceptable, a missed one is not.
        if len(_substituted_models) >= _SUBSTITUTED_MEMO_CAP:
            _substituted_models.clear()
        _substituted_models.add(pair)


def _emit_usage_sink(model, usage) -> None:
    """#2185 seam: optional usage-capture sink fire — strict NO-OP when unset.

    Called at the response-parse site of every real chat transport with the
    RESPONSE-LOCAL usage block (the per-attempt billed totals, incl.
    cache-detail fields when the provider sends them). Payload is keyword:
    ``(provider, model_id, usage, usage_present)`` — the SAME contract on all
    four seam sites (#2185 A1):

    - ``provider`` — the adapter's own class attribute where it exists
      (openrouter/venice/deepseek-direct); ``None`` on provider-less classes
      (OpenAICompatModel, OfficialJudgeModel) — the harness binds provider at
      registration time, never on the shared product class (Am 9).
    - ``usage`` — the response usage dict (may be None / {} when the provider
      sent none).
    - ``usage_present`` — ``bool(usage)`` (False = non-billing lane / no usage
      block; the call still counts).
    """
    sink = getattr(model, "usage_sink", None)
    if sink is None:
        return
    with contextlib.suppress(Exception):
        # round-2 code-review P2: a metering observer must NEVER flip a call
        # outcome — a raising/poisoned sink degrades to a silent no-op at
        # the fire site (the provider response was already parsed).
        sink(provider=getattr(model, "provider", None),
             model_id=model.id, usage=usage, usage_present=bool(usage))


@runtime_checkable
class Model(Protocol):
    id: str
    def complete(self, *, system: str, user: str) -> str: ...


_UNSET = object()


class OpenAICompatModel:
    def __init__(self, *, id: str, base_url: str, api_key_env: str | None = "OPENAI_API_KEY",
                 temperature: float = 0.0, timeout: int = 60,
                 response_format: dict | None | object = _UNSET,  # noqa: RUF036
                 max_tokens: int | None = None):
        # api_key_env=None → no Authorization header.
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.timeout = timeout
        # response_format defaults to the legacy JSON-object mode so existing
        # extraction callers are unaffected; pass response_format=None to opt
        # out (the LongMemEval reader must NOT force JSON — the official
        # benchmark call shape has no response_format). max_tokens=None → the
        # key is omitted from the request body.
        self.response_format = (
            {"type": "json_object"} if response_format is _UNSET else response_format)
        self.max_tokens = max_tokens
        # #2185: additive usage-capture seam (no-op unless the harness sets
        # it). NO last_* mirrors on this class — it is shared with the product
        # paths (sdk.py/ingest.py/mining.py) and the eval owns its metering.
        self.usage_sink = None

    def build_request(self, system: str, user: str) -> dict:
        req = {
            "model": self.id,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.response_format is not None:
            req["response_format"] = self.response_format
        if self.max_tokens is not None:
            req["max_tokens"] = self.max_tokens
        return req

    @staticmethod
    def parse_response(data: dict) -> str:
        return data["choices"][0]["message"]["content"]

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise RuntimeError(
                    f"{self.id}: env var {self.api_key_env} is not set")
            h["Authorization"] = f"Bearer {key}"
        return h

    def complete(self, *, system: str, user: str) -> str:
        body = json.dumps(self.build_request(system, user)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        # #4129: observe the SERVED model before anything else consumes the
        # response — the only place a silent substitution is visible.
        _warn_on_model_substitution(self.id, data.get("model"))
        # #2185 seam: fire with the response-local usage (provider None here —
        # bound at registration by the harness; no mirrors on this class).
        _emit_usage_sink(self, data.get("usage"))
        return self.parse_response(data)


class OllamaModel:
    """Native Ollama /api/chat with a `think` toggle. The OpenAI-compat endpoint
    can't control qwen3's thinking, so we hit the native endpoint directly.
    think=False for the mechanical point tier (thinking is wasted and ~50x slower);
    think=True for the relation tier, which needs reasoning to find operators."""

    def __init__(self, *, id: str, base_url: str = "http://localhost:11434",
                 timeout: int = 300, think: bool = False, temperature: float = 0.0):
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.think = think
        self.temperature = temperature

    def build_request(self, system: str, user: str) -> dict:
        req = {
            "model": self.id,
            "think": self.think,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if "options" not in req:
            req["options"] = {}
        req["options"]["temperature"] = self.temperature
        return req

    @staticmethod
    def parse_response(data: dict) -> str:
        return data["message"]["content"]

    def complete(self, *, system: str, user: str) -> str:
        body = json.dumps(self.build_request(system, user)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return self.parse_response(json.loads(r.read()))
