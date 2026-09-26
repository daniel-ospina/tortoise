"""#528 tests — server-side PostHog analytics (fail-safe, deduped).

R19: telemetry never degrades the API. Covers:
- disabled by default / placeholder key → no-op, never raises
- capture() never raises even when the posthog client blows up
- first_api_call deduped per team (fires exactly once per process)
- tenant_provisioned / api_key_created wrappers carry the #528 props
- onboarding_seed_complete / onboarding_decide_complete wrappers (#2006 W11)
"""
from __future__ import annotations  # noqa: I001

import pytest

import tortoise.analytics as analytics


# Snapshot the as-imported posthog client state so each test starts clean
# (module state is read from the env at import time; tests mutate it).
_ORIG_DISABLED = analytics.posthog.disabled
_ORIG_CAPTURE = analytics.posthog.capture


@pytest.fixture(autouse=True)
def _clean_state():
    """Fresh module state per test — dedup set + posthog client."""
    analytics._first_api_call_seen.clear()
    analytics.posthog.disabled = _ORIG_DISABLED
    analytics.posthog.capture = _ORIG_CAPTURE
    yield
    analytics._first_api_call_seen.clear()
    analytics.posthog.disabled = _ORIG_DISABLED
    analytics.posthog.capture = _ORIG_CAPTURE


def _enable(client_recorder=None):
    """Force the module enabled and stub posthog.capture."""
    analytics.posthog.disabled = False
    analytics.posthog.capture = client_recorder or (lambda **kw: None)


class TestDisabled:
    def test_disabled_by_default(self):
        # No POSTHOG_API_KEY in the environment → no-op (R19)
        assert analytics.is_enabled() is False
        # Must never raise even with garbage input
        analytics.capture("tenant_provisioned", "u1", {"org_id": "t1"})

    def test_placeholder_key_disabled(self, monkeypatch):
        # "__..." keys are placeholders (same convention as consent.js)
        monkeypatch.setattr(analytics.posthog, "project_api_key", "__phc_test")
        monkeypatch.setattr(analytics.posthog, "disabled", True)
        assert analytics.is_enabled() is False
        analytics.first_api_call("u1", "t1", "/v1/points", "POST")

    def test_hooks_never_raise_when_disabled(self):
        analytics.tenant_provisioned("u1", "t1", "acme", "free", "team_t1")
        analytics.api_key_created("u1", "t1", "t1key_01", "k1", "provision")
        analytics.first_api_call("u1", "t1", "/v1/points", "POST")
        analytics.onboarding_seed_complete("u1", "t1", "checkpoint")
        analytics.onboarding_decide_complete("u1", "t1", "checkpoint")


class TestCapture:
    def test_capture_posts_to_posthog_with_props(self):
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.capture("user_signed_up", "user-uuid", {"provider": "email"})
        assert calls == [
            {"distinct_id": "user-uuid", "event": "user_signed_up",
             "properties": {"provider": "email"}}
        ]

    def test_capture_never_raises_when_client_fails(self):
        def _boom(**kw):
            raise RuntimeError("posthog down")

        _enable(_boom)
        analytics.capture("tenant_provisioned", "u1", {})  # must not raise
        analytics.onboarding_seed_complete("u1", "t1", "checkpoint")
        analytics.onboarding_decide_complete("u1", "t1", "checkpoint")

    def test_tenant_provisioned_wrapper(self):
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.tenant_provisioned("user-uuid", "t1", "Acme", "free", "team_t1")
        assert calls[0]["event"] == "tenant_provisioned"
        assert calls[0]["distinct_id"] == "user-uuid"
        assert calls[0]["properties"] == {
            "org_id": "t1", "org_name": "Acme", "tier": "free",
            "graph_name": "team_t1",
        }

    def test_api_key_created_wrapper(self):
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.api_key_created("user-uuid", "t1", "t1key_01", "k1", "org_keys")
        assert calls[0]["event"] == "api_key_created"
        assert calls[0]["properties"] == {
            "org_id": "t1", "key_prefix": "t1key_01", "key_id": "k1",
            "source": "org_keys",
        }

    def test_onboarding_seed_complete_wrapper(self):
        """#2006 W11 — the seed funnel event, attributed to its write path."""
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.onboarding_seed_complete("user-uuid", "t1", "checkpoint")
        assert calls == [{
            "distinct_id": "user-uuid", "event": "onboarding_seed_complete",
            "properties": {"org_id": "t1", "source": "checkpoint"},
        }]

    def test_onboarding_decide_complete_wrapper(self):
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.onboarding_decide_complete("user-uuid", "t1", "mcp_auto")
        assert calls[0]["event"] == "onboarding_decide_complete"
        assert calls[0]["distinct_id"] == "user-uuid"
        assert calls[0]["properties"] == {"org_id": "t1",
                                          "source": "mcp_auto"}

    def test_onboarding_wrappers_are_enabled_key_only(self):
        """#2006: the wrappers hold NO dedup state of their own — the caller
        gates on the COMPLETED_STEP edge, so a direct caller emits every time
        (the wrapper must not silently add a second, in-process dedup)."""
        calls = []
        _enable(lambda **kw: calls.append(kw))
        for _ in range(3):
            analytics.onboarding_seed_complete("u1", "t1", "checkpoint")
        assert len(calls) == 3
        assert analytics._first_api_call_seen == set()


class TestFirstApiCallDedup:
    def test_fires_once_per_team(self):
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.first_api_call("u1", "t1", "/v1/points", "POST")
        analytics.first_api_call("u1", "t1", "/v1/points", "POST")
        analytics.first_api_call("u1", "t1", "/v1/points", "GET")
        assert len(calls) == 1
        assert calls[0]["event"] == "first_api_call"
        assert calls[0]["properties"] == {
            "org_id": "t1", "endpoint": "/v1/points", "method": "POST",
        }

    def test_distinct_teams_each_fire(self):
        calls = []
        _enable(lambda **kw: calls.append(kw))
        analytics.first_api_call("u1", "t1", "/v1/points", "POST")
        analytics.first_api_call("u2", "t2", "/v1/points", "POST")
        assert len(calls) == 2

    def test_pending_peek_tracks_claims(self):
        _enable()
        assert analytics.first_api_call_pending("t1") is True
        analytics.first_api_call("u1", "t1", "/v1/points", "POST")
        assert analytics.first_api_call_pending("t1") is False
        assert analytics.first_api_call_pending("t2") is True

    def test_pending_peek_disabled_returns_false(self):
        # Disabled module → no worker-thread spawns, no set growth
        assert analytics.is_enabled() is False
        assert analytics.first_api_call_pending("t1") is False
