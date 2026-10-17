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

import sys
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
