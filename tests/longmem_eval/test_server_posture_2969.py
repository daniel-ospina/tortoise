"""#2969 suggested fix 3 — the eval lane refuses a defragging graph server.

Hermetic by construction: no docker, no real FalkorDB, no network. The server
is a fake connection that records every command it receives, so each test can
pin exactly what the guard did (and did not) write to the server.

The pre-fix behaviour each test fails on: before this module existed, a
``--db`` eval run had NO read of the server's active-defrag posture at all —
the measured #2969 incident (a container in a continuous defrag loop, ingest
grinding at ~8.6 s/point for hours with no error) started with no signal and
nothing refused it. The wiring test fails on pre-fix code because
``run_main`` proceeded straight to the embedder pre-flight / dataset load.
"""
from __future__ import annotations

import contextlib
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import run as runner  # noqa: E402, RUF100
from tools.longmem_eval.server_posture import (  # noqa: E402, RUF100
    DEFAULT_ACTIVEDEFRAG_POLICY,
    ENV_ACTIVEDEFRAG,
    POLICY_ALLOW,
    POLICY_DISABLE,
    POLICY_REQUIRE_OFF,
    STATUS_ALLOWED,
    STATUS_DISABLED,
    STATUS_OFF,
    STATUS_UNKNOWN,
    ServerPostureError,
    enforce_server_posture,
    read_activedefrag,
    resolve_activedefrag_policy,
)


class FakeConnection:
    """Records commands; answers ``CONFIG GET activedefrag`` from ``value``."""

    def __init__(self, value: str = "yes", *, resp3: bool = False,
                 get_error: BaseException | None = None,
                 set_error: BaseException | None = None,
                 set_effective: bool = True) -> None:
        self.value = value
        self.resp3 = resp3
        self.get_error = get_error
        self.set_error = set_error
        self.set_effective = set_effective
        self.commands: list[tuple[str, ...]] = []

    def execute_command(self, *args):
        cmd = tuple(str(a) for a in args)
        self.commands.append(cmd)
        if cmd == ("CONFIG", "GET", "activedefrag"):
            if self.get_error is not None:
                raise self.get_error
            return ({"activedefrag": self.value} if self.resp3
                    else ["activedefrag", self.value])
        if cmd == ("CONFIG", "SET", "activedefrag", "no"):
            if self.set_error is not None:
                raise self.set_error
            if self.set_effective:
                self.value = "no"
            return "OK"
        raise AssertionError(f"unexpected command: {cmd!r}")

    def sets(self) -> list[tuple[str, ...]]:
        return [c for c in self.commands if c[:2] == ("CONFIG", "SET")]


def _lines(collected: list[str]):
    def emit(line: str) -> None:
        collected.append(line)
    return emit


@pytest.fixture(autouse=True)
def _no_policy_env(monkeypatch):
    monkeypatch.delenv(ENV_ACTIVEDEFRAG, raising=False)


# ── policy resolution ──────────────────────────────────────────────────────

def test_default_policy_is_require_off():
    assert DEFAULT_ACTIVEDEFRAG_POLICY == POLICY_REQUIRE_OFF
    assert resolve_activedefrag_policy() == POLICY_REQUIRE_OFF


def test_malformed_policy_fails_loud(monkeypatch):
    """A typo must never silently choose a policy (least of all 'allow')."""
    monkeypatch.setenv(ENV_ACTIVEDEFRAG, "yes-please")
    with pytest.raises(ValueError, match=ENV_ACTIVEDEFRAG):
        resolve_activedefrag_policy()


# ── the gate ───────────────────────────────────────────────────────────────

def test_defrag_on_refuses_by_default_and_writes_nothing():
    """The whole point: a known-bad posture stops the run, and the guard
    does NOT mutate a server it does not own."""
    conn = FakeConnection("yes")
    lines: list[str] = []
    with pytest.raises(ServerPostureError) as exc:
        enforce_server_posture(connection=conn, emit=_lines(lines))
    # The remediation is in the message (an operator can act on it).
    assert "CONFIG SET activedefrag no" in str(exc.value)
    assert "CONFIG GET" in str(exc.value) or "activedefrag" in str(exc.value)
    # Only the read: no CONFIG SET on a server the eval does not own.
    assert conn.sets() == []
    assert conn.commands == [("CONFIG", "GET", "activedefrag")]


def test_defrag_off_is_a_pass_and_noop():
    conn = FakeConnection("no")
    lines: list[str] = []
    result = enforce_server_posture(connection=conn, emit=_lines(lines))
    assert result["status"] == STATUS_OFF
    assert conn.sets() == []
    assert "defrag-quiet" in result["banner"]
    assert lines == [result["banner"]]


def test_disable_policy_applies_the_incident_mitigation_and_reads_it_back():
    """``disable`` is the opt-in that DOES write — and it verifies the write."""
    conn = FakeConnection("yes")
    result = enforce_server_posture(
        connection=conn, policy=POLICY_DISABLE, emit=_lines([]))
    assert result["status"] == STATUS_DISABLED
    assert conn.sets() == [("CONFIG", "SET", "activedefrag", "no")]
    # read → set → read-back: the SET's own reply is not evidence.
    assert conn.commands == [
        ("CONFIG", "GET", "activedefrag"),
        ("CONFIG", "SET", "activedefrag", "no"),
        ("CONFIG", "GET", "activedefrag"),
    ]
    assert conn.value == "no"
    assert "read-back" in result["banner"]


def test_disable_policy_fails_closed_when_the_set_does_not_take():
    """A server that accepts the SET but stays on must NOT be reported off —
    the #2969 stall condition would still be present."""
    conn = FakeConnection("yes", set_effective=False)
    with pytest.raises(ServerPostureError, match="read-back"):
        enforce_server_posture(connection=conn, policy=POLICY_DISABLE,
                               emit=_lines([]))


def test_disable_policy_reports_a_refused_set():
    conn = FakeConnection(
        "yes", set_error=RuntimeError("ERR CONFIG SET is disabled"))
    with pytest.raises(ServerPostureError) as exc:
        enforce_server_posture(connection=conn, policy=POLICY_DISABLE,
                               emit=_lines([]))
    assert "CONFIG SET is disabled" in str(exc.value)


def test_allow_policy_proceeds_loudly():
    conn = FakeConnection("yes")
    lines: list[str] = []
    result = enforce_server_posture(
        connection=conn, policy=POLICY_ALLOW, emit=_lines(lines))
    assert result["status"] == STATUS_ALLOWED
    assert conn.sets() == []
    assert lines and "ALLOWED" in lines[0]
    assert "#2969" in lines[0]


def test_unreadable_config_is_unknown_never_clean():
    """A server that forbids CONFIG (managed endpoints) is not "defrag off"."""
    conn = FakeConnection(get_error=RuntimeError("NOPERM this user has no "
                                                 "permissions"))
    lines: list[str] = []
    result = enforce_server_posture(connection=conn, emit=_lines(lines))
    assert result["status"] == STATUS_UNKNOWN
    assert result["status"] != STATUS_OFF
    assert "UNKNOWN" in result["banner"]
    assert lines == [result["banner"]]


def test_resp3_dict_shape_is_parsed():
    """RESP3 answers a dict; the guard must read it, not silently pass."""
    assert read_activedefrag(FakeConnection("yes", resp3=True)) == "yes"
    conn = FakeConnection("yes", resp3=True)
    with pytest.raises(ServerPostureError):
        enforce_server_posture(connection=conn, emit=_lines([]))


def test_probe_connection_failure_is_unknown_not_a_new_hard_dependency(monkeypatch):
    """The guard must not become the reason a run dies: an unopenable probe
    is reported UNKNOWN (loudly) and the run's own dial reports the real
    error later."""
    from tools.longmem_eval import server_posture

    def _boom(uri):  # pragma: no cover — must be called
        raise ConnectionError("no route to host")

    monkeypatch.setattr(server_posture, "_connection_from_uri", _boom)
    lines: list[str] = []
    result = enforce_server_posture(db_uri="redis://127.0.0.1:1/eval_test",
                                    emit=_lines(lines))
    assert result["status"] == STATUS_UNKNOWN
    assert "no route to host" in result["detail"]
    assert "NOT a defrag-clean assertion" in result["banner"]


# ── wiring: the gate runs before anything expensive ────────────────────────

def test_run_main_aborts_before_embedder_and_dataset(monkeypatch):
    """A known-bad posture stops the run BEFORE the embedder load and the
    dataset download — and before any question executes."""
    seen: dict[str, bool] = {}

    def _forbidden(*_a, **_kw):  # pragma: no cover — must never run
        seen["reached"] = True
        raise AssertionError("must abort at the posture gate")

    def _raise_posture(**_kw):
        raise ServerPostureError("activedefrag='yes' on the measurement server")

    monkeypatch.setattr(runner, "enforce_server_posture", _raise_posture)
    monkeypatch.setattr(runner, "_preflight_embedder", _forbidden)
    monkeypatch.setattr(runner.ds, "load_dataset", _forbidden)

    import os
    uri_before = os.environ.get("TORTOISE_DB_URI")
    timeout_before = os.environ.get("TORTOISE_DB_SOCKET_TIMEOUT")

    with pytest.raises(SystemExit) as exc:
        runner.run_main(["--db", "redis://127.0.0.1:6379/eval_posture_pin",
                         "--mock", "--limit", "1", "--split", "s",
                         "--no-download"])
    assert exc.value.code == 1
    assert "reached" not in seen
    # The run's scoped env is restored on exit (#1349) — the gate must not
    # leak the --db URI or the eval socket bound into later in-process SDKs.
    assert os.environ.get("TORTOISE_DB_URI") == uri_before
    assert os.environ.get("TORTOISE_DB_SOCKET_TIMEOUT") == timeout_before


def test_run_main_passes_the_resolved_policy(monkeypatch):
    """The policy is resolved in run.py and threaded in — one resolution."""
    seen: dict[str, object] = {}

    def _record(**kw):
        seen["policy"] = kw.get("policy")
        raise ServerPostureError("stop here")

    monkeypatch.setenv(ENV_ACTIVEDEFRAG, POLICY_ALLOW)
    monkeypatch.setattr(runner, "enforce_server_posture", _record)
    with pytest.raises(SystemExit) as exc:
        runner.run_main(["--db", "redis://127.0.0.1:6379/eval_posture_pin",
                         "--mock", "--limit", "1", "--split", "s",
                         "--no-download"])
    assert exc.value.code == 1
    assert seen["policy"] == POLICY_ALLOW


# ── the REAL builder, over a real socket (the load-bearing assumption) ─────
#
# Every test above either hands `enforce_server_posture` a fake connection or
# replaces `_connection_from_uri` with a stub — so the production builder and a
# real `CONFIG GET` were exercised by nothing, and an inert gate would have
# stayed green. That matters because the surrounding failure mode is a FALSE
# PASS: any exception in the builder (a rejected kwarg, an endpoint resolver
# that refuses the scheme) or in the read collapses to UNKNOWN -> proceed. The
# server below speaks just enough RESP2 for a `CONFIG` probe, so these tests
# prove the gate really reads a server, not merely that it handles a fake.


def _parse_resp_commands(buf: bytes) -> tuple[list[tuple[bytes, ...]], bytes]:
    """Split ``buf`` into complete RESP arrays of bulk strings + the remainder."""
    commands: list[tuple[bytes, ...]] = []
    i, n = 0, len(buf)
    while i < n and buf[i:i + 1] == b"*":
        eol = buf.find(b"\r\n", i)
        if eol == -1:
            break
        try:
            count = int(buf[i + 1:eol])
        except ValueError:  # pragma: no cover - defensive
            break
        cursor, args, complete = eol + 2, [], True
        for _ in range(count):
            if buf[cursor:cursor + 1] != b"$":
                complete = False
                break
            head = buf.find(b"\r\n", cursor)
            if head == -1:
                complete = False
                break
            length = int(buf[cursor + 1:head])
            start = head + 2
            end = start + length
            if end + 2 > n:
                complete = False
                break
            args.append(buf[start:end])
            cursor = end + 2
        if not complete:
            break
        commands.append(tuple(args))
        i = cursor
    return commands, buf[i:]


class _FakeRedisServer:
    """A loopback RESP2 server for the ``CONFIG`` option probe."""

    def __init__(self, activedefrag: str = "no", *,
                 get_error: str | None = None,
                 set_effective: bool = True) -> None:
        self.value = activedefrag
        self.get_error = get_error
        self.set_effective = set_effective
        self.commands: list[tuple[str, ...]] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self._srv.settimeout(0.2)
        self.port = self._srv.getsockname()[1]
        self._acceptor = threading.Thread(target=self._accept, daemon=True)

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> _FakeRedisServer:
        self._acceptor.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._srv.close()
        for thread in self._threads:
            thread.join(timeout=2)

    @property
    def uri(self) -> str:
        return f"redis://127.0.0.1:{self.port}/eval_posture"

    def issued(self, *expected: str) -> bool:
        """Did the client ISSUE this command? (case-insensitive)"""
        want = tuple(e.upper() for e in expected)
        return any(tuple(tok.upper() for tok in cmd) == want
                   for cmd in self.commands)

    # -- RESP --------------------------------------------------------------
    def _reply(self, args: tuple[bytes, ...]) -> bytes:
        tokens = tuple(a.decode("utf-8", "replace") for a in args)
        self.commands.append(tokens)
        upper = tuple(t.upper() for t in tokens)
        if upper[:2] == ("CONFIG", "GET"):
            if self.get_error is not None:
                return f"-{self.get_error}\r\n".encode()
            field = tokens[2].encode()
            value = str(self.value).encode()
            return (b"*2\r\n$" + str(len(field)).encode() + b"\r\n" + field +
                    b"\r\n$" + str(len(value)).encode() + b"\r\n" + value +
                    b"\r\n")
        if upper[:2] == ("CONFIG", "SET"):
            if self.set_effective and len(tokens) > 3:
                self.value = tokens[3]
            return b"+OK\r\n"
        # CLIENT SETINFO / HELLO / anything else a redis client sends.
        return b"+OK\r\n"

    def _serve(self, conn: socket.socket) -> None:
        with contextlib.suppress(OSError):
            conn.settimeout(0.2)
            buf = b""
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                buf += data
                commands, buf = _parse_resp_commands(buf)
                for args in commands:
                    with contextlib.suppress(OSError):
                        conn.sendall(self._reply(args))

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            thread = threading.Thread(target=self._serve, args=(conn,),
                                     daemon=True)
            thread.start()
            self._threads.append(thread)


def test_the_real_builder_reads_a_defrag_quiet_server():
    """The gate is not inert: the production builder + a real CONFIG GET."""
    with _FakeRedisServer("no") as server:
        result = enforce_server_posture(db_uri=server.uri, emit=_lines([]))
    assert result["status"] == STATUS_OFF
    assert server.issued("CONFIG", "GET", "activedefrag")


def test_the_real_builder_refuses_a_defragging_server():
    """THE assertion that fails if the gate ever goes inert: a real server
    answering ``yes`` must stop the run, not collapse to UNKNOWN."""
    with _FakeRedisServer("yes") as server:
        with pytest.raises(ServerPostureError):
            enforce_server_posture(db_uri=server.uri, emit=_lines([]))
        # the refusal is ascribable to a read that really happened...
        assert server.issued("CONFIG", "GET", "activedefrag")
        # ...and it did NOT mutate a server it does not own.
        assert not server.issued("CONFIG", "SET", "activedefrag", "no")


def test_the_real_builder_disable_policy_writes_and_verifies():
    with _FakeRedisServer("yes") as server:
        result = enforce_server_posture(db_uri=server.uri,
                                        policy=POLICY_DISABLE, emit=_lines([]))
        assert server.value == "no"
    assert result["status"] == STATUS_DISABLED
    assert server.issued("CONFIG", "GET", "activedefrag")
    assert server.issued("CONFIG", "SET", "activedefrag", "no")


def test_a_refused_config_over_a_socket_is_unknown_not_off():
    """A managed endpoint's error reply must not read as a pass."""
    with _FakeRedisServer(
            "no", get_error="NOPERM this user has no permissions to run CONFIG"
    ) as server:
        result = enforce_server_posture(db_uri=server.uri, emit=_lines([]))
    assert result["status"] == STATUS_UNKNOWN


def test_builder_passes_the_eval_socket_bound_and_the_resolved_endpoint(
        monkeypatch):
    """Pin the builder's kwargs — the leg that a rejected kwarg would turn
    into a permanent, silent UNKNOWN."""
    import falkordb

    from tools.longmem_eval.server_posture import _connection_from_uri

    seen: dict[str, object] = {}

    class _RecordingClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(falkordb, "FalkorDB", _RecordingClient)
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "120")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "2")

    client = _connection_from_uri("redis://:s3cr3t@evalhost:6380/eval_graph")

    assert isinstance(client, _RecordingClient)
    assert seen["host"] == "evalhost"
    assert seen["port"] == 6380
    assert seen["password"] == "s3cr3t"
    assert seen["ssl"] is False
    # the EVAL lane's own 120s read bound, not redis-py's unbounded default.
    assert seen["socket_timeout"] == 120.0
    assert seen["socket_connect_timeout"] == 2.0
    # and the no-multiplier retry pin, so the bound IS the wait.
    assert seen["retry"] is not None


# ── the two fail-open branches ─────────────────────────────────────────────


def test_disable_policy_fails_closed_when_the_config_cannot_be_read():
    """``disable`` is an explicit request to APPLY the mitigation: it cannot
    confirm its precondition, so it must not wave the run through."""
    conn = FakeConnection(get_error=RuntimeError("NOPERM this user has no "
                                                 "permissions"))
    with pytest.raises(ServerPostureError, match="could not be read"):
        enforce_server_posture(connection=conn, policy=POLICY_DISABLE,
                               emit=_lines([]))
    assert conn.sets() == []


def test_unknown_stays_soft_for_the_policies_that_did_not_opt_in():
    """The fail-closed change above is narrow: require-off keeps the documented
    UNKNOWN-and-proceed behaviour so the guard stays a non-dependency."""
    conn = FakeConnection(get_error=RuntimeError("NOPERM"))
    assert enforce_server_posture(
        connection=conn, emit=_lines([]))["status"] == STATUS_UNKNOWN
    assert enforce_server_posture(
        connection=FakeConnection(
            get_error=RuntimeError("NOPERM")),
        policy=POLICY_ALLOW, emit=_lines([]))["status"] == STATUS_UNKNOWN


@pytest.mark.parametrize("value", ["1", "true", "on", "maybe"])
def test_an_unrecognised_value_is_unknown_never_off(value):
    """Only the literal ``no`` is a PASS. A readable-but-uninterpretable reply
    must not become a positive verdict (the module's stated fail-open rule)."""
    conn = FakeConnection(value)
    lines: list[str] = []
    result = enforce_server_posture(connection=conn, emit=_lines(lines))
    assert result["status"] == STATUS_UNKNOWN
    assert result["status"] != STATUS_OFF
    assert value in result["detail"]
    assert "UNKNOWN" in result["banner"]
    assert lines == [result["banner"]]


def test_run_main_aborts_before_the_model_is_injected(monkeypatch):
    """The gate sits ABOVE ``inject_model``: a refused run must not first pay a
    ``--model`` load (``--load-timeout`` can be minutes) to learn that the
    measurement target is defragging."""
    seen: dict[str, bool] = {}

    def _record_injection(*_a, **_kw):  # pragma: no cover — must never run
        seen["injected"] = True

    def _raise_posture(**_kw):
        raise ServerPostureError("activedefrag='yes' on the measurement server")

    monkeypatch.setattr(runner, "enforce_server_posture", _raise_posture)
    monkeypatch.setattr(runner, "inject_model", _record_injection)

    with pytest.raises(SystemExit) as exc:
        runner.run_main(["--db", "redis://127.0.0.1:6379/eval_posture_pin",
                         "--mock", "--model", "minilm", "--limit", "1",
                         "--split", "s", "--no-download"])
    assert exc.value.code == 1
    assert "injected" not in seen


def test_run_main_aborts_before_the_cross_encoder_is_loaded(monkeypatch):
    """BOTH model loads sit below the gate, not just ``inject_model``.

    The R6 pre-warm (~90 MB cross-encoder) used to run ABOVE the gate, so a
    ``--rerank`` arm — a documented eval A/B arm — paid that load before it
    could learn the measurement target is defragging. Non-vacuous pre-fix: the
    pre-warm branch ran before ``enforce_server_posture``, so ``get_scorer``
    was reached and ``seen`` was populated.
    """
    from tools.longmem_eval import rerank as rerank_mod

    seen: dict[str, bool] = {}

    def _record_prewarm(*_a, **_kw):
        seen["prewarmed"] = True
        return None, "must not be called"

    def _raise_posture(**_kw):
        raise ServerPostureError("activedefrag='yes' on the measurement server")

    monkeypatch.setattr(runner, "enforce_server_posture", _raise_posture)
    monkeypatch.setattr(rerank_mod, "get_scorer", _record_prewarm)

    with pytest.raises(SystemExit) as exc:
        runner.run_main(["--db", "redis://127.0.0.1:6379/eval_posture_pin",
                         "--mock", "--rerank", "--limit", "1",
                         "--split", "s", "--no-download"])
    assert exc.value.code == 1
    assert "prewarmed" not in seen


def test_disable_policy_fails_closed_when_the_probe_cannot_be_opened(monkeypatch):
    """``disable`` cannot apply a mitigation through a probe it never opened —
    the same fail-open as an unreadable value, one step earlier."""
    from tools.longmem_eval import server_posture

    def _boom(uri):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(server_posture, "_connection_from_uri", _boom)
    with pytest.raises(ServerPostureError, match="no route to host"):
        enforce_server_posture(db_uri="redis://127.0.0.1:1/eval_test",
                               policy=POLICY_DISABLE, emit=_lines([]))
    # …while the policies that did NOT opt in keep the soft UNKNOWN.
    result = enforce_server_posture(db_uri="redis://127.0.0.1:1/eval_test",
                                    emit=_lines([]))
    assert result["status"] == STATUS_UNKNOWN


@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_disable_policy_still_applies_the_mitigation_on_an_unrecognised_value(
        value):
    """A readable value that is not ``no`` is a state ``disable`` was asked to
    remove — reporting UNKNOWN there would silently skip the mitigation the
    operator opted into."""
    conn = FakeConnection(value)
    result = enforce_server_posture(
        connection=conn, policy=POLICY_DISABLE, emit=_lines([]))
    assert result["status"] == STATUS_DISABLED
    assert conn.sets() == [("CONFIG", "SET", "activedefrag", "no")]
    assert conn.value == "no"
