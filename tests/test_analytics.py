"""D10 tests — analytics hooks (fire-and-forget, non-blocking).

Epic: 2026-08-07-tortoise-user-journeys · Issue: #577 (D10)
Plan §6.4 event schema; R19: telemetry never degrades the API.
"""
from __future__ import annotations

import os

import tortoise.analytics as analytics


class TestAnalytics:
    def test_disabled_by_default(self):
        # No POSTHOG_KEY/ENDPOINT → disabled → fire is a no-op (R19)
        assert analytics.is_enabled() is False

    def test_fire_and_forget_does_not_raise_when_disabled(self):
        # Must never raise even with bad input
        analytics.first_api_call("u1", "t1", "/v1/points")
        analytics.tenant_provisioned("u1", "t1", "confirmed")

    def test_fire_and_forget_with_bad_endpoint_does_not_raise(self):
        # Enabled but unreachable endpoint → drop-on-failure (R19)
        os.environ["POSTHOG_ENDPOINT"] = "http://127.0.0.1:1"
        os.environ["POSTHOG_KEY"] = "test"
        try:
            analytics.fire_and_forget("first_api_call", "u1", {"team_id": "t1"})
        finally:
            os.environ.pop("POSTHOG_ENDPOINT", None)
            os.environ.pop("POSTHOG_KEY", None)
        # Reached here without raising = pass (fire-and-forget is best-effort)
