"""The shipped `tortoise-client status` probe's exit-code contract (#3832 / D5).

The LIBRARY keeps never raising — `tortoise.mcp_client.status()` still returns
the payload so script callers skip cleanly. The CLI **probe** reports a distinct
process exit code, so a degraded probe cannot look like success to a human or
an agent harness:

    available -> 0 · empty -> 0 · degraded (can't reach it) -> 3
    unconfigured (not set up) -> 4
    1 stays a query/tool call that genuinely fails · 2 is argparse's

This supersedes #526's exit-0 clause **for the CLI probe only**.

#3805 LANDED THE STATUS WORDS AS THE RECORDED TERM SET. The pins below changed
from `ok` / `tortoise_unavailable` / `not_configured` to the four recorded terms
(roadmap §7 item 9: `available | empty | degraded | unconfigured`) because the
CONTRACT changed — the pre-#3805 words are translated by
`tortoise.status_vocabulary`, never asserted. Per #3832's own rule the pins were
NOT widened to accept both spellings: a pin that accepts both cannot catch the
regression it exists to catch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIR = REPO_ROOT / "client"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Bind the CANONICAL driver FIRST, then load the client shim from `client/`.
# Inserting `client/` shadows the engine `tortoise` package while it is on the
# path (the client dir carries its own `tortoise/` namespace), so the driver has
# to be cached in sys.modules first — mirrors tests/test_client_surface.py. The
# path entry is REMOVED again so no later test in the same pytest session sees
# the client-only `tortoise`. (Without this, a sibling module that imported
# `tortoise.tortoise_client` first leaves `tortoise/` on sys.path and
# `import tortoise_client` binds that FILE instead of the client package.)
import tortoise.mcp_client  # noqa: E402, F401

sys.path.insert(0, str(CLIENT_DIR))
try:
    from tortoise_client import cli
finally:
    sys.path.remove(str(CLIENT_DIR))


def _fake_status(word: str, **extra):
    return lambda: {"status": word, "url": "http://localhost:8000/mcp", **extra}


def test_status_ok_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "status", _fake_status("ok", tools=7))
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "available"


def test_status_unreachable_exits_three(monkeypatch, capsys):
    """A configured-but-down endpoint is the can't-reach-it case: the recorded
    term is `degraded` (#3832 exit code 3, preserved verbatim)."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
    monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
    assert cli.main(["status"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "degraded"


def test_status_not_configured_exits_four(monkeypatch, capsys):
    """No endpoint configured + an unreachable probe is a SET-UP gap, not an
    outage; its recorded term is `unconfigured` (#3832 exit code 4, preserved).
    The driver is unchanged (it has no notion of a missing endpoint and falls
    back to the self-host default), so the probe splits the state out.

    On THIS probe 'TORTOISE_MCP_URL is unset' is the definition of
    unconfigured, and the rewritten payload must not contradict itself: the
    driver's default `url` is dropped and `configured: false` is added."""
    monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
    monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
    assert cli.main(["status"]) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unconfigured"
    assert out["configured"] is False
    assert out["url"] is None


def test_status_down_daemon_with_declared_default_stays_degraded(monkeypatch, capsys):
    """Boundary: declaring the endpoint - even as the bare default - is what
    moves a down daemon OUT of unconfigured and into degraded. The probe never
    guesses."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://localhost:8000/mcp")
    monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
    assert cli.main(["status"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "degraded"


def test_status_forwards_driver_not_configured(monkeypatch, capsys):
    """Forward-compat: a `not_configured` word from the driver maps to 4 and is
    never second-guessed by the endpoint heuristic; its term is `unconfigured`."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
    monkeypatch.setattr(cli, "status", _fake_status("not_configured"))
    assert cli.main(["status"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "unconfigured"


def test_call_failure_keeps_exit_one(monkeypatch, capsys):
    """1 is KEPT for a query that genuinely fails — the supersede is narrow."""

    def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr(cli, "call_tool", boom)
    assert cli.main(["call", "some_tool"]) == 1
    assert "call failed" in capsys.readouterr().err
