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
import os
import subprocess
import sys
import threading
import types

import pytest
import redis as redis_mod

from tortoise.fork_slot import (
    ForkSlotRecovery,
    _list_processes,
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


# Budget for the harness waits below. The property under test is *whether* the
# child is titled / reaped, never how quickly a loaded runner gets there — so the
# budget is generous rather than tight (#4056: the previous 5.0 s window left no
# margin on a busy runner). It is a deadline, never a licence to pass: an
# untitled child still fails (TestTitleWaitIsStillStrict pins that).
_CHILD_WAIT_S = 30.0


def _ps_command(pid: int) -> str:
    """The argv of ``pid``.

    Prefers ``/proc/<pid>/cmdline``: the previous body forked a ``ps`` process
    on *every* poll — up to ~250 forks per wait, each with its own 5 s timeout,
    so one slow ``ps`` could eat the entire budget and fail the wait for a
    reason that has nothing to do with the child. The ``ps`` fallback keeps
    macOS (and any /proc-less host) working.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        pass
    else:
        # /proc answered, so trust it — including an empty read (a zombie's
        # cmdline is empty). Falling through to `ps` here would restore the
        # fork-per-poll storm this branch exists to avoid.
        return " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)
    try:
        return subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except Exception:
        return ""


def _spawn_titled_child(title: str, socket_path: str) -> subprocess.Popen:
    """A REAL process whose ``ps`` title is ``title`` and argv names ``socket_path``.

    The title is ``argv[0]`` — exactly what Redis sets on a forked GRAPH.COPY
    child (``redis-module-fork unixsocket:<path>``) and exactly what ``ps``
    reports. It is set by exec'ing ``sleep`` with that ``argv[0]``, which keeps
    the child a LONE process (a shell wrapping ``sleep`` would orphan the sleep
    when the shell is killed) and replaces the interpreter in place, so the pid
    stays valid. The reaper thread is the ``waitpid`` Redis does: without it a
    SIGKILLed child is a zombie and would never disappear.

    Why not ``/bin/sh -c 'exec -a "<title>" /bin/sleep 300'`` (the previous
    body): ``/bin/sh`` is dash on CI, dash has no ``exec -a`` (``/bin/sh: 1:
    exec: -a: not found``, rc 127), so no child is ever created and no wait
    deadline of any length can succeed — these tests could not pass on Linux;
    they only ever passed under a macOS ``/bin/sh`` (bash). The shell also
    interpolated ``socket_path`` inside double quotes, breaking on any
    ``tmp_path`` containing a space.
    """
    import time

    # The title travels via the environment on purpose: handed over as an argv
    # element it would land in the interpreter's OWN command line, which a
    # substring match would accept as the title (the wait would then pass with
    # the exec never having happened). `startswith` is the real guard — this
    # keeps the helper honest on top of it.
    env = {**os.environ, "_FORK_TITLE": f"{title} unixsocket:{socket_path}"}
    proc = subprocess.Popen([
        sys.executable, "-c",
        "import os; os.execv('/bin/sleep', [os.environ['_FORK_TITLE'], '300'])",
    ], env=env)
    threading.Thread(target=proc.wait, daemon=True).start()
    deadline = time.monotonic() + _CHILD_WAIT_S
    while time.monotonic() < deadline:
        # ``startswith``, not ``in``: the title has to BE argv[0]. A substring
        # check would also accept a command line that merely mentions it — the
        # shape that let an earlier cut of this fix "pass" without its exec ever
        # happening.
        if _ps_command(proc.pid).startswith(title):
            return proc
        time.sleep(0.05)
    # Report enough to tell a timing miss from a dead child, and do not leave the
    # child behind for the runner's teardown to reap.
    seen, was_alive = _ps_command(proc.pid), proc.poll() is None
    with contextlib.suppress(Exception):
        proc.kill()
    raise AssertionError(
        f"child {proc.pid} never took the title {title!r} within "
        f"{_CHILD_WAIT_S:.0f}s (alive={was_alive}, argv={seen!r})"
    )


def _drain_until(proc: subprocess.Popen, *, alive: bool, timeout_s: float = _CHILD_WAIT_S) -> bool:
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
            found = [p for p, _ in find_hung_module_fork_children(db)]
            assert found == [proc.pid], (
                f"the wedge scanner did not see the titled child: found={found}, "
                f"pid={proc.pid}, COLUMNS={os.environ.get('COLUMNS')!r}, "
                f"argv={_ps_command(proc.pid)!r}"
            )

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


class TestTitleWaitIsStillStrict:
    """The generous budget is a deadline, never a licence to pass (#4056)."""

    def test_an_untitled_child_still_fails_the_wait(self, monkeypatch, tmp_path):
        import time

        mod = sys.modules[__name__]
        monkeypatch.setattr(mod, "_CHILD_WAIT_S", 0.3)
        # The title never becomes visible — exactly the shape of a real
        # regression the gate exists to catch.
        monkeypatch.setattr(mod, "_ps_command", lambda pid: "")
        started = time.monotonic()
        proc = None
        try:
            with pytest.raises(AssertionError) as err:
                proc = _spawn_titled_child("redis-module-fork", str(tmp_path / "x.socket"))
        finally:
            # `_spawn_titled_child` only kills the child on its own failure path;
            # if a mutation makes the wait return, this call succeeds and hands
            # back a LIVE titled child. Own the kill either way.
            if proc is not None and proc.poll() is None:
                proc.kill()
        elapsed = time.monotonic() - started
        msg = str(err.value)
        assert "never took the title" in msg
        # The message must distinguish a timing miss from a dead child.
        assert "alive=" in msg and "argv=" in msg
        # ... and the budget really is the bound (no silent infinite wait).
        assert elapsed < 10.0, f"wait ignored its budget: {elapsed:.1f}s"

    def test_a_command_line_that_only_mentions_the_title_does_not_pass(
            self, monkeypatch, tmp_path):
        """argv[0] fidelity: the title must BE argv[0], not merely appear (#4056).

        A substring match is what lets a helper that leaks the title into its own
        interpreter command line "titled" — the wait would pass with no exec.
        """
        import time

        mod = sys.modules[__name__]
        monkeypatch.setattr(mod, "_CHILD_WAIT_S", 0.3)
        def _mentions_the_title(pid: int) -> str:
            # The leak shape: the interpreter's own command line NAMES the title
            # as an argument, so the title appears in it without being argv[0].
            return " ".join(["/usr/bin/python3", "-c", "os.execv(...)",
                             "redis-module-fork unixsocket:/x"])

        monkeypatch.setattr(mod, "_ps_command", _mentions_the_title)
        started = time.monotonic()
        proc = None
        try:
            with pytest.raises(AssertionError, match="never took the title"):
                proc = _spawn_titled_child("redis-module-fork", str(tmp_path / "x.socket"))
        finally:
            # See the sibling test: a returning wait must not leave a live child.
            if proc is not None and proc.poll() is None:
                proc.kill()
        assert time.monotonic() - started < 10.0


class TestScannerIsWidthIndependent:
    """A wedge scanner truncated by the reporting width misses real children (#4070)."""

    def test_a_long_titled_child_is_visible_at_a_narrow_width(self, monkeypatch):
        """`ps` truncates `command` to COLUMNS; the scan must ask for unlimited width.

        The failure this guards is silent: the child is real, hung and titled, but
        its socket path is cut off, so ``find_hung_module_fork_children`` reports
        nothing and the recovery concludes the slot is not wedged.
        """
        sock = "/tmp/" + "n" * 90 + "/redis.socket"  # > any 80-column window
        monkeypatch.setenv("COLUMNS", "80")
        proc = _spawn_titled_child("redis-module-fork", sock)
        try:
            # Prove the lever bites HERE before asserting the fix: a platform whose
            # `ps` ignores COLUMNS when piped (macOS) cannot truncate through this
            # code path, so passing there would be vacuous, not evidence. Skip
            # loudly instead of reporting a green that proves nothing.
            narrow = subprocess.run(
                ["ps", "-A", "-o", "pid=,ppid=,etime=,command="],
                capture_output=True, text=True,
            ).stdout
            narrow_line = next((line for line in narrow.splitlines()
                                if line.split(None, 3)[0] == str(proc.pid)), "")
            if sock in narrow_line:
                pytest.skip("ps does not truncate to COLUMNS on this platform "
                            "— #4070 is GNU-ps-specific")
            listed = {pid: cmd for pid, _ppid, _age, cmd in _list_processes()}
            assert proc.pid in listed, "the child is not listed at all (did ps run?)"
            assert sock in listed[proc.pid], (
                f"#4070: the command is {len(listed[proc.pid])} chars and lost the "
                f"socket path — `ps` was truncated to COLUMNS: {listed[proc.pid]!r}"
            )
        finally:
            if proc.poll() is None:
                proc.kill()
