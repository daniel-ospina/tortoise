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
        if "MATCH (n:Point) RETURN count(n)" in statement:
            return _Result([[len(self.nodes)]])
        # ⛔ THE KEY READ, which this fake used to answer with `[]` while reporting a
        # nonzero count — the self-inconsistent shape a wrong `key_prop` produces for
        # real, and the one `outside_view.read` now refuses. A fake that says "3 resident"
        # and then "no keys" is not a graph the product can be, so it must not be the
        # shape the verb's test drives.
        if "AS key LIMIT" in statement:
            return _Result([[k] for k in self.nodes])
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


def test_a_declared_budget_without_a_resolvable_window_is_refused(wired, capsys,
                                                               monkeypatch):
    """A budget declared with no window cannot be capped, so it is never accepted.

    `__post_init__` enforces `max_nodes < window` only when a window is known, so this
    pair was ACCEPTED: `pin` wrote the declaration and reported `written: true`, and
    every subsequent sweep re-derived its own `window // 10`, found the closure over
    budget and refused. That is the unbounded-cache state #10976 exists to prevent,
    reached from a command that reported success — and it is the asymmetric failure the
    whole control is built to avoid (a pin may buy speed; it may not take the cache).
    """
    monkeypatch.delenv("TORTOISE_CACHE_MAX_NODES", raising=False)
    code = resident.main(["pin", *BASE, "--key", "p0", "--max-pins", "10000000"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "REFUSED"
    assert out["code"] == "config"
    assert "window" in out["reason"]
    assert PINS_META_KEY not in wired.meta
    assert wired.mutations == [], "a refused pin issued a mutating statement"


def test_pin_over_the_budget_refuses_with_the_number_and_writes_nothing(
        wired, capsys):
    # ⛔ The window is supplied because a DECLARED budget without one is its own
    # refusal (see `test_a_declared_budget_without_a_resolvable_window_is_refused`):
    # this arm is about the CLOSURE exceeding a budget the window can be checked
    # against, so it must name both.
    code = resident.main(["pin", *BASE, "--key", "p0", "--window-nodes", "1000",
                          "--max-pins", "1"])
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
    code = resident.main(["show", *BASE, "--window-nodes", "1000", "--max-pins", "1"])
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


def test_an_unmeasured_cross_check_exits_4_never_0(wired, capsys, monkeypatch):
    """`record_total` failing alone must not become `verdict: OK` / exit 0.

    The span read succeeded, so `outside` is a real number — but the cross-check that
    makes a resident/outside disagreement visible is missing, so the view is not a
    census. A scheduler must not record this run as "nothing is outside".
    """

    class _NoTotal(_Conn):
        def cursor(self):
            cur = super().cursor()

            def _boom(sql, params=None):
                # ⛔ `GROUP BY` excluded: the TOPIC statement also carries a
                # `count(*) AS n`, so the narrower match is the record-total
                # statement alone.
                if "count(*) AS n" in sql and "GROUP BY" not in sql:
                    raise RuntimeError("permission denied for relation point")
                return _Cursor.execute(cur, sql, params)

            cur.execute = _boom  # type: ignore[method-assign]
            return cur

    _fake_psycopg(monkeypatch, _NoTotal())
    code = resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                          "org_a", "--graph-id", "g_1", "--window-nodes", "1000"])
    assert code == resident.EXIT_UNMEASURED
    view = json.loads(capsys.readouterr().out)["outside"]
    assert view["outside"] == 1, "sanity: the span read succeeded"
    assert view["record_total"] is None
    assert view["complete"] is False


def test_outside_with_an_unknown_label_is_a_usage_error_not_a_traceback(
        wired, capsys, monkeypatch):
    """A `--label` the record has no table for is a FLAG mistake, so it is exit 1."""
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--label", "Nonesuch", "--dsn",
                       "postgresql://x", "--org-id", "org_a", "--graph-id", "g_1",
                       "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE, (
        "an unknown label escaped as something other than the declared usage code"
    )


def test_outside_with_an_unvalidated_topic_column_is_a_usage_error(
        wired, capsys, monkeypatch):
    """The one identifier that reaches SQL must be refused, not interpolated."""
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                       "org_a", "--graph-id", "g_1", "--topic-column",
                       "node_key; DROP TABLE point", "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE


def test_outside_with_a_non_bare_key_prop_is_a_usage_error_not_unmeasured(
        wired, capsys, monkeypatch):
    """A bad `--key-prop` is a FLAG mistake, so it is exit 1 — never exit 4 (UNMEASURED).

    `key_prop` is the third identifier `read()` interpolates, and it used to be validated
    only inside `resident_keys` — which sits behind the guard's `except Exception`. The
    result was `complete=False` with the `ValueError` folded into the reason, i.e. exit 4,
    whose documented meaning is "the measurement failed, retry": a scheduler was told to
    retry a flag that can never become valid. The other two identifiers already raise
    before the guard; this arm keeps the third from being the exception.
    """
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                       "org_a", "--graph-id", "g_1", "--key-prop",
                       "bad prop", "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE


def test_outside_refuses_a_zero_key_bound_instead_of_widening_it(
        wired, capsys, monkeypatch):
    """`--max-keys 0` must reach the module's floor refusal, never be defaulted away.

    ⛔ `args.max_keys or DEFAULT` silently rewrote `0` to 200,000 — the WIDENING
    direction on the one flag that bounds a read — which also made the module's own floor
    ("a bound of zero cannot read any key") unreachable from the command line. A caller
    asking for a bound of zero is making a mistake; the answer is the refusal, not the
    maximum.
    """
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                       "org_a", "--graph-id", "g_1", "--max-keys", "0",
                       "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE


@pytest.mark.parametrize("value", ["0", "-1"])
def test_outside_refuses_a_top_below_its_floor_instead_of_leaving_the_record_unmeasured(
        wired, capsys, monkeypatch, value):
    """`--top -1` used to reach the record as `LIMIT -1` and come back exit 4 UNMEASURED.

    ⛔ Exit 4 is "the measurement failed, retry" — the code an operator RETRIES for a flag
    mistake no retry can fix, and the module's own table says a flag mistake is usage.
    The `--max-keys` half of this floor has its own row; this is the `--top` half, which
    the floor in `outside_view.read` covers but no row reached through the CLI.
    """
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                       "org_a", "--graph-id", "g_1", "--top", value,
                       "--window-nodes", "1000"])
    assert exc.value.code == resident.EXIT_USAGE, (
        "a flag mistake surfaced as UNMEASURED (exit 4) — the code an operator retries"
    )


def test_pin_with_a_non_bare_label_is_a_refusal_not_a_traceback(wired, capsys):
    """A first pin on an EMPTY declaration reaches `bare_identifier` inside `pin`.

    `read()` validates the DECLARATION's pair and returns early when nothing is
    declared, so the caller's `--label` is only checked in `_closure_for` — where it
    used to raise `PinConfigError` straight out of `main()` as a traceback instead of
    the REFUSED envelope this tool's exit-code vocabulary promises.
    """
    assert PINS_META_KEY not in wired.meta, "sanity: nothing is declared yet"
    code = resident.main(["pin", *BASE, "--key", "p0", "--label", "Bad Label",
                          "--window-nodes", "1000"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "REFUSED"
    assert out["code"] == "config"
    # ⛔ The refusal moved EARLIER, and it is more accurate: `pin` now resolves the
    # label's contract key property first, so an unknown label is refused for THAT (there
    # is no `KEY_PROPERTY` entry to check it against) rather than for its spelling. Both
    # are `REFUSED`/`config`; what must hold is that the label is named and nothing was
    # written.
    assert "Bad Label" in out["reason"] or "bare Cypher identifier" in out["reason"], (
        out["reason"]
    )
    assert PINS_META_KEY not in wired.meta, "a refused pin wrote a declaration"
    assert wired.mutations == []


def test_pin_with_a_non_bare_key_prop_is_a_refusal_not_a_traceback(wired, capsys):
    code = resident.main(["pin", *BASE, "--key", "p0", "--key-prop", "i d",
                          "--window-nodes", "1000"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "config"
    assert wired.mutations == []


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
    _graph, calls = sweep
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
                                                               tmp_path, monkeypatch):
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    # `--max-nodes` is the WINDOW, so the derived pin cap is 100; declare a smaller one.
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == 0, "sanity: 1000-node window, 2-slot closure — inside the cap"

    # ⛔ `monkeypatch`, not `os.environ[...]` + `del`: the `del` form DESTROYED an ambient
    # value instead of restoring the prior one, so a wrapper or a developer shell that
    # exported this variable had it unset for every later test in the same process — which
    # silently switches `PinBudget.resolve()`'s source from `env:…` to `derived:…`.
    monkeypatch.setenv("TORTOISE_PIN_MAX_NODES", "1")
    capsys.readouterr()
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes",
                                        "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert len(calls) == 1, (
        "the sweep reached `evict` with a pin set it may not honour — an over-budget "
        "pin set must stop the run, not warn beside a deletion"
    )
    out = json.loads(capsys.readouterr().out)
    assert "pinned closure is 2 node(s)" in out["reason"]


# ── review round 5: the arms a green suite could not see ────────────────────────
def test_a_truncated_declaration_stops_the_sweep_and_names_the_truncation(
        sweep, capsys, tmp_path, monkeypatch):
    """The sweep's `or pins.truncated` arm is a STATED enforcement arm, so it is pinned.

    ⛔ Deleting `or pins.truncated` left the suite green, and
    `docs/architecture/SEARCH-ARCHITECTURE.md` claims this as one of the two places the cap
    is enforced. The state that makes the arm load-bearing is a truncated declaration AND
    an unparseable budget at once: with the arm the refusal names the TRUNCATION (a fact
    about the declaration, settled before any budget is resolved); without it the same run
    reports a CONFIG error and sends an operator to fix a value that is not the cause.
    """
    from tortoise import resident_pin

    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id",
                                 "keys": ["p0", "p1", "p2"]}
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == 0, "sanity: three readable keys, inside both bounds"

    monkeypatch.setattr(resident_pin, "DEFAULT_KEY_LIMIT", 1)
    monkeypatch.setenv("TORTOISE_PIN_MAX_NODES", "not-a-number")
    calls.clear()
    capsys.readouterr()
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert calls == [], "a truncated pin read is not a licence to delete"
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert "could not be read in full" in reason, reason
    assert "cannot be resolved" not in reason, (
        "a truncated declaration was reported as a BUDGET configuration error — the arm "
        "that settles declaration facts before resolving a budget was removed"
    )


def test_show_with_no_resolvable_budget_says_the_cap_was_not_checked(
        wired, capsys, monkeypatch):
    """`show` may hold no budget; it may not therefore imply it checked the cap.

    ⛔ The skipped half was announced on STDERR only, while the envelope said
    `verdict: OK, reason: ""` — so a scheduler keying on the verdict recorded "within
    budget" for a run that measured nothing.
    """
    monkeypatch.delenv("TORTOISE_CACHE_MAX_NODES", raising=False)
    monkeypatch.delenv("TORTOISE_PIN_MAX_NODES", raising=False)
    wired.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    # ⛔ THE FIELD ALONE DOES NOT CLOSE THIS. `cap_checked: false` fixes the consumer
    # that reads the JSON; the consumer that reads the EXIT STATUS still saw 0, which is
    # the surface a scheduler actually keys on. Two lanes fixed this finding
    # independently — one added the field, one changed the verdict — and BOTH are kept,
    # because they close different doors: the cap is the question `show` exists to
    # answer, so "cannot check it" is the tool's UNMEASURED answer (exit 4), and the
    # field says which half was skipped. `unpin` stays budget-free either way: it is the
    # documented remedy and must work in exactly this state.
    assert resident.main(["show", *BASE]) == resident.EXIT_UNMEASURED
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "UNMEASURED"
    assert out["cap_checked"] is False
    assert "--window-nodes" in out["reason"]


def test_show_with_a_budget_says_the_cap_was_checked(wired, capsys):
    """The positive half, so `cap_checked` cannot be a constant `false`."""
    wired.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    assert resident.main(["show", *BASE, "--window-nodes", "1000"]) == resident.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["cap_checked"] is True
    assert out["reason"] == ""


def test_a_sweep_with_no_declaration_reports_pins_applied_as_NOT_APPLICABLE(
        sweep, capsys, tmp_path):
    """`pins.applied` is `None` when there is nothing to apply — never `False`.

    ⛔ MUTATION-VERIFIED GAP: only the `False` (wrong-key-space) arm was asserted, so
    reverting the field to `pinned_gate is not None` left the suite GREEN. The consequence
    is an alarm with no cause: `False` is otherwise emitted only beside a sentence
    explaining the refusal, so a monitor keying on this field — the consumer it was added
    for — alarms on every clean sweep of an unpinned graph.
    """
    _, calls = sweep
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == 0
    assert len(calls) == 1 and calls[0]["pinned"] is None
    out = json.loads(capsys.readouterr().out)
    assert out["pins"]["applied"] is None, (
        "a sweep with NOTHING declared reported `applied` as a boolean — `False` here is "
        "an alarm with no cause, and it is indistinguishable from the wrong-key-space "
        "refusal, which always carries a reason"
    )
    assert out["pins"]["not_applied_reason"] == "no pin declaration — nothing to apply"


def test_a_pin_set_for_another_key_space_refuses_the_sweep(sweep, capsys, tmp_path):
    """A pin declaration in ANOTHER key space REFUSES the sweep, rather than not applying.

    ⛔ The mismatch is a comparison on the PROPERTY NAME, so it does not prove the two
    key spaces hold different populations: a sweep that keys the SAME nodes by another
    property (declaration on `id`, sweep with `--key-prop point_id`) would evict a pinned
    closure while the report read `held_pinned: 0` — the control silently protecting
    nothing, on the run that deletes. Reporting it beside the run left a pinned node
    deletable, which is the failure this control exists to prevent.

    The over-block the old behaviour avoided is real (a per-graph set is declared once,
    so a blanket refusal would stop every sweep of every other population) — which is
    why the way past is ONE explicit flag, not a silent default:
    `--allow-unapplied-pin-set`.
    """
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Source", "key_prop": "url",
                                 "keys": ["https://example.test"]}
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert calls == [], "a refused run reached `evict` and could have deleted"
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "REFUSED"
    assert "--allow-unapplied-pin-set" in out["reason"]
    assert out["pins"]["applied"] is False


def test_the_unapplied_pin_set_refusal_is_overridable_in_one_auditable_flag(
        sweep, capsys, tmp_path):
    """The other-population sweep stays reachable — deliberately, and visibly.

    The refusal above is an over-block when the two key spaces really do hold different
    populations, and that case is legitimate. It is answered by an explicit flag on the
    command, so the decision is recorded in the run rather than made silently by a
    default, and the stderr line says the protection was not applied.
    """
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Source", "key_prop": "url",
                                 "keys": ["https://example.test"]}
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000",
                                       "--allow-unapplied-pin-set"))
    assert code == 0
    assert len(calls) == 1 and calls[0]["pinned"] is None
    out = json.loads(capsys.readouterr().out)
    assert out["pins"]["applied"] is False
    assert "NOT applied" in out["pins"]["not_applied_reason"]


# ── review round 1: a pin problem must not block sweeps that do not use it ─────
def test_a_pin_budget_misconfiguration_does_not_refuse_a_sweep_with_NOTHING_pinned(
        sweep, capsys, tmp_path, monkeypatch):
    """#10976's state (an over-budget cache) must not be reached through a pin typo.

    Resolving the pin budget before reading the declaration refused EVERY sweep — with
    nothing pinned — on an unrelated env typo or a window too small for a pin to exist.
    """
    _, calls = sweep
    monkeypatch.setenv("TORTOISE_PIN_MAX_NODES", "not-a-number")
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path),
                                        "--max-nodes", "1000"))
    assert code == 0, "an unused pin budget refused a sweep with nothing pinned"
    assert len(calls) == 1 and calls[0]["pinned"] is None


def test_the_same_misconfiguration_IS_refused_once_something_is_pinned(
        sweep, capsys, tmp_path, monkeypatch):
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    monkeypatch.setenv("TORTOISE_PIN_MAX_NODES", "not-a-number")
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path),
                                        "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert len(calls) == 0
    out = json.loads(capsys.readouterr().out)
    assert "a pin declaration exists and its budget cannot be resolved" in out["reason"]


def test_an_unreadable_declaration_is_reported_as_unreadable_not_as_a_config_error(
        sweep, capsys, tmp_path):
    graph, calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": 7}
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert len(calls) == 0
    assert "could not be read" in json.loads(capsys.readouterr().out)["reason"]


# ── review round 1: usage vs unmeasured, and the record-view doors ─────────────
def test_a_grouping_column_that_is_not_a_record_column_is_a_usage_error(
        wired, capsys, monkeypatch):
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                       "org_a", "--graph-id", "g_1", "--window-nodes", "1000",
                       "--topic-column", "(SELECT count(*) FROM public.point)"])
    assert exc.value.code == resident.EXIT_USAGE, (
        "an injected grouping expression was refused as UNMEASURED (exit 4) rather than "
        "as a usage mistake — and exit 4 is what an operator retries"
    )


def test_a_record_scope_refusal_exits_4_with_the_reason(wired, capsys, monkeypatch):
    class _Mismatched(_Cursor):
        def execute(self, sql, params=None):
            if "bypassing" in sql:
                self._last = [{"tenant": "someone_else", "bypassing": False}]
                return
            return super().execute(sql, params)

    class _Conn2(_Conn):
        def cursor(self):
            return _Mismatched(self.ok)

    _fake_psycopg(monkeypatch, _Conn2())
    code = resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                          "org_a", "--graph-id", "g_1", "--window-nodes", "1000"])
    assert code == resident.EXIT_UNMEASURED
    view = json.loads(capsys.readouterr().out)["outside"]
    assert "tenant GUC" in view["reason"], (
        "the record's own refusal was swallowed — the operator gets 'unmeasured' with no "
        "cause"
    )


def test_an_unwritable_out_keeps_the_verdict_and_the_exit_code(
        wired, capsys, tmp_path):
    """An IO fault must not become "retry the FLAGS".

    ⛔ `_emit()` was unguarded, so `--out` into an impossible path raised straight out of
    `main()`: a REFUSED (3) — or, on `pin`/`unpin`, a declaration that HAD been written —
    became a traceback with exit 1, this tool's USAGE code. The exit code the verb
    computed survives, the report goes to stdout, and the fault is named on stderr.
    """
    # `afile` is a FILE, so `afile/deep/` cannot exist: an OSError on mkdir.
    (tmp_path / "afile").write_text("x")
    code = resident.main(["pin", *BASE, "--key", "p0", "--window-nodes", "1000",
                          "--out", str(tmp_path / "afile" / "deep" / "r.json")])
    captured = capsys.readouterr()
    assert code == resident.EXIT_OK, "the verdict was replaced by the IO fault"
    payload = json.loads(captured.out)
    assert payload["verdict"] == "OK"
    assert "could not write" in captured.err


def test_pin_resolves_the_labels_CONTRACT_key_not_id(wired, capsys):
    """The `--key-prop` help promises the contract key AND a refusal; `pin` did neither.

    ⛔ MEASURED on a `:Source` population the record keys by `url`: `pin --label Source`
    defaulted to `id`, found nothing under it, and refused with code `unknown-node` —
    "1 key(s) are not in the fast layer at all" — a remedy (check indexing, re-ingest)
    that cannot clear the state, because the KEY was there and the PROPERTY was wrong.
    The silent half is worse: on a graph that happened to carry `id`, the declaration was
    written and reported `written: true` into a key space nobody reads, so `pin` and
    `outside` disagreed about the same flag.
    """
    wired.nodes = {"https://a.example/x": []}
    code = resident.main(["pin", *BASE, "--key", "https://a.example/x",
                          "--label", "Source", "--window-nodes", "1000"])
    assert code == resident.EXIT_OK, (
        "pin refused a key that IS in the fast layer, because it looked under `id` "
        "instead of the label's contract key `url`"
    )
    assert wired.meta[PINS_META_KEY]["key_prop"] == "url"

    # And an EXPLICIT non-contract property is refused, matching the help text.
    capsys.readouterr()
    code = resident.main(["pin", *BASE, "--key", "https://a.example/x",
                          "--label", "Source", "--key-prop", "id",
                          "--window-nodes", "1000"])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "config"
    assert "url" in out["reason"]


def test_unpin_needs_no_budget_even_when_the_cap_cannot_be_checked(
        wired, capsys, monkeypatch):
    """`unpin` is the documented remedy for an over-budget set, so it must not need one.

    ⛔ `show` is split out below, deliberately: it used to be asserted here as `OK`, which
    locked in a healthy verdict over a cap that was never checked. The remedy (`unpin`)
    must still work in EXACTLY that configuration — that is the whole reason the two
    verbs are not the same, and it is what a blanket "require a budget" change would
    have broken.
    """
    monkeypatch.delenv("TORTOISE_CACHE_MAX_NODES", raising=False)
    monkeypatch.delenv("TORTOISE_PIN_MAX_NODES", raising=False)
    wired.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    assert resident.main(["unpin", *BASE, "--key", "p0"]) == resident.EXIT_OK
    assert PINS_META_KEY not in wired.meta


# ── review round 6: the identifier twin of the falsy-default bug, and the low door ──
def test_an_empty_label_is_refused_not_silently_defaulted_to_Point(wired, capsys):
    """`--label "$LABEL"` with `LABEL` unset used to MEASURE `Point` and report OK.

    ⛔ `args.label or "Point"` is the same falsy-default substitution the numeric flags are
    documented against fifteen lines above: the caller is answered about a population they
    never named. `\"\"` must reach `bare_identifier`, which refuses it.
    """
    code = resident.main(["pin", *BASE, "--key", "p0", "--window-nodes", "1000",
                          "--label", ""])
    assert code == resident.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "REFUSED"
    assert "bare" in out["reason"] or "identifier" in out["reason"], out["reason"]
    assert PINS_META_KEY not in wired.meta, "a refused pin wrote a declaration"


def test_outside_with_an_empty_label_is_a_usage_error_not_a_Point_measurement(
        wired, capsys, monkeypatch):
    """The `outside` half of the same door: `\"\"` is not a record label, so it is usage."""
    _fake_psycopg(monkeypatch, _Conn())
    with pytest.raises(SystemExit) as exc:
        resident.main(["outside", *BASE, "--dsn", "postgresql://x", "--org-id",
                       "org_a", "--graph-id", "g_1", "--label", "", "--window-nodes",
                       "1000"])
    assert exc.value.code == resident.EXIT_USAGE


def test_a_declaration_pinned_under_an_unbounded_budget_refuses_the_sweep_not_the_eviction(
        sweep, capsys, tmp_path):
    """The invariant behind `PinBudget`'s low door is the SWEEP's, and it is fail-closed.

    ⛔ `pin(budget=PinBudget(max_nodes=…))` bypasses `resolve()`, so a budget can be
    declared with no window to bound it. What must hold regardless is that the sweep
    resolves its OWN budget from the real window and REFUSES rather than deleting a pinned
    node — the failure is "your pin set is unusable", never "your pins were swept". That
    invariant had no row.
    """
    from tortoise.resident_pin import PinBudget

    graph, calls = sweep
    # A huge cap with no window: the declaration is legal, the cap proves nothing.
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}

    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "2"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    assert calls == [], "the sweep reached `evict` for a pin set its own budget refuses"

    # And the sweep's refusal is about the CAP, not about an unreadable declaration.
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "REFUSED"
    assert "pinned closure" in out["reason"] or "budget" in out["reason"], out["reason"]
    # The low door still constructs the value it always did (an API constant, not a claim).
    assert PinBudget(max_nodes=10).window_nodes is None


def test_every_pin_refusal_carries_the_same_pins_shape_as_the_success_body(
        sweep, capsys, tmp_path, monkeypatch):
    """F6 — three of five pin envelopes omitted `applied`/`not_applied_reason`.

    ⛔ A consumer keying on `pins.applied` — the field the SUCCESS body defines and the
    key-space-mismatch refusal asserts — hit a KeyError on exactly the refusals it must act
    on. That is the "second shape for one refusal" class `tools/resident.py::_refused`
    already closed for its own envelopes; the sweep's five arms now share it too.
    """
    graph, _calls = sweep
    graph.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}

    # arm 1: the declaration is unusable (truncated read)
    from tortoise import resident_pin

    # NB: restore by hand, NOT monkeypatch.undo() — the `sweep` fixture shares this
    # monkeypatch instance, so an undo() would also unwire the CLI and the next arm would
    # fail for the wrong reason (measured while writing this row).
    original_limit = resident_pin.DEFAULT_KEY_LIMIT
    monkeypatch.setattr(resident_pin, "DEFAULT_KEY_LIMIT", 0)
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    body = json.loads(capsys.readouterr().out)
    assert body["verdict"] == "REFUSED"
    assert body["pins"]["applied"] is False
    assert body["pins"]["not_applied_reason"]
    monkeypatch.setattr(resident_pin, "DEFAULT_KEY_LIMIT", original_limit)

    # arm 2: the budget cannot be resolved
    monkeypatch.setenv("TORTOISE_PIN_MAX_NODES", "not-a-number")
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED
    body = json.loads(capsys.readouterr().out)
    assert body["pins"]["applied"] is False
    assert "cannot be resolved" in body["pins"]["not_applied_reason"]

    # arm 3: the declaration is READABLE but its set is over the cap, so `gate_reason()`
    # refuses the sweep. ⛔ This arm was the one the row did not cover: the two rows that
    # reach it assert `out["reason"]` only, so reverting THIS envelope to bare
    # `pins.as_dict()` left the suite green — the exact `KeyError` the row exists to close,
    # on the refusal an operator must act on (unpin, or raise `TORTOISE_PIN_MAX_NODES`).
    monkeypatch.setenv("TORTOISE_PIN_MAX_NODES", "1")
    code = cache_evict.main(_sweep_argv(_report_file(tmp_path), "--max-nodes", "1000"))
    assert code == cache_evict.CACHE_EVICTION_EXIT_REFUSED, "sanity: the set is over its cap"
    body = json.loads(capsys.readouterr().out)
    assert body["pins"]["applied"] is False, (
        "the over-budget refusal reported `applied` as anything but False — a monitor "
        "cannot tell a refused sweep from an applied one"
    )
    assert "pinned closure" in body["pins"]["not_applied_reason"]


def test_show_with_no_declaration_and_no_window_still_says_cap_checked_is_false(
        wired, capsys, monkeypatch):
    """F3 — the `cap_checked` OK-arm could be replaced by the constant `True` undetected.

    ⛔ The positive row asserts `is True`, which a constant satisfies, and the `is False` row is
    served by the separate hardcoded literal on the no-budget-with-keys path. The ONE state
    where the OK arm's own expression yields `False` — no declaration AND no resolvable
    window, so `show` falls through with `keys` empty — was reached by no test. Reporting
    only (the verdict is `OK` over a declaration that claims nothing), but the whole point of
    the field is that a monitor can trust it.
    """
    monkeypatch.delenv("TORTOISE_CACHE_MAX_NODES", raising=False)
    monkeypatch.delenv("TORTOISE_PIN_MAX_NODES", raising=False)
    assert PINS_META_KEY not in wired.meta, "sanity: nothing is declared"

    assert resident.main(["show", *BASE]) == resident.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["cap_checked"] is False, (
        "the OK arm reported a cap check on a tree with no window and no declaration — the "
        "`budget is not None` expression is not what this field is measuring"
    )
    assert "not checked" in out["reason"].lower()


def test_show_refuses_a_named_pair_that_disagrees_with_the_declaration(
        wired, capsys):
    """F8 — the `show --label`/`--key-prop` refusal was deletable with the suite green.

    ⛔ `show` accepts both flags (they sit on the shared flag set) and used to IGNORE
    them, so `show --label Source` on a `Point` declaration answered `OK` about a
    population the operator named and the verb never consulted — and `show --key-prop
    bogus` exited 0 where its own help promises a refusal. The loop that closes it was a
    round-6 fix with NO row behind it: replacing the whole block with `pass` left 40/40
    green, because no other row passes either flag to `show`. Same "answered about a
    population they never named" class the PR pins for `pin` and `outside`; pinned here.
    """
    wired.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}

    for flag, value, actual in (
            ("--label", "Source", "Point"),
            ("--key-prop", "bogus", "id")):
        with pytest.raises(SystemExit) as exc:
            resident.main(["show", *BASE, "--window-nodes", "1000", flag, value])
        assert exc.value.code != 0, (
            f"`show {flag} {value}` answered about a population the declaration does not "
            f"describe ({actual!r}) — a named pair that disagrees is a usage error"
        )
        captured = capsys.readouterr()
        assert captured.out.strip() == "", (
            f"`show {flag} {value}` emitted a JSON body before refusing — a consumer "
            f"reading `--out` would record an answer about the wrong population"
        )
        assert value in captured.err and actual in captured.err

    # naming the declaration's OWN pair is fine, and so is naming none
    assert resident.main(["show", *BASE, "--window-nodes", "1000",
                          "--label", "Point", "--key-prop", "id"]) == resident.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "OK"
