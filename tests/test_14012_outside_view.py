"""#14012 — *see what's outside*: the view, its honesty guards, and its one truth source.

The done-when is *"the outside view matches what the evictor actually removed in a
scripted run"*, so the central test here runs the REAL executor (`evict`) against a
fake graph, and then asks the view what is outside. No expected number is written by
hand: the removed keys are the eviction's own `deleted` tuple.

⛔ The record side is a fake CURSOR with a documented contract, not a SQL engine. It
interprets the three aggregate shapes this module issues and nothing else, and the
suite separately asserts the STATEMENTS carry the tenant scope and the `= 'graph'` tier
predicate — so "the view is derived from the record, scoped, and tier-correct" is
pinned even though no Postgres is involved (the PR lane has no URI; see
`test_14012_resident_pin.py`'s header for why a hermetic suite is mandatory here).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import outside_view, record_projection
from tortoise.cache_eviction import Assessment, CacheState
from tortoise.cache_evictor import Candidate, EvictionPolicy, evict

ORG = "org_test"
GRAPH_ID = "g_abc"


# ── the record side ─────────────────────────────────────────────────────────────
class _Cursor:
    """Interprets the three aggregate shapes `outside_view` issues, and nothing else.

    The row filter is the module's own: tenant scope, the tier predicate when the
    statement carries it, and the resident-key complement (`node_key = ANY(keys)`).
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, tuple]] = []
        self._last: list = []
        self._org: str | None = None

    # psycopg's cursor protocol, as far as these two readers use it
    def execute(self, sql: str, params=None) -> None:
        params = params or ()
        self.executed.append((sql, params))
        if "bypassing" in sql:
            # `record_projection.TENANT_SCOPE_SQL` — the scope read-back.
            self._last = [{"tenant": self._org, "bypassing": False}]
            return
        if "set_config" in sql or "current_setting" in sql:
            self._org = params[0] if params else None
            self._last = []
            return
        self._last = self._answer(sql, params)

    def fetchall(self):
        return list(self._last)

    def fetchone(self):
        return self._last[0] if self._last else None

    def _answer(self, sql: str, params: tuple) -> list[dict]:
        if "min(created_at) AS oldest" in sql:
            org_id, graph_id, keys = params
            rows = self._scope(org_id, graph_id, sql, keys)
            if not rows:
                return [{"outside": 0, "oldest": None, "newest": None}]
            return [{
                "outside": len(rows),
                "oldest": min(r["created_at"] for r in rows),
                "newest": max(r["created_at"] for r in rows),
            }]
        if "GROUP BY 1" in sql:
            org_id, graph_id, keys, top = params
            rows = self._scope(org_id, graph_id, sql, keys)
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["point_kind"]] = counts.get(row["point_kind"], 0) + 1
            ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            return [{"topic": t, "n": n} for t, n in ordered[:top]]
        if "count(*) AS n FROM public" in sql:
            org_id, graph_id = params
            return [{"n": len(self._scope(org_id, graph_id, sql, None))}]
        raise AssertionError(f"unexpected statement: {sql}")

    def _scope(self, org_id, graph_id, sql, keys) -> list[dict]:
        rows = [r for r in self.rows
                if r["org_id"] == org_id and r["graph_id"] == graph_id]
        if "cache_tier = 'graph'" in sql:
            rows = [r for r in rows if r["cache_tier"] == "graph"]
        if keys is not None:
            resident = set(keys)
            rows = [r for r in rows if r["node_key"] not in resident]
        return rows


class _Conn:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.cursors: list[_Cursor] = []

    def cursor(self) -> _Cursor:
        cur = _Cursor(self.rows)
        self.cursors.append(cur)
        return cur

    @property
    def executed(self) -> list[tuple[str, tuple]]:
        out: list[tuple[str, tuple]] = []
        for cur in self.cursors:
            out.extend(cur.executed)
        return out


def _record_rows(keys: list[str], *, kind: str = "statement",
                 tier: str = "graph") -> list[dict]:
    return [
        {
            "org_id": ORG,
            "graph_id": GRAPH_ID,
            "node_key": key,
            "created_at": f"2026-11-{index + 1:02d}T00:00:00Z",
            "point_kind": kind if index % 2 == 0 else "decision",
            "cache_tier": tier,
        }
        for index, key in enumerate(keys)
    ]


# ── the graph side ──────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, rows: list[list]) -> None:
        self.result_set = rows


class _Graph:
    def __init__(self, keys: list[str], *, name: str = "g") -> None:
        self.name = name
        self.nodes: dict[int, dict] = {}
        for key in keys:
            self.nodes[len(self.nodes)] = {"id": key}
        self.statements: list[str] = []

    def node_keys(self) -> list[str]:
        return sorted(p["id"] for p in self.nodes.values())

    def ro_query(self, statement: str, params=None):
        return self.query(statement, params=params)

    def query(self, statement: str, params=None):
        self.statements.append(statement)
        params = params or {}
        if "MATCH (n:Point) RETURN count(n)" in statement:
            return _Result([[len(self.nodes)]])
        if "MATCH (n:Point) RETURN n.id AS key LIMIT" in statement:
            limit = int(statement.rsplit("LIMIT", 1)[1].strip())
            return _Result([[p["id"]] for p in
                            list(self.nodes.values())[:limit]])
        if statement.startswith("MATCH (n) RETURN count(n)"):
            return _Result([[len(self.nodes)]])
        if "DETACH DELETE n RETURN count(n)" in statement:
            removed = 0
            for row in params["rows"]:
                props = self.nodes.get(row["id"])
                if props is None or props.get("id") != row["key"]:
                    continue
                del self.nodes[row["id"]]
                removed += 1
            return _Result([[removed]])
        if "RETURN id(n)" in statement:
            return _Result([[r["id"]] for r in (params.get("rows") or [])
                            if r["id"] in self.nodes])
        raise AssertionError(f"unexpected statement: {statement}")


class _Client:
    def info(self, section: str):
        return {"used_memory": 1_000_000}


def _assessment(nodes: int) -> Assessment:
    return Assessment(
        state=CacheState(
            graph_name="g", resident_bytes=1_000_000, maxmemory_bytes=0,
            maxmemory_policy="noeviction", ceiling_bytes=1,
            ceiling_source="declared (test)", graph_key_ttl_seconds=-1,
            keys_per_graph=1, graph_memory_usage_mb=None,
            graph_memory_usage_note=None,
        ),
        classes=(),
        verdict="PLAN",
        plan={"evictable": [{"population": "turn", "nodes": nodes,
                             "instrument": "tools/turn_parity_census.py"}]},
    )


def _coldest(graph: _Graph, limit: int, population: str) -> list[Candidate]:
    return [Candidate(node_id=i, key=p["id"], population=population)
            for i, p in sorted(graph.nodes.items())[:limit]]


def _view(graph: _Graph, conn: _Conn, **kwargs) -> outside_view.OutsideView:
    return outside_view.read(graph, conn, org_id=ORG, graph_id=GRAPH_ID, **kwargs)


# ── the done-when ───────────────────────────────────────────────────────────────
def test_the_outside_view_matches_what_the_evictor_actually_removed():
    """A scripted eviction, then the view — the removed keys are the eviction's own."""
    keys = ["p0", "p1", "p2", "p3", "p4"]
    graph = _Graph(keys)
    conn = _Conn(_record_rows(keys))

    before = _view(graph, conn)
    assert before.complete is True
    assert before.outside == 0, "nothing has left the fast layer yet"
    assert before.resident == len(keys)

    report = evict(
        _assessment(nodes=3), _Client(), graph,
        population="turn",
        selector=_coldest, record_probe=lambda c: c.key is not None,
        dry_run=False,
        policy=EvictionPolicy(max_nodes=3, batch_size=10, max_passes=2),
    )
    removed = sorted(c.key for c in report.deleted)
    assert removed, "the scripted run evicted nothing, so the comparison is vacuous"

    after = _view(graph, conn)

    assert after.complete is True
    assert after.outside == len(removed), (
        "the outside view disagrees with what the evictor removed — the view is "
        "derived from the graph, so the two must be the same fact"
    )
    assert after.resident == len(keys) - len(removed)
    assert after.record_total == len(keys), (
        "nothing was removed from the RECORD — 'nothing is lost' is the promise"
    )
    rows = _record_rows(keys)
    gone = [r for r in rows if r["node_key"] in removed]
    assert after.oldest == min(r["created_at"] for r in gone)
    assert after.newest == max(r["created_at"] for r in gone)
    kinds = {}
    for row in gone:
        kinds[row["point_kind"]] = kinds.get(row["point_kind"], 0) + 1
    assert dict((b.topic, b.count) for b in after.topics) == kinds


def test_the_view_counts_nothing_it_could_not_measure():
    """A partial resident read is UNMEASURED, never a short (over-counting) answer."""
    graph = _Graph(["p0", "p1", "p2"])
    conn = _Conn(_record_rows(["p0", "p1", "p2"]))

    view = _view(graph, conn, key_limit=2)

    assert view.complete is False
    assert view.truncated is True
    assert view.outside is None, (
        "the complement of a TRUNCATED resident set reports resident nodes as outside — "
        "the direction that needlessly worries a customer, so it must not be computed"
    )
    assert "could not be measured" in view.headline or "could not be" in view.reason
    assert not [s for s, _ in conn.executed if "min(created_at)" in s], (
        "an unmeasurable view still issued the record query"
    )


def test_a_record_only_row_is_not_outside_the_fast_layer():
    """§9.1's tier was never meant to be in the graph — it is not 'outside' it."""
    rows = _record_rows(["p0"]) + _record_rows(["t0"], tier="record_only")
    graph = _Graph(["p0"])
    view = _view(graph, _Conn(rows))
    assert view.outside == 0
    assert view.record_total == 1, (
        "a record_only row was counted as residency-eligible; it is outside the graph "
        "BY DECLARATION, so counting it would report a demotion that never happened"
    )


def test_the_statements_carry_the_tenant_scope_and_the_indexable_tier_predicate():
    graph = _Graph(["p0", "p1"])
    conn = _Conn(_record_rows(["p0", "p1"]))
    _view(graph, conn)

    aggregates = [(s, p) for s, p in conn.executed if "FROM public.point" in s]
    assert aggregates, "the view never read the record"
    for sql, params in aggregates:
        assert "org_id = %s AND graph_id = %s" in sql, (
            "a record read without the tenant scope is every tenant's rows"
        )
        assert params[0] == ORG and params[1] == GRAPH_ID
        # ⛔ `= 'graph'`, never `<> 'record_only'`: the partial index
        # `idx_point_graph_tier` is only used where the predicate IMPLIES it.
        assert "cache_tier = 'graph'" in sql
        assert "<> 'record_only'" not in sql


def test_the_view_never_claims_something_was_deleted_or_lost():
    graph = _Graph(["p0"])
    conn = _Conn(_record_rows(["p0", "p1"]))
    view = _view(graph, conn)
    text = (view.headline + " " + str(view.as_dict())).lower()
    for forbidden in ("deleted", "lost", "removed from", "permanently", "gone"):
        assert forbidden not in text, (
            f"the outside view says {forbidden!r} — the promise is 'nothing is lost', "
            f"and this view is a statement about where an item is, never about whether "
            f"it exists"
        )
    assert "still in your record" in view.headline


def test_an_empty_view_says_so_rather_than_showing_a_zero_count():
    graph = _Graph(["p0"])
    view = _view(graph, _Conn(_record_rows(["p0"])))
    assert view.outside == 0
    # ⛔ The zero case is SCOPED to the view's label: "everything in your record" was a
    # completeness claim made from a single-label count (#2952).
    assert "Nothing outside the fast layer for the Point items" in view.headline
    assert "everything in your record" not in view.headline.lower()


def test_a_refused_record_read_is_unmeasured_not_zero():
    class _Broken(_Conn):
        def cursor(self):
            cur = super().cursor()

            def _boom(sql, params=None):
                if "FROM public.point" in sql and "count(*) AS n" not in sql:
                    raise RuntimeError("record unreachable")
                return _Cursor.execute(cur, sql, params)

            cur.execute = _boom  # type: ignore[method-assign]
            return cur

    graph = _Graph(["p0"])
    view = _view(graph, _Broken(_record_rows(["p0"])))
    assert view.complete is False
    assert view.outside is None
    assert "could not be read" in view.reason


def test_an_unreadable_graph_is_unmeasured_not_zero():
    class _BrokenGraph(_Graph):
        def query(self, statement, params=None):
            if "n.id AS key" in statement:
                raise RuntimeError("transport blip")
            return super().query(statement, params=params)

    view = _view(_BrokenGraph(["p0"]), _Conn(_record_rows(["p0"])))
    assert view.complete is False
    assert view.outside is None


def test_the_scope_is_required():
    with pytest.raises(ValueError):
        outside_view.read(_Graph(["p0"]), _Conn([]), org_id="", graph_id=GRAPH_ID)


def test_the_columns_this_module_reads_are_real_record_columns():
    """A renamed record column must be a RED here, not a SQL error in production."""
    columns = record_projection.table_columns()["point"]
    missing = sorted(set(outside_view.READ_COLUMNS) - set(columns))
    assert not missing, (
        f"outside_view reads {missing}, which public.point does not have "
        f"(record_projection.table_columns() parsed from the migrations)"
    )


def test_a_useless_key_bound_is_refused_rather_than_read_as_an_empty_cache():
    """`limit=0` must not reach the truncation arm with an empty resident set.

    The floor used to be checked on the `+1` probe row (`0 + 1 = 1`), so a caller could
    ask for zero keys and receive `([], truncated=True)` — the exact shape the
    truncation guard reports as UNMEASURED, reached through an argument that looks
    harmless.
    """
    graph = _Graph(["p0"])
    for bad in (0, -1, True, "10"):
        with pytest.raises(ValueError):
            outside_view.resident_keys(graph, limit=bad)


# ── the two fail-open paths review round 2 found ────────────────────────────────
def test_a_topic_column_that_is_not_a_record_column_is_refused():
    """`topic_column` is the ONE identifier that reaches a SQL string — allowlisted.

    `TOPICS_SQL` puts it in the SELECT list and `GROUP BY 1`, which is an IDENTIFIER
    position: `%s` covers values only, so a `.format` that skips the check turns a
    caller-supplied value into SQL structure (psycopg's own guidance: allowlist the
    identifier, do not merely interpolate it). Every other identifier in this module is
    already checked, so this one must be too — it is a library door, and the next
    caller of it is the customer-facing API verb.
    """
    for bad in ("point_kind; DROP TABLE point", "(SELECT max(node_key) FROM public.point)",
                "not_a_column", "created_at2"):
        conn = _Conn(_record_rows(["p0"]))
        with pytest.raises(ValueError) as exc:
            _view(_Graph(["p0"]), conn, topic_column=bad)
        assert "not a column of public.point" in str(exc.value)
        assert not [s for s, _ in conn.executed if "FROM public.point" in s], (
            f"the refused topic_column {bad!r} still reached a statement"
        )


def test_a_real_topic_column_reaches_the_statement():
    """Non-vacuity for the allowlist above: it must not refuse EVERY column."""
    conn = _Conn(_record_rows(["p0"]))
    _view(_Graph(["p0"]), conn, topic_column="created_at")
    topics = [s for s, _ in conn.executed if "GROUP BY 1" in s]
    assert topics, "the topic statement was never issued"
    assert "SELECT created_at AS topic" in topics[0], (
        "a real `point` column was not the one interpolated — the allowlist accepted "
        f"it but the statement does not carry it: {topics[0]!r}"
    )


def test_an_unmeasured_cross_check_is_not_complete():
    """`record_total` failing ALONE must still make `complete` False.

    `_record_total` issues its own statement after the span and topic reads have already
    succeeded, so it can fail alone (a statement timeout, a dropped connection, a
    `count(*)` permission the aggregates do not need). It swallows the exception and
    returns None — and `complete` used to be a literal `True` on this path, so the field
    a monitor keys on, and `tools/resident.py`'s `verdict: OK` / exit 0, both failed
    OPEN over a partial read.
    """

    class _NoTotal(_Conn):
        def cursor(self):
            cur = super().cursor()

            def _boom(sql, params=None):
                # ⛔ `GROUP BY` excluded: the TOPIC statement also carries
                # `count(*) AS n FROM public.…`, so the narrower match is the
                # record-total statement alone.
                if ("count(*) AS n FROM public" in sql
                        and "GROUP BY" not in sql):
                    raise RuntimeError("permission denied for relation point")
                return _Cursor.execute(cur, sql, params)

            cur.execute = _boom  # type: ignore[method-assign]
            return cur

    view = _view(_Graph(["p0"]), _NoTotal(_record_rows(["p0", "p1"])))

    assert view.outside == 1, "sanity: the span read itself succeeded"
    assert view.record_total is None, "sanity: the cross-check could not be read"
    assert view.complete is False, (
        "an unmeasured cross-check was reported as a COMPLETE view — `complete` is `True` "
        "only when both halves were read in full, and it is what the CLI turns into "
        "`verdict: OK` / exit 0"
    )
    assert "cross-check" in view.reason, (
        f"the reason does not name what was missing — got {view.reason!r}"
    )

# ── merged from review round 1 (this lane) — two cases round 2 did not cover ───
def test_a_scope_refusal_is_unmeasured_not_a_traceback():
    """`_apply_tenant_scope` REFUSES by design; outside the guard it escaped as one.

    A DSN whose role bypasses RLS, a GUC that could not be set, or an unverifiable scope
    makes it raise (#9600). Outside the `try`, that escaped out of the verb as a
    traceback whose exit 1 is the USAGE code — telling a scheduler to retry FLAGS against
    a DSN that can never work.
    """
    class _Mismatched(_Cursor):
        def execute(self, sql, params=None):
            if "bypassing" in sql:
                self.executed.append((sql, params or ()))
                self._last = [{"tenant": "someone_else", "bypassing": False}]
                return
            return super().execute(sql, params)

    class _Conn2(_Conn):
        def cursor(self):
            cur = _Mismatched(self.rows)
            self.cursors.append(cur)
            return cur

    view = _view(_Graph(["p0"]), _Conn2(_record_rows(["p0"])))
    assert view.complete is False
    assert view.outside is None
    assert "record could not be read" in view.reason
    assert "tenant GUC" in view.reason, "the refusal's own reason was swallowed"


def test_complete_is_false_when_the_RESIDENT_count_alone_fails():
    """The third number that can fail alone — round 2's arm covers `record_total`."""
    class _NoCount(_Graph):
        def query(self, statement, params=None):
            if "RETURN count(n)" in statement:
                raise RuntimeError("count blip")
            return super().query(statement, params=params)

    view = _view(_NoCount(["p0"]), _Conn(_record_rows(["p0"])))
    assert view.resident is None
    assert view.complete is False
    assert "resident figure" in view.reason
