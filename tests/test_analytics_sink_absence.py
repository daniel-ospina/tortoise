"""#3944 — detecting an analytics sink that silently STOPS emitting.

#3820 gave the analytics write path a terminal outcome vocabulary and a
"transition" alert: a write classified ``fallback``/``dropped``/
``supabase_env_incomplete`` files ``ANALYTICS_SINK_DEGRADED``. That signal is
triggered by a write, so a sink that never receives one — the emitter is never
invoked, or ``_track_analytics_event`` regresses to a bare ``return`` — is
invisible: no outcome, no streak, nothing counted.

The absence half (D5b) is a dead-man's switch, split by role:

* the PROBE is a fixed-cadence CANARY write through the REAL sink path
  (``_analytics_canary_loop`` / ``_analytics_canary_tick``) — only an
  end-to-end write proves the write path;
* the HEARTBEAT is ``_ANALYTICS_LAST_DELIVERED_AT``, set ONLY on a delivered
  (2xx) write and published on the internal ``/status`` endpoint — a channel
  the sink cannot silence. A last-ATTEMPT stamp would be fail-open;
* the ALARM is external (``.github/scripts/registry-cron.sh``) and files the
  SAME kind, so absence and degradation cannot double-file.

These tests pin the app half behaviourally. The driver half is pinned by
``.github/scripts/registry-cron.test.sh``.

RED mutations each test is built to catch:
* delete the heartbeat write in ``_analytics_note_success`` → T3/T6 fail;
* make the canary skip whenever the sink is only half-configured (gate on
  ``configured`` instead of ``intended``) → T5 fails;
* seed the heartbeat at boot (a last-attempt stamp) → T4 fails;
* emit from the canary before sleeping → T8 fails;
* drop ``_analytics_canary_task`` from ``_LIVENESS_TASK_ATTRS`` → the boot
  regression pin fails (tests/test_boot_regressions.py).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import tortoise.hosted_api as ha

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"
PROD_URL = "https://analytics3944.supabase.co"
PROD_KEY = "svc-3944"


class _Resp:
    def __init__(self, status: int):
        self.status_code = status


class _StubClient:
    """Records the POST and replies with a chosen status (only that is read)."""

    status: ClassVar[int] = 201
    posts: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        type(self).posts.append((url, kwargs))
        return _Resp(type(self).status)


def _prod_env(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _no_env(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _half_env(monkeypatch) -> None:
    """#3677's own shape: a URL with the key resolving to ``""``."""
    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _stub_http(monkeypatch, status: int = 201):
    _StubClient.status = status
    _StubClient.posts = []
    monkeypatch.setattr("httpx.Client", _StubClient)
    return _StubClient


def _canary_posts():
    return [
        (u, p)
        for u, p in _StubClient.posts
        if p.get("json", {}).get("event_name") == ha._ANALYTICS_CANARY_EVENT
    ]


# ── the sink-configuration seam ─────────────────────────────────────────────


def test_probe_is_one_seam_with_the_two_predicates(monkeypatch):
    """``configured`` (both present) and ``intended`` (either present) are
    distinct, and both come from the one seam every site reads."""
    _no_env(monkeypatch)
    url, key, configured, intended = ha._analytics_sink_probe()
    assert not url and not key and not configured and not intended

    _prod_env(monkeypatch)
    assert ha._analytics_sink_probe() == (PROD_URL, PROD_KEY, True, True)
    _half_env(monkeypatch)
    url, _key, configured, intended = ha._analytics_sink_probe()
    assert url == PROD_URL
    assert configured is False, "a half-configured env cannot deliver a write"
    assert intended is True, (
        "a half-configured env INTENDED a sink — gating on `configured` alone "
        "is what left the absence half blind to #3677 itself"
    )


# ── the canary probe ────────────────────────────────────────────────────────


def test_tick_skips_when_no_sink_was_intended(monkeypatch, tmp_path):
    """selfhost/dev: the JSONL IS the intended sink — no canary, no line."""
    _no_env(monkeypatch)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))

    attempted = asyncio.run(ha._analytics_canary_tick())

    assert attempted is False
    assert ha._ANALYTICS_CANARY_ATTEMPTS == 0
    assert not fallback.exists(), "an unconfigured deployment must stay clean"


def test_tick_emits_through_the_real_sink_path(monkeypatch):
    """The canary is a REAL write: it reaches the analytics endpoint, on the
    telemetry pool (off the event loop), with the reserved identity."""
    _prod_env(monkeypatch)
    stub = _stub_http(monkeypatch, status=201)

    attempted = asyncio.run(ha._analytics_canary_tick())

    assert attempted is True
    assert ha._ANALYTICS_CANARY_ATTEMPTS == 1
    canary = _canary_posts()
    assert len(canary) == 1, stub.posts
    url, kwargs = canary[0]
    assert url == f"{PROD_URL}/rest/v1/analytics_events"
    # The reserved identity is what keeps canary rows out of every per-org read.
    assert kwargs["json"]["org_id"] == ha._ANALYTICS_CANARY_ORG
    assert kwargs["json"]["org_id"].startswith("_")
    assert kwargs["json"]["properties"] == {}
    # A DELIVERED write refreshes the heartbeat.
    assert ha._ANALYTICS_LAST_DELIVERED_AT is not None


def test_tick_attempts_a_half_configured_sink(monkeypatch, tmp_path):
    """The P1 fix: #3677's shape must be OBSERVABLE by the absence half.

    Gating the canary on ``configured`` would skip here, the heartbeat would
    never advance, and the driver would read ``intended=false`` and stay
    silent — the absence half blind to the very incident that created #3820.
    """
    _half_env(monkeypatch)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))
    _stub_http(monkeypatch, status=201)

    attempted = asyncio.run(ha._analytics_canary_tick())

    assert attempted is True, "a half-configured sink must still be probed"
    assert ha._ANALYTICS_CANARY_ATTEMPTS == 1
    assert not _StubClient.posts, "half-configured never reaches Supabase"
    rows = [json.loads(ln) for ln in fallback.read_text().splitlines()]
    assert [r["event_name"] for r in rows] == [ha._ANALYTICS_CANARY_EVENT]
    # And the heartbeat is NOT refreshed — the write degraded.
    assert ha._ANALYTICS_LAST_DELIVERED_AT is None


def test_tick_counts_an_attempt_but_does_not_refresh_on_degradation(monkeypatch, tmp_path):
    """A dead sink: attempted, never delivered. ``canary_attempts`` advancing
    while ``age_s`` grows is the dead-SINK signature (vs a dead instrument)."""
    _prod_env(monkeypatch)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(tmp_path / "analytics_fallback.jsonl"))
    _stub_http(monkeypatch, status=500)  # httpx does not raise on a 5xx

    attempted = asyncio.run(ha._analytics_canary_tick())

    assert attempted is True
    assert ha._ANALYTICS_CANARY_ATTEMPTS == 1
    assert ha._ANALYTICS_LAST_DELIVERED_AT is None, (
        "a last-ATTEMPT stamp would stay fresh while every write fails — the "
        "fail-open shape this heartbeat exists to avoid"
    )


def test_heartbeat_refreshes_on_any_delivered_write_not_only_the_canary(monkeypatch):
    """A real funnel write proves the sink as well as a canary does — the
    canary supplies the CADENCE, not the only evidence."""
    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)

    assert ha._track_analytics_event("org-1", "artifact_copied") == "supabase"
    assert ha._ANALYTICS_LAST_DELIVERED_AT is not None


def test_canary_loop_does_not_emit_before_its_first_period(monkeypatch):
    """Sleep-first is load-bearing: the canary must not emit at boot.

    A LONG period plus a few real scheduler yields is the discriminating read —
    the correct loop is parked in its first sleep and cannot have ticked, while
    a loop that ticks before sleeping has already emitted. (Asserting only that
    ``ensure_future`` has not run yet would be vacuous: the coroutine is not
    scheduled until the test first yields.) Found by the mutation proof — the
    weaker form of this test passed under a tick-before-sleep mutant.
    """
    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    monkeypatch.setattr(ha, "_ANALYTICS_CANARY_PERIOD_S", 30)

    async def _scenario():
        task = asyncio.ensure_future(ha._analytics_canary_loop())
        for _ in range(5):
            # Let the coroutine run up to its first suspension point.
            await asyncio.sleep(0.01)
        assert ha._ANALYTICS_CANARY_ATTEMPTS == 0, (
            "the canary emitted before its first period"
        )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())


def test_canary_loop_ticks_after_its_period(monkeypatch):
    """The loop is wired: with a tiny period it emits, and the ticks reach the
    real sink path."""
    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    monkeypatch.setattr(ha, "_ANALYTICS_CANARY_PERIOD_S", 0.01)

    async def _scenario():
        task = asyncio.ensure_future(ha._analytics_canary_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())
    assert ha._ANALYTICS_CANARY_ATTEMPTS >= 1
    assert _canary_posts(), "the ticks reached the real sink path"


# ── the published heartbeat ─────────────────────────────────────────────────


def test_heartbeat_block_shape(monkeypatch):
    """The exact block the external driver reads, with the threshold DERIVED
    from the period where the period lives."""
    _prod_env(monkeypatch)
    monkeypatch.setattr(ha, "_ANALYTICS_CANARY_PERIOD_S", 300)
    monkeypatch.setattr(ha, "_ANALYTICS_CANARY_ATTEMPTS", 2)
    monkeypatch.setattr(ha, "_ANALYTICS_BOOT_AT", datetime.now(UTC) - timedelta(seconds=1000))
    monkeypatch.setattr(
        ha, "_ANALYTICS_LAST_DELIVERED_AT", datetime.now(UTC) - timedelta(seconds=100)
    )

    block = ha._analytics_heartbeat_block()

    assert block["configured"] is True
    assert block["intended"] is True
    assert block["canary_period_s"] == 300
    assert block["silent_threshold_s"] == 900, "Period + Grace = 3x the period"
    assert 995 <= block["uptime_s"] <= 1005
    assert 95 <= block["age_s"] <= 105
    assert block["canary_attempts"] == 2
    assert block["last_delivered_at"] is not None
    assert set(block["outcomes"]) == set(ha._ANALYTICS_OUTCOMES)
    # No secret ever rides the block.
    blob = json.dumps(block)
    assert PROD_URL not in blob and PROD_KEY not in blob


def test_heartbeat_block_is_cold_start_safe(monkeypatch):
    """No delivered write YET is ``age_s=None`` (a fabricated boot seed would
    be a last-attempt stamp) — the driver disambiguates with ``uptime_s``."""
    _prod_env(monkeypatch)
    monkeypatch.setattr(ha, "_ANALYTICS_LAST_DELIVERED_AT", None)
    monkeypatch.setattr(ha, "_ANALYTICS_BOOT_AT", datetime.now(UTC) - timedelta(seconds=5))

    block = ha._analytics_heartbeat_block()

    assert block["age_s"] is None
    assert block["last_delivered_at"] is None
    assert block["uptime_s"] < block["silent_threshold_s"]


def test_heartbeat_block_reports_a_half_configured_sink_as_intended(monkeypatch):
    _half_env(monkeypatch)
    monkeypatch.setattr(ha, "_ANALYTICS_LAST_DELIVERED_AT", None)

    block = ha._analytics_heartbeat_block()

    assert block["configured"] is False
    assert block["intended"] is True


# ── the surface the external driver reads ───────────────────────────────────


def _status(**env):
    import os

    from fastapi.testclient import TestClient

    old = {k: os.environ.get(k) for k in env}
    os.environ["FASTAPI_INTERNAL_KEY"] = "test-internal-3944"
    os.environ.update({k: v for k, v in env.items() if v is not None})
    try:
        # NOT a context manager: no lifespan, no graph DB — this only renders
        # the /status body.
        resp = TestClient(ha.app).get(
            "/v1/internal/backups/status",
            headers={"Authorization": "Bearer test-internal-3944"},
        )
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return resp


def test_status_carries_the_analytics_heartbeat_on_the_degraded_path(monkeypatch, tmp_path):
    """The heartbeat must survive an unusable object store: it is in-process
    state, and a storage blip must not blind the sink-absence alarm."""
    _prod_env(monkeypatch)
    monkeypatch.setattr(
        ha, "_ANALYTICS_LAST_DELIVERED_AT", datetime.now(UTC) - timedelta(seconds=42)
    )
    # No R2 config → _backup_storage() raises → the early-return path.

    resp = _status()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "analytics" in body, "the analytics block must be on the storage-error return too"
    block = body["analytics"]
    assert block["configured"] is True
    assert 41 <= block["age_s"] <= 43


# ── the production-caller residual, pinned statically ───────────────────────


def test_production_emission_lanes_still_reach_the_sink_entry_point():
    """The runtime heartbeat can prove the WRITE PATH, but not that a
    production CALL SITE still exists: there is no expected emission cadence to
    compare against, and the canary would mask a deleted caller. That residual
    is therefore pinned at PR time — this fails if a refactor removes the
    production lanes and leaves only the canary.

    Two independent claims, because they fail differently:

    1. The lane FUNCTION's body still calls the sink entry point (AST, below).
       This catches the call being deleted from a surviving lane.
    2. A handler lane is still a live endpoint on ``ha.app`` (below). Claim 1
       alone does not cover a deleted *route*: the body keeps its call while no
       production traffic can reach it, the canary keeps the heartbeat fresh,
       and the absence half stays green over a lane that no longer runs.

    What is still NOT asserted: that a helper lane (`_track_onboarding_event`)
    is itself referenced by a handler, and that an endpoint's route PATH is
    unchanged. See the module docstring's residual note.

    Adding a lane means adding it here (deliberate friction: the set is the
    reviewable record of where analytics is emitted from).
    """
    tree = ast.parse(HOSTED_API.read_text(), filename="hosted_api.py")
    # Lanes whose own name is the endpoint function name — claim 2 applies to
    # these; the helper lanes have no route of their own.
    handler_lanes = {
        "patch_onboarding_state",
        "github_callback",
        "webhooks_stripe",
    }
    expected_lanes = {
        # the funnel lanes (see _emit_analytics_off_loop's docstring)
        "_track_onboarding_event",
        "patch_onboarding_state",
        "github_callback",
        # the billing lane
        "webhooks_stripe",
        # #3944's own probe
        "_analytics_canary_tick",
    }
    found: set[str] = set()
    stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_emit_analytics_off_loop":
                found.add(stack[-1] if stack else "?")
            self.generic_visit(node)

    _Visitor().visit(tree)
    missing = expected_lanes - found
    assert not missing, (
        f"production analytics emission lane(s) {sorted(missing)} no longer "
        f"call _emit_analytics_off_loop — the sink-absence heartbeat cannot "
        f"detect that at runtime (#3944); got {sorted(found)}"
    )

    # Claim 2 — the handler lanes are still registered endpoints.
    registered = {
        getattr(route, "endpoint", None).__name__
        for route in ha.app.routes
        if getattr(route, "endpoint", None) is not None
    }
    unreachable = handler_lanes - registered
    assert not unreachable, (
        f"handler lane(s) {sorted(unreachable)} still call the sink but are no "
        f"longer registered on the app — no production traffic reaches them, "
        f"so the canary would mask the dead lane (#3944)"
    )
