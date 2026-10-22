"""#9556 / R8 — ``rebuild_from_record`` has an OPERATOR VERB, and it refuses to lie.

Measured for #9556: ``FalkorProjection.rebuild_from_record`` had **no CLI verb
and no production caller** (``grep -rn 'rebuild_from_record(' tortoise/ | grep
-v 'def '`` → 0 call sites), so *"the graph is gone, restore it"* had a method
and no operator answer. ``tortoise/record_restore.py`` is the composition and
``python -m tortoise record-restore`` is its caller — the same shape, the same
DSN resolution and the same exit-code discipline as ``record-reconcile`` (its
sibling verb).

What is pinned here, and each one is a way a restore verb is worthless:

1. **An EMPTY source is REFUSED, and the cache is provably untouched.** A view
   with no rows wipes the cache and writes nothing — the shipped path reports
   ``nodes_written: 0`` under a ``complete`` declaration, i.e. a
   successful-looking run that destroyed the cache. The live test seeds a real
   graph, then asserts the refusal AND that the graph's node is still there: a
   verb that wiped first and refused after would pass any exit-code-only pin.
2. **A rebuild that wrote nothing is refused too**, separately from (1) — the
   record HAD rows and the report says 0 written. A verb that trusted the
   return code would report that as a restore.
3. **The cache's L2 guard is reported as a REFUSAL, not a traceback** (#2944).
   It fires on every production graph name (``org_<org_id>``), so an operator
   must read the reason, not a stack trace.
4. **The verb cannot guess its target.** ``--cache-graph`` has no derived
   default: a wrong guess opens an EMPTY graph and the run would report a
   restore into a graph nobody reads (the ``record-reconcile`` rule, where the
   consequence is *worse* for a restore).
5. **No DSN / a malformed DSN is "no verdict", never "restored"** — and the DSN
   arming surface is the writer's ``record_dsn``, not a second spelling here.
6. **The verb CALLS the composition** (an AST pin — the ``record-reconcile``
   lesson: an import-only pin is satisfied by a body that returns a constant).
7. **The restore is bounded by the record, not by the journal** — the
   composition cannot reach ``rebuild_all`` (R8's indicator: 0 restore steps
   that read the journal instead of the record).
8. **A bounded restore declares ``partial``** and reports the unwritten keys as
   ``not-hot`` (§17.3), so a green exit never reads as "the whole cache is
   back", and **a live restore puts the record's state in the cache** (the
   pre-existing node is gone, the record's nodes are there).

⚠️ This file is registered in ``config/ci-surfaces.yml`` under BOTH ``api`` and
``core``, for the reason ``test_record_reconcile_trigger.py`` is: the caller
lives in ``tortoise/__main__.py`` — an EXACT ``api`` SOURCE_PATTERN whose match
REPLACES the ``core`` fallback — while ``record_restore.py`` matches no
SOURCE_PATTERN at all. One registration alone leaves the pin unexecuted on the
change that breaks the other half (#1349/#2938/#3154).
"""
from __future__ import annotations

import ast
import contextlib
import json
import sys
import types
import uuid
from pathlib import Path

import pytest

from tortoise import cache_projection as cp
from tortoise import record_restore as rrestore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _REPO_ROOT / "tortoise" / "record_restore.py"
_CLI_PATH = _REPO_ROOT / "tortoise" / "__main__.py"

_TARGET = "test_9556_target"


# ── fixtures / fakes ───────────────────────────────────────────────────────


@pytest.fixture
def cache_graph(shared_proj):
    """A per-test cache graph, so a restore's counts are only THIS test's."""
    if shared_proj is None:
        pytest.skip("no graph engine available on this lane")
    graph = shared_proj.db.select_graph(f"test_9556_{uuid.uuid4().hex[:8]}")
    try:
        yield graph
    finally:
        with contextlib.suppress(Exception):
            graph.delete()


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _RecordingProjection:
    """A projection whose ``rebuild_from_record`` the test can observe.

    Only the projection is faked — the composition, its guards, the exit-code
    mapping and the CLI wiring are the shipped ones.
    """

    def __init__(self, *, result: dict | None = None,
                 raises: BaseException | None = None,
                 guard_refuses: bool = False) -> None:
        self.g = None
        self.closed = False
        self.calls: list[dict] = []
        self._result = result
        self._raises = raises
        self._guard_refuses = guard_refuses

    def _assert_test_graph(self, reason: str = "", *, graph_name=None):
        """The projection's L2 guard, faked so a refusal is observable."""
        if self._guard_refuses:
            raise RuntimeError(
                "Graph guard: operation blocked on non-test graph "
                "'org_acme'. "
                f"{reason} — destructive bulk ops require a graph name "
                "starting with 'test_' or 'tortoise_test'.")

    def rebuild_from_record(self, view, **kwargs):
        self.calls.append({"view": view, **kwargs})
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return dict(self._result)
        nodes = len(view.nodes)
        bounded = kwargs.get("limit") is not None
        written = nodes if not bounded else min(nodes, kwargs["limit"])
        return {
            "nodes_written": written,
            "relations_written": len(view.relations),
            "statements": 0,
            "record_nodes": nodes,
            "record_relations": len(view.relations),
            "coverage": "partial" if bounded else "complete",
            "declared_keys": written,
            "source": "record",
        }

    def close(self) -> None:
        self.closed = True


def _view(n: int = 2) -> cp.RecordView:
    return cp.RecordView(nodes=tuple(
        cp.RecordNode(label="Point", key=f"p-9556-{i}",
                      props={"id": f"p-9556-{i}", "content": f"c{i}",
                             "status": "live"})
        for i in range(n)))


def _install_fakes(monkeypatch, *, view, projection):
    """Fake only the two HANDLES: the record connection and the projection."""
    from tortoise.projection import FalkorProjection

    conn = _FakeConn()

    def _connect(dsn, **kw):
        return conn

    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2.connect = _connect                    # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
    monkeypatch.setattr(
        cp, "read_record_view",
        lambda conn, *, org_id, graph_id: view, raising=True)
    monkeypatch.setattr(
        FalkorProjection, "from_uri",
        staticmethod(lambda uri, graph_name=None: projection))
    return conn


def _drive(monkeypatch, *, view, projection=None, extra_args=()):
    """Run ``python -m tortoise record-restore``; return (rc, conn, projection)."""
    from tortoise import __main__ as cli

    projection = projection if projection is not None else _RecordingProjection()
    conn = _install_fakes(monkeypatch, view=view, projection=projection)
    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:6379/g",
                   "--record-dsn", "postgresql://fake/record",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", _TARGET, *extra_args])
    return rc, conn, projection


# ── 5. no verdict is not a restore ─────────────────────────────────────────


def test_the_verb_refuses_without_a_record_dsn(monkeypatch, capsys):
    from tortoise import __main__ as cli

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:6379/g",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", _TARGET])
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "no record DSN" in err, err
    assert "UNCHANGED" in err, err


def test_a_malformed_record_dsn_is_a_MISCONFIGURATION_not_an_outage(capsys):
    """The DSN arming surface is ``record_writer.record_dsn``, not a re-spelling."""
    from tortoise import __main__ as cli

    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:6379/g",
                   "--record-dsn", "mysql://user:pw@db.example/record",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", _TARGET])
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "must start with postgresql:// or postgres://" in err, err
    assert "misconfiguration, not an unarmed record" in err, err
    assert "mysql://" not in err, err          # the value is fingerprinted
    assert "pw@db.example" not in err, err


def test_an_unreadable_record_is_UNAVAILABLE_and_nothing_is_wiped(
        monkeypatch, capsys):
    from tortoise import __main__ as cli

    projection = _RecordingProjection()
    _install_fakes(monkeypatch, view=_view(2), projection=projection)

    def _boom(dsn, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setitem(sys.modules, "psycopg2",
                        types.SimpleNamespace(connect=_boom))
    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:6379/g",
                   "--record-dsn", "postgresql://fake/record",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", _TARGET])
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "record is unreadable" in err, err
    assert "UNCHANGED" in err, err
    assert projection.calls == [], "nothing may be restored over an unread record"


# ── 4. the target cannot be guessed ───────────────────────────────────────


def test_the_verb_refuses_to_guess_the_cache_graph(monkeypatch, capsys):
    """A wrong guess opens an EMPTY graph and reports a restore nobody reads."""
    from tortoise import __main__ as cli

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://fake/record")
    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:6379/g",
                   "--org", "o", "--graph", "g"])
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "--cache-graph is required" in err, err
    assert "EMPTY graph" in err, err
    assert "Nothing was restored" in err, err


# ── 6. the verb does not merely IMPORT the composition ────────────────────


def _called_names(path: Path, func: str) -> set[str]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return {c.func.id for c in ast.walk(node)
                    if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name)}
    raise AssertionError(f"{func} not found in {path}")


def test_the_verb_CALLS_the_restore_composition():
    """An import-only pin is satisfied by a body of ``return EXIT_RESTORED``."""
    called = _called_names(_CLI_PATH, "_cmd_record_restore")
    for required in ("restore_from_record_view", "read_record_view",
                     "record_dsn", "log_result"):
        assert required in called, (
            f"`_cmd_record_restore` never CALLS {required}: it imports the "
            f"piece and does not run it")


def test_the_composition_passes_the_destructive_token_at_the_entry_point():
    """A composition that forgot it would raise on EVERY restore.

    That failure is indistinguishable from a guard doing its job by exit code
    alone (both are non-zero), so it is pinned on the source: exactly one
    ``rebuild_from_record`` call, with ``confirm_destructive=True``.
    """
    tree = ast.parse(_MODULE_PATH.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "rebuild_from_record"]
    assert len(calls) == 1, "expected exactly one rebuild_from_record call"
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("confirm_destructive"), ast.Constant)
    assert kw["confirm_destructive"].value is True


# ── 7. the restore reads the RECORD, never the journal ────────────────────


def test_the_restore_cannot_reach_the_journal_replay():
    """R8's indicator: 0 restore steps that require reading the journal.

    Pinned on the AST, not on the prose: the module docstring names
    ``rebuild_all`` to say it is deliberately unreachable, and a naive
    substring pin would redden on the sentence that documents the rule.
    """
    tree = ast.parse(_MODULE_PATH.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert "rebuild_all" not in names | attrs, (
        "record_restore must not CALL the journal replay (`rebuild_all`) — R8's "
        "restore reads the RECORD only")
    assert not any(m == "tortoise.log" or m.startswith("tortoise.log.")
                   for m in imported), (
        f"record_restore imports a journal module: {sorted(imported)}")


# ── 1. an EMPTY source is refused, and the cache is provably untouched ────


def test_an_empty_record_is_REFUSED_and_the_cache_is_not_wiped(
        monkeypatch, shared_proj, cache_graph, capsys):
    """The guard the verb exists behind: an empty source WIPES and restores nothing.

    A REAL cache graph holding a REAL node, read back AFTER the refusal — an
    exit-code-only pin would also pass if the verb wiped and then refused.
    """
    from tortoise import __main__ as cli

    cache_graph.query("CREATE (:Point {id:'p-9556-survivor'})")
    projection = _projection_bound_to(shared_proj, cache_graph)
    _install_fakes(monkeypatch, view=cp.RecordView(), projection=projection)

    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:16390/g",
                   "--record-dsn", "postgresql://fake/record",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", cache_graph.name])
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_REFUSED, err
    assert "NO rows" in err, err
    assert "--allow-empty" in err, err
    remaining = cache_graph.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    assert remaining == 1, (
        "the empty-source refusal must happen BEFORE the wipe — the cache still "
        f"holds its node, got {remaining}")


def test_allow_empty_proceeds(monkeypatch, capsys):
    """The positive control: the refusal is a guard, not a wall."""
    projection = _RecordingProjection(result={
        "nodes_written": 0, "relations_written": 0, "statements": 0,
        "record_nodes": 0, "record_relations": 0,
        "coverage": "complete", "declared_keys": 0, "source": "record"})
    rc, conn, projection = _drive(
        monkeypatch, view=cp.RecordView(), projection=projection,
        extra_args=("--allow-empty",))
    out = capsys.readouterr().out

    assert rc == rrestore.EXIT_RESTORED, out
    assert len(projection.calls) == 1, "the wipe was authorized and must run"
    assert projection.calls[0]["confirm_destructive"] is True
    assert projection.closed and conn.closed, "the verb owns both handles"


# ── 2. a rebuild that wrote nothing is not a restore ──────────────────────


def test_a_rebuild_that_wrote_nothing_is_REFUSED(monkeypatch, capsys):
    projection = _RecordingProjection(result={
        "nodes_written": 0, "relations_written": 0, "statements": 0,
        "record_nodes": 2, "record_relations": 0,
        "coverage": "complete", "declared_keys": 0, "source": "record"})
    rc, _, _ = _drive(monkeypatch, view=_view(2), projection=projection)
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_REFUSED, err
    assert "wrote 0 node(s)" in err, err
    assert "not a restore" in err, err


def test_a_contradictory_report_is_UNAVAILABLE_rather_than_a_silent_pass(
        monkeypatch, capsys):
    """The report says it wrote more than the record holds — UNKNOWN, not green."""
    projection = _RecordingProjection(result={
        "nodes_written": 7, "relations_written": 0, "statements": 0,
        "record_nodes": 2, "record_relations": 0,
        "coverage": "complete", "declared_keys": 2, "source": "record"})
    rc, _, _ = _drive(monkeypatch, view=_view(2), projection=projection)
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "disagree" in err, err


# ── 3. the L2 guard is a refusal, not a traceback ─────────────────────────


def test_the_cache_guard_refusal_is_reported_as_a_REFUSAL(monkeypatch, capsys):
    """#2944's L2 refuses a bulk wipe on a non-``test_`` server graph.

    Every production graph name is ``org_<org_id>``, so this is the shape an
    operator WILL hit: the reason must be printed, the exit code distinct, and
    the refusal must be attributed BEFORE the rebuild (the fake's
    ``rebuild_from_record`` is never reached — asserted).
    """
    projection = _RecordingProjection(guard_refuses=True)
    rc, conn, projection = _drive(monkeypatch, view=_view(2),
                                  projection=projection)
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_REFUSED, err
    assert "Restore REFUSED" in err, err
    assert "non-test graph 'org_acme'" in err, err
    assert "#2944" in err, err
    # Pre-wipe attribution: the guard is asked BEFORE any rebuild, so nothing
    # was written and the "REFUSED" class is honest.
    assert projection.calls == [], "the wipe must not be attempted past a refusal"
    assert projection.closed and conn.closed, "both handles must close"


def test_a_failure_DURING_the_rebuild_is_UNKNOWN_not_a_clean_refusal(
        monkeypatch, capsys):
    """A post-preflight failure may have wiped: exit 2, never "nothing written".

    ``DirectCacheWriteRefused`` / ``CacheProjectionError`` are ``RuntimeError``
    subclasses raised from ``project_records`` — i.e. AFTER the wipe. A verb
    that mapped every ``RuntimeError`` to the refusal class would tell the
    operator the cache was untouched while it was destroyed.
    """
    from tortoise.projection import DirectCacheWriteRefused

    projection = _RecordingProjection(raises=DirectCacheWriteRefused(
        "Refusing a mutating statement on the raw-query lane"))
    rc, conn, projection = _drive(monkeypatch, view=_view(2),
                                  projection=projection)
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "UNKNOWN" in err, err
    assert "DURING the rebuild" in err, err
    assert "REFUSED" not in err, err
    assert len(projection.calls) == 1
    assert projection.closed and conn.closed


def test_an_unexpected_driver_error_is_UNAVAILABLE_not_a_traceback(
        monkeypatch, capsys):
    """A socket error during the wipe is not a ``RuntimeError``.

    ``DirectCacheWriteRefused``/``CacheProjectionError`` are ``RuntimeError``
    subclasses, so a ``except RuntimeError`` classifier catches them and misses
    every driver error (a reset socket, a driver's own exception type). The
    unclassified case must land on exit ``2`` with an UNKNOWN statement from
    ONE place (the composition) — otherwise it leaves as a traceback, and the
    one path with no documented code is the one where the cache may have been
    wiped and not rebuilt.
    """
    projection = _RecordingProjection(raises=ConnectionResetError(
        "connection reset by peer"))
    rc, _, _ = _drive(monkeypatch, view=_view(2), projection=projection)
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "UNKNOWN" in err, err
    assert "Traceback" not in err, err


# ── 8. a bounded restore is PARTIAL, and its unwritten keys are not-hot ───


def test_a_bounded_restore_declares_partial_and_names_the_not_hot_set(
        monkeypatch, capsys):
    projection = _RecordingProjection()
    rc, _, _ = _drive(monkeypatch, view=_view(5), projection=projection,
                      extra_args=("--limit", "2", "--json"))
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc == rrestore.EXIT_RESTORED, out
    assert projection.calls[0]["limit"] == 2
    assert payload["coverage"] == "partial"
    assert payload["unwritten_keys"] == 3, payload


def test_the_human_line_names_the_not_hot_consequence():
    """A partial restore must not read as "the whole cache is back"."""
    outcome = rrestore.RestoreOutcome(
        org_id="o", graph_id="g", cache_graph="org_o",
        record_nodes=5, record_relations=0, nodes_written=2,
        relations_written=0, target_nodes_before=0,
        coverage="partial", declared_keys=2, bounded=True)
    line = outcome.render()
    assert "not-hot" in line, line
    assert "never empty" in line, line
    assert outcome.unwritten_keys == 3, outcome.as_dict()


def test_a_COMPLETE_restore_is_not_reported_as_not_hot():
    """A ``complete`` declaration enumerates NO keys — and that is not a gap.

    ``declaration_for`` returns ``coverage=complete, hot_keys=()`` when it covers
    the whole record, so ``record_nodes - declared_keys`` reported EVERY
    complete restore as fully not-hot (measured: a 5-node full restore printed
    "5 record key(s) were NOT written").
    """
    outcome = rrestore.RestoreOutcome(
        org_id="o", graph_id="g", cache_graph="org_o",
        record_nodes=5, record_relations=0, nodes_written=5,
        relations_written=0, target_nodes_before=0,
        coverage="complete", declared_keys=0, bounded=False)
    assert outcome.unwritten_keys == 0, outcome.as_dict()
    assert "not-hot" not in outcome.render(), outcome.render()


def test_limit_zero_is_REFUSED_before_any_write(monkeypatch, capsys):
    """0 is not a harmless bound: it wipes and writes nothing.

    Measured before the guard: the wipe ran, the "wrote 0" refusal fired AFTER
    it, and the verb exited 3 — whose documented meaning is "nothing was
    written". A scripted ``--limit $N`` with ``N=0`` would destroy a cache and
    report the code that means untouched.
    """
    projection = _RecordingProjection()
    rc, _, projection = _drive(monkeypatch, view=_view(2),
                               projection=projection,
                               extra_args=("--limit", "0"))
    err = capsys.readouterr().err

    assert rc == rrestore.EXIT_UNAVAILABLE, err
    assert "--limit must be >= 1" in err, err
    assert projection.calls == [], "a 0 bound must not reach the wipe"


def test_the_pre_wipe_count_is_BOUNDED(monkeypatch):
    """The count is a full-graph read: unbounded, it can hang before any output."""
    seen: list[dict] = []

    class _Rows(list):
        @property
        def result_set(self):
            return [[0]]

    class _Graph:
        def query(self, cypher, **kw):
            seen.append({"cypher": cypher, **kw})
            return _Rows()

    projection = _RecordingProjection()
    projection.g = _Graph()
    rrestore.restore_from_record_view(
        _view(2), org_id="o", graph_id="g", cache_graph="test_x",
        project=projection)

    assert seen and "timeout" in seen[0], seen
    assert seen[0]["timeout"] == rrestore.CACHE_READ_TIMEOUT_MS, seen

# ── the live drive: a real cache, restored from a real view ───────────────


def _projection_bound_to(shared_proj, graph):
    """A REAL ``FalkorProjection`` whose graph handle is ``graph``.

    ``from_uri`` would open the graph by name on the server; instead the
    construction state is copied off the session's projection and only the
    graph handle/name are rebound, so ``_project_record_view`` (token → L2 →
    wipe → indexes → records → declaration) runs unmodified on a graph this
    test owns.
    """
    from tortoise.projection import FalkorProjection

    proj = object.__new__(FalkorProjection)
    proj.__dict__.update(shared_proj.__dict__)
    proj.g = graph
    proj._graph_name = graph.name
    return proj


@pytest.mark.parametrize("mode", ["module", "cli"])
def test_a_live_restore_puts_the_record_state_into_the_cache(
        monkeypatch, shared_proj, cache_graph, capsys, mode):
    """The end of the chain: record view → cache, by the shipped projection.

    Asserts on the CACHE, not on the exit code: a verb that returned a constant
    leaves the pre-existing node in place and the record's nodes absent.
    """
    from tortoise import __main__ as cli

    cache_graph.query("CREATE (:Point {id:'p-9556-stale'})")
    projection = _projection_bound_to(shared_proj, cache_graph)

    if mode == "module":
        outcome = rrestore.restore_from_record_view(
            _view(2), org_id="o", graph_id="g",
            cache_graph=cache_graph.name, project=projection)
        assert outcome.nodes_written == 2, outcome.render()
        assert outcome.target_nodes_before == 1, outcome.render()
    else:
        _install_fakes(monkeypatch, view=_view(2), projection=projection)
        rc = cli.main(["record-restore", "--db", "docker://:x@localhost:16390/g",
                       "--record-dsn", "postgresql://fake/record",
                       "--org", "o", "--graph", "g",
                       "--cache-graph", cache_graph.name])
        printed = capsys.readouterr()
        assert rc == rrestore.EXIT_RESTORED, printed.err
        assert "✓ restored o/g -> cache" in printed.out, printed.out

    ids = sorted(r[0] for r in cache_graph.query(
        "MATCH (p:Point) RETURN p.id").result_set)
    assert ids == ["p-9556-0", "p-9556-1"], (
        f"the cache must hold exactly the record's two points: got {ids}")


def test_a_LIVE_complete_restore_reports_zero_unwritten_keys(
        monkeypatch, shared_proj, cache_graph, capsys):
    """End-to-end against a REAL projection: 5 in, 5 out, 0 not-hot.

    This is the assertion the review found missing: every fake agreed with the
    code's arithmetic, so the complete-declaration bug (``declared_keys == 0`` →
    ``unwritten_keys == record_nodes``) was invisible to the suite.
    """
    from tortoise import __main__ as cli

    projection = _projection_bound_to(shared_proj, cache_graph)
    _install_fakes(monkeypatch, view=_view(5), projection=projection)
    rc = cli.main(["record-restore", "--db", "docker://:x@localhost:16390/g",
                   "--record-dsn", "postgresql://fake/record",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", cache_graph.name, "--json"])
    printed = capsys.readouterr()

    assert rc == rrestore.EXIT_RESTORED, printed.err
    payload = json.loads(printed.out)
    assert payload["coverage"] == "complete", payload
    assert payload["nodes_written"] == 5, payload
    assert payload["unwritten_keys"] == 0, payload
    assert "not-hot" not in printed.out, printed.out
