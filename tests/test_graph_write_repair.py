"""Hermetic guards for ``tools/graph_write_repair.py`` + its schedule installer.

Finding 4 of the PR #8427 review: neither new file had a test, so the ONE
property the change exists for -- "ONLY the exact reload defect authorises a
repair" -- was pinned by nothing, and the `--quiet`/count-contract (Finding 3)
had no regression cover at all. The repo's convention for this artifact type is
a dedicated guard test registered on the surface its source selects (see
``tests/test_install_reaper_schedule.py`` /
``tests/test_install_queue_reconcile_schedule.py``, and the CORE_ALSO entries
for both tool paths in ``tools/ci_selection.py``).

Everything here is hermetic: a fake graph server (no FalkorDB, no network, no
URI) for the tool, and a stubbed-``launchctl``/``plutil`` sandbox for the
installer. ``uname``/``launchctl``/``plutil`` never touch the host, and the
installer tests NEVER run against the real ``$HOME``: they copy the script into
a throwaway repo so ``REPO`` (derived from ``BASH_SOURCE``) resolves there.

Each test whose docstring carries a ``Mutation:`` line names the mutation that
turns it RED.
"""
from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import redis.exceptions as redis_exc

import tortoise.graph_delete_guard as gdg
from tools import graph_write_repair as gwr

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "tools" / "install-graph-write-repair-schedule.sh"
LABEL = "com.tortoise.graph-write-repair"

#: The v6 engine's whole abort message (anchored by `tortoise/retry.py`).
DEFECT = "graph was deleted or replaced while the query was running, aborting"

#: The threshold `repair()` refuses to swap below.
MIN_NODES = gwr.MIN_NODES

#: A node count that clears MIN_NODES, so a test only exercises the gate it names.
BIG = MIN_NODES + 500


# ── a fake graph server: enough of GRAPH.COPY/RENAME + query to run the tool ──

class _Row:
    def __init__(self, value: int) -> None:
        self.result_set = [[value]]


class _EmptyRows:
    """An empty ``CALL db.indexes()`` catalogue (no boolean index — the healthy case)."""

    def __init__(self) -> None:
        self.result_set: list = []


@dataclasses.dataclass
class _GraphState:
    nodes: int
    edges: int = 0
    writable: bool = True
    #: Raised on CREATE when `writable` is False. A real ``ResponseError`` carrying
    #: the ``DEFECT`` sentence is the only thing `graph_abort_family` accepts.
    error: BaseException | None = None
    #: How many nodes a probe CREATE lands. 2 simulates a count that moved wrongly.
    create_delta: int = 1


class _FakeGraph:
    def __init__(self, db: _FakeDb, name: str) -> None:
        self._db = db
        self.name = name

    def query(self, cypher: str, params=None):
        st = self._db.graphs.get(self.name)
        if st is None:
            # A Cypher READ on a missing graph AUTO-CREATES an empty one in FalkorDB --
            # measured live: `MATCH (n) RETURN count(n)` against an absent key returns
            # [[0]] AND the key then appears in GRAPH.LIST. The fake must reproduce that
            # rather than raise: raising makes the pre-RENAME RETENTION path unreachable, so a
            # test aimed at the DATA LOSS would redden on a message string instead of on
            # the loss itself (#8451 review, P2). Matches the engine's observed semantics.
            head = cypher.strip().upper()
            if head.startswith("MATCH") or "COUNT(" in head:
                self._db.graphs[self.name] = _GraphState(nodes=0, edges=0, writable=True)
                return _Row(0)
            raise RuntimeError(f"graph {self.name!r} does not exist")
        head = cypher.strip().upper()
        if "DB.INDEXES()" in head:
            # #3154 audit: an empty catalogue == no boolean is_operator index.
            return _EmptyRows()
        if "COUNT(N)" in head:
            return _Row(st.nodes)
        if "COUNT(R)" in head:
            return _Row(st.edges)
        if head.startswith("CREATE"):
            if not st.writable:
                raise st.error if st.error is not None else RuntimeError("write refused")
            st.nodes += st.create_delta
            return _Row(1)
        if "DELETE" in head:
            st.nodes -= 1
            return _Row(0)
        raise AssertionError(f"unexpected cypher: {cypher!r}")

    def delete(self) -> None:
        self._db.graphs.pop(self.name, None)


class _FakeDb:
    def __init__(self, graphs: dict, *, copy_drops_edges: bool = False,
                 copy_bumps_source: int = 0, copy_drops_source: bool = False,
                 copy_empties_source: bool = False,
                 copy_mixed: tuple[int, int] | None = None,
                 copy_clone_ahead: int = 0,
                 copy_makes_source_writable: bool = False,
                 swap_rename_fails: bool = False) -> None:
        self.graphs = graphs
        self.commands: list[tuple] = []
        self._copy_drops_edges = copy_drops_edges
        #: Nodes added to the SOURCE right after GRAPH.COPY, simulating a concurrent
        #: fleet write landing between the copy and the pre-RENAME re-read.
        self._copy_bumps_source = copy_bumps_source
        #: Delete the SOURCE right after GRAPH.COPY, simulating the source vanishing
        #: inside the critical section (the lock's TTL is NOT renewed, and the
        #: container is `restartpolicy=always`).
        self._copy_drops_source = copy_drops_source
        #: EMPTY the source right after GRAPH.COPY -- the source is present but has lost
        #: everything. This is the losing direction the presence check alone cannot see:
        #: presence is genuinely TRUE, and the read still yields (0, 0).
        self._copy_empties_source = copy_empties_source
        #: (nodes_delta, edges_delta) applied to the SOURCE right after GRAPH.COPY -- the
        #: MIXED case, nodes UP while edges DOWN, which is a LOSS and must retain the clone
        #: even though a node-count-only test would read it as a gain.
        self._copy_mixed = copy_mixed
        #: Build the CLONE ahead of the source by this many nodes, with the source left at its
        #: entry baseline -- the case where the clone snapshotted a concurrent write that the
        #: source has since lost, so the clone holds the only copy of that delta while the
        #: source still reads at `before_*`.
        self._copy_clone_ahead = copy_clone_ahead
        #: Flip the SOURCE writable right after GRAPH.COPY -- the defect healed (a container
        #: restart, or another repair) while this run was mid-flight, so the source is back
        #: to accepting writes and competing fleet writes can land in the window.
        self._copy_makes_source_writable = copy_makes_source_writable
        #: Raise on a RENAME whose DESTINATION is the live source (the swap) while still
        #: allowing a RENAME onto an `__gwr_*` quarantine key (the retain park).
        self._swap_rename_fails = swap_rename_fails
        # `repair()` reaches GRAPH.COPY/RENAME through `db.connection`.
        self.connection = self

    def select_graph(self, name: str) -> _FakeGraph:
        return _FakeGraph(self, name)

    def list_graphs(self) -> list[str]:
        return list(self.graphs)

    def execute_command(self, cmd: str, *args: str) -> None:
        self.commands.append((cmd, *args))
        if cmd == "GRAPH.COPY":
            src, dst = args
            st = self.graphs[src]
            # A freshly copied graph is WRITABLE even when the source is broken (the
            # defect lives in the RDB reload path, not in GRAPH.COPY).
            self.graphs[dst] = _GraphState(
                nodes=st.nodes + self._copy_clone_ahead,
                edges=0 if self._copy_drops_edges else st.edges,
                writable=True,
            )
            if self._copy_bumps_source:
                self.graphs[src].nodes += self._copy_bumps_source
            if self._copy_empties_source:
                self.graphs[src].nodes = 0
                self.graphs[src].edges = 0
            if self._copy_mixed is not None:
                self.graphs[src].nodes += self._copy_mixed[0]
                self.graphs[src].edges += self._copy_mixed[1]
            if self._copy_makes_source_writable:
                self.graphs[src].writable = True
                self.graphs[src].error = None
            if self._copy_drops_source:
                del self.graphs[src]
        elif cmd == "RENAME":
            src, dst = args
            if self._swap_rename_fails and not str(dst).startswith("__gwr_"):
                raise redis_exc.ResponseError("RENAME refused by the engine")
            self.graphs[dst] = self.graphs.pop(src)
        else:
            raise AssertionError(f"unexpected command {cmd!r}")


def _defect_state(nodes: int = BIG, edges: int = 0, **kw) -> _GraphState:
    return _GraphState(nodes=nodes, edges=edges, writable=False,
                       error=redis_exc.ResponseError(DEFECT), **kw)


@pytest.fixture
def fake_lock(monkeypatch):
    """Replace the cross-process delete lock: the lock is not the subject here."""
    @contextlib.contextmanager
    def _lock(db, **kwargs):
        yield True

    monkeypatch.setattr(gdg, "graph_delete_lock", _lock)


def _run_main(monkeypatch, db: _FakeDb, *argv: str) -> None:
    monkeypatch.setattr(gwr, "connect", lambda port: db)
    monkeypatch.setattr(sys, "argv", ["graph_write_repair.py", *argv])


def _scratch_graphs(db: _FakeDb) -> list[str]:
    return sorted(k for k in db.graphs if k.startswith("__gwr_"))


def _swaps(db: _FakeDb, source: str = "tortoise") -> list[tuple]:
    """RENAMEs that would overwrite the LIVE source.

    A retain also RENAMEs -- it parks the clone at the deterministic quarantine key -- so
    "did the swap happen?" must be asked of the DESTINATION, not merely of whether any
    `RENAME` command ran.
    """
    return [c for c in db.commands if c[0] == "RENAME" and c[2] == source]


@pytest.fixture(autouse=True)
def _no_real_cross_db_sockets(monkeypatch):
    """Pin the collision scan's SOCKET boundary, so no test opens a real one.

    `main()` always checks for the upstream collision (FalkorDB/FalkorDB#2782), and that
    check dials the host. Patching `_raw_client` -- the boundary, not the function under test
    -- keeps every `main()` case hermetic (an empty keyspace == "scan ran, no duplicates")
    while the collision tests below can still drive `cross_db_duplicates` itself.
    """
    class _EmptyKeyspaceClient:
        def execute_command(self, cmd: str, *args):
            return {} if cmd == "INFO" else 0

    monkeypatch.setattr(gwr, "_raw_client", lambda port, db: _EmptyKeyspaceClient())

# ── probe classification: ok / defect / unknown / absent ────────────────────

def test_probe_ok_and_the_probe_cleans_up_after_itself():
    """A writable graph is `ok`, and the probe node is removed again.

    Mutation: skip the DELETE cleanup -> the graph gains a node -> RED.
    """
    db = _FakeDb({"g": _GraphState(nodes=5, edges=2)})
    kind, detail = gwr.probe(db, "g")
    assert (kind, detail) == ("ok", "writes accepted")
    assert db.graphs["g"].nodes == 5, "the probe left its node behind"


def test_probe_refuses_an_empty_re_materialised_graph():
    """A 0-node graph is NOT `ok` -- it is the signature of a key re-created by a read.

    The presence check and the count read are separate statements, so a graph deleted between
    them is auto-created EMPTY by the read: presence is TRUE, the CREATE is accepted, the
    post-write re-check sees the phantom this call just made, and `probe` used to return "ok"
    on a graph holding nothing. On the clone path that phantom was then RENAMEd over the live
    graph (measured 1500 -> 0); on the `main()` path the tool printed HEALTHY with exit 0,
    which the scheduled job keys on.

    Mutation: remove the `before == 0` refusal -> this returns "ok" -> RED.
    """
    db = _FakeDb({"g": _GraphState(nodes=0, edges=0)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "unknown", (kind, detail)
    assert "0 nodes" in detail, detail
    # The probe must not have left its own node behind in the phantom.
    assert db.graphs["g"].nodes == 0, db.graphs["g"]


def test_repair_refuses_the_swap_when_the_clone_vanishes_after_its_audit(monkeypatch, fake_lock):
    """The clone is RE-VERIFIED immediately before the swap, and a gone clone refuses it.

    Everything known about the clone was established earlier. This is the last moment a check
    can run before the irreversible `RENAME`, and it is what stops an EMPTY PHANTOM being
    swapped over the live graph when the clone was deleted and a read then auto-created its
    key empty. The clone is static after its audit, so the comparison is exact.

    The clone is deleted from inside the audit -- the statement immediately preceding the
    guard -- so this drives the real window rather than a simulated one.

    Mutation: remove the pre-swap re-verification -> the phantom IS renamed over the live
    graph -> RED.
    """
    import tortoise.hosted_backup as hb

    db = _FakeDb({"tortoise": _defect_state(edges=7)})
    real = hb._audit_copied_boolean_indexes
    seen = []

    def _audit_then_vanish(*a, **k):
        out = real(*a, **k)
        seen.append(True)
        for key in [g for g in db.graphs if g.startswith("__gwr_")]:
            db.graphs.pop(key, None)
        return out

    monkeypatch.setattr(hb, "_audit_copied_boolean_indexes", _audit_then_vanish)
    ok, msg = gwr.repair(db, "tortoise")
    assert seen, "the audit did not run -- the test would not exercise the swap window"
    assert ok is False, (ok, msg)
    assert "GONE before the swap" in msg, msg
    # The live graph must NOT have been overwritten by a phantom.
    assert "RENAME" not in [c[0] for c in db.commands if len(c) > 2 and c[2] == "tortoise"], \
        db.commands
    assert db.graphs["tortoise"].nodes == BIG, "the live graph was clobbered"


def test_repair_refuses_the_swap_when_the_clone_is_re_materialised_empty(monkeypatch, fake_lock):
    """A clone that is PRESENT BUT EMPTY is refused -- the fingerprint half of the guard.

    This is the scenario the guard's comment credits with catching, and the sibling test does
    NOT reach it: deleting the clone outright trips the `graph_exists` half first. Here the
    clone's key still EXISTS (so presence is true) but holds nothing -- exactly what an
    auto-created phantom looks like after a read materialised a deleted key. Without the
    fingerprint comparison the phantom would be RENAMEd over the live graph.

    Mutation: delete the `fingerprint(...) != (before_nodes, before_edges)` refusal -> the
    empty phantom IS swapped in -> RED.
    """
    import tortoise.hosted_backup as hb

    db = _FakeDb({"tortoise": _defect_state(edges=7)})
    real = hb._audit_copied_boolean_indexes
    re_materialised = []

    def _audit_then_emptied(*a, **k):
        out = real(*a, **k)
        for key in [g for g in db.graphs if g.startswith("__gwr_")]:
            # The key survives; the CONTENT is gone -- a read materialised it empty.
            db.graphs[key] = _GraphState(nodes=0, edges=0)
            re_materialised.append(key)
        return out

    monkeypatch.setattr(hb, "_audit_copied_boolean_indexes", _audit_then_emptied)
    ok, msg = gwr.repair(db, "tortoise")
    assert re_materialised, "the clone was not re-materialised -- the guard was not driven"
    assert ok is False, (ok, msg)
    assert "no longer matches" in msg, msg
    assert "RENAME" not in [c[0] for c in db.commands if len(c) > 2 and c[2] == "tortoise"], \
        db.commands
    assert db.graphs["tortoise"].nodes == BIG, "the live graph was clobbered by the phantom"


def test_probe_absent_when_the_key_is_not_in_graph_list():
    """Presence is established with the key catalogue, never by querying an absent graph.

    Mutation: drop the `graph_exists` check -> the fake raises on the count read
    and the classification becomes `unknown` -> RED.
    """
    db = _FakeDb({})
    assert gwr.probe(db, "g") == ("absent", "graph 'g' does not exist")


def test_probe_defect_only_for_the_anchored_engine_message():
    """The whole, ANCHORED engine sentence is the defect.

    Mutation: replace `retryable_aborted_write` with `DEFECT in str(exc)` ->
    the mid-message case below is classified `defect` -> RED.
    """
    db = _FakeDb({"g": _defect_state(nodes=5)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "defect"
    assert DEFECT in detail


def test_probe_mid_message_occurrence_is_not_the_defect():
    """A parse error that merely ECHOES the sentence is not the defect (the anchor).

    Mutation: unanchored substring test -> classified `defect` -> RED.
    """
    err = redis_exc.ResponseError(f"could not parse query: {DEFECT}")
    db = _FakeDb({"g": _GraphState(nodes=5, writable=False, error=err)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "unknown"
    assert "unclassified failure" in detail


def test_probe_busy_loading_is_unknown_never_defect():
    """THE 2026-10-13 incident: a server still loading its RDB is UNKNOWN.

    A `BusyLoadingError` classified as "broken" ran the destructive swap against a
    graph that was merely not ready. Mutation: treat any exception as the defect
    -> this reds.
    """
    err = redis_exc.BusyLoadingError("Redis is loading the dataset in memory")
    db = _FakeDb({"g": _GraphState(nodes=5, writable=False, error=err)})
    kind, _ = gwr.probe(db, "g")
    assert kind == "unknown"


def test_probe_write_slot_contention_is_unknown_never_defect():
    """A LOST WRITE RACE is not the reload defect.

    `retryable_aborted_write` accepts this arm state-independently, but the graph is
    healthy and must not be swapped for a stale COPY. Mutation: classify with
    `retryable_aborted_write` (or drop the `graph_abort_family` gate) -> `defect` -> RED.
    """
    err = redis_exc.ResponseError("another write is in progress, retry the query")
    db = _FakeDb({"g": _GraphState(nodes=5, writable=False, error=err)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "unknown", detail


def test_probe_constraint_path_write_abort_is_unknown_never_defect():
    """The OTHER state-independent write-slot literal is a transient refusal too.

    Mutation: `retryable_aborted_write` -> `defect` -> RED.
    """
    err = redis_exc.ResponseError("Write query aborted: another write is in progress")
    db = _FakeDb({"g": _GraphState(nodes=5, writable=False, error=err)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "unknown", detail


def test_probe_misconf_is_unknown_never_defect():
    """`MISCONF`/`Can't persist` is a persistence outage, not the reload defect.

    Repairing it would issue a whole-graph COPY against a server refusing to persist.
    Mutation: `retryable_aborted_write` -> `defect` -> RED.
    """
    err = redis_exc.ResponseError(
        "MISCONF Errors writing to the AOF file: No space left on device")
    db = _FakeDb({"g": _GraphState(nodes=5, writable=False, error=err)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "unknown", detail


def test_probe_refuses_a_write_whose_count_did_not_move_by_one():
    """A write accepted but a count that moved by 2 is not a healthy graph."""
    db = _FakeDb({"g": _GraphState(nodes=5, writable=True, create_delta=2)})
    kind, detail = gwr.probe(db, "g")
    assert kind == "unknown"
    assert "count moved 5 -> 7" in detail


# ── exit contract: only the defect repairs ──────────────────────────────────

def test_main_unknown_exits_2_and_never_repairs(monkeypatch, capsys):
    """A non-defect write failure is UNKNOWN -> exit 2, and no COPY/RENAME happens.

    Mutation: fold "unknown" into the repair branch -> exit 0 and a swap -> RED.
    """
    err = redis_exc.BusyLoadingError("Redis is loading the dataset in memory")
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG, writable=False, error=err)})
    _run_main(monkeypatch, db)
    assert gwr.main() == 2
    assert not [c for c in db.commands if c[0] in ("GRAPH.COPY", "RENAME")], db.commands


def test_main_write_slot_contention_exits_2_and_never_repairs(monkeypatch, capsys):
    """A healthy graph whose probe CREATE merely lost the write slot is NOT repaired.

    Mutation: gate on `retryable_aborted_write` -> exit 0 with GRAPH.COPY + RENAME, and
    every fleet write between them is discarded -> RED.
    """
    err = redis_exc.ResponseError("another write is in progress, retry the query")
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG, writable=False, error=err)})
    _run_main(monkeypatch, db)
    assert gwr.main() == 2
    assert not [c for c in db.commands if c[0] in ("GRAPH.COPY", "RENAME")], db.commands


def test_main_absent_exits_3(monkeypatch, capsys):
    """An absent key is exit 3 and the tool refuses to create it.

    Mutation: `return 2` for absent -> RED.
    """
    db = _FakeDb({})
    _run_main(monkeypatch, db)
    assert gwr.main() == 3
    assert "is ABSENT" in capsys.readouterr().err


def test_main_exit_3_when_the_re_read_reports_absent(monkeypatch, capsys):
    """The absent-on-re-read branch carries the SAME exit code as the presence check.

    Mutation: fold `absent` back into `if kind in ("unknown", "absent")` ->
    exit 2 -> RED.
    """
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG)})
    monkeypatch.setattr(gwr, "probe", lambda db, name: ("absent", "gone under us"))
    _run_main(monkeypatch, db)
    assert gwr.main() == 3
    assert "ABSENT on re-read" in capsys.readouterr().err


def test_main_healthy_prints_the_counts(monkeypatch, capsys):
    """Healthy is exit 0, and the counts print so the log can answer the question."""
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG, edges=9)})
    _run_main(monkeypatch, db)
    assert gwr.main() == 0
    out = capsys.readouterr().out
    assert "HEALTHY" in out
    assert f"{BIG} nodes / 9 edges" in out


def test_main_never_traces_back_when_the_probe_raises(monkeypatch, capsys):
    """A listing failure inside probe is a named exit 2, not a traceback (P3 note 2).

    Mutation: drop the try/except around `probe` in main -> RuntimeError escapes
    and the process exits 1 with a traceback -> RED.
    """
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG)})

    def _boom(*_a, **_k):
        raise RuntimeError("GRAPH.LIST failed")

    monkeypatch.setattr(gwr, "probe", _boom)
    _run_main(monkeypatch, db)
    assert gwr.main() == 2
    assert "the probe could not complete" in capsys.readouterr().err


# ── the repair path: copy -> verify -> RENAME, and the --quiet log contract ──

def test_quiet_repair_still_logs_before_and_after_counts(monkeypatch, capsys, fake_lock):
    """Finding 3: under --quiet the PRE-repair baseline must still be logged.

    The installer arms the scheduled job with `--quiet`, so this is the only log
    the unattended repair writes; a post-repair count with no baseline cannot
    answer "did the repair preserve the data?".

    Mutation: route the counts through `say()` (verbose-gated) instead of
    `counts()` -> neither the "before repair" nor the "node count after repair"
    line prints -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state()})
    _run_main(monkeypatch, db, "--quiet")
    assert gwr.main() == 0
    out = capsys.readouterr().out

    before = [ln for ln in out.splitlines() if "before repair" in ln]
    after = [ln for ln in out.splitlines() if "node count after repair" in ln]
    assert before and str(BIG) in before[0], out
    assert after and str(BIG) in after[0], out
    assert "REPAIRED" in out
    # `--quiet` still suppresses the step NARRATION -- otherwise this test would not
    # be exercising the quiet path at all.
    assert "[1/4]" not in out, out


def test_repair_swaps_in_the_clone_and_preserves_the_data(monkeypatch, capsys, fake_lock):
    """The RENAME swap keeps every node and leaves no scratch graph behind.

    Mutation: skip the RENAME -> the graph stays unwritable -> exit 3 -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state(edges=7)})
    _run_main(monkeypatch, db)
    assert gwr.main() == 0
    assert db.graphs["tortoise"].nodes == BIG
    assert db.graphs["tortoise"].writable
    assert any(c[0] == "RENAME" for c in db.commands), db.commands
    assert _scratch_graphs(db) == [], f"scratch left behind: {db.graphs}"


def test_a_clone_that_lost_edges_is_never_swapped_in(monkeypatch, capsys, fake_lock):
    """The copy gate compares nodes AND edges (a node count alone cannot see edge loss).

    Mutation: gate on `clone_nodes != before_nodes` only -> the edge-losing clone
    is swapped in and the live graph comes back with 0 edges -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state(edges=42)}, copy_drops_edges=True)
    _run_main(monkeypatch, db)
    assert gwr.main() == 3
    assert not _swaps(db), db.commands
    assert db.graphs["tortoise"].edges == 42, "the source was modified"
    # ⛔ RETAINED, NOT DROPPED. Every path that reaches the clone helper is a FAILED repair,
    # and a count test cannot license a delete (review cycle 5: a net-zero source churn
    # passed every count test while the clone held the only copy of a lost node). The helper
    # therefore never deletes -- see its docstring.
    assert _scratch_graphs(db), "the failed repair's clone must be RETAINED, not dropped"


def test_repair_runs_the_boolean_index_audit_on_the_clone(monkeypatch, fake_lock, capsys):
    """#3154: every GRAPH.COPY site audits the boolean is_operator index before the swap.

    Mutation: drop the `_audit_copied_boolean_indexes(...)` call -> the spy records no
    call -> RED.
    """
    calls = []

    def _spy(g, *, graph_name, stage, raise_on_failure=True):
        calls.append((graph_name, stage))
        return False

    monkeypatch.setattr("tortoise.hosted_backup._audit_copied_boolean_indexes", _spy)
    db = _FakeDb({"tortoise": _defect_state()})
    _run_main(monkeypatch, db)
    assert gwr.main() == 0
    assert len(calls) == 1, calls
    assert calls[0][0].startswith("__gwr_"), calls
    assert calls[0][1] == "graph-write-repair clone", calls


def test_repair_refuses_to_swap_when_the_boolean_index_audit_fails(monkeypatch, fake_lock, capsys):
    """A clone whose boolean index survived the audit is NEVER swapped in (#3154).

    Mutation: swallow the audit's RuntimeError / swap anyway -> RENAME runs on a graph
    whose `is_operator = false` predicate is silently dead -> RED.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("boolean index survived the drop")

    monkeypatch.setattr("tortoise.hosted_backup._audit_copied_boolean_indexes", _boom)
    db = _FakeDb({"tortoise": _defect_state()})
    _run_main(monkeypatch, db)
    assert gwr.main() == 3
    assert not _swaps(db), db.commands
    # RETAINED, not dropped -- a failed repair is never cleaned up.
    assert _scratch_graphs(db), "the un-audited clone must be RETAINED, not dropped"


def test_repair_refuses_when_the_source_gained_a_node_after_the_copy(monkeypatch, capsys, fake_lock):
    """A concurrent ADDITION between COPY and RENAME refuses the swap (any change is stale).

    The pre-RENAME re-read previously refused only on a node LOSS, so a fleet write that
    ADDED a node passed the check and the older clone overwrote it -- silently discarding
    the write while reporting REPAIRED.

    Mutation: refuse only on `now_fp[0] < before_nodes` -> the RENAME runs and the source's
    extra node is lost -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state()}, copy_bumps_source=1)
    _run_main(monkeypatch, db)
    assert gwr.main() == 3
    assert not _swaps(db), db.commands
    assert db.graphs["tortoise"].nodes == BIG + 1, "the concurrent write was overwritten"
    # RETAINED, not dropped -- a failed repair is never cleaned up.
    assert _scratch_graphs(db), "the stale clone must be RETAINED, not dropped"


def test_repair_refuses_a_graph_below_min_nodes(monkeypatch, capsys, fake_lock):
    """An emptied/near-empty graph must never be "repaired" into place (the `0 < 0` guard).

    Mutation: drop the MIN_NODES check -> a 5-node graph is swapped for a 5-node
    clone and reported repaired -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state(nodes=5)})
    _run_main(monkeypatch, db)
    assert gwr.main() == 3
    assert not [c for c in db.commands if c[0] in ("GRAPH.COPY", "RENAME")], db.commands
    assert db.graphs["tortoise"].nodes == 5


def test_repair_refuses_an_absent_graph_and_creates_nothing(monkeypatch, fake_lock):
    """`repair()` re-establishes presence INSIDE the lock before any read.

    A Cypher read on a missing graph auto-creates an empty one; doing that before
    the presence check is how `before=0 -> clone 0 -> after=0 -> exit 0` reported a
    destroyed graph as repaired.

    Mutation: remove the in-lock `graph_exists` check -> the count read raises and
    the message becomes "repair aborted" instead of the ABSENT refusal -> RED.
    """
    db = _FakeDb({})
    ok, msg = gwr.repair(db, "gone")
    assert ok is False
    assert "ABSENT inside the repair" in msg
    assert db.commands == [], db.commands


def test_repair_retains_the_clone_when_the_source_vanishes_before_the_swap(monkeypatch, fake_lock):
    """A source that disappears mid-repair must NOT cost us the last copy.

    The lock's TTL is not renewed and the container is `restartpolicy=always`, so the
    source CAN vanish between the COPY and the pre-RENAME guard. If the guard reads
    first, that read AUTO-CREATES an empty graph, `now_fp` becomes (0, 0), and the
    comparison trips -- on `main` the old `drop_clone` then deleted the ONLY copy,
    leaving an empty `tortoise` and no fallback anywhere. At this head the clone IS
    retained on that path too, so the DIRECT presence check (not the clone's survival)
    is what this test pins.

    Mutation: move the presence check AFTER `fingerprint()` -> the read re-materialises
    the absent source, the direction test reports a LOSS instead of a disappearance, and
    this test goes RED on the `"disappeared" in msg` assertion.
    """
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG)}, copy_drops_source=True)
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False
    # ⛔ PIN THE PRESENCE CHECK ITSELF, not just "the clone survived". Removing the check at
    # the top of the guard leaves this test GREEN otherwise, because the direction test
    # further down also retains the clone on the auto-created (0, 0) read -- so a revert of
    # the presence guard would ship with a green suite (#8451 review cycle 2, P2).
    assert "disappeared" in msg, msg
    assert "tortoise" not in db.graphs, (
        "the absent source was re-materialised -- the presence check did not fire before "
        "the read"
    )
    assert not _swaps(db), db.commands
    # The clone -- the only surviving copy -- must still be there.
    retained = [k for k in db.graphs if k.startswith("__gwr_")]
    assert retained, f"the clone was DELETED; remaining graphs: {sorted(db.graphs)}"
    assert db.graphs[retained[0]].nodes == BIG


def test_repair_retains_the_clone_when_the_source_loses_edges_only(monkeypatch, fake_lock):
    """The LOSS test must cover EDGES, not just nodes.

    The mixed case -- nodes UP while edges DOWN -- is a LOSS, so the clone must be
    retained even though a node-count-only comparison reads it as a gain and would
    drop the last copy. Mutating `now_fp[1] < before_edges` away leaves the rest of
    the suite green (#8451 review cycle 2, P2), so this case has to be pinned
    explicitly.
    """
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG, edges=100)}, copy_mixed=(50, -60))
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False
    assert "LOST data" in msg, msg
    assert not _swaps(db), db.commands
    retained = [k for k in db.graphs if k.startswith("__gwr_")]
    assert retained, f"the clone was DELETED; remaining graphs: {sorted(db.graphs)}"
    assert db.graphs[retained[0]].nodes == BIG


def test_retain_clone_never_deletes_the_last_copy_when_the_source_is_gone(monkeypatch, fake_lock):
    """Every failure path that can run with the source gone must RETAIN the clone.

    The incomplete-copy path and the #3154 boolean-index-audit path both reach retention
    BEFORE the pre-RENAME presence check. When the source has vanished by then, the clone is
    the ONLY surviving copy -- and the #3154 case is the worse one: the clone is
    data-complete, writable and probe-passed, and only its INDEX is corrupt.

    Mutation: make `retain_clone` DROP the clone (or park nothing) instead of RENAMEs-ing it
    to the quarantine key -> each case below loses its only copy and goes RED on the
    RETAINED assertion.
    """
    # (i) the copy is incomplete (edges dropped) AND the source vanished. The source MUST
    # carry edges, or dropping them leaves `clone_fp == before` and the incomplete-copy
    # branch is never taken -- the case then exercises no `retain_clone` call at all.
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG, edges=10)}, copy_drops_edges=True,
                 copy_drops_source=True)
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False
    assert msg, "the refusal must explain itself"
    retained = [k for k in db.graphs if k.startswith("__gwr_")]
    assert retained, f"the LAST COPY was deleted; remaining graphs: {sorted(db.graphs)}"
    assert db.graphs[retained[0]].nodes == BIG
    # ⛔ PIN THE PRESENCE GUARD ITSELF. Without this assertion the guard is
    # green-without-fix: removing it leaves the suite passing, because the loss check
    # below then reads the absent key, AUTO-CREATES it empty, and retains on (0, 0) --
    # the same verdict reached by a different path. The guard's real contribution is that
    # the absent source is NOT re-materialised, so assert that (#8451 review cycle 4, P2).
    assert "tortoise" not in db.graphs, (
        "the absent source was re-materialised by a read -- the presence guard did not fire"
    )

    def _boom(*_a, **_k):
        raise RuntimeError("boolean index survived the drop")

    monkeypatch.setattr("tortoise.hosted_backup._audit_copied_boolean_indexes", _boom)
    db2 = _FakeDb({"tortoise": _defect_state(nodes=BIG)}, copy_drops_source=True)
    ok2, msg2 = gwr.repair(db2, "tortoise")
    assert ok2 is False
    assert "boolean-index audit failed" in msg2, msg2
    retained2 = [k for k in db2.graphs if k.startswith("__gwr_")]
    assert retained2, f"the LAST COPY was deleted; remaining graphs: {sorted(db2.graphs)}"
    assert db2.graphs[retained2[0]].nodes == BIG, "the retained clone lost its data"

    # (j) the source is PRESENT BUT EMPTIED -- presence is TRUE, so an absence-only
    # guard would pass and the last DATA-BEARING copy used to be deleted. This is
    # the case that defeated the first version of the guard.
    db3 = _FakeDb({"tortoise": _defect_state(nodes=BIG)}, copy_drops_edges=True,
                  copy_empties_source=True)
    ok3, msg3 = gwr.repair(db3, "tortoise")
    assert ok3 is False
    assert msg3, "the refusal must explain itself"
    retained3 = [k for k in db3.graphs if k.startswith("__gwr_")]
    assert retained3, f"the LAST COPY was deleted; remaining graphs: {sorted(db3.graphs)}"
    assert db3.graphs[retained3[0]].nodes == BIG, "the retained clone lost its data"

    # (k) same, on the #3154 path: the clone is data-complete and only its INDEX is
    # corrupt -- the worst possible copy to destroy.
    db4 = _FakeDb({"tortoise": _defect_state(nodes=BIG)}, copy_empties_source=True)
    ok4, msg4 = gwr.repair(db4, "tortoise")
    assert ok4 is False
    assert "boolean-index audit failed" in msg4, msg4
    retained4 = [k for k in db4.graphs if k.startswith("__gwr_")]
    assert retained4, f"the LAST COPY was deleted; remaining graphs: {sorted(db4.graphs)}"
    assert db4.graphs[retained4[0]].nodes == BIG, "the retained clone lost its data"


def test_a_retained_clone_keeps_the_delta_the_source_never_saw(monkeypatch, fake_lock):
    """The retained copy keeps what the source no longer has.

    The clone is built AHEAD of the source (`copy_clone_ahead=5`) while the copy drops edges,
    so `clone_fp != before` and the repair retains the clone on the incomplete-copy path. A
    retain that dropped the clone -- or parked some other graph -- would lose the only copy
    of that 5-node delta.

    Mutation: make `retain_clone` drop the clone instead of parking it -> the only copy of
    the delta is gone and this goes RED.
    """
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG, edges=100)}, copy_drops_edges=True,
                 copy_clone_ahead=5)
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False
    assert msg, "the refusal must explain itself"
    retained = [k for k in db.graphs if k.startswith("__gwr_")]
    assert retained, f"the ONLY copy of the delta was deleted; graphs: {sorted(db.graphs)}"
    # The clone must still hold what the source no longer has.
    assert db.graphs[retained[0]].nodes == BIG + 5, db.graphs[retained[0]]


def test_repair_retains_the_clone_when_the_source_is_emptied_before_the_swap(monkeypatch, fake_lock):
    """A source that LOST data is not a stale clone -- the clone must be retained.

    The presence check alone cannot close this: the source is genuinely PRESENT, so
    `graph_exists` passes, and the read still yields (0, 0). The distinction that
    matters is DIRECTION -- a source that GAINED makes the clone stale (drop is
    correct); a source that LOST nodes or edges may mean the clone is all that is
    left (retain it).

    Mutation: drop the `< before_nodes or < before_edges` direction test (treat ANY change
    as stale) -> the verdict no longer says LOST data -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG)}, copy_empties_source=True)
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False
    assert "LOST data" in msg, msg
    assert not _swaps(db), db.commands
    retained = [k for k in db.graphs if k.startswith("__gwr_")]
    assert retained, f"the clone was DELETED; remaining graphs: {sorted(db.graphs)}"
    assert db.graphs[retained[0]].nodes == BIG, "the clone lost the data it was holding"


def test_repair_refuses_a_net_zero_churn_once_the_source_accepts_writes(monkeypatch, fake_lock):
    """The count gate is EQUALITY, not identity -- a net-zero churn is invisible to it.

    A source that removes one node and adds another (a supersede is exactly that shape)
    reads back at precisely the entry baseline, so `now_fp != before` is False, the gate
    passes, and the older clone is RENAMEd in -- silently discarding the concurrent write
    while reporting a repair. The false negative only matters while the source ACCEPTS
    writes, so the swap is licensed only while the source is still REFUSING them; the source
    is re-probed immediately before the irreversible step.

    Mutation: drop the `src_kind != "defect"` re-probe -> the equal counts pass, the source
    is overwritten and this goes RED on both the verdict and the swap assertion.
    """
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG, edges=100)},
                 copy_bumps_source=-1, copy_mixed=(1, 0),
                 copy_makes_source_writable=True)
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False, msg
    assert "defect state" in msg, msg
    assert not _swaps(db), db.commands
    assert db.graphs["tortoise"].nodes == BIG, "the concurrent write was overwritten"
    assert _scratch_graphs(db), "the clone must be retained, not dropped"


def test_a_standing_failure_retains_at_most_one_clone(monkeypatch, fake_lock):
    """Retention is BOUNDED: one deterministic quarantine key, REPLACED each failure.

    The scheduled job retries every StartInterval, and the reachable standing failure is
    the #3154 boolean-index audit: if the clone keeps carrying an unrepairable index,
    every retry retains ANOTHER full copy of the graph (#8451 review, P2). The clone is
    parked at one key per source, so N failures keep ONE copy.

    Mutation: retain under the run-unique `scratch` name (no park) -> three failures leave
    three `__gwr_*` graphs and this goes RED on the exact-name assertion.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("boolean index survived the drop")

    monkeypatch.setattr("tortoise.hosted_backup._audit_copied_boolean_indexes", _boom)
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG)})
    for _ in range(3):
        ok, msg = gwr.repair(db, "tortoise")
        assert ok is False, msg
    assert _scratch_graphs(db) == [gwr.QUARANTINE_PREFIX + "tortoise"], db.graphs
    assert db.graphs[gwr.QUARANTINE_PREFIX + "tortoise"].nodes == BIG


def test_a_failing_swap_rename_parks_the_clone_instead_of_accumulating(monkeypatch, fake_lock):
    """The swap's OWN RENAME failing must park the clone too -- it is the retried path.

    The deterministic quarantine key is what bounds retention when a standing failure keeps
    the repair from succeeding. A failed swap `RENAME` is the one failure the scheduled job
    is GUARANTEED to retry (the job runs every StartInterval and the probe still classes the
    graph as the defect), so leaving the clone under its run-unique name leaked one full-graph
    copy per run and no `__gwr_q_*` ever appeared (#8451 review cycle 5, P2).

    Mutation: drop the `retain_clone(...)` call from the swap-RENAME failure branch -> three
    failures leave three `__gwr_<uuid>` graphs and no quarantine key -> RED.
    """
    q = gwr.QUARANTINE_PREFIX + "tortoise"
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG)}, swap_rename_fails=True)
    for _ in range(3):
        ok, msg = gwr.repair(db, "tortoise")
        assert ok is False, msg
        assert "RENAME failed" in msg, msg
        assert q in msg, f"the verdict must name the retained clone: {msg}"
    assert _scratch_graphs(db) == [q], db.graphs
    assert db.graphs[q].nodes == BIG


def test_a_lossier_clone_never_downgrades_a_more_complete_retained_copy(monkeypatch, fake_lock):
    """A known-incomplete clone must not replace a more complete retained copy.

    Retention parks at one key per source, and the park is an unconditional `RENAME` -- so
    the NEWEST clone always won, even from the "incomplete copy" branch, whose whole point
    is that the clone is known to be MISSING data. A later, lossier clone therefore
    destroyed an earlier, more complete retained copy while the log still asserted the
    survivor "may be the only copy of data the source no longer has" (#8451 review cycle 5,
    P2).

    Mutation: park unconditionally (drop the dominance guard) -> the second run replaces
    the (BIG, 100) retained copy with (BIG, 0) -> RED on the edges assertion.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("boolean index survived the drop")

    monkeypatch.setattr("tortoise.hosted_backup._audit_copied_boolean_indexes", _boom)
    q = gwr.QUARANTINE_PREFIX + "tortoise"
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG, edges=100)})

    # run 1: the clone is complete (BIG, 100); the #3154 audit fails, so it is parked.
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False, msg
    assert db.graphs[q].edges == 100, db.graphs[q]

    # run 2: GRAPH.COPY now loses every edge, so this run's clone is KNOWN incomplete. It
    # must not overwrite the more complete copy already retained.
    db._copy_drops_edges = True
    ok2, msg2 = gwr.repair(db, "tortoise")
    assert ok2 is False, msg2
    assert "RETAINED" in msg2, msg2
    assert db.graphs[q].edges == 100, (
        f"the retained copy was downgraded to {db.graphs[q]}")
    assert db.graphs[q].nodes == BIG


def test_an_unexpected_failure_still_parks_the_clone(monkeypatch, fake_lock):
    """The outer failure path retains too -- an unexpected raise must not leak the clone.

    Anything raised inside the repair AFTER `GRAPH.COPY` (before the swap) used to return
    with the clone still at its run-unique name, so a standing failure accumulated a full
    copy per scheduled run and no `__gwr_q_*` appeared (#8451 review cycle 5, P2).

    Mutation: drop the `retain_clone(...)` call from the outer `except Exception` -> the
    clone stays at `__gwr_<uuid>` and no quarantine key exists -> RED.
    """
    real_probe = gwr.probe

    def _probe(db, name):
        if str(name).startswith("__gwr_"):
            raise RuntimeError("unexpected probe failure")
        return real_probe(db, name)

    monkeypatch.setattr(gwr, "probe", _probe)
    q = gwr.QUARANTINE_PREFIX + "tortoise"
    db = _FakeDb({"tortoise": _defect_state(nodes=BIG)})
    ok, msg = gwr.repair(db, "tortoise")
    assert ok is False, msg
    assert "repair aborted" in msg, msg
    assert q in msg, f"the verdict must name the retained clone: {msg}"
    assert _scratch_graphs(db) == [q], db.graphs
    assert db.graphs[q].nodes == BIG




# ── the upstream collision: the same graph name in another logical DB (#2782) ──

class _FakeRawClient:
    """One logical DB of the collision scan: `INFO`/`EXISTS`/`TYPE` ONLY.

    ⛔ ANY OTHER COMMAND FAILS THE TEST. A scan that issued a Cypher read
    (`GRAPH.QUERY` / `GRAPH.RO_QUERY`) against the name in another DB would OPEN that graph,
    which IS the registry displacement the scan exists to diagnose -- the probe would brick
    the live graph it was checking. Making every other command an error is what pins that.
    """

    def __init__(self, host: _FakeRawHost, db_index: int) -> None:
        self._host = host
        self._db = db_index

    def execute_command(self, cmd: str, *args):
        self._host.issued.append((self._db, (cmd, *args)))
        if cmd == "INFO":
            if self._host.info_error is not None:
                raise self._host.info_error
            return {f"db{i}": {"keys": max(1, len(v))}
                    for i, v in sorted(self._host.keyspace.items())}
        if self._db in self._host.unreadable_dbs:
            raise RuntimeError(f"db{self._db} is unreadable")
        keys = self._host.keyspace.get(self._db, {})
        if cmd == "EXISTS":
            return 1 if args[0] in keys else 0
        if cmd == "TYPE":
            return keys[args[0]]
        raise AssertionError(
            f"the collision scan must not issue {cmd!r} -- a Cypher read against the name "
            f"in another DB is itself the displacement (#2782)")


class _FakeRawHost:
    def __init__(self, keyspace: dict[int, dict[str, bytes]], *,
                 info_error: BaseException | None = None,
                 unreadable_dbs: set[int] | None = None) -> None:
        self.keyspace = keyspace
        self.info_error = info_error
        self.unreadable_dbs = unreadable_dbs or set()
        self.issued: list[tuple[int, tuple]] = []

    def client(self, port: int, db_index: int) -> _FakeRawClient:
        return _FakeRawClient(self, db_index)


def test_cross_db_duplicates_finds_only_the_same_name_in_other_dbs(monkeypatch):
    """The scan reports same-name keys in OTHER DBs, and nothing else.

    Mutation: compare every key instead of `name`, or include the home DB -> the
    expected set changes -> RED.
    """
    host = _FakeRawHost({
        0: {"tortoise": b"graphdata", "other": b"graphdata"},
        1: {"tortoise": b"graphdata"},
        2: {"unrelated": b"graphdata"},
        3: {"tortoise": b"graphdata"},
    })
    monkeypatch.setattr(gwr, "_raw_client", host.client)
    assert gwr.cross_db_duplicates(16379, "tortoise") == [(1, b"graphdata"),
                                                          (3, b"graphdata")]


def test_cross_db_duplicates_never_issues_a_cypher_or_graph_command(monkeypatch):
    """The scan is SIDE-EFFECT FREE: `INFO`/`EXISTS`/`TYPE`, and no graph query at all.

    This is the safety property of the whole check, not a style preference: a Cypher read
    against the duplicate OPENS that graph and displaces the live one -- a diagnostic that
    would cause the defect. `_FakeRawClient` raises on any other command, so issuing one
    fails here.

    Mutation: probe the duplicate with `GRAPH.QUERY`/`GRAPH.RO_QUERY` (e.g. to report a node
    count) -> the fake raises -> RED.
    """
    host = _FakeRawHost({0: {"tortoise": b"graphdata"}, 1: {"tortoise": b"graphdata"}})
    monkeypatch.setattr(gwr, "_raw_client", host.client)
    gwr.cross_db_duplicates(16379, "tortoise")
    kinds = {cmd for _, (cmd, *_rest) in host.issued}
    assert kinds == {"INFO", "EXISTS", "TYPE"}, host.issued


def test_cross_db_duplicates_is_none_when_the_keyspace_scan_fails(monkeypatch):
    """An unreadable server is `None` ("the check did not run"), never `[]` ("clean").

    Mutation: return `[]` in the except arm -> a scan that could not run reports "no
    collision" -> RED.
    """
    host = _FakeRawHost({0: {}}, info_error=RuntimeError("connection refused"))
    monkeypatch.setattr(gwr, "_raw_client", host.client)
    assert gwr.cross_db_duplicates(16379, "tortoise") is None


def test_cross_db_duplicates_skips_an_unreadable_db_and_reports_the_rest(monkeypatch):
    """One unreadable DB does not hide the collisions in the others.

    Mutation: let the per-DB exception escape (or abort the loop) -> the db3 duplicate is
    lost -> RED.
    """
    host = _FakeRawHost({
        0: {"tortoise": b"graphdata"},
        1: {"tortoise": b"graphdata"},
        3: {"tortoise": b"graphdata"},
    }, unreadable_dbs={1})
    monkeypatch.setattr(gwr, "_raw_client", host.client)
    assert gwr.cross_db_duplicates(16379, "tortoise") == [(3, b"graphdata")]


def test_collision_message_names_the_upstream_and_the_loud_clear():
    """The report is the artifact an operator reads: it names the upstream cause and the
    exact DURABLE clear, and it is EMPTY when there is no collision.

    Mutation: drop the upstream reference or the `DEL` recipe -> RED.
    """
    assert gwr.collision_message("tortoise", []) == ""
    msg = gwr.collision_message("tortoise", [(1, b"graphdata"), (2, b"graphdata")])
    assert "FalkorDB/FalkorDB#2782" in msg
    assert "db1" in msg and "db2" in msg
    assert "DEL tortoise" in msg
    # It must say the write-lock is the SYMPTOM of the collision, not the other way round.
    assert "CAUSE" in msg


def test_main_reports_the_collision_even_when_the_graph_is_healthy(monkeypatch, capsys):
    """A HEALTHY graph holding a collision is one restart from the defect, so it is reported.

    Mutation: move the scan inside the unwritable branch -> a healthy-but-colliding graph
    exits 0 silently -> RED.
    """
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG, edges=9)})
    _run_main(monkeypatch, db)
    monkeypatch.setattr(gwr, "cross_db_duplicates",
                        lambda *a, **k: [(1, b"graphdata")])
    assert gwr.main() == 0
    err = capsys.readouterr().err
    assert "FalkorDB/FalkorDB#2782" in err
    assert "WILL return" in err


def test_main_names_the_collision_as_the_cause_when_the_graph_is_unwritable(
        monkeypatch, capsys):
    """On the defect path the collision is reported as the CAUSE and the clear is printed.

    Mutation: report only the generic "reload defect" wording -> the operator is sent to look
    at the RDB load path instead of the colliding key -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state()})
    _run_main(monkeypatch, db, "--check")
    monkeypatch.setattr(gwr, "cross_db_duplicates",
                        lambda *a, **k: [(1, b"graphdata"), (2, b"graphdata")])
    assert gwr.main() == 3
    err = capsys.readouterr().err
    assert "FalkorDB/FalkorDB#2782" in err
    assert "DURABLE CLEAR" in err


def test_main_says_the_repair_is_temporary_while_a_collision_exists(
        monkeypatch, capsys, fake_lock):
    """A successful RENAME with a live collision must NOT claim the repair is durable.

    Mutation: keep the old unconditional "recurs on the next container restart (upstream, RDB
    load path)" note -> the cause and the fix are hidden from the operator -> RED.
    """
    db = _FakeDb({"tortoise": _defect_state()})
    _run_main(monkeypatch, db)
    monkeypatch.setattr(gwr, "cross_db_duplicates",
                        lambda *a, **k: [(1, b"graphdata")])
    assert gwr.main() == 0
    captured = capsys.readouterr()
    assert "TEMPORARY" in captured.err
    assert "FalkorDB/FalkorDB#2782" in captured.err


def test_main_keeps_the_generic_recurs_note_without_a_collision(
        monkeypatch, capsys, fake_lock):
    """With no collision the previously documented residual note is unchanged."""
    db = _FakeDb({"tortoise": _defect_state()})
    _run_main(monkeypatch, db)
    assert gwr.main() == 0
    assert "recurs on the next container restart" in capsys.readouterr().out


@pytest.mark.parametrize("quiet", [False, True], ids=["loud", "quiet"])
def test_main_says_so_when_the_collision_scan_could_not_run(
        monkeypatch, capsys, quiet: bool):
    """`None` must not read as "no collision": an unrun check is said out loud.

    ⛔ INCLUDING UNDER `--quiet` -- that is the ONLY mode the launchd job runs in
    (installer `ProgramArguments`: `<PY> <TOOL> --quiet`), so a note routed through
    `say()` is invisible in exactly the log an unattended run produces, and an unrun
    scan would be byte-identical to a clean one.

    Mutation: treat `None` like `[]` -> the log claims a clean scan that never
    happened -> RED. Mutation (the pre-fix shape): route the note through `say()`
    -> it lands on stdout, not on the stderr this test reads -> BOTH parametrized
    cases RED (measured: 2 failed). The pre-fix version of this test drove only the
    loud path and asserted on stdout, so it PASSED against that source -- the
    parametrization over `--quiet` is what turns the contract into a gate.
    """
    db = _FakeDb({"tortoise": _GraphState(nodes=BIG, edges=9)})
    _run_main(monkeypatch, db, *(["--quiet"] if quiet else []))
    monkeypatch.setattr(gwr, "cross_db_duplicates", lambda *a, **k: None)
    assert gwr.main() == 0
    captured = capsys.readouterr()
    assert "could not scan other logical DBs" in captured.err
    assert "did NOT run" in captured.err
    # The note is a FACT about the graph, so it is not narration: nothing about it may
    # ride on `--quiet`. It must not reappear on stdout, where `say()` would put it.
    assert "could not scan other logical DBs" not in captured.out


# ── the installer: CLI contract, escaping, lint-before-move, load verification ──

def _write_stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sandbox(tmp_path: Path, home: Path | None = None) -> dict:
    """Throwaway HOME + PATH stubs for `launchctl`/`plutil`.

    `launchctl` only RECORDS invocations and its `print` exit code is
    env-controlled, so the install path never touches the host's user agent.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    home = home or (tmp_path / "home")
    home.mkdir(parents=True, exist_ok=True)
    logdir = tmp_path / "log"
    logdir.mkdir()

    _write_stub(
        bindir, "launchctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "launchctl $*" >> "$STUB_LOG/launchctl.log"\n'
        'case "$1" in\n'
        '  print) exit "${STUB_PRINT_EXIT:-0}" ;;\n'
        '  *) exit 0 ;;\n'
        "esac\n",
    )
    _write_stub(bindir, "plutil", '#!/usr/bin/env bash\nexit "${STUB_LINT_EXIT:-0}"\n')

    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "STUB_LOG": str(logdir),
    })
    return {
        "env": env,
        "home": home,
        "log": logdir,
        "launchctl_log": logdir / "launchctl.log",
        "plist": home / "Library" / "LaunchAgents" / f"{LABEL}.plist",
    }


def _repo_copy(tmp_path: Path, subpath: str = "repo") -> Path:
    """A throwaway repo whose ``REPO`` (from BASH_SOURCE) is NOT the worktree.

    `REPO` is derived from the script's own path, so copying the script is what
    makes the installer address the sandbox instead of the real checkout.
    """
    repo = tmp_path / subpath
    (repo / "tools").mkdir(parents=True)
    script = repo / "tools" / INSTALLER.name
    shutil.copy(INSTALLER, script)
    (repo / "tools" / "graph_write_repair.py").write_text("# stub tool\n", encoding="utf-8")
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)
    return script


def _run(sb: dict, script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args],
        env=sb["env"], capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )


def test_installer_refuses_to_apply_from_a_lane_worktree(tmp_path):
    """`--apply` from a `/.worktrees/` path refuses before any launchctl call.

    Mutation: drop the worktree guard -> the sandbox plist is written and
    `launchctl bootstrap` runs -> RED.
    """
    sb = _sandbox(tmp_path)
    script = _repo_copy(tmp_path, "lane/.worktrees/fix")
    res = _run(sb, script, "--apply")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "refusing to install from a lane worktree" in res.stderr
    assert not sb["launchctl_log"].exists(), "the refusal still called launchctl"
    assert not sb["plist"].exists(), "the refusal still wrote a plist"


def test_installer_rejects_a_non_integer_or_too_small_interval(tmp_path):
    """`--interval` is validated BEFORE it is interpolated into `<integer>`.

    Mutation: remove the integer/regex guard -> a `5m` value is rendered and the
    plist is written -> RED.
    """
    sb = _sandbox(tmp_path)
    script = _repo_copy(tmp_path)
    for value, needle in (("5m", "must be an integer"), ("29", ">= 30s"), ("0", ">= 30s")):
        res = _run(sb, script, "--apply", "--interval", value)
        assert res.returncode == 2, (value, res.stdout, res.stderr)
        assert needle in res.stderr, (value, res.stderr)
        assert not sb["plist"].exists(), f"interval {value!r} still wrote a plist"


def test_installer_interval_without_a_value_refuses(tmp_path):
    """A trailing `--interval` is a usage error, never a silently unchanged run."""
    sb = _sandbox(tmp_path)
    script = _repo_copy(tmp_path)
    res = _run(sb, script, "--apply", "--interval")
    assert res.returncode != 0
    assert "--interval needs a value" in res.stderr, res.stderr


def test_installer_xml_escapes_paths_in_the_rendered_plist(tmp_path):
    """A HOME carrying XML metacharacters is escaped in the rendered plist.

    Mutation: drop `xml_escape` -> raw `&`/`<`/`>` land in the plist, which
    `plutil` rejects -> RED.
    """
    home = tmp_path / "ho&me<x"
    home.mkdir()
    sb = _sandbox(tmp_path, home=home)
    script = _repo_copy(tmp_path)
    res = _run(sb, script)  # dry run: renders, installs nothing
    assert res.returncode == 0, (res.stdout, res.stderr)
    # Only the RENDERED plist is asserted: the script's own `plist: …` line echoes the
    # raw path (that is a shell report, not XML).
    rendered = res.stdout.split("<?xml", 1)[1]
    assert "ho&amp;me&lt;x" in rendered, res.stdout
    assert "ho&me<x" not in rendered, res.stdout


def test_installer_lints_before_moving_into_place(tmp_path):
    """A plist that fails `plutil -lint` is never left at the install path.

    Mutation: render straight to `$PLIST` and lint after -> a rejected plist is
    on disk where launchd can try to load it -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["STUB_LINT_EXIT"] = "1"
    script = _repo_copy(tmp_path)
    res = _run(sb, script, "--apply")
    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "rendered plist is invalid" in res.stderr
    assert not sb["plist"].exists(), "the invalid plist was moved into place"
    assert list(sb["plist"].parent.glob("*.tmp.*")) == [], "a temp plist leaked"


def test_installer_verifies_the_job_actually_loaded(tmp_path):
    """A bootstrap that did not load the job is exit 3, not a silent success.

    Mutation: drop the post-bootstrap `launchctl print` verification -> the run
    reports success with no job loaded -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["STUB_PRINT_EXIT"] = "1"
    script = _repo_copy(tmp_path)
    res = _run(sb, script, "--apply")
    assert res.returncode == 3, (res.stdout, res.stderr)
    assert "did NOT load after bootstrap" in res.stderr
    assert "bootstrap" in sb["launchctl_log"].read_text(encoding="utf-8")


def test_installer_arms_the_job_quiet_and_points_at_the_checkout(tmp_path):
    """The installed job runs the checkout's own interpreter + tool, with `--quiet`.

    `--quiet` is exactly why Finding 3 mattered: the scheduled run is the log with
    no pre-repair baseline. This pins the arm so the two cannot drift apart.

    Mutation: drop `<string>--quiet</string>` from ProgramArguments -> the counts
    contract is no longer exercised by the scheduled run -> RED.
    """
    sb = _sandbox(tmp_path)
    script = _repo_copy(tmp_path)
    res = _run(sb, script, "--apply")
    assert res.returncode == 0, (res.stdout, res.stderr)
    text = sb["plist"].read_text(encoding="utf-8")
    assert "<string>--quiet</string>" in text, text
    assert "<integer>300</integer>" in text, text
    assert str(tmp_path / "repo" / ".venv" / "bin" / "python") in text, text
    assert str(tmp_path / "repo" / "tools" / "graph_write_repair.py") in text, text
    assert "bootstrap" in sb["launchctl_log"].read_text(encoding="utf-8")


def test_installer_uninstall_refuses_while_the_job_is_still_loaded(tmp_path):
    """`--uninstall` verifies the bootout: a job STILL loaded keeps its plist.

    Without the verification, `rm -f` removes the plist while launchd keeps firing the job
    every StartInterval -- the repair looks uninstalled but is not. Mutation: drop the
    post-bootout `launchctl print` check -> the plist is removed -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["STUB_PRINT_EXIT"] = "0"  # still loaded after bootout
    sb["plist"].parent.mkdir(parents=True, exist_ok=True)
    sb["plist"].write_text("<plist/>", encoding="utf-8")
    script = _repo_copy(tmp_path)
    res = _run(sb, script, "--uninstall", "--apply")
    assert res.returncode == 3, (res.stdout, res.stderr)
    assert "STILL LOADED after bootout" in res.stderr, res.stderr
    assert sb["plist"].exists(), "the plist was removed while the job stayed loaded"
    assert "bootout" in sb["launchctl_log"].read_text(encoding="utf-8")


def test_installer_uninstall_removes_the_plist_once_the_job_is_gone(tmp_path):
    """`--uninstall` removes the plist only after verifying the job booted out."""
    sb = _sandbox(tmp_path)
    sb["env"]["STUB_PRINT_EXIT"] = "1"  # not loaded after bootout
    sb["plist"].parent.mkdir(parents=True, exist_ok=True)
    sb["plist"].write_text("<plist/>", encoding="utf-8")
    script = _repo_copy(tmp_path)
    res = _run(sb, script, "--uninstall", "--apply")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "uninstalled" in res.stdout, res.stdout
    assert not sb["plist"].exists(), "the plist was not removed"
