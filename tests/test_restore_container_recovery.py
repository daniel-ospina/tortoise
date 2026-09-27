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

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "graph-scripts"))

import rdb_snapshot_restore  # noqa: E402

CONTAINER = "falkordb-test"
URI = "docker://:@localhost:16379/tortoise"
# #3089: a URI that carries a credential AND a named user (redis:// form).
AUTH_URI = "docker://:falkordb@localhost:16379/tortoise"
NAMED_USER_URI = "redis://alice:s3cr3t@localhost:16379/tortoise"


def _stub_non_docker_helpers(monkeypatch) -> None:
    """Neutralise everything that would touch a real graph or container."""
    monkeypatch.setattr(
        rdb_snapshot_restore, "_appendonly_state",
        lambda _c, password="", username="": {"aof_enabled": False},
        raising=False)
    monkeypatch.setattr(
        rdb_snapshot_restore, "_container_rdb_info",
        lambda _c, password="", username="": {
            "dir": "/var/lib/falkordb/data", "dbfilename": "dump.rdb"},
        raising=False)
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for", lambda _u: {"by_label": {}},
        raising=False)


def _auth_replies(args: list[str], env: dict[str, str]) -> tuple[str, int]:
    """stdout/rc for a redis-cli call against a fake requirepass server.

    Without ``REDISCLI_AUTH`` the server answers ``NOAUTH`` on STDOUT with rc 0
    — the real behaviour that let an unauthenticated path mis-parse the reply as
    config data (#3089). With it, the real CONFIG/BGSAVE/LASTSAVE replies.
    """
    if not env.get("REDISCLI_AUTH"):
        return "NOAUTH Authentication required.\n", 0
    rest = tuple(args[args.index("redis-cli") + 1:])
    if rest[:1] == ("--user",):
        # #3089: a named-user call carries `--user <name>` before the command;
        # strip it so the table lookup sees the real CONFIG/BGSAVE/LASTSAVE verb.
        rest = rest[2:]
    table = {
        ("CONFIG", "GET", "dir"): "dir\n/data\n",
        ("CONFIG", "GET", "dbfilename"): "dbfilename\ndump.rdb\n",
        ("CONFIG", "GET", "appendonly"): "appendonly\nno\n",
        ("BGSAVE",): "Background saving started\n",
        ("LASTSAVE",): "9999999999\n",
    }
    return table.get(rest, ""), 0


def _fake_docker_failing_on(fail_verb: str):
    """Return a `_docker` stand-in that fails only on `fail_verb`."""
    calls: list[str] = []

    def _docker(args, timeout=30):
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
    assert result["container_running"] is True, (
        "the recovery start succeeded, so the end state must be reported "
        "as running"
    )
    assert "STOPPED" not in result["error"]


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

    def _docker(args, timeout=30):
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
    assert result["container_running"] is True, (
        "the recovery start succeeded — the end state is running, not merely "
        "'a start was attempted'"
    )
    assert "STOPPED" not in result["error"]


def test_restore_starts_container_when_docker_raises(monkeypatch):
    """A raising `_docker` mid-sequence must not skip recovery.

    The original code recovered in `except`; the exception path is precisely
    why recovery was moved to `finally`. `_docker` raises (it does not return
    non-zero) on timeout, so this path needs its own test.
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):
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
    assert result["container_running"] is True
    assert "STOPPED" not in result["error"]


def test_restore_recovers_from_transient_start_failure(monkeypatch):
    """First `start` fails, the recovery retry succeeds — container RUNNING.

    Asserts the end state, not merely that a retry was attempted: a second
    useless start would satisfy a call-count assertion but not this one.
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []
    state = {"starts": 0, "last_start_rc": None}

    def _docker(args, timeout=30):
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


def test_restore_retries_start_when_start_raises(monkeypatch):
    """A RAISING `docker start` must still be retried.

    Honest about the end state: this fake raises on EVERY start, so the
    recovery retry fails too and the container ends STOPPED — which the
    assertions pin. The transient-recovery test above covers success.
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):
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
    assert result["container_running"] is False
    assert "STOPPED" in result["error"]


def test_restore_reports_original_error_when_recovery_also_fails(monkeypatch):
    """Second-order failure: recovery fails too, so SAY the graph is down.

    Without this the caller gets only the cp error and cannot distinguish
    "container stopped" from "graph empty" — the precursor to re-creating
    evidence onto the wrong graph (agent-infra#730).
    """
    _stub_non_docker_helpers(monkeypatch)
    calls: list[str] = []

    def _docker(args, timeout=30):
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


# ── #3089: the URI credential must reach the redis-cli transport ──────────
#
# `parse_uri` decoded the credential and both snapshot()/restore() discarded it
# (an unused-variable suppression), so every server-facing command ran through an
# unauthenticated `docker exec redis-cli`. A requirepass server answers "NOAUTH Authentication
# required." on STDOUT with rc 0, which the code then parsed as a directory name
# — so restore failed with a bogus "Could not find the file NOAUTH ..." and a
# snapshot never ran. These tests drive the real snapshot()/restore() against a
# fake that emulates that server, so the failing state is reachable, not merely
# the parsed string asserted.

def _auth_enforcing_docker(record: list):
    """A `_docker` stand-in that emulates a password-protected FalkorDB.

    Mirrors the two real behaviours the bug depended on: redis-cli answers
    NOAUTH on stdout with rc 0 unless REDISCLI_AUTH is in the child env, and
    `docker cp` to the resulting NOAUTH-shaped path fails as the daemon does.
    """
    def _docker(args, timeout=30, env=None):
        record.append((list(args), dict(env or {})))
        if "redis-cli" in args:
            stdout, rc = _auth_replies(args, dict(env or {}))
            return subprocess.CompletedProcess(args, rc, stdout=stdout,
                                               stderr="")
        if args[:1] == ["cp"] and "NOAUTH" in " ".join(args):
            return subprocess.CompletedProcess(
                args, 1, stdout="",
                stderr=("Error response from daemon: Could not find the file "
                        "NOAUTH Authentication required. in container"))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    return _docker


def _acl_docker(record: list, *, user: str = "alice",
                password: str = "s3cr3t"):
    """A `_docker` stand-in emulating a real ACL user on a requirepass server.

    Measured against falkordb/falkordb with ``ACL SETUSER alice on >pw
    allcommands allkeys``: the server replies with DATA only when redis-cli
    supplies BOTH the named ``--user alice`` AND the matching REDISCLI_AUTH. A
    wrong password, a non-existent user and a disabled user are
    indistinguishable — all three answer ``NOAUTH Authentication required.`` on
    stdout with ``AUTH failed: WRONGPASS …`` on stderr. This fake reproduces
    exactly that, so it is the strongest faithful stand-in short of a
    Docker-dependent test (which cannot run in CI); the live path is verified
    out-of-band against a throwaway container.
    """
    def _docker(args, timeout=30, env=None):
        record.append((list(args), dict(env or {})))
        if "redis-cli" in args:
            child_env = dict(env or {})
            supplied_user = (args[args.index("--user") + 1]
                             if "--user" in args else None)
            authenticated = (supplied_user == user
                             and child_env.get("REDISCLI_AUTH") == password)
            if authenticated:
                stdout, rc = _auth_replies(args, child_env)
                return subprocess.CompletedProcess(args, rc, stdout=stdout,
                                                   stderr="")
            return subprocess.CompletedProcess(
                args, 0, stdout="NOAUTH Authentication required.\n",
                stderr=("AUTH failed: WRONGPASS invalid username-password "
                        "pair or user is disabled.\n"))
        if args[:1] == ["cp"]:
            if "NOAUTH" in " ".join(args) or "WRONGPASS" in " ".join(args):
                return subprocess.CompletedProcess(
                    args, 1, stdout="",
                    stderr="Could not find the file NOAUTH in container")
            dest = args[-1]
            if ":" not in dest:  # host-side destination (snapshot's cp out)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write("REDIS0009-fake-rdb")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    return _docker


def test_restore_authenticates_to_requirepass_instance(monkeypatch):
    """#3089: restore() must drive an authenticated redis-cli transport."""
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for",
        lambda _u: {"by_label": {"Point": 2}}, raising=False)
    record: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(rdb_snapshot_restore, "_docker",
                        _auth_enforcing_docker(record))

    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        result = rdb_snapshot_restore.restore(AUTH_URI, fh.name, CONTAINER,
                                              yes=True)

    assert result["ok"] is True, result
    assert result["verified"] is True
    cli = [(a, e) for a, e in record if "redis-cli" in a]
    assert cli, "restore never invoked redis-cli"
    assert all(e.get("REDISCLI_AUTH") == "falkordb" for _, e in cli), cli
    assert all("falkordb" not in a for a, _ in cli), (
        "the password leaked into redis-cli argv")
    assert all("NOAUTH" not in " ".join(a)
               for a, _ in record if a[:1] == ["cp"]), (
        "docker cp targeted a path derived from the NOAUTH reply")


def test_snapshot_authenticates_to_requirepass_instance(monkeypatch, tmp_path):
    """#3089: snapshot() must drive an authenticated redis-cli transport.

    The BGSAVE/LASTSAVE path is deliberately NOT stubbed: a regression that
    dropped the credential from ``_bgsave_and_wait`` alone must fail this test,
    not slip through (the fake rejects every unauthenticated call, so the pin
    is the real path, not a stub's default).
    """
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for",
        lambda _u: {"by_label": {"Point": 2}}, raising=False)
    record: list[tuple[list[str], dict[str, str]]] = []

    def _docker(args, timeout=30, env=None):
        record.append((list(args), dict(env or {})))
        if "redis-cli" in args:
            stdout, rc = _auth_replies(args, dict(env or {}))
            return subprocess.CompletedProcess(args, rc, stdout=stdout,
                                               stderr="")
        if args[:1] == ["cp"]:
            if "NOAUTH" in " ".join(args):
                return subprocess.CompletedProcess(
                    args, 1, stdout="",
                    stderr="Could not find the file NOAUTH in container")
            with open(args[-1], "w", encoding="utf-8") as fh:
                fh.write("REDIS0009-fake-rdb")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    result = rdb_snapshot_restore.snapshot(AUTH_URI, str(tmp_path), CONTAINER)

    assert result["ok"] is True, result
    assert result["graph_stats"] == {"by_label": {"Point": 2}}
    assert result["uri"] == "docker://:****@localhost:16379/tortoise"
    cli = [(a, e) for a, e in record if "redis-cli" in a]
    assert cli, "snapshot never invoked redis-cli"
    # The save-and-wait path must have ACTUALLY run (not been stubbed away) and
    # carried the credential — otherwise dropping creds from _bgsave_and_wait
    # would not be caught.
    assert any(a[-1] == "BGSAVE" for a, _ in cli), cli
    assert any(a[-1] == "LASTSAVE" for a, _ in cli), cli
    assert all(e.get("REDISCLI_AUTH") == "falkordb" for _, e in cli), cli
    assert all("falkordb" not in a for a, _ in cli)


def test_auth_rejection_fails_loudly_not_as_data(monkeypatch):
    """#3089: a NOAUTH reply raises, instead of becoming a directory name.

    The URI carries no credential, so the requirepass server rejects redis-cli;
    the failure must name the cause instead of surfacing as the daemon's
    misleading "file not found".
    """
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for",
        lambda _u: {"by_label": {}}, raising=False)
    monkeypatch.setattr(
        rdb_snapshot_restore, "_docker",
        lambda args, timeout=30, env=None: subprocess.CompletedProcess(
            args, 0, stdout="NOAUTH Authentication required.\n", stderr=""))

    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh, \
            pytest.raises(RuntimeError, match="rejected"):
        rdb_snapshot_restore.restore(URI, fh.name, CONTAINER, yes=True)


def test_redis_cli_uses_env_not_argv(monkeypatch):
    """#3089: the password travels in REDISCLI_AUTH, never in argv; --user iff named."""
    seen: dict = {}

    def _docker(args, timeout=30, env=None):
        seen["args"] = list(args)
        seen["env"] = dict(env or {})
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)

    rdb_snapshot_restore._redis_cli("c", "CONFIG", "GET", "dir",
                                    password="s3cr3t", username="alice")
    assert "REDISCLI_AUTH" in seen["args"]
    assert "s3cr3t" not in " ".join(seen["args"]), "password leaked into argv"
    assert seen["env"]["REDISCLI_AUTH"] == "s3cr3t"
    assert "--user" in seen["args"] and "alice" in seen["args"]

    # anonymous docker:// form: no user, no auth material at all
    rdb_snapshot_restore._redis_cli("c", "PING")
    assert "--user" not in seen["args"]
    assert "REDISCLI_AUTH" not in seen["args"]
    assert seen["env"] == {}


def test_named_user_uri_authenticates_end_to_end(monkeypatch, tmp_path):
    """#3089: a URI naming an ACL user drives ``--user`` through BOTH phases.

    ``NAMED_USER_URI`` (``redis://alice:s3cr3t@…``) must reach redis-cli as
    ``--user alice`` plus the ``REDISCLI_AUTH`` secret — proven end-to-end
    through ``snapshot()`` THEN ``restore()``, not just the ``_redis_cli`` unit
    call. The ACL fake rejects any call missing either half, exactly as the live
    server does (see ``_acl_docker``).
    """
    monkeypatch.setattr(
        rdb_snapshot_restore, "graph_stats_for",
        lambda _u: {"by_label": {"Point": 2}}, raising=False)
    record: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _acl_docker(record))

    snap = rdb_snapshot_restore.snapshot(NAMED_USER_URI, str(tmp_path),
                                         CONTAINER)
    assert snap["ok"] is True, snap

    with tempfile.NamedTemporaryFile(suffix=".rdb") as fh:
        rest = rdb_snapshot_restore.restore(NAMED_USER_URI, fh.name, CONTAINER,
                                            yes=True)
    assert rest["ok"] is True, rest

    cli = [(a, e) for a, e in record if "redis-cli" in a]
    assert cli, "no redis-cli call was made"
    assert any(a[-1] == "BGSAVE" for a, _ in cli), cli
    assert any("appendonly" in a for a, _ in cli), cli
    for args, env in cli:
        assert "--user" in args, args
        assert args[args.index("--user") + 1] == "alice", args
        assert env.get("REDISCLI_AUTH") == "s3cr3t", env
    assert all("s3cr3t" not in a for a, _ in cli), (
        "the named-user password leaked into redis-cli argv")


def test_auth_rejection_message_includes_stderr_acl_detail(monkeypatch):
    """#3089 F3: the client's stderr reason is surfaced, not just stdout.

    Measured live: a rejected credential yields ``NOAUTH Authentication
    required.`` on stdout but the precise ``AUTH failed: WRONGPASS …`` on
    stderr. Building the message from stdout alone loses that detail — and, when
    the URI named a user, misdiagnoses a wrong/disabled ACL user as a missing
    password.
    """
    def _docker(args, timeout=30, env=None):
        return subprocess.CompletedProcess(
            args, 0, stdout="NOAUTH Authentication required.\n",
            stderr=("AUTH failed: WRONGPASS invalid username-password pair "
                    "or user is disabled.\n"))

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with pytest.raises(RuntimeError) as excinfo:
        rdb_snapshot_restore._redis_cli("c", "CONFIG", "GET", "dir",
                                        password="s3cr3t", username="alice")
    message = str(excinfo.value)
    assert "WRONGPASS" in message, message
    assert "alice" in message, message
    assert "supply the password" not in message, (
        "the password WAS supplied — a wrong/disabled USER is misdiagnosed as "
        f"a missing password: {message}")
    assert "s3cr3t" not in message, "the credential leaked into the error"


def test_auth_rejection_message_asks_for_password_when_none_supplied(monkeypatch):
    """#3089 F3: with no credential at all, the hint asks for the password."""
    def _docker(args, timeout=30, env=None):
        return subprocess.CompletedProcess(
            args, 0, stdout="NOAUTH Authentication required.\n", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    with pytest.raises(RuntimeError) as excinfo:
        rdb_snapshot_restore._redis_cli("c", "CONFIG", "GET", "dir")
    assert "no password was supplied" in str(excinfo.value)


def test_marker_inside_config_value_is_not_a_rejection(monkeypatch):
    """#3089 F4: a value CONTAINING a marker is data, not an auth error.

    ``CONFIG GET dir`` legitimately returns ``/data/NOAUTH-backups`` and a
    dbfilename may carry ``WRONGPASS``; the old whole-reply substring test
    raised on both. The predicate is shape-keyed, so the values pass through.
    """
    def _docker(args, timeout=30, env=None):
        if "dir" in args:
            return subprocess.CompletedProcess(
                args, 0, stdout="dir\n/data/NOAUTH-backups\n", stderr="")
        if "dbfilename" in args:
            return subprocess.CompletedProcess(
                args, 0, stdout="dbfilename\ndump-WRONGPASS.rdb\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(rdb_snapshot_restore, "_docker", _docker)
    info = rdb_snapshot_restore._container_rdb_info("c", password="pw")
    assert info == {"dir": "/data/NOAUTH-backups",
                    "dbfilename": "dump-WRONGPASS.rdb"}
