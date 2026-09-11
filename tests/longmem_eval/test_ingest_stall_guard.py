"""#2969 — ingest stall guard: bounded socket read + liveness heartbeat.

Hermetic by construction (no docker, no real FalkorDB, no LLM):
  * the socket bound is exercised against a LOCAL fake server that accepts
    the TCP connection and never replies (a genuine hung read);
  * a second fake server replies promptly, pinning "a normal fast response
    is unaffected";
  * the heartbeat/no-progress budget is driven by an injected monotonic
    clock, so no test sleeps for a real budget.

The last assertion of the classification tests pins the requirement that
matters: a stall is NOT a new error channel — it grades
``ingest:retries_exhausted`` with ``retryable=True``, which is exactly what
``--retry-failed`` re-attempts.
"""
from __future__ import annotations

import contextlib
import errno
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import redis
import redis.exceptions as redis_exc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import run as runner  # noqa: E402, RUF100
from tools.longmem_eval.errors import eval_failure_class  # noqa: E402, RUF100
from tools.longmem_eval.ingest_v2 import ingest_haystack_v2  # noqa: E402, RUF100
from tools.longmem_eval.stall_guard import (  # noqa: E402, RUF100
    DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S,
    DEFAULT_EVAL_SOCKET_TIMEOUT_S,
    DEFAULT_STALL_TIMEOUT_S,
    ENV_SOCKET_CONNECT_TIMEOUT,
    ENV_SOCKET_TIMEOUT,
    ENV_STALL_TIMEOUT,
    Heartbeat,
    IngestStallTimeout,
    resolve_stall_timeout_s,
)
from tortoise.projection import (  # noqa: E402, RUF100
    _DEFAULT_SOCKET_CONNECT_TIMEOUT,
    _DEFAULT_SOCKET_TIMEOUT,
    _SOCKET_CONNECT_TIMEOUT_ENV,
    _SOCKET_TIMEOUT_ENV,
    FalkorProjection,
    _resolve_socket_timeout,
)
from tortoise.retry import retryable_transient  # noqa: E402, RUF100

# ── fake servers ────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _tcp_server(reply: bytes | None):
    """A loopback TCP server. ``reply=None`` → accepts and never answers (a
    hung read); otherwise every received request gets ``reply`` back."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    stop = threading.Event()
    conns: list[socket.socket] = []

    def _serve(conn: socket.socket) -> None:
        with contextlib.suppress(OSError):
            conn.settimeout(0.2)
            while not stop.is_set():
                try:
                    data = conn.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                if reply is not None:
                    conn.sendall(reply)

    def _accept() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conns.append(conn)
            if reply is not None:
                threading.Thread(target=_serve, args=(conn,),
                                 daemon=True).start()

    acceptor = threading.Thread(target=_accept, daemon=True)
    acceptor.start()
    try:
        yield port
    finally:
        stop.set()
        with contextlib.suppress(OSError):
            srv.close()
        for conn in conns:
            with contextlib.suppress(OSError):
                conn.close()


@pytest.fixture()
def hung_server():
    """A server that accepts the connection and never answers."""
    with _tcp_server(None) as port:
        yield port


@pytest.fixture()
def pong_server():
    """A server that answers every request ``+PONG`` promptly."""
    with _tcp_server(b"+PONG\r\n") as port:
        yield port


class _FakeClock:
    """Injected monotonic clock — no test sleeps a real budget."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ── (a) a stalled read raises, bounded, timeout-classified ──────────────────


def test_stalled_read_raises_bounded_timeout(monkeypatch, hung_server):
    """The eval-lane graph client must RAISE within the bound instead of
    blocking forever in ``recv()`` (the #2969 failure mode)."""
    monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, "0.4")
    monkeypatch.setenv(_SOCKET_CONNECT_TIMEOUT_ENV, "0.4")
    t0 = time.monotonic()
    with pytest.raises(redis_exc.TimeoutError) as ei:
        proj = FalkorProjection(host="127.0.0.1", port=hung_server,
                                graph_name="stall_probe",
                                skip_health_check=True)
        # Construction may already trip on a probe; if it survived (every
        # probe is best-effort), the explicit query must trip.
        proj.g.query("RETURN 1")
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"read was not bounded: {elapsed:.1f}s"
    # timeout-classified: the retry predicate trusts redis TimeoutError.
    assert retryable_transient(ei.value) is True


def test_hung_read_grades_ingest_retries_exhausted(monkeypatch, hung_server):
    monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, "0.4")
    monkeypatch.setenv(_SOCKET_CONNECT_TIMEOUT_ENV, "0.4")
    with pytest.raises(redis_exc.TimeoutError) as ei:
        proj = FalkorProjection(host="127.0.0.1", port=hung_server,
                                graph_name="stall_probe",
                                skip_health_check=True)
        proj.g.query("RETURN 1")
    exc = ei.value
    assert eval_failure_class(exc, site="ingest") == "ingest:retries_exhausted"
    entry = runner._failure_entry("q1", "multi-session", exc,
                                  stage="ingest", attempts=0)
    assert entry["retryable"] is True
    assert entry["error_class"] == "ingest:retries_exhausted"
    # …and that entry is exactly what the resume path re-attempts.
    assert runner._retry_failed_gate_ok(entry) is True


def test_ingest_stall_timeout_is_retryable_and_resume_eligible():
    exc = IngestStallTimeout("stalled", stalled_for=900.0, stage="phase-c",
                             beats=3)
    assert isinstance(exc, TimeoutError)
    assert exc.errno == errno.ETIMEDOUT  # transport evidence for the predicate
    assert retryable_transient(exc) is True
    assert eval_failure_class(exc, site="ingest") == "ingest:retries_exhausted"
    entry = runner._failure_entry("q7", "temporal", exc,
                                  stage="ingest", attempts=0)
    assert entry["retryable"] is True
    assert runner._retry_failed_gate_ok(entry) is True


# ── (c) fast/normal paths are unaffected ───────────────────────────────────


def test_socket_timeout_defaults_preserve_product_behaviour(monkeypatch):
    monkeypatch.delenv(_SOCKET_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(_SOCKET_CONNECT_TIMEOUT_ENV, raising=False)
    assert _resolve_socket_timeout(_SOCKET_TIMEOUT_ENV, _DEFAULT_SOCKET_TIMEOUT) \
        == _DEFAULT_SOCKET_TIMEOUT == 10.0
    assert _resolve_socket_timeout(
        _SOCKET_CONNECT_TIMEOUT_ENV, _DEFAULT_SOCKET_CONNECT_TIMEOUT) == 5.0
    # Env-tunable, both directions.
    monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, "120")
    assert _resolve_socket_timeout(_SOCKET_TIMEOUT_ENV, 10.0) == 120.0
    # Explicit opt-out (documented, not recommended).
    for token in ("none", "off", "0"):
        monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, token)
        assert _resolve_socket_timeout(_SOCKET_TIMEOUT_ENV, 10.0) is None
    # A typo fails loud — never silently unbounded.
    monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, "abc")
    with pytest.raises(ValueError):
        _resolve_socket_timeout(_SOCKET_TIMEOUT_ENV, 10.0)
    # …including a NON-FINITE value: 'inf' must not become an unbounded read.
    for token in ("inf", "nan"):
        monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, token)
        with pytest.raises(ValueError):
            _resolve_socket_timeout(_SOCKET_TIMEOUT_ENV, 10.0)


def test_fast_response_unaffected_by_the_bound(monkeypatch, pong_server):
    monkeypatch.delenv(_SOCKET_TIMEOUT_ENV, raising=False)
    # Even a tight bound must not trip a healthy prompt reply.
    monkeypatch.setenv(_SOCKET_TIMEOUT_ENV, "2")
    kwargs = {
        "host": "127.0.0.1",
        "port": pong_server,
        "socket_timeout": _resolve_socket_timeout(_SOCKET_TIMEOUT_ENV, 10.0),
        "socket_connect_timeout": _resolve_socket_timeout(
            _SOCKET_CONNECT_TIMEOUT_ENV, 5.0),
        "decode_responses": True,
        # falkordb's FalkorDB() constructs its client with protocol=2 —
        # mirror it so the fake server needs no RESP3 HELLO handshake.
        "protocol": 2,
    }
    t0 = time.monotonic()
    client = redis.Redis(**kwargs)
    try:
        assert client.ping() is True
    finally:
        client.close()
    assert time.monotonic() - t0 < 2.0


# ── heartbeat: signal + no-progress budget ─────────────────────────────────


def test_heartbeat_fresh_does_not_raise_and_budget_disabled():
    clock = _FakeClock()
    hb = Heartbeat(label="q1", stall_timeout_s=10.0, clock=clock,
                   emit=lambda _line: None)
    hb.beat("ingest:start")
    clock.advance(1.0)
    hb.check("s0:phase-a")  # fresh → no raise
    # A disabled budget never raises, however long the silence.
    relaxed = Heartbeat(label="q2", stall_timeout_s=0.0, clock=clock,
                        emit=lambda _line: None)
    clock.advance(10_000.0)
    relaxed.check("s0:phase-a")


def test_heartbeat_detects_no_progress_stall():
    clock = _FakeClock()
    hb = Heartbeat(label="q1", stall_timeout_s=5.0, clock=clock,
                   emit=lambda _line: None)
    hb.beat("ingest:start")
    hb.beat("phase-a")
    clock.advance(30.0)
    assert hb.since() == 30.0
    with pytest.raises(IngestStallTimeout) as ei:
        hb.check("s1:phase-a")
    exc = ei.value
    assert exc.stalled_for == 30.0
    assert exc.stage == "phase-a"
    assert exc.beats == 2
    assert retryable_transient(exc) is True


def test_heartbeat_emits_progress_line():
    clock = _FakeClock()
    lines: list[str] = []
    hb = Heartbeat(label="q42", stall_timeout_s=100.0, emit_interval_s=0.0,
                   clock=clock, emit=lines.append)
    hb.beat("ingest:start")
    hb.beat("phase-c", detail="s3")
    assert len(lines) == 2
    assert "q42" in lines[-1]
    assert "stage=phase-c" in lines[-1]
    assert "beats=2" in lines[-1]
    assert "s3" in lines[-1]


def test_stall_budget_resolution(monkeypatch):
    monkeypatch.delenv(ENV_STALL_TIMEOUT, raising=False)
    assert resolve_stall_timeout_s() == DEFAULT_STALL_TIMEOUT_S
    monkeypatch.setenv(ENV_STALL_TIMEOUT, "45")
    assert resolve_stall_timeout_s() == 45.0
    monkeypatch.setenv(ENV_STALL_TIMEOUT, "none")
    assert resolve_stall_timeout_s() == 0.0
    monkeypatch.setenv(ENV_STALL_TIMEOUT, "not-a-number")
    with pytest.raises(ValueError):
        resolve_stall_timeout_s()
    # Explicit wins over the env.
    assert resolve_stall_timeout_s(12.0) == 12.0


# ── end-to-end wiring: a mid-question stall aborts the QUESTION ────────────


class _BoomSDK:
    """Explodes on ANY attribute access — proves the abort happens on the
    heartbeat boundary, not inside a graph call."""

    def __getattr__(self, name: str):
        raise AssertionError(f"SDK touched during a stalled ingest: {name}")


def test_ingest_haystack_v2_aborts_stalled_question(monkeypatch):
    """Two sessions, a frozen clock, and a phase-A writer that consumes 100s
    of (fake) wall clock: session 2's boundary check must abort with
    ``IngestStallTimeout`` — classified retryable — instead of grinding on."""
    from tools.longmem_eval import ingest_v2 as iv2

    clock = _FakeClock()
    hb = Heartbeat(label="q1", stall_timeout_s=5.0, clock=clock,
                   emit=lambda _line: None)

    def _slow_phase_a(_sdk, **_kw):
        clock.advance(100.0)      # a "successful" but glacial write
        return {"sessions": 1, "chunks": 0}

    def _phase_c(_sdk, **_kw):
        return ({}, 0, 0)

    def _extract(_model, _turns, **_kw):
        return {"payload": {}, "stats": {}}

    monkeypatch.setattr(iv2, "_write_v2_phase_a", _slow_phase_a)
    monkeypatch.setattr(iv2, "_write_v2_phase_c", _phase_c)
    monkeypatch.setattr("tortoise.extractor_v2.extract_session_v2", _extract)

    question = {
        "question_id": "q1",
        "answer": "x",
        "haystack_sessions": [
            [{"role": "user", "content": "hi"}],
            [{"role": "user", "content": "again"}],
        ],
        "haystack_dates": ["2024-01-01", "2024-01-02"],
        "haystack_session_ids": ["s0", "s1"],
    }
    with pytest.raises(IngestStallTimeout) as ei:
        ingest_haystack_v2(_BoomSDK(), question, model=None, heartbeat=hb)
    assert ei.value.stalled_for >= 100.0
    assert retryable_transient(ei.value) is True
    assert eval_failure_class(ei.value, site="ingest") == \
        "ingest:retries_exhausted"
    # The guard fired on the boundary that FOLLOWED the slow stage (the
    # stage label is the one that overran) — the graph was never touched.
    assert ei.value.stage == "s0:phase-a"


# ── run.py wiring: the eval lane actually gets the bounded read ────────────


def test_run_main_scopes_the_eval_socket_timeouts(monkeypatch):
    """``--db`` runs must enter the graph with the EVAL read bound (120s),
    not the product default (10s) — and must restore the caller's env on
    exit (#1349 isolation), with an explicit operator value winning."""
    monkeypatch.delenv(ENV_SOCKET_TIMEOUT, raising=False)
    monkeypatch.delenv(ENV_SOCKET_CONNECT_TIMEOUT, raising=False)
    captured: dict[str, object] = {}

    def _fake_run_main(_parser, _args, db_uri):
        captured["uri"] = db_uri
        captured["read"] = os.environ.get(ENV_SOCKET_TIMEOUT)
        captured["connect"] = os.environ.get(ENV_SOCKET_CONNECT_TIMEOUT)
        return {}

    monkeypatch.setattr(runner, "_run_main", _fake_run_main)
    runner.run_main(["--db", "docker://:falkordb@localhost:6380/lme"])
    assert captured["uri"] == "docker://:falkordb@localhost:6380/lme"
    assert float(captured["read"]) == DEFAULT_EVAL_SOCKET_TIMEOUT_S
    assert float(captured["connect"]) == DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S
    # Restored — no leak into later SDK constructions in this process.
    assert os.environ.get(ENV_SOCKET_TIMEOUT) is None
    assert os.environ.get(ENV_SOCKET_CONNECT_TIMEOUT) is None

    monkeypatch.setenv(ENV_SOCKET_TIMEOUT, "42")
    captured.clear()
    runner.run_main(["--db", "docker://:falkordb@localhost:6380/lme"])
    assert captured["read"] == "42"          # operator value wins
    assert os.environ.get(ENV_SOCKET_TIMEOUT) == "42"


def test_resolve_stall_budget_is_not_fingerprint_member():
    """The new knob is resilience config, not a measurement axis — the
    checkpoint fingerprint shape must not change (a stale-fingerprint
    refusal on every existing checkpoint would be a P0 for the eval)."""
    import inspect

    from tools.longmem_eval import run as run_mod

    src = inspect.getsource(run_mod._build_fingerprint)
    assert "stall_timeout" not in src
    assert "SOCKET_TIMEOUT" not in src


def test_ingest_bound_banner_is_well_formed(monkeypatch):
    """The #2969 run diagnostic must render cleanly in all four states
    (a mangled line is worse than no line — a verifier caught a stray unit
    suffix here)."""
    monkeypatch.delenv(ENV_SOCKET_TIMEOUT, raising=False)
    line = runner._ingest_bound_banner(900.0, db_uri="docker://h:6379/g")
    assert line == (
        "[longmem_eval] ingest stall budget: 900s "
        f"({ENV_STALL_TIMEOUT}); graph socket read timeout: 120s "
        f"({ENV_SOCKET_TIMEOUT})")
    monkeypatch.setenv(ENV_SOCKET_TIMEOUT, "45")
    assert "read timeout: 45s " in runner._ingest_bound_banner(
        900.0, db_uri="docker://h:6379/g")
    monkeypatch.setenv(ENV_SOCKET_TIMEOUT, "none")
    assert "read timeout: UNBOUNDED (explicit opt-out) " in \
        runner._ingest_bound_banner(900.0, db_uri="docker://h:6379/g")
    monkeypatch.delenv(ENV_SOCKET_TIMEOUT, raising=False)
    embedded = runner._ingest_bound_banner(0.0, db_uri=None)
    assert "stall budget: disabled " in embedded
    assert "read timeout: n/a (embedded lane) " in embedded
