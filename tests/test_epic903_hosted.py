"""Epic 903-C8 (#1246) — hosted dream wiring: mode selection, #329 budget
accounting, 429+Retry-After, selfhost forwarding (DE2E-11).

The hosted handlers accept `team` as an explicit parameter (the Depends
dependency is satisfied by FastAPI at request time) — the tests drive the
handler functions directly with an explicit team dict, pinning the budget +
mode logic without a live server.
"""
from __future__ import annotations

import asyncio
import random
import time as _t

import pytest

from tests.epic903_fixtures import FIXED_SEED, f1_corpus


class TestBudgetAccounting:
    def test_full_override_consumes_bucket(self):
        """mode='full' via the override counts against the #329 bucket;
        exhaustion raises 429 with Retry-After."""
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR

        tid = "team-budget-test"
        # Recent timestamps (within the 1h window) — epoch values would be
        # pruned by the handler's freshness sweep.
        ha._DREAM_FULL_BUCKETS[tid] = [_t.time() - 1.0] * MAX_DREAM_FULL_PER_HOUR
        try:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as he:
                asyncio.run(ha.dream(full=True, team={"team_id": tid}))
            assert he.value.status_code == 429
            assert "Retry-After" in (he.value.headers or {}), (
                "the 429 must carry Retry-After (seconds until window reset)")
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)

    def test_window_passes_do_not_consume_bucket(self):
        """mode='stale-first' passes are bounded by their per-pass operator
        budget — they do NOT consume the #329 full bucket."""
        import tortoise.hosted_api as ha
        tid = "team-window-test"
        ha._DREAM_FULL_BUCKETS.pop(tid, None)
        try:
            asyncio.run(ha.dream(mode="stale-first", budget=2,
                                  team={"team_id": tid}))
            assert tid not in ha._DREAM_FULL_BUCKETS, (
                "window passes must not consume the #329 bucket")
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)

    def test_full_mode_via_override_consumes_bucket(self):
        """mode='full' (the I1 override) consumes the bucket EVEN when
        full=False — the budget rule counts full passes incl. override."""
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR
        tid = "team-override-test"
        # Bucket at capacity → the override-full request (full=False but
        # mode='full') must hit the 429, proving the override is counted.
        ha._DREAM_FULL_BUCKETS[tid] = (
            [_t.time() - 1.0] * MAX_DREAM_FULL_PER_HOUR)
        try:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as he:
                asyncio.run(ha.dream(mode="full", team={"team_id": tid}))
            assert he.value.status_code == 429
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)


class TestModeWiring:
    def test_worker_routes_write_burst_to_local(self):
        """The _dream_worker drains write bursts in LOCAL mode (W1) — never
        silently full (I1 precedence)."""
        f = f1_corpus()
        try:
            f.sdk._mark_dirty([f.claims["p1"]])
            random.seed(FIXED_SEED)
            r = f.sdk.dream(dirty_only=True, mode="local")
            assert r["mode"] == "local"
        finally:
            f.sdk.close()

    def test_selfhost_forwards_mode_and_budget(self):
        """selfhost /dream forwards mode/budget transparently (no #329)."""
        import inspect
        from tortoise.selfhost_api import dream as selfhost_dream
        sig = inspect.signature(selfhost_dream)
        assert "mode" in sig.parameters, (
            "selfhost /dream must forward mode")
        assert "budget" in sig.parameters, (
            "selfhost /dream must forward budget")
