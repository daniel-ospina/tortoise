"""#4879: a redislite `.settings` registry naming a DEAD socket must never be replayed.

redislite reuses a live daemon by loading `<dbdir>/<dbfilename>.settings` and
taking `unixsocket` from it (`_load_setting_registry()`). Its
`_is_redis_running()` guard checks that the registry file exists, that the
recorded **pidfile** exists, and that the recorded **pid** is a live process —
and it never checks that the recorded **socket file** exists. A recycled pid
therefore satisfies every check while the socket it names is gone, so the
client is pointed at a dead path and dies on first use:

    redis.exceptions.ConnectionError: Error 2 connecting to
    /tmp/tmpXXXX/redis.socket. No such file or directory.

That is the flake this file pins: it passes in isolation and fails in the full
suite because it is really cross-construction registry state, and its signature
moves with the fast-suite ORDER (the #3981 chain changed `config/ci-surfaces.yml`
from 722 to 724 registered files).

The repair lives at our construction seam (`tortoise/__init__.py`), which prunes
a registry whose recorded socket is absent before redislite reads it —
`tortoise.embedded_lifecycle.prune_dead_setting_registry` (#4879). The tests
below are written against the observable contract, not the helper: the
integration test builds the poisoned registry redislite would replay, and
asserts the construction comes up and answers.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from tortoise.embedded_lifecycle import prune_dead_setting_registry


def _dead_socket_path() -> str:
    """A path that does NOT exist, short enough to reach the OS error.

    Deliberately not `tmp_path`: on macOS a `tmp_path`-rooted socket path
    exceeds the ~104-char AF_UNIX limit, so the connect fails with
    "path too long" instead of the CI signature ("No such file or
    directory"). Using the short system tempdir makes this test reproduce
    the *reported* error on both macOS and Linux CI.
    """
    root = os.path.join(tempfile.gettempdir(), f"tortoise-4879-gone-{os.getpid()}")
    assert not os.path.exists(root), "stale fixture dir from a previous run"
    return os.path.join(root, "r.sock")


def _write_registry(db_path: str, *, socket_path: str, pidfile: str) -> str:
    """Write the `.settings` registry redislite derives from `db_path`."""
    registry = db_path + ".settings"
    with open(registry, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "pidfile": pidfile,
                "unixsocket": socket_path,
                "dbdir": os.path.dirname(db_path),
                "dbfilename": os.path.basename(db_path),
            },
            fh,
        )
    return registry


def test_prune_removes_a_registry_naming_a_missing_socket(tmp_path):
    """The registry is dropped when the socket it records does not exist."""
    db = str(tmp_path / "dead.db")
    pidfile = str(tmp_path / "dead.db.pid")
    with open(pidfile, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    registry = _write_registry(db, socket_path=_dead_socket_path(), pidfile=pidfile)

    assert prune_dead_setting_registry(db) is True
    assert not os.path.exists(registry)


def test_prune_leaves_a_registry_whose_socket_exists(tmp_path):
    """A LIVE registry is never pruned — the guard only repairs dead sockets.

    This is the anti-over-prune leg: a healthy reuse must not be turned into a
    restart (which would silently split a live daemon's state).
    """
    db = str(tmp_path / "live.db")
    pidfile = str(tmp_path / "live.db.pid")
    with open(pidfile, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    live_socket = tmp_path / "live.socket"
    live_socket.write_text("")
    registry = _write_registry(db, socket_path=str(live_socket), pidfile=pidfile)

    assert prune_dead_setting_registry(db) is False
    assert os.path.exists(registry)


def test_prune_is_a_noop_when_the_registry_is_absent(tmp_path):
    """No registry at all, or an unusable db_path, is left alone."""
    assert prune_dead_setting_registry(str(tmp_path / "never.db")) is False
    assert prune_dead_setting_registry(":memory:") is False
    assert prune_dead_setting_registry("") is False
    assert prune_dead_setting_registry(None) is False


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",  # unparseable
        '["not", "a", "dict"]',  # valid JSON, wrong top-level shape
        "{}",  # no `unixsocket` key at all
        '{"unixsocket": null}',
        '{"unixsocket": ""}',
        '{"unixsocket": ["/tmp/a"]}',  # os.path.exists(list) -> TypeError
        '{"unixsocket": {"a": 1}}',  # os.path.exists(dict) -> TypeError
        '{"unixsocket": 1.5}',  # os.path.exists(float) -> TypeError
        '{"unixsocket": 123}',  # the fd trap: exists(123) reads an OPEN FD
    ],
)
def test_prune_never_raises_and_never_prunes_an_unusable_registry(tmp_path, payload):
    """An unusable registry must be a no-op, NOT an exception.

    This helper runs on the embedded construction path before
    `super().__init__()`, so a raise here aborts the construction — exactly
    the failure it exists to prevent. The typed cases matter as much as the
    unparseable one: `os.path.exists` raises TypeError on a list/dict/float,
    and silently reads an open file descriptor for an int (a false negative
    would prune a registry on no evidence at all).
    """
    db = str(tmp_path / "shape.db")
    registry = db + ".settings"
    with open(registry, "w", encoding="utf-8") as fh:
        fh.write(payload)

    assert prune_dead_setting_registry(db) is False
    assert os.path.exists(registry), "an unusable registry must be left alone"


def test_construction_survives_a_poisoned_registry(tmp_path):
    """The #4879 reproduction: a live pid + a missing socket must not wedge us.

    Every condition redislite's own guard inspects is satisfied — the registry
    exists, the pidfile exists, the pid is live — so without the prune redislite
    replays the recorded (dead) socket and the construction dies with
    `ConnectionError: ... redis.socket. No such file or directory`. With the
    prune the registry is discarded and a clean server starts.
    """
    import tortoise

    db = str(tmp_path / "poison.db")
    pidfile = str(tmp_path / "poison.db.pid")
    with open(pidfile, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))  # a LIVE pid: passes redislite's guard
    dead_socket = _dead_socket_path()
    _write_registry(db, socket_path=dead_socket, pidfile=pidfile)
    assert not os.path.exists(dead_socket)

    client = tortoise.FalkorDB(db)
    try:
        # `execute_command` is bound from the embedded redislite client
        # (redislite/falkordb_client.py:106), so PING proves the connection.
        assert client.execute_command("PING") is True
        # The replayed path was discarded, so the client is NOT bound to it.
        assert client.client.socket_file != dead_socket
    finally:
        client.close()
