"""Dedicated perf lane — the wall-clock SLO for ``POST /v1/context`` (#3992).

WHY THIS FILE EXISTS
====================
The #2080 plan names a *dedicated perf lane* as the owner of the hard
wall-clock latency assertion, and pins the measurement METHOD — and until
#3992 nothing implemented it. The identical reference still stands in
``tortoise/hosted_api.py`` at the ``TORTOISE_VOLUNTEER_ENFORCE_SLO`` arming:

    ``TORTOISE_VOLUNTEER_ENFORCE_SLO`` — perf lane / induced-timeout tests only.

*Here is that lane.* The claim and the machinery now agree, and this module is
the thing the code comment names.

THE PINNED METHOD (not invented here)
=====================================
``docs/planning/2026-09-01-2080-gbrain-plan.md`` §E2E-9 step 6 — "Latency
(specified method)" (the plan's line numbers rot; cite the anchor, not the
number):

  * **warm-up discipline** — discard the first N warm requests (connection
    pool / embedding cache);
  * **a FIXED request-mix table** — window size x contested/superseded/clean
    share;
  * **measurement boundary** — client-observed elapsed for a keep-alive HTTP
    round-trip, EXCLUDING setup;
  * **stability rule** — median of 3 runs of 50; assert p95 <= 300 ms.

This module is those four rules, one-to-one: ``_WARMUP``,
``_REQUEST_MIX``, the real uvicorn server + one reused ``httpx.Client`` (a
real socket with keep-alive, never an in-process ASGI transport), and
``_RUNS`` x ``_REQUESTS_PER_RUN`` reduced by ``statistics.median``.

WHY 3 x 50 AND NOT N >= 200 IN ONE RUN
--------------------------------------
The plan offers both ("median of 3 runs of 50 (or N >= 200)"). The 3-run form
is the stronger measurement — it can distinguish a stable p95 from one carried
by a single slow run — so it is the form implemented. ``_REQUESTS_PER_RUN`` is
derived from ``_REQUEST_MIX`` and pinned by an assertion, so the table cannot
silently stop summing to 50.

WHY A REAL SERVER, NOT TestClient
---------------------------------
``fastapi.testclient.TestClient`` drives the app through an in-process ASGI
transport: no socket, no connection reuse. The pinned boundary is a
*client-observed keep-alive HTTP round-trip*, so the lane boots the real
``tortoise.hosted_api.app`` under uvicorn on a loopback port and measures with
one reused ``httpx.Client``. Boot cost is setup and is therefore excluded —
the clock starts per request, after the warm-up.

THE DENSE LEG IS REQUIRED — FAIL CLOSED, NEVER MEASURE FTS-ONLY
---------------------------------------------------------------
The product's read path is HYBRID (dense + keyword). An unavailable embedder
grades it to keyword-only retrieval, which does LESS work and would pass a
latency assertion VACUOUSLY — a cheaper path wearing the product's label. The
lane therefore calls the repo's own gate
(``battery/runner/retrieval_preflight.require_hybrid_retrieval``, #2985)
BEFORE measuring, so the number always describes the hybrid read. The workflow
provisions the pinned model first (``tools/embedder_provision.py``); the
manual invocation below therefore sets ``HF_HUB_OFFLINE=1`` /
``TRANSFORMERS_OFFLINE=1`` (a warm local cache).

THE SLO IS A RECORDED DECISION, AND THE LANE PINS IT
----------------------------------------------------
``tortoise.volunteer.SLO_MS`` is the single source of the threshold. The lane
asserts the code still records 300 ms (``test_slo_is_still_the_recorded_300ms``)
AND measures against it, so a silent move of the constant fails here instead of
re-baselining the lane under it.

FAIL-OPEN IS THE CONTRACT — NEVER 503
-------------------------------------
A breach must stay ``degraded_response(DEGRADED_TIMEOUT)`` (HTTP 200, empty
block, ``degraded_reason='timeout'``) — never a 503
(``tortoise/hosted_api.py``, the SLO-breach branch). The lane's measured arm
therefore also asserts every measured response is 200 with
``degraded_reason is None``: a degraded response does almost no work, so
without that guard a systematically-degrading endpoint would *pass* a latency
test vacuously. The induced-breach arm
(``test_slo_breach_degrades_timeout_never_503``) pins the fail-open SHAPE at
the same threshold.

BACKEND
=======
Embedded carve-out (``TORTOISE_TEST_CARVE_OUT=1``), the lane the epic's E2E
legs ran on. This file is registered in ``config/ci-surfaces.yml``'s
``on_demand:`` lane — it runs from ``.github/workflows/perf-lane.yml`` and
never gates a merge (the plan: "generously margined, retry-tolerant";
blocking CI keeps the MECHANISM assertions).

DATED MANUAL INVOCATION (acceptance criterion 4)
------------------------------------------------
    TORTOISE_DB_URI= TORTOISE_TEST_CARVE_OUT=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m pytest tests/test_perf_lane_volunteer_context.py -q -s -p no:cacheprovider

(``TORTOISE_DB_URI=`` is required: the repo conftest refuses any run aimed at
the canonical fleet store. The offline pair keeps the pinned embedder on the
local cache; if it cannot load, the lane FAILS at the dense-leg gate rather
than measuring keyword-only.) Recorded 2026-10-14. The workflow is
``.github/workflows/perf-lane.yml`` (``workflow_dispatch`` + nightly).
"""
from __future__ import annotations

import math
import os
import statistics
import tempfile
import threading
import time
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import httpx
import pytest

from battery.runner.retrieval_preflight import require_hybrid_retrieval
from tortoise.volunteer import SLO_MS

# Plan: "median of 3 runs of 50". Run count and per-run request count are the
# stability rule; the mix below is the request-shape half.
_RUNS = 3
_REQUESTS_PER_RUN = 50
_WARMUP = 10

# The FIXED request-mix table (plan: "window size x contested/superseded/clean
# share"). Rows sum to _REQUESTS_PER_RUN; the values are the mix, deliberately
# not randomized — a fixed mix is what makes two runs comparable.
_REQUEST_MIX: tuple[tuple[int, str, int], ...] = (
    # (window turns, retrieval target, requests per run)
    (1, "contested", 8),
    (1, "superseded", 5),
    (1, "clean", 5),
    (2, "contested", 8),
    (2, "superseded", 5),
    (2, "clean", 4),
    (3, "contested", 6),
    (3, "superseded", 5),
    (3, "clean", 4),
)

# Distinctive topics so retrieval is deterministic and the three classes stay
# disjoint (a retrieval that finds nothing does less work and would understate
# the measurement).
_TOPIC = {
    "contested": "Zephyrgate rollout",
    "superseded": "Harborlight allocation",
    "clean": "Orion migration",
}

_ORG_ID = "perf-org"


def _p95(samples: list[float]) -> float:
    """Nearest-rank p95 (the plan's percentile, no interpolation)."""
    ordered = sorted(samples)
    k = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[k]


def _set_posterior(sdk, point_id: str, alpha: float, beta: float) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
        "n.posterior_alpha=$a, n.posterior_beta=$b",
        params={"id": point_id, "a": alpha, "b": beta,
                "c": round(alpha / (alpha + beta), 4)})


def _plant_contested(sdk, topic: str) -> str:
    """A conflicted claim (IMPL support + NAND counter, variance > threshold)."""
    ev = sdk.create_point("evidence", f"{topic} [supporting record]")
    claim = sdk.create_point("statement", topic)
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    counter = sdk.create_point("statement", f"{topic} [contradicting report]")
    sdk.create_operator("NAND", counter["id"], [claim["id"]])
    _set_posterior(sdk, claim["id"], 2.0, 0.7)
    _set_posterior(sdk, ev["id"], 12.0, 1.0)
    _set_posterior(sdk, counter["id"], 10.0, 1.0)
    return claim["id"]


def _plant_superseded(sdk, topic: str) -> str:
    """A superseded predecessor: conflicted structure + CORRECTS + status."""
    old = _plant_contested(sdk, topic)
    successor = sdk.create_point("statement", f"{topic} [corrected belief]")
    _set_posterior(sdk, successor["id"], 10.0, 1.0)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
        "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
        "n.outdated=true, n.validTo='2026-06-01'",
        params={"old": old, "new": successor["id"]})
    return old


def _plant_clean(sdk, topic: str) -> str:
    """A supported, unconflicted claim."""
    ev = sdk.create_point("evidence", f"{topic} [supporting record]")
    claim = sdk.create_point("statement", topic)
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    _set_posterior(sdk, claim["id"], 9.0, 1.0)
    _set_posterior(sdk, ev["id"], 12.0, 1.0)
    return claim["id"]


def _window(target: str, turns: int) -> list[dict]:
    """A turn window of ``turns`` user turns naming ``target``.

    The extra turns are the window-size half of the mix; they are real
    conversation turns, not padding, so the endpoint assembles the same shape
    it serves in production.
    """
    topic = _TOPIC[target]
    msgs = [{"role": "user",
             "content": f"What is the current status of the {topic}?"}]
    for i in range(max(0, turns - 1)):
        msgs.append({"role": "assistant",
                     "content": f"Checking the graph record ({i + 1})."})
        msgs.append({"role": "user",
                     "content": f"Also, what changed about the {topic}?"})
    return msgs


def _request_plan() -> list[tuple[int, str]]:
    """Expand `_REQUEST_MIX` into one request shape per measured request."""
    plan: list[tuple[int, str]] = []
    for turns, target, count in _REQUEST_MIX:
        plan.extend([(turns, target)] * count)
    return plan


@pytest.fixture(scope="module")
def perf_server():
    """The REAL ``hosted_api.app`` on a loopback uvicorn port (keep-alive).

    The dependency override is the suite's standard auth bypass seam
    (``get_current_org_gated`` honours an override on ``get_current_org``);
    the SDK is pinned to a temp embedded store exactly as
    ``tests/test_hosted_volunteer_context.py`` pins it.
    """
    import uvicorn

    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app, get_current_org

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "perf-lane.db")
        orig_init = ha_mod.TortoiseSDK.__init__

        def _patched_init(self, db_path_arg=None, *, namespace=None,
                          graph_name=None, **kwargs):
            orig_init(self, db_path, namespace=namespace,
                      graph_name=graph_name)

        ha_mod.TortoiseSDK.__init__ = _patched_init
        os.environ["TORTOISE_DB_PATH"] = db_path
        ha_mod._FALLBACK_KEEPALIVE.clear()
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": _ORG_ID, "tier": "free", "key_id": None}

        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 60.0
            while (not (server.started and server.servers)
                   and time.monotonic() < deadline):
                time.sleep(0.05)
            assert server.started, "the hosted app never started"
            port = server.servers[0].sockets[0].getsockname()[1]
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            app.dependency_overrides.clear()
            ha_mod.TortoiseSDK.__init__ = orig_init
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod._FALLBACK_KEEPALIVE.clear()


@pytest.fixture(scope="module")
def seeded(perf_server):
    """Seed the disjoint contested / superseded / clean corpus once."""
    import tortoise.hosted_api as ha_mod

    sdk = ha_mod._make_sdk(namespace=_ORG_ID)
    planted = {
        "contested": _plant_contested(sdk, _TOPIC["contested"]),
        "superseded": _plant_superseded(sdk, _TOPIC["superseded"]),
        "clean": _plant_clean(sdk, _TOPIC["clean"]),
    }
    assert all(planted.values())
    return planted


# ── the lane ───────────────────────────────────────────────────────────────

def test_slo_is_still_the_recorded_300ms():
    """The plan's threshold is a recorded decision — pin it against drift.

    If this fails, the SLO was moved: re-derive the lane's assertion from the
    decision, do not silently re-baseline it to the new constant.
    """
    assert SLO_MS == 300, (
        f"tortoise.volunteer.SLO_MS is {SLO_MS} ms, not the recorded 300 ms. "
        "The plan (#2080 E2E-9) pins 300; a breach still degrades fail-open.")


def test_request_mix_sums_to_the_pinned_per_run_count():
    """The mix table IS the measurement envelope — it cannot silently shrink."""
    total = sum(count for _, _, count in _REQUEST_MIX)
    assert total == _REQUESTS_PER_RUN, (
        f"request mix sums to {total}, expected {_REQUESTS_PER_RUN}")


@pytest.mark.timeout(900)
def test_v1_context_p95_within_slo(perf_server, seeded):
    """Median-of-3-runs p95 <= SLO_MS over the fixed request mix (#3992)."""
    plan = _request_plan()
    base_url = perf_server

    # Fail closed on the dense leg FIRST: keyword-only retrieval does less
    # work, so measuring it would pass this assertion vacuously. This also
    # loads the model as SETUP (excluded from the measurement), not inside the
    # first measured request.
    require_hybrid_retrieval()

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        def _post(turns: int, target: str) -> float:
            payload = {
                "window": _window(target, turns),
                "session_id": f"perf-{uuid.uuid4().hex}",
                "why": True,
            }
            t0 = time.perf_counter()
            r = client.post("/v1/context", json=payload)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert r.status_code == 200, r.text
            body = r.json()
            # A degraded response does almost no work: without this the lane
            # would pass vacuously on a systematically-degrading endpoint.
            assert body["degraded_reason"] is None, (
                f"measured request degraded ({body['degraded_reason']}) — the "
                "latency sample is not a real read")
            assert body["pointers"], (
                f"retrieval found nothing for {target!r} — the sample understates "
                "the real read path")
            return elapsed_ms

        # Warm-up discipline: discard the first N (connection pool / cache).
        for turns, target in plan[:_WARMUP]:
            _post(turns, target)

        run_p95s: list[float] = []
        for _ in range(_RUNS):
            samples = [_post(turns, target) for turns, target in plan]
            assert len(samples) == _REQUESTS_PER_RUN
            run_p95s.append(_p95(samples))

    measured = statistics.median(run_p95s)
    # Report the measurement: a perf lane whose number appears only on failure
    # cannot be read as evidence. `-s` (the workflow's flag) surfaces this.
    print(f"[perf-lane] run p95s={[round(p, 2) for p in run_p95s]} ms "
          f"median={measured:.2f} ms SLO={SLO_MS} ms "
          f"({_REQUESTS_PER_RUN} req/run, {_RUNS} runs, "
          f"{_WARMUP} warm-up discarded)")
    assert measured <= SLO_MS, (
        f"POST /v1/context p95 {measured:.1f} ms > SLO {SLO_MS} ms "
        f"(run p95s: {[round(p, 1) for p in run_p95s]})")


def test_slo_breach_degrades_timeout_never_503(perf_server, monkeypatch):
    """A breach stays fail-open: 200 + ``degraded_reason='timeout'``, never 503.

    Acceptance criterion 3 (#3992): the lane must not assert the opposite of
    the endpoint's fail-open design. The breach is INDUCED (a slow pipeline +
    the ``TORTOISE_VOLUNTEER_ENFORCE_SLO`` arming the code comment names), so
    the assertion is about the SHAPE at the same threshold, not a flaky
    machine.
    """
    import tortoise.hosted_api as ha_mod

    monkeypatch.setenv("TORTOISE_VOLUNTEER_ENFORCE_SLO", "1")
    orig = ha_mod.TortoiseSDK.volunteer_context

    def _slow(self, window, *args, **kwargs):
        time.sleep(SLO_MS / 1000.0 + 0.1)  # breach the SLO on the worker thread
        return orig(self, window, *args, **kwargs)

    monkeypatch.setattr(ha_mod.TortoiseSDK, "volunteer_context", _slow)

    r = httpx.post(
        f"{perf_server}/v1/context", timeout=30.0,
        json={"window": _window("contested", 1),
              "session_id": f"perf-breach-{uuid.uuid4().hex}", "why": True})

    assert r.status_code == 200, r.text  # fail-open, NEVER 503
    body = r.json()
    assert body["degraded_reason"] == "timeout"
    assert body["pointers"] == []
    assert body["block"] == ""
