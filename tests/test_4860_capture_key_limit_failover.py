"""#4860 requirement 4 — end-to-end reachability of the extraction-key rotation.

**Why this file exists (#4282).** #4282 retires 26 surplus public SDK methods,
one of which is ``TortoiseSDK.capture_session``. The guard this file replaces
(``tests/eval/write_path/test_write_path_benchmark.py::
test_capture_session_fails_over_on_403_key_limit``) drove that SDK method, so
deleting the method would have deleted the ONLY end-to-end guard for #4860 —
deleting a live production behaviour's proof along with a dead name. Per the
recorded sequence on #4282 (owner delegation, comment 6612767065: *preserve the
evidence first, delete second*) the guard is RE-EXPRESSED here, before the
deletion, on a door that SURVIVES it.

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
which is precisely the pre-#4860 behaviour this guard exists to catch.
"""
from __future__ import annotations

import itertools
import json

import pytest
import requests

import tortoise.model_adapters as ma
from tests._provider_env import pin_provider_routing_env
from tests.test_hosted_api import TEST_ORG_ID  # noqa: F401  (seeds the org)
from tests.test_hosted_api import client as client  # noqa: F401


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
    for key in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "VENICE_API_KEY"):
        monkeypatch.setenv(key, "test-key")


def test_capture_fails_over_when_a_provider_hits_its_key_limit(client, monkeypatch):
    """#4860 — a 403 key-limit on one leg rotates, and the write still succeeds.

    ``openrouter`` is the exhausted leg (exactly #4860's configured chain:
    deepseek-direct funded, openrouter spent). Pre-#4860 the 403 was
    unconditionally FATAL: the extraction aborted, the capture returned
    ``ok=False`` and the session's claims were never written. Post-fix the lane
    is cooled, the next provider serves, and the receipt names it.
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
    assert calls["venice"] >= 1, "rotation must reach a live configured leg"
    assert body.get("extraction_provider") == "venice", body
    assert body.get("extraction_mode") == "llm:venice", body
