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

_v4 = socket.AF_INET
from pathlib import Path

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
    (b) Reachable: a probe that grows a new status, or an engine dict that lost
    its status key.
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

def test_probe_graph_refuses_quickly_on_a_closed_port(monkeypatch):
    """(a) FAILS if `probe_graph` raises instead of returning a result, or
    exceeds its bound.
    (b) Reachable: any unused loopback port. Bind then close to get one that is
    almost certainly free without racing a real service.
    """
    # The HOST comes from the URI (field-by-field override), so an ambient
    # non-loopback URI would send this probe elsewhere and the local server
    # would never be contacted — passing for the wrong reason.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
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
    (b) Reachable: omission of `sock.settimeout(timeout)` before `connect()`.
    """
    seen = []
    real_socket = socket.socket

    class Spy:
        def __init__(self, *a, **k):
            self._sock = real_socket(*a, **k)

        def settimeout(self, value):
            seen.append(value)
            return self._sock.settimeout(value)

        def __getattr__(self, name):
            return getattr(self._sock, name)

    monkeypatch.setattr(e.socket, "socket", Spy)
    e.probe_graph("127.0.0.1", 1, timeout=1.5)
    assert 1.5 in seen, f"connect bound not applied: {seen}"


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

def test_the_graph_line_shows_the_actual_reply_not_a_literal_pong(monkeypatch, capsys):
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
    # The HOST comes from the URI (field-by-field override), so an ambient
    # non-loopback URI would send this probe elsewhere and the local server
    # would never be contacted — passing for the wrong reason.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert e.main(["--no-docker", "--port", str(port), "--timeout", "1"]) == 2


def test_the_graph_down_prose_never_claims_an_unprobed_engine_answered(monkeypatch, capsys):
    """(a) FAILS if the GRAPH_DOWN prose asserts "The engine answered" when the
    engine was not probed. That sentence sends the operator to the graph
    container on a machine whose engine is the actual problem — the misdiagnosis
    this tool was built to remove — and it is invisible to any test that only
    reads the exit code, which is how it survived the first review.
    (b) Reachable: `--no-docker`, the mode that exists precisely so the engine
    is NOT probed.
    """
    # The HOST comes from the URI (field-by-field override), so an ambient
    # non-loopback URI would send this probe elsewhere and the local server
    # would never be contacted — passing for the wrong reason.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
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
    # The HOST comes from the URI (field-by-field override), so an ambient
    # non-loopback URI would send this probe elsewhere and the local server
    # would never be contacted — passing for the wrong reason.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
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


def test_a_single_flag_does_not_bypass_the_tls_refusal(monkeypatch):
    """(a) FAILS if the refusal is skipped when only ONE field is overridden. A
    single flag leaves the OTHER field coming from the TLS URI, so the probe
    speaks plaintext to a TLS endpoint's neighbour and reports a confident
    GRAPH_DOWN for the one thing the tool says it cannot address — measured: a
    local listener answering with TLS handshake bytes gave rc=2 / GRAPH_DOWN.
    (b) Reachable: `--port <same>` with a rediss URI.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    called = []
    monkeypatch.setattr(e, "probe_graph",
                        lambda *a, **k: called.append(a) or {"ok": True})
    assert e.main(["--no-docker", "--port", "6380", "--timeout", "1"]) == 3
    assert e.main(["--no-docker", "--host", "g.example.com", "--timeout", "1"]) == 3
    assert called == [], "a TLS endpoint must not be probed with one flag given"


def test_an_unresolvable_uri_still_yields_to_both_flags(monkeypatch):
    """(a) FAILS if a stale/typo'd URI vetoes an endpoint the caller named. The
    contract is field-by-field override, and the TLS path already honours it, so
    an unresolvable URI refusing a fully-specified probe is inconsistent.
    (b) Reachable: a typo'd port left in TORTOISE_DB_URI while --host/--port name
    the real endpoint.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16x00/t")
    seen = {}
    monkeypatch.setattr(e, "probe_graph",
                        lambda h, p, t: seen.update(host=h, port=p) or {"ok": True})
    rc = e.main(["--no-docker", "--host", "10.0.0.9", "--port", "2222",
                 "--timeout", "1"])
    assert rc == 0, rc
    assert seen == {"host": "10.0.0.9", "port": 2222}, seen


def test_a_hanging_resolver_is_bounded(monkeypatch):
    """(a) FAILS if name resolution runs outside `--timeout`. `create_connection`
    calls `getaddrinfo` with no bound, so a slow resolver blew through the
    promise the module makes in capitals — measured 5.05s elapsed for a 0.5s
    bound, 10x over. A hanging resolver IS the wedged-substrate case the tool
    exists for, so it is bounded rather than documented away.
    (b) Reachable: a slow/hanging DNS server, or an unresolvable host on a
    blackholed network.
    """
    import time as _t

    def slow(*_a, **_k):
        _t.sleep(5)
        return []

    monkeypatch.setattr(e.socket, "getaddrinfo", slow)
    started = _t.monotonic()
    result = e.probe_graph("slow.example.com", 16379, timeout=0.5)
    elapsed = _t.monotonic() - started
    # `ok is None` — the endpoint was never reached, so this must be
    # UNMEASURABLE rather than a confident GRAPH_DOWN blaming a port the probe
    # never dialled.
    assert result["ok"] is None, result
    assert result["error"] == "resolve-timeout", result
    assert e.classify(result, {"status": e.ENGINE_OK}) == e.UNMEASURABLE
    assert elapsed < 2.0, f"resolution was not bounded: {elapsed:.2f}s"


def test_json_is_uniform_across_probe_and_refusal(monkeypatch, capsys):
    """(a) FAILS if the `probed` key exists only on refusals. A consumer doing
    `payload["graph"]["probed"]` then works on a refusal and KeyErrors on
    success, so the machine-readable mode is not actually uniform.
    (b) Reachable: both paths.
    """
    import json as _json
    monkeypatch.setattr(e, "probe_graph", lambda *a, **k: {"ok": True})
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    e.main(["--json", "--no-docker", "--port", "1", "--timeout", "1"])
    assert _json.loads(capsys.readouterr().out)["graph"]["probed"] is True

    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    e.main(["--json", "--no-docker", "--timeout", "1"])
    refused = _json.loads(capsys.readouterr().out)
    assert refused["graph"]["probed"] is False, refused


def test_the_non_loopback_note_is_suppressed_when_nothing_was_probed(monkeypatch,
                                                                    capsys):
    """(a) FAILS if the trailing NOTE claims "this probed a non-loopback host" on
    a refusal, where nothing was probed at all — the same "assert a state the
    probe did not measure" shape the rest of this review set is about.
    (b) Reachable: the TLS refusal, whose reported host is the URI's.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    e.main(["--no-docker", "--timeout", "1"])
    out = capsys.readouterr().out
    assert "NOTE: this probed" not in out, out


def test_an_ipv6_target_is_connected_not_crashed(monkeypatch):
    """(a) FAILS if the resolved sockaddr is handed to `create_connection`,
    which unpacks it as a 2-tuple: an AF_INET6 sockaddr is a 4-tuple
    (`('::1', port, 0, 0)`), so the call raised `ValueError: too many values to
    unpack` and the tool produced NO verdict, exit 1 — exactly when an operator
    is diagnosing a wedge. It also fires for any dual-stack hostname whose AAAA
    is returned first, the common case for managed hosts.
    (b) Reachable: `--host ::1`/`localhost`, or a URI such as
    `redis://[2001:db8::1]:16379/t` (only the literal strings `localhost` and
    `::1` are remapped, so other IPv6 targets reach the probe).
    """
    server = socket.socket(socket.AF_INET6)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("::1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        try:
            conn, _ = server.accept()
            with conn:
                conn.recv(64)
                conn.sendall(b"+PONG\r\n")
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve, daemon=True).start()
    result = e.probe_graph("::1", port, timeout=2.0)
    assert result["ok"] is True, result
    assert result["reply"] == "+PONG", result


def test_every_resolved_address_is_tried_not_just_the_first(monkeypatch):
    """(a) FAILS if only `addrinfo[0]` is dialled: a dual-stack host whose first
    (AAAA) address is unreachable while the second (A) answers was reported
    GRAPH_DOWN for a live graph — the multi-address retry that
    `create_connection((host, port))` used to perform must be preserved.
    (b) Reachable: `localhost` here resolves to `::1` FIRST, so a v4-only
    listener is exactly this shape.
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
                conn.recv(64)
                conn.sendall(b"+PONG\r\n")
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve, daemon=True).start()
    # IPv6 first (dead), then the live IPv4 entry
    real_getaddrinfo = socket.getaddrinfo
    dead_v6 = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
               ("::1", port, 0, 0))
    monkeypatch.setattr(e.socket, "getaddrinfo",
                        lambda *a, **k: [dead_v6]
                        + real_getaddrinfo("127.0.0.1", port,
                                           proto=socket.IPPROTO_TCP))
    result = e.probe_graph("dual.example.com", port, timeout=2.0)
    assert result["ok"] is True, result


def test_a_non_oserror_resolver_failure_is_not_reported_as_a_timeout(monkeypatch):
    """(a) FAILS if a resolver that raises something other than OSError kills the
    worker thread, leaving an empty result that the caller reports as
    `resolve-timeout` — a timeout it never measured.
    (b) Reachable: any unexpected resolver error.
    """
    seen = {}

    def explode(*_a, **_k):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(e.socket, "getaddrinfo", explode)
    try:
        e._resolve_within_bound("x.example.com", 1, 1.0)
        raise AssertionError("must propagate")
    except RuntimeError:
        pass
    # and probe_graph must not claim a timeout for it — nor raise, which is its
    # documented contract and the whole point of a diagnostic tool
    result = e.probe_graph("x.example.com", 1, timeout=1.0)
    assert result["ok"] is None, result
    assert result["error"] != "resolve-timeout", result
    assert "RuntimeError" in result["error"], result
    assert e.classify(result, {"status": e.ENGINE_OK}) == e.UNMEASURABLE


def test_a_refusal_reports_ok_none_not_a_measured_failure(monkeypatch, capsys):
    """(a) FAILS if a refusal synthesizes `ok: false`. `ok=False` means "every
    address was dialled and every one failed"; a refusal never reached the
    endpoint, and `classify` itself treats `ok` as the tri-state authority, so a
    JSON consumer would read "measured down" for something never contacted.
    (b) Reachable: both refusal paths (TLS, unresolvable URI).
    """
    import json as _json
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    e.main(["--json", "--no-docker", "--timeout", "1"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["graph"]["ok"] is None, payload
    assert payload["verdict"] == e.UNMEASURABLE, payload


def test_the_engine_line_does_not_claim_a_flag_that_was_not_passed(monkeypatch,
                                                                   capsys):
    """(a) FAILS if the summary prints "engine not probed (--no-docker)" on a
    refusal, where the engine was skipped because the probe was refused, not
    because the caller passed the flag — asserting a mechanism the probe did not
    observe, which is the class the earlier rounds closed for the graph prose.
    (b) Reachable: a TLS refusal without `--no-docker`.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://g.example.com:6380/t")
    e.main(["--timeout", "1"])
    out = capsys.readouterr().out
    assert "--no-docker" not in out, out
    assert "refused" in out, out


def test_a_pre_connect_exception_is_unmeasured(monkeypatch):
    """(a) FAILS if the catch-all returns `ok=False`: an exception before any
    connect is an endpoint never dialled, so `ok=False` becomes a confident
    GRAPH_DOWN. Not reachable through `main` today (the timeout is validated
    first, and a huge value is rejected by `Thread.join` as an OverflowError),
    but `probe_graph` is public and its contract is the tri-state.
    (b) Reachable: a caller passing a hostile timeout.
    """
    monkeypatch.setattr(e, "_resolve_within_bound",
                        lambda *a: [(_v4, e.socket.SOCK_STREAM,
                                     e.socket.IPPROTO_TCP, "", ("127.0.0.1", 1))])
    real_socket = e.socket.socket

    def broken(*a, **k):
        raise ValueError("Timeout value out of range")

    monkeypatch.setattr(e.socket, "socket", broken)
    result = e.probe_graph("x.example.com", 1, timeout=-1.0)
    assert result["ok"] is None, result
    assert e.classify(result, {"status": e.ENGINE_OK}) == e.UNMEASURABLE


def test_a_resolution_failure_exits_three_not_two(monkeypatch, capsys):
    """(a) FAILS if a resolver failure is reported as a measured graph failure.
    It exited 2 with "The graph port is unreachable" and `probed: true` for a
    port that was never dialled — the confident-verdict-from-an-unmeasured-state
    class this tool exists to remove, and no test pinned it.
    (b) Reachable: an unresolvable host, a hanging resolver, or a resolver error.
    """
    import json as _json
    monkeypatch.setattr(e.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(
                            socket.gaierror("Name or service not known")))
    rc = e.main(["--json", "--no-docker", "--host", "nope.invalid",
                 "--timeout", "1"])
    payload = _json.loads(capsys.readouterr().out)
    assert rc == 3, (rc, payload)
    assert payload["verdict"] == e.UNMEASURABLE, payload
    assert payload["graph"]["probed"] is False, payload


def test_an_empty_address_list_is_reported_not_indexed(monkeypatch):
    """(a) FAILS if an empty resolver result reaches `addrinfo[0]`: it raised
    IndexError out of `probe_graph`, whose contract is "Never raises".
    (b) Reachable: a resolver returning no addresses.
    """
    monkeypatch.setattr(e.socket, "getaddrinfo", lambda *a, **k: [])
    result = e.probe_graph("empty.example.com", 16379, timeout=1.0)
    assert result["ok"] is None, result
    assert result["error"] == "no-addresses", result
    assert e.classify(result, {"status": e.ENGINE_OK}) == e.UNMEASURABLE


def test_redis_port_comes_from_the_uri_when_present(monkeypatch):
    """(a) FAILS if the configured port is ignored, so the tool probes 16379
    while the lane's graph is elsewhere and reports a false GRAPH_DOWN.
    (b) Reachable: TORTOISE_DB_URI pointing at a non-default port.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:16400/tortoise")
    assert e.graph_target_from_env()["port"] == 16400
    monkeypatch.setenv("TORTOISE_DB_URI", "falkor:///tmp/x")
    try:
        e.graph_target_from_env()
        raise AssertionError("falkor:// must refuse")
    except ValueError:
        pass


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
    assert e.graph_target_from_env()["port"] == 16379


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
