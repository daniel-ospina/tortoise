"""#2013 PRODUCT-GATING tests — the hosted ask EXPOSURE is off by default.

The reader (tortoise/reader.py) stays shipped — it is the eval's reader
(the LongMemEval benchmark runs through it; the eval re-exports the
product reader). The HOSTED ask EXPOSURE is gated: no /v1/ask route in
the served app unless ``TORTOISE_ENABLE_ASK=1`` (tests/dev). The route
handler + the path-scoped error translation stay in the codebase, tested,
ready — just not served to customers until the reader-model decision is
made (the benchmark will use a strong reader model).

Both states are verified in ONE SUBPROCESS: the route registration happens
at hosted_api import time on a module-level app, so a single pytest
session cannot observe both states on the shared app (test_ask_api.py
registers the route explicitly to exercise the full pipeline). The
subprocess imports hosted_api fresh per flag value — the exact production
behavior — and returns the verdicts for every flag value in one go.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)

# One subprocess, four flag values (unset / "1" / "0" / "true") — the
# strict ``== "1"`` parse is pinned on both the route and the default MCP
# surface. Test-session env vars (TORTOISE_TEST_MODE et al) are stripped so
# the child observes production-like conditions (mirrors test_mcp_server.py
# epic #1647 P0-4).
_GATING_PROBE = r"""
import importlib, json, os, sys
sys.path.insert(0, {root!r})
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

def verdict():
    import tortoise.hosted_api as ha
    registered = "/v1/ask" in [r.path for r in ha.app.routes]
    from fastapi.testclient import TestClient
    with TestClient(ha.app) as tc:
        r = tc.post("/v1/ask", json={{"question": "what is the office hours?"}})
    try:
        body = r.json()
    except Exception:
        body = None
    return {{"registered": registered, "status": r.status_code, "body": body}}

out = {{}}
# 1) flag unset (off)
out["unset"] = verdict()
# 2) flag "1" (on) — fresh import so the import-time gate re-evaluates
os.environ["TORTOISE_ENABLE_ASK"] = "1"
sys.modules.pop("tortoise.hosted_api", None)
out["1"] = verdict()
# 3) flag "0" (off — strict parse) + 4) flag "true" (off — strict parse)
for v in ("0", "true"):
    os.environ["TORTOISE_ENABLE_ASK"] = v
    sys.modules.pop("tortoise.hosted_api", None)
    out[v] = verdict()
print(json.dumps(out))
"""


def _probe() -> dict:
    """Run the 4-state gating probe in ONE fresh subprocess (production-like
    env, no test-session leaks) and return the per-flag verdicts."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("TORTOISE_TEST_")
    }
    env.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
    env.setdefault("RATE_LIMIT_DISABLED", "1")
    env.pop("TORTOISE_ENABLE_ASK", None)
    out = subprocess.run(
        [sys.executable, "-c", _GATING_PROBE.format(root=_REPO_ROOT)],
        capture_output=True, text=True, env=env, timeout=300,
    )
    if out.returncode != 0:
        raise AssertionError(
            f"gating probe subprocess failed (rc={out.returncode}):\n"
            f"{out.stderr[-2000:]}")
    for line in out.stdout.splitlines():
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON verdict in probe output:\n{out.stdout[-2000:]}")


@pytest.fixture(scope="module")
def gating_verdicts() -> dict:
    """One subprocess for the whole module (the probe is ~20s)."""
    return _probe()


def test_ask_route_404_when_exposure_off(gating_verdicts):
    """Default (flag unset): /v1/ask is NOT registered — the served app
    404s it (the path-scoped error translation falls through to the
    standard 404 body; no auth dependency ever runs)."""
    v = gating_verdicts["unset"]
    assert v["registered"] is False
    assert v["status"] == 404
    # FastAPI's default 404 body — NOT the canonical ask error shape
    assert v["body"] == {"detail": "Not Found"}


def test_ask_route_serves_when_exposure_on(gating_verdicts):
    """TORTOISE_ENABLE_ASK=1: /v1/ask IS registered and SERVES — a request
    without credentials reaches the auth dependency and returns the ask
    lane's canonical 401 ({"error": {"code": "unauthorized"}} via the
    path-scoped handler), proving the route is live (a 404 would return
    the default body)."""
    v = gating_verdicts["1"]
    assert v["registered"] is True
    assert v["status"] == 401
    assert v["body"] == {"error": {"code": "unauthorized"}}


@pytest.mark.parametrize("flag", ["0", "true"])
def test_ask_route_strict_flag_parse_off(gating_verdicts, flag):
    """The gate parses STRICTLY == \"1\" — \"0\" and \"true\" keep the
    route unregistered (a regression to truthy parsing must fail here)."""
    v = gating_verdicts[flag]
    assert v["registered"] is False
    assert v["status"] == 404
