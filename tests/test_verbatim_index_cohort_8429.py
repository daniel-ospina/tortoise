"""#8429 — a VERBATIM (plain ``List``) embedding is invisible to the HNSW index leg.

THE DEFECT
----------
A caller-supplied embedding is stored VERBATIM as a plain Cypher ``List`` (PR
#3018's recorded decision, reaffirmed #5004). The ``Point`` HNSW vector index
holds ``Vectorf32`` values ONLY, so such a node is not in the index COHORT at
all: ``CALL db.idx.vector.queryNodes`` does not raise, it simply never yields it.
#8316 fixed the brute-force READ (a plain ``List`` no longer aborts the whole
``MATCH``), which covers the embedded lane and every index-absent/index-failed
lane — it does NOT cover an INDEXED store, because the index leg owned the read
there and its zero-row answer was recorded as a healthy ``ok``/count 0.

MEASURED (#8429's own reproduction, docker lane, ``CREATE VECTOR INDEX FOR
(n:Point) ON (n.embedding)``):

    indexed before verbatim:            [('q8316_a', 0.0)]
    typeof after verbatim overwrite:    [['List']]
    queryNodes after verbatim:          []

THE FIX (and its declared residual)
-----------------------------------
Zero index rows now FALL THROUGH to the brute-force scan, which reads BOTH
shapes through the shared seam — the same verdict #11642 already applies to an
empty RECORD answer ("a fold with no backfill is not an empty corpus"). The leg
records ``scan_fallback``: an index was expected and did not answer.

⛔ DECLARED RESIDUAL, pinned below: an index that DOES return rows still cannot
yield a cohort member it does not hold, so a verbatim node that would have
out-ranked one of them stays dark. Closing that half is #8429's option (b) —
widen the index cohort at the WRITE boundary — which reverses PR #3018 and
belongs to the #5046/#5048 reopen, never a quiet re-route.

Two lanes:
* the MECHANISM tests are hermetic (a graph double, no server) and run anywhere;
* the COHORT test needs a real HNSW index (FalkorDB server, not FalkorDBLite)
  and skips loudly where there is none — else the mechanism would be certified
  by a store where the defect cannot occur.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest

from tests import _live_utils

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.search_engine import (
    MECHANISM_INDEX,
    MECHANISM_SCAN_FALLBACK,
    reset_circuit_breakers,
    run_vector_query,
)

# ── FalkorDB availability check ─────────────────────────────────────────────
# ⛔ THE PROBE AND THE COHORT MUST USE THE SAME SEAM (review F5). The docker and
# legacy lanes publish on DIFFERENT ports (#6673: `TORTOISE_TEST_DOCKER_PORT` vs
# `TORTOISE_TEST_LEGACY_PORT`), so a probe that succeeds on one seam while the
# cohort URI is built from the other produces a hard
# `ConnectionError: ... 6379` red on a lane that has no server there — reading
# as a #8429 regression, and falsifying this file's own "skips loudly" premise.
# The candidates are tried in order and the URI that ANSWERED is the base every
# cohort graph is derived from.
FALKORDB_AVAILABLE = False
_PROBE_URI: str | None = None
_old_uri = os.environ.get("TORTOISE_DB_URI")
try:
    from tortoise.projection import FalkorProjection as _FP
    for _uri in (
        _old_uri,
        _live_utils.docker_uri("tortoise_probe_8429", password=None),
        _live_utils.legacy_uri("tortoise_probe_8429", password=None),
    ):
        if not _uri:
            continue
        try:
            _probe = _FP.from_uri(_uri)
            try:
                _probe.g.query("RETURN 1")
            finally:
                _probe.close()
        except Exception:  # noqa: BLE001, RUF100 — this seam has no server
            continue
        _PROBE_URI = _uri
        FALKORDB_AVAILABLE = True
        break
finally:
    if _old_uri is not None:
        os.environ["TORTOISE_DB_URI"] = _old_uri
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


# ── the MECHANISM: a zero-row index answer is not a verdict ─────────────────

class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _CohortGraph:
    """A graph double that answers the INDEX query and the SCAN separately.

    One double for both legs, because the defect IS the difference between them
    (the index cannot hold the row the scan can read).
    """

    def __init__(self, index_rows, scan_rows):
        self.index_rows = list(index_rows)
        self.scan_rows = list(scan_rows)
        self.queries: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.queries.append(cypher)
        return _Result(self.index_rows if "queryNodes" in cypher else self.scan_rows)


def _vec(n: int = 8) -> list[float]:
    return [0.1] * n


def test_a_zero_row_index_answer_falls_back_to_the_scan():
    """The fix: the index's empty cohort must not be reported as the answer."""
    reset_circuit_breakers()
    graph = _CohortGraph(index_rows=[], scan_rows=[("hidden", 0.97)])
    trace: list[dict] = []

    hits = run_vector_query(graph, _vec(), limit=5, is_embedded=False,
                            leg_trace=trace)

    assert hits == [("hidden", 0.97)], (
        "the dense leg returned nothing although the scan can read the node — "
        "a zero-row INDEX cohort is not an empty corpus (#8429)")
    assert any("queryNodes" in q for q in graph.queries), (
        "premise: the index was attempted first")
    assert any("vec.euclideanDistance" in q for q in graph.queries), (
        "the scan never ran — the zero-row index answer was returned as final")
    entry = next(e for e in trace if e["leg"] == "vector")
    assert entry["mechanism"] == MECHANISM_SCAN_FALLBACK, (
        f"the fallback must be NAMED (#4999): mechanism={entry['mechanism']!r}. "
        "An index was expected and did not answer.")
    assert entry["reason"] == "ok"
    assert entry["count"] == 1


def test_a_non_empty_index_answer_does_not_scan():
    """The other side of the pin: the fallback must not become "always scan".

    A full index answer is the index doing its job; paying for a label scan on
    every dense read would make the index pointless (#13108's concern).
    """
    reset_circuit_breakers()
    graph = _CohortGraph(index_rows=[("a", 0.1), ("b", 0.4)],
                         scan_rows=[("must-not-appear", 0.99)])
    trace: list[dict] = []

    hits = run_vector_query(graph, _vec(), limit=5, is_embedded=False,
                            leg_trace=trace)

    assert [pid for pid, _ in hits] == ["a", "b"]
    assert "must-not-appear" not in dict(hits)
    assert not any("vec.euclideanDistance" in q for q in graph.queries), (
        "the scan ran although the index answered — the #8429 fallback is "
        "only for a zero-row cohort")
    entry = next(e for e in trace if e["leg"] == "vector")
    assert entry["mechanism"] == MECHANISM_INDEX


def _cohort_uri() -> str:
    """A DEDICATED graph on the seam the PROBE proved (never a second guess).

    The population must be exactly this test's: an indexed neighbour left in a
    shared graph would fill the index cohort and hide the very case measured,
    and the fixture deletes every ``:Point`` it finds.
    """
    assert _PROBE_URI, "no live FalkorDB seam was probed — the tests skip"
    head, _sep, _tail = _PROBE_URI.rpartition("/")
    return f"{head}/tortoise_cohort_8429" if head else _PROBE_URI


@contextlib.contextmanager
def _cohort_sdk():
    """An SDK on the dedicated graph.

    ``TortoiseSDK(<uri>)`` treats a positional as a PATH (rejected as relative),
    and the SDK reads ``TORTOISE_DB_URI`` at construction — so the URI is
    installed for exactly the construction and restored immediately, leaving no
    lane-wide env change behind. The SDK has already captured it by then.
    """
    from tortoise.sdk import TortoiseSDK

    old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _cohort_uri()
    try:
        sdk = TortoiseSDK()
    finally:
        if old is None:
            os.environ.pop("TORTOISE_DB_URI", None)
        else:
            os.environ["TORTOISE_DB_URI"] = old
    try:
        yield sdk
    finally:
        sdk.close()


# ── the COHORT: a real index drops the verbatim node (and now recovers) ─────

def _raw_index_ids(graph, vec, limit: int = 10) -> list:
    """What the INDEX ALONE yields — the gap, measured rather than assumed.

    Both known argument orders (#1359): the engine that created the index
    accepts exactly one. An index that raises yields an empty list, and the
    assertion that reads this is about the COHORT, never about the API shape.
    """
    for cypher in (
        "CALL db.idx.vector.queryNodes('Point', 'embedding', $q, $k) "
        "YIELD node RETURN node.id",
        "CALL db.idx.vector.queryNodes('Point', 'embedding', $k, vecf32($q)) "
        "YIELD node RETURN node.id",
    ):
        try:
            rows = graph.query(cypher, params={"q": vec, "k": limit}).result_set
        except Exception:  # noqa: BLE001, RUF100 — wrong signature for this engine
            continue
        return [row[0] for row in rows]
    return []


def _index_leg(sdk, query, limit=10):
    """One dense read through the index lane, with its trace entry.

    Retries until the store actually uses the INDEX: the engine's index build is
    asynchronous, and a read taken before it settles would report `scan` and
    certify nothing.
    """
    import time
    deadline = time.monotonic() + 30.0
    entry = None
    hits: list = []
    while True:
        reset_circuit_breakers()
        trace: list[dict] = []
        hits = run_vector_query(sdk._get_proj().g, query, limit=limit,
                               is_embedded=False, leg_trace=trace)
        entry = next(e for e in trace if e["leg"] == "vector")
        if entry.get("mechanism") != "scan" or time.monotonic() > deadline:
            return dict(hits), entry
        time.sleep(0.1)


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason=_live_utils.LIVE_URI_SKIP_REASON)
def test_a_verbatim_embedding_is_reachable_on_an_indexed_store():
    """RED before: the index returns 0 rows, the leg reports `ok`, hits == {}.

    The store is a fresh graph so the Point population is exactly this test's —
    an indexed neighbour from another test would fill the cohort and hide the
    very case being measured.
    """
    with _cohort_sdk() as sdk:
        g = sdk._get_proj().g
        g.query("MATCH (p:Point) DETACH DELETE p")
        near = [1.0] * 384
        g.query(
            "CREATE (p:Point {id:'cohort-typed', content:'typed', "
            "pointKind:'statement', embedding: vecf32($v)})",
            params={"v": near})

        hits, entry = _index_leg(sdk, near)
        assert entry["mechanism"] == MECHANISM_INDEX, (
            f"premise: this store must be answering from the HNSW index "
            f"(got {entry['mechanism']!r}) — without an index there is no "
            "cohort to miss")
        assert "cohort-typed" in hits, "premise: an indexed node is findable"

        # THE WRITE the recorded decision allows: the caller's vector, verbatim.
        g.query("MATCH (p:Point {id:'cohort-typed'}) SET p.embedding=$v",
                params={"v": near})
        types = g.query(
            "MATCH (p:Point {id:'cohort-typed'}) RETURN typeof(p.embedding)"
        ).result_set
        assert types and types[0][0] == "List", (
            "premise: the caller-supplied vector is stored VERBATIM (PR #3018)")

        assert _raw_index_ids(g, near) == [], (
            "premise: the HNSW index cohort does NOT hold the verbatim node — "
            "that gap is what the dense leg used to report as `ok`/count 0 "
            "(#8429). If the index now yields it, the cohort gap itself was "
            "closed at the WRITE boundary and this pin must be redone.")

        hits, entry = _index_leg(sdk, near)
        assert "cohort-typed" in hits, (
            "the verbatim node is invisible to the dense leg on an indexed "
            "store — the HNSW index holds Vectorf32 values only, so the leg "
            "returned 0 rows and reported 'ok' (#8429)")
        assert entry["mechanism"] == MECHANISM_SCAN_FALLBACK, (
            f"the recovery must be NAMED (#4999): mechanism={entry['mechanism']!r}")
        assert entry["reason"] == "ok"


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason=_live_utils.LIVE_URI_SKIP_REASON)
def test_the_declared_residual_a_non_empty_index_answer_still_hides_it():
    """#8429's residual, pinned so the claim in the fix stays verified.

    When the index DOES return rows, the scan is not consulted, so a verbatim
    node that would have out-ranked a returned row is still absent. This is the
    half that needs the WRITE boundary (PR #3018's reopen) — closing it must RED
    this pin and the seam docstrings, not silently pass.
    """
    with _cohort_sdk() as sdk:
        g = sdk._get_proj().g
        g.query("MATCH (p:Point) DETACH DELETE p")
        query = [1.0] * 384
        other = [-1.0] * 384
        g.query(
            "CREATE (p:Point {id:'residual-indexed', content:'indexed', "
            "pointKind:'statement', embedding: vecf32($v)})",
            params={"v": other})
        g.query(
            "CREATE (p:Point {id:'residual-verbatim', content:'verbatim', "
            "pointKind:'statement', embedding: $v})", params={"v": query})

        hits, entry = _index_leg(sdk, query)
        assert entry["mechanism"] == MECHANISM_INDEX, (
            "premise: the index answered (a non-empty cohort), so no scan was "
            "owed — that IS the residual")
        assert "residual-verbatim" not in hits, (
            "the verbatim node is now reachable from a NON-EMPTY index answer — "
            "the declared residual was closed: UPDATE this pin and the seam "
            "docstrings rather than deleting them")
