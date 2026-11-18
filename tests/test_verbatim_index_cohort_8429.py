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

import ast
import contextlib
import os
import sys
import time
import uuid
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

#: Bound and cadence of the index-settle poll (#13290 review F3). Named so the
#: hermetic pin below reads the SAME numbers the live lane uses.
_INDEX_SETTLE_TIMEOUT_S = 30.0
_INDEX_SETTLE_POLL_S = 0.1

# ── FalkorDB availability check ─────────────────────────────────────────────
# ⛔ THE PROBE AND THE COHORT MUST USE THE SAME SEAM (review F5). The docker and
# legacy lanes publish on DIFFERENT ports (#6673: `TORTOISE_TEST_DOCKER_PORT` vs
# `TORTOISE_TEST_LEGACY_PORT`), so a probe that succeeds on one seam while the
# cohort URI is built from the other produces a hard
# `ConnectionError: ... 6379` red on a lane that has no server there — reading
# as a #8429 regression, and falsifying this file's own "skips loudly" premise.
# The candidates are tried in order and the URI that ANSWERED is the base every
# cohort graph is derived from. Each candidate's graph is the SAME per-run MINT
# (`_probe_graph_name`, #13290 review): a fixed probe name is unreapable by both
# cleanup arms, and `from_uri`'s default `ensure_indexes=True` CREATES the graph
# it names — so a fixed name leaked one graph per lane, and on the ambient seam
# it ran schema DDL on the shared env-URI handle.
def _probe_graph_name() -> str:
    """A PER-RUN name for the availability probe's OWN graph (#13290 review).

    ⛔ The literal this replaces was the FIXED ``tortoise_probe_8429``, and it is
    the exact class `_cohort_graph_name` below exists to close, one level up:
    it satisfies NEITHER cleanup arm (``"tortoise_probe_8429".startswith(
    ("test_", "tortoise_test"))`` is ``False``, and ``8429`` is 4 hex characters
    against a floor of 8), so no arm could ever reap it — and ``from_uri``
    defaults to ``ensure_indexes=True``, whose schema DDL **CREATES** the graph.
    A fixed name is therefore a leak per lane and per CI run, and on the ambient
    seam it aimed that DDL at whatever graph the env URI names — the same SHARED
    handle the cohort arm must never write, so the override below also takes the
    probe OFF that graph entirely.

    Same vocabulary as ``_cohort_graph_name``: the ``tortoise_test`` prefix puts
    it inside ``tests/_embedded.py``'s ``_SERVER_WIPE_PREFIXES``, and the
    ``_<10 lowercase hex>`` suffix matches ``_PER_RUN_GRAPH_NAME_RE`` so
    ``drop_test_graph`` admits it instead of refusing a shared spelling.
    Pinned by ``test_the_probe_graph_name_is_one_this_tree_can_reap``.
    """
    return f"tortoise_test_probe_8429_{uuid.uuid4().hex[:10]}"


FALKORDB_AVAILABLE = False
_PROBE_URI: str | None = None
_old_uri = os.environ.get("TORTOISE_DB_URI")
try:
    from tortoise.projection import FalkorProjection as _FP
    _probe_graph = _probe_graph_name()
    for _uri in (
        _old_uri,
        _live_utils.docker_uri(_probe_graph, password=None),
        _live_utils.legacy_uri(_probe_graph, password=None),
    ):
        if not _uri:
            continue
        try:
            _probe = _FP.from_uri(_uri, graph_name=_probe_graph)
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
    """A graph double that answers the INDEX query, the PROBE and the SCAN apart.

    THREE populations, because the #8429 decision has three inputs and the defect
    is their DIFFERENCE: the index query (filtered — the answer the leg returns),
    the k=1 probe (``LIMIT 1`` — the engine's raw cohort, asked only on the
    zero-row path) and the scan (which reads both stored shapes). ``probe_rows``
    defaults to ``index_rows``, i.e. "the engine yielded exactly what the read's
    own predicate let through"; a caller that sets it can express the state the
    probe exists for — a POPULATED cohort whose rows the predicate excluded.
    """

    def __init__(self, index_rows, scan_rows, probe_rows=None, burn_s=0.0):
        self.index_rows = list(index_rows)
        self.scan_rows = list(scan_rows)
        self.probe_rows = (list(index_rows) if probe_rows is None
                           else list(probe_rows))
        self.queries: list[str] = []
        #: ``(cypher, timeout)`` per call, in order. The two #13290 review F2
        #: pins read this: both NEW `#11691` budget guards are visible ONLY here.
        self.calls: list[tuple[str, int | None]] = []
        self.burn_s = burn_s

    def query(self, cypher, params=None, timeout=None):
        self.queries.append(cypher)
        self.calls.append((cypher, timeout))
        if "queryNodes" not in cypher:
            return _Result(self.scan_rows)
        if "LIMIT 1" in cypher:  # the cohort probe, never the answer
            return _Result(self.probe_rows)
        if self.burn_s:
            # The index attempt SPENDS read budget. Without this a remainder is
            # arithmetically indistinguishable from a whole budget on a fast
            # double, which is why the pre-fix code could be reverted with every
            # assertion here still green (#13290 review F2).
            time.sleep(self.burn_s)
        return _Result(self.index_rows)

    def timeouts_for(self, needle: str) -> list[int | None]:
        """The ``timeout`` handed to every call whose cypher carries ``needle``."""
        return [t for q, t in self.calls if needle in q]


def _vec(n: int = 8) -> list[float]:
    return [0.1] * n


#: The read budget and the time the INDEX attempt spends before answering zero
#: rows — the two numbers the #13290 review F2 pins below measure against. The
#: burn is a REAL sleep, the technique `test_11691_vector_leg_budget` uses: a
#: remainder is only distinguishable from a whole budget if the leg actually
#: spent some of the read first.
_BUDGET_MS = 500
_INDEX_BURN_S = 0.08
#: The least the index attempt must have spent for the remainder to read as one.
#: Below the sleep, with room for a slow runner's own accounting.
_BURN_FLOOR_MS = 50


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


def test_a_post_filter_empty_answer_does_not_scan_or_claim_the_gap():
    """#8429 review F2, PINNED: a populated cohort whose rows the read's OWN
    predicate excluded is NOT the cohort gap.

    The index query carries the status/`documentKind` predicate (`LIMIT` counts
    POST-filter rows on purpose), so it can return zero rows while the engine
    holds a full cohort. Without the probe this state was indistinguishable from
    the verbatim-node gap: the leg would have paid a FULL LABEL SCAN on every
    such read and reported `scan_fallback` — "the index did not answer" — for
    an index that answered and was then filtered, which is the exact cost
    `#13108` removed, on the regime where it was measured.

    Deleting the probe leaves every other case in this file green, which is why
    this one exists.
    """
    reset_circuit_breakers()
    # index (filtered) -> nothing survived; probe (unfiltered, k=1) -> the cohort
    # is there; the scan would return a row if it were allowed to run.
    graph = _CohortGraph(index_rows=[],
                         scan_rows=[("would-only-come-from-a-scan", 0.5)],
                         probe_rows=[("excluded-by-the-predicate",)])
    trace: list[dict] = []

    hits = run_vector_query(graph, _vec(), limit=5, is_embedded=False,
                            leg_trace=trace)

    assert hits == [], (
        "the leg returned a row its index answer did not contain — the scan ran "
        "on a cohort the probe says is POPULATED (#8429 review F2)")
    assert not any("vec.euclideanDistance" in q for q in graph.queries), (
        "a full label scan was paid on a filtered-to-empty index answer — the "
        "exact cost #13108 removed (#8429 review F2)")
    assert any("LIMIT 1" in q for q in graph.queries), (
        "premise: the zero-row path asks the engine about its raw cohort")
    entry = next(e for e in trace if e["leg"] == "vector")
    assert entry["mechanism"] == MECHANISM_INDEX, (
        "the leg blamed the index for a filter it applied itself: "
        f"mechanism={entry['mechanism']!r} (#8429 review F2)")
    assert entry["reason"] == "ok" and entry["count"] == 0


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


def test_the_fallthrough_scan_is_handed_what_is_left_of_the_read():
    """#13290 review F2 — the round-1 F3 bound was fixed with NO assertion.

    ``search_engine.py:4281`` hands the scan ``remaining_read_ms(...)`` so a slow
    index cannot buy the recovery a SECOND full budget. Reverting it to
    ``timeout_ms`` reddened no row in the tree: the finding was raised in round 1
    ("the fall-through scan got a fresh full budget"), fixed, and left unpinned —
    the same shape #11691's record leg had.

    The failure is invisible on a fast store, which is why no existing pin saw it:
    the scan gets a fresh 500 ms, `degradation_chain` collects past its own
    deadline, records ``reason="timeout"`` and DISCARDS the recovered rows —
    precisely on the slow-index regime this fallback exists for.

    RED if the scan is handed the whole budget again: 500 is not <= 450.
    """
    reset_circuit_breakers()
    graph = _CohortGraph(index_rows=[], scan_rows=[("hidden", 0.97)],
                         burn_s=_INDEX_BURN_S)
    trace: list[dict] = []

    hits = run_vector_query(graph, _vec(), limit=5, is_embedded=False,
                            leg_trace=trace, timeout_ms=_BUDGET_MS)

    assert dict(hits) == {"hidden": 0.97}, (
        "premise: the zero-row index answer fell through to the scan")
    handed = graph.timeouts_for("vec.euclideanDistance")
    assert handed, "premise: the scan ran — nothing to pin otherwise"
    assert max(handed) <= _BUDGET_MS - _BURN_FLOOR_MS, (
        f"the fall-through scan was handed {max(handed)} ms of a {_BUDGET_MS} ms "
        f"read AFTER the index attempt had already spent ~{_INDEX_BURN_S * 1000:.0f} "
        "ms of it — the remainder is not being handed over, so the recovery can "
        "outlive the read and the collector discards its rows as `timeout` "
        "(#11691's rule, round-1 F3)")
    assert min(handed) >= 1, (
        f"the scan was handed timeout={min(handed)}: 0 means NO TIMEOUT to the "
        "driver, which is the unbounded leg this bound exists to close")


def test_the_cohort_probe_is_handed_what_is_left_of_the_read():
    """#13290 review F2 — the round-2 probe bound was fixed with NO assertion.

    ``search_engine.py:4199-4200`` bounds ``_index_yields_any_node()`` by what is
    LEFT of the read: a full ``timeout_ms`` there lets a zero-row answer spend the
    leg's whole budget and then a second one, so the recovery is lost exactly when
    the index is slow. No row in the tree read that argument — the probe's own
    double accepted ``timeout`` and never looked at it.

    RED if the probe is handed the whole budget again: 500 is not <= 450.
    """
    reset_circuit_breakers()
    graph = _CohortGraph(index_rows=[], scan_rows=[("must-not-appear", 0.99)],
                         probe_rows=[("in-cohort",)], burn_s=_INDEX_BURN_S)
    trace: list[dict] = []

    hits = run_vector_query(graph, _vec(), limit=5, is_embedded=False,
                            leg_trace=trace, timeout_ms=_BUDGET_MS,
                            excluded_statuses=("retired",))

    assert hits == [], (
        "premise: a POPULATED cohort filtered to zero rows is not the #8429 gap")
    handed = graph.timeouts_for("LIMIT 1")
    assert handed, "premise: the cohort probe ran — nothing to pin otherwise"
    assert max(handed) <= _BUDGET_MS - _BURN_FLOOR_MS, (
        f"the cohort probe was handed {max(handed)} ms of a {_BUDGET_MS} ms read "
        f"AFTER the index attempt had already spent ~{_INDEX_BURN_S * 1000:.0f} ms "
        "of it — a zero-row answer can then spend the read twice over, which is "
        "the budget leak the round-2 F bound exists to close")
    assert min(handed) >= 1, (
        f"the probe was handed timeout={min(handed)}: 0 means NO TIMEOUT to the "
        "driver, which is the unbounded leg this bound exists to close")


class _StubSDK:
    """The whole surface ``_index_leg`` touches: a graph handle it never uses."""

    class _Proj:
        g = object()

    def _get_proj(self):
        return _StubSDK._Proj()


def test_the_settle_retry_actually_fires(monkeypatch):
    """#13290 review F3 — the poll must key on the token the code EMITS.

    ``scan_mechanism`` is ``MECHANISM_SCAN_FALLBACK`` on every non-embedded
    path, so a ``!= "scan"`` condition could never be true and the loop always
    returned on its FIRST iteration: the async-index-build protection this
    helper documents did not exist, and the premise assert in the two live
    COHORT rows was evaluated on one un-retried read.

    RED against that condition (one call, ``scan_fallback`` returned), GREEN
    here. Hermetic — the dense read is replaced, so no server is needed.
    """
    calls: list[int] = []

    def _fake(graph, query, limit=10, is_embedded=False, leg_trace=None):
        calls.append(1)
        settled = len(calls) >= 3
        leg_trace.append({
            "leg": "vector", "ran": True, "degraded": False,
            "reason": "ok", "count": 1,
            "mechanism": MECHANISM_INDEX if settled else MECHANISM_SCAN_FALLBACK,
        })
        return [("settled" if settled else "not-yet", 0.5)]

    monkeypatch.setattr(sys.modules[__name__], "run_vector_query", _fake)
    hits, entry = _index_leg(_StubSDK(), _vec())

    assert entry["mechanism"] == MECHANISM_INDEX, (
        "the helper returned a NOT-SETTLED answer, so the premise assert in "
        "the live COHORT rows would red on a loaded runner (#13290 review F3)")
    assert len(calls) == 3, (
        f"the dense read ran {len(calls)} time(s) — the settle retry must poll "
        "on `mechanism != MECHANISM_INDEX`, which is the token a non-embedded "
        "read actually emits; `!= 'scan'` can never fire")
    assert "settled" in hits, "the retried read's rows must be the ones returned"


def _cohort_graph_name() -> str:
    """A PER-RUN graph name this tree can OWN and REAP (#13290 review F1).

    * the prefix is ``tortoise_test``, inside ``tests/_embedded.py``'s
      ``_SERVER_WIPE_PREFIXES``, so the session sweep sees it;
    * the suffix is ``_<10 lowercase hex>``, matching
      ``tests/_live_utils.py::_PER_RUN_GRAPH_NAME_RE``, so ``drop_test_graph``
      admits it instead of REFUSING a fixed/shared spelling.

    ⛔ The name is MINTED per call rather than fixed at ``tortoise_cohort_8429``.
    A fixed name failed BOTH arms above (``"tortoise_test".startswith(...)`` is
    False and a 4-hex ``8429`` is below the 8-character floor), so every lane and
    CI job would share one graph and each would run the whole-label ``DETACH
    DELETE`` over the others' ``:Point`` nodes — a false red that reads as a
    #8429 regression — while the graph itself was unreachable by any cleanup and
    leaked forever. ``test_the_cohort_graph_name_is_one_this_tree_can_reap``
    pins the vocabulary; the literal this replaced is its mutant.
    """
    return f"tortoise_test_cohort_8429_{uuid.uuid4().hex[:10]}"


def _cohort_uri() -> tuple[str, str]:
    """A DEDICATED, PER-RUN graph on the seam the PROBE proved.

    Returns ``(uri, graph_name)``. The POPULATION must be exactly this test's —
    an indexed neighbour left in a shared graph would fill the index cohort and
    hide the very case measured — and the name must be one this tree can own and
    reap, which is ``_cohort_graph_name``'s rule.
    """
    assert _PROBE_URI, "no live FalkorDB seam was probed — the tests skip"
    name = _cohort_graph_name()
    head, _sep, _tail = _PROBE_URI.rpartition("/")
    assert head, f"the probed URI carries no graph path: {_PROBE_URI!r}"
    return f"{head}/{name}", name


@contextlib.contextmanager
def _cohort_sdk():
    """An SDK on a dedicated, PER-RUN graph — and the graph is reaped here.

    ``TortoiseSDK(<uri>)`` treats a positional as a PATH (rejected as relative),
    and the SDK reads ``TORTOISE_DB_URI`` at construction — so the URI is
    installed for exactly the construction and restored immediately, leaving no
    lane-wide env change behind. The SDK has already captured it by then.

    The teardown DROPS the graph through ``_live_utils.drop_test_graph``, whose
    fail-closed gate (prefix + mint shape) admits this per-run name and would
    refuse a fixed one. Leaving it to the session sweep would be a second owner
    for the same policy; leaving it to nothing is the leak #13290 review F1
    measured.
    """
    from tortoise.sdk import TortoiseSDK

    uri, graph_name = _cohort_uri()
    old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = uri
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
        # The graph is OUR per-run mint, so it is reaped here rather than left to
        # the session sweep — one owner for the policy. `drop_test_graph` never
        # raises by contract; the suppress is for the SDK handle itself.
        with contextlib.suppress(Exception):
            _live_utils.drop_test_graph(sdk._get_proj(), name=graph_name)
        sdk.close()


def test_the_cohort_graph_name_is_one_this_tree_can_reap():
    """#13290 review F1 — a FIXED name is invisible to BOTH cleanup arms.

    The live fixture opens with a whole-label ``MATCH (p:Point) DETACH DELETE p``,
    so the graph it runs against must be per-run and owned. The name that shipped
    first, ``tortoise_cohort_8429``, satisfied neither arm of this tree's
    graph-name policy:

    * ``"tortoise_cohort_8429".startswith(("test_", "tortoise_test"))`` is
      ``False`` — the session-end sweep keys on exactly those prefixes, so it
      could never drop the graph;
    * ``_PER_RUN_GRAPH_NAME_RE`` requires a ``_<8+ lowercase hex>`` suffix —
      ``8429`` is 4 characters, so ``drop_test_graph`` REFUSED the name by its own
      fail-closed gate and the graph leaked once per lane and CI run.

    RED on that literal (the first two assertions), GREEN on the mint. Hermetic —
    it reads the policy's own declarations, so it needs no server.
    """
    from tests._embedded import _SERVER_WIPE_PREFIXES

    name = _cohort_graph_name()
    assert name.startswith(_SERVER_WIPE_PREFIXES), (
        f"{name!r} is outside the session sweep's prefix family "
        f"{_SERVER_WIPE_PREFIXES} — nothing would ever drop it (#13290 F1)")
    assert _live_utils._PER_RUN_GRAPH_NAME_RE.match(name), (
        f"{name!r} is not a per-run MINT shape, so `drop_test_graph` refuses to "
        "delete it and the graph leaks (#13290 F1)")
    assert name != _cohort_graph_name(), (
        "the name must be minted per RUN — a stable spelling is a shared graph "
        "carrying a whole-label DETACH DELETE (#13290 F1)")


def test_the_probe_graph_name_is_one_this_tree_can_reap():
    """#13290 review — the SAME policy, applied to the probe's own graph.

    The availability probe shipped with the FIXED ``tortoise_probe_8429``, which
    failed both arms `test_the_cohort_graph_name_is_one_this_tree_can_reap`
    pins (wrong prefix family, 4-hex suffix) while ``from_uri``'s default
    ``ensure_indexes=True`` **creates** the graph it names — a leak no cleanup
    arm could reach, and on the ambient seam a schema DDL aimed at the shared
    env-URI handle. RED on that literal, GREEN on the mint.

    Hermetic — it reads the policy's own declarations, so it needs no server.
    """
    from tests._embedded import _SERVER_WIPE_PREFIXES

    name = _probe_graph_name()
    assert name.startswith(_SERVER_WIPE_PREFIXES), (
        f"{name!r} is outside the session sweep's prefix family "
        f"{_SERVER_WIPE_PREFIXES} — nothing would ever drop it (#13290)")
    assert _live_utils._PER_RUN_GRAPH_NAME_RE.match(name), (
        f"{name!r} is not a per-run MINT shape, so `drop_test_graph` refuses to "
        "delete it and the graph leaks (#13290)")
    assert name != _probe_graph_name(), (
        "the probe's graph must be minted per RUN — a stable spelling is a "
        "shared graph, and `from_uri(ensure_indexes=True)` creates it (#13290)")

    # ⛔ THE CALL SITE, not only the helper (review pass 3, P2 — MEASURED): the
    # assertions above read a FRESH call and the `_probe_graph` global, and
    # NEITHER is tied to the argument `from_uri` actually receives. MUTANT that
    # was GREEN against them: replace the call site with
    # `_probe = _FP.from_uri(_uri, graph_name="tortoise_probe_8429")` — the
    # literal this change removes — leaving `_probe_graph` untouched. The pin
    # passed and the census stayed `[]` (it only sees the PRESENCE of
    # `graph_name=`), i.e. the leak could return with every gate green. So the
    # call is read statically here: the ONE `from_uri` call in this module must
    # take `graph_name=` AS the minted name, and that name must come from the
    # minting helper.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    from_uri_calls = [node for node in ast.walk(tree)
                      if isinstance(node, ast.Call)
                      and getattr(node.func, "attr", None) == "from_uri"]
    assert len(from_uri_calls) == 1, (
        f"expected ONE `from_uri(` call in this module, found "
        f"{len(from_uri_calls)} — this pin reads that one site (#13290)")
    kw = {k.arg: k.value for k in from_uri_calls[0].keywords}
    assert "graph_name" in kw, (
        "the probe's `from_uri(` passes no `graph_name=` — the census reds and "
        "the graph its schema DDL creates belongs to nobody (#13290)")
    assert isinstance(kw["graph_name"], ast.Name) and \
        kw["graph_name"].id == "_probe_graph", (
        "the call site's `graph_name=` is not the MINTED `_probe_graph` — a "
        "literal there feeds `ensure_indexes`' DDL into ONE graph shared by "
        "every lane and CI run, and the census still passes because it only "
        "sees the keyword (#13290)")
    # ⛔ EXACTLY ONE binding, not "a mint assignment exists somewhere" (review
    # pass 4, P2 — MEASURED): `any()` over the assignments let a DECOY
    # `_probe_graph = _probe_graph_name()` sit beside `_probe_graph =
    # "tortoise_probe_8429"` in use, and both pins plus the census stayed GREEN.
    # An annotated binding (`_probe_graph: str = _probe_graph_name()`) is the
    # same fact spelled differently, so it counts as a binding rather than a
    # false RED against a correct call site.
    bindings = [n for n in ast.walk(tree)
                if isinstance(n, (ast.Assign, ast.AnnAssign))
                and (any(isinstance(t, ast.Name) and t.id == "_probe_graph"
                         for t in (n.targets if isinstance(n, ast.Assign)
                                   else [n.target])))]
    assert len(bindings) == 1, (
        f"expected EXACTLY ONE binding of `_probe_graph`, found {len(bindings)} "
        "— a decoy mint assignment beside the literal IN USE would otherwise "
        "pass this pin and the census (#13290)")
    assert isinstance(bindings[0].value, ast.Call) and \
        getattr(bindings[0].value.func, "id", None) == "_probe_graph_name", (
        "`_probe_graph` is not bound to `_probe_graph_name()` — the call site is "
        "wired to something other than the per-run mint (#13290)")


def test_the_live_fixture_teardown_drops_its_own_graph(monkeypatch):
    """#13290 review — the DROP CALL is pinned, not only the reap-able NAME.

    ``test_the_cohort_graph_name_is_one_this_tree_can_reap`` proves the graph is
    reapABLE; it says nothing about anyone reaping it. Deleting the
    ``drop_test_graph`` call from ``_cohort_sdk``'s teardown reddened NOTHING,
    leaving a per-run graph leaked per lane and per CI run — the same leak the
    name pin exists to prevent, one step earlier. This pins the call itself.

    Hermetic: the SDK and the drop are both doubled, so no server is touched and
    the fixture body runs end to end (construction, ``yield``, teardown).
    """
    import types

    import tortoise.sdk as sdk_mod

    module = sys.modules[__name__]
    dropped: list[tuple[object, str]] = []
    graph_name = "tortoise_test_abcdef1234"

    monkeypatch.setattr(module, "_cohort_uri",
                        lambda: (f"docker://probe/{graph_name}", graph_name))
    monkeypatch.setattr(module, "_live_utils", types.SimpleNamespace(
        drop_test_graph=lambda proj, *, name=None: dropped.append((proj, name))))

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            self.closed = False

        def _get_proj(self):
            return "THE-PROJECTION"

        def close(self):
            self.closed = True

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)

    with _cohort_sdk() as sdk:
        assert isinstance(sdk, _FakeSDK), "premise: the doubled SDK was built"
        assert dropped == [], "the graph was dropped BEFORE the fixture body ran"

    assert dropped == [("THE-PROJECTION", graph_name)], (
        "the fixture's teardown did not drop the graph it minted "
        f"(drops seen: {dropped!r}) — deleting that call leaks a per-run graph "
        "per lane and per CI run (#13290 review)")
    assert sdk.closed is True, (
        "the fixture's teardown did not close the SDK handle")


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


def _index_leg(sdk, query, limit=10, want: str = MECHANISM_INDEX):
    """One dense read through the index lane, with its trace entry.

    Retries until the store actually uses the INDEX: the engine's index build is
    asynchronous, and a read taken before it settles does not answer from the
    index, so it would certify nothing.

    ``want`` is the mechanism the caller is ABOUT to assert (round-3 review): a
    call that expects ``MECHANISM_SCAN_FALLBACK`` would otherwise never satisfy an
    ``MECHANISM_INDEX`` predicate and would burn the whole settle deadline before
    returning the very answer it was waiting to see.

    ⛔ THE RETRY KEYS ON ``MECHANISM_INDEX``, NOT ON ``"scan"`` (#13290 review
    F3, the same finding in four reviews). ``index_attempted`` is set ``True`` on
    EVERY non-embedded path — the index attempt, the measured-absent skip and
    the record-leg fall-through — so ``scan_mechanism`` is
    ``MECHANISM_SCAN_FALLBACK`` and ``MECHANISM_SCAN`` is **never** emitted
    here. A ``!= "scan"`` condition therefore returned on its FIRST iteration,
    always: the protection this docstring promises did not exist, and the very
    state it was written for (an index that has not settled) reached the
    ``MECHANISM_INDEX`` premise assert un-retried and redded on a loaded runner.
    ``test_the_settle_retry_actually_fires`` pins that the poll can fire.
    """
    deadline = time.monotonic() + _INDEX_SETTLE_TIMEOUT_S
    entry = None
    hits: list = []
    while True:
        reset_circuit_breakers()
        trace: list[dict] = []
        hits = run_vector_query(sdk._get_proj().g, query, limit=limit,
                               is_embedded=False, leg_trace=trace)
        entry = next(e for e in trace if e["leg"] == "vector")
        if entry.get("mechanism") == want or time.monotonic() > deadline:
            return dict(hits), entry
        time.sleep(_INDEX_SETTLE_POLL_S)


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

        hits, entry = _index_leg(sdk, near, want=MECHANISM_SCAN_FALLBACK)
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
