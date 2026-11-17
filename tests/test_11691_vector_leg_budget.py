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
                        "timeout_ms": BUDGET_MS}, seen[-1]
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
    `_cross_lens_pairs` site (`sdk.py:35507`), all 12 tests here passed. The
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
    """REPO-WIDE: every `int(_elevated_timeout_ms or …)` names the constant.

    `search_engine.py` states the rule this protects: "the fused read JOINS its
    workers, so a leg on a different budget is a leg the cap cannot bound." The
    per-site pins above cannot see the rest of them — measured at this head, the
    two site-specific pins left FOUR copies of the same expression in the same
    method (the bm25 leg, the structural leg, the readiness probe and the
    residency read), so moving the constant would have silently split the read's
    budget (#11691 review, finding 3).

    A scan, not a count: the number of legs is expected to grow, so this asserts
    about EVERY match rather than pinning how many there are.
    """
    source = pathlib.Path(sdk_module_file()).read_text()
    tree = ast.parse(source)
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
        if not _names_the_budget(inner.values[1]):
            offenders.append(
                f"sdk.py:{node.lineno} binds {ast.unparse(node)} — the budget's "
                f"fallback is a literal, so this leg keeps its own number")
    assert not offenders, (
        "legs of the fused read carry their own budget literal:\n  "
        + "\n  ".join(offenders))


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
