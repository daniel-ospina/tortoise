"""Hermetic tests for `tools/engine_probe.py` (#7017).

No network, no docker: every probe is injected. Two things are pinned here, and
the first is the whole reason the tool exists:

  * **the inversion** — a WEDGED ENGINE must NOT be reported as `GRAPH_DOWN`.
    The graph's state is unmeasurable in that case, and reporting it as a graph
    failure sends the diagnosis somewhere the problem is not (the failure this
    tool was filed to remove: #4844 on 2026-09-29, #7017 on 2026-10-03);
  * **every probe is bounded** — a probe that can hang is not a probe. On
    2026-10-03 a single unbounded `docker ps` cost 900s.

EVERY TEST STATES, IN ITS DOCSTRING, (a) THE EXACT VALUE/STATE THAT MAKES IT
FAIL and (b) WHY THAT STATE IS REACHABLE.
"""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import engine_probe as e  # noqa: E402


# ==========================================================================
# THE INVERSION — a wedged engine is not a graph failure
# ==========================================================================

def test_a_wedged_engine_is_reported_as_wedged_not_as_graph_down():
    """(a) FAILS if `classify` checks the graph first, or maps a wedged engine
    onto GRAPH_DOWN — the exact misdiagnosis this tool exists to prevent.
    (b) Reachable: 2026-10-03, `docker ps` hung to its 900s bound while the
    graph socket read timed out; every instinct reads that as "graph down".
    """
    verdict = e.classify(
        {"ok": False, "error": "timeout", "elapsed_s": 3.0},
        {"status": e.ENGINE_WEDGED, "detail": "no answer in 3s", "elapsed_s": 3.0},
    )
    assert verdict == e.ENGINE_WEDGED
    assert verdict != e.GRAPH_DOWN


def test_a_reachable_graph_with_a_wedged_engine_is_still_wedged():
    """(a) FAILS if a healthy graph short-circuits the engine verdict. The
    engine being wedged is a fact about the MACHINE, not about this graph.
    (b) Reachable: the engine can wedge while an already-open graph connection
    still answers one more command.
    """
    verdict = e.classify(
        {"ok": True, "reply": "+PONG", "error": None, "elapsed_s": 0.01},
        {"status": e.ENGINE_WEDGED, "detail": "no answer in 3s", "elapsed_s": 3.0},
    )
    assert verdict == e.ENGINE_WEDGED


def test_an_unreachable_graph_with_a_healthy_engine_is_graph_down():
    """(a) FAILS if every failure is blamed on the engine, which would make the
    tool useless for its second purpose (naming a real graph outage).
    (b) Reachable: the graph container is stopped while docker answers fine.
    """
    verdict = e.classify(
        {"ok": False, "error": "ConnectionRefusedError", "elapsed_s": 0.01},
        {"status": e.ENGINE_OK, "detail": "27.0", "elapsed_s": 0.2},
    )
    assert verdict == e.GRAPH_DOWN


def test_a_reachable_graph_with_a_healthy_engine_is_graph_up():
    """(a) FAILS if the happy path is not reachable at all.
    (b) Reachable: the normal case.
    """
    verdict = e.classify(
        {"ok": True, "reply": "+PONG", "error": None, "elapsed_s": 0.01},
        {"status": e.ENGINE_OK, "detail": "27.0", "elapsed_s": 0.2},
    )
    assert verdict == e.GRAPH_UP


def test_skipping_the_engine_probe_never_reports_wedged():
    """(a) FAILS if `engine=None` (--no-docker) is treated as wedged, which
    would break the one mode that works without a container engine (embedded /
    remote use).
    (b) Reachable: `main(--no-docker)` passes engine=None explicitly.
    """
    assert e.classify({"ok": True, "reply": "+PONG", "error": None,
                       "elapsed_s": 0.01}, None) == e.GRAPH_UP
    assert e.classify({"ok": False, "error": "timeout",
                       "elapsed_s": 3.0}, None) == e.GRAPH_DOWN


# ==========================================================================
# BOUNDEDNESS — a probe that can hang is not a probe
# ==========================================================================

def test_probe_graph_refuses_quickly_on_a_closed_port():
    """(a) FAILS if `probe_graph` raises instead of returning a result, or
    exceeds its bound.
    (b) Reachable: any unused loopback port. Bind then close to get one that is
    almost certainly free without racing a real service.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    started = __import__("time").monotonic()
    result = e.probe_graph("127.0.0.1", port, timeout=1.0)
    elapsed = __import__("time").monotonic() - started
    assert result["ok"] is False
    assert result["error"] is not None
    assert elapsed < 3.0, f"probe exceeded its bound: {elapsed:.2f}s"


def test_probe_graph_reports_a_real_pong(monkeypatch):
    """(a) FAILS if a successful read is not read as success — the happy path
    must be reachable, or the tool can never say GRAPH_UP.
    (b) Reachable: a tiny in-process TCP server that answers PING with PONG.
    """
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        try:
            conn, _ = server.accept()
            with conn:
                if b"PING" in conn.recv(64):
                    conn.sendall(b"+PONG\r\n")
        except OSError:
            pass
        finally:
            server.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    result = e.probe_graph("127.0.0.1", port, timeout=2.0)
    t.join(timeout=2.0)
    assert result["ok"] is True, result
    assert result["reply"] == "+PONG"


def test_probe_engine_treats_a_slow_answer_as_wedged(monkeypatch):
    """(a) FAILS if the subprocess timeout is not applied — without it this
    call blocks for the full kill interval when the control plane is wedged,
    which is the 900s burn the tool removes.
    (b) Reachable: patch subprocess.run to raise TimeoutExpired, which is what
    a wedged engine produces.
    """
    import subprocess as sp

    def boom(*_a, **_k):
        raise sp.TimeoutExpired(cmd="docker version", timeout=3.0)

    monkeypatch.setattr(e.subprocess, "run", boom)
    result = e.probe_engine(3.0)
    assert result["status"] == e.ENGINE_WEDGED


def test_probe_engine_reports_a_missing_cli_as_absent_not_wedged(monkeypatch):
    """(a) FAILS if FileNotFoundError is folded into ENGINE_WEDGED — a machine
    without the docker CLI is not a wedged engine, and reporting it as one
    would send a lane to restart something that is not running.
    (b) Reachable: any host with no docker binary.
    """
    def boom(*_a, **_k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(e.subprocess, "run", boom)
    assert e.probe_engine(3.0)["status"] == e.ENGINE_ABSENT


def test_probe_engine_reports_a_nonzero_rc_as_wedged(monkeypatch):
    """(a) FAILS if `returncode != 0` is read as success. A CLI that answers
    with an error is not evidence the engine is answering.
    (b) Reachable: `docker version` against a dead daemon exits nonzero.
    """
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Cannot connect to the Docker daemon"

    monkeypatch.setattr(e.subprocess, "run", lambda *a, **k: Proc())
    assert e.probe_engine(3.0)["status"] == e.ENGINE_WEDGED


# ==========================================================================
# CLI contract
# ==========================================================================

def test_main_exits_two_when_the_graph_is_unreachable(monkeypatch):
    """(a) FAILS if a broken substrate exits 0, which would let a caller gate
    on the exit code and be told everything is fine.
    (b) Reachable: point at a closed port with --no-docker.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert e.main(["--no-docker", "--port", str(port), "--timeout", "1"]) == 2


def test_main_rejects_a_nonpositive_timeout():
    """(a) FAILS if --timeout 0 is accepted: a zero bound turns every probe
    into an instant false failure, which would report a healthy graph as down.
    (b) Reachable: `--timeout 0`.
    """
    assert e.main(["--timeout", "0"]) == 1


def test_redis_port_comes_from_the_uri_when_present(monkeypatch):
    """(a) FAILS if the configured port is ignored, so the tool probes 16379
    while the lane's graph is elsewhere and reports a false GRAPH_DOWN.
    (b) Reachable: TORTOISE_DB_URI pointing at a non-default port.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16400/tortoise")
    assert e.redis_port_from_env() == 16400
    monkeypatch.setenv("TORTOISE_DB_URI", "falkor:///tmp/x")
    assert e.redis_port_from_env() is None
