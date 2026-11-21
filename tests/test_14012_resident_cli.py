"""#14012 — the operator verb (`tools/resident.py`) and the sweep's pin wiring.

⛔ WHAT THESE TESTS GUARD, stated as the negative: **a pin set the budget will not
honour must stop the sweep BEFORE `evict` is called.** Every other refusal in the
evictor happens per node; this one happens before a single statement, and a regression
that turned it into a warning would delete a node the customer declared resident while
the report said everything was fine.

The second negative: `pin` must write NOTHING when it refuses. A partially applied
declaration is the worst of the three outcomes — the user is told the control worked,
the budget says otherwise, and nothing in the state distinguishes "asked for ten, got
six" from "asked for six".
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from tools import cache_evict, resident
from tortoise.cache_eviction import Assessment, CacheState
from tortoise.cache_evictor import EvictionReport
from tortoise.resident_pin import PINS_META_KEY

MUTATING = ("DETACH DELETE", "DELETE", "CREATE", "MERGE", "SET ", "REMOVE", "DROP")


class _Result:
    def __init__(self, rows=()):
        self.result_set = [list(r) for r in rows]


class _Graph:
    """A graph handle that models the `:Meta` declaration and records statements."""

    name = "tortoise"

    def __init__(self, nodes: dict[str, list[str]] | None = None) -> None:
        #: key -> neighbour keys. The closure is derived from these, never stored.
        self.nodes = nodes or {}
        self.meta: dict[str, dict] = {}
        self.statements: list[str] = []

    def _answer(self, statement: str, params=None):
        params = params or {}
        if "MATCH (n:Meta {key:$key}) RETURN n.label, n.key_prop, n.keys" in statement:
            entry = self.meta.get(params["key"])
            if entry is None:
                return _Result([])
            return _Result([[entry["label"], entry["key_prop"], entry["keys"]]])
        if "MERGE (n:Meta {key:$key}) SET n += $props" in statement:
            self.meta.setdefault(params["key"], {}).update(params["props"])
            return _Result([])
        if "MATCH (n:Meta {key:$key}) DETACH DELETE n" in statement:
            self.meta.pop(params["key"], None)
            return _Result([])
        if "UNWIND $keys AS k" in statement:
            rows = []
            for key in params["keys"]:
                if key not in self.nodes:
                    continue
                nbrs = self.nodes[key]
                rows.append([key, list(range(len(nbrs))), list(nbrs)])
            return _Result(rows)
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
    def info(self, section=None):
        return {"used_memory": 1000, "maxmemory": 0}

    def close(self):
        pass


BASE = ["--graph-uri", "docker://:pw@h:6379/tortoise", "--graph", "tortoise"]


@pytest.fixture()
def wired(monkeypatch):
    graph = _Graph({"p0": ["p1"], "p1": ["p0"], "p2": []})
    endpoint = types.SimpleNamespace(graph_name="tortoise")

    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_endpoint", lambda uri: endpoint)
    monkeypatch.setattr(census, "_connection", lambda ep: (_Client(), graph))
    monkeypatch.setattr(census, "_absent_graph_refusal", lambda c, name: None)
    return graph


# ── the pin verb ────────────────────────────────────────────────────────────────
def test_pin_writes_the_declaration_and_reports_its_closure(wired, capsys):
    code = resident.main(["pin", *BASE, "--key", "p0", "--window-nodes", "1000"])
    assert code == resident.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "OK"
    assert out["pin"]["added"] == ["p0"]
    assert wired.meta[PINS_META_KEY]["keys"] == ["p0"]
    # "together with the things connected to them"
    assert out["pin"]["set"]["closure"] == ["p0", "p1"]
    assert out["pin"]["set"]["closure_size"] == 2


def test_pin_over_the_budget_refuses_with_the_number_and_writes_nothing(
        wired, capsys):
    code = resident.main(["pin", *BASE, "--key", "p0", "--max-pins", "1"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "REFUSED"
    assert out["code"] == "budget"
    assert "would need 2 fast-layer slot(s)" in out["reason"]
    assert PINS_META_KEY not in wired.meta
    assert wired.mutations == [], "a refused pin issued a mutating statement"


def test_show_reports_an_over_budget_set_as_a_refusal(wired, capsys):
    resident.main(["pin", *BASE, "--key", "p0", "--window-nodes", "1000"])
    capsys.readouterr()
    code = resident.main(["show", *BASE, "--max-pins", "1"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert "would then take over the fast layer" in out["reason"]
    assert out["pin"]["over_budget"]


def test_show_with_no_declaration_is_ok_and_names_nothing(wired, capsys):
    assert resident.main(["show", *BASE, "--window-nodes", "1000"]) == resident.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "OK"
    assert out["pin"]["keys"] == []


def test_a_graph_that_disagrees_with_the_uri_is_a_usage_error(
        wired, capsys, monkeypatch):
    import tools.cache_eviction_census as census

    # The URI names `other`; the flags name `tortoise`. Acting on one graph while
    # naming another is the failure this check exists to prevent.
    monkeypatch.setattr(census, "_endpoint",
                        lambda uri: types.SimpleNamespace(graph_name="other"))
    with pytest.raises(SystemExit) as exc:
        resident.main(["show", "--graph-uri", "docker://:pw@h:6379/other",
                       "--graph", "tortoise", "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE


def test_an_unreachable_fast_layer_is_a_refusal_not_a_traceback(wired, capsys,
                                                               monkeypatch):
    import tools.cache_eviction_census as census

    def boom(ep):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(census, "_connection", boom)
    code = resident.main(["show", *BASE, "--window-nodes", "1000"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert "could not be reached" in out["reason"]


def test_a_graph_that_is_not_on_the_instance_is_never_created(wired, capsys,
                                                              monkeypatch):
    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_absent_graph_refusal",
                        lambda c, name: f"graph {name!r} is not on this instance")
    code = resident.main(["show", *BASE, "--window-nodes", "1000"])
    assert code == resident.EXIT_REFUSED
    assert "not on this instance" in json.loads(capsys.readouterr().out)["reason"]


# ── the outside verb ───────────────────────────────────────────────────────────
class _Cursor:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self._last: list = []

    def execute(self, sql, params=None):
        if "bypassing" in sql:
            self._last = [{"tenant": "org_a", "bypassing": False}]
            return
        if "set_config" in sql or "current_setting" in sql:
            self._last = []
            return
        if not self.ok:
            raise RuntimeError("record unreachable")
        if "min(created_at)" in sql:
            self._last = [{"outside": 1, "oldest": "2026-11-02T00:00:00Z",
                           "newest": "2026-11-02T00:00:00Z"}]
        elif "GROUP BY 1" in sql:
            self._last = [{"topic": "statement", "n": 1}]
        else:
            self._last = [{"n": 2}]

    def fetchall(self):
        return list(self._last)

    def fetchone(self):
        return self._last[0] if self._last else None


class _Conn:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.closed = False

    def cursor(self):
        return _Cursor(self.ok)

    def close(self):
        self.closed = True


def _fake_psycopg(monkeypatch, conn: _Conn) -> None:
    module = types.SimpleNamespace(connect=lambda dsn: conn)
    monkeypatch.setitem(sys.modules, "psycopg", module)


def test_outside_reports_what_the_record_holds_and_the_graph_does_not(wired, capsys,
                                                                     monkeypatch):
    conn = _Conn()
    _fake_psycopg(monkeypatch, conn)
    code = resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                          "org_a", "--graph-id", "g_1", "--window-nodes", "1000"])
    assert code == resident.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    view = out["outside"]
    assert view["outside"] == 1
    assert view["record_total"] == 2
    assert view["topics"] == [{"topic": "statement", "count": 1}]
    assert view["complete"] is True
    assert "still in your record" in view["headline"]
    assert conn.closed, "the outside verb left its record connection open"


def test_an_unmeasurable_outside_view_exits_4_never_0(wired, capsys, monkeypatch):
    _fake_psycopg(monkeypatch, _Conn(ok=False))
    code = resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                          "org_a", "--graph-id", "g_1", "--window-nodes", "1000"])
    assert code == resident.EXIT_UNMEASURED, (
        "an unmeasured view exited 0 — a scheduler would record 'nothing is outside' "
        "for a run that measured nothing"
    )
    view = json.loads(capsys.readouterr().out)["outside"]
    assert view["outside"] is None
    assert view["complete"] is False


def test_outside_without_a_record_dsn_is_a_usage_error(wired, capsys, monkeypatch):
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--org-id", "org_a", "--graph-id",
                       "g_1", "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE


# ── the sweep honours the pin, with no off-switch ──────────────────────────────
def _assessment() -> Assessment:
    return Assessment(
        state=CacheState(
            graph_name="tortoise", resident_bytes=1000, maxmemory_bytes=0,
            maxmemory_policy="noeviction", ceiling_bytes=1,
            ceiling_source="declared (test)", graph_key_ttl_seconds=-1,
            keys_per_graph=1, graph_memory_usage_mb=None,
            graph_memory_usage_note=None,
        ),
        classes=(),
        verdict="PLAN",
        plan={"evictable": [{"population": "turn", "nodes": 3,
                             "instrument": "tools/turn_parity_census.py"}]},
    )


@pytest.fixture()
def sweep(monkeypatch, wired):
    """The evict CLI, wired to the fake graph, with `evict` captured rather than run."""
    import tools.cache_eviction_census as census

    monkeypatch.setattr(census, "_endpoint",
                        lambda uri: types.SimpleNamespace(graph_name="tortoise"))
    monkeypatch.setattr(cache_evict, "assess", lambda *a, **k: _assessment())
    monkeypatch.setattr(cache_evict, "_load_callable", lambda spec, *, flag: (
        (lambda g, limit, pop: []) if flag == "--selector" else (lambda c: True)))
    calls: list[dict] = []

    def fake_evict(assessment, client, graph, **kwargs):
        calls.append(kwargs)
        return EvictionReport(verdict="CLEAN", reason="test")

    monkeypatch.setattr(cache_evict, "evict", fake_evict)
    return wired, calls


SWEEP = ["--parity-graph", "tortoise", "--population", "turn", "--selector", "m:f",
         "--record-probe", "m:g"]


def _sweep_argv(report, *extra: str) -> list[str]:
    return [*BASE, "--parity-report", str(report), *SWEEP, *extra]


def _report_file(tmp_path):
    import json as _json

    path = tmp_path / "r.json"
    path.write_text(_json.dumps({"graph": "tortoise", "instrument": "tools/turn_parity_census.py",
                                 "rows": []}), encoding="utf-8")
    return path


def test_a_sweep_with_no_pin_set_passes_no_gate_and_proceeds(sweep, capsys, tmp_path):
    graph, calls = sweep
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == 0
    assert len(calls) == 1
    assert calls[0]["pinned"] is None


def test_a_pinned_key_is_held_back_by_the_sweep(sweep, capsys, tmp_path):
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == 0
    gate = calls[0]["pinned"]
    assert gate is not None
    from tortoise.cache_evictor import Candidate

    assert gate(Candidate(node_id=0, key="p0", population="turn")) is True
    assert gate(Candidate(node_id=1, key="p1", population="turn")) is True  # closure
    assert gate(Candidate(node_id=2, key="p9", population="turn")) is False


def test_a_pin_set_over_its_budget_stops_the_sweep_before_evict(sweep, capsys,
                                                               tmp_path):
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    # `--max-nodes` is the WINDOW, so the derived pin cap is 100; declare a smaller one.
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == 0, "sanity: 1000-node window, 2-slot closure — inside the cap"

    import os

    os.environ["TORTOISE_PIN_MAX_NODES"] = "1"
    try:
        capsys.readouterr()
        code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes",
                                            "1000"))
        assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    finally:
        del os.environ["TORTOISE_PIN_MAX_NODES"]
    assert len(calls) == 1, (
        "the sweep reached `evict` with a pin set it may not honour — an over-budget "
        "pin set must stop the run, not warn beside a deletion"
    )
    out = json.loads(capsys.readouterr().out)
    assert "pinned closure is 2 node(s)" in out["reason"]


def test_a_pin_set_for_another_key_space_stops_the_sweep(sweep, capsys, tmp_path):
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Source", "key_prop": "url",
                                 "keys": ["https://example.test"]}
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert len(calls) == 0
    out = json.loads(capsys.readouterr().out)
    assert "unmatched pin set" in out["reason"]
