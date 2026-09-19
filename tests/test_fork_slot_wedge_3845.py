"""#3845 — embedded ``GRAPH.COPY`` module-fork wedge: detection + recovery.

The wedge: FalkorDB's ``GRAPH.COPY`` forks a Redis MODULE child. On the
embedded lane that child can deadlock inside the server's own log path
(``RM_Log`` -> ``serverLogRaw`` -> ``strftime_l`` -> ``tzsetwall_basic``,
blocked on the process-global timezone rwlock inherited held across ``fork``).
The child never exits, so Redis's single module-fork slot is never released and
every later copy is refused with EEXIST / "could not fork" — forever.

These guards assert OBSERVABLES, not constants:

* a real hung ``redis-module-fork`` child is found, killed, and REAPED, and a
  real ``GRAPH.COPY`` succeeds afterwards (the EEXIST does not persist);
* a snapshot child (``redis-rdb-bgsave``) is never touched;
* a restore whose fork slot cannot be released still completes through the
  fork-free promotion, installs exactly the backed-up content, and never
  appends to a non-empty destination.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import threading
import types

import pytest
import redis as redis_mod

from tortoise.fork_slot import (
    ForkSlotRecovery,
    find_hung_module_fork_children,
    is_fork_refusal,
    recover_fork_slot,
    socket_path_of,
)
from tortoise.hosted_backup import (
    DUMP_FORMAT,
    MemoryStorage,
    _promote_payload_fork_free,
    encrypt_backup,
    restore_backup,
)

pytestmark = pytest.mark.embedded_only

_KEY = b"k" * 32
_REFUSAL = "GRAPH.COPY failed, could not fork"


# ── helpers ─────────────────────────────────────────────────────────────────


def _payload(*, graph_name: str, n_nodes: int) -> dict:
    return {
        "format": DUMP_FORMAT,
        "graph_name": graph_name,
        "node_count": n_nodes,
        "edge_count": 0,
        "nodes": [
            {"dump_id": i + 1, "labels": ["Point"],
             "props": {"id": f"pt-{i}", "content": f"c{i}",
                       "pointKind": "claim"}}
            for i in range(n_nodes)
        ],
        "edges": [],
    }


def _upload(store: MemoryStorage, *, org_id: str, graph_name: str,
            payload: dict) -> str:
    blob = encrypt_backup(json.dumps(payload).encode(), key=_KEY)
    prefix = f"backups/{org_id}/{graph_name}"
    store.upload(f"{prefix}/dump.enc", blob)
    store.upload(f"{prefix}/manifest.json", json.dumps({
        "org_id": org_id,
        "graph_name": graph_name,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }).encode())
    return f"{prefix}/dump.enc"


def _ps_command(pid: int) -> str:
    try:
        return subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except Exception:
        return ""


def _spawn_titled_child(title: str, socket_path: str) -> subprocess.Popen:
    """A REAL process whose ``ps`` title is ``title`` and argv names ``socket_path``.

    ``exec -a`` gives the single ``sleep`` process the exact title Redis sets on
    a forked GRAPH.COPY child (``redis-module-fork unixsocket:<path>``) while
    keeping it a lone process — a shell wrapping ``sleep`` would orphan the
    sleep when the shell is killed. The reaper thread is the ``waitpid`` Redis
    does: without it a SIGKILLed child is a zombie and would never disappear.
    """
    import time

    proc = subprocess.Popen([
        "/bin/sh", "-c",
        f'exec -a "{title} unixsocket:{socket_path}" /bin/sleep 300',
    ])
    threading.Thread(target=proc.wait, daemon=True).start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if title in _ps_command(proc.pid):
            return proc
        time.sleep(0.02)
    raise AssertionError(f"child {proc.pid} never took the title {title!r}")


def _drain_until(proc: subprocess.Popen, *, alive: bool, timeout_s: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (proc.poll() is None) is alive:
            return True
        time.sleep(0.05)
    return (proc.poll() is None) is alive


@pytest.fixture
def falkor(tmp_path):
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "fork_slot.db"))
    try:
        yield proj
    finally:
        with contextlib.suppress(Exception):
            proj.close()


# ── detection ───────────────────────────────────────────────────────────────


class TestForkRefusalDetection:
    def test_engine_refusal_is_recognised_through_the_exception_chain(self):
        # The exact wire strings a wedged embedded server produces.
        assert is_fork_refusal(
            redis_mod.exceptions.ResponseError(_REFUSAL))
        assert is_fork_refusal(RuntimeError("Can't fork for module: File exists"))
        # redis-py masks the response behind the torn socket — the chain is
        # what carries the truth.
        masked = ValueError("I/O operation on closed file")
        masked.__context__ = redis_mod.exceptions.TimeoutError("Timeout reading from socket")
        masked.__cause__ = redis_mod.exceptions.ResponseError(_REFUSAL)
        assert is_fork_refusal(masked)

    def test_unrelated_copy_error_is_not_a_fork_refusal(self):
        assert not is_fork_refusal(
            redis_mod.exceptions.ResponseError("destination key already exists"))
        assert not is_fork_refusal(None)


# ── recovery ────────────────────────────────────────────────────────────────


class TestForkSlotRecovery:
    def test_hung_module_fork_child_is_killed_and_the_next_copy_succeeds(self, falkor):
        """The EEXIST does not persist: real child killed+reaped, real copy OK."""
        db = falkor.db
        sock = socket_path_of(db)
        assert sock, "embedded falkordb must expose a unix socket"
        g = db.select_graph("src")
        g.query("CREATE (p:Point {id:'p0'})")

        proc = _spawn_titled_child("redis-module-fork", sock)
        try:
            assert [p for p, _ in find_hung_module_fork_children(db)] == [proc.pid]

            recovery = recover_fork_slot(db, min_age_s=0.0, timeout_s=8.0)
            assert recovery.recovered, recovery.detail
            assert recovery.killed_pids == [proc.pid]
            # Observable: the hung child is gone (killed, and reaped).
            assert _drain_until(proc, alive=False), "child still present after recovery"

            # Observable: the very next GRAPH.COPY succeeds on the same server.
            g.copy("after_recovery")
            assert "after_recovery" in db.list_graphs()
            assert find_hung_module_fork_children(db) == []
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_a_snapshot_child_is_never_killed(self, falkor):
        """Recovery must not touch a BGSAVE/RDB child — only module forks."""
        db = falkor.db
        sock = socket_path_of(db)
        assert sock
        proc = _spawn_titled_child("redis-rdb-bgsave", sock)
        try:
            # Not a module-fork child: never a candidate.
            assert find_hung_module_fork_children(db) == []
            recovery = recover_fork_slot(db, min_age_s=0.0, timeout_s=2.0)
            assert recovery.recovered is False
            # Observable: the snapshot child is still alive.
            assert proc.poll() is None, "a BGSAVE child was killed"
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_no_embedded_socket_means_no_recovery_attempt(self):
        """A server-mode handle has no child of ours to reap — fail closed."""
        recovery = recover_fork_slot(object(), min_age_s=0.0, timeout_s=0.5)
        assert recovery.wedged is True
        assert recovery.recovered is False
        assert recovery.killed_pids == []


# ── restore: wedge cannot fail the swap ─────────────────────────────────────


def _refuse_first_copy(monkeypatch, *, times: int = 1) -> dict:
    """Make the restore's GRAPH.COPY seam refuse ``times`` times, then work.

    #3813: the restore no longer calls ``Graph.copy`` — it issues
    ``GRAPH.COPY`` through ``hosted_backup._issue_graph_copy`` on its own
    derived client (its own read bound). Injecting at that seam is what makes
    the refusal actually reach ``_graph_copy_or_diagnose``; patching
    ``_EmbeddedGraphMixin.copy`` is now dead code (the same re-point the
    branch applied to ``tests/test_hosted_backup.py``).
    """
    import tortoise.hosted_backup as hb

    real = hb._issue_graph_copy
    state = {"seen": 0}

    def flaky(client, src_name, dst_name):
        if state["seen"] < times:
            state["seen"] += 1
            raise redis_mod.exceptions.ResponseError(_REFUSAL)
        return real(client, src_name, dst_name)

    monkeypatch.setattr(hb, "_issue_graph_copy", flaky)
    return state


class TestRestoreSurvivesWedge:
    def test_wedged_slot_is_released_and_reported_distinctly(self, falkor, monkeypatch):
        """Recovery releases the slot; the copy is retried and the restore is real."""
        db = falkor.db
        live = db.select_graph("org_wedge")
        live.query("CREATE (p:Point {id:'old'})")
        store = MemoryStorage()
        key = _upload(store, org_id="team_w", graph_name="org_wedge",
                      payload=_payload(graph_name="org_wedge", n_nodes=2))
        _refuse_first_copy(monkeypatch, times=1)

        import tortoise.hosted_backup as hb
        monkeypatch.setattr(
            hb, "recover_fork_slot",
            lambda *a, **kw: ForkSlotRecovery(wedged=True, recovered=True,
                                              killed_pids=[4242],
                                              detail="reaped 1 hung child"))

        result = restore_backup(db, None, store, key, org_id="team_w",
                                graph_name="org_wedge", key=_KEY, drill=True)
        assert result["restored"] == {"nodes": 2, "edges": 0}
        # Reported as a WEDGE (distinctly), with what recovery did.
        assert result["fork_slot"]["wedged"] is True
        assert result["fork_slot"]["recovered"] is True
        assert result["fork_slot"]["killed_pids"] == [4242]
        # The live graph holds the restorable content, old data gone.
        rows = live.query("MATCH (n:Point) RETURN n.id ORDER BY n.id").result_set
        assert [r[0] for r in rows] == ["pt-0", "pt-1"]

    def test_unrecoverable_wedge_promotes_fork_free_without_lying(self, falkor, monkeypatch):
        """No fork available → fork-free promotion; content exact, not temp-as-live."""
        db = falkor.db
        live = db.select_graph("org_nofork")
        live.query("CREATE (p:Point {id:'old'})")
        store = MemoryStorage()
        key = _upload(store, org_id="team_nf", graph_name="org_nofork",
                      payload=_payload(graph_name="org_nofork", n_nodes=3))
        # EVERY copy refuses: the slot cannot be released.
        _refuse_first_copy(monkeypatch, times=99)

        import tortoise.hosted_backup as hb
        monkeypatch.setattr(
            hb, "recover_fork_slot",
            lambda *a, **kw: ForkSlotRecovery(wedged=True, recovered=False,
                                              detail="held by a foreign child"))

        result = restore_backup(db, None, store, key, org_id="team_nf",
                                graph_name="org_nofork", key=_KEY, drill=True)
        assert result["restored"] == {"nodes": 3, "edges": 0}
        assert result["fork_slot"]["wedged"] is True
        assert result["fork_slot"]["recovered"] is False
        # The live graph — not the temp graph — holds exactly the payload.
        rows = live.query("MATCH (n:Point) RETURN n.id ORDER BY n.id").result_set
        assert [r[0] for r in rows] == ["pt-0", "pt-1", "pt-2"]
        assert live.query("MATCH (n) RETURN count(n)").result_set[0][0] == 3
        assert db.select_graph("org_nofork").query(
            "MATCH (n) RETURN count(n)").result_set[0][0] == 3
        scratch = [g for g in db.list_graphs() if "_restore_" in g or "_pre_restore_" in g]
        assert scratch == []

    def test_retry_exhaustion_surfaces_a_wedge_so_the_fallback_engages(
            self, falkor, monkeypatch):
        """A retry that keeps re-wedging must END as a wedge, not a raw copy error.

        The retry forks again and can lose the same race; if exhaustion leaked
        the raw ``could not fork`` response the swap would report a generic
        copy failure and never reach the fork-free promotion. Exhaustion must
        therefore be `ForkSlotWedgedError` — and the restore must still land.
        """
        db = falkor.db
        live = db.select_graph("org_retry")
        live.query("CREATE (p:Point {id:'old'})")
        store = MemoryStorage()
        key = _upload(store, org_id="team_r", graph_name="org_retry",
                      payload=_payload(graph_name="org_retry", n_nodes=2))
        _refuse_first_copy(monkeypatch, times=99)  # every retry also refuses

        import tortoise.hosted_backup as hb
        monkeypatch.setattr(
            hb, "recover_fork_slot",
            lambda *a, **kw: ForkSlotRecovery(wedged=True, recovered=True,
                                              killed_pids=[7], detail="reaped 1"))

        result = restore_backup(db, None, store, key, org_id="team_r",
                                graph_name="org_retry", key=_KEY, drill=True)
        assert result["restored"] == {"nodes": 2, "edges": 0}
        assert result["fork_slot"]["wedged"] is True
        assert [r[0] for r in live.query(
            "MATCH (n:Point) RETURN n.id ORDER BY n.id").result_set] == ["pt-0", "pt-1"]

    def test_fork_free_promotion_refuses_a_non_empty_destination(self):
        """Correctness is not weakened: never append a restore onto live data."""
        class _NonEmptyGraph:
            def __init__(self):
                self.queries: list[str] = []

            def delete(self):
                raise RuntimeError("simulated delete failure")

            def query(self, q, params=None):
                self.queries.append(q)
                return types.SimpleNamespace(result_set=[[7]])

        g = _NonEmptyGraph()
        with pytest.raises(RuntimeError):
            _promote_payload_fork_free(
                g, _payload(graph_name="g", n_nodes=1),
                live_name="g", temp_name="g_tmp",
                expected_nodes=1, expected_edges=0,
            )
        # Observable: the promotion never issued a single write (no CREATE).
        assert not [q for q in g.queries if "CREATE" in q]
