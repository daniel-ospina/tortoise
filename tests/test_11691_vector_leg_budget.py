"""#11691 — the record dense leg is bound by the READ's budget, not the door's.

THE DEFECT THESE PINS EXIST FOR. `run_vector_query` gives the two GRAPH vector
branches the read's budget — ``graph.query(..., timeout=timeout_ms)``, 500 ms by
default — while the record leg it replaced them with inherited
``record_serve.RECORD_STATEMENT_TIMEOUT_MS`` (15 s) plus a 10 s connect bound: a
30x mismatch between two legs of the SAME read. And the read's own cap did not
save the caller, because the fused read JOINS its workers on the way out
(`degradation_chain`'s ``ThreadPoolExecutor.__exit__``, ``search_engine.py``:
*"the caller therefore pays a straggler's own latency, never the cap"*). So one
slow record query turned a 500 ms search into a multi-second one AND recorded
``reason=timeout``, discarding rows it may have had.

THE CONTRACT, and why it is a construction parameter rather than a call argument
(owner decision, option A — `#11691`): the leg is a callable whose FOUR-argument
shape is load-bearing —
``search(entity_type, query_vec, limit, excluded_statuses=None)`` — because one
ordering pass consumes both legs' rows and the ranking cannot drift between them
by construction (`#6214`/`#10121`). A fifth, per-call budget argument would be
that same drift re-opened in a new position: a caller that omitted it would
silently be back on the 15 s door. So the budget is bound where the callable is
CONSTRUCTED (`record_vector.searcher(..., timeout_ms=…)`), the door applies
``min(RECORD_STATEMENT_TIMEOUT_MS, timeout_ms)``, and the shape does not move.

WHAT THE FALSIFIER ACTUALLY MODELS. The fake record door below does not assert
the bound and then agree with itself: it BEHAVES like a statement bounded by
whatever ``statement_timeout`` the door applied — it blocks for that long and is
then cancelled, exactly as Postgres does — and `run_vector_query` must survive
that by answering from the graph leg and NAMING the fallback. Delete the
threading and the fake is handed the door's 15 s, the query "runs" 30x past the
read's budget, and this file reds on the applied bound (the 15 s sleep itself is
refused rather than simulated — see `_Cursor`).
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import time

import pytest

from tortoise import record_serve, record_vector, search_engine
from tortoise.search_engine import MECHANISM_SCAN_FALLBACK, run_vector_query

#: The read's budget these pins use — deliberately NOT read from
#: `VECTOR_LEG_BUDGET_MS`, so the pins keep their own number and a change to the
#: constant has to face them.
BUDGET_MS = 500

#: What the read door applies when nobody tightens it.
DOOR_MS = 15000


class _QueryCanceled(Exception):
    """psycopg2's ``QueryCanceled`` shape — what a statement_timeout raises.

    Not imported from psycopg2: the point of these pins is that the bound is
    applied and honoured, and a driver import would make the file skip on a box
    without the extra. `vector_search` turns ANY exception into
    :class:`record_vector.RecordVectorError`, which is the caller's named
    fallback, so the class identity is not what is under test.
    """


class _FakeCursor:
    """A cursor whose dense read takes exactly as long as the bound allows.

    ``statement_timeout`` is read from the ``set_config`` the door executed, so
    the latency is not a test parameter — it is the value under test. A leg that
    would run far past the read's budget is NOT simulated to completion: a 15 s
    sleep in a pin is a slow test that reports the wrong cause, so it raises at
    once and records ``over_budget`` for the assertion to name.
    """

    def __init__(self, state: dict):
        self._state = state

    def execute(self, sql: str, params=None):
        self._state["statements"].append(sql)
        if "set_config('statement_timeout'" in sql:
            self._state["applied_ms"] = int(params[0])
            return None
        if "set_config('hnsw.iterative_scan'" in sql:
            return None
        applied = self._state.get("applied_ms")
        if applied is None:
            raise AssertionError(
                "the dense read ran with NO statement_timeout applied — "
                "the record door's whole point (#9558) is that a served read "
                "cannot be unbounded")
        if applied > BUDGET_MS * 4:
            # 30x the read's budget: this is the DEFECT, not a slow test.
            self._state["over_budget"] = applied
            raise _QueryCanceled(
                f"the dense read would have run for {applied} ms inside a "
                f"{BUDGET_MS} ms read")
        time.sleep(applied / 1000.0)
        self._state["read_cancelled_after_ms"] = applied
        raise _QueryCanceled(
            f"canceling statement due to statement timeout ({applied} ms)")

    def fetchall(self):
        raise AssertionError("unreachable — the read above raises")

    def fetchone(self):
        raise AssertionError("unreachable — the read above raises")

    def close(self):
        return None


class _FakeConn:
    def __init__(self, state: dict):
        self._state = state

    def cursor(self):
        return _FakeCursor(self._state)

    def close(self):
        return None


class _FakeGraph:
    """The GRAPH branch: one row, so "the graph answered" is observable."""

    ID = "graph-leg-point"

    def __init__(self):
        self.query_calls: list[tuple[str, dict, dict]] = []

    def query(self, cypher, params=None, timeout=None):
        self.query_calls.append((cypher, params or {}, {"timeout": timeout}))
        return _Result([(self.ID, 0.25)])


class _Result:
    def __init__(self, result_set):
        self.result_set = result_set


@pytest.fixture()
def armed(monkeypatch):
    """The cutover plus a tenant — the two gates `searcher()` refuses without."""
    import tortoise.record_writer as rw

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.setenv(record_vector.ARMING_ENV, "1")
    monkeypatch.setattr(rw, "record_tenant", lambda: None, raising=False)
    return ("org_11691", "graph_11691")


def _wire_record_door(monkeypatch, state: dict) -> None:
    """Route the REAL door at a fake driver, leaving its own bound in place.

    ⛔ `record_serve.open_record` is NOT replaced: its `min(ceiling, asked)` rule
    is the second half of this change, and a fake that re-implemented it would
    make the test agree with itself. Only the DRIVER is replaced.
    """
    import tortoise.record_projection as rp
    import tortoise.record_writer as rw

    monkeypatch.setattr(rw, "_default_connect",
                        lambda dsn: _FakeConn(state), raising=True)
    monkeypatch.setattr(rp, "scope_connection",
                        lambda conn, org: conn.cursor(), raising=True)


# ── the falsifier ──────────────────────────────────────────────────────────

def test_the_record_leg_is_bound_by_the_reads_own_budget(monkeypatch, armed):
    """A record leg slower than the read yields the GRAPH's answer, NAMED.

    RED if the budget stops being threaded at construction: the door then
    applies its own 15 s, the fake query "runs" 30x past the read's budget, and
    both the applied-bound assertion and the wall-clock assertion fail. RED if
    the engine stops falling back on a raised record leg: the answer is not the
    graph's and the trace carries no `scan_fallback`.
    """
    state: dict = {"statements": []}
    _wire_record_door(monkeypatch, state)

    searcher = record_vector.searcher(carried=armed, timeout_ms=BUDGET_MS)
    assert searcher is not None, "the leg must be armed for this pin to mean anything"

    graph = _FakeGraph()
    trace: list[dict] = []
    started = time.monotonic()
    hits = run_vector_query(
        graph, [0.5] * 384, limit=5, timeout_ms=BUDGET_MS,
        is_embedded=True, leg_trace=trace, pg_vector=searcher)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    # (1) THE BOUND ITSELF — the value the server would have enforced.
    assert state.get("over_budget") is None, (
        f"the record leg was bounded at {state.get('over_budget')} ms for a "
        f"{BUDGET_MS} ms read: the budget is not reaching the door "
        f"(applied {state.get('applied_ms')})")
    assert state["applied_ms"] == min(DOOR_MS, BUDGET_MS), (
        f"the door applied {state.get('applied_ms')} ms; a read's budget must "
        f"tighten the door's ceiling and never widen it")

    # (2) THE READ SURVIVES IT — inside a bounded multiple of its own budget.
    assert state.get("read_cancelled_after_ms") == BUDGET_MS, (
        "the query was not the one that was cancelled — the leg never ran")
    assert elapsed_ms < BUDGET_MS * 3, (
        f"the read took {elapsed_ms:.0f} ms for a {BUDGET_MS} ms budget: the "
        f"bound is not bounding the caller's wall clock")

    # (3) THE GRAPH ANSWERED, and the trace NAMES the fallback — never a silent
    #     `timeout` after seconds, and never an empty answer.
    assert [pid for pid, _score in hits] == [graph.ID], (
        f"the graph leg's answer did not survive the record leg's cancellation: "
        f"{hits}")
    vector_entries = [e for e in trace if e.get("leg") == "vector"]
    assert vector_entries, f"the read recorded no vector leg at all: {trace}"
    mechanisms = [e.get(search_engine.VECTOR_MECHANISM_KEY) for e in vector_entries]
    assert MECHANISM_SCAN_FALLBACK in mechanisms, (
        f"a record leg that did not answer must be reported as the fallback it "
        f"took (#4999), got mechanisms={mechanisms}")
    assert all(e.get("reason") != "timeout" for e in vector_entries), (
        "the leg answered; a `timeout` entry would discard a result it had")


def test_the_same_leg_unbounded_costs_the_door_bound(monkeypatch, armed):
    """The control: with NO budget the door's own 15 s stands (unchanged).

    Pins the OTHER half of the rule — this change tightens and does not replace
    the ceiling. Reconstructions (health, replay, backup, restore) legitimately
    need seconds, and they reach the same door.
    """
    state: dict = {"statements": []}
    _wire_record_door(monkeypatch, state)
    searcher = record_vector.searcher(carried=armed)
    assert searcher is not None

    with pytest.raises(record_vector.RecordVectorError):
        searcher("point", [0.5] * 384, 5, None)
    assert state.get("over_budget") == DOOR_MS, (
        f"an un-budgeted read must still get the door's {DOOR_MS} ms, got "
        f"{state.get('applied_ms')}")


# ── the door's rule, on its own ────────────────────────────────────────────

def test_the_door_ceiling_can_be_tightened_and_never_widened(monkeypatch):
    """`min(ceiling, asked)`, plus the ONE value that would mean "no bound".

    ``statement_timeout = 0`` is Postgres's "NO TIMEOUT AT ALL", so a caller
    whose budget defaulted or rounded to 0 would silently acquire the unbounded
    read the constant exists to prevent. 1 ms, never 0.
    """
    import tortoise.record_writer as rw

    for asked, expected in ((None, DOOR_MS), (BUDGET_MS, BUDGET_MS),
                            (60_000, DOOR_MS), (0, 1), (-5, 1)):
        state: dict = {"statements": []}
        monkeypatch.setattr(rw, "_default_connect",
                            lambda dsn, _s=state: _FakeConn(_s), raising=True)
        conn = record_serve.open_record("postgresql://localhost/record",
                                        statement_timeout_ms=asked)
        # The bound is executed by `open_record` itself, on the connection it
        # returns — read it from the fake the door was given.
        assert state["applied_ms"] == expected, (asked, state["applied_ms"])
        conn.close()


# ── the contract that must NOT move ────────────────────────────────────────

def test_the_searcher_keeps_its_four_argument_shape(armed):
    """Four arguments, bound to the read at CONSTRUCTION (owner option A).

    Option B — a fifth per-call budget argument — is refused because the
    four-argument shape is what makes one ordering pass consume both legs, and
    a caller who omitted a fifth argument would be silently back on the door's
    15 s bound. So the shape is pinned, not merely described.
    """
    searcher = record_vector.searcher(carried=armed, timeout_ms=BUDGET_MS)
    assert searcher is not None
    params = list(inspect.signature(searcher).parameters)
    assert params == ["entity_type", "query_vec", "limit", "excluded_statuses"], (
        f"the record leg's callable shape moved: {params}. A budget argument "
        f"belongs at CONSTRUCTION (`searcher(..., timeout_ms=…)`), not here.")
    with pytest.raises(TypeError):
        searcher("point", [0.5] * 384, 5, None, BUDGET_MS)


def test_the_sdk_helper_threads_the_budget_it_is_given(monkeypatch):
    """`sdk._record_vector_searcher(sdk, timeout_ms=…)` → `searcher(…)`.

    The three construction sites are the only place a read's budget can be
    attached to the record leg, so the helper that they all go through must not
    drop it.
    """
    from tortoise import sdk as sdk_mod

    seen: list[dict] = []

    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return object()

    monkeypatch.setattr(record_vector, "searcher", _spy, raising=True)

    class _WithTenant:
        _record_tenant = ("org_9", "graph_9")

    assert sdk_mod._record_vector_searcher(_WithTenant(), timeout_ms=BUDGET_MS)
    assert seen[-1] == {"carried": ("org_9", "graph_9"),
                        "timeout_ms": BUDGET_MS}, seen[-1]
    # Omitted -> None, which leaves the door's own bound in place.
    assert sdk_mod._record_vector_searcher(_WithTenant())
    assert seen[-1]["timeout_ms"] is None, seen[-1]


def test_every_construction_site_passes_a_budget():
    """All THREE `_record_vector_searcher(self, …)` sites in `sdk.py`.

    A site that drifts back to the bare call is a read whose record leg is on
    the door's 15 s again, and nothing at runtime would say so — the leg would
    look armed and healthy while it quietly outlived the read.
    """
    source = pathlib.Path(sdk_module_file()).read_text()
    sites = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_record_vector_searcher"):
            sites.append(node)
    assert len(sites) == 3, (
        f"expected the three known construction sites, found {len(sites)} — "
        f"a new one must pass the read's budget too")
    for node in sites:
        keywords = {kw.arg for kw in node.keywords}
        assert "timeout_ms" in keywords, (
            f"the call at sdk.py:{node.lineno} does not bind the read's budget, "
            f"so its record leg keeps the door's {DOOR_MS} ms bound")


def sdk_module_file() -> str:
    from tortoise import sdk as sdk_mod

    return sdk_mod.__file__


# ── deliverable 2: the numbers are arithmetic on committed files ───────────

def test_the_committed_measurement_prices_itself_from_pricing_json():
    """Every dollar in the evidence doc re-derives from the two artifacts.

    ⛔ The same discipline `docs/evidence/9558-record-write-cost/README.md` states:
    *"every number here is arithmetic on a committed file"*. A figure typed into
    the prose instead of derived from `measurement.json` × `product/pricing.json`
    is a number nobody can correct — and the rates are the owner's, so a hand-copy
    goes stale silently the day a rate moves. This pin recomputes the whole money
    half from the two JSON files: the storage principal at BOTH published rates,
    the per-tier flow at the volumes `pricing.json` itself lists, and the bytes
    the production figure is scaled from.
    """
    import json
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    art = json.loads((repo / "docs/evidence/11691-vector-leg-budget/"
                             "measurement.json").read_text())
    pricing = json.loads((repo / "product/pricing.json").read_text())
    storage = pricing["billing"]["storage"]["dimensions"]["postgres_storage"]
    bytes_per_gb = pricing["billing"]["storage"]["bytes_per_gb"]
    pv = art["production_volume"]

    # (1) the production bytes ARE the measured bytes/row × the production rows,
    #     so the scale figure cannot be edited without its own denominator.
    assert pv["production_total_bytes"] == round(
        pv["bytes_per_row"] * pv["production_rows"]), pv
    assert pv["production_index_bytes"] == round(
        pv["index_bytes_per_row"] * pv["production_rows"]), pv

    # (2) the two storage rates, re-applied.
    for field, rate in (("principal_cost_basis",
                         storage["cost_basis_usd_per_gb_month"]),
                        ("principal_overage",
                         storage["overage_price_usd_per_gb_month"])):
        expected = round(pv["production_total_bytes"] / bytes_per_gb * rate, 4)
        assert pv["usd_per_month"][field] == expected, (field, expected)

    # (3) the flow, at the write volumes pricing.json publishes per tier.
    per_1k = (art["deltas"]["hnsw_bytes_per_1000_writes"]
              + art["deltas"]["payload_bytes_per_1000_writes"])
    for tier, spec in pricing["tiers"].items():
        ops = spec.get("included_write_ops_per_month")
        if not ops:
            continue
        for key, rate in (("cost_basis", storage["cost_basis_usd_per_gb_month"]),
                          ("overage", storage["overage_price_usd_per_gb_month"])):
            expected = round(per_1k * (ops / 1000.0) / bytes_per_gb * rate, 4)
            assert pv["flow_usd_per_month"][f"{tier}_{key}"] == expected, (
                tier, key, expected)

    # (4) the added WRITE TIME is the paired p50, not the host-contaminated
    #     unpaired one — and the per-tier seconds follow from it.
    added = pv["added_write_time"]
    assert added["ms_per_write_p50"] == (
        art["deltas"]["scale_population"]["paired_total_ms_p50"]), added
    for tier, spec in pricing["tiers"].items():
        ops = spec.get("included_write_ops_per_month")
        if not ops:
            continue
        assert added["seconds_per_month_at_tier_volume"][tier] == round(
            added["ms_per_write_p50"] * ops / 1000.0, 1), tier
