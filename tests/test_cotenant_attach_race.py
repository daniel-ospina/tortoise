"""#4879 — a mid-attach cyclic GC pass must not tear the co-tenant server down.

Deterministic, single-process reproduction of the production race that reds
``tests/test_pack_state.py::TestBackfillScript::test_apply_writes_to_introspection_read_target``:

* **construction #1** is an UNBOUND temporary — ``TortoiseSDK(db_path=...)`` left
  unclosed. Its ``FalkorProjection`` is held only by the reference cycle
  ``proj.g -> _GuardedGraph._proj -> proj`` (``tortoise/projection/__init__.py``),
  so it is reclaimed only by a **cyclic GC pass**, through the ``weakref.finalize``
  -> ``_gc_close`` -> ``cotenant_holds_server`` path.
* **construction #2** attaches to the SAME embedded server: redislite replays the
  shared ``<db>.settings`` registry, so #2's ``socket_file`` is #1's path, and it
  blocks in ``RedisMixin._wait_for_server_start()`` pinging that socket.

If the cycle is collected INSIDE that ping window, ``cotenant_holds_server`` counts
only the dying client — the ``#4487`` owner claim for #2 is written only AFTER
``RedisMixin.__init__`` returns (``embedded_lifecycle.py``) — so it reports "last
client", ``_gc_close`` takes the destructive branch (SHUTDOWN + socket-dir removal)
and unlinks #2's socket before its ping.

This test **forces** the interleaving instead of hoping for it: automatic
generation collection is disabled for the span (so no *earlier* pass can consume
the cycle and silently drop the scenario), and a once-armed wrapper around
``RedisMixin._wait_for_server_start`` runs the ONE ``gc.collect()`` at the exact
seam — inside the attach window, before the first ping. No sleeps, no timing
dependence, no whole-suite context needed.

The primary assertion is OBSERVABLE — #2 must attach and answer a query. On the
unfixed tree it dies with ``redis.exceptions.ConnectionError: Error 2 connecting
to .../redis.socket. No such file or directory``. A secondary assertion pins the
shared-branch contract: exactly ONE live writer on the RDB (the divergence a naive
"just start a fresh server" repair would introduce).

No production file is touched.
"""

from __future__ import annotations

import contextlib
import gc
import os
import weakref

from tortoise.sdk import TortoiseSDK


def _live_rdb_writers(db_dir, dbfilename):
    """Live redis-server pids whose OWN redis.config declares this RDB.

    The RDB identity is ``(dir, dbfilename)`` in the server's config, not in its
    argv (redislite's argv names the socket tempdir). Counting by argv would miss
    a second server started against the same RDB — the hazard pinned below.
    """
    from tortoise.embedded_reaper import (
        _pgrep_redis_servers,
        _read_redis_config,
        _socket_dir_from_cmdline,
    )

    want = os.path.realpath(str(db_dir))
    writers = []
    for pid in _pgrep_redis_servers():
        socket_dir = _socket_dir_from_cmdline(pid)
        if not socket_dir:
            continue
        config = _read_redis_config(socket_dir) or {}
        if config.get("dbfilename") != dbfilename:
            continue
        if os.path.realpath(config.get("dir", "")) == want:
            writers.append(pid)
    return sorted(writers)


def test_cotenant_survives_gc_mid_attach(tmp_path, monkeypatch):
    # Deterministic control, not a timing guess: with automatic generation
    # collection OFF, the ONLY cyclic pass that can touch construction #1 is the
    # explicit gc.collect() at the seam below. An automatic gen0 pass landing
    # earlier in construction #2 would collect #1 too early and the test would
    # silently stop exercising the race.
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        _run_cotenant_race(tmp_path, monkeypatch)
    finally:
        if gc_was_enabled:
            gc.enable()


def _run_cotenant_race(tmp_path, monkeypatch):
    db_path = tmp_path / "bf.db"

    # ── construction #1: an UNBOUND temporary, deliberately never closed ──
    sdk1 = TortoiseSDK(db_path=str(db_path))
    proj1 = sdk1._get_proj()
    proj_ref = weakref.ref(proj1)
    sock1 = proj1.db.client.socket_file
    created = sdk1.org_create("LegacyCo")
    org_id = created["id"]
    assert sock1 and os.path.exists(sock1)
    del sdk1, proj1
    # Now refcount-unreachable: only the proj.g <-> _GuardedGraph._proj cycle
    # keeps it alive, exactly as in the production failure.
    assert proj_ref() is not None, (
        "construction #1's projection must survive only through its reference "
        "cycle after the SDK reference is dropped"
    )

    # ── the deterministic seam: collect at the attach window's first ping ──
    import redislite.client as rclient

    original_wait = rclient.RedisMixin._wait_for_server_start
    seam = {
        "fired": False,
        "holder_alive_before": None,
        "sock_before": None,
        "holder_reaped": None,
        "sock_after": None,
    }

    def _wait_forced_collect(self, *args, **kwargs):
        if not seam["fired"]:
            seam["fired"] = True
            seam["holder_alive_before"] = proj_ref() is not None
            seam["sock_before"] = os.path.exists(sock1)
            gc.collect()
            seam["holder_reaped"] = proj_ref() is None
            seam["sock_after"] = os.path.exists(sock1)
        return original_wait(self, *args, **kwargs)

    monkeypatch.setattr(rclient.RedisMixin, "_wait_for_server_start", _wait_forced_collect)

    # ── construction #2: attach to the SAME server, across the seam ──
    sdk2 = TortoiseSDK(db_path=str(db_path), namespace=org_id)
    error = None
    rows = None
    writers = None
    try:
        rows = sdk2._get_proj().db.select_graph("control_plane").query("RETURN 1").result_set
        writers = _live_rdb_writers(tmp_path, db_path.name)
    except Exception as exc:
        error = exc
    finally:
        with contextlib.suppress(Exception):
            sdk2.close()

    assert seam["fired"], (
        "the forced-collection seam never ran inside construction #2's attach "
        "window — the reproduction did not exercise the race"
    )
    assert seam["holder_alive_before"], (
        "the leaked co-tenant must still be reachable when the attach window "
        "opens, or the GC pass cannot trigger the race"
    )
    assert seam["holder_reaped"], (
        "the forced GC pass must reclaim construction #1's leaked projection"
    )
    assert error is None, (
        "the peer co-tenant must survive a GC pass inside its attach window, "
        f"but construction #2 could not attach: {type(error).__name__}: {error}"
    )
    assert rows == [[1]], rows
    assert seam["sock_after"], "the shared server's socket must still exist after the attach window"
    assert len(writers or []) == 1, (
        "the shared-branch contract: exactly ONE live writer may hold the RDB, "
        f"but found {writers!r}"
    )
