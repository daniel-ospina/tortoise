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
``min(RECORD_STATEMENT_TIMEOUT_MS, record_statement_ms(timeout_ms))``, and the
shape does not move.

WHAT THE FALSIFIER ACTUALLY MODELS. The fake record door below does not assert
the bound and then agree with itself: it BEHAVES like a statement bounded by
whatever ``statement_timeout`` the door applied — it blocks for that long and is
then cancelled, exactly as Postgres does — and `run_vector_query` must survive
that by answering from the graph leg and NAMING the fallback. Delete the
threading and the fake is handed the door's 15 s, the query "runs" 30x past the
read's budget, and this file reds on the applied bound (the 15 s sleep itself is
refused rather than simulated — see `_Cursor`).

⛔ TWO CLOCKS, NOT ONE (PR #13133 review P1). ``statement_timeout`` bounds a
STATEMENT and the timer starts when the statement does — after the pool borrow,
the connect and the ``set_config`` round trips. So a leg bound to the WHOLE read
budget is cancelled AT the fused read's deadline, the graph branch that must
answer after it has no time left, and `degradation_chain` records
``reason="timeout"`` and discards the rows: the issue's latency half fixed, its
rows-and-named-fallback half not. That is why the budget is DIVIDED before it
reaches the door (``record_vector.record_statement_ms``: two bounded statements
per read, a third each) and why the second pin below drives the real collector
instead of calling `run_vector_query` directly — the direct call has no deadline,
so it cannot see this class at all.

⛔ AND THE DIVIDED-OFF RESERVE IS HANDED OVER (#11691 review P2). Dividing buys
the graph branch a third, but `run_vector_query` originally gave the graph
branches ``timeout=timeout_ms`` — the WHOLE budget, restarted from after the
record leg had already spent its share — so the fallback could end at ~666 ms of
a 500 ms read and be discarded as a ``timeout`` anyway. The fall-through path now
passes :func:`search_engine.remaining_read_ms` (what is left, measured from just
before the record leg was called, floored at 1 ms so an exhausted read is not
handed ``timeout=0``, which drivers read as NO TIMEOUT). Pin:
``test_the_graph_branch_is_handed_what_is_left_of_the_read``, with
``test_a_graph_only_read_keeps_the_whole_budget`` as its control — the narrowing
is scoped to the record fall-through and must not shave the pre-#11642 path.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re
import textwrap
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

#: The fused read's collector takes only futures that are DONE when its deadline
#: expires, so the record leg cannot be given the whole budget (review P1):
#: `record_vector.record_statement_ms` divides it by the number of bounded
#: statements the leg may issue, and the remainder is the graph branch's reserve.
LEG_STATEMENT_MS = BUDGET_MS // record_vector.RECORD_LEG_STATEMENTS_PER_READ


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
    """The cutover plus a tenant — the two gates `searcher()` refuses without.

    ⚠️ The SINGLE-TENANT declaration is part of the arming now (#13331 review
    F4): `TORTOISE_RECORD_JOURNAL` names ONE journal path, process-globally, so
    `journal_ready` refuses to apply it where the deployment did not declare that
    it serves one graph (#9192). A test that stands in for an armed single-tenant
    box must say so, exactly as the deployment does.
    """
    import tortoise.record_writer as rw

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.setenv(record_vector.ARMING_ENV, "1")
    monkeypatch.setenv(rw.RECORD_SINGLE_TENANT_ENV, "1")
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
    assert state["applied_ms"] == min(DOOR_MS, LEG_STATEMENT_MS), (
        f"the door applied {state.get('applied_ms')} ms; a read's budget must "
        f"tighten the door's ceiling and never widen it, and it must reach the "
        f"door DIVIDED ({LEG_STATEMENT_MS} ms per statement for a {BUDGET_MS} ms "
        f"read)")

    # (2) THE READ SURVIVES IT — inside a bounded multiple of its own budget.
    assert state.get("read_cancelled_after_ms") == LEG_STATEMENT_MS, (
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


def test_the_fused_read_keeps_the_graphs_answer_inside_its_own_budget(monkeypatch, armed):
    """The COLLECTOR's clock, not just the statement's (PR #13133 review P1).

    `#11691`'s acceptance has two halves: (a) the leg is bound by
    ``min(door, read budget)``, and (b) *a slow record yields a NAMED
    `scan_fallback` to the graph INSIDE the read's budget*. The direct
    `run_vector_query` call above proves (a) and the naming, and cannot see (b):
    it has no deadline, so a leg that is cancelled AT the read's budget still
    answers there.

    The path that ships is `degradation_chain`, and its collector takes only
    futures that are ALREADY done when its deadline expires — otherwise the
    strategy is recorded ``reason="timeout"`` and its rows are DISCARDED. Since
    the record leg's timer starts at the statement (after the borrow, the connect
    and the `set_config` round trips), a leg on the whole budget is cancelled too
    late for the graph branch to answer.

    RED if `record_vector.record_statement_ms` stops dividing: the leg sleeps the
    read's whole budget, the collector times out first, and both the rows and the
    `scan_fallback` entry go with it — the state the issue was filed about.
    """
    state: dict = {"statements": []}
    _wire_record_door(monkeypatch, state)
    searcher = record_vector.searcher(carried=armed, timeout_ms=BUDGET_MS)
    assert searcher is not None, "the leg must be armed for this pin to mean anything"

    graph = _FakeGraph()
    trace: list[dict] = []
    started = time.monotonic()
    fused = search_engine.degradation_chain(
        graph, "what did the record cost", None, [0.5] * 384,
        {"fts": False, "vector": True, "structural": False},
        entity_type="point", limit=5, elevated_timeout_ms=BUDGET_MS,
        leg_trace=trace, pg_vector=searcher)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert [pid for pid, _score in fused.get("vector", [])] == [graph.ID], (
        f"the fused read lost the graph's answer to the record leg's "
        f"cancellation: {fused}. A leg cancelled at the read's own deadline "
        f"leaves the collector nothing — that is the defect, not a slow test.")
    vector_entries = [e for e in trace if e.get("leg") == "vector"]
    assert vector_entries, f"the fused read recorded no vector leg at all: {trace}"
    mechanisms = [e.get(search_engine.VECTOR_MECHANISM_KEY) for e in vector_entries]
    assert MECHANISM_SCAN_FALLBACK in mechanisms, (
        f"the fused read must NAME the fallback it took (#4999), got {mechanisms}")
    assert all(e.get("reason") != "timeout" for e in vector_entries), (
        f"the leg answered, so a `timeout` entry discards a result it had: {trace}")
    # ⛔ THE DISCRIMINATOR IS ARITHMETIC, not the wall clock: with the budget
    # divided, at most TWO statements (the kNN read, and the shortfall `count(*)`
    # on the short path) fit STRICTLY INSIDE the read, which is what leaves the
    # graph branch a reserve after the cancellation. Give the leg the whole
    # budget and two statements equal the read — no reserve, and whether the
    # collector's post-deadline rescan happens to salvage the answer becomes a
    # race (measured: it does, 501 ms in, which is why a wall-clock assertion
    # alone would pin nothing here).
    assert state["applied_ms"] * 2 < BUDGET_MS, (
        f"the leg may issue two statements of {state['applied_ms']} ms inside a "
        f"{BUDGET_MS} ms read — that spends the WHOLE budget on the record and "
        f"leaves the graph fallback nothing to answer from")
    # A gross-regression backstop only, loose on purpose: the box runs a fleet and
    # this call includes a real sleep.
    assert elapsed_ms < BUDGET_MS * 2, (
        f"the fused read took {elapsed_ms:.0f} ms for a {BUDGET_MS} ms budget")


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


# ── the reserve is HANDED OVER, not just left on the clock ─────────────────

def test_the_graph_branch_is_handed_what_is_left_of_the_read(monkeypatch, armed):
    """The fallback's budget is the REMAINDER, not the whole read again.

    PR #13133 review P2. The division of the read's budget into thirds buys the
    graph branch a reserve, but a reserve nobody spends is not a bound: pre-fix
    `run_vector_query` gave the graph branches ``timeout=timeout_ms`` — the
    WHOLE budget, measured from AFTER the record leg had already spent its share
    — so a leg cancelled at 166 ms of a 500 ms read could run to ~666 ms, past
    the collector's 500 ms deadline, and the fused read then recorded
    ``reason="timeout"`` and DISCARDED the rows it had: exactly the outcome the
    division exists to prevent.

    RED if the remainder stops being threaded: the graph branch is handed
    ``BUDGET_MS`` and the first assertion fails (500 is not <= 334). RED if the
    floor is dropped from :func:`search_engine.remaining_read_ms`: the third pin
    below reads ``0`` for an exhausted read.
    """
    state: dict = {"statements": []}
    _wire_record_door(monkeypatch, state)
    searcher = record_vector.searcher(carried=armed, timeout_ms=BUDGET_MS)
    assert searcher is not None, "the leg must be armed for this pin to mean anything"

    graph = _FakeGraph()
    trace: list[dict] = []
    hits = run_vector_query(
        graph, [0.5] * 384, limit=5, timeout_ms=BUDGET_MS,
        is_embedded=True, leg_trace=trace, pg_vector=searcher)

    assert graph.query_calls, "the graph branch was never consulted — nothing to pin"
    handed = [call[2]["timeout"] for call in graph.query_calls]
    # ARITHMETIC, not the wall clock: the leg slept its own share before it was
    # cancelled, so elapsed >= LEG_STATEMENT_MS and what is left can be at most
    # the other two thirds of the read.
    assert max(handed) <= BUDGET_MS - LEG_STATEMENT_MS, (
        f"the graph branch was handed {handed} ms of a {BUDGET_MS} ms read AFTER "
        f"the record leg had already spent {state.get('applied_ms')} ms of it — "
        f"the reserve is not being handed over, so the fallback can outlive the "
        f"read and the collector discards its rows as `timeout`")
    assert min(handed) >= 1, (
        f"a graph branch was handed timeout={min(handed)}: 0 means NO TIMEOUT "
        f"to the driver, which is the unbounded leg this change exists to close")

    # The fallback still ANSWERS, and still NAMES itself — the bound must not be
    # bought by turning a slow read into an empty one.
    assert [pid for pid, _score in hits] == [graph.ID], (
        f"the bounded graph branch lost its answer: {hits}")
    mechanisms = [e.get(search_engine.VECTOR_MECHANISM_KEY)
                  for e in trace if e.get("leg") == "vector"]
    assert MECHANISM_SCAN_FALLBACK in mechanisms, (
        f"the fallback must stay named (#4999), got {mechanisms}")


def test_a_graph_only_read_keeps_the_whole_budget(monkeypatch):
    """The control: with NO record leg, the graph branch is unchanged.

    The narrowing is scoped to the RECORD fall-through. A box with no searcher
    armed (every caller before #11642, and any box whose record is not armed)
    must be byte-identical — same ``timeout`` on every graph query as before this
    change, no substituted remainder. Without this pin, shaving the budget on
    the graph-only path would look like the same fix and quietly shorten every
    existing dense read.
    """
    graph = _FakeGraph()
    trace: list[dict] = []
    run_vector_query(
        graph, [0.5] * 384, limit=5, timeout_ms=BUDGET_MS,
        is_embedded=True, leg_trace=trace)

    assert graph.query_calls, "the graph branch was never consulted — nothing to pin"
    handed = {call[2]["timeout"] for call in graph.query_calls}
    assert handed == {BUDGET_MS}, (
        f"a graph-only read was handed {sorted(handed)} instead of its own "
        f"{BUDGET_MS} ms budget — the remainder must narrow the RECORD fallback "
        f"only, never the pre-#11642 path")


def test_the_remaining_budget_floors_at_one_and_never_widens():
    """`remaining_read_ms`'s two edge rules, on their own (no sleep).

    ``0`` is not a small timeout, it is NO TIMEOUT — the trap
    `record_vector.record_statement_ms` floors its quotient for, in the other
    leg. And the remainder may only ever TIGHTEN: a caller that hands a future
    timestamp gets the read's own budget back, never more than it asked for.
    """
    assert search_engine.remaining_read_ms(time.monotonic(), BUDGET_MS) <= BUDGET_MS
    # An exhausted read: the whole budget already spent.
    assert search_engine.remaining_read_ms(time.monotonic() - 10.0, BUDGET_MS) == 1
    # A budget smaller than the elapsed time is still floored, never ``0``.
    assert search_engine.remaining_read_ms(time.monotonic() - 10.0, 5) == 1
    # A future start (a caller error) cannot WIDEN the budget.
    assert search_engine.remaining_read_ms(time.monotonic() + 10.0, BUDGET_MS) == BUDGET_MS


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


def test_a_malformed_budget_reaches_the_leak_guard_and_closes_the_handle(
        monkeypatch):
    """The FALSIFIER for the leak guard (#11691 review, finding 4).

    Every case above passes a valid int or `None`, so re-hoisting the coercion
    ABOVE the guarded `try` — the exact defect the review named — reds none of
    them. The failure it protects against is not "a wrong bound": it is an
    `int()` that RAISES while the door is holding a POOLED connection, which
    escaped the handler that closes it and abandoned one borrowed socket per
    call.

    Measured before the fix: `open_record(..., statement_timeout_ms="abc")`
    raised `ValueError` out of the door with the handle still open.
    """
    import tortoise.record_writer as rw

    state: dict = {"statements": [], "closed": 0}

    class _Tracking(_FakeConn):
        def close(self):
            state["closed"] += 1

    monkeypatch.setattr(rw, "_default_connect",
                        lambda dsn, _s=state: _Tracking(_s), raising=True)

    with pytest.raises(record_serve.RecordUnavailable):
        record_serve.open_record("postgresql://localhost/record",
                                 statement_timeout_ms="abc")
    assert state["closed"] == 1, (
        "the door abandoned a POOLED connection: a malformed budget escaped "
        "before the guarded `try`, so the handler that closes the handle never "
        "ran")
    assert state["statements"] == [], (
        f"a statement ran on an unbounded handle: {state['statements']}")


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
                        "timeout_ms": BUDGET_MS,
                        # #13610: the coverage DENOMINATOR, forwarded from the
                        # projection the read already holds. It is `None` here
                        # because this double has none — and `None` is the SAFE
                        # answer (the gated labels decline, the graph keeps the
                        # read), never a silent "verified".
                        "graph_population": None}, seen[-1]
    # Omitted -> None, which leaves the door's own bound in place.
    assert sdk_mod._record_vector_searcher(_WithTenant())
    assert seen[-1]["timeout_ms"] is None, seen[-1]


def test_every_construction_site_passes_a_budget():
    """All THREE `_record_vector_searcher(self, …)` sites in `sdk.py`.

    A site that drifts back to the bare call is a read whose record leg is on
    the door's 15 s again, and nothing at runtime would say so — the leg would
    look armed and healthy while it quietly outlived the read.

    ⛔ THE VALUE, NOT THE SPELLING (#11691 second review, finding 1). This pin
    used to reject only an ABSENT keyword and an explicit `None` constant, so
    `timeout_ms=15000` at any site — or a mutation of `_budget_ms =
    VECTOR_LEG_BUDGET_MS` to `_budget_ms = 15000` — left every arm in this file
    GREEN. Measured at `f0da22b90f`: with `timeout_ms=15000` at the
    `_cross_lens_pairs` site (`sdk.py:35512`), all 12 tests here passed. The
    sibling pins cannot see it either: `test_the_dense_pass_default_…` pins a
    different function's DEFAULT, `test_the_expansion_pass_…` pins the OTHER
    end of the budget, and `test_no_leg_in_the_fused_read_…` pins a different
    expression shape (`int(_elevated_timeout_ms or X)`). So the pin now follows
    the value one hop: every site must reach `VECTOR_LEG_BUDGET_MS`.
    """
    source = pathlib.Path(sdk_module_file()).read_text()
    tree = ast.parse(source)
    sites = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_record_vector_searcher"):
            sites.append(node)
    assert len(sites) == 3, (
        f"expected the three known construction sites, found {len(sites)} — "
        f"a new one must pass the read's budget too")
    # The values the sites bind are LOCALS, so this pin needs one hop of
    # resolution or it grades the spelling instead of the value (see the
    # measured counter-example in this test's docstring).
    assigned = _single_assignments(tree)
    for node in sites:
        keyword = next((kw for kw in node.keywords if kw.arg == "timeout_ms"), None)
        assert keyword is not None, (
            f"the call at sdk.py:{node.lineno} does not bind the read's budget, "
            f"so its record leg keeps the door's {DOOR_MS} ms bound")
        # ⛔ PRESENCE IS NOT BINDING (#11691 review F6): `timeout_ms=None` asks
        # the door for its own bound — the 15 s path `_record_vector_searcher`
        # documents as "kept for the places that do not go through a bounded
        # read" — so a site that names the keyword and passes `None` (or an
        # `or VECTOR_LEG_BUDGET_MS` expression whose left side is always None)
        # would satisfy a presence-only pin while the leg is unbounded again.
        bound = next(kw.value for kw in node.keywords if kw.arg == "timeout_ms")
        # ⛔ …AND A NUMBER IS NOT BINDING EITHER (second review, finding 1).
        # Measured: with `timeout_ms=15000` at the `_cross_lens_pairs` site,
        # every arm in this file stayed GREEN — the old guard rejected only the
        # `None` constant. A literal is precisely the defect (the hardcoded leg
        # bound), so reject the whole `Constant` shape rather than enumerating
        # which constants are bad.
        assert not isinstance(bound, ast.Constant), (
            f"the call at sdk.py:{node.lineno} binds `timeout_ms=` a literal "
            f"({ast.unparse(bound)}).  A `None` leaves the record leg on the "
            f"door's {DOOR_MS} ms bound and a NUMBER is the hardcoded leg this "
            f"PR removed — a literal is never the read's budget")
        assert _binds_the_read_budget(bound, assigned, tree), (
            f"the call at sdk.py:{node.lineno} binds `timeout_ms=` "
            f"{ast.unparse(bound)}, which does not reach `VECTOR_LEG_BUDGET_MS` "
            f"directly or through the single assignment of the name it uses — "
            f"so a mutation of that assignment to a number silently unbounds "
            f"this leg while every other arm stays green (measured)")


def _single_assignments(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """Every `<name> = <value>` / `<name>: <ann> = <value>` value in a module.

    The one hop the construction-site pin needs: the sites pass LOCALS
    (`_budget_ms`, `_leg_budget_ms`), so a pin that only reads the call's own
    arguments cannot see the assignment those locals come from — and mutating
    `_budget_ms = VECTOR_LEG_BUDGET_MS` to `_budget_ms = 15000` is the same
    defect one level up.
    """
    out: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(node.value)
        elif (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None):
            out.setdefault(node.target.id, []).append(node.value)
    return out


def _param_default_names_the_budget(tree: ast.Module, name: str) -> bool:
    """Is `name` some function's parameter, defaulting to the constant?

    The third construction site passes its OWN parameter (`timeout_ms=timeout_ms`
    inside `_entity_key_dense_pass`), so its value is the CALLER's and no
    module-level assignment to resolve. That shape's bound is pinned by
    `test_the_dense_pass_default_is_the_one_budget_not_a_literal` (the default)
    and by `test_the_expansion_pass_binds_the_constant_not_a_literal` (the call
    site), so this pin accepts it only when that default really names the
    constant — the short-circuit is earned, not assumed.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Positional defaults align to the TAIL of `args`; kwonly defaults align
        # 1:1 with `kwonlyargs` and may be `None` ("required"). Zipping them in
        # one list is how a pin silently anchors on the wrong parameter.
        positional = node.args.args[len(node.args.args) - len(node.args.defaults):]
        pairs: list[tuple[ast.arg, ast.expr | None]] = [
            *zip(positional, node.args.defaults, strict=False),
            *zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False),
        ]
        for param, default in pairs:
            if (param.arg == name and default is not None
                    and _names_the_budget(default)):
                return True
    return False


def _binds_the_read_budget(
        expr: ast.expr,
        assigned: dict[str, list[ast.expr]],
        tree: ast.Module) -> bool:
    """Does this bound reach `VECTOR_LEG_BUDGET_MS` — directly, or one hop?

    DIRECT: the expression reads the constant (`_names_the_budget`).
    ONE HOP: it mentions a name whose EVERY assignment in `sdk.py` names the
    constant (`_budget_ms = VECTOR_LEG_BUDGET_MS`,
    `_leg_budget_ms = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)`).
    ALL, not ANY: a name assigned twice — once to the constant and once to a
    number — is still a site that can run on the number, so `any` would be the
    wrong quantifier here.
    PARAMETER: a name that is a function parameter whose default names the
    constant (see `_param_default_names_the_budget`).

    Anything else — a literal (rejected earlier), an attribute of another
    module (`record_serve.RECORD_STATEMENT_TIMEOUT_MS`), a name with no
    assignment — is FALSE, which is what makes a mutation of the assignment
    RED instead of invisible.
    """
    if _names_the_budget(expr):
        return True
    for node in ast.walk(expr):
        if not isinstance(node, ast.Name):
            continue
        values = assigned.get(node.id)
        if values and all(_names_the_budget(value) for value in values):
            return True
        if _param_default_names_the_budget(tree, node.id):
            return True
    return False


#: The budgets a fused-read call site may fall back to when the caller gave
#: none, and WHY that statement is allowed a number other than the read's own.
#:
#: `VECTOR_LEG_BUDGET_MS` is the read's: every leg the fused read JOINS runs on
#: it, because `degradation_chain` waits on its workers' exit, so a leg on
#: another budget is a leg the cap cannot bound.
#:
#: ⛔ A SECOND MEMBER IS ADMITTED ONLY FOR A STATEMENT THAT IS NOT JOINED
#: (`#13302` review F1). `tortoise/ended.py`'s `ENDED_SCAN_BUDGET_MS` bounds the
#: family-wide `MATCH … OPTIONAL MATCH … collect() … ORDER BY … LIMIT` that
#: `tortoise_fts_query`'s ended arm issues, and that arm RETURNS before the
#: retrieval machinery — so the hazard the read's budget exists to prevent (the
#: read JOINS a straggler, and the caller pays its latency) cannot reach it:
#: there is no sibling to straggle against, and the scan is bounded on its own
#: (`GRAPH.QUERY … TIMEOUT <ms>` terminates the statement). What 500 ms DID do
#: there is degrade the population the arm exists for to an empty answer
#: (measured: 50k-ended points returned `[]` + `incomplete` at a 500 ms bound;
#: 100k ended points cost 1.2 s unbounded). The 10 000 ms is the vendor's own
#: general-query default (`ConnectionConfig.query_timeout_ms = 10_000`, FalkorDB
#: GraphRAG), not a guess.
#:
#: The register is what ADMITS a fallback, so it cannot be widened by an edit
#: alone: the scan below resolves every member from the module that owns it.
#:
#: ONE ENTRY PER budget a fused-read fallback may name:
#: ``(owning module, why it is admitted, the ONE site it is admitted for)``.
#:
#: ⛔ THE THIRD FIELD IS THE POINT (#13302 review F1). A `None` site means "the
#: read's own budget, admitted at every site" — that is `VECTOR_LEG_BUDGET_MS`,
#: the one home for the number, and a fallback naming it anywhere is the fix, not
#: a hole. Any OTHER member is admitted at exactly one
#: ``(enclosing function, assignment target)`` and is REJECTED everywhere else:
#: the admission is keyed on the STATEMENT, not on the name.
#:
#: The first version of this register keyed on the name alone, and that was
#: measured as a hole: retargeting the bm25 arm — a leg the fused read JOINS,
#: whose latency the caller pays — from the read's 500 ms budget to
#: `ENDED_SCAN_BUDGET_MS` (10 000 ms) left the whole suite green. A name-scoped
#: admission in a site-scoped guard protects only the site that already has its
#: own pin.
DECLARED_LEG_BUDGETS: dict[str, tuple[str, str, tuple[str, str] | None]] = {
    "VECTOR_LEG_BUDGET_MS": (
        "tortoise.search_engine",
        "the fused read's own budget — every leg it JOINS runs on it",
        None),
    "ENDED_SCAN_BUDGET_MS": (
        "tortoise.ended",
        "the ended-scope scan, which returns BEFORE the retrieval machinery, so "
        "it is not a leg the fused read joins (#13302 review F1)",
        # `if ended_scope:` inside `tortoise_fts_query`, the arm that RETURNS
        # before the retrieval machinery runs. Pinned to the statement, so the
        # same name at any joined site is an offender rather than a pass.
        ("tortoise_fts_query", "_ended_bound")),
}


def _declared_budget_names(node: ast.expr) -> set[str]:
    """Which `DECLARED_LEG_BUDGETS` names does the expression READ?"""
    names = {n.id for n in ast.walk(node)
             if isinstance(n, ast.Name) and n.id in DECLARED_LEG_BUDGETS}
    names |= {n.attr for n in ast.walk(node)
              if isinstance(n, ast.Attribute) and n.attr in DECLARED_LEG_BUDGETS}
    return names


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return ""


def _assignment_statements(tree: ast.AST) -> list[tuple[tuple[str, str], ast.stmt]]:
    """Every simple assignment, as `((function, target), statement)`."""
    parents = _parent_map(tree)
    out: list[tuple[tuple[str, str], ast.stmt]] = []
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        else:
            continue
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if len(names) != 1:
            continue
        out.append(((_enclosing_function(stmt, parents), names[0]), stmt))
    return out


def _enclosing_sites(tree: ast.AST) -> dict[int, tuple[str, str]]:
    """Map every node to `(enclosing function, nearest simple assignment)`.

    A site is how the register keys an admission: `_ended_bound = int(
    _elevated_timeout_ms or ENDED_SCAN_BUDGET_MS)` is identified by the pair
    `("tortoise_fts_query", "_ended_bound")`, and a keyword argument inside a
    bare call — every joined leg — maps to its own enclosing assignment, or to
    `("<function>", "")` when the statement binds nothing.
    """
    sites: dict[int, tuple[str, str]] = {}
    for site, stmt in _assignment_statements(tree):
        for node in ast.walk(stmt):
            sites[id(node)] = site
    return sites


def _budget_fallback_offenders(source: str) -> list[str]:
    """Every `int(_elevated_timeout_ms or …)` that has no legal budget fallback.

    A PURE FUNCTION OF THE SOURCE on purpose: the repo-wide pin below feeds it
    `sdk.py`, and the mutation test feeds it synthetic trees, so the guard's own
    behaviour is pinned by a test that genuinely reds when the guard is
    loosened — instead of being asserted only through the one file it scans.
    """
    tree = ast.parse(source)
    sites = _enclosing_sites(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        # the shape `int(_elevated_timeout_ms or <X>)` — compare the AST, so a
        # renamed local or a reformatted line cannot slip past a text search.
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "int" and len(node.args) == 1):
            continue
        inner = node.args[0]
        if not (isinstance(inner, ast.BoolOp)
                and isinstance(inner.op, ast.Or)
                and len(inner.values) == 2
                and isinstance(inner.values[0], ast.Name)
                and inner.values[0].id == "_elevated_timeout_ms"):
            continue
        fallback = inner.values[1]
        where = f"line {getattr(node, 'lineno', '?')}: {ast.unparse(node)}"
        names = _declared_budget_names(fallback)
        if not names:
            offenders.append(
                f"{where} — the budget's fallback names no budget in "
                f"DECLARED_LEG_BUDGETS, so this leg keeps its own number")
            continue
        site = sites.get(id(node), ("", ""))
        binds = ".".join(p for p in site if p) or "<no binding>"
        for name in sorted(names):
            permitted = DECLARED_LEG_BUDGETS[name][2]
            if permitted is not None and site != permitted:
                offenders.append(
                    f"{where} — `{name}` is admitted only for the "
                    f"`{permitted[0]}::{permitted[1]}` assignment, and this "
                    f"statement binds `{binds}`; that leg is on another budget "
                    f"the fused read cannot cap (#11691 / #13302 review F1)")
    return offenders


def _names_the_budget(node: ast.expr) -> bool:
    """Does the expression READ `VECTOR_LEG_BUDGET_MS` anywhere?

    A `Constant` check is not enough, and that is measured, not assumed: the
    first version of these pins asserted "not a literal", and the call site
    `int(_elevated_timeout_ms or 500)` — a `Call` wrapping a `BinOp` whose right
    side is the literal — stayed GREEN under exactly the mutation it exists to
    catch (#11691 review F4). The pin has to name the thing it wants.
    """
    return any((isinstance(n, ast.Name) and n.id == "VECTOR_LEG_BUDGET_MS")
               or (isinstance(n, ast.Attribute) and n.attr == "VECTOR_LEG_BUDGET_MS")
               for n in ast.walk(node))


def test_the_dense_pass_default_is_the_one_budget_not_a_literal():
    """`_entity_key_dense_pass`'s `timeout_ms` default NAMES the constant.

    The default and `degradation_chain`'s fallback are the SAME number, which is
    exactly why nothing would go red while the default stopped tracking the budget
    the moment somebody moved it. Measured:
    reverting the default to the literal left every other arm in this file
    passing (the whole point of #11691 is "ONE home for the number").
    """
    source = pathlib.Path(sdk_module_file()).read_text()
    defaults: list[ast.expr] = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.FunctionDef)
                and node.name == "_entity_key_dense_pass"):
            params = node.args.kwonlyargs + node.args.args
            for param, default in zip(params[len(params) - len(node.args.defaults or
                                                              []):],
                                      node.args.defaults or [], strict=False):
                if param.arg == "timeout_ms":
                    defaults.append(default)
            for param, default in zip(node.args.kwonlyargs,
                                      node.args.kw_defaults, strict=False):
                if param.arg == "timeout_ms" and default is not None:
                    defaults.append(default)
    assert defaults, (
        "no `timeout_ms` default found on `_entity_key_dense_pass` — the pin's "
        "own anchor moved, so re-derive it rather than deleting this test")
    for default in defaults:
        assert _names_the_budget(default), (
            "`_entity_key_dense_pass`'s `timeout_ms` default does not name "
            f"`VECTOR_LEG_BUDGET_MS` ({ast.unparse(default)}) — the constant is "
            "the one home for the number (#11691)")

    # …and the name it should carry really is the budget, not a lookalike.
    from tortoise import sdk as sdk_mod
    assert (sdk_mod.VECTOR_LEG_BUDGET_MS
            is search_engine.VECTOR_LEG_BUDGET_MS), (
        "`sdk.VECTOR_LEG_BUDGET_MS` is not the object `search_engine` defines — "
        "a second home for the number has appeared")


def test_the_expansion_pass_binds_the_constant_not_a_literal():
    """The call site that hands the dense pass its budget names the constant.

    `test_every_construction_site_passes_a_budget` pins the record leg's three
    sites; this pins the OTHER end of the same budget — the expansion pass's own
    `timeout_ms=`, which was a literal `500` while the constant lived one module
    away.
    """
    tree = ast.parse(pathlib.Path(sdk_module_file()).read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_entity_key_dense_pass"]
    assert len(calls) == 1, (
        f"expected ONE `_entity_key_dense_pass` call site, found {len(calls)} — "
        "a new one must name the constant too")
    bound = next(kw.value for kw in calls[0].keywords if kw.arg == "timeout_ms")
    assert _names_the_budget(bound), (
        f"the call at sdk.py:{calls[0].lineno} does not name "
        f"`VECTOR_LEG_BUDGET_MS` ({ast.unparse(bound)}) — a literal here leaves "
        "this leg on the old budget when the constant moves")


def test_no_leg_in_the_fused_read_carries_its_own_budget_literal():
    """REPO-WIDE: every `int(_elevated_timeout_ms or …)` has a legal fallback.

    The defect class is the LITERAL: `int(_elevated_timeout_ms or 500)` is a
    number with no home, so moving the constant splits the read's budget without
    one pin noticing. A fallback that names a DECLARED budget is the opposite of
    that — it is greppable, and the register above requires a reason for every
    member.

    ⛔ AND A DECLARED NAME IS NOT A LICENCE (#13302 review F1): a second member
    is admitted for ITS OWN STATEMENT only, so `ENDED_SCAN_BUDGET_MS` at the
    bm25 arm is an offender while the same name at `tortoise_fts_query`'s
    `_ended_bound` is the fix. Before that, the name alone bought any site off.

    `search_engine.py` states the rule this protects: "the fused read JOINS its
    workers, so a leg on a different budget is a leg the cap cannot bound." The
    per-site pins above cannot see the rest of them — measured at this head, the
    two site-specific pins left FOUR copies of the same expression in the same
    method (the bm25 leg, the structural leg, the readiness probe and the
    residency read), so moving the constant would have silently split the read's
    budget (#11691 review, finding 3).

    A scan, not a count: the number of legs is expected to grow, so this asserts
    about EVERY match rather than pinning how many there are. The scan itself is
    `_budget_fallback_offenders`, pinned in both directions by the mutation test
    below; this test is the same function applied to the real module.
    """
    source = pathlib.Path(sdk_module_file()).read_text()
    offenders = _budget_fallback_offenders(source)
    assert not offenders, (
        "legs of the fused read carry their own budget literal:\n  "
        + "\n  ".join(offenders))

    # POSITIVE CONTROL — the register is what admits a fallback, so every member
    # must resolve to a real budget in the module that owns it, and the read's
    # own budget must still be in it. A typo'd or invented entry buys nothing.
    assert "VECTOR_LEG_BUDGET_MS" in DECLARED_LEG_BUDGETS, (
        "the read's own budget was dropped from DECLARED_LEG_BUDGETS — the "
        "register is not a place to retire it")
    for name, (module_name, why, _site) in DECLARED_LEG_BUDGETS.items():
        declared = getattr(importlib.import_module(module_name), name, None)
        assert isinstance(declared, int) and declared > 0, (
            f"DECLARED_LEG_BUDGETS admits {name!r} via {module_name}, which does "
            f"not define it as a positive int ({declared!r}) — a register entry "
            f"that resolves to nothing admits nothing")
        assert why.strip(), f"{name} is admitted with no reason recorded"

    # POSITIVE CONTROL — the site-scoped admissions are exactly one, and the
    # exemption is USED (an exemption that pins no live statement is dormant,
    # and a second statement with the same target would ride it).
    exempt = {name: spec[2] for name, spec in DECLARED_LEG_BUDGETS.items()
              if spec[2] is not None}
    assert set(exempt) == {"ENDED_SCAN_BUDGET_MS"}, (
        f"the site-scoped admissions are {sorted(exempt)} — re-derive this "
        "control before widening it (#13302 review F1)")
    tree = ast.parse(source)
    used = {site for site, _stmt in _assignment_statements(tree)}
    counts: dict[tuple[str, str], int] = {}
    for site, _stmt in _assignment_statements(tree):
        counts[site] = counts.get(site, 0) + 1
    for name, site in exempt.items():
        assert site in used, (
            f"{name} is admitted for the site {site}, which binds no statement "
            f"in sdk.py — a dormant exemption admits nothing")
        assert counts.get(site) == 1, (
            f"the exempting binding {site} is bound {counts.get(site, 0)} times "
            f"in sdk.py — the admission is keyed on ONE statement")


def test_a_declared_budget_is_admitted_only_at_the_site_that_earns_it():
    """F1: the register's admission is SITE-scoped, not name-scoped.

    Driven against synthetic trees, because this pins the GUARD rather than the
    file it scans: the measured pre-fix behaviour was "retarget any joined leg's
    fallback to `ENDED_SCAN_BUDGET_MS` and nothing reds", and a test that only
    reads `sdk.py` can never show that. Each mutant below is exactly one edit to
    a tree that otherwise satisfies the contract.
    """
    base = textwrap.dedent(
        '''\
        def tortoise_fts_query(self):
            _probe = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)
            _residency = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)
            _bm25 = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)
            _structural = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)
            _expansion = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)
            _ended_bound = int(_elevated_timeout_ms or ENDED_SCAN_BUDGET_MS)
        ''')

    # CONTRACT HOLDS — the ended arm's own statement is admitted, and every joined
    # leg still names the read's budget.
    assert _budget_fallback_offenders(base) == [], _budget_fallback_offenders(base)

    # THE FINDING, one mutant per joined leg: retarget THIS leg to the ended
    # scan's 10 000 ms. Green everywhere before the fix.
    joined = ("_probe", "_residency", "_bm25", "_structural", "_expansion")
    for binding in joined:
        mutant = base.replace(
            f"{binding} = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)",
            f"{binding} = int(_elevated_timeout_ms or ENDED_SCAN_BUDGET_MS)")
        assert mutant != base, binding
        offenders = _budget_fallback_offenders(mutant)
        assert len(offenders) == 1 and binding in offenders[0], (binding, offenders)
        assert "ENDED_SCAN_BUDGET_MS" in offenders[0], offenders

    # …and the exemption cannot be carried to another statement by renaming its
    # target, so the admission is a fact about the code, not about a comment.
    renamed = base.replace("_ended_bound = int(_elevated_timeout_ms or "
                           "ENDED_SCAN_BUDGET_MS)",
                           "_scope_bound = int(_elevated_timeout_ms or "
                           "ENDED_SCAN_BUDGET_MS)")
    assert renamed != base
    assert len(_budget_fallback_offenders(renamed)) == 1, (
        _budget_fallback_offenders(renamed))

    # …and a statement in ANOTHER function is not the admitted one either.
    moved = base.replace("def tortoise_fts_query(self):",
                         "def some_other_read(self):")
    assert len(_budget_fallback_offenders(moved)) == 1, (
        _budget_fallback_offenders(moved))

    # The original defect class still reds: a literal at a joined site.
    literal = base.replace("_bm25 = int(_elevated_timeout_ms or "
                           "VECTOR_LEG_BUDGET_MS)",
                           "_bm25 = int(_elevated_timeout_ms or 500)")
    assert literal != base
    offenders = _budget_fallback_offenders(literal)
    assert len(offenders) == 1 and "names no budget" in offenders[0], offenders


def sdk_module_file() -> str:
    from tortoise import sdk as sdk_mod

    return sdk_mod.__file__


# ── deliverable 2: the numbers are arithmetic on committed files ───────────

def test_the_committed_measurement_prices_itself_from_pricing_json():
    """Every dollar in the evidence ARTIFACT re-derives from `product/pricing.json`.

    ⛔ The same discipline `docs/evidence/9558-record-write-cost/README.md` states:
    *"every number here is arithmetic on a committed file"*. A figure typed into
    the artifact instead of derived from `measurement.json` × `product/pricing.json`
    is a number nobody can correct — and the rates are the owner's, so a hand-copy
    goes stale silently the day a rate moves. This pin recomputes the whole money
    half of `measurement.json` from the two JSON files: the storage principal at
    BOTH published rates, the per-tier flow at the volumes `pricing.json` itself
    lists, and the bytes the production figure is scaled from.

    ⚠️ WHAT THIS DOES NOT READ, stated rather than implied (PR #13133 review P2):
    `README.md`'s PROSE. The docstring used to say *"every dollar in the evidence
    doc"*, which was wider than the read — the test opens `measurement.json` and
    `pricing.json` and never `README.md`, so a figure retyped into the prose (the
    `$0.029/month` / `+3.987 ms` / `39.07 MiB` family) can drift and this stays
    green. Pinning prose figures means parsing prose: the shapes there are
    formatted (`$0.0089`) rather than round-tripped, so a regex arm would pin the
    formatting as much as the value. The narrower claim is the honest one; the
    prose is derived from the artifact by the harness above, and a reader is told
    which half is machine-checked.
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
    #     THREE components, because the control shape carries a primary key the
    #     index under test does not (#11691 review F2) — the split is reported,
    #     never folded into the HNSW figure.
    per_1k = (art["deltas"]["hnsw_bytes_per_1000_writes"]
              + art["deltas"]["primary_key_bytes_per_1000_writes"]
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


def test_the_index_split_re_derives_from_the_shapes_that_were_measured():
    """The HNSW/pkey split is arithmetic on the artifact — and neither is 0.

    A SUM is not an invariant (#11691 review, finding 1): the money pin above
    adds the two fields, so SWAPPING them, or a `.get(index, 0)` that reads 0.0
    and dumps the whole index cost into the field named "primary key", leaves it
    green. Measured on the committed artifact before this pin: with the HNSW
    name changed to a typo the split became 0.0 / 2,129,920.0 and every existing
    arm still passed.

    So this re-derives each half from the measured shapes, and pins that both
    halves are non-zero — the direction the fail-open default moved them.
    """
    import json
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    art = json.loads((repo / "docs/evidence/11691-vector-leg-budget/"
                             "measurement.json").read_text())
    deltas = art["deltas"]
    hnsw = deltas["hnsw_bytes_per_1000_writes"]
    pkey = deltas["primary_key_bytes_per_1000_writes"]

    assert hnsw > 0, (
        "the HNSW figure is 0 — the index-byte read found no HNSW index, and "
        f"its cost has been relabelled into the primary key: {deltas}")
    assert pkey > 0, (
        "the primary-key figure is 0 — no key growth was attributed, so the "
        f"split is not measuring what it names: {deltas}")

    # The split's own denominator is NOT `scale.index_bytes`: that is the
    # ABSOLUTE size at 20,000 rows, while these two fields are the GROWTH per
    # 1,000 writes measured on the interleaved delta. Measured attempt at that
    # equation: 2,129,920/1k × 20 = 42,598,400 vs `index_bytes` 42,074,112 — a
    # 1.25% gap, i.e. the two numbers are different quantities and an equality
    # pin here would be a fabricated invariant. What IS checkable from the
    # committed artifact is the SHAPE of the split (see the assertions above)
    # and that it names the index set that was actually measured.
    full = next(s for s in art["scale"]["indexes"] if "hnsw" in s)
    assert set(art["scale"]["indexes"]) == {full, "point_pkey"}, (
        f"the measured shape's index set changed: {sorted(art['scale']['indexes'])}")
    assert hnsw > pkey, (
        f"the HNSW index ({hnsw}/1k) is not larger than the key ({pkey}/1k) — "
        "on a 384-d vector index that inversion means the halves are swapped")

    # …and the corpus figure the doc quotes is a FIELD, of the sample measured.
    corpus = art["corpus"]
    assert corpus["rows"] == 1200, (
        f"the corpus field describes {corpus['rows']} rows, not the 1,200 the "
        "write phase consumes")
    assert 3_500 < corpus["text_bytes_per_row_mean"] < 3_700, corpus

    # ── the two "of which" rows of the volume table are a DERIVED SPLIT ────
    # (second review, finding 2). The README publishes 69.0 MB / 1.9 MB, which
    # are NOT fields — they are `production_index_bytes` apportioned by the
    # measured shape's own index sizes, and the README now says so with the
    # formula and its inputs. What this arm adds is that the stated derivation
    # is the one the artifact supports: the measured index set must apportion
    # `scale.index_bytes` EXACTLY (no index left over, none invented), so the
    # split sums back to the aggregate rather than to something near it — the
    # shape that makes a published per-index figure checkable at all.
    scale_indexes = art["scale"]["indexes"]
    pv_index_bytes = art["production_volume"]["production_index_bytes"]
    assert sum(scale_indexes.values()) == art["scale"]["index_bytes"], (
        "the measured index set does not add up to `scale.index_bytes`, so the "
        "README's apportionment formula does not describe the artifact: "
        f"{scale_indexes} vs {art['scale']['index_bytes']}")
    split = {name: round(pv_index_bytes * size
                         / art["scale"]["index_bytes"])
             for name, size in scale_indexes.items()}
    assert round(split[full] / 1e6, 1) == 69.0, split
    assert round(split["point_pkey"] / 1e6, 1) == 1.9, split
    assert sum(split.values()) == pv_index_bytes, split


# ── the measurement harness must not empty the database it measures ────────
#
# `tools/record_vector_write_cost_11691.py` builds its three stand-in shapes with
# `DROP TABLE IF EXISTS public.{point,source,event} CASCADE`, and its `--dsn`
# option is documented as "a Postgres you already have" — so the obvious way to
# reproduce "current production volume" is the record's own DSN, which would
# destroy the record's three tables and leave stand-ins of the same name. The
# guard is DATA-driven rather than name-driven (an empty database has nothing to
# lose; no name makes a populated table safe to drop), and it must run BEFORE the
# first DDL — a check that runs after the DROP it guards protects nothing.

def _harness_module():
    """Load the standalone harness by path — it is a script, not an importable module."""
    import importlib.util
    import pathlib

    path = (pathlib.Path(__file__).resolve().parent.parent
            / "tools/record_vector_write_cost_11691.py")
    spec = importlib.util.spec_from_file_location("_write_cost_11691", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeDatabase:
    """A cursor that answers the guard's preflight queries about `tables`.

    Only the four statements the preflight may issue are answered; anything else
    is recorded and returns `None`, which is exactly what a `DROP TABLE` needs to
    be visible as "the guard did NOT stop the DDL".
    """

    def __init__(self, tables: dict[str, int], database: str = "tortoise_record"):
        self.tables = tables
        self.database = database
        self.statements: list[str] = []
        self._row = None

    def execute(self, sql, params=None):
        self.statements.append(sql)
        text = " ".join(sql.split()).upper()
        self._row = None
        if text.startswith("SELECT CURRENT_DATABASE()"):
            self._row = (self.database,)
        elif text.startswith("SELECT TO_REGCLASS("):
            name = str(params[0]).split(".", 1)[-1]
            self._row = (name in self.tables,)
        elif text.startswith("SELECT EXISTS (SELECT 1 FROM PUBLIC."):
            name = text.split("PUBLIC.", 1)[1].split()[0].rstrip(")").lower()
            self._row = (self.tables.get(name, 0) > 0,)
        elif text.startswith("SELECT COUNT(*) FROM PUBLIC."):
            name = text.split("PUBLIC.", 1)[1].split()[0].rstrip(")").lower()
            self._row = (self.tables[name],)

    def fetchone(self):
        return self._row

    @property
    def ddl(self) -> list[str]:
        return [s for s in self.statements
                if s.lstrip().upper().startswith(("DROP", "CREATE", "ALTER"))]


def test_the_harness_refuses_to_drop_stand_ins_over_a_populated_database():
    """The preflight stops `build_schema` BEFORE its first DROP, naming the data.

    Non-vacuity: this is the reviewer's exact scenario — `--dsn` pointing at the
    record (33,671 live `public.point` rows). The pre-fix `build_schema` runs
    `DROP TABLE IF EXISTS public.point CASCADE` against it and this arm reds on
    the missing refusal. The second assertion is the half that survives a
    "guard added in the wrong place" fix: a check that runs after the DROP still
    raises, so only the no-DDL assertion proves it protects anything.
    """
    harness = _harness_module()
    cur = _FakeDatabase({"point": 33_671, "source": 1_204, "event": 55_010})

    with pytest.raises(SystemExit) as refused:
        harness.build_schema(cur)

    message = str(refused.value)
    assert "REFUSING" in message, message
    assert "tortoise_record" in message, (
        f"the refusal does not name the database it refused: {message}")
    assert "public.point" in message and "33,671" in message, (
        f"the refusal does not name the populated table and its row count, so an "
        f"operator cannot see WHAT was about to be destroyed: {message}")
    assert not cur.ddl, (
        "the guard ran AFTER DDL — these statements executed before the refusal, "
        f"so the tables were already dropped: {cur.ddl}")


def test_the_harness_accepts_an_empty_target_and_a_deliberate_override():
    """An empty database needs no ceremony; a non-empty one needs the explicit flag.

    Both halves matter: a guard that also refuses the empty throwaway container
    would break the documented happy path, and a guard with no override refuses a
    bench replica the operator has chosen to sacrifice. The override is asserted
    to still reach the DDL, so it is a real escape rather than a silent no-op.
    """
    harness = _harness_module()

    empty = _FakeDatabase({})
    harness._refuse_destructive_target(empty)  # must not raise

    occupied = _FakeDatabase({"point": 5})
    with pytest.raises(SystemExit):
        harness._refuse_destructive_target(occupied)

    harness._refuse_destructive_target(occupied, allow_non_empty=True)

    # …and the escape must be REAL, not a silent no-op: with it, `build_schema`
    # reaches its DDL (which is the destruction the operator just authorised).
    overridden = _FakeDatabase({"point": 5})
    harness.build_schema(overridden, allow_non_empty=True)  # must not raise
    assert overridden.ddl, (
        "--allow-non-empty-dsn did not reach the DDL, so it is not an override")


def _derived_stand_in_widths(ddl: list[str]) -> dict[str, int]:
    """The width each stand-in ACTUALLY gets, parsed from the executed DDL.

    Deliberately derived rather than restated: `STAND_IN_COLUMNS` exists because
    the operator-facing refusal carried a remembered number twice (#11691 review,
    P2), so the pin must not carry one either.
    """
    widths: dict[str, int] = {}
    for statement in ddl:
        text = " ".join(statement.split())
        created = re.fullmatch(
            r"CREATE TABLE public\.(point|source|event) \((.*)\)", text, re.S)
        if created:
            depth = 0
            columns = 1
            for char in created.group(2):
                depth += {"(": 1, ")": -1}.get(char, 0)
                columns += int(char == "," and depth == 0)
            widths[created.group(1)] = columns
            continue
        altered = re.fullmatch(
            r"ALTER TABLE public\.(point|source|event) (.*)", text, re.S)
        if altered:
            widths[altered.group(1)] = (widths.get(altered.group(1), 0)
                                       + altered.group(2).count("ADD COLUMN"))
    return widths


def test_the_destructive_warning_names_the_width_the_ddl_actually_builds():
    """The refusal's width is DERIVED from the DDL, not remembered (#11691 review, P2).

    Non-vacuity: the pre-fix texts answered this one question three ways — the
    docstring said "four-column", the `SystemExit` and the artifact README said
    "eight-column", and the DDL builds 11 columns in `public.point` and 7 in
    `public.source`/`public.event`. Two different wrong numbers in operator-facing
    text about a DESTRUCTIVE operation is the signal that the number was never
    derived, so this arm re-derives the same mapping from the statements
    `build_schema` actually executes and compares it with the one the refusal
    formats from: add a column to the base CREATE TABLE, or to the migration's
    `ALTER`, and this reds until the warning says the new width.
    """
    harness = _harness_module()
    cur = _FakeDatabase({})
    harness.build_schema(cur, allow_non_empty=True)
    derived = _derived_stand_in_widths(cur.ddl)

    assert derived, f"no stand-in DDL was captured, so nothing was derived: {cur.ddl}"
    assert derived == harness.STAND_IN_COLUMNS, (
        f"the refusal quotes {harness.STAND_IN_COLUMNS} but the DDL it warns about "
        f"builds {derived}")

    # …and what an operator READS carries those numbers: the constant is not the
    # text, so a refusal that kept a literal would still be wrong on the page.
    occupied = _FakeDatabase({"point": 5})
    with pytest.raises(SystemExit) as refused:
        harness._refuse_destructive_target(occupied)
    message = str(refused.value)
    for table, width in derived.items():
        assert f"{width} columns in public.{table}" in message, (
            f"the refusal does not state the derived width of public.{table} "
            f"({width} columns): {message}")


# ── the READINESS gate is bound too (#13331 review F3) ─────────────────────
#
# The gate that keeps a lagging tenant off the dense leg runs on the REQUEST's
# thread, inside `record_vector.searcher()`, BEFORE any leg budget exists. It
# opened the record with no `statement_timeout_ms`, so it inherited the door's
# 15 s ceiling — the exact `#11691` defect one function above the fix for it —
# and `journal_coverage` issued TWO statements under that ceiling
# (`_COVERAGE_SQL`, then `_FIRST_GAP_SQL`, whose answer readiness never reads).
# Cached 30 s per tenant, so a wedged record ate a ~30 s stall on roughly one
# request per window.
#
# The pins below are the falsifiers: delete the budget threading or re-issue the
# gap statement and they red on the APPLIED bound and on the statement set, not
# on a wall clock (a 15 s sleep would be a slow test reporting the wrong cause).


class _ReadinessCursor:
    """Answers the coverage read; records the bound and every statement issued."""

    def __init__(self, state: dict):
        self._state = state

    def execute(self, sql: str, params=None):
        self._state["statements"].append(sql)
        if "set_config('statement_timeout'" in sql:
            self._state["applied_ms"] = int(params[0])
            return None
        if "lead(seq)" in sql:
            self._state["gap_statements"] = self._state.get("gap_statements", 0) + 1
            return None
        if "journal_event" in sql:
            return None
        raise AssertionError(f"the readiness gate issued an unexpected statement: {sql}")

    def fetchone(self):
        return self._state["row"]

    def fetchall(self):
        raise AssertionError("unreachable — the readiness gate reads one row")

    def close(self):
        return None


class _ReadinessConn:
    def __init__(self, state: dict):
        self._state = state

    def cursor(self):
        return _ReadinessCursor(self._state)

    def close(self):
        return None


def _wire_readiness_door(monkeypatch, state: dict) -> None:
    """The REAL door, a fake driver — its `min(ceiling, asked)` rule stays live."""
    import tortoise.record_projection as rp
    import tortoise.record_writer as rw

    monkeypatch.setattr(rw, "_default_connect",
                        lambda dsn: _ReadinessConn(state), raising=True)
    monkeypatch.setattr(rp, "scope_connection",
                        lambda conn, org: conn.cursor(), raising=True)


def test_the_readiness_gate_is_bound_by_the_reads_budget(
        monkeypatch, armed, tmp_path):
    """`(high, present, min, within) = (2, 2, 1, 2)` against a 3-line journal."""
    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}\n{}\n")
    monkeypatch.setenv(record_vector.RECORD_JOURNAL_ENV, str(journal))

    state: dict = {"statements": [], "row": (2, 2, 1, 2)}
    _wire_readiness_door(monkeypatch, state)
    record_vector.reset_readiness_cache()
    try:
        assert record_vector.journal_ready(
            record_vector._Scope(org_id="org_11691", graph_id="graph_11691"),
            timeout_ms=BUDGET_MS) is False, (
            "a tenant whose record holds 2 of 3 journal positions was called ready")
    finally:
        record_vector.reset_readiness_cache()

    assert state.get("applied_ms") == LEG_STATEMENT_MS, (
        f"the readiness gate asked the door for {state.get('applied_ms')} ms "
        f"inside a {BUDGET_MS} ms read: the read's budget does not reach it, so "
        f"a wedged record can stall the request thread for the door's own "
        f"{DOOR_MS} ms ceiling before the leg's budget exists at all")
    assert not state.get("gap_statements"), (
        "the readiness gate issued `_FIRST_GAP_SQL`, whose answer it never "
        "reads — spending the read's budget on a fact nobody consumes")


def test_the_readiness_gate_skips_the_gap_statement_and_says_so(
        monkeypatch, armed, tmp_path):
    """An unmeasured gap is NOT a measured absence (`#5090` Q2).

    Skipping the statement is only correct if the coverage object stops claiming
    the hole edge is clean: `first_gap is None` would otherwise make
    `contiguous` answer `True` for a record with a hole in the middle.
    """
    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}\n{}\n")
    monkeypatch.setenv(record_vector.RECORD_JOURNAL_ENV, str(journal))

    state: dict = {"statements": [], "row": (3, 3, 1, 3)}
    _wire_readiness_door(monkeypatch, state)
    record_vector.reset_readiness_cache()
    captured: dict = {}

    import tortoise.record_projection as rp

    real_coverage = rp.journal_coverage

    def spy(conn, **kwargs):
        cov = real_coverage(conn, **kwargs)
        captured["cov"] = cov
        return cov

    monkeypatch.setattr(rp, "journal_coverage", spy)
    try:
        assert record_vector.journal_ready(
            record_vector._Scope(org_id="org_11691", graph_id="graph_11691"),
            timeout_ms=BUDGET_MS) is True
    finally:
        record_vector.reset_readiness_cache()

    cov = captured["cov"]
    assert cov.gap_measured is False
    assert cov.contiguous is None, (
        "a coverage reading that never looked for a hole reported `contiguous`")
    assert cov.unprojected == 0          # the aggregate question IS answered


def _column_path_statement_sites() -> int:
    """How many DATA statements the column-filtered path really issues — from SOURCE.

    ⛔ THE EXPECTED VALUE MUST NOT COME FROM THE DECLARATION THAT PRODUCES IT
    (`#13466` cycle 6, P2). `RECORD_LEG_STATEMENTS_PER_READ` is DEFINED as
    ``COLUMN_ARM_STATEMENTS + 1``, so asserting that relation back is a TAUTOLOGY
    that cannot red for any value. Measured by the cycle-6 reviewer: with the count
    set to 3 the only failing assertion was the cross-arm one, while
    "count + 1 == divisor" and the reserve loop both PASSED. The count therefore
    needs an oracle that reads the implementation, which is what this is.

    What counts, and why: `vector_search` issues the kNN read
    (``cur.execute(sql, params)``) and, on the SHORT path only, the shortfall
    ``count(*)`` probe. ``set_config`` is a session setting, not a data statement,
    so it is excluded — the same rule `test_13422`'s folded oracle applies.

    ⛔ DECLARED BOUND: a data statement added inside a HELPER is invisible here,
    exactly as it is to the folded arm's oracle. What this catches is the shape
    that actually drifted twice already — another ``cur.execute`` written inline in
    the arm — and it catches it in the direction that matters: an UNDER-divided
    arm, whose cancelled kNN hands the graph branch a floored ``1 ms``.
    """
    module = (pathlib.Path(__file__).resolve().parent.parent
              / "tortoise" / "record_vector.py")
    tree = ast.parse(module.read_text())
    vector_search = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "vector_search")
    sites = 0
    for child in ast.walk(vector_search):
        if not (isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "execute"):
            continue
        sql = ast.unparse(child.args[0]) if child.args else ""
        if "set_config" in sql:
            continue
        sites += 1
    return sites


def test_each_arm_divides_by_its_own_statement_count_plus_the_reserve():
    """⛔ THE DIVISOR IS NOT THE STATEMENT COUNT (`#13466` cycle 6, P2).

    `RECORD_LEG_STATEMENTS_PER_READ` and `FOLD_ARM_DIVISOR` are DIVISORS: the data
    statements an arm issues PLUS the one share left for the graph branch. The two
    numbers are different, and conflating them is the defect `#13466` cycle 4
    fixed — a divisor equal to the count spends the whole read and hands the graph
    branch a floored ``1 ms``.

    ⛔ THE COUNT SIDE IS MEASURED FROM SOURCE, not read back from a declaration
    derived from the divisor (`_column_path_statement_sites` below; the folded arm
    has had such an oracle since cycle 5). The relation-only version of this pin
    was a tautology and was replaced — see that oracle's docstring for the
    measurement that proved it.
    """
    column_statements = _column_path_statement_sites()
    folded_statements = record_vector.FOLD_ARM_STATEMENTS

    assert column_statements == record_vector.COLUMN_ARM_STATEMENTS, (
        f"the column path issues {column_statements} data statement(s) at its "
        f"source sites but COLUMN_ARM_STATEMENTS says "
        f"{record_vector.COLUMN_ARM_STATEMENTS} — the divisor is sized from the "
        f"declaration, so a count that drifts under it under-divides the arm")

    # The reserve is REAL, per arm: on a budget the arm spends its share for each
    # statement and still leaves the graph branch at least one share.
    for divisor, statements in (
        (record_vector.RECORD_LEG_STATEMENTS_PER_READ, column_statements),
        (record_vector.FOLD_ARM_DIVISOR, folded_statements),
    ):
        share = record_vector.record_statement_ms(BUDGET_MS, divisor)
        assert BUDGET_MS - share * statements >= share, (
            f"divisor={divisor} statements={statements} leaves the graph branch "
            f"less than one share of a {BUDGET_MS} ms read")
