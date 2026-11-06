"""`tools/cache_evict.py` — the egress executor's operator verb (#10976).

⛔ WHAT EVERY TEST HERE IS GUARDING. This is the first surface in the repo that can
issue a `DETACH DELETE` against the cache on purpose. The census (`#11004`) is
read-only by construction; `tortoise/cache_evictor.py` is bounded and record-gated but
has no caller. This CLI is that caller, so the discriminating assertions are the
negative ones: **a refused, mistyped or unlicensed invocation issues NO mutating
statement** — not "exits non-zero", which a traceback would also satisfy.

The mutating-statement spy is the point: it records every statement the graph handle
was asked to run, so "nothing was deleted" is measured rather than inferred from the
node count of a graph that may not have been reached at all.
"""
from __future__ import annotations

import json

import pytest

from tools import cache_evict
from tortoise.cache_evictor import Candidate

MUTATING = ("DETACH DELETE", "DELETE", "CREATE", "MERGE", "SET ", "REMOVE", "DROP")

#: The production seam loader, captured before any fixture stubs it.
_REAL_LOAD_CALLABLE = cache_evict._load_callable


class _Result:
    def __init__(self, rows=()):
        self.result_set = list(rows)


class _Graph:
    """A graph handle that records every statement it is asked to run."""

    #: The fake cache's resident population. Every count answers it, so `assess` sees a
    #: coherent graph (labels, per-label counts and the node total agree) — without that
    #: the census can only ever answer UNBOUNDED/REFUSED and the PLAN arms are
    #: unreachable from this harness.
    NODES = 10

    #: The graph this handle SAYS it is — `evict`'s gate 5 refuses a handle that cannot
    #: name its graph, or names one other than the census's. `BASE`'s `--graph` is
    #: `tortoise`, and the fake instance's only graph is `tortoise`, so a real handle here
    #: would answer the same name (`falkordb.Graph.name`).
    name = "tortoise"

    def __init__(self, *, fail_delete: bool = False, delete_removes: int | None = None,
                 fail_recount: bool = False):
        self.statements: list[str] = []
        #: Raise INSIDE the `DETACH DELETE` — the statement was ISSUED and its effect is
        #: unknown, which is the one case the exit code must not call "nothing happened".
        self.fail_delete = fail_delete
        #: ⛔ How many rows the delete REPORTS removing. Without this the fake answered
        #: `[]` for every delete, so `report.deleted` was EMPTY in all of these tests and the
        #: `report.deleted` operand of the CLI's exit-code gate could never be true — a
        #: half-untested guard on the most safety-critical line in the tool.
        self.delete_removes = delete_removes
        #: Raise on the POST-DELETE re-count: the DELETE landed, then the after-state
        #: could not be measured. The report must still carry the deletions.
        self.fail_recount = fail_recount
        self._deleted = False

    def _answer(self, statement, params=None):
        low = statement.lower()
        if "detach delete" in low:
            if self.fail_delete:
                raise RuntimeError("RUNTIME reset on the delete")
            rows = list((params or {}).get("rows") or [])
            self._deleted = True
            removed = len(rows) if self.delete_removes is None else self.delete_removes
            return _Result([[removed]])
        if "call db.labels" in low:
            return _Result([("Point",)])
        if "size(labels(n)) = 0" in low:
            return _Result([(0,)])
        if "count(" in low:
            if self.fail_recount and self._deleted:
                raise RuntimeError("recount transport blip")
            return _Result([(self.NODES,)])
        return _Result([])

    def ro_query(self, statement, params=None, timeout=None):
        self.statements.append(statement)
        return self._answer(statement, params)

    def query(self, statement, params=None, timeout=None):
        self.statements.append(statement)
        return self._answer(statement, params)

    @property
    def mutations(self) -> list[str]:
        return [s for s in self.statements if any(m in s.upper() for m in MUTATING)]


class _Client:
    def __init__(self, used_memory=1000):
        self.used_memory = used_memory
        self.statements: list[str] = []

    def info(self, section=None):
        return {"used_memory": self.used_memory, "maxmemory": 0}

    def execute_command(self, *args, **kwargs):
        self.statements.append(" ".join(str(a) for a in args))
        if args and str(args[0]).upper() == "GRAPH.LIST":
            return [b"tortoise"]
        if args and str(args[0]).upper() == "GRAPH.MEMORY":
            return ["total_graph_sz_mb", 1.0, "indices_sz_mb", 0.5]
        return None

    def exists(self, name):
        # The census probes the graph's own Redis key by NAME (never `KEYS *`).
        return 1 if str(name) == "tortoise" else 0

    def ttl(self, name):
        # `-1` = no expiry, measured on every key including `telemetry{}`: the
        # eviction unit is the whole graph, which is why the census exists.
        return -1

    @property
    def mutations(self) -> list[str]:
        return [s for s in self.statements if any(m in s.upper() for m in MUTATING)]

    def close(self):
        pass


@pytest.fixture()
def wired(monkeypatch):
    """Point the CLI at a fake instance, so every flag arm is reachable offline."""
    client, graph = _Client(), _Graph()
    endpoint = type("E", (), {"graph_name": "tortoise"})()
    monkeypatch.setattr(cache_evict, "_load_callable", _never_called_load)
    monkeypatch.setitem(cache_evict.__dict__, "_fake", True)

    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_endpoint", lambda uri: endpoint)
    monkeypatch.setattr(census, "_connection", lambda ep: (client, graph))
    monkeypatch.setattr(census, "_absent_graph_refusal", lambda c, name: None)
    return client, graph


def _never_called_load(spec, *, flag):
    """A stand-in seam the non-destructive arms never reach."""

    if flag == "--selector":
        def _select(graph, limit, population):
            return [Candidate(node_id=i, key=f"k{i}", population=population)
                    for i in range(int(limit))]

        return _select
    return lambda candidate: True


BASE = ["--graph-uri", "docker://:pw@h:6379/tortoise", "--graph", "tortoise"]


def _argv(*extra: str) -> list[str]:
    return [*BASE, *extra]


# ── the destructive token ──────────────────────────────────────────────────────


def test_execute_without_the_token_refuses_before_any_connection(monkeypatch, capsys):
    """The token is the OPERATOR's authorization; a missing one is never a default."""
    called = {"connect": False}

    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_connection",
                        lambda ep: called.__setitem__("connect", True))

    code = cache_evict.main(_argv("--population", "turn", "--max-nodes", "10",
                                  "--selector", "m:f", "--record-probe", "m:g",
                                  "--execute"))
    assert code == 1
    assert called["connect"] is False, "a missing token reached the store"
    assert "--confirm-graph" in capsys.readouterr().err


def test_a_token_naming_another_graph_is_not_a_confirmation(wired, tmp_path):
    """`--confirm-graph other` proves the operator authorized a DIFFERENT cache.

    ⛔ EVERY UPSTREAM ARM MUST BE SATISFIED, or this passes for the wrong reason. Its
    earlier form carried no `--parity-report`, so `main` returned at the parity guard
    BEFORE the token arm was reached — the assertion was met by a different refusal, and
    `if False:` in place of the token check left the whole CLI suite green (measured: 94
    passed) while a fully valid invocation issued the `DETACH DELETE` for a cache the
    operator's token explicitly did not name. That is the gate the file itself calls
    "the token proves the OPERATOR authorized THIS graph".
    """
    client, graph = wired
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "10", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "tortoise",
        "--execute", "--confirm-graph", "not-tortoise"))
    assert code == 1
    assert client.statements == [], "the store was touched after a refused token"
    assert graph.mutations == [], f"a refused token mutated: {graph.mutations}"


def test_a_graph_that_disagrees_with_the_URI_refuses_before_any_statement(
        wired, tmp_path, monkeypatch):
    """`--graph` names one cache and the URI another: refuse, do not sweep either.

    Unpinned before this test (`if False:` left 16 passing), and the clearer refusal is
    what it buys — `_connection` would hand back a handle for the URI's graph, which the
    executor's gate 5 then refuses. Two layers is fine; one of them being untested is not.
    """
    _client, graph = wired
    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_endpoint",
                        lambda uri: type("E", (), {"graph_name": "other"})())
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "10", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise"))
    assert code == 1
    assert graph.mutations == [], f"a URI/graph disagreement mutated: {graph.mutations}"


# ── the licence ───────────────────────────────────────────────────────────────


def test_without_a_parity_report_the_tool_refuses_rather_than_inventing_one(wired):
    client, graph = wired
    code = cache_evict.main(_argv("--population", "turn", "--max-nodes", "10",
                                  "--selector", "m:f", "--record-probe", "m:g"))
    assert code == 1
    assert client.statements == []
    assert graph.statements == []


def test_an_unbounded_cache_is_refused_and_deletes_nothing(wired, tmp_path):
    """⛔ `maxmemory` 0 + no declared ceiling = NOTHING bounds this cache.

    That is the exact state #10976 was filed about ("it holds every node forever"),
    and it must be its OWN verdict (exit 4) rather than sharing the "measured, fine"
    exit: a scheduler keying on 0 would otherwise conclude a bound exists.

    The licensed-but-wrong-population arm is the executor's own gate, covered by
    `tests/test_cache_eviction_evictor_pins.py` — this CLI never re-decides it.
    """
    client, graph = wired
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    code = cache_evict.main(_argv(
        "--population", "Point", "--max-nodes", "10",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "tortoise"))
    assert code == 4, "an unbounded cache must not read as a licence"
    assert graph.mutations == [], f"a refused pass mutated: {graph.mutations}"
    assert client.mutations == []


def test_two_reports_for_one_instrument_are_a_usage_error(wired, tmp_path):
    """LAST-WINS IS A SAFETY BUG: a REFUSED file followed by a stale CLEAN one won."""
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "10",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-report", str(report),
        "--parity-graph", "tortoise"))
    assert code == 1


def test_a_parity_report_for_another_graph_is_refused(wired, tmp_path):
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "10",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "other"))
    assert code == 1


def test_a_report_with_no_instrument_is_refused_rather_than_attributed(
        wired, tmp_path):
    """A nameless report could be attributed to any population by whoever hands it over."""
    path = tmp_path / "nameless.json"
    path.write_text(json.dumps({"graph": "tortoise", "verdict": "CLEAN"}), "utf-8")
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "10",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(path), "--parity-graph", "tortoise"))
    assert code == 3, "unreadable evidence is a refusal about the STORE, not a typo"


# ── the seams ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("flag", ["--selector", "--record-probe"])
def test_a_seam_that_is_not_a_callable_is_a_usage_error(
        wired, tmp_path, capsys, monkeypatch, flag):
    """A defaulted seam would arm the delete path with the two decisions we refuse.

    ⛔ This must reach the SEAM loader. An earlier version of this test omitted
    `--parity-report`, so `main` returned at the parity guard long before
    `_load_callable` ever ran and the gate had zero coverage — the same "asserts
    nothing" defect the review flagged. So: a valid report, the REAL loader restored,
    and the message asserted.
    """
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    # Put the REAL seam loader back: the fixture stubs it so the other arms do not need
    # importable callables, and leaving the stub in place is exactly what made this test
    # assert nothing.
    monkeypatch.setattr(cache_evict, "_load_callable", _REAL_LOAD_CALLABLE)
    # Real importables for the OTHER seam, so the target seam is the one that fails.
    seam = {"--selector": "os.path:join", "--record-probe": "os.path:join"}
    seam[flag] = "no_colon_here"
    with pytest.raises(SystemExit) as exit_info:
        cache_evict.main(_argv(
            "--population", "turn", "--max-nodes", "10", "--ceiling-bytes", "1",
            "--parity-report", str(report), "--parity-graph", "tortoise",
            *[x for pair in seam.items() for x in pair]))
    assert exit_info.value.code == 1
    assert "module:callable" in capsys.readouterr().err


def test_an_unlicensed_population_is_refused_and_deletes_nothing(wired, tmp_path):
    """⛔ The licence is `plan["evictable"]`, and it is the ONLY thing it covers.

    The census answers PLAN (a proven `turn` population, resident over a declared
    ceiling), and the caller asks for `Point` — a label the plan does NOT license. The
    executor must refuse (exit 3) and issue no mutating statement. A caller who types
    the wrong name learns the right one from `licensed_populations` in the report.
    """
    client, graph = wired
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    code = cache_evict.main(_argv(
        "--population", "Point", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "tortoise"))
    assert code == 3, "an unlicensed population is a refusal, never a licence"
    assert graph.mutations == [], f"a refused pass mutated: {graph.mutations}"
    assert client.mutations == []


def test_a_licensed_population_dry_runs_and_the_metric_is_a_MEASUREMENT(wired, tmp_path):
    """The driver's own record of a destructive run must be data, not a repr.

    `report.metric` (a bound method) serialised to `"<bound method …>"` under
    `default=str`, so `nodes_evicted`/`bytes_reclaimed` vanished from the only
    machine-readable record of a sweep. This asserts the SHAPE, which is what a monitor
    keys on.
    """
    import contextlib
    import io

    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cache_evict.main(_argv(
            "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
            "--selector", "m:f", "--record-probe", "m:g",
            "--parity-report", str(report), "--parity-graph", "tortoise"))
    body = json.loads(buf.getvalue())
    assert isinstance(body["metric"], dict), body["metric"]
    assert isinstance(body["metric"]["nodes_evicted"], int)
    assert body["executed"] is False, "a dry run is the default and must say so"
    assert body["metric"]["dry_run"] is True


def _verdict(body: dict):
    """The report's verdict, whichever arm emitted it.

    Three arms emit their own shape — a bare `{"verdict": ...}` for UNBOUNDED / an
    unreadable report / an unmeasurable cache, and the full body (`eviction.metric`) for
    a pass that ran. Reading both keeps these tests about the EXIT CODE rather than about
    which arm answered.
    """
    if "verdict" in body:
        return body["verdict"]
    return body.get("eviction", {}).get("metric", {}).get("verdict")


def _reason(body: dict) -> str:
    if "reason" in body:
        return body["reason"]
    return body.get("eviction", {}).get("metric", {}).get("reason") or ""


def _parity_report(tmp_path, *, instrument: str):
    path = tmp_path / "parity.json"
    path.write_text(json.dumps({
        "instrument": instrument,
        "graph": "tortoise",
        "verdict": "CLEAN",
        "population": {"episodic_graph_nodes": 4},
        "dry_run": {"would_delete_turn_nodes": 4, "held_back_turn_nodes": 0},
    }), "utf-8")
    return path


# ── the exit-code vocabulary on the DESTRUCTIVE arm ───────────────────────────
#
# ⛔ The producer's flag (`delete_unverified`) is pinned in the executor's pins file; what
# these cover is the CLI's CONSUMPTION of it. Reverting `if report.deleted or
# report.delete_unverified:` to `if report.deleted:` merged green before them — the exact
# cycle-3 P1, reproduced in the file whose own comment describes it.


def test_an_UNVERIFIED_delete_reports_2_not_3(wired, tmp_path, capsys):
    """The DELETE was ISSUED; the server may have applied it. That is not "nothing went"."""
    client, _unused_graph = wired
    graph = _Graph(fail_delete=True)
    import tools.cache_eviction_census as census

    census._connection = lambda ep: (client, graph)  # type: ignore[assignment]
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")

    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "tortoise",
        "--execute", "--confirm-graph", "tortoise"))

    body = json.loads(capsys.readouterr().out)
    assert body["metric"]["delete_unverified"] is True, body["metric"]
    assert body["metric"]["nodes_evicted"] == 0
    assert code == 2, ("an issued-but-unverified DELETE must not read as 'nothing "
                       "happened' (exit 3)")


def test_a_REFUSED_run_that_deleted_nothing_still_reports_3(wired, tmp_path):
    """The control that keeps 3 meaning what the table says it means."""
    _client, graph = wired
    code = cache_evict.main(_argv(
        "--population", "Point", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise"))
    assert code == 3
    assert graph.mutations == []


def test_a_SUCCESSFUL_delete_whose_after_state_is_UNMEASURED_reports_2(wired, tmp_path,
                                                                     capsys):
    """⛔ The OTHER operand of the exit-code gate: `report.deleted`.

    The DELETE lands, then the post-delete re-count raises, so the pass cannot measure its
    after-state and returns `deleted=<n>` with `verdict="REFUSED"`. Two things must hold,
    and neither was covered before this test:

    * the report still CARRIES the deletions (a measurement fault after a commit must not
      lose the record of what was committed);
    * the CLI reports **2**, not 3. Reverting `if report.deleted or
      report.delete_unverified:` to `if report.delete_unverified:` left the whole CLI
      suite green, i.e. a one-token regression would report "nothing was removed" for a
      run that removed four nodes — the exact R2 violation the operand exists to prevent.
    """
    client, _unused = wired
    graph = _Graph(delete_removes=4, fail_recount=True)
    import tools.cache_eviction_census as census

    census._connection = lambda ep: (client, graph)  # type: ignore[assignment]
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")

    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "tortoise",
        "--execute", "--confirm-graph", "tortoise"))

    body = json.loads(capsys.readouterr().out)
    assert body["metric"]["nodes_evicted"] == 4, body["metric"]
    assert body["metric"]["delete_unverified"] is False, (
        "the delete SUCCEEDED; only the after-state is unmeasured")
    assert code == 2, ("a run that removed nodes must never report 3 ('nothing was "
                       "removed')")


def test_a_cache_already_inside_its_budget_reports_0(wired, tmp_path, capsys):
    """⛔ The exit-code table's ROW 0 — the last arm with no assertion.

    Measured before this test: deleting the `if report.verdict == "CLEAN": return
    CACHE_EVICTION_EXIT_CLEAN` arm left all 14 CLI tests green, so a refactor could
    report a nothing-to-do run as 2 ("may have evicted") — a false alarm on the one
    reading a scheduler acts on without looking, and the only arm of the split nothing
    protected.
    """
    _client, _unused = wired
    report = _parity_report(tmp_path, instrument="tools/turn_parity_census.py")

    code = cache_evict.main(_argv(
        # The fake cache holds exactly 10 nodes and the budget IS 10, so the pass finds
        # the cache inside its bound and evicts nothing.
        "--population", "turn", "--max-nodes", "10", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(report), "--parity-graph", "tortoise",
        "--execute", "--confirm-graph", "tortoise"))

    body = json.loads(capsys.readouterr().out)
    assert body["metric"]["verdict"] == "CLEAN", body["metric"]
    assert body["metric"]["nodes_evicted"] == 0
    assert code == 0, ("a cache already inside its budget must report 0, not 2 — a "
                       "scheduler acts on this without reading the body")


def test_a_report_that_is_not_utf8_is_a_REFUSAL_not_a_traceback(wired, tmp_path):
    """⛔ Corrupt EVIDENCE is a refusal about the store (3), not a mistyped flag (1).

    A `Path.read_text()` guard caught only `OSError`, so the non-UTF8 case raised
    `UnicodeDecodeError` (a `ValueError`) straight out of `main` — a traceback and exit 1,
    the USAGE code, telling the operator to retry FLAGS. The census pins the same case for
    the same reason, and this file's own comment promises it.
    """
    _client, graph = wired
    path = tmp_path / "not-utf8.json"
    path.write_bytes(b'{"instrument": "tools/turn_parity_census.py", "graph": "\xff"}')

    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "10", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(path), "--parity-graph", "tortoise"))

    assert code == 3, "unreadable evidence is a refusal about the STORE, not a typo"
    assert graph.mutations == []


# ── the refusal MAPPINGS, and the report that must survive a bad --out ────────
#
# ⛔ Each of these was pinned by NOTHING: satisfying the handler with an exception
# that never fires (e.g. `except ZeroDivisionError`) left all 17 CLI tests green, and
# removing `_emit`'s OSError fallback left all 95 across the suite green. A regression in
# any of them turns a REFUSAL (3) into a traceback with exit 1 — this file's USAGE code —
# or destroys the only record of a run that already committed deletes.


def test_an_unwritable_out_does_not_destroy_a_completed_runs_record(wired, tmp_path,
                                                                    capsys):
    """The report IS the record of a destructive run; a bad `--out` must not eat it.

    ⛔ The unwritable path here is not a typo — it is the ENOSPC / read-only-mount /
    stale-directory case, which surfaces AFTER `evict()` has committed deletes. Without
    the fallback the run exits 1 ("retry the FLAGS") with no record, and a scheduler
    keyed on 1 may re-run the sweep and delete a second licensed batch.
    """
    client, _unused = wired
    graph = _Graph(delete_removes=4, fail_recount=True)
    import tools.cache_eviction_census as census

    census._connection = lambda ep: (client, graph)  # type: ignore[assignment]
    unwritable = tmp_path / "a-directory"
    unwritable.mkdir()                      # a DIRECTORY cannot be written to as a file

    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise", "--out", str(unwritable),
        "--execute", "--confirm-graph", "tortoise"))

    body = json.loads(capsys.readouterr().out)   # fell back to STDOUT
    assert body["metric"]["nodes_evicted"] == 4
    assert code == 2, "the destructive run's exit code must survive a bad --out"


def test_an_unwritable_out_on_a_REFUSAL_still_reports_3(wired, tmp_path, capsys):
    """The other arm: a refusal must not become a traceback/exit 1 either."""
    _client, graph = wired
    unwritable = tmp_path / "a-directory"
    unwritable.mkdir()

    code = cache_evict.main(_argv(
        "--population", "Point", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise", "--out", str(unwritable)))

    body = json.loads(capsys.readouterr().out)
    assert _verdict(body) == "REFUSED", body
    assert code == 3
    assert graph.mutations == []


def test_an_ABSENT_graph_is_refused_and_never_created(wired, tmp_path, monkeypatch):
    """Reading a non-existent graph CREATES it — so this refusal is the one that stops a
    refused invocation from being a mutation (R2). The census pins the helper; its USE
    here was pinned by nothing (`if absent is not None:` disabled left 17/17 green)."""
    client, graph = wired
    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_absent_graph_refusal",
                        lambda c, name: f"graph {name!r} does not exist")
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise"))
    assert code == 3
    assert graph.statements == [], f"an absent graph was opened: {graph.statements}"
    assert client.statements == []


def test_a_store_that_cannot_be_REACHED_reports_3_not_1(wired, tmp_path, monkeypatch,
                                                        capsys):
    """An unreachable store is a refusal about the STORE, never a flag mistake."""
    import tools.cache_eviction_census as census

    def boom(ep):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(census, "_connection", boom)
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise"))
    body = json.loads(capsys.readouterr().out)
    assert body["verdict"] == "REFUSED"
    assert code == 3, "exit 1 would tell the operator to retry FLAGS against a dead store"


def test_a_cache_that_cannot_be_MEASURED_reports_3_not_1(wired, tmp_path, monkeypatch,
                                                         capsys):
    _client, graph = wired
    import tools.cache_eviction_census as census

    def boom(*a, **k):
        raise RuntimeError("INFO memory unreadable")

    monkeypatch.setattr(census, "_absent_graph_refusal", lambda c, n: None)
    monkeypatch.setattr(cache_evict, "assess", boom)
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "5", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise"))
    body = json.loads(capsys.readouterr().out)
    assert body["verdict"] == "REFUSED"
    assert code == 3
    assert graph.mutations == []


def test_an_invalid_BOUND_refuses_rather_than_sweeping(wired, tmp_path, capsys):
    """`--max-nodes 0` is the state the executor's own floor exists to refuse."""
    _client, graph = wired
    code = cache_evict.main(_argv(
        "--population", "turn", "--max-nodes", "0", "--ceiling-bytes", "1",
        "--selector", "m:f", "--record-probe", "m:g",
        "--parity-report", str(_parity_report(tmp_path,
                                              instrument="tools/turn_parity_census.py")),
        "--parity-graph", "tortoise", "--execute", "--confirm-graph", "tortoise"))
    body = json.loads(capsys.readouterr().out)
    assert _verdict(body) == "REFUSED", body
    assert "config refused" in _reason(body), _reason(body)
    assert code == 3
    assert graph.mutations == []
