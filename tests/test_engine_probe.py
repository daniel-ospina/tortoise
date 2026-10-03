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


def test_an_unprobed_or_malformed_engine_is_not_read_as_healthy():
    """(a) FAILS if `classify` enumerates only ENGINE_WEDGED and lets any other
    unknown status through to a confident verdict — the exact "an unmeasurable
    situation becomes a confident wrong answer" shape this tool removes. The
    allow-list treats everything that is not ENGINE_OK/ENGINE_ABSENT as wedged.
    (b) Reachable: a probe that grows a new status (ENGINE_UNPROBED exists in
    this module and is consumed by nothing), or an engine dict that lost its
    status key.
    """
    dead = {"ok": False, "error": "timeout", "elapsed_s": 3.0}
    for bad in ({}, {"detail": "no status key"},
                {"status": e.ENGINE_UNPROBED}, {"status": "SOMETHING_NEW"}):
        assert e.classify(dead, bad) == e.ENGINE_WEDGED, bad
    # and the two genuinely-known-good states still do not invert
    assert e.classify(dead, {"status": e.ENGINE_OK}) == e.GRAPH_DOWN
    assert e.classify(dead, {"status": e.ENGINE_ABSENT}) == e.GRAPH_DOWN


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


def test_probe_graph_bounds_a_silent_peer(monkeypatch):
    """(a) FAILS if the READ is not bounded — the defect the old closed-port
    test could not see, because a refused connect returns in ~0.5ms and never
    reaches the read at all. The bound here is consumed, not merely not
    exceeded: a peer that accepts and then says nothing must produce a timeout
    AT ~the bound.
    (b) Reachable: a wedged engine leaves the graph socket accepted and silent,
    which is exactly the 2026-10-03 shape.
    """
    import time

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    held = []

    def accept_and_say_nothing():
        try:
            conn, _ = server.accept()
            held.append(conn)      # keep it open, never write
            conn.recv(64)          # never reply
        except OSError:
            pass

    t = threading.Thread(target=accept_and_say_nothing, daemon=True)
    t.start()
    try:
        started = time.monotonic()
        result = e.probe_graph("127.0.0.1", port, timeout=1.0)
        elapsed = time.monotonic() - started
        assert result["ok"] is False, result
        assert result["error"] == "timeout", result
        # both ends: an unbounded read would hang (>=1.5 is the tell if it
        # somehow returned), and an unread fast-fail would be << 0.5.
        assert 0.5 <= elapsed < 1.5, f"bound not consumed: {elapsed:.3f}s"
    finally:
        for conn in held:
            conn.close()
        server.close()


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
    which is the 900s burn the tool removes. The fake ASSERTS the bound was
    passed: a fake that raises regardless of its kwargs would pass even with
    `timeout=` dropped from the call, which is the hole the first version of
    this test had.
    (b) Reachable: patch subprocess.run to raise TimeoutExpired, which is what
    a wedged engine produces.
    """
    import subprocess as sp

    seen = {}

    def boom(*_a, **kwargs):
        seen.update(kwargs)
        assert kwargs.get("timeout"), "probe_engine must pass a bound to docker"
        raise sp.TimeoutExpired(cmd="docker version", timeout=kwargs["timeout"])

    monkeypatch.setattr(e.subprocess, "run", boom)
    result = e.probe_engine(3.0)
    assert result["status"] == e.ENGINE_WEDGED
    assert seen["timeout"] == 3.0, seen


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


def test_redis_port_is_not_taken_from_a_colon_that_is_not_a_port(monkeypatch):
    """(a) FAILS with a `:`-split parser, which reads the last `1` of the IPv6
    host `::1` as a port: the tool would then probe port 1 and report a FALSE
    GRAPH_DOWN against a healthy graph. A malformed port must yield None (the
    caller's documented default) rather than a confident wrong number.
    (b) Reachable: an IPv6 target with no port, and a typo'd port.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@[::1]/tortoise")
    assert e.redis_port_from_env() is None
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16x00/tortoise")
    assert e.redis_port_from_env() is None
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16379")
    assert e.redis_port_from_env() == 16379


def test_the_engine_probe_tool_keeps_its_ci_carveout():
    """(a) FAILS if the `tools/engine_probe.py` TOOL_CARVEOUTS entry is dropped:
    `tools/` is in NON_PYTHON_PREFIXES, so an engine_probe-only change filters
    to `changed == []`, takes the docs-only early return, and this suite never
    runs on the PR that changes the tool (the #1349/#3332/#3616 silent-drop
    class). No SOURCE_PATTERNS entry matches the tool, so the carved-out path
    must fail CLOSED to the full matrix.
    (b) Reachable: it already happened once for this file — the review that
    added this test found the entry missing.
    """
    import ci_selection as cs

    assert "tools/engine_probe.py" in cs.TOOL_CARVEOUTS
    sel = cs.select(["tools/engine_probe.py"], "pull_request", cs.load_manifest())
    assert sel["full"], "an engine_probe-only diff must fail closed to the full matrix"
