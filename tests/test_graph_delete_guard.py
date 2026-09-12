"""#2961 — race-safe ``GRAPH.DELETE`` guard + poisoned-AOF detection.

The bug
-------
A session sweep issued ``GRAPH.QUERY <g> "MATCH (n) DETACH DELETE n"``
against a name taken from a possibly-stale ``GRAPH.LIST``. When a concurrent
session dropped that graph first, the DETACH *re-created* it as an empty
graph — and because FalkorDB persists graph writes to the AOF as **effects**,
an empty graph emits none, so the re-creation left no replayable record while
the following ``GRAPH.DELETE`` was appended. On the next load the replay had
no such graph, answered ``-ERR Invalid graph operation on empty key``, and
the shared FalkorDB container crash-looped, taking the whole docker test lane
with it (issue #2961).

These tests are hermetic — fakes only, no server, no Docker. They pin that
the guard transmits **no** graph command for an absent graph, that it
serializes the check-then-drop, that the sweep call sites inherit that
behaviour, and that the poison signature is recognised and reported with
actionable recovery steps.
"""
from __future__ import annotations

import types

import pytest

from tortoise import graph_delete_guard as g
from tortoise.graph_delete_guard import safe_graph_delete

# The AOF-load line, verbatim from a reproduced falkordb-server:v4.20.4
# crash loop (see the module docstring of tortoise/graph_delete_guard.py).
POISON_LOG = (
    "1:M 12 Sep 2026 19:28:57.632 * Reading RDB base file on AOF loading...\n"
    "1:M 12 Sep 2026 19:28:57.632 * Done loading RDB, keys loaded: 0, "
    "keys expired: 0.\n"
    "1:M 12 Sep 2026 19:28:57.632 # == CRITICAL == This server is sending "
    "an error to its AOF-loading-client: '-ERR Invalid graph operation on "
    "empty key' after processing the command 'graph.DELETE'\n"
)


# ── fakes ─────────────────────────────────────────────────────────────────

class _FakeGraph:
    def __init__(self, name: str, calls: list) -> None:
        self._name, self._calls = name, calls

    def query(self, q, *a, **k):
        self._calls.append(("query", self._name, q))
        return types.SimpleNamespace(result_set=[])

    def delete(self):
        self._calls.append(("delete", self._name))


class _FakeDb:
    """Minimal FalkorDB client: ``list_graphs`` + ``select_graph``.

    Deliberately exposes NO ``connection`` — the guard must degrade to
    unlocked operation for such fakes (and never raise for lock problems).
    """

    def __init__(self, graphs=(), calls=None) -> None:
        self._graphs = list(graphs)
        self.calls = calls if calls is not None else []

    def list_graphs(self):
        return list(self._graphs)

    def select_graph(self, name):
        return _FakeGraph(name, self.calls)


class _FakeConnection:
    """Raw redis-ish connection recording the lock protocol."""

    def __init__(self, *, grants: bool = True, fail_on: str | None = None):
        self.commands: list[tuple] = []
        self._grants = grants
        self._fail_on = fail_on

    def execute_command(self, *args):
        self.commands.append(args)
        if self._fail_on is not None and args[0] == self._fail_on:
            raise RuntimeError("injected lock failure")
        if args[0] == "SET":
            return True if self._grants else None
        return 1


class _LockingDb(_FakeDb):
    def __init__(self, *a, connection=None, **k) -> None:
        super().__init__(*a, **k)
        self.connection = connection


# ── graph_exists / safe_graph_delete ──────────────────────────────────────

def test_graph_exists_is_list_membership():
    db = _FakeDb(graphs=["test_a", "test_b"])
    assert g.graph_exists(db, "test_a") is True
    assert g.graph_exists(db, "test_missing") is False


def test_absent_graph_transmits_no_command():
    """THE regression: no DETACH, no GRAPH.DELETE for an absent graph.

    Pre-#2961 the sweep's blind DETACH created the graph here and the
    following delete poisoned the AOF.
    """
    db = _FakeDb(graphs=["test_present"])
    assert safe_graph_delete(db, "test_absent", detach=True, drop=True) is False
    assert db.calls == [], (
        "no graph command may be transmitted for an absent graph — the "
        f"DETACH is what creates the AOF-invisible phantom; got {db.calls}")


def test_present_graph_detaches_then_deletes():
    db = _FakeDb(graphs=["test_here"])
    assert safe_graph_delete(db, "test_here", detach=True, drop=True) is True
    assert db.calls == [("query", "test_here", "MATCH (n) DETACH DELETE n"),
                        ("delete", "test_here")]


def test_present_graph_delete_rides_the_command_vector():
    """#1647 cycle-6 P1-0 preserved: delete() is GRAPH.DELETE as a command,
    never ``query("GRAPH.DELETE")`` (a Cypher parse error)."""
    db = _FakeDb(graphs=["test_here"])
    safe_graph_delete(db, "test_here", detach=False, drop=True)
    assert db.calls == [("delete", "test_here")]
    assert not [c for c in db.calls if c[0] == "query"], \
        "GRAPH.DELETE must never ride GRAPH.QUERY"


def test_real_failure_still_propagates():
    """The guard must not swallow genuine errors (auth, dead connection)."""
    class _Boom(_FakeDb):
        def select_graph(self, name):
            raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError, match="connection reset"):
        safe_graph_delete(_Boom(graphs=["test_here"]), "test_here")


# ── cross-process lock ────────────────────────────────────────────────────

def test_lock_is_held_across_the_drop_and_released():
    conn = _FakeConnection()
    db = _LockingDb(graphs=["test_here"], connection=conn)
    safe_graph_delete(db, "test_here", detach=False, drop=True)
    assert [c[0] for c in conn.commands] == ["SET", "EVAL"], \
        f"expected acquire+release, got {conn.commands}"
    acquire = conn.commands[0]
    assert acquire[1] == g.GRAPH_DELETE_LOCK_KEY and "NX" in acquire \
        and "PX" in acquire, "the delete lock must be SET NX PX (self-healing)"
    assert conn.commands[-1][0] == "EVAL"
    assert conn.commands[-1][3] == g.GRAPH_DELETE_LOCK_KEY, \
        "release must target the delete lock"
    assert conn.commands[-1][4] == acquire[2], \
        "release must be compare-and-delete on OUR token, never a blind DEL"


def test_lock_released_when_the_drop_raises():
    conn = _FakeConnection()
    db = _LockingDb(graphs=["test_here"], connection=conn)
    db.select_graph = lambda name: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        safe_graph_delete(db, "test_here")
    assert conn.commands[-1][0] == "EVAL", \
        "the lock must be released even when the critical section raises"


def test_lock_waits_then_degrades_without_failing_the_sweep(caplog):
    """Another process holding the lock must never wedge or fail a sweep —
    the guard proceeds unlocked (presence re-check still applies)."""
    conn = _FakeConnection(grants=False)  # SET NX never grants
    db = _LockingDb(graphs=["test_here"], connection=conn)
    with caplog.at_level("WARNING"):
        assert safe_graph_delete(db, "test_here", detach=False, drop=True) is True
    assert db.calls == [("delete", "test_here")]
    assert not [c for c in conn.commands if c[0] == "EVAL"], \
        "a lock we never acquired must never be released"
    assert any("WITHOUT the cross-process lock" in r.message
               for r in caplog.records), "the degraded path must be loud"


def test_lock_command_failure_degrades():
    conn = _FakeConnection(fail_on="SET")
    db = _LockingDb(graphs=["test_here"], connection=conn)
    assert safe_graph_delete(db, "test_here", detach=False, drop=True) is True
    assert db.calls == [("delete", "test_here")]


def test_lock_not_taken_when_the_graph_is_absent():
    """Absence is decided before any command — including the lock."""
    conn = _FakeConnection()
    db = _LockingDb(graphs=[], connection=conn)
    assert safe_graph_delete(db, "test_absent") is False
    assert db.calls == []


# ── sweep call sites inherit the guard (#2961) ────────────────────────────

class _VanishingDb(_FakeDb):
    """``list_graphs()`` yields the graph once, then never again — a
    concurrent session's drop landing between the sweep's GRAPH.LIST and the
    drop, the exact race in #2961."""

    def __init__(self, name: str) -> None:
        super().__init__(graphs=[], calls=[])
        self._name = name
        self._lists = 0

    def list_graphs(self):
        self._lists += 1
        return [self._name] if self._lists == 1 else []


def test_wipe_server_race_emits_no_graph_delete():
    """The reported sequence: a peer drops the graph after the sweep listed
    it. Pre-#2961 this emitted DETACH (re-creating the phantom) + a
    GRAPH.DELETE that poisoned the AOF. It must now emit nothing."""
    from tests._embedded import wipe_server
    db = _VanishingDb("test_race_vanish")
    proj = types.SimpleNamespace(
        _host="localhost", _is_embedded=False,
        graph_name="test_race_vanish", db=db)
    wipe_server(proj, scope={"test_race_vanish"}, drop=True)
    assert db.calls == [], (
        "a graph that vanished after GRAPH.LIST must not be DETACHed or "
        f"GRAPH.DELETEd (that is the AOF poison); got {db.calls}")


def test_drop_one_graph_absent_is_success_without_commands():
    """An already-absent graph still counts as dropped (keep-on-partial must
    not retry it forever) — but transmits nothing."""
    from tests._embedded import _drop_one_graph
    db = _FakeDb(graphs=[])
    proj = types.SimpleNamespace(db=db)
    assert _drop_one_graph(proj, "test_gone", drop=True) is True
    assert db.calls == []


def test_drop_one_graph_present_detaches_and_deletes():
    from tests._embedded import _drop_one_graph
    db = _FakeDb(graphs=["test_here"])
    proj = types.SimpleNamespace(db=db)
    assert _drop_one_graph(proj, "test_here", drop=True) is True
    assert db.calls == [("query", "test_here", "MATCH (n) DETACH DELETE n"),
                        ("delete", "test_here")]


# ── poisoned-AOF detection + recovery guidance ────────────────────────────

def test_poison_signature_is_recognised_verbatim():
    assert g.is_poisoned_aof_log(POISON_LOG) is True


def test_poison_detection_needs_both_markers_on_one_line():
    """A bare AOF-load error naming another command is not this poison."""
    assert g.is_poisoned_aof_log(
        "== CRITICAL == This server is sending an error to its "
        "AOF-loading-client: 'ERR bad' after processing the command "
        "'graph.query'\n") is False
    assert g.is_poisoned_aof_log(
        "-ERR Invalid graph operation on empty key\n") is False


def test_poison_detection_ignores_a_clean_boot_log():
    assert g.is_poisoned_aof_log(
        "1:M * Done loading RDB, keys loaded: 0, keys expired: 0.\n"
        "1:M * Ready to accept connections tcp\n") is False
    assert g.is_poisoned_aof_log("") is False


def test_recovery_hint_names_the_container_volume_and_repair():
    hint = g.poisoned_aof_recovery_hint("falkordb", "memory_falkordb_data")
    assert "POISONED AOF DETECTED (#2961)" in hint
    assert "falkordb" in hint and "memory_falkordb_data" in hint
    # actionable: stop, quarantine, start — and warn about the data cost
    assert "docker stop falkordb" in hint
    assert "appendonlydir.corrupt" in hint
    assert "docker start falkordb" in hint
    assert "DISCARDS" in hint, "the destructive consequence must be stated"


def test_diagnose_reads_the_container_log(monkeypatch):
    monkeypatch.setattr(g, "find_container_for_port", lambda port: "falkordb")
    monkeypatch.setattr(g, "find_container_volume", lambda c: "memory_falkordb_data")
    monkeypatch.setattr(g, "_docker", lambda *a, **k: POISON_LOG)
    hint = g.diagnose_poisoned_aof(6379)
    assert hint is not None and "POISONED AOF DETECTED" in hint


def test_diagnose_returns_none_on_a_clean_log(monkeypatch):
    monkeypatch.setattr(g, "find_container_for_port", lambda port: "falkordb")
    monkeypatch.setattr(g, "_docker", lambda *a, **k: "Ready to accept connections")
    assert g.diagnose_poisoned_aof(6379) is None


def test_diagnose_returns_none_without_docker(monkeypatch):
    """No Docker / no matching container must be a silent no-op — the
    diagnosis is an aid, never a gate."""
    monkeypatch.setattr(g, "_docker", lambda *a, **k: None)
    assert g.diagnose_poisoned_aof(6379) is None


def test_docker_helper_never_raises(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise subprocess.SubprocessError("no docker")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert g._docker("ps") is None


# ── the tripwire's recovery hook (#2961 wiring) ────────────────────────────

def test_tripwire_poison_hint_is_none_for_an_unparsable_port(monkeypatch):
    """A URI whose port does not parse must short-circuit to None — the
    recovery hook may never raise into the session tripwire."""
    from tests import conftest

    monkeypatch.setattr(g, "diagnose_poisoned_aof",
                        lambda port: pytest.fail("must not be called"))
    assert conftest._poisoned_aof_hint_for_uri("http://x:notaport/y") is None


def test_tripwire_poison_hint_is_wired_to_the_diagnosis(monkeypatch):
    """The tripwire's failure path must surface the #2961 recovery message
    for the port in TORTOISE_DB_URI."""
    from tests import conftest

    seen: list[int] = []

    def _fake(port):
        seen.append(port)
        return "POISONED AOF DETECTED (#2961)"

    monkeypatch.setattr(g, "diagnose_poisoned_aof", _fake)
    hint = conftest._poisoned_aof_hint_for_uri("docker://:pw@localhost:6399")
    assert hint == "POISONED AOF DETECTED (#2961)"
    assert seen == [6399]
