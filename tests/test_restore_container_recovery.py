"""Regression tests for the restore sequence's container-recovery contract (#2993).

The bug: `restore()` stops the FalkorDB container, places an RDB, then starts it
again. Recovery from a mid-sequence failure lived in an `except` clause, but the
failure paths `return` from *inside* the `try` — and a `return` inside `try`
never reaches `except`. So a failed `docker cp` (or a failed `docker start`) left
the database **stopped**, with nothing to bring it back.

That is not hypothetical: it left `falkordb-16379` down for 6 days
(agent-infra#730). Its logs show a clean `docker stop` — "User requested
shutdown" — and `docker inspect` reports `OOMKilled=false`, matching this path.

The contract these tests pin: **once `restore()` has stopped the container, it
makes a best-effort attempt to leave it running on every exit path** — and when
that attempt itself fails, it SAYS so (`container_running`, and a `WARNING` in
the error) rather than letting the caller mistake a stopped container for an
empty graph.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "graph-scripts"))

import rdb_snapshot_restore  # noqa: E402

CONTAINER = "falkordb-test"
URI = "docker://:@localhost:16379/tortoise"


def _stub_non_docker_helpers(monkeypatch) -> None:
    """Neutralise everything that would touch a real graph or container."""
    monkeypatch.setattr(
        rdb_snapshot_restore, "_appendonly_state",
        lambda _c: {"aof_enabled": False}, raising=False)
    monkeypatch.setattr(
        rdb_snapshot_restore, "_container_rdb_info",
        lambda _c: {"dir": "/var/lib/falkordb/data", "dbfilename": "dump.rdb"},
        raising=False)
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for", lambda _u: {"by_label": {}},
        raising=False)


def _fake_docker_failing_on(fail_verb: str):
    """Return a `_docker` stand-in that fails only on `fail_verb`."""
    calls: list[str] = []

    def _docker(args, timeout=30):  # noqa: ANN001, ARG001
        verb = args[0]
        calls.append(verb)
        failed = verb == fail_verb
        return subprocess.CompletedProcess(
            args=["docker", *args], returncode=1 if failed else 0,
            stdout="", stderr=f"simulated {verb} failure" if failed else "")

    return _docker, calls


def _run_restore(monkeypatch, fail_verb: str):
    _stub_non_docker_helpers(monkeypatch)
    fake, calls = _fake_docker_failing_on(fail_verb)
    monkeypatch.setattr(rdb_snapshot_restore, "_docker", fake)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)
    return result, calls


def test_restore_starts_container_when_cp_fails(monkeypatch):
    """A failed `docker cp` must NOT leave the container stopped.

    This is the exact shape of the 2026-09-04 outage.
    """
    result, calls = _run_restore(monkeypatch, fail_verb="cp")

    assert result["ok"] is False
    assert "stop" in calls, "precondition: the container was stopped"
    assert "start" in calls, (
        "container left STOPPED after a failed `docker cp` — this is the "
        "outage class from #2993 / agent-infra#730"
    )


def test_restore_retries_start_when_start_fails(monkeypatch):
    """A failing `docker start` gets a recovery retry.

    (Named for what it asserts — the end state here is STOPPED, since every
    start in this fake fails; the transient-recovery test below covers the
    success case.)
    """
    result, calls = _run_restore(monkeypatch, fail_verb="start")

    assert result["ok"] is False
    assert calls.count("start") >= 2, (
        "a failed `docker start` should be retried by the recovery path, "
        f"got start calls: {calls.count('start')}"
    )


def test_restore_does_not_double_start_on_success(monkeypatch):
    """On the happy path the container is started exactly once.

    The recovery must not become a redundant second start (which on a healthy
    run would be a no-op at best and confusing at worst).
    """
    _stub_non_docker_helpers(monkeypatch)
    fake, calls = _fake_docker_failing_on(fail_verb="")  # nothing fails
    monkeypatch.setattr(rdb_snapshot_restore, "_docker", fake)
    # Connectivity wait would otherwise spin for the full timeout.
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for", lambda _u: {"by_label": {}})

    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is True
    assert calls.count("start") == 1, f"expected one start, got {calls}"


def test_restore_refuses_without_yes(monkeypatch):
    """The destructive guard must fire before any docker call."""
    fake, calls = _fake_docker_failing_on(fail_verb="")
    monkeypatch.setattr(rdb_snapshot_restore, "_docker", fake)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=False)
    assert result["ok"] is False
    assert calls == [], "no docker call may happen without --yes"


def test_restore_starts_container_when_stop_times_out(monkeypatch):
    """A `docker stop` that RAISES must still trigger recovery.

    `_docker` is a bare `subprocess.run(..., timeout=...)`, so it raises
    `TimeoutExpired` at the wall rather than returning non-zero. If the stop
    sits outside the `try`, that exception escapes before the `finally` and the
    container is left down. A stop that times out may nonetheless have stopped
    the container daemon-side, so this is a real outage path.
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):  # noqa: ANN001, ARG001
        calls.append(args[0])
        if args[0] == "stop":
            raise subprocess.TimeoutExpired(cmd=["docker", *args],
                                            timeout=timeout)
        return subprocess.CompletedProcess(args=["docker", *args],
                                           returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is False
    assert "start" in calls, (
        "a raising `docker stop` skipped recovery — the container is left "
        "stopped (the hole this fix exists to close)"
    )


def test_restore_starts_container_when_docker_raises(monkeypatch):
    """A raising `_docker` mid-sequence must not skip recovery.

    The original code recovered in `except`; the exception path is precisely
    why recovery was moved to `finally`. `_docker` raises (it does not return
    non-zero) on timeout, so this path needs its own test.
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):  # noqa: ANN001, ARG001
        calls.append(args[0])
        if args[0] == "cp":
            raise subprocess.TimeoutExpired(cmd=["docker", *args],
                                            timeout=timeout)
        return subprocess.CompletedProcess(args=["docker", *args],
                                           returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is False
    assert "start" in calls, "a raising `docker cp` skipped recovery"


def test_restore_recovers_from_transient_start_failure(monkeypatch):
    """First `start` fails, the recovery retry succeeds — container RUNNING.

    Asserts the end state, not merely that a retry was attempted: a second
    useless start would satisfy a call-count assertion but not this one.
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []
    state = {"starts": 0, "last_start_rc": None}

    def _docker(args, timeout=30):  # noqa: ANN001, ARG001
        calls.append(args[0])
        rc = 0
        if args[0] == "start":
            state["starts"] += 1
            rc = 1 if state["starts"] == 1 else 0
            state["last_start_rc"] = rc
        return subprocess.CompletedProcess(args=["docker", *args],
                                           returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is False
    assert state["starts"] == 2, f"expected one retry, got {calls}"
    assert state["last_start_rc"] == 0, (
        "the recovery start must leave the container running; "
        f"last start rc was {state['last_start_rc']}"
    )


def test_restore_recovers_when_stop_returns_nonzero(monkeypatch):
    """A non-zero `docker stop` must still reach the recovery start.

    A non-zero stop can still mean the container was stopped, so returning
    past the recovery would leave it down.
    """
    _stub_non_docker_helpers(monkeypatch)
    fake, calls = _fake_docker_failing_on(fail_verb="stop")
    monkeypatch.setattr(rdb_snapshot_restore, "_docker", fake)

    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is False
    assert "stop" in calls
    assert "start" in calls, "returned past the recovery on a failed stop"
    assert result["container_running"] is True


def test_restore_recovers_when_start_raises(monkeypatch):
    """A RAISING `docker start` leaves `restarted` False and must be retried."""
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):  # noqa: ANN001, ARG001
        calls.append(args[0])
        if args[0] == "start":
            raise subprocess.TimeoutExpired(cmd=["docker", *args],
                                            timeout=timeout)
        return subprocess.CompletedProcess(args=["docker", *args],
                                           returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is False
    assert calls.count("start") == 2, (
        "a raising start must still be retried, got "
        f"{calls.count('start')} start calls"
    )


def test_restore_reports_original_error_when_recovery_also_fails(monkeypatch):
    """Second-order failure: recovery fails too, so SAY the graph is down.

    Without this the caller gets only the cp error and cannot distinguish
    "container stopped" from "graph empty" — the precursor to re-creating
    evidence onto the wrong graph (agent-infra#730).
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):  # noqa: ANN001, ARG001
        calls.append(args[0])
        if args[0] == "cp":
            return subprocess.CompletedProcess(
                args=["docker", *args], returncode=1, stdout="",
                stderr="simulated cp failure")
        if args[0] == "start":
            raise subprocess.TimeoutExpired(cmd=["docker", *args],
                                            timeout=timeout)
        return subprocess.CompletedProcess(args=["docker", *args],
                                           returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)

    assert result["ok"] is False
    assert "cp" in result["error"], (
        f"the recovery masked the root cause: {result['error']!r}"
    )
    assert result["container_running"] is False
    assert "STOPPED" in result["error"], (
        "a failed recovery must be reported, not silently absorbed"
    )
