"""#13722 — a projection open must not scan the tenant's whole graph, and the
hosted per-request round-trip count is bounded so it cannot grow unnoticed.

WHAT WAS WRONG. `_probe_ok()` — the reachability gate every projection
construction runs (`_auto_health_recover`, before `_ensure_indexes`) — issued
``MATCH (n) RETURN count(n) LIMIT 1``. That is an UNBOUNDED node scan:
``count()`` is an aggregation, so ``LIMIT 1`` bounds the returned ROW and not
the work. Its value was then **discarded** — the probe's only outputs are a
boolean and ``_probe_error``, and the 0-node ("lost graph") decision belongs to
``recover_from_log``, which counts nodes itself.

WHY IT MATTERED ENOUGH TO PIN. The hosted data plane does not open one
projection per process: in URI mode `_make_sdk` returns a FRESH ``TortoiseSDK``
per request, so every request pays a full construction, and #4465's recorded
measurement puts one hosted capture at ~12 constructions (7 of them on the
event-loop thread). A cost linear in the tenant's graph, multiplied by that and
by every concurrent tenant, is store CPU — the resource #13722 measured at
~320 ms per round trip in the degraded window.

THE THREE THINGS THIS FILE PINS, each a way the fix could be defeated:

1. **The probe still probes.** EXACTLY ONE graph query, and it still traverses
   nodes. A probe that stopped querying would pass a "costs nothing" assertion
   while silently losing the fail-loud-on-open arm (`_probe_error` feeding the
   OOM/``maxmemory`` and corruption classifiers).
2. **The probe does not aggregate.** Mutant-driven: the pre-fix statement must
   FAIL the same detector, so the test measures the mechanism rather than a
   coincidence of the current string.
3. **The open's round-trip count is bounded** against a real store — #13722's
   own metric. One trivial read is 1 round trip; a steady-state open measured
   10, of which 5 are protocol commands and 5 are `GRAPH.QUERY` (enumerated
   below). A change that makes the open cost more fails here instead of after
   a deploy.

HOW DISCRIMINATING A REVERT IS (measured, so a later reader inherits the real
number): reverting the probe to `count(n)` reddens **3 of these 8 arms** on a
lane with a store — "one query, no aggregate", the open's wire-count filter,
and the scaling/control arm — and **1 of 7** where those two live arms skip
(the cite pin is not one of them).
(Measured at this head: `3 failed, 5 passed` with the probe reverted, and
`1 failed, 7 passed` with the aggregate filter narrowed back to
`GRAPH.QUERY`; the file as it stands, `8 passed`.)

A FOURTH, CHEAPER PIN rides along because this branch's own insert is what moved it: the
fix adds 30 lines at ``tortoise/projection/__init__.py:12099``, so EVERY ``file.py:NNNN``
cite below that point shifts by +30 — including the one the #7949 capstone probe's
``_a4_pointkind`` docstring quotes to explain the blank-``pointKind`` fold. A cite is pinned
to its CONTENT, never to a delta someone re-added (#12184's false-correction lesson), so the
row below re-derives the line from the fragment it quotes and reds if the evidence points
somewhere else.

Run (the first six and the cite pin need no database; the live ones need the docker lane)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13722_projection_open_round_trips.py -q
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils

#: Aggregations that turn a node traversal into a whole-graph scan. `count()` is
#: the one the probe used; the rest are here because "aggregate over the graph"
#: is the CLASS this file refuses, not one spelling of it.
_AGGREGATES = ("count(", "sum(", "avg(", "min(", "max(", "collect(")


def _aggregates(cypher: str) -> list[str]:
    """The aggregate calls a statement makes — parenthesised, so a property
    named `count` (``n.count``) is not a false hit."""
    low = " ".join(cypher.lower().split())
    return [a for a in _AGGREGATES if a in low]


def _aggregating_graph_sends(sends) -> list[str]:
    """The aggregating STATEMENTS carried by a list of ``(command, cypher)`` sends.

    Filtered on the whole `GRAPH.` namespace, not on `GRAPH.QUERY` alone. The
    graph handle this pin watches (``_GuardedGraph``) exposes `ro_query` as well
    as `query`, and `ro_query` goes out as `GRAPH.RO_QUERY` — FalkorDB runs the
    SAME Cypher under it, rejecting only write clauses, so
    ``MATCH (n) RETURN count(n) LIMIT 1`` is a perfectly legal read-only
    statement. A filter pinned to the read-write verb therefore leaves half the
    handle unwatched while this file's claim is "no aggregating graph query
    issued inside an open".
    """
    return [c for (name, c) in sends
            if name.upper().startswith("GRAPH.") and c and _aggregates(c)]


class _SpyGraph:
    """Just enough of ``proj.g`` for ``_probe_ok``: records, answers nothing."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.queries: list[str] = []
        self._raises = raises

    def query(self, cypher, *args, **kwargs):
        self.queries.append(cypher)
        if self._raises is not None:
            raise self._raises
        return []


def _probe_only_projection(spy: _SpyGraph):
    """A projection built without running ``__init__``.

    ``_probe_ok`` touches exactly two attributes — ``self.g`` and
    ``self._probe_error`` — so a bare object is the honest fixture: it makes the
    test about the probe's statement instead of about everything a real
    construction does first. Same ``object.__new__`` shape the module's own
    ``_schema_is_current`` fast path anticipates (the reason its reads use
    ``getattr``).
    """
    from tortoise.projection import FalkorProjection

    proj = object.__new__(FalkorProjection)
    proj.g = spy
    return proj


# ── 1 + 2: the statement, without a database ────────────────────────────────

def test_the_probe_issues_exactly_one_query_and_never_aggregates():
    """One node traversal, no aggregation — and it really is a traversal."""
    spy = _SpyGraph()
    proj = _probe_only_projection(spy)

    assert proj._probe_ok() is True
    assert len(spy.queries) == 1, (
        "the reachability probe must be ONE graph query; it issued "
        f"{spy.queries!r}")
    cypher = spy.queries[0]
    assert _aggregates(cypher) == [], (
        "the probe aggregates over the graph, which makes it linear in the "
        f"tenant's node count: {cypher!r}")
    # What the probe is FOR: a traversal of the store's node iterator, so a
    # graph that cannot be walked still fails here. A bare `RETURN 1` with no
    # MATCH would pass the assertions above and lose that arm.
    assert cypher.strip().upper().startswith("MATCH ("), (
        f"the probe stopped traversing the graph: {cypher!r}")


def test_the_pre_fix_statement_fails_that_detector():
    """MUTANT: the statement this change removed must be REJECTED.

    Without this, a future edit could loosen the assertion to "some query ran"
    and stay green over the exact defect #13722 removed.
    """
    pre_fix = "MATCH (n) RETURN count(n) LIMIT 1"
    assert _aggregates(pre_fix) == ["count("], (
        "the aggregate detector no longer recognises the statement #13722 "
        "removed — the assertion above has stopped discriminating")


def test_the_open_filter_watches_every_graph_verb_not_just_graph_query():
    """MUTANT: an aggregate issued through ANY `GRAPH.*` verb must be caught.

    The defect this closes is a pin narrower than its own claim: filtering on
    `GRAPH.QUERY` alone stays GREEN when the same aggregate is sent through
    `ro_query` (`GRAPH.RO_QUERY`), which is the verb a read-only path uses.
    """
    pre_fix = "MATCH (n) RETURN count(n) LIMIT 1"
    for verb in ("GRAPH.QUERY", "GRAPH.RO_QUERY", "graph.ro_query"):
        assert _aggregating_graph_sends([(verb, pre_fix)]) == [pre_fix], (
            f"an aggregate sent through {verb} was not seen by the open's "
            "filter — the pin is narrower than its own claim")

    # Not "any query at all": the statement the fix installs passes, so the
    # filter cannot be satisfied by flagging traffic generically.
    probe = "MATCH (n) RETURN 1 LIMIT 1"
    assert _aggregating_graph_sends([("GRAPH.QUERY", probe)]) == []
    assert _aggregating_graph_sends([("GRAPH.RO_QUERY", probe)]) == []

    # And it is scoped to the graph namespace: a non-graph command whose
    # argument happens to carry that text is not this pin's business.
    assert _aggregating_graph_sends([("CLIENT", pre_fix)]) == []


def test_a_failing_probe_still_keeps_the_reason():
    """The probe's OTHER half: a raise is recorded, never swallowed (#2981).

    The `maxmemory`/OOM and corruption classifiers downstream read
    ``_probe_error``, so a cheaper statement must not cost the reason.
    """
    boom = RuntimeError("Timeout argument must be a positive integer")
    proj = _probe_only_projection(_SpyGraph(raises=boom))

    assert proj._probe_ok() is False
    assert proj._probe_error is boom


def test_the_sampling_helper_samples_every_quantity_in_one_pass():
    """MUTANT: a helper that timed each callable in its OWN sequential block
    would hand back the same-shaped medians while measuring the host's load
    drift as if it were the statements' cost.

    The live arms below compare their numbers AGAINST EACH OTHER (2k-vs-50k,
    and probe-vs-control), so drift measured that way surfaces as a false RED
    on a correct probe — the cost the interleaved pass exists to remove. That
    A,B,C,A,B,C is what makes the comparison honest is therefore asserted
    here, not merely described in the helper's docstring.
    """
    order: list[str] = []

    def mk(name):
        def fn():
            order.append(name)
        return fn

    medians = _interleaved_medians(
        mk("small"), mk("large"), mk("control"), samples=4)

    assert order == ["small", "large", "control"] * 4, (
        "the sampling pass did not interleave the timed quantities, so each "
        f"one was measured in its own load window: {order!r}")
    assert len(medians) == 3 and all(m >= 0.0 for m in medians), (
        f"the helper must return one non-negative median per quantity: {medians!r}")


# ── 3: the round-trip count of a real open ──────────────────────────────────

_GRAPH = f"tortoise_test_13722_{uuid.uuid4().hex[:8]}"
_DB_URI = _live_utils.docker_uri(_GRAPH)

FALKORDB_AVAILABLE = False
try:  # pragma: no cover - environment probe
    from tortoise.sdk import TortoiseSDK as _ProbeSDK

    _old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    _probe = None
    try:
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        FALKORDB_AVAILABLE = True
    finally:
        # Close on BOTH paths: a probe whose check RAISED still holds an open
        # connection, and leaving it to the interpreter is a connection this
        # module leaks into everything that imports it. A close() that raises
        # must not turn a reachable lane into a skip, so it is swallowed.
        if _probe is not None:
            with contextlib.suppress(Exception):  # teardown of a dead handle
                _probe.close()
        if _old_uri is not None:
            os.environ["TORTOISE_DB_URI"] = _old_uri
        else:
            os.environ.pop("TORTOISE_DB_URI", None)
except Exception:  # pragma: no cover - no docker lane here
    FALKORDB_AVAILABLE = False


@pytest.fixture
def _live_uri():
    """Point the SDK at this test's isolated graph, restore after (#176)."""
    old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    try:
        yield _DB_URI
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


#: A steady-state (schema already current) projection open, MEASURED on docker
#: FalkorDB 4.20.6 at #13722 by enumerating every `Connection.send_command`:
#:
#:     1 CLIENT LIB-NAME     2 CLIENT LIB-VER   3 INFO
#:     4 INFO               5 MODULE
#:     6 GRAPH.QUERY  MATCH (n) RETURN 1 LIMIT 1                    (_probe_ok)
#:     7 GRAPH.QUERY  CALL db.indexes()
#:     8 GRAPH.QUERY  MATCH (n:Point) WHERE … search_keys …        (:Point FTS flatten)
#:     9 GRAPH.QUERY  CALL db.idx.vector.createNodeIndex(…)
#:    10 GRAPH.QUERY  CREATE VECTOR INDEX FOR (p:Point) …
#:
#: i.e. 5 protocol commands + 5 `GRAPH.QUERY` = 10, and a second open of the
#: same graph costs the same 10 (measured, same enumeration). The bound is 12 so
#: an engine that answers the vector procedure differently is not a red — that is
#: TWO commands of slack; a THIRD net-new graph-wide statement per open (10 → 13)
#: still is.
#:
#: ⛔ WHAT THIS ENUMERATION DOES NOT MEAN — THE OPEN IS NOT O(1). Command 8 is
#: still GRAPH-PROPORTIONAL: `_ARRAY_FTS_PROP_PROBE` (`projection/__init__.py`) is
#: `MATCH (n:Point) WHERE … RETURN 1 LIMIT 1`, so `LIMIT 1` bounds the ROW and not
#: the work — the exact shape this change removes from `_probe_ok`. It is issued
#: on the schema-current fast path (`_schema_is_current` →
#: `_array_valued_fts_prop_exists`) and carries no aggregate, so the arm below
#: cannot see it either. Review-measured at ~12 ms on a 50k-node graph on this
#: lane's container. It is PRICED AND LEFT deliberately (removing it is a design
#: decision — a write-maintained signal — and #5444/#5482 are both closed with
#: neither removing the cost), exactly as the PR body states; this note exists so
#: the bound cannot be misread as "a projection open does no graph-proportional
#: work".
_OPEN_ROUND_TRIPS_MAX = 12


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason="Live FalkorDB (Docker) not available")
def test_a_steady_state_open_is_bounded_and_a_read_is_one_round_trip(
        _live_uri, monkeypatch):
    """Pin #13722's own metric: a steady-state open <= 12 round trips, a read == 1.

    "Round trips" is counted at the wire — one ``Connection.send_command`` per
    command, which this client issues one at a time (measured: sends ==
    responses for every open below), so the number is network traffic rather
    than a call-site count dressed up as it.
    """
    from tortoise.projection import FalkorProjection

    # Warm the graph so the measured open takes the schema-current fast path —
    # the shape every request after the first one takes in production.
    warm = FalkorProjection.from_uri(_live_uri, graph_name=_GRAPH)
    warm.g.query("RETURN 1")

    import redis.connection as rc

    sent: list[tuple[str, str]] = []
    real = rc.Connection.send_command

    def counting(self, *args, **kwargs):
        name = args[0] if args else ""
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        sent.append((str(name), str(args[2]) if len(args) > 2 else ""))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(rc.Connection, "send_command", counting)

    before_open = len(sent)
    proj = FalkorProjection.from_uri(_live_uri, graph_name=_GRAPH)
    open_sends = sent[before_open:]

    before_read = len(sent)
    proj.g.query("MATCH (n:Point) RETURN n.id LIMIT 5")
    read_sends = sent[before_read:]

    assert len(read_sends) == 1, (
        "a trivial read must be ONE round trip; it issued "
        f"{[s[0] for s in read_sends]!r}")
    assert len(open_sends) <= _OPEN_ROUND_TRIPS_MAX, (
        f"a projection open issued {len(open_sends)} wire commands (bound "
        f"{_OPEN_ROUND_TRIPS_MAX}): {[s[0] for s in open_sends]!r}. Every "
        "hosted request pays this — URI mode builds a fresh SDK per request — "
        "so growth here is a per-request latency multiplier (#13722).")

    offending = _aggregating_graph_sends(open_sends)
    assert offending == [], (
        "a projection open issued an aggregating graph query, which scans the "
        f"tenant's whole graph on every request: {offending!r}")


#: The two node counts of the SCALING differential, and the two graphs they
#: live in. Each size gets its OWN graph so the two probes can be sampled
#: ALTERNATELY over one run (see `_interleaved_medians`) — a shared FalkorDB
#: under xdist has a load that drifts over tens of seconds, and two medians
#: taken in sequence measure that drift as much as the statement.
_SMALL_NODES = 2_000
_LARGE_NODES = 50_000
_SMALL_GRAPH = f"{_GRAPH}_2k"

#: The statement #13722 REMOVED, kept here as the same-run CONTROL. It is the
#: O(n) work the fix deletes, so timing it on the same graph calibrates the box
#: instead of assuming its speed.
_PRE_FIX = "MATCH (n) RETURN count(n) LIMIT 1"

#: Growth bound: O(1) probe cost is a flat line. MEASURED on docker FalkorDB
#: (lane, this change): probe 0.70 ms @2k -> 0.85 ms @50k = **1.2x**, while the
#: pre-fix statement went 0.76 -> 4.41 ms = **5.8x** over the same range. 2.5x
#: sits between them with ~2x headroom on BOTH sides, so a loaded lane cannot
#: false-red it and a re-introduced scan cannot pass it. RE-MEASURED by the
#: interleaved sampling in this file (docker 4.20.6, same box): 0.75 -> 0.83 ms
#: = **1.1x**, control `count(n)` at 5.82 ms = **7.0x** separation.
#:
#: ⛔ KEPT ABSOLUTE ON PURPOSE (review observation, #13722). A load-relative
#: form (`growth <= 2.5 x control_growth`) would also pass a probe that scales
#: WITH the graph but costs less than `count(n)` — a false PASS bought to avoid
#: a false RED, and a false pass is the expensive error. The load sensitivity is
#: answered at the SAMPLING instead: the two sizes — and the control below —
#: are sampled in ONE interleaved pass, so a load step lands on every half of
#: every ratio in this arm.
_SCALING_BOUND = 2.5

#: The control must be measurably slower than the probe. MEASURED at 50k:
#: count(n) 4.41 ms vs the probe's 0.85 ms = **5.2x**, vs a bare `RETURN 1` at
#: 0.68 ms (so the probe's cost IS the round trip, not a scan). This is the arm
#: a REVERT reddens: reverting the fix makes the probe and the control the same
#: statement, and the ratio collapses to ~1.0. 2x leaves 2.6x of headroom
#: (RE-MEASURED at 7.0x on the same box as the note above).
_CONTROL_RATIO = 2.0


def _interleaved_medians(*fns, samples: int = 11) -> tuple[float, ...]:
    """Medians of each callable in *fns*, sampled ALTERNATELY — one round each.

    ⛔ WHY EVERY TIMED QUANTITY IN AN ARM GOES THROUGH THIS ONE SAMPLING PASS.
    This lane shares its FalkorDB host with the rest of the fleet, and its load
    drifts over tens of seconds. Two medians taken in SEQUENTIAL passes measure
    that drift as much as the statements: a load step that arrives between them
    inflates whichever half it lands on without anything in the product
    changing (measured baseline 1.2x against a 2.5x bound, so a sustained ~2x
    step was enough to red a correct probe). Interleaving puts every sample of
    every quantity inside the same load window, so a drift lands on all of
    them.

    That matters for the CONTROL too, not only for the 2k-vs-50k pair: it is a
    ratio against a second statement (`count(n)`), so a control block measured
    after the interleaved pair can be deflated by a load DROP and red a correct
    probe — a false red on a legitimate fix, which is the cost this exists to
    remove.

    Medians over repeated samples, because one scheduler hiccup must not decide
    a scaling claim — and every arm below is a RATIO of two such medians, so it
    compares a run against itself rather than against a fixed number of
    milliseconds that belongs to the host.
    """
    import statistics
    import time

    timings: list[list[float]] = [[] for _ in fns]
    for _ in range(samples):
        for fn, acc in zip(fns, timings):
            t0 = time.perf_counter()
            fn()
            acc.append((time.perf_counter() - t0) * 1000.0)
    return tuple(statistics.median(acc) for acc in timings)


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason="Live FalkorDB (Docker) not available")
def test_the_open_probe_is_flat_in_a_large_graph(_live_uri):
    """The probe's cost does not grow with the graph — measured against ITSELF.

    ⛔ WHY THIS IS A DIFFERENTIAL AND NOT A WALL-CLOCK CEILING (review, #13722).
    The first cut of this arm asserted a 10 ms median at 50k nodes — and the
    pre-fix statement it exists to catch measures **4.41 ms** there (measured on
    the lane, matching the issue's table: 1.9 ms @20k, 7.6 ms @100k). So the
    defect PASSED the row that names it: reverting the fix left this arm green,
    and an O(graph) probe re-introduced at <=50k nodes would have shipped.

    Both halves of the claim are therefore ratios sampled in the SAME run, on
    the SAME graph, over the SAME connection:

    1. SCALING — the probe at 50k vs the same probe at 2k (O(1) work is a flat
       line; the pre-fix scan grows 5.8x across this range). The two sizes are
       sampled ALTERNATELY, so a load drift lands on both halves of the ratio.
    2. THE CONTROL — the probe vs the pre-fix statement itself on the 50k
       graph. This is the arm a revert reddens directly: the two samples become
       the same statement and the ratio collapses to ~1. Sampled in the SAME
       interleaved pass as (1), for the same reason.
    """
    from tortoise.projection import FalkorProjection

    small = FalkorProjection.from_uri(_live_uri, graph_name=_SMALL_GRAPH)
    small.g.query("MATCH (n) RETURN 1 LIMIT 1")  # warm the connection
    small.g.query(f"UNWIND range(1, {_SMALL_NODES}) AS i "
                  "MERGE (:Point {id: 'rt13722_' + toString(i)})")

    proj = FalkorProjection.from_uri(_live_uri, graph_name=_GRAPH)
    proj.g.query("MATCH (n) RETURN 1 LIMIT 1")  # warm the connection
    proj.g.query(f"UNWIND range(1, {_LARGE_NODES}) AS i "
                 "MERGE (:Point {id: 'rt13722_' + toString(i)})")

    # ALL THREE quantities in ONE interleaved pass, so the host's load is the
    # same on every sample of every ratio below — the two halves of `growth`
    # AND the two halves of the control ratio. The control is the statement the
    # fix deletes, timed on the LARGE graph (the tree being measured).
    small_ms, large_ms, control_ms = _interleaved_medians(
        small._probe_ok,
        proj._probe_ok,
        lambda: proj.g.query(_PRE_FIX),
    )

    growth = large_ms / max(small_ms, 1e-6)
    assert growth < _SCALING_BOUND, (
        f"the open probe grew {growth:.1f}x from {_SMALL_NODES} to "
        f"{_LARGE_NODES} nodes ({small_ms:.2f} ms -> {large_ms:.2f} ms; bound "
        f"{_SCALING_BOUND}x). Its cost is supposed to be O(1) — cost that "
        "rises with the graph is the unbounded-scan shape #13722 removed, paid "
        "on EVERY projection open, and the hosted lane opens one per request.")

    assert large_ms * _CONTROL_RATIO <= control_ms, (
        f"the open probe cost {large_ms:.2f} ms on a {_LARGE_NODES}-node graph "
        f"against {control_ms:.2f} ms for the pre-fix `{_PRE_FIX}` (needs "
        f"{_CONTROL_RATIO}x separation). Together those two numbers are the "
        "defect this file exists for: if the probe is as expensive as the "
        "aggregate, it IS the aggregate.")


# ── 6: the evidence cite this branch's own insert moved ────────────────────
#
# The fix inserts 30 lines at `tortoise/projection/__init__.py:12099`, so every
# `file.py:NNNN` cite BELOW it shifts by +30. The #7949 capstone probe quotes the
# blank-`pointKind` synthesis block to justify `A4`'s third reported state, and a
# cite that is 30 lines off reads exactly like a correct one — the reader opens the
# line, sees an unrelated statement, and silently discounts the passage.
#
# Pinned to CONTENT, not to a delta: the row derives the cited line from the
# fragment the probe actually quotes, and reds if that fragment is not there.
# (#12184: a re-derivation that re-applied its own delta produced a FALSE cite, and
# a `⚠️ CORRECTED` marker records that text changed — never that it changed right.)

_ROOT = Path(__file__).resolve().parent.parent
_CAPSTONE_PROBE = _ROOT / "docs/evidence/7949-capstone/probes/capstone_measure.py"
_PROJECTION_INIT = _ROOT / "tortoise/projection/__init__.py"
_CITE_RE = re.compile(r"tortoise/projection/__init__\.py:(\d+)(?:-(\d+))?")
_POINTKIND_FRAGMENT = "# Ensure pointKind is present"


def test_the_capstone_probe_cites_the_pointkind_block_where_it_lives():
    """The evidence cite this branch's +30-line insert moved must land on its content."""
    quoted = [ln for ln in _CAPSTONE_PROBE.read_text(encoding="utf-8").splitlines()
              if _POINTKIND_FRAGMENT in ln and _CITE_RE.search(ln)]
    assert len(quoted) == 1, (
        "expected exactly one probe line quoting `"
        f"{_POINTKIND_FRAGMENT}` beside a `tortoise/projection/__init__.py:NNNN` cite; "
        f"found {len(quoted)}: {quoted!r}. Keep the cite on the line that quotes the "
        "fragment so it stays re-derivable.")

    m = _CITE_RE.search(quoted[0])
    start = int(m.group(1))
    end = int(m.group(2) or start)
    src = _PROJECTION_INIT.read_text(encoding="utf-8").splitlines()

    assert 1 <= start <= end <= len(src), (
        f"the probe cites projection/__init__.py:{start}-{end}, outside the file "
        f"({len(src)} lines)")
    assert _POINTKIND_FRAGMENT in src[start - 1], (
        f"the probe cites projection/__init__.py:{start} for `{_POINTKIND_FRAGMENT}`, but "
        f"that line is {src[start - 1].strip()!r} — the cite was not re-derived after a "
        "shift. Search the fragment and re-point the number.")
    block = "\n".join(src[start - 1:end])
    assert 'clean["pointKind"] = ""' in block, (
        f"the probe's cited range {start}-{end} must cover the blank-`pointKind` "
        f"assignment it quotes; it does not:\n{block}")
