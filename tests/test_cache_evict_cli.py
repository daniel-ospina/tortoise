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

MUTATING = ("DETACH DELETE", "DELETE", "CREATE", "MERGE", "SET ", "REMOVE", "DROP")


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

    def __init__(self):
        self.statements: list[str] = []

    def _answer(self, statement):
        low = statement.lower()
        if "call db.labels" in low:
            return _Result([("Point",)])
        if "size(labels(n)) = 0" in low:
            return _Result([(0,)])
        if "count(" in low:
            return _Result([(self.NODES,)])
        return _Result([])

    def ro_query(self, statement, params=None, timeout=None):
        self.statements.append(statement)
        return self._answer(statement)

    def query(self, statement, params=None, timeout=None):
        self.statements.append(statement)
        return self._answer(statement)

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
    """A selector/probe seam that must never be needed by the arms under test."""

    def _fn(*args, **kwargs):
        return True

    return _fn


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


def test_a_token_naming_another_graph_is_not_a_confirmation(wired):
    """`--confirm-graph other` proves the operator authorized a DIFFERENT cache."""
    client, graph = wired
    code = cache_evict.main(_argv("--population", "turn", "--max-nodes", "10",
                                  "--selector", "m:f", "--record-probe", "m:g",
                                  "--execute", "--confirm-graph", "not-tortoise"))
    assert code == 1
    assert client.statements == [], "the store was touched after a refused token"
    assert graph.statements == []


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
def test_a_seam_that_is_not_a_callable_is_a_usage_error(wired, flag):
    """A defaulted seam would arm the delete path with the two decisions we refuse."""
    extra = {"--selector": "m:f", "--record-probe": "m:g"}
    extra[flag] = "no_colon_here"
    code = cache_evict.main(_argv("--population", "turn", "--max-nodes", "10",
                                  *[x for pair in extra.items() for x in pair]))
    assert code == 1


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
    import io
    import contextlib

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
