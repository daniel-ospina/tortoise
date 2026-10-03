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
                {"status": "ENGINE_SOMETHING_NEW"}):
        assert e.classify(dead, bad) == e.ENGINE_WEDGED, bad
    # the genuinely-known-good states must NOT invert: a daemon that is down
    # fails fast, so the graph probe is still believable
    for good in (e.ENGINE_OK, e.ENGINE_ABSENT, e.ENGINE_DAEMON_DOWN):
        assert e.classify(dead, {"status": good}) == e.GRAPH_DOWN, good


def test_a_malformed_graph_probe_is_unmeasurable_not_graph_down():
    """(a) FAILS if the graph side is not allow-listed: a probe result without a
    real `ok` bool would become a confident GRAPH_DOWN, which is the engine-side
    mistake mirrored. The module consumes its own pure API this way, so the
    shape is reachable through any caller that builds a result dict.
    (b) Reachable: a future probe_graph that forgets `ok`, or a caller that
    passes a partial dict.
    """
    for bad in ({}, {"ok": None}, {"ok": "yes"}, {"reply": "+PONG"}):
        assert e.classify(bad, {"status": e.ENGINE_OK}) == e.UNMEASURABLE, bad
    # the real shapes still work, in both directions
    assert e.classify({"ok": True}, {"status": e.ENGINE_OK}) == e.GRAPH_UP
    assert e.classify({"ok": False}, {"status": e.ENGINE_OK}) == e.GRAPH_DOWN


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


def test_a_credentialed_graph_is_up_not_down(monkeypatch):
    """(a) FAILS if only `+PONG` counts as up. Redis answers a pre-AUTH PING with
    `-NOAUTH Authentication required.`, and this repo's canonical URI is
    credential-bearing (`.env.example`), so requiring PONG reported a REACHABLE
    graph as GRAPH_DOWN with the prose pointing at the graph container — the
    #4844/#7017 misdiagnosis inverted.
    (b) Reachable: the default developer config, and the behaviour is already
    documented in tests/test_restore_container_recovery.py.
    """
    server = _one_shot_server(b"-NOAUTH Authentication required.\r\n")
    port = server.getsockname()[1]
    result = e.probe_graph("127.0.0.1", port, timeout=2.0)
    assert result["ok"] is True, result
    assert result["reply"].startswith("-NOAUTH"), result
    assert e.classify(result, {"status": e.ENGINE_OK}) == e.GRAPH_UP


def test_a_non_redis_service_on_the_port_is_not_up(monkeypatch):
    """(a) FAILS if ANY reply is read as a reachable graph. The port-reachable
    test must not become "any bytes = up"; only the Redis protocol framing does.
    (b) Reachable: something else (an HTTP server, a proxy) bound on the port.
    """
    server = _one_shot_server(b"GET / HTTP/1.0\r\n\r\n")
    port = server.getsockname()[1]
    result = e.probe_graph("127.0.0.1", port, timeout=2.0)
    assert result["ok"] is False, result
    assert e.classify(result, {"status": e.ENGINE_OK}) == e.GRAPH_DOWN


def _one_shot_server(payload: bytes) -> socket.socket:
    """Accept one connection, reply `payload`, close. Returns the listening
    socket (its port is already bound)."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        try:
            conn, _ = server.accept()
            with conn:
                conn.recv(64)
                conn.sendall(payload)
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server


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


def test_the_connect_bound_is_passed_once_per_call(monkeypatch):
    """(a) FAILS if the CONNECT bound is dropped — the property the closed-port
    test cannot see, because a refused connect returns immediately on loopback.
    It matters for the blackholed-remote case `--timeout` exists to cover: the
    bound is the only thing between a dropped packet and a hang.
    (b) Reachable: removal of `timeout=timeout` from create_connection, which the
    sibling read-bound test does not detect.
    """
    seen = []
    real = socket.create_connection

    def spy(address, timeout=None, **kw):
        seen.append(timeout)
        return real(address, timeout=timeout, **kw)

    monkeypatch.setattr(e.socket, "create_connection", spy)
    e.probe_graph("127.0.0.1", 1, timeout=1.5)
    assert seen == [1.5], f"connect bound not passed: {seen}"


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


def test_probe_engine_reports_a_nonzero_rc_as_daemon_down(monkeypatch):
    """(a) FAILS if `returncode != 0` is folded into ENGINE_WEDGED. The two are
    NOT the same state: a daemon that is down answers with an error, so docker
    calls FAIL FAST rather than hang, and the recovery is `docker start`, not
    "restart the engine". Calling an answered error "no answer" also makes the
    CLI print a mechanism sentence the output disproves.
    (b) Reachable: `docker version` against a dead daemon exits nonzero with
    "Cannot connect to the Docker daemon".
    """
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Cannot connect to the Docker daemon"

    monkeypatch.setattr(e.subprocess, "run", lambda *a, **k: Proc())
    result = e.probe_engine(3.0)
    assert result["status"] == e.ENGINE_DAEMON_DOWN
    assert result["status"] != e.ENGINE_WEDGED


def test_probe_engine_reports_a_missing_cli_as_absent_not_wedged(monkeypatch):
    """(a) FAILS if FileNotFoundError is folded into ENGINE_WEDGED — a machine
    without the docker CLI is not a wedged engine, and reporting it as one
    would send a lane to restart something that is not running.
    (b) Reachable: any host with no docker binary and no engine socket.
    """
    def boom(*_a, **_k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(e.subprocess, "run", boom)
    monkeypatch.setattr(e, "docker_socket", lambda: None)
    assert e.probe_engine(3.0)["status"] == e.ENGINE_ABSENT


def test_a_missing_cli_with_a_live_socket_is_unmeasured_not_absent(monkeypatch):
    """(a) FAILS if ENGINE_ABSENT is read as "no engine is in play" whenever the
    CLI is missing. A present engine SOCKET means something is trying to be an
    engine and cannot be probed — the graph's state is then unmeasurable, and
    calling it ABSENT is the #4844/#7017 misdiagnosis re-entering through the
    allow-list (the socket-existence half of the definition was never checked).
    (b) Reachable: this machine — an OrbStack socket exists, and PATH was
    mutated so `docker` was missing (measured live during review).
    """
    def boom(*_a, **_k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(e.subprocess, "run", boom)
    monkeypatch.setattr(e, "docker_socket", lambda: Path("/var/run/docker.sock"))
    result = e.probe_engine(3.0)
    assert result["status"] == e.ENGINE_UNMEASURED
    assert result["status"] != e.ENGINE_ABSENT
    # and it must not be believed
    assert e.classify({"ok": False}, result) == e.ENGINE_WEDGED


# ==========================================================================
# CLI contract
# ==========================================================================

def test_the_graph_line_shows_the_actual_reply_not_a_literal_pong(capsys):
    """(a) FAILS if the summary prints the literal `PONG` whenever `ok` is true.
    It printed `PONG` for a `-NOAUTH` reply, so an operator reading the line
    would believe the graph was authenticated and answering — the display then
    contradicts the very distinction the probe was just taught to make.
    (b) Reachable: measured live — a byte-accurate `-NOAUTH` server produced
    `graph 127.0.0.1:56848 -> PONG` before this fix.
    """
    server = _one_shot_server(b"-NOAUTH Authentication required.\r\n")
    port = server.getsockname()[1]
    e.main(["--no-docker", "--port", str(port), "--timeout", "2"])
    out = capsys.readouterr().out
    assert "-NOAUTH" in out, out
    assert "-> PONG" not in out, out
    # the verdict is still up: the endpoint demonstrably answered
    assert "VERDICT: GRAPH_UP" in out, out


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


def test_the_graph_down_prose_never_claims_an_unprobed_engine_answered(capsys):
    """(a) FAILS if the GRAPH_DOWN prose asserts "The engine answered" when the
    engine was not probed. That sentence sends the operator to the graph
    container on a machine whose engine is the actual problem — the misdiagnosis
    this tool was built to remove — and it is invisible to any test that only
    reads the exit code, which is how it survived the first review.
    (b) Reachable: `--no-docker`, the mode that exists precisely so the engine
    is NOT probed.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    e.main(["--no-docker", "--port", str(port), "--timeout", "1"])
    out = capsys.readouterr().out
    assert "The engine answered" not in out, out
    assert "cannot be ruled out" in out, out


def test_the_graph_down_prose_matches_the_engine_state(monkeypatch, capsys):
    """(a) FAILS if the prose asserts a mechanism the probed state contradicts:
    "The engine answered" for ENGINE_ABSENT/ENGINE_UNMEASURED, or the wedged
    timeout sentence for a daemon that answered with an error.
    (b) Reachable: each branch is produced by a real probe state (measured live
    during review: PATH mutated so docker was missing, socket present).
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    # ENGINE_UNMEASURED: no CLI but a live socket -> the graph verdict cannot be
    # believed, so the prose must say so rather than sending us to the graph.
    def no_cli(*_a, **_k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(e.subprocess, "run", no_cli)
    monkeypatch.setattr(e, "docker_socket", lambda: Path("/var/run/docker.sock"))
    e.main(["--port", str(port), "--timeout", "1"])
    out = capsys.readouterr().out
    assert "ENGINE_UNMEASURED" in out, out
    assert "no docker CLI, but an engine socket exists" in out, out
    assert "The engine answered" not in out, out
    # and the wedged sentence must not ASSERT a timeout that never happened
    assert "did not answer inside the bound" not in out, out

    # ENGINE_DAEMON_DOWN: the engine DID answer (with an error), so "answered"
    # is true but the wedged wording ("did not answer inside the bound") is not.
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Cannot connect to the Docker daemon"

    monkeypatch.setattr(e.subprocess, "run", lambda *a, **k: Proc())
    e.main(["--port", str(port), "--timeout", "1"])
    out = capsys.readouterr().out
    assert "fail fast" in out, out
    assert "not necessarily a stopped daemon" in out, out
    # and the prose must not name a cause the probe never measured
    assert "daemon is not running" not in out, out
    assert "did not answer inside the bound" not in out, out


def test_main_rejects_a_nonpositive_timeout():
    """(a) FAILS if --timeout 0 is accepted: a zero bound turns every probe
    into an instant false failure, which would report a healthy graph as down.
    (b) Reachable: `--timeout 0`.
    """
    assert e.main(["--timeout", "0"]) == 1


def test_redis_and_docker_schemes_both_supply_host_and_port(monkeypatch):
    """(a) FAILS if only `docker://` is read or the HOST is discarded. Reading
    only the port silently probed `127.0.0.1:16379` for a
    `redis://prod-graph.example.com:16400` URI, so a DOWN remote graph read
    GRAPH_UP wherever a local graph existed — a false green, the one direction a
    caller gating on the exit code cannot detect.
    (b) Reachable: `tortoise.config.SUPPORTED_URI_SCHEMES` declares redis/rediss,
    so these are product-supported URIs.
    """
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://prod-graph.example.com:16400/tortoise")
    assert e.graph_target_from_env()["host"] == "prod-graph.example.com"
    assert e.graph_target_from_env()["port"] == 16400
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16379/tortoise")
    assert e.graph_target_from_env()["host"] == "127.0.0.1"
    assert e.graph_target_from_env()["port"] == 16379
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    assert e.graph_target_from_env()["ssl"] is True
    # no URI at all is the ONLY thing that returns None
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert e.graph_target_from_env() is None


def test_a_portless_uri_keeps_its_host(monkeypatch):
    """(a) FAILS if a portless URI is read as "no URI configured". The round-1
    host-discard survived for this form: the canonical resolver defaults the port
    to 16379 and KEEPS the host, so probing 127.0.0.1 instead of the configured
    host is a wrong endpoint, and a false green wherever a local graph exists.
    (b) Reachable: `resolve_db_endpoint` supports the portless form.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "redis://prod-graph.example.com/tortoise")
    target = e.graph_target_from_env()
    assert target["host"] == "prod-graph.example.com", target
    assert target["port"] == 16379, target


def test_an_unresolvable_uri_raises_instead_of_defaulting(monkeypatch):
    """(a) FAILS if an unsupported scheme, an embedded path, or a malformed /
    out-of-range port is collapsed into the same None as "no URI". `main` then
    probed the DEFAULT endpoint, so `docker://:pw@127.0.0.1:16x00` came back
    exit 0 against a graph the tool never reached — while the product RAISES on
    that same URI.
    (b) Reachable: a typo'd port, an out-of-range port, an embedded path, and a
    scheme nobody supports.
    """
    for bad in ("docker://:pw@127.0.0.1:16x00/tortoise",
                "docker://:pw@127.0.0.1:99999/tortoise",
                "falkor:///tmp/x",
                "/tmp/tortoise.db"):
        monkeypatch.setenv("TORTOISE_DB_URI", bad)
        try:
            target = e.graph_target_from_env()
        except ValueError:
            continue
        raise AssertionError(f"{bad} resolved to {target} instead of refusing")
    # and redis_port_from_env stays total for callers that only want a port
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16x00/t")
    assert e.redis_port_from_env() is None


def test_the_uri_localhost_is_mapped_to_loopback_v4(monkeypatch):
    """(a) FAILS if `localhost` is dialled literally: it resolves `::1` FIRST and
    a second, near-empty FalkorDB has been observed there (#6666), so the probe
    would report on the wrong instance.
    (b) Reachable: the repo's own `.env.example` uses `localhost`.
    """
    for uri in ("docker://:pw@localhost:16379/t",
                "docker://:pw@[::1]:16379/t"):
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        assert e.graph_target_from_env()["host"] == "127.0.0.1", uri


def test_a_rediss_target_is_refused_rather_than_guessed(monkeypatch, capsys):
    """(a) FAILS if a TLS endpoint is probed with a plaintext PING and its
    failure is reported as GRAPH_DOWN — that blames a graph this tool never
    reached. "Could not tell" must not be dressed as "broken".
    (b) Reachable: `rediss://` is a declared supported scheme.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    rc = e.main(["--no-docker", "--timeout", "1"])
    assert rc == 3, rc
    assert "rediss" in capsys.readouterr().err


def test_the_tls_refusal_does_not_override_an_explicit_endpoint(monkeypatch):
    """(a) FAILS if the refusal fires before --host/--port are applied. `main`'s
    own contract is that the flags override the URI field by field, so a caller
    naming a plaintext endpoint is by construction asking for that endpoint; the
    URI's TLS-ness must not veto it.
    (b) Reachable: `--host`/`--port` given while TORTOISE_DB_URI is a rediss
    target.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    seen = {}
    monkeypatch.setattr(e, "probe_graph",
                        lambda h, p, t: seen.update(host=h, port=p) or {"ok": True})
    rc = e.main(["--host", "1.2.3.4", "--port", "1111", "--no-docker",
                 "--timeout", "1"])
    assert seen == {"host": "1.2.3.4", "port": 1111}, seen
    assert rc == 0, rc


def test_json_is_emitted_on_every_exit_path(monkeypatch, capsys):
    """(a) FAILS if a refusal bypasses `_report`. `--json` is the documented
    machine-readable mode and it emitted ZERO bytes on the TLS refusal, so a
    caller parsing stdout got nothing — while nothing tested JSON at all.
    (b) Reachable: the refusal path, and the normal path.
    """
    import json as _json
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    rc = e.main(["--json", "--no-docker", "--timeout", "1"])
    out = capsys.readouterr().out
    assert rc == 3, rc
    assert _json.loads(out)["verdict"] == e.UNMEASURABLE, out

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    rc = e.main(["--json", "--no-docker", "--port", "1", "--timeout", "1"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["verdict"] == e.GRAPH_DOWN, payload
    assert payload["graph"]["port"] == 1

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    rc = e.main(["--json", "--no-docker", "--port", "1", "--timeout", "1"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["verdict"] == e.GRAPH_DOWN, payload
    assert payload["graph"]["port"] == 1


def test_the_endpoint_main_probes_is_the_one_the_uri_names(monkeypatch):
    """(a) FAILS if `main` discards the URI's host or port, or ignores --host or
    --port. Those four mutations left all previous tests GREEN: the parser was
    pinned but never the endpoint actually probed, so the round-1 bugs could be
    reintroduced silently through the CLI seam.
    (b) Reachable: exactly those mutations.
    """
    seen = {}
    monkeypatch.setattr(e, "probe_graph",
                        lambda h, p, t: seen.update(host=h, port=p) or {"ok": True})

    # URI only -> the URI's endpoint
    monkeypatch.setenv("TORTOISE_DB_URI", "redis://g.example.com:16400/t")
    assert e.main(["--no-docker", "--timeout", "1"]) == 0
    assert seen == {"host": "g.example.com", "port": 16400}, seen

    # URI + --port -> the URI's host, the flag's port
    e.main(["--no-docker", "--timeout", "1", "--port", "2222"])
    assert seen == {"host": "g.example.com", "port": 2222}, seen

    # URI + --host -> the flag's host, the URI's port
    e.main(["--no-docker", "--timeout", "1", "--host", "10.0.0.9"])
    assert seen == {"host": "10.0.0.9", "port": 16400}, seen

    # no URI -> the documented defaults
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    e.main(["--no-docker", "--timeout", "1"])
    assert seen == {"host": "127.0.0.1", "port": 16379}, seen


def test_a_non_finite_timeout_is_rejected_not_crashed(monkeypatch):
    """(a) FAILS if `nan` passes the `<= 0` guard: `nan <= 0` is False, so the
    probe reached the socket layer and raised an uncaught ValueError, breaking
    the documented 0/1/2/3 exit contract with a traceback. `inf` overflows.
    (b) Reachable: `--timeout nan` / `--timeout inf`.
    """
    for bad in ("nan", "inf", "0"):
        assert e.main(["--timeout", bad]) == 1, bad
    # `-inf` reaches argparse as a FLAG, so argparse itself exits 2 — which the
    # EXIT CONTRACT documents. `--timeout=-inf` names the value and must reach
    # our own guard rather than crash.
    assert e.main(["--timeout=-inf"]) == 1


def test_docker_socket_honours_the_environment(monkeypatch, tmp_path):
    """(a) FAILS if `DOCKER_HOST` is ignored. `docker_socket()` is the SOLE
    discriminator between ENGINE_ABSENT (believable) and ENGINE_UNMEASURED
    (treated as wedged); ignoring DOCKER_HOST made "no engine is in play" assert
    something the environment contradicts, and a missed socket endorses a graph
    result that a wedged engine could explain.
    (b) Reachable: colima/rootless/Docker-Desktop layouts set DOCKER_HOST.
    """
    fake = tmp_path / "docker.sock"
    fake.write_text("")            # exists() is the predicate, not is_socket()
    monkeypatch.setenv("DOCKER_HOST", f"unix://{fake}")
    assert e.docker_socket() == fake
    # a tcp:// DOCKER_HOST names no local socket, so it must not be mistaken for
    # one — the default path list is neutralized so this box's real OrbStack
    # socket cannot make the assertion pass for the wrong reason.
    monkeypatch.setenv("DOCKER_HOST", "tcp://1.2.3.4:2375")
    monkeypatch.setattr(e, "DOCKER_SOCKETS", (tmp_path / "absent.sock",))
    assert e.docker_socket() is None
    # and a DOCKER_HOST pointing nowhere falls through to the default probe
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path}/absent.sock")
    monkeypatch.setattr(e, "DOCKER_SOCKETS", (tmp_path / "also-absent.sock",))
    assert e.docker_socket() is None


def test_an_unresolvable_uri_is_refused_before_any_probe(monkeypatch, capsys):
    """(a) FAILS if the refusal path skips `_report`, or if the URI-set-but-
    unresolvable case falls back to the default endpoint.
    (b) Reachable: any uri the canonical resolver rejects.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16x00/t")
    called = []
    monkeypatch.setattr(e, "probe_graph",
                        lambda *a, **k: called.append(a) or {"ok": True})
    rc = e.main(["--no-docker", "--timeout", "1"])
    assert rc == 3, rc
    assert called == [], "must not probe a default endpoint for an unresolvable URI"
    assert "refus" in capsys.readouterr().err.lower()


def test_an_unmeasurable_probe_exits_three_not_two(monkeypatch, capsys):
    """(a) FAILS if UNMEASURABLE is folded into 2. "broken substrate" and "could
    not tell" have different recoveries, and a caller that treats them alike will
    act on a guess. Without this test the branch is unreachable from the CLI
    (probe_graph always sets a bool `ok`), so nothing pins it at all.
    (b) Reachable: any future probe_graph that stops setting `ok`, or a caller
    that passes a partial result — simulated here by patching the probe.
    """
    monkeypatch.setattr(e, "probe_graph", lambda *a, **k: {"reply": "+PONG"})
    rc = e.main(["--no-docker", "--port", "1", "--timeout", "1"])
    assert rc == 3, rc
    assert "UNMEASURABLE" in capsys.readouterr().out


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
    GRAPH_DOWN against a healthy graph. A malformed port must refuse (the
    canonical resolver raises) rather than becoming a confident wrong number.
    (b) Reachable: an IPv6 target, and a typo'd port.
    """
    # The canonical resolver defaults a missing port to 16379 (as the product
    # does) and keeps the host — mapped to 127.0.0.1 by #6666.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@[::1]/tortoise")
    assert e.graph_target_from_env() == {
        "host": "127.0.0.1", "port": 16379, "ssl": False,
        "uri": "docker://:pw@[::1]/tortoise"}
    # a malformed port is NOT defaulted: it refuses
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16x00/tortoise")
    try:
        e.graph_target_from_env()
        raise AssertionError("a malformed port must not resolve")
    except ValueError:
        pass
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
