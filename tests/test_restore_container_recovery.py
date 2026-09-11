"""Regression tests for the restore sequence's container-recovery contract (#2993).

The bug: `restore()` stops the FalkorDB container, places an RDB, then starts it
again. Recovery from a mid-sequence failure lived in an `except` clause, but the
failure paths `return` from *inside* the `try` — and a `return` inside `try`
never reaches `except`. So a failed `docker cp` (or a failed `docker start`) left
the database **stopped**, with nothing to bring it back.

That is not hypothetical: it left `falkordb-16379` down for 6 days
(agent-infra#730). Its logs show a clean `docker stop` — "User requested
shutdown" — and `docker inspect` reports `OOMKilled=false`, matching this path.

The contract these tests pin: **once `restore()` has stopped the container, every
exit path leaves it running.**
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


def test_restore_starts_container_when_start_fails(monkeypatch):
    """If the explicit start fails, recovery must still attempt a start."""
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
