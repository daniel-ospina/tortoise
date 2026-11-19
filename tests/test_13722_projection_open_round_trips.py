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
   10, of which 2 are the redis handshake. A change that makes the open cost
   more fails here instead of after a deploy.

Run (the first three need no database; the live ones need the docker lane)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13722_projection_open_round_trips.py -q
"""
from __future__ import annotations

import os
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


def test_a_failing_probe_still_keeps_the_reason():
    """The probe's OTHER half: a raise is recorded, never swallowed (#2981).

    The `maxmemory`/OOM and corruption classifiers downstream read
    ``_probe_error``, so a cheaper statement must not cost the reason.
    """
    boom = RuntimeError("Timeout argument must be a positive integer")
    proj = _probe_only_projection(_SpyGraph(raises=boom))

    assert proj._probe_ok() is False
    assert proj._probe_error is boom


# ── 3: the round-trip count of a real open ──────────────────────────────────

_GRAPH = f"tortoise_test_13722_{uuid.uuid4().hex[:8]}"
_DB_URI = _live_utils.docker_uri(_GRAPH)

FALKORDB_AVAILABLE = False
try:  # pragma: no cover - environment probe
    from tortoise.sdk import TortoiseSDK as _ProbeSDK

    _old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    try:
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        _probe.close()
        FALKORDB_AVAILABLE = True
    finally:
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


#: A steady-state (schema already current) projection open, measured on docker
#: FalkorDB 4.20.6 at #13722: 2 redis-handshake commands (`CLIENT SETINFO` ×2)
#: + 5 `GRAPH.QUERY` (`_probe_ok`, `CALL db.indexes()`, the `:Point` FTS
#: flatten check, and the vector-index API resolution ×2) = 10. The bound is 12
#: so an engine that answers the vector procedure differently is not a red,
#: while a whole new graph-wide statement per open still is.
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

    offending = [c for (n, c) in open_sends
                 if n.upper() == "GRAPH.QUERY" and c and _aggregates(c)]
    assert offending == [], (
        "a projection open issued an aggregating graph query, which scans the "
        f"tenant's whole graph on every request: {offending!r}")


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason="Live FalkorDB (Docker) not available")
def test_the_open_probe_is_flat_in_a_large_graph(_live_uri):
    """The probe's cost does not grow with the graph.

    Deliberately loose — it is a scaling assertion, not a performance test. The
    pre-fix statement measured 0.9 ms @1k, 7.6 ms @100k and 21.7 ms @300k nodes
    versus 0.4-0.6 ms flat; the ceiling absorbs a loaded CI box while still
    failing an O(n) statement at 50k nodes.
    """
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection.from_uri(_live_uri, graph_name=_GRAPH)
    proj.g.query("UNWIND range(1, 50000) AS i "
                 "MERGE (:Point {id: 'rt13722_' + toString(i)})")

    import statistics
    import time

    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        assert proj._probe_ok() is True
        samples.append((time.perf_counter() - t0) * 1000.0)
    median = statistics.median(samples)
    assert median < 10.0, (
        f"the open probe took {median:.1f} ms on a 50k-node graph — that is "
        "the unbounded-scan shape #13722 removed (flat cost is ~0.5 ms), paid "
        "on EVERY projection open and the hosted lane opens one per request")
