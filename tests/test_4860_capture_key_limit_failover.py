"""#4860 requirement 4 — end-to-end reachability of the extraction-key rotation.

**Why this file exists (#4282).** #4282 retires 26 surplus public SDK methods,
one of which is ``TortoiseSDK.capture_session``. The guard this file replaces
(``tests/eval/write_path/test_write_path_benchmark.py::
test_capture_session_fails_over_on_403_key_limit``) drove that SDK method, so
deleting the method would have deleted the ONLY end-to-end guard for #4860 —
deleting a live production behaviour's proof along with a dead name. Per the
recorded decision on #4282 — comment ``6612966554`` (ID verified against the
API; an earlier draft of this file cited ``6612767065``, which resolves to 404),
quoted: *"procedure = B: preserve the evidence, then delete"* — the guard is
RE-EXPRESSED here, before the deletion, on a door that SURVIVES it.

**The door.** ``POST /v1/sessions`` — the hosted capture route. It is the same
pipeline the ``tortoise_session_capture`` MCP tool serves, and it reaches the
EXACT bytes the deleted probe reached: ``_capture_session_impl`` calls
``sdk._extract_session_v2`` (``tortoise/hosted_api.py:17452``) — the same
``_extract_session_v2`` → ``_model_adapter`` → ``build_extractor_model`` →
``RotatingModel`` chain, over the same ``requests.Response.raise_for_status()``
body path. So the behaviour under test is unchanged; only the entry point moves,
and it moves to a MORE public one (an HTTP route rather than a Python method).

What is asserted is exactly #4860's claim: an exhausted provider key (OpenRouter
monthly key limit, reported as HTTP 403 with a ``Key limit exceeded`` body)
must NOT abort the write — the rotation reaches a live leg and the capture
succeeds, reporting the leg that actually served it.

Non-vacuity is measured, not asserted: the mutation that reds this file is to
stop a key-limit 403 from rotating (``model_adapters.is_key_limit_403``
returning False, so ``is_billing_exhausted`` is False and the 403 is fatal),
which is precisely the pre-#4860 behaviour this guard exists to catch. Two
clauses catch it independently — ``calls["venice"] >= 1`` (a live leg was
reached) and the ``extraction_mode == f"llm:{provider}"`` equality (extraction
actually succeeded); the ``extraction_provider`` membership test alone does NOT,
and the assertion block says why.
"""
from __future__ import annotations

import itertools
import json

import pytest
import requests

import tortoise.model_adapters as ma
from tests._provider_env import pin_provider_routing_env
from tests.test_hosted_api import client as client  # its fixture seeds the org


@pytest.fixture(autouse=True)
def _pinned_provider_routing_env(monkeypatch):
    """Neutralise ambient routing (#3699), then declare THIS test's chain.

    Without this an ambient ``TORTOISE_EXTRACTOR_PROVIDER`` / ``VENICE_API_KEY``
    decides whether the failover even has a live leg to reach, and the guard
    would pass or fail for a reason unrelated to #4860.
    """
    pin_provider_routing_env(monkeypatch)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    # ``_failover_cooldown_seconds`` (model_adapters) is a ROUTING knob too, and
    # ``pin_provider_routing_env`` deliberately covers only the key envs and the
    # four selectors. An ambient ``TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN=0``
    # disables the flap guard, so the exhausted openrouter leg is re-admitted and
    # hit TWICE — which reds the "hit exactly once" clause and misdiagnoses a
    # deliberately-disabled cooldown as broken rotation (#3699 class).
    monkeypatch.delenv("TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN", raising=False)
    for key in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "VENICE_API_KEY"):
        monkeypatch.setenv(key, "test-key")


def test_capture_fails_over_when_a_provider_hits_its_key_limit(client, monkeypatch):
    """#4860 — a 403 key-limit on one leg rotates, and the write still succeeds.

    ``openrouter`` is the exhausted leg (exactly #4860's configured chain:
    deepseek-direct funded, openrouter spent). Pre-#4860 the 403 was
    unconditionally FATAL: the extraction aborted and the session's claims were
    never written. Post-fix the lane is cooled, the next provider serves, and the
    receipt names it.

    ⚠️ What "aborted" looks like through THIS door is NOT an ``ok=False``
    receipt — the hosted capture receipt has no ``ok`` field by design
    (``hosted_api.py``: the HTTP status is the ok signal). A fatal extraction
    still answers **HTTP 200** and reports ``extraction_mode == "error"`` with a
    non-empty ``errors[]``. The old SDK-door probe asserted ``ok is True`` /
    ``errors == []``; here ``extraction_mode == f"llm:{provider}"`` is the
    equivalent and stricter spelling, because a non-empty ``extraction_errors``
    forces ``effective_mode`` to ``"error"``. A later lane must not go looking
    for ``body["ok"]`` on this door — it does not exist.
    """
    or_key_limit = json.dumps({"error": {
        "message": "Key limit exceeded (monthly limit).",
        "code": 403}})
    s1_story = "The session revealed a durable strategy decision."
    s2_json = ('{"entities": [], "events": [], "operators": [], '
               '"points": [{"content": "the new strategy is durable", '
               '"pointKind": "statement", '
               '"quote": "the new strategy is durable", "tier": "A"}]}')
    s4_json = ('{"entities": [], "events": [], "operators": [], "points": [], '
               '"retractions": [], "link_before_create": [], '
               '"chain_notes": []}')
    calls = {"venice": 0, "openrouter": 0, "deepseek": 0}

    def _resp(url, body=None):
        r = requests.Response()
        r.url = url
        r.encoding = "utf-8"
        if body is not None:
            r.status_code = 403
            r._content = body.encode("utf-8")
        else:
            r.status_code = 200
            r._content = json.dumps({
                "choices": [{"message": {"content": "unused"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            }).encode("utf-8")
        r.request = requests.Request("POST", url).prepare()
        return r

    def _fake_post(session, url, *args, **kwargs):
        if "api.venice.ai" in url:
            calls["venice"] += 1
        elif "openrouter.ai" in url:
            calls["openrouter"] += 1
        elif "api.deepseek.com" in url:
            calls["deepseek"] += 1
        else:  # pragma: no cover — no other host is expected
            raise AssertionError(f"unexpected provider URL {url!r}")
        if "openrouter.ai" in url:
            return _resp(url, body=or_key_limit)
        system = ""
        messages = (kwargs.get("json") or {}).get("messages") or []
        if messages:
            system = messages[0].get("content", "")
        if "STORY SUMMARIZER" in system:
            content = s1_story
        elif "GRAPH MAPPER" in system:
            content = s2_json
        elif "GAP REVIEWER" in system:
            content = s4_json
        else:
            content = s2_json
        resp = _resp(url)
        resp._content = json.dumps({
            "choices": [{"message": {"content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5},
        }).encode("utf-8")
        return resp

    monkeypatch.setattr(requests.sessions.Session, "post", _fake_post)

    # Deterministic rotation: try the exhausted openrouter leg FIRST on every
    # pick, then a healthy leg — the rotation policy is the subject here, not
    # the weighted round-robin RNG.
    pick_seq = itertools.cycle(["openrouter", "venice", "deepseek-direct"])

    def _forced_pick(self):
        target = next(pick_seq)
        names = [p.provider for p in self.providers]
        return names.index(target) if target in names else 0

    monkeypatch.setattr(ma.RotatingModel, "_pick", _forced_pick)

    r = client.post("/v1/sessions", json={
        "conversation": [
            {"role": "user", "content": "we decided X"},
            {"role": "assistant", "content": "the plan is durable"},
        ],
        "session_id": "wp_kl_403_door",
        "harness": "pi",
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert calls["openrouter"] == 1, (
        "the exhausted leg must be hit exactly once and then cooldowned")
    # ⭐ THE DISCRIMINATING CLAUSE. Under the non-vacuity mutation
    # (``is_key_limit_403 -> False``, i.e. pre-#4860) ``openrouter`` is still hit
    # once and the default route still reads ``venice`` — only this line reds.
    # Do not delete or "simplify" it: it is the assertion that proves a live leg
    # was actually REACHED.
    assert calls["venice"] >= 1, "rotation must reach a live configured leg"

    provider = body.get("extraction_provider")
    # ⚠️ Two things this pair is and is NOT, both measured, because the honest
    # reading differs from the obvious one:
    #
    #   * ``extraction_provider`` is ``RotatingModel.route``, which is
    #     INITIALISED to ``providers[0].provider`` and only reassigned on a
    #     SUCCESS (``model_adapters.py``: ``__init__`` sets ``self.route =
    #     providers[0].provider``; ``complete`` reassigns it after a response
    #     arrives). It therefore does NOT prove a leg served: on the pre-#4860
    #     fatal path the receipt still reads ``extraction_provider ==
    #     "venice"`` even though venice never answered and the fatal leg was
    #     openrouter. So the membership test below is a cheap "the receipt does
    #     not name the EXHAUSTED leg" sanity check, NOT rotation evidence.
    #   * ``extraction_mode`` IS discriminating: a non-empty
    #     ``extraction_errors`` forces ``effective_mode`` to ``"error"``, so
    #     under the mutation the mode is ``"error"`` and the equality below
    #     reds on its own. It is the second live clause, not decoration.
    assert provider in ("venice", "deepseek-direct"), body
    assert body.get("extraction_mode") == f"llm:{provider}", body
