"""#2944 — the destructive-wipe guard must be STRUCTURAL, not name-based.

Before #2944 the safety of ``MATCH (n) DETACH DELETE n`` in ``rebuild_all``
rested on (a) a graph-NAME prefix check and (b) a mutable ``_skip_guard``
boolean. A graph renamed to look disposable passed (a) in server mode, and
embedded mode skipped the check entirely. This file pins the replacement:

  L1 (structural) — ``rebuild_all``/``rebuild`` refuse unless the CALLER
      passed ``confirm_destructive=True``. Default = refuse, for every
      caller, embedded included. Nothing can authorize a wipe by accident.
  L2 (defence in depth) — the disposable-graph name check STILL applies to
      every bulk DETACH DELETE that reaches the guarded handle, so a
      non-test server graph is refused even when L1 was satisfied.

Coverage map (per the issue's acceptance criteria):
  (a) a non-disposable graph is refused even if named ``test_...``-like, or
      when the old ``_skip_guard`` bypass would have allowed it
  (b) the legitimate disposable paths still work
  (c) ``_skip_guard`` (or its replacement) cannot be flipped from ordinary
      production code — pinned at three depths: the identifier is gone from
      both query paths (bytecode), the token is keyword-only with a refusing
      default, and the L1 assertion BODY itself refuses without it (the
      chokepoint test that reds if enforcement is neutered).

The unit tests use a recording fake graph handle so they run in BOTH the
embedded carve-out lane and the docker lane without a live server. The
end-to-end allow tests use a real embedded FalkorDB in ``tmp_path``.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.log import EventLog
from tortoise.projection import FalkorProjection, _GuardedGraph

REPO_ROOT = Path(__file__).resolve().parents[1]

# A path that must never be touched: L1 refusals fire before ANY graph or
# filesystem I/O, so tests that reach them deliberately use a bogus path to
# prove no work happened.
NO_IO_DIR = "/nonexistent/2944-destructive-wipe-guard"

# Graph names the issue names explicitly as "real-looking" — these must be
# refused even WITH the L1 token (L2 defence in depth). Bare "test" is here
# because the historical L2 check is `startswith(("test_", "tortoise_test"))`
# — "test" never passed it (see tests/test_wipe_server.py).
NON_DISPOSABLE_NAMES = [
    "prod_tortoise", "tortoise_prod", "team_x", "production", "test",
]

# Names that look disposable to L2 (the historical guard) — but a name alone
# must no longer authorize a wipe, so these are refused without the L1 token.
TEST_LOOKING_NAMES = ["test_prod_tortoise", "test_team_x", "tortoise_test",
                     "tortoise_test_delta"]


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _RecordingGraph:
    """Minimal fake FalkorDB graph handle: records queries, returns empty sets.

    Count queries return 0 so ``rebuild_all``'s trailing count reads work; the
    wipe is identified by its Cypher text, never executed.
    """

    def __init__(self):
        self.queries: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.queries.append(cypher)
        if "COUNT(" in cypher.upper():
            return _Result([[0]])
        return _Result([])

    def wipes(self) -> list[str]:
        return [q for q in self.queries if "DETACH DELETE" in q.upper()]


class _EmptyLog:
    """A log with no events — lets guard tests reach the wipe without I/O."""

    def read_all(self):
        return []


def _bare_projection(*, graph_name: str, embedded: bool) -> FalkorProjection:
    """A FalkorProjection with no DB — enough to exercise the guard paths.

    The refusal tests assert the guard fires BEFORE any graph I/O, so no
    connection is needed. The allow test with a fake graph only reads counts.
    """
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = embedded
    proj._graph_name = graph_name
    proj.graph_name = graph_name
    proj.g = _RecordingGraph()
    return proj


# ── (c) the old bypass is dead: no attribute can authorize a wipe ─────────


def _referenced_names(fn) -> tuple[set, set]:
    """Names/literals the compiled function body actually references.

    Compiled metadata, not source text — so a comment mentioning the removed
    bypass cannot make this pin pass vacuously."""
    return set(fn.__code__.co_names), set(fn.__code__.co_consts)


def test_projection_has_no_skip_guard_field():
    """The mutable ``_skip_guard`` bypass was REMOVED (#2944), not defaulted.

    Pins the two guarded query paths at the bytecode level: neither may
    reference ``_skip_guard`` again. A reintroduced read would silently
    re-open the bypass the issue is about."""
    for fn in (FalkorProjection.query, _GuardedGraph.query):
        names, consts = _referenced_names(fn)
        assert "_skip_guard" not in names, (
            f"{fn.__qualname__} reads _skip_guard — the bypass was replaced by "
            f"the per-call confirm_destructive token (#2944)"
        )
        assert "_skip_guard" not in consts, (
            f"{fn.__qualname__} looks up _skip_guard — the bypass was replaced "
            f"by the per-call confirm_destructive token (#2944)"
        )


def test_setting_skip_guard_attribute_does_not_authorize_wipe():
    """(a)/(c) ``proj._skip_guard = True`` must have NO effect.

    The pre-#2944 bypass is exactly this assignment; it must not authorize a
    wipe on a test-looking graph, and must not let a raw bulk DETACH DELETE
    through the guarded handle on a non-test server graph."""
    proj = _bare_projection(graph_name="test_prod_tortoise", embedded=False)
    proj._skip_guard = True  # the old bypass — must be inert now
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR)
    assert proj.g.wipes() == [], "no wipe may run while L1 refused"

    # Raw guarded-handle path: the attribute must not open L2 either.
    raw = _bare_projection(graph_name="prod_tortoise", embedded=False)
    raw._skip_guard = True
    guarded = _GuardedGraph(_RecordingGraph(), raw)
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.query("MATCH (n) DETACH DELETE n")


def test_replacement_is_a_call_site_parameter_not_ambient_state():
    """(c) The only authorization is a per-call argument whose default is False.

    There is no module flag, env var, or constructor switch to flip: the
    signature itself is the contract."""
    for meth in (FalkorProjection.rebuild_all, FalkorProjection.rebuild):
        params = inspect.signature(meth).parameters
        assert "confirm_destructive" in params, f"{meth.__name__} has no token"
        p = params["confirm_destructive"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{meth.__name__}: token must be keyword-only (explicit at the call site)"
        )
        assert p.default is False, (
            f"{meth.__name__}: default must be False (refuse) — a default-allow "
            f"would let a new caller wipe a graph by forgetting the token"
        )


def _rebuild_call_sites(path: Path) -> list:
    """Every ``*.rebuild(...)`` / ``*.rebuild_all(...)`` call AST node in a file.

    An AST walk, not a substring scan: the token must be present on the CALL,
    so a comment or docstring mentioning ``confirm_destructive=True`` cannot
    make this pin pass vacuously while the call itself omits it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name in ("rebuild", "rebuild_all"):
            sites.append(node)
    return sites


def test_production_entry_points_thread_the_token():
    """(c) Every production call site must pass the token — no silent default.

    A caller reaching ``rebuild_all``/``rebuild`` without opting in gets the
    refusal, so the shipped entry points must opt in explicitly at the call.
    Per-call-site AST check: dropping the argument (and thereby relying on the
    default) reds here even if the token text survives elsewhere in the
    file."""
    checks = {
        REPO_ROOT / "tortoise" / "__main__.py": "tortoise rebuild CLI",
        REPO_ROOT / "tortoise" / "migrate_db.py": "migrate_db",
        REPO_ROOT / "validation" / "validate_tortoise_ep.py": "validation script",
    }
    for path, label in checks.items():
        sites = _rebuild_call_sites(path)
        assert sites, f"{label}: no rebuild/rebuild_all call found in {path.name}"
        for call in sites:
            token = next(
                (kw for kw in call.keywords if kw.arg == "confirm_destructive"),
                None,
            )
            assert token is not None, (
                f"{label}: {path.name}:{call.lineno} reaches the wipe without "
                f"the explicit confirm_destructive token"
            )
            assert isinstance(token.value, ast.Constant) and token.value.value is True, (
                f"{label}: {path.name}:{call.lineno}: confirm_destructive must be "
                f"the literal True (a variable could be flipped at runtime)"
            )


# ── (a) non-disposable graphs are refused ─────────────────────────────────


@pytest.mark.parametrize("name", TEST_LOOKING_NAMES)
def test_test_looking_name_alone_is_not_enough(name):
    """(a) A name that passes the historical L2 check must STILL be refused
    when the caller did not opt in — names are no longer load-bearing."""
    proj = _bare_projection(graph_name=name, embedded=False)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR, confirm_destructive=False)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR)  # default = refuse
    assert proj.g.queries == [], "guard must fire before any graph I/O"


@pytest.mark.parametrize("name", NON_DISPOSABLE_NAMES)
def test_non_disposable_name_refused_even_with_token(name, tmp_path):
    """(a) L2 still refuses a real-looking server graph even when L1 is
    satisfied — the guard was not weakened into a rubber stamp."""
    proj = _bare_projection(graph_name=name, embedded=False)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    assert proj.g.wipes() == []


def test_non_disposable_name_refused_on_rebuild_too():
    """(a) ``rebuild(log)`` shares the L1/L2 chokepoints — same refusal."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild(object(), confirm_destructive=False)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.rebuild(_EmptyLog(), confirm_destructive=True)
    assert proj.g.wipes() == []


def test_embedded_without_token_is_refused():
    """(a) The old bypass would have allowed this: embedded mode was exempt
    from the guard entirely. \"Embedded\" means isolated, not unsupervised."""
    proj = _bare_projection(graph_name="tortoise", embedded=True)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR)
    assert proj.g.wipes() == [], "embedded wipe must not run without the opt-in"


def test_raw_bulk_wipe_still_refused_on_non_test_server_graph():
    """L2 is unchanged for hand-written Cypher that never went through L1."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    proj.g = _GuardedGraph(_RecordingGraph(), proj)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.query("MATCH (n) DETACH DELETE n")
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.g.query("MATCH (n) DETACH DELETE n")


def test_targeted_delete_is_still_allowed():
    """No false positives: a targeted delete (property map) is not a bulk wipe."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(_RecordingGraph(), proj)
    guarded.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": "x"})
    assert len(guarded._g.queries) == 1


# ── (b) the legitimate disposable paths still work ────────────────────────


@pytest.mark.parametrize("name", TEST_LOOKING_NAMES)
def test_server_test_graph_with_token_is_allowed(name, tmp_path):
    """(b) A disposable (test-named) server graph wipes with the token."""
    proj = _bare_projection(graph_name=name, embedded=False)
    counts = proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    assert proj.g.wipes() == ["MATCH (n) DETACH DELETE n"]
    assert counts["nodes"] == 0 and counts["events"] == 0


def test_embedded_graph_with_token_is_allowed(tmp_path, monkeypatch):
    """(b) The real embedded path (CLI / migrate target) still wipes + replays.

    This is the end-to-end legitimate case: a real FalkorDB in a temp dir, a
    real JSONL log, the token passed explicitly."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)  # force embedded
    db_path = str(tmp_path / "guard_allow.db")
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    log = EventLog(log_dir / "events.jsonl")
    for i in range(2):
        log.append({
            "type": "PointAdded",
            "point": {"id": f"guard-{i}", "content": f"c{i}", "context": "t"},
        })

    proj = FalkorProjection(db_path, graph_name="test_guard_allow",
                            skip_health_check=True)
    try:
        counts = proj.rebuild_all(str(log_dir), confirm_destructive=True)
    finally:
        proj.close()
    assert counts["nodes"] == 2, "a legitimate wipe+replay must still work"
    assert counts["events"] == 2


def test_embedded_rebuild_single_log_with_token(tmp_path, monkeypatch):
    """(b) ``rebuild(log)`` legitimate path still works with the token."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)  # force embedded
    db_path = str(tmp_path / "guard_rebuild_one.db")
    log = EventLog(tmp_path / "one.jsonl")
    log.append({"type": "PointAdded",
                "point": {"id": "one", "content": "x", "context": "t"}})
    proj = FalkorProjection(db_path, graph_name="test_guard_one",
                            skip_health_check=True)
    try:
        proj.rebuild(log, confirm_destructive=True)
        rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
    finally:
        proj.close()
    assert int(rows[0][0]) == 1


def test_rebuild_parses_before_wipe(tmp_path):
    """A log that cannot be read must raise BEFORE the wipe (no data loss).

    `rebuild()` mirrors `rebuild_all()`'s WIPE-AFTER-PARSE ordering: a corrupt
    MID-FILE line raises out of `read_all()` while the graph is still intact.
    Parsing after the wipe would leave a wiped, empty graph behind."""
    proj = _bare_projection(graph_name="tortoise", embedded=True)
    log = EventLog(tmp_path / "corrupt.jsonl")
    log.append({"type": "PointAdded",
                "point": {"id": "first", "content": "y", "context": "t"}})
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")          # mid-file corruption
    log.append({"type": "PointAdded",
                "point": {"id": "second", "content": "z", "context": "t"}})
    with pytest.raises(ValueError, match="mid-file corruption"):
        proj.rebuild(log, confirm_destructive=True)
    assert proj.g.wipes() == [], "no wipe may run when the log cannot be read"


def test_embedded_raw_wipe_on_test_graph_still_allowed():
    """(b) L2's embedded exemption is unchanged — no regression for the raw
    ``MATCH (n) DETACH DELETE n`` reset sites in the suite."""
    proj = _bare_projection(graph_name="tortoise", embedded=True)
    guarded = _GuardedGraph(_RecordingGraph(), proj)
    guarded.query("MATCH (n) DETACH DELETE n")
    assert len(guarded._g.queries) == 1


def test_server_test_named_graph_raw_wipe_without_token_is_allowed():
    """(b) The other half of L2's exemption, pinned: on the RAW-QUERY lane a
    test-named SERVER graph wipes with no token (L1 is not on that lane). This
    is the counterpart to the non-test server refusal above."""
    proj = _bare_projection(graph_name="test_raw_server", embedded=False)
    guarded = _GuardedGraph(_RecordingGraph(), proj)
    guarded.query("MATCH (n) DETACH DELETE n")
    assert len(guarded._g.queries) == 1


# ── (c) DISCRIMINATING pins: the chokepoint body, not just caller shape ───


def test_l1_chokepoint_itself_refuses_without_the_token():
    """(c) The assertion BODY, not just the callers' shape.

    Neutering ``_assert_destructive_confirmed`` is the exact failure mode the
    identifier/signature pins above cannot observe, so this exercises the
    chokepoint directly: it must raise for ``False`` and return for ``True``."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj._assert_destructive_confirmed(False, "rebuild_all")
    proj._assert_destructive_confirmed(True, "rebuild_all")  # must not raise


def test_wipe_all_nodes_consults_the_token_before_issuing_the_wipe(monkeypatch):
    """(c) ``_wipe_all_nodes`` itself consults the token before wiping.

    The named method is called DIRECTLY: ``rebuild_all`` has its own L1
    fast-fail, so driving this through ``rebuild_all`` would only prove that
    *one* of the two calls exists (deleting the one in ``_wipe_all_nodes``
    left the full file green — cycle-4 finding). With the token check
    replaced by a sentinel raise, the direct call must raise and must never
    reach the handle."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)

    def _sentinel(confirm_destructive, operation):
        raise RuntimeError("confirm_destructive: sentinel")

    monkeypatch.setattr(proj, "_assert_destructive_confirmed", _sentinel)
    with pytest.raises(RuntimeError, match="sentinel"):
        proj._wipe_all_nodes(confirm_destructive=True, operation="probe")
    assert proj.g.queries == [], "graph I/O ran before the token check"
    assert proj.g.wipes() == [], "the wipe ran before the token check"


def test_rebuild_all_fast_fails_before_any_work(monkeypatch):
    """(c) The rebuild lane's own L1 fast-fail runs before snapshot/parse/wipe.

    ``NO_IO_DIR`` does not exist, so a caller that got past L1 would raise a
    filesystem error instead of the sentinel — the sentinel proves ordering."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)

    def _sentinel(confirm_destructive, operation):
        raise RuntimeError("confirm_destructive: sentinel")

    monkeypatch.setattr(proj, "_assert_destructive_confirmed", _sentinel)
    with pytest.raises(RuntimeError, match="sentinel"):
        proj.rebuild_all(NO_IO_DIR, confirm_destructive=True)
    assert proj.g.queries == []
