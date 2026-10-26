"""W4-b contentiousness-as-scored-signal tests (issue #2102, epic #2080).

S7 (ranking.py scoring surface) + S8 (batch EP reads) over both rankers:

* contested = has_ep AND variance > CONTESTED_VARIANCE_THRESHOLD (STRICT —
  exactly-at is NOT contested, just-above IS; uncalibrated (no persisted
  alpha/beta) is NOT contested);
* the scored boost applies ONLY under the W4 flag (TORTOISE_W4_ENRICHMENT)
  AND a query AND a conflict RELEVANT to the query (significant-token
  overlap with the NAND-side counter-claim, resolved in ONE batch read —
  plus one constant present-label resolution read since #7927, never one
  read per result);
* flag-off / no-query / irrelevant-conflict runs are byte-identical to the
  pre-W4-b ranking (R11 golden-ordering regression);
* E2E-1 ranking-participation: the contested twin outranks its uncontested
  twin on a conflict-relevant query (pre-assertion: the contested twin's
  PERSISTED variance exceeds the threshold before the ordering asserts).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

from tortoise.ranking import (
    CONTESTED_VARIANCE_THRESHOLD,
    W4_CONTESTED_BOOST,
    GraphRanker,
    StateRanker,
    _query_touches_conflict,
    _significant_tokens,
    resolve_contested_relevance,
)
from tortoise.sdk import TortoiseSDK


def _beta_variance(a: float, b: float) -> float:
    return (a * b) / ((a + b) ** 2 * (a + b + 1))


@pytest.fixture(autouse=True)
def _w4_flag_env_cleanup():
    """Env hygiene: the W4 flag is process-global — never leak it into
    sibling modules collected after this one in the same worker."""
    os.environ.pop("TORTOISE_W4_ENRICHMENT", None)
    yield
    os.environ.pop("TORTOISE_W4_ENRICHMENT", None)


# ── Unit: tokenization + relevance ────────────────────────────────────────

class TestConflictRelevance:
    def test_significant_tokens_min_length(self):
        assert _significant_tokens("the zebra finch cache") == {"zebra", "finch", "cache"}
        assert _significant_tokens("a b cd") == set()  # all below the floor

    def test_query_touches_conflict_shared_token(self):
        assert _query_touches_conflict(
            "zebra finch migration", ["flocking zebra finch behavior is social"]
        ) is True
        # A SINGLE generic overlap ("data", "study", "claim") must NOT fire —
        # generic queries would otherwise match enormous counter-claim
        # classes (second-model gate finding).
        assert _query_touches_conflict(
            "zebra migration data", ["social behavior shows otherwise"]
        ) is False
        # Exactly ONE overlapping token is still below the >= 2 gate.
        assert _query_touches_conflict(
            "zebra migration data", ["data shows the opposite"]
        ) is False
        # Stopword-only overlap never counts.
        assert _query_touches_conflict("the and or", ["the or and"]) is False
        assert _query_touches_conflict("zebra finch", []) is False
        assert _query_touches_conflict("", ["zebra"]) is False

    def test_relevance_is_directional_content(self):
        # The counter-claim content is what matters — a query sharing only
        # tokens with the CLAIM (not the counter-claim) is NOT relevant.
        assert _query_touches_conflict(
            "migration phenology", ["flocking behavior is social"]
        ) is False


class _FakeProjection:
    """Branch on the cypher: NAND query -> counter-claims; signal query ->
    canned rows.  Counts queries per cypher family for the N+1 guard."""

    def __init__(self, point_rows=None, counter_claims=None, nand_rows=None):
        self.point_rows = point_rows or []
        self.counter_claims = counter_claims or []  # [(pid, content)]
        self.nand_rows = nand_rows
        self.nand_query_count = 0
        self.signal_query_count = 0
        self.g = self  # rankers call projection.g.query(...)

    class _Rows:
        def __init__(self, rows):
            self.result_set = rows

    def query(self, cypher, params=None):
        # Discriminator: the resolution query is the adjacency MATCH
        # "(c:Point)-[r:NAND]->(n)"; signal queries use [r:IMPL|NAND].
        if "[r:NAND]" in cypher:
            self.nand_query_count += 1
            if self.nand_rows is not None:
                return self._Rows(self.nand_rows)
            # (pid, claim_id, claim_nid, content, alpha, beta) — mirror the
            # resolver's RETURN, INCLUDING the internal node id the resolver
            # dedupes on (the coalesced identity is not unique). Neutral
            # (1.0, 1.0) alpha/beta => medium severity.
            return self._Rows([
                (pid, f"{pid}-c{i}", 1000 + i, content, 1.0, 1.0)
                for i, (pid, content) in enumerate(self.counter_claims)
            ])
        self.signal_query_count += 1
        return self._Rows(self.point_rows)


def test_conflict_subset_is_not_decided_by_row_order_under_a_collision():
    """The budgeted subset must be deterministic even when claim identities
    COLLIDE.

    ``claim_id`` is not unique (``live.EPISTEMIC_ID_KEYS``: the guarded-merge
    suffix branch and the #8879 cross-key shape both collide), so a tie group
    of three claims sharing one id, with a budget of two, was resolved by
    Cypher ROW ORDER — the docstring promises it is not. Measured before the
    internal-id tiebreak: the same graph boosted forward and did not boost
    reversed.
    """
    from tortoise.ranking import resolve_contested_relevance

    class _Colliding:
        """Three claims, ONE shared ``claim_id``, distinct internal ids."""

        def __init__(self, order):
            self.order = order
            self.g = self

        class _Rows:
            def __init__(self, rows):
                self.result_set = rows

        def query(self, cypher, params=None):
            rows = [
                ("p1", "shared-claim", 1001, "the cache rollback is safer", 1.0, 1.0),
                ("p1", "shared-claim", 1002, "an unrelated weather note", 1.0, 1.0),
                ("p1", "shared-claim", 1003, "another unrelated note", 1.0, 1.0),
            ]
            if self.order == "reversed":
                rows = list(reversed(rows))
            return self._Rows(rows)

    fwd = resolve_contested_relevance(_Colliding("forward"), ["p1"], "cache rollback")
    rev = resolve_contested_relevance(_Colliding("reversed"), ["p1"], "cache rollback")
    assert fwd == rev, (
        "the boost depends on ROW ORDER — the subset is not deterministic: "
        f"forward={fwd} reversed={rev}")


def test_shared_claim_key_merges_commutatively():
    """Two ROWS may describe one node with DIFFERENT ``claim_id``.

    When ``src`` carries neither key, ``claim_nid`` is ``id(src)`` (non-null)
    while ``claim_id`` falls through to the NANDer's — so the same node can
    arrive with two claim ids, and that value is the SORT KEY's second
    component. Keeping "the first seen" therefore still let row order decide
    which claims fit the budget: measured, 12 of 24 row permutations boosted and
    12 did not. The merge is a total order over ``(mean, content, claim_id)``.
    """
    from tortoise.ranking import resolve_contested_relevance

    class _SharedNode:
        def __init__(self, order):
            self.order = order
            self.g = self

        class _Rows:
            def __init__(self, rows):
                self.result_set = rows

        def query(self, cypher, params=None):
            # One node (nid 1001) carries two claim ids that STRADDLE the two
            # unrelated claims in sort order; the budget is 2.
            rows = [
                ("p1", "a1", 1001, "the cache rollback is safer", 1.0, 1.0),
                ("p1", "z1", 1001, "the cache rollback is safer", 1.0, 1.0),
                ("p1", "c0", 1002, "an unrelated weather note", 1.0, 1.0),
                ("p1", "c15", 1003, "another unrelated note", 1.0, 1.0),
            ]
            if self.order == "reversed":
                rows = list(reversed(rows))
            return self._Rows(rows)

    fwd = resolve_contested_relevance(_SharedNode("forward"), ["p1"], "cache rollback")
    rev = resolve_contested_relevance(_SharedNode("reversed"), ["p1"], "cache rollback")
    assert fwd == rev, (
        "the merged survivor — and therefore the budgeted subset — depends on "
        f"ROW ORDER: forward={fwd} reversed={rev}")
    # ...and the survivor must be the one that KEEPS the relevant claim in the
    # budget. An order-stable merge that always discards it would satisfy the
    # equality above and be useless.
    assert fwd == {"p1": 0.05}, (
        "the merge is stable but always demotes the relevant claim out of the "
        f"budget: {fwd}")


def test_shared_claim_key_tolerates_a_mixed_type_claim_id():
    """A raw property id can be an int on an out-of-tree graph.

    Comparing two claim ids of different types raises, and the handler around
    the read turns that into an empty ``{}`` for EVERY contested point — a
    silent false negative. Both the merge and the sort key compare ``str()``.
    """
    from tortoise.ranking import resolve_contested_relevance

    class _Mixed:
        def __init__(self, order):
            self.order = order
            self.g = self

        class _Rows:
            def __init__(self, rows):
                self.result_set = rows

        def query(self, cypher, params=None):
            rows = [
                ("p1", 12345, 1001, "the cache rollback is safer", 1.0, 1.0),
                ("p1", "12345", 1001, "the cache rollback is safer", 1.0, 1.0),
                ("p1", "c0", 1002, "an unrelated weather note", 1.0, 1.0),
            ]
            if self.order == "reversed":
                rows = list(reversed(rows))
            return self._Rows(rows)

    fwd = resolve_contested_relevance(_Mixed("forward"), ["p1"], "cache rollback")
    rev = resolve_contested_relevance(_Mixed("reversed"), ["p1"], "cache rollback")
    assert fwd == rev, f"mixed-type claim ids split on row order: {fwd} vs {rev}"
    assert fwd == {"p1": 0.05}, (
        "a mixed-type claim id produced no boost at all — the read swallowed a "
        f"TypeError: {fwd}")


# ── Unit: batch relevance resolution (S8 N+1 guard) ────────────────────────

class TestBatchResolution:
    def test_single_batch_query_for_many_contested(self):
        """O(1) in the number of contested ids: the SAME read count for 1 id
        and for N ids — never one read per result."""
        one = _FakeProjection(counter_claims=[
            ("p1", "the cache rollback is the safer default"),
        ])
        resolve_contested_relevance(one, ["p1"], "cache rollback")
        assert one.nand_query_count == 1

        fake = _FakeProjection(counter_claims=[
            ("p1", "the cache rollback is the safer default"),
            ("p2", "cache rollback versioning adds nothing"),
            ("p3", "unrelated weather claim"),
        ])
        boosts = resolve_contested_relevance(fake, ["p1", "p2", "p3"], "cache rollback")
        # ONE batch read for N > 1 contested ids — never per-result. (#7927:
        # the present-label resolution is a separate constant read, and it is
        # what keeps this at one NAND read instead of the four-label sweep.)
        assert fake.nand_query_count == one.nand_query_count == 1
        assert boosts == {"p1": W4_CONTESTED_BOOST, "p2": W4_CONTESTED_BOOST}

    def test_no_ids_or_no_query_short_circuits(self):
        fake = _FakeProjection(counter_claims=[("p1", "cache rollback")])
        assert resolve_contested_relevance(fake, [], "cache") == {}
        assert resolve_contested_relevance(fake, ["p1"], "") == {}
        assert fake.nand_query_count == 0

    def test_defensive_on_query_failure(self):
        class _Boom:
            def query(self, cypher, params=None):
                raise RuntimeError("db down")

        assert resolve_contested_relevance(_Boom(), ["p1"], "cache") == {}


# ── Unit: strict threshold boundary + uncalibrated (S7) ───────────────────

# GraphRanker._fetch_point_signals row: (id, conf, degree, created, alpha,
# beta, has_ep) — alpha/beta are the COALESCED persisted values.
def _point_row(pid, a, b, has_ep=True, conf=0.9, degree=0):
    return (pid, conf, degree, "2026-01-01T00:00:00Z", a, b, has_ep)


class TestContestedFormula:
    def test_strict_threshold_boundary(self):
        # variance(2.625, 2.625) == 0.04 EXACTLY -> NOT contested (strict >).
        # variance(2.6, 2.6) ~ 0.04032 -> contested.
        exact = _beta_variance(2.625, 2.625)
        assert exact == pytest.approx(CONTESTED_VARIANCE_THRESHOLD, abs=1e-9)
        just_above = _beta_variance(2.6, 2.6)
        assert just_above > CONTESTED_VARIANCE_THRESHOLD
        fake = _FakeProjection(point_rows=[
            _point_row("at", 2.625, 2.625),
            _point_row("above", 2.6, 2.6),
            _point_row("low", 10.0, 10.0),
        ])
        ranker = GraphRanker(projection=fake)
        sigs = ranker._fetch_point_signals(["at", "above", "low"])
        assert sigs["at"]["contested"] is False   # exactly-at NOT contested
        assert sigs["above"]["contested"] is True  # just-above contested
        assert sigs["low"]["contested"] is False

    def test_uncalibrated_never_contested(self):
        # has_ep=False (no persisted alpha/beta) — unmeasured != contested
        # even with high variance.
        fake = _FakeProjection(point_rows=[
            _point_row("raw", 1.6, 1.6, has_ep=False),
        ])
        ranker = GraphRanker(projection=fake)
        sigs = ranker._fetch_point_signals(["raw"])
        assert sigs["raw"]["variance"] > CONTESTED_VARIANCE_THRESHOLD
        assert sigs["raw"]["contested"] is False


# ── GraphRanker: flag-gated contested boost (S7 / E2E-1 shape) ────────────

_TWIN_CONTENT = "zebra finch migration phenology contested claim"
_TWIN_IDS = ("twin_calm", "twin_hot")


def _twin_results() -> list[dict]:
    # Identical similarity + structure — the only difference is EP variance.
    return [
        {"id": _TWIN_IDS[0], "name": _TWIN_CONTENT, "similarity": 0.5},
        {"id": _TWIN_IDS[1], "name": _TWIN_CONTENT, "similarity": 0.5},
    ]


def _graph_ranker_fake(counter_claims) -> _FakeProjection:
    # twin_hot (2, 2) -> variance 0.05 > threshold -> contested; twin_calm
    # (10, 10) -> 0.0119 -> not.
    return _FakeProjection(
        point_rows=[
            _point_row("twin_calm", 10.0, 10.0),
            _point_row("twin_hot", 2.0, 2.0),
        ],
        counter_claims=counter_claims,
    )


class TestGraphRankerBoost:
    def test_flag_off_is_byte_identical_with_query(self):
        fake = _graph_ranker_fake([("twin_hot", "flocking zebra behavior is social")])
        ranker = GraphRanker(projection=fake)
        # autouse fixture guarantees the flag is unset here.
        base = ranker.rerank(_twin_results(), query="zebra finch migration")
        ranked = {r["id"]: r["graph_ranking"] for r in base}
        # Contested surfaced but NOT scored — identical final scores.
        assert ranked["twin_hot"]["contested"] is True
        assert ranked["twin_calm"]["contested"] is False
        assert ranked["twin_hot"]["final_score"] == ranked["twin_calm"]["final_score"]
        assert "w4_contested_boost" not in ranked["twin_hot"]

    def test_conflict_relevant_query_boosts_contested_twin(self, monkeypatch):
        fake = _graph_ranker_fake([("twin_hot", "flocking zebra behavior is social")])
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        # Pre-assertion (E2E-1): the contested twin's PERSISTED variance
        # exceeds the threshold before any ordering assertion.
        sigs = ranker._fetch_point_signals(list(_TWIN_IDS))
        assert sigs["twin_hot"]["variance"] > CONTESTED_VARIANCE_THRESHOLD
        ranked = ranker.rerank(_twin_results(), query="zebra finch social behavior")
        by_id = {r["id"]: r for r in ranked}
        assert by_id["twin_hot"]["graph_ranking"]["w4_contested_boost"] == W4_CONTESTED_BOOST
        # The contested twin OUTRANKS its uncontested twin.
        assert ranked[0]["id"] == "twin_hot"
        assert by_id["twin_hot"]["graph_ranking"]["final_score"] > by_id["twin_calm"]["graph_ranking"]["final_score"]

    def test_no_query_never_boosts_even_with_flag(self, monkeypatch):
        fake = _graph_ranker_fake([("twin_hot", "flocking zebra behavior is social")])
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        ranked = ranker.rerank(_twin_results())
        assert fake.nand_query_count == 0  # no query -> no relevance read at all
        by_id = {r["id"]: r["graph_ranking"] for r in ranked}
        assert by_id["twin_hot"]["final_score"] == by_id["twin_calm"]["final_score"]
        assert "w4_contested_boost" not in by_id["twin_hot"]

    def test_flag_on_resolution_is_one_batch_read(self, monkeypatch):
        """S8 at the rerank level: N contested ids resolve in exactly ONE
        resolution query under flag-on + query; the signal fetch is the
        ranker's own (not counted here)."""
        fake = _graph_ranker_fake([
            ("twin_hot", "flocking zebra behavior is social"),
            ("twin_hot", "another cache claim"),
        ])
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        ranker.rerank(_twin_results(), query="zebra finch social behavior")
        assert fake.nand_query_count == 1

    def test_budget_cap_skips_claims_beyond_why_budget(self, monkeypatch):
        """The resolution budget mirrors why.py's W4_MAX_CONFLICTS: a
        relevance match living at claim position 3+ never fires the boost
        (the canonical why-layer would not surface it either)."""
        fake = _graph_ranker_fake([
            ("twin_hot", "unrelated weather front claim"),
            ("twin_hot", "another irrelevant thesis"),
            ("twin_hot", "flocking zebra finch behavior is social"),  # 3rd
        ])
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        ranked = ranker.rerank(_twin_results(), query="zebra finch social behavior")
        by_id = {r["id"]: r["graph_ranking"] for r in ranked}
        assert "w4_contested_boost" not in by_id["twin_hot"]
        assert by_id["twin_hot"]["final_score"] == by_id["twin_calm"]["final_score"]

    def test_severity_selection_mirrors_why_budget(self, monkeypatch):
        """P2-2 (round-3): the budgeted subset mirrors why.py's severity-
        then-id selection.  A HIGH-severity NANDer (persisted EP mean >=
        0.6) is always chosen before MEDIUM ones — Cypher row order never
        decides which counter-claims are tested for relevance."""
        class _SevFake(_FakeProjection):
            pass

        # twin_hot has 3 NANDers: high-severity irrelevant (id "zz"),
        # medium irrelevant ("aa"), medium RELEVANT ("bb").  Severity-first
        # selection picks [zz(high), aa] — the relevant bb at position 3 is
        # outside the W4_MAX_CONFLICTS budget, exactly as why.py would
        # surface it.  Without severity-first ordering, [aa, bb] would fire.
        fake = _SevFake(
            point_rows=[
                _point_row("twin_calm", 10.0, 10.0),
                _point_row("twin_hot", 2.0, 2.0),
            ],
            nand_rows=[
                ("twin_hot", "zz-high", "unrelated weather front claim", 3.0, 1.0),
                ("twin_hot", "aa-med", "another irrelevant thesis", 1.0, 1.0),
                ("twin_hot", "bb-med", "flocking zebra finch behavior is social", 1.0, 1.0),
            ],
        )
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        ranked = ranker.rerank(_twin_results(), query="zebra finch social behavior")
        by_id = {r["id"]: r["graph_ranking"] for r in ranked}
        assert "w4_contested_boost" not in by_id["twin_hot"]
        assert fake.nand_query_count == 1

    def test_graph_boost_cap_keeps_invariant_at_max(self, monkeypatch):
        """The [0, 1] graph_boost invariant: a contested point near the
        structural cap (conf 1.0 + very high connectivity => gb 0.975) has
        the W4-b increment CLAMPED (0.975 + 0.05 -> 1.0, not 1.025) — the
        boost becomes partially inert above the cap."""
        class _CapFake(_FakeProjection):
            pass

        # connectivity = 1 - 1/(1+degree): degree 19 -> 0.95 -> gb =
        # 0.5*1.0 + 0.5*0.95 = 0.975.  Unbounded the boost would take the
        # contested twin to 1.025; the clamp holds it at 1.0.
        fake = _CapFake(
            point_rows=[
                _point_row("twin_calm", 10.0, 10.0, conf=1.0, degree=19),
                _point_row("twin_hot", 2.0, 2.0, conf=1.0, degree=19),
            ],
            counter_claims=[("twin_hot", "flocking zebra finch behavior is social")],
        )
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        ranked = ranker.rerank(_twin_results(), query="zebra finch social behavior")
        by_id = {r["id"]: r["graph_ranking"] for r in ranked}
        # Clamped: the contested twin holds at exactly 1.0 (not 1.025).
        assert by_id["twin_hot"]["graph_boost"] == 1.0
        assert by_id["twin_calm"]["graph_boost"] == 0.975
        # Partially inert: the surviving delta is 0.35 * (1.0 - 0.975),
        # not the full 0.35 * 0.05 the delta-causality test pins at low cap.
        assert by_id["twin_hot"]["final_score"] - by_id["twin_calm"]["final_score"] == pytest.approx(
            0.35 * 0.025, abs=1e-4)

    def test_non_str_counter_claim_never_raises(self, monkeypatch):
        """Fail-open contract: raw-graph writes can leave a non-str
        content/label on a NANDer — the resolver coerces and returns boosts
        instead of crashing the recall/search turn."""
        class _RawFake(_FakeProjection):
            pass

        fake = _RawFake(
            point_rows=[
                _point_row("twin_calm", 10.0, 10.0),
                _point_row("twin_hot", 2.0, 2.0),
            ],
            counter_claims=[("twin_hot", 12345)],  # non-str raw content
        )
        ranker = GraphRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        ranked = ranker.rerank(_twin_results(), query="zebra finch social behavior")
        # No crash; the non-str claim matches nothing and the boost is inert.
        assert fake.nand_query_count == 1
        assert all("w4_contested_boost" not in r["graph_ranking"] for r in ranked)

    def test_irrelevant_conflict_is_ranked_noise_free(self, monkeypatch):
        """S7 negative: the query touches the point (it matched retrieval)
        but NOT the conflict — the counter-claim shares no significant token
        -> ordering identical to the flag-off baseline (no boost on an
        irrelevant conflict)."""
        fake = _graph_ranker_fake(
            [("twin_hot", "flocking aggregations follow weather fronts")])
        ranker = GraphRanker(projection=fake)
        baseline = ranker.rerank(_twin_results(), query="zebra finch migration")
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        boosted_try = ranker.rerank(_twin_results(), query="zebra finch migration")
        base_map = {r["id"]: r["graph_ranking"] for r in baseline}
        flag_map = {r["id"]: r["graph_ranking"] for r in boosted_try}
        assert [r["id"] for r in baseline] == [r["id"] for r in boosted_try]
        assert flag_map["twin_hot"]["final_score"] == base_map["twin_hot"]["final_score"]
        assert "w4_contested_boost" not in flag_map["twin_hot"]


# ── StateRanker: multiplicative boost under the flag ───────────────────────

class TestStateRankerBoost:
    def test_state_ranker_boosts_relevant_contested(self, monkeypatch):
        class _StateFake(_FakeProjection):
            pass

        # StateRanker._fetch_point_signals row shape: (id, alpha, beta,
        # has_ep, confidence, degree, created).
        fake = _StateFake(
            point_rows=[
                ("twin_calm", 10.0, 10.0, True, 0),
                ("twin_hot", 2.0, 2.0, True, 0),
            ],
            counter_claims=[("twin_hot", "flocking zebra behavior is social")],
        )
        ranker = StateRanker(projection=fake)
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
        results = [
            {"id": "twin_calm", "entity_type": "point", "confidence": 0.9},
            {"id": "twin_hot", "entity_type": "point", "confidence": 0.9},
        ]
        ranked = ranker.rerank(results, query="zebra finch social behavior")
        by_id = {r["id"]: r["recall_ranking"] for r in ranked}
        assert by_id["twin_hot"]["contested"] is True
        assert ranked[0]["id"] == "twin_hot"
        assert by_id["twin_hot"]["w4_contested_boost"] == W4_CONTESTED_BOOST

    def test_state_ranker_flag_off_unchanged(self):
        fake = _FakeProjection(
            point_rows=[
                ("twin_calm", 10.0, 10.0, True, 0),
                ("twin_hot", 2.0, 2.0, True, 0),
            ],
            counter_claims=[("twin_hot", "flocking zebra behavior is social")],
        )
        ranker = StateRanker(projection=fake)
        results = [
            {"id": "twin_calm", "entity_type": "point", "confidence": 0.9},
            {"id": "twin_hot", "entity_type": "point", "confidence": 0.9},
        ]
        ranked = ranker.rerank(results, query="zebra finch social behavior")
        by_id = {r["id"]: r["recall_ranking"] for r in ranked}
        assert by_id["twin_hot"]["final_score"] == by_id["twin_calm"]["final_score"]
        assert "w4_contested_boost" not in by_id["twin_hot"]


# ── Integration: real graph, E2E-1 ranking participation (embedded) ───────
#
# Leg precondition: the ORDERED claim (contested outranks its twin) is a
# single-leg claim and is pinned sparse via ``force_sparse_tfidf``.  The other
# integration tests here assert boost presence/absence, not an order between
# content-identical twins, and are leg-count-agnostic by construction.

def _wipe(sdk):
    sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")


@pytest.fixture()
def ranked_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_w4b_"), "test.db")
    sdk = TortoiseSDK(db_path)
    _wipe(sdk)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _seed_twin_conflict(sdk, counter_content: str) -> tuple[str, str, str]:
    """Twin claim points (identical content, different persisted EP
    variance) + a NAND operator from a counter-claim into the contested
    twin (the canonical conflict shape why.py resolves).

    The calm twin receives a PARITY NAND from an unrelated point so both
    twins have identical operator-connectivity: the only difference that
    can separate them is EP variance (and the W4-b boost under it).

    TWO premises come out of the mint order, and both are asserted here
    rather than assumed downstream (a ranking assertion must not rest on a
    silent fixture accident):

    * ``ulid()`` is ``<ms-hex>-<random>``, so the twins' id order — and with
      it which twin #3019's ``ORDER BY score DESC, node.<id> ASC`` tie-break
      hands rank 0 — is decided by the millisecond each was minted in.  The
      mint below steps across a millisecond boundary (2 ms) so a same-ms
      collision cannot hand the tie-break to 48 bits of randomness.
    * the STAND-IN embedder's vectors are hash-seeded
      (``force_embedder_on``: "assertions must never depend on the vectors'
      VALUES"), so WHICH filler row lands between the twins on a small fused
      page is a property of this fixture's texts, not of the mechanism.  A
      test that quotes a number depending on that span names it as a premise.
    """
    calm = sdk.create_point("statement", _TWIN_CONTENT)
    time.sleep(0.002)  # cross an ulid() millisecond boundary — see docstring
    hot = sdk.create_point("statement", _TWIN_CONTENT)
    assert calm["id"] < hot["id"], (
        f"the calm twin must hold the LOWER ULID (#3019's id tie-break is "
        f"what hands it rank 0, and every span assertion reads that way "
        f"round): calm {calm['id']} vs hot {hot['id']} — regenerate the "
        f"fixture rather than re-deriving the numbers")
    cc = sdk.create_point("statement", counter_content)
    cc_parity = sdk.create_point(
        "statement", "regional weather fronts shift whole bird populations")
    proj = sdk._get_proj()
    for pid, a, b in [(calm["id"], 10.0, 10.0), (hot["id"], 2.0, 2.0)]:
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence = 0.9, "
            "n.posterior_alpha = $a, n.posterior_beta = $b, "
            "n.ep_alpha = $a, n.ep_beta = $b",
            params={"id": pid, "a": a, "b": b},
        )
    # NAND operators: counter-claim -> contested twin; parity point -> calm.
    sdk.create_operator("NAND", cc["id"], [hot["id"]], direction="unidirectional")
    sdk.create_operator("NAND", cc_parity["id"], [calm["id"]], direction="unidirectional")
    return calm["id"], hot["id"], cc["id"]


def test_integration_recall_state_surface_boosts_contested_only(ranked_sdk, monkeypatch):
    """P2-2 (review): the recall_state surface threads the query — the
    epic's main path — so an E2E through it must show the contested boost on
    the contested POINT and its absence on OBJECT rows (objects aggregate
    means; they are never contested, never boosted)."""
    calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "zebra finch flocking behavior is social, not migratory")
    obj = ranked_sdk.create_object("zebra finch habitat", "concept")
    proj = ranked_sdk._get_proj()
    proj.g.query(
        "MATCH (p:Point {id:$id}) MATCH (o:Object {id:$oid}) "
        "CREATE (p)-[:aboutObject]->(o)",
        params={"id": hot, "oid": obj["id"]},
    )
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    out = ranked_sdk.recall_state("zebra finch social behavior", limit=20)
    recs = {r["id"]: r.get("recall_ranking", {}) for r in out}
    assert hot in recs and calm in recs
    assert recs[hot]["w4_contested_boost"] == W4_CONTESTED_BOOST
    assert "w4_contested_boost" not in recs[calm]
    obj_ids = [r["id"] for r in out if r.get("entity_type") == "object"]
    for oid in obj_ids:
        assert "w4_contested_boost" not in recs[oid]


def test_integration_conflict_relevant_query_outranks_twin(
    ranked_sdk, monkeypatch, force_sparse_tfidf,
):
    """The contested twin outranks its twin **on a pinned single-leg read**.

    ``force_sparse_tfidf`` (tests/conftest.py, the #2573/#2772 pattern) is
    NOT cosmetic here — it is the fixture's precondition.  The ordered claim
    below needs the two content-identical twins to have EXACTLY equal
    retrieval relevance, so the W4-b boost is the only differentiator.  Only
    the sparse leg guarantees that.  With a dense leg present the SDK FUSES
    (``len(raw_results) > 1``, ``match_source="rrf"``); both legs tie,
    each leg's own ``ORDER BY score, id ASC`` tie-break ranks the lower-ULID
    twin first, and that one-rank RRF difference is worth MORE than the
    boost — so the ordering claim is false, not flaky (#7704, measured on the
    relevance/final-score basis the boost effect is on: fused gap 0.0189 of
    final score — the 0.0378 similarity gap x the 0.5 similarity weight —
    > boost contribution 0.0175).

    The `match_source` assertion below makes the path the test actually
    measured part of the contract: revert the pin and the fused read FAILS
    the guard with a message that names the cause, instead of the opaque
    ``assert 1 < 0`` this test used to emit at the rail (#7629).
    """
    calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "zebra finch flocking behavior is social, not migratory")
    proj = ranked_sdk._get_proj()
    # Pre-assertion: the contested twin's PERSISTED variance exceeds the
    # threshold before ordering assertions (calibrated, not aspirational).
    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN "
        "coalesce(n.posterior_alpha, n.ep_alpha), coalesce(n.posterior_beta, n.ep_beta)",
        params={"id": hot},
    ).result_set[0]
    assert _beta_variance(float(row[0]), float(row[1])) > CONTESTED_VARIANCE_THRESHOLD

    off = _run_fts_graph(ranked_sdk, flag_on=False)
    by_off = {r["id"]: r["graph_ranking"] for r in off}
    # Causality pre-assertion: the boost, NOT a structural or retrieval
    # artifact, must be the differentiator — so first pin the flag-off
    # baseline score of both twins (retrieval min-max noise is identical
    # on/off because the pool and similarities do not change).
    hot_off = by_off[hot]["final_score"]
    calm_off = by_off[calm]["final_score"]

    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    results = ranked_sdk.tortoise_fts_query(
        "zebra finch migration", limit=10, order_by="graph")
    ids = [r["id"] for r in results]
    assert hot in ids and calm in ids
    by_id = {r["id"]: r for r in results}
    on_hot = by_id[hot]["graph_ranking"]
    on_calm = by_id[calm]["graph_ranking"]
    # Fixture precondition, ASSERTED (#7629): the pinned embedder-absent lane
    # yields ONE leg, so the SDK's single-list shortcut (sdk.py:17798)
    # bypasses rrf_fusion and the twins have exactly equal relevance.  A
    # fused read (match_source="rrf") would make the ordering claim below
    # invalid — see the docstring and #7704 — so state which path this test
    # measured instead of inheriting it from ambient embedder availability.
    assert {r.get("match_source") for r in results} == {"fts"}, (
        "fixture precondition changed: the ordered claim below is a "
        "single-leg (sparse) claim and does NOT hold on a fused read "
        "(#7704) — see force_sparse_tfidf")
    # Causal assertions FIRST (they hold on both paths): if this test ever
    # fails, say whether the boost failed to apply or the order ignored it —
    # the previous order reported only `assert 1 < 0` for either cause
    # (#7629).
    assert on_hot["w4_contested_boost"] == W4_CONTESTED_BOOST
    assert on_hot["final_score"] > on_calm["final_score"]
    # Causal delta: flag-on moves the contested twin UP by exactly the
    # boost effect (W4_CONTESTED_BOOST x graph_boost_weight = 0.0175 in
    # final-score space) and leaves the uncontested twin untouched.  If the
    # boost were inert, hot_final(on) == hot_off and the ordering assert
    # below could pass on retrieval noise alone.
    assert on_calm["final_score"] == calm_off  # calm never boosted
    assert on_hot["final_score"] - hot_off == pytest.approx(
        W4_CONTESTED_BOOST * 0.35, abs=2e-3)
    # Conflict-relevant query (the counter-claim shares "zebra"+"finch") =>
    # the contested twin OUTRANKS its uncontested twin on the pinned
    # single-leg read.
    assert ids.index(hot) < ids.index(calm)


def _run_fts_graph(sdk, flag_on: bool, monkeypatch=None):
    if flag_on:
        assert monkeypatch is not None
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    else:
        os.environ.pop("TORTOISE_W4_ENRICHMENT", None)
    return sdk.tortoise_fts_query(
        "zebra finch migration", limit=10, order_by="graph")


def test_integration_fused_path_applies_contested_boost(
    ranked_sdk, monkeypatch, force_embedder_on,
):
    """FUSED-path coverage (#7704): the SDK FUSES, and the boost still applies.

    ``test_integration_conflict_relevant_query_outranks_twin`` pins the ORDER
    on a single-leg read (``force_sparse_tfidf``).  This is the other path: a
    dense leg is present (``force_embedder_on``, the #3223 seam), so
    ``len(raw_results) > 1``, the SDK calls ``rrf_fusion``, and the result set
    reports ``match_source="rrf"``.  Before this test the fused path — the
    SHIPPED hybrid path — had no W4-b coverage at all (#7629, #7704).

    The ORDERING claim is deliberately NOT asserted here.  The twins are
    content-identical, so they TIE on every leg; each leg's own ``ORDER BY
    score DESC, node.<id> ASC`` tie-break (#3019) hands the lower-ULID twin
    rank 0, and RRF (rank-based, ``1/(k+rank+1)``) converts that one-rank
    difference into a post-fusion min-max similarity gap that can EXCEED the
    boost's final-score contribution.  Measured on this fixture: similarity
    gap 0.0378 => 0.0189 of final score, vs the boost's 0.0175 — so the
    contested twin ranks BELOW its twin on this path and the outcome is
    decided by the id tie-break, not the contested signal (#7704).  That gap
    is a product decision (boost magnitude / fusion tie semantics) and is
    tracked on #7704; this test pins the path-independent contract instead.

    What IS asserted, on the fused path: the twins tie on every leg (the
    precondition above), the contested twin carries exactly
    ``W4_CONTESTED_BOOST``, its final score moves by exactly
    ``W4_CONTESTED_BOOST x graph_boost_weight``, and the calm twin is
    untouched.
    """
    calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "zebra finch flocking behavior is social, not migratory")
    off = _run_fts_graph(ranked_sdk, flag_on=False)
    by_off = {r["id"]: r for r in off}
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    results = ranked_sdk.tortoise_fts_query(
        "zebra finch migration", limit=10, order_by="graph")
    by_id = {r["id"]: r for r in results}
    assert hot in by_id and calm in by_id, (
        f"FIXTURE NOT REACHED: a twin is missing from the fused pool "
        f"{sorted(by_id)}")
    # Say which path this test measured (#7629's lesson): the fused path is the
    # one under test here, and it must not silently degrade to single-leg.
    assert {r.get("match_source") for r in results} == {"rrf"}, (
        "fixture precondition changed: this test is the FUSED-path coverage "
        "(#7704) and only means something when the SDK actually fuses — the "
        "single-leg claim is covered by "
        "test_integration_conflict_relevant_query_outranks_twin")
    # Precondition the #7704 divergence rests on: the content-identical twins
    # tie on EVERY leg, so any fused relevance difference between them is the
    # legs' id tie-break, not a relevance signal.  If a leg stops tying, this
    # test no longer exercises the mechanism it is named for.
    leg_scores = [
        {k: r["scores"].get(k) for k in ("fts", "vector")}
        for r in (by_id[calm], by_id[hot])
    ]
    assert leg_scores[0] == leg_scores[1], (
        f"the twins no longer tie on every leg, so this test no longer "
        f"exercises the #7704 mechanism: {leg_scores}")
    on_hot = by_id[hot]["graph_ranking"]
    on_calm = by_id[calm]["graph_ranking"]
    # Causal assertions (path-independent — they hold on both the single-leg
    # and the fused read): the boost applied, and only to the contested twin.
    assert on_hot["w4_contested_boost"] == W4_CONTESTED_BOOST
    assert "w4_contested_boost" not in on_calm
    assert on_calm["final_score"] == by_off[calm]["graph_ranking"]["final_score"]
    assert on_hot["final_score"] - by_off[hot]["graph_ranking"]["final_score"] == pytest.approx(
        W4_CONTESTED_BOOST * 0.35, abs=2e-3)


def test_integration_boost_requirement_is_bounded_by_the_page(
    ranked_sdk, monkeypatch, force_embedder_on,
):
    """#7704 remedy (2): the required boost is BOUNDED — the CLAMP is the blocker.

    The rationale comment on ``W4_CONTESTED_BOOST`` used to say the required
    magnitude "is unbounded as the page shrinks", which is self-contradictory
    and forecloses a legitimate remedy: ``_min_max_normalize`` maps a PAGE into
    [0, 1] and a page cannot hold fewer than the two rows being ordered, so the
    requirement SATURATES on a 2-row page instead of growing without bound.
    The two consequences a reader needs are measured here, not restated:

    * the requirement FALLS as the page grows (flat while the page still
      spans the twins, then down to the default page's 0.0540) — and the
      repair that follows is RUN, not inferred: a raise inside the clamp's
      headroom flips the shipped default page (``ten_req < two_req`` alone
      would NOT imply that, so the headroom comparison is asserted too), and
      "raising the boost does not repair this" was true only of the smallest
      pages;
    * on a 2-row page whose contested row is the LESS similar one — the bottom
      of the span, ``d_sim == +1``, the placement asserted below and the case
      this boost exists for — it needs MORE than the clamp's headroom
      ``1.0 - baseline graph_boost``, so no ``cb`` inside the documented
      [0, 1] invariant can clear it.  Shown directly: raise
      ``W4_CONTESTED_BOOST`` far past the clamp and the graph term pins at 1.0
      and the ordering still loses.

    Both pages are the REAL ranker over REAL rows of this fixture.  The default
    page is the retrieved fused page; the 2-row page is the ``result_ids[:2]``
    page the SDK hands the ranker when the twins are the whole page — built
    from the same rows rather than queried, because which rows a 2-row fused
    page holds is an embedder property, while the page's SIZE is what the bound
    depends on.  Expectations are DERIVED from the payloads through the
    ranker's own weights, so this asserts the mechanism and not a constant.
    """
    from tortoise.ranking import (
        DEFAULT_GRAPH_BOOST_WEIGHT,
        DEFAULT_RECENCY_WEIGHT,
        DEFAULT_SIMILARITY_WEIGHT,
        _min_max_normalize,
        _similarity_of,
    )

    calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "zebra finch flocking behavior is social, not migratory")
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    query = "zebra finch migration"
    ranker = GraphRanker(projection=ranked_sdk._get_proj())

    def required_cb(rows: list[dict]) -> tuple[float, dict, dict, list[str]]:
        """Total ``cb`` that would bring the contested twin level with its
        twin, read off this page's payloads: strip the boost that was ACTUALLY
        applied back out — the boost is the only thing separating the twins'
        structure — and divide what is left by the graph weight.

        The applied increment is read OFF THE PAYLOAD (the contested row's
        post-boost ``graph_boost`` minus its own query-less baseline), never
        from ``W4_CONTESTED_BOOST``: inside the ``cb=2.0`` block the clamp
        delivers only ``1.0 - 0.7 = 0.3`` of the request, so a helper that
        stripped the requested magnitude (2.0) or the shipped one (0.05) would
        return ``(gap + 0.7)/0.35`` instead of the level condition.
        """
        page = ranker.rerank(rows, query=query)
        gr = {r["id"]: r["graph_ranking"] for r in page}
        assert hot in gr and calm in gr, (
            f"the twins are not both on this page: {sorted(gr)}")
        h, c = gr[hot], gr[calm]
        applied = h["graph_boost"] - baseline_graph_boost(rows)[1]
        gap = c["final_score"] - (
            h["final_score"] - applied * DEFAULT_GRAPH_BOOST_WEIGHT)
        return (gap / DEFAULT_GRAPH_BOOST_WEIGHT, h, c,
                [r["id"] for r in page])

    def baseline_graph_boost(rows: list[dict]) -> tuple[float, float]:
        """The twins' PRE-boost ``graph_boost`` on this page: ``(calm, hot)``.

        Read from a query-less rerank — no query means no contested boost at
        all, so the payload's ``graph_boost`` is exactly the baseline the
        clamp is applied to."""
        gr = {r["id"]: r["graph_ranking"] for r in ranker.rerank(rows)}
        return gr[calm]["graph_boost"], gr[hot]["graph_boost"]

    def derived_cb(h: dict, c: dict, gb_calm: float, gb_hot: float) -> float:
        """The ``cb`` the comment's formula predicts for this page, from the
        payloads alone:

            cb_req = (sim_w * Δsim + graph_w * Δgb + rec_w * Δrec) / graph_w

        with Δ = calm − hot.  The ``ceiling`` in the caller bounds the
        ``graph_w * Δgb`` term with ``|Δgb|`` and is valid for any ``Δgb``; what
        assumes ``Δgb == 0`` is the 1.857 it is pinned to.  So the expectation
        here is derived with the term PRESENT and the parity is asserted
        separately, instead of being assumed by the band."""
        return (
            DEFAULT_SIMILARITY_WEIGHT * (c["similarity"] - h["similarity"])
            + DEFAULT_GRAPH_BOOST_WEIGHT * (gb_calm - gb_hot)
            + DEFAULT_RECENCY_WEIGHT * (c["recency_boost"] - h["recency_boost"])
        ) / DEFAULT_GRAPH_BOOST_WEIGHT

    def unrounded_preboost_gap(rows: list[dict]) -> float:
        """The twins' PRE-boost relevance gap, in FINAL-SCORE units, recomputed
        from the UNROUNDED input ``rerank`` consumes rather than from the 4-dp
        payload it reports.

        ``rerank`` rounds every field it publishes (``similarity``,
        ``graph_boost``, ``recency_boost``, ``final_score``) to 4 dp, so a gap
        read as a difference of two reported finals carries ~1.4e-4 — wider
        than the 1e-4 that separates the comment's 0.0189 from the 0.0188 an
        earlier wording quoted.  A payload-basis read therefore CANNOT settle
        which of the two the comment is entitled to quote, however tight its
        band is made; the raw ``scores.rrf`` values in these row dicts are not
        rounded, so the min-max span is recomputed here exactly as ``rerank``
        computed it.

        ``sim_w * d_sim`` IS the whole level condition once ``d_gb`` and
        ``d_rec`` are zero, and the caller asserts both by name on the page it
        applies this to — so this is the level condition on that page, not a
        subset of it.
        """
        norm = _min_max_normalize([_similarity_of(r) for r in rows])
        ns = {r["id"]: value for r, value in zip(rows, norm, strict=True)}
        return DEFAULT_SIMILARITY_WEIGHT * (ns[calm] - ns[hot])

    # ── The shipped default page, retrieved through the fused path ────────
    res = ranked_sdk.tortoise_fts_query(query, limit=10, order_by="graph")
    assert {r.get("match_source") for r in res} == {"rrf"}, (
        "this test is about the FUSED read; it must not degrade to single-leg")
    by_id = {r["id"]: r for r in res}
    assert hot in by_id and calm in by_id, "FIXTURE NOT REACHED: a twin is off the page"
    # #7704's precondition: the content-identical twins tie on EVERY leg.
    assert ({k: by_id[calm]["scores"].get(k) for k in ("fts", "vector")}
            == {k: by_id[hot]["scores"].get(k) for k in ("fts", "vector")})
    two_row = [dict(by_id[hot]), dict(by_id[calm])]
    # The comment JUSTIFIES building the 2-row page from the fused rows by
    # saying the SDK's own limit=2 read does not hold both twins.  Assert the
    # premise: if it ever does, the constructed page is no longer the honest
    # stand-in for a 2-row read.
    res2 = ranked_sdk.tortoise_fts_query(query, limit=2, order_by="graph")
    assert not {hot, calm} <= {r["id"] for r in res2}, (
        f"the comment says the SDK's own limit=2 read does not hold both "
        f"twins; it now does ({[r['id'] for r in res2]}) — re-derive the "
        f"paragraph instead of re-describing it")
    ten_req, ten_hot, ten_calm, _tids = required_cb([dict(r) for r in res])

    # ── Worst case: a 2-row page (a page cannot be smaller) ───────────────
    two_req, two_hot, two_calm, two_ids = required_cb(two_row)
    two_gb_calm, two_gb_hot = baseline_graph_boost(two_row)
    assert len(two_ids) == 2
    # The whole normalization span is consumed (order-free: that is the bound).
    assert {two_hot["similarity"], two_calm["similarity"]} == {0.0, 1.0}
    # …and the CONTESTED twin must be the 0 end — the end the boost has to lift.
    # That is a FIXTURE premise, not a claim about the ranker: #3019's per-leg
    # ``ORDER BY score, id ASC`` hands rank 0 to the lower-ULID twin, so the
    # mint order in ``_seed_twin_conflict`` decides which twin lands at 0.  A
    # same-millisecond ULID collision can flip it; if this fires, regenerate
    # the fixture rather than re-deriving the comment's 1.4286.
    assert two_hot["similarity"] == 0.0, (
        f"FIXTURE PREMISE BROKEN: the contested twin must sit at the 0 end of "
        f"the normalization span (it has {two_hot['similarity']}, calm has "
        f"{two_calm['similarity']}). #3019's tie-break mints the lower-ULID "
        f"twin at rank 0, so the twins' mint order flipped — the page still "
        f"spans {{0,1}}, but this fixture no longer measures the comment's "
        f"1.4286 requirement")

    # ── The PREMISE the quoted 1.4286 rests on, asserted BY NAME ──────────
    # ``ceiling`` below bounds the ``graph_w * Δgb`` term with ``|Δgb|``, which
    # tracks this page's own placement; the 1.857 it is pinned to is legitimate
    # only because the twins share a baseline graph_boost — the parity NAND in
    # ``_seed_twin_conflict`` is what makes them.  The parity is asserted here
    # BY NAME so a break reports its cause: dropping that NAND takes calm to
    # degree 0 (gb 0.45, Δgb −0.25, the 2-row requirement 1.1786), which the
    # band assertions below also fail — but their messages point at the comment
    # rather than at the fixture this assertion names.
    assert two_gb_calm == two_gb_hot == pytest.approx(0.7), (
        f"the fixture no longer gives both twins the same 0.7 baseline "
        f"graph_boost the comment quotes (calm {two_gb_calm}, hot "
        f"{two_gb_hot}) — the 1.4286 requirement is derived from that parity")
    assert two_calm["recency_boost"] == two_hot["recency_boost"], (
        f"the comment's 1.4286 also assumes equal recency: calm "
        f"{two_calm['recency_boost']} vs hot {two_hot['recency_boost']}")

    # The mechanism, evaluated on THIS page's payloads (all three terms, the
    # graph delta included), reproduces the measured requirement.
    assert two_req == pytest.approx(
        derived_cb(two_hot, two_calm, two_gb_calm, two_gb_hot), abs=5e-3), (
        f"the measured 2-row requirement {two_req:.4f} is not what the "
        f"formula predicts from this page's payloads — the derivation in "
        f"W4_CONTESTED_BOOST's comment is stale")
    # With Δgb = Δrec = 0 the only term left is the full similarity span, which
    # is exactly the number the comment quotes: (0.5 * 1) / 0.35 = 1.4286.
    assert two_req == pytest.approx(
        DEFAULT_SIMILARITY_WEIGHT / DEFAULT_GRAPH_BOOST_WEIGHT, abs=5e-3), (
        f"the comment quotes a 2-row requirement of 1.4286; this page measures "
        f"{two_req:.4f}")
    # The CEILING carries the graph term the comment's formula has:
    #   cb_req = (sim_w*d_sim + graph_w*d_gb + rec_w*d_rec) / graph_w
    #          <= (sim_w + graph_w*|d_gb| + rec_w) / graph_w   (page in [0, 1])
    # |d_gb| is read off THIS page, so the band tracks the page instead of
    # restating two weights: 0 here (the parity premise asserted above), which
    # is the comment's 1.857; 2.857 at |d_gb| = 1, the general ceiling the
    # comment now names.
    #
    # There is deliberately NO matching ``floor`` assertion.  Δsim == 1 and
    # Δgb == 0 are BOTH asserted above, so the formula reduces to
    # ``1.4286 + 0.4286*Δrec`` and ``two_req >= (sim_w - rec_w)/graph_w == 1.0``
    # follows from Δrec >= -1 — a check that cannot fail, whose stale-comment
    # message would then fire only when the real cause was a broken premise.
    # The bound that is genuinely NOT entailed by those premises is the clamp's
    # headroom, and that is asserted as the guarantee further down.
    ceiling = (
        DEFAULT_SIMILARITY_WEIGHT
        + DEFAULT_GRAPH_BOOST_WEIGHT * abs(two_gb_calm - two_gb_hot)
        + DEFAULT_RECENCY_WEIGHT
    ) / DEFAULT_GRAPH_BOOST_WEIGHT
    assert two_req <= ceiling + 1e-6, (
        f"the 2-row requirement is {two_req:.4f}, ABOVE its ceiling "
        f"{ceiling:.4f} — then it is not bounded by the page after all")
    # A RATIO pin rather than a payload read.  The pinned quantity is
    # ``(sim_w + rec_w)/graph_w`` with ``|d_gb| = 0`` (which the parity
    # assertion above guarantees), so it catches any weight move that changes
    # that ratio — a sum-preserving redistribution that keeps it fixed slips
    # through HERE and is caught by the ``required_cb`` pins further down — and
    # it also fails if the twins stop sharing a baseline, which the parity
    # assertion above catches first, with a message that names it.  It is here
    # so the 1.857 the comment quotes cannot drift away from this band.
    assert ceiling == pytest.approx(1.857, abs=1e-3), (
        f"the comment quotes 1.857 as the ceiling at |d_gb| = 0; this page's "
        f"|d_gb| = {abs(two_gb_calm - two_gb_hot):.4f} puts it at "
        f"{ceiling:.4f}")
    # …and the general ceiling the comment now names, with ``|d_gb|`` at its
    # maximum: pure arithmetic on the three weights, labelled as such.
    absolute = (
        DEFAULT_SIMILARITY_WEIGHT + DEFAULT_GRAPH_BOOST_WEIGHT + DEFAULT_RECENCY_WEIGHT
    ) / DEFAULT_GRAPH_BOOST_WEIGHT
    assert absolute == pytest.approx(2.857, abs=1e-3), (
        f"the comment quotes 2.857 as the saturation at maximal |d_gb| "
        f"(sim_w + graph_w + rec_w) / graph_w; this is {absolute:.4f}")
    # THE GUARANTEE — the ONE claim here about pages rather than this fixture:
    # ``cb`` is confined to the graph term, so the most it can add is the
    # clamp's headroom ``1.0 - baseline``.  On a page whose CONTESTED row is the
    # LESS similar one — ``d_sim == +1`` (``d = calm − hot``), i.e. the bottom
    # of the span, the placement asserted above and the case this boost exists
    # for — with equal recency, the requirement is ``1.4286 + d_gb`` >=
    # ``1.4286 - gb_hot``, which EXCEEDS that headroom, so no ``cb`` inside the
    # invariant clears it.  Scope it exactly: on a page whose contested row is
    # the MORE similar one (the top of the span) ``d_sim`` is negative, the
    # requirement is ``-1.4286 + d_gb`` and DROPS below the headroom, so the
    # clamp CAN deliver it — which is why the placement is asserted as a
    # PREMISE above rather than assumed by the band.  ``1.0`` is not a 2-row
    # bound and is not asserted as one: the value pinned just below is THIS
    # fixture's requirement (1.4286, from the parity), not "a 2-row page must
    # need the full span".
    two_headroom = 1.0 - two_gb_hot
    assert two_headroom == pytest.approx(0.3), (
        f"the comment quotes 0.3 of clamp headroom (1.0 - a 0.7 baseline); "
        f"this page gives {two_headroom:.4f}")
    assert two_req > two_headroom, (
        f"the 2-row requirement {two_req:.4f} must EXCEED the clamp's headroom "
        f"{two_headroom:.4f}: that is the 'the CLAMP is the blocker' claim, "
        f"and it is what a band alone does not say")

    # The blocker is the CLAMP, not unboundedness: give the boost a value far
    # past the clamp and the ordering still loses, the graph term pinned.
    with monkeypatch.context() as patched:
        patched.setattr("tortoise.ranking.W4_CONTESTED_BOOST", 2.0)
        huge_req, huge_hot, huge_calm, huge_ids = required_cb(two_row)
    assert huge_hot["w4_contested_boost"] == 2.0, "the bigger boost did not apply"
    # The requirement is a property of the PAGE, not of the boost that happened
    # to be applied, so stripping the CLAMPED increment (1.0 - 0.7 = 0.3, not
    # the requested 2.0) must return the same 2-row requirement as the shipped
    # run.  A helper that stripped the requested or the shipped magnitude would
    # report 3.1286 / 1.1786 here — requirements, NOT level conditions.  (The
    # raw final-score gaps those strips leave are 1.095 / 0.4125, i.e. the same
    # two numbers before the division by the 0.35 graph weight: quoting 1.095
    # beside 1.1786 would compare a raw gap with a requirement.  ``1.1786`` is
    # also the number ``tortoise/ranking.py`` quotes for the unrelated
    # "no parity NAND" counterfactual; it is coincidence, not the same
    # measurement.)
    assert huge_req == pytest.approx(two_req, abs=5e-3), (
        f"reading the requirement back off the cb=2.0 run must reproduce the "
        f"page's own requirement ({two_req:.4f}); got {huge_req:.4f} — the "
        f"strip in ``required_cb`` is no longer the increment that applied")
    # The two illustrative numbers above, pinned on ONE basis (requirements —
    # after the division by the graph weight) and read off THIS run's payload,
    # so the comment's arithmetic fails here rather than going stale if the
    # clamp, the recency term or the fixture moves.
    huge_gap_strip_requested = huge_calm["final_score"] - (
        huge_hot["final_score"] - 2.0 * DEFAULT_GRAPH_BOOST_WEIGHT)
    huge_gap_strip_shipped = huge_calm["final_score"] - (
        huge_hot["final_score"]
        - W4_CONTESTED_BOOST * DEFAULT_GRAPH_BOOST_WEIGHT)
    assert huge_gap_strip_requested / DEFAULT_GRAPH_BOOST_WEIGHT == (
        pytest.approx(3.1286, abs=5e-3)), (
        f"``required_cb``'s docstring says stripping the REQUESTED 2.0 would "
        f"report 3.1286 (raw gap 1.095 / 0.35); this run gives "
        f"{huge_gap_strip_requested / DEFAULT_GRAPH_BOOST_WEIGHT:.4f}")
    assert huge_gap_strip_shipped / DEFAULT_GRAPH_BOOST_WEIGHT == (
        pytest.approx(1.1786, abs=5e-3)), (
        f"``required_cb``'s docstring says stripping the SHIPPED 0.05 would "
        f"report 1.1786 (raw gap 0.4125 / 0.35); this run gives "
        f"{huge_gap_strip_shipped / DEFAULT_GRAPH_BOOST_WEIGHT:.4f}")
    assert huge_hot["graph_boost"] == 1.0, (
        f"the documented [0,1] clamp must bind: graph_boost="
        f"{huge_hot['graph_boost']} — if it no longer does, a large cb DOES "
        f"reach the smallest pages and the comment's blocker analysis is stale")
    # The comment quotes this run's two finals (0.5000 vs 0.8950): pin them, so
    # a shift in the clamp, the recency term or the fixture fails here rather
    # than leaving the quoted pair unpinned.
    assert huge_hot["final_score"] == pytest.approx(0.5000, abs=2e-3), (
        f"the comment quotes the contested twin's 2-row final as 0.5000 when "
        f"cb=2.0 pins the graph term; this run gives {huge_hot['final_score']}")
    assert huge_calm["final_score"] == pytest.approx(0.8950, abs=2e-3), (
        f"the comment quotes the calm twin's 2-row final as 0.8950; this run "
        f"gives {huge_calm['final_score']}")
    assert huge_ids.index(calm) < huge_ids.index(hot), (
        "cb=2.0 must still leave the contested twin second on a 2-row page: "
        "that loss is the CLAMP, not an unreachable magnitude")

    # ── The requirement FALLS as the page grows (it does not rise) ────────
    assert ten_req < two_req, (
        f"the requirement must fall as the page grows (default page "
        f"{ten_req:.4f} vs 2-row {two_req:.4f}); a rising requirement is what "
        f"'unbounded' would look like, and it is what the corrected comment "
        f"denies")

    # ── The intermediate page a reader may quote: limit=3 ────────────────
    # The comment's middle row.  A 3-row fused page STILL holds the twins at
    # both ends of the span (the third row lands in the middle), so it buys
    # nothing over the 2-row page — which is why the requirement is flat to
    # 1.4286 and only falls at limit>=4.
    res3 = ranked_sdk.tortoise_fts_query(query, limit=3, order_by="graph")
    assert {r.get("match_source") for r in res3} == {"rrf"}, (
        "the limit=3 row of the comment is a FUSED-read measurement")
    three_req, three_hot, three_calm, three_ids = required_cb(
        [dict(r) for r in res3])
    assert {hot, calm} <= set(three_ids), (
        f"the 3-row page must hold both twins: {three_ids}")
    # The DRAW-INDEPENDENT half of the comment's claim: for a page whose
    # contested row is the less similar one (``d_sim > 0``, the placement
    # asserted above), the requirement is non-increasing in page size
    # (``_min_max_normalize``'s span can only grow, and the twins' own numerator
    # is fixed by their ranks), so a 3-row page can never need more than the
    # 2-row worst case.  Which of those rows the page puts between the twins
    # does not matter; the span DIRECTION does, which is why it is named.
    assert three_req <= two_req + 5e-3, (
        f"the 3-row requirement ({three_req:.4f}) must not EXCEED the 2-row "
        f"one ({two_req:.4f}): the requirement falls as the page grows")
    # The exact 1.4286 additionally needs the twins to consume the whole span
    # (a third row strictly between them).  WHICH row that is belongs to the
    # fixture's texts through the stand-in embedder's hash-seeded vectors, so
    # the premise is named, here and in ``_seed_twin_conflict``.
    assert {three_calm["similarity"], three_hot["similarity"]} == {0.0, 1.0}, (
        f"EMBEDDER/FIXTURE PREMISE: the comment's flat 1.4286 at limit=3 "
        f"assumes a third row lands BETWEEN the twins, so they still hold both "
        f"ends of the span; this page gives calm "
        f"{three_calm['similarity']} / hot {three_hot['similarity']} "
        f"({three_ids}). Which row lands where is set by the stand-in "
        f"embedder's hash-seeded vectors and the twins' mint order — a text "
        f"edit to the fixture can move it without any ranker change")
    assert three_hot["similarity"] == 0.0, (
        f"FIXTURE PREMISE BROKEN (see the 2-row note above): the contested "
        f"twin must sit at the 0 end of the span; here calm "
        f"{three_calm['similarity']} / hot {three_hot['similarity']}")
    assert three_req == pytest.approx(1.4286, abs=5e-3), (
        f"the comment quotes 1.4286 at limit=3; this page requires "
        f"{three_req:.4f}")
    # Both figures the comment quotes for this pool, each pinned to the CONSTANT
    # it quotes rather than to a rearrangement of the others (an assertion built
    # from the payload it is meant to check — ``residual == (residual + boost) -
    # boost`` — can never fail and guards nothing):
    #   * 0.5    — the pre-boost relevance gap the boost must clear (the basis
    #              the 0.0189 beside it is on; 1.4286 * 0.35),
    #   * 0.4825 — the final-score residual a reader sees in the payload once the
    #              shipped boost applies (0.5 - 0.0175).
    # The boost separating them is read off the payload, so a silent change to
    # the applied magnitude shows up as a 0.5 that no longer reconciles.
    assert three_hot["w4_contested_boost"] == W4_CONTESTED_BOOST, (
        f"the 3-row page must carry the SHIPPED boost (payload says "
        f"{three_hot['w4_contested_boost']}); both quoted figures assume it")
    three_applied = three_hot["w4_contested_boost"] * DEFAULT_GRAPH_BOOST_WEIGHT
    three_residual = three_calm["final_score"] - three_hot["final_score"]
    assert three_residual == pytest.approx(0.4825, abs=2e-3), (
        f"the comment quotes the 3-row pool's post-boost residual as 0.4825 "
        f"(0.5 - 0.0175); this page measures {three_residual:.4f}")
    three_gap = three_residual + three_applied
    # The 0.5 gap on a CROSS-BASIS check rather than a restatement of the
    # 0.4825 pin beside it: with the parity making ``d_gb == 0`` and the twins'
    # recency equal, the pre-boost gap must be the SIMILARITY term alone —
    # ``sim_w * (similarity_calm - similarity_hot)`` — so the payload's
    # final-score residual and the payload's similarity span have to reconcile.
    # (``three_residual ≈ 0.4825`` plus a pinned 0.0175 boost already forces
    # ``three_gap ≈ 0.5``, so re-asserting that constant would guard nothing.)
    assert three_gap == pytest.approx(
        DEFAULT_SIMILARITY_WEIGHT
        * (three_calm["similarity"] - three_hot["similarity"]), abs=2e-3), (
        f"the comment quotes a 0.5 pre-boost relevance gap on the 3-row pool; "
        f"the payloads give {three_gap:.4f} (residual {three_residual:.4f} "
        f"plus the {three_applied:.4f} the payload shows was applied), which "
        f"must be the similarity term alone — "
        f"sim_w * d_sim = {DEFAULT_SIMILARITY_WEIGHT * (three_calm['similarity'] - three_hot['similarity']):.4f} "
        f"— because the twins' parity zeroes d_gb")

    # ── Remedy (2)'s actionable half: the raise is RUN, not inferred ──────
    # ``ten_req < two_req`` alone does NOT imply that a raise repairs the
    # default page — a value above ten_req can still exceed the clamp's
    # headroom (ten_req=0.9, two_req=1.4286 would pass every assertion above
    # and make the claim false), so the headroom is asserted with it, and the
    # flip is measured through the shipped read rather than argued.
    ten_gb_calm, ten_gb_hot = baseline_graph_boost([dict(r) for r in res])
    assert ten_gb_calm == ten_gb_hot, (
        f"the default page's twins no longer share a baseline graph_boost "
        f"(calm {ten_gb_calm}, hot {ten_gb_hot}): the requirement read off "
        f"this page is then not the comment's")
    ten_headroom = 1.0 - ten_gb_hot
    # The two figures the comment quotes for this page.  0.0540 is the measured
    # requirement (asserted against the payloads through the formula); 0.0714
    # is NOT a payload read — it is arithmetic on the ranker's own weights for
    # #2102's E2E-1 pre-assertion (relevance delta <= 0.05 in similarity
    # space), so it is checked as arithmetic and then against the requirement.
    assert ten_req == pytest.approx(
        derived_cb(ten_hot, ten_calm, ten_gb_calm, ten_gb_hot), abs=5e-3)
    # The 0.0540 is measured on THIS fixture's 4-row page, whose similarity
    # span is set by those four rows through the stand-in embedder — name the
    # coupling so a fixture edit is not mistaken for a ranker change.
    assert len(res) == 4, (
        f"the comment's 0.0540 is measured on this fixture's 4-row page (the "
        f"whole graph: twins, counter-claim, parity row); this read returned "
        f"{len(res)} rows, so the requirement's similarity span is a "
        f"different one — re-measure it rather than re-describing it")
    # The level condition the comment quotes on this page assumes the twins'
    # recency term cancels (equal ``createdAt``); name that premise, because
    # ``unrounded_preboost_gap`` below leans on it to reduce to the similarity
    # term alone.
    assert ten_calm["recency_boost"] == ten_hot["recency_boost"], (
        f"the comment's default-page requirement assumes the twins' recency "
        f"cancels; calm {ten_calm['recency_boost']} vs hot "
        f"{ten_hot['recency_boost']}")
    # The requirement and the 0.0189 gap are pinned on an UNROUNDED basis.
    # ``ten_req`` above is reconstructed from the 4-dp payload, and that
    # reconstruction carries ~4e-4 — so it cannot say whether the comment is
    # entitled to 0.0189 or to the 0.0188 the earlier wording quoted, and a
    # tight band on IT would be false precision.  ``scores.rrf`` is the exact
    # input ``rerank`` normalized, so the gap is recomputed from it here and
    # the payload reconstruction is then checked against that exact value,
    # each with the band its own basis supports.
    ten_gap_unrounded = unrounded_preboost_gap([dict(r) for r in res])
    ten_req_unrounded = ten_gap_unrounded / DEFAULT_GRAPH_BOOST_WEIGHT
    assert ten_req == pytest.approx(ten_req_unrounded, abs=5e-4), (
        f"the payload-derived requirement ({ten_req:.7f}) and the one "
        f"recomputed from the unrounded ``scores.rrf`` span "
        f"({ten_req_unrounded:.7f}) must agree to the payload's own 4-dp "
        f"budget: a wider split means ``required_cb``'s strip is no longer "
        f"reproducing ``rerank``'s level condition")
    assert ten_req_unrounded == pytest.approx(0.0540, abs=1e-4), (
        f"the comment quotes a 0.0540 requirement on the shipped default "
        f"page; the unrounded derivation measures {ten_req_unrounded:.7f}")
    assert ten_gap_unrounded == pytest.approx(0.0189, abs=1e-5), (
        f"the comment quotes this page's relevance gap as 0.0189 of final "
        f"score (just above the 0.0175 boost); the unrounded derivation "
        f"measures {ten_gap_unrounded:.7f}.  The 1e-5 band is only honest on "
        f"THIS basis: the similarity term is recomputed from the unrounded "
        f"``scores.rrf`` span and the other two terms are zero by the parity "
        f"and recency assertions above, so no 4-dp rounding enters.  Read off "
        f"the payload instead, the same gap carries ~1.4e-4 and the band "
        f"could not separate 0.0189 from 0.0188 — which is the whole question "
        f"this correction exists to answer")
    clears_2102 = DEFAULT_SIMILARITY_WEIGHT * 0.05 / DEFAULT_GRAPH_BOOST_WEIGHT
    assert clears_2102 == pytest.approx(0.0714, abs=1e-3)
    assert ten_req < clears_2102, (
        f"the comment says a cb clearing #2102's 0.05 relevance delta "
        f"({clears_2102:.4f}) also clears the default page's requirement "
        f"({ten_req:.4f})")
    # The claim is two-sided on THIS page: at the shipped boost the contested
    # twin is SECOND (the failure #7704 reports), and at the comment's own
    # 0.06 it is FIRST.  The lower bound is the headroom, so the inference
    # from ``ten_req < two_req`` alone is not what is being relied on.
    shipped_ids = [r["id"] for r in res]
    assert shipped_ids.index(hot) > shipped_ids.index(calm), (
        f"at the shipped cb the contested twin must rank BELOW its twin on "
        f"the default page — that loss is the defect #7704 reports (got "
        f"{shipped_ids})")
    modest = 0.06  # the comment's measured value: > ten_req, < ten_headroom
    assert ten_req < modest <= ten_headroom, (
        f"a modest raise ({modest}) must sit between the default page's "
        f"requirement ({ten_req:.4f}) and the clamp's headroom "
        f"({ten_headroom:.4f}) — outside the headroom it could not flip it")
    with monkeypatch.context() as patched:
        patched.setattr("tortoise.ranking.W4_CONTESTED_BOOST", modest)
        flipped = ranked_sdk.tortoise_fts_query(query, limit=10, order_by="graph")
    fby = {r["id"]: r["graph_ranking"] for r in flipped}
    assert hot in fby and calm in fby, "FIXTURE NOT REACHED: a twin is off the page"
    assert fby[hot]["w4_contested_boost"] == modest, "the modest raise did not apply"
    flipped_ids = [r["id"] for r in flipped]
    assert flipped_ids.index(hot) < flipped_ids.index(calm), (
        f"cb={modest} must rank the contested twin FIRST on the shipped "
        f"default page: it is above that page's measured requirement "
        f"({ten_req:.4f}) and inside the clamp's headroom "
        f"({ten_headroom:.4f}). If it does not, the comment's 'a modest "
        f"raise therefore DOES repair the shipped default read' is false")

    # ── The threshold is the ROUND-TRIPPED one, not the requirement ─────────
    # ``graph_boost`` is rounded to 4 dp BEFORE it is weighted
    # (``round(min(1.0, graph_boost + cb), 4)``) and ``final_score`` is rounded
    # again, so the flip sits ABOVE the unrounded requirement: the requirement
    # lands on graph_boost cell 0.7540, 0.0541 (cell 0.7541) still ties, and the
    # flip starts at cell 0.7542 — ``cb`` >= 0.0542 on the 1e-4 grid, 0.05415
    # continuous.
    #
    # The CELLS are asserted as arithmetic on THIS page's baseline, and the
    # ORDERING is probed where it is not a rounding boundary.  Probing 0.0541 /
    # 0.0542 through the ranker would make a 5e-5 margin the criterion — 1.75e-5
    # of final-score gap against a 1e-4 grid, i.e. decided by which way two
    # independently rounded finals land — while the requirement those boundaries
    # belong to is only pinned to the payload's honest 4-dp budget.  A test that
    # PERMITS the requirement to drift and REQUIRES its boundary to hold to 5e-5
    # contradicts itself; the boundary is arithmetic, the behaviour is probed a
    # whole grid cell clear of it on each side.
    def _flips_at(value: float) -> bool:
        with monkeypatch.context() as patched:
            patched.setattr("tortoise.ranking.W4_CONTESTED_BOOST", value)
            got = ranked_sdk.tortoise_fts_query(query, limit=10, order_by="graph")
        ids = [r["id"] for r in got]
        assert hot in ids and calm in ids, (
            f"FIXTURE NOT REACHED at cb={value}: a twin is off the page")
        return ids.index(hot) < ids.index(calm)

    assert round(ten_gb_hot + ten_req_unrounded, 4) == pytest.approx(0.7540), (
        f"the comment says the requirement lands on the graph_boost cell "
        f"0.7540 (baseline {ten_gb_hot} + the unrounded requirement "
        f"{ten_req_unrounded:.7f}); the round-trip puts it at "
        f"{round(ten_gb_hot + ten_req_unrounded, 4)} — re-measure the grid "
        f"rather than re-describing it")
    assert round(ten_gb_hot + 0.0541, 4) == pytest.approx(0.7541), (
        f"the comment quotes 0.0541 as landing on graph_boost cell 0.7541 — "
        f"one cell above the requirement's 0.7540, and still a tie, which is "
        f"why it is not yet enough.  On this page's baseline "
        f"{ten_gb_hot} the round-trip gives "
        f"{round(ten_gb_hot + 0.0541, 4)}")
    assert round(ten_gb_hot + 0.0542, 4) == pytest.approx(0.7542), (
        f"the comment quotes 0.0542 as the smallest increment on the 1e-4 "
        f"grid that flips the shipped default page (graph_boost cell 0.7542); "
        f"on this page's baseline {ten_gb_hot} the round-trip gives "
        f"{round(ten_gb_hot + 0.0542, 4)}")
    # The ordering, probed where a 1e-4 grid cannot decide it: at the exact
    # (unrounded) requirement the contested twin must NOT take the lead, and a
    # full four cells above it — four times the width of the boundary cell —
    # it must.  Four cells also swallows the payload's rounding slack, so this
    # does not depend on ``ten_req`` landing on a particular 4-dp value.
    assert not _flips_at(ten_req_unrounded), (
        f"the comment puts the flip at cb >= 0.05415, ABOVE the unrounded "
        f"requirement ({ten_req_unrounded:.7f}) — so cb == the requirement "
        f"must NOT reorder the payload. It did: the round-trip (graph_boost "
        f"and final_score both rounded to 4 dp before/after weighting) is no "
        f"longer what the comment says it is")
    assert _flips_at(ten_req_unrounded + 4 * 1e-4), (
        f"cb immediately above the requirement must flip the shipped default "
        f"page; {ten_req_unrounded + 4 * 1e-4:.7f} does not — re-measure the "
        f"round-trip threshold rather than re-describing it")

    # ── The bound is CONDITIONAL: the ``Δgb`` term is not free ─────────────
    # ``cb_req = (sim_w*Δsim + graph_w*Δgb + rec_w*Δrec) / graph_w``.  1.857 is the
    # saturation at ``Δgb == 0`` (the parity case); the requirement MOVES with
    # ``Δgb`` — ``1.4286 + Δgb`` on this 2-row page — so it only passes 1.857
    # once ``Δgb`` >= 0.4286, and the sign is not guaranteed either (the
    # contested twin carries the counter-claim edge, i.e. the HIGHER
    # connectivity, which pushes ``Δgb`` negative).  Measured end-to-end through
    # the real ranker: give the twins a maximal confidence gap and the 2-row
    # requirement rises by exactly ``Δgb``, past the parity ceiling — which is
    # why the comment must not quote 1.857 as the general bound, nor as a floor.
    proj = ranked_sdk._get_proj()
    for pid, conf in ((calm, 1.0), (hot, 0.0)):
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence = $c",
            params={"id": pid, "c": conf},
        )
    wide_req, _wh, _wc, _wid = required_cb(two_row)
    wide_gb_calm, wide_gb_hot = baseline_graph_boost(two_row)
    wide_dgb = wide_gb_calm - wide_gb_hot
    # The comment's other corrected half: the requirement passes the 1.857
    # saturation only once ``Δgb >= 0.4286`` (``ceiling - two_req``), so
    # ``Δgb`` >= 0 does NOT put it at or above 1.857.  Arithmetic on the two
    # measured quantities, pinned here so the sentence cannot re-flip.
    crossing = ceiling - two_req
    assert crossing == pytest.approx(0.4286, abs=5e-3), (
        f"the requirement passes the 1.857 saturation only once "
        f"Δgb >= {crossing:.4f}; at these weights that is 0.4286")
    assert wide_dgb > crossing, (
        f"the measured Δgb ({wide_dgb:.4f}) must sit above the crossing "
        f"({crossing:.4f}) for this page to exceed the parity ceiling")
    assert wide_dgb == pytest.approx(0.5, abs=5e-3), (
        f"a 1.0/0.0 confidence gap must move Δgb by 0.5 (graph_boost is "
        f"0.5 * confidence + 0.5 * connectivity); got {wide_dgb:.4f}")
    assert wide_req == pytest.approx(two_req + wide_dgb, abs=5e-3), (
        f"the requirement must rise by exactly Δgb ({wide_dgb:.4f}) when the "
        f"twins' baselines diverge: {wide_req:.4f} vs {two_req:.4f}")
    assert wide_req > ceiling, (
        f"with Δgb > 0 the requirement ({wide_req:.4f}) exceeds the parity "
        f"ceiling {ceiling:.4f} — so W4_CONTESTED_BOOST's comment must state "
        f"1.857 as the Δgb == 0 saturation, neither the general bound nor a floor")
    assert wide_req <= absolute, (
        f"and it must still be bounded: {wide_req:.4f} <= {absolute:.4f}")


def test_integration_w4_enrich_false_suppresses_boost_with_flag_on(ranked_sdk, monkeypatch):
    """P1 (second-model gate): w4_enrich=False must suppress the boost even
    with TORTOISE_W4_ENRICHMENT=1 — order and the why-layer keys must never
    disagree (both off together).  Byte-equal to the flag-off run."""
    _calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "zebra finch flocking behavior is social, not migratory")
    off = _run_fts_graph(ranked_sdk, flag_on=False)
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    on_optout = ranked_sdk.tortoise_fts_query(
        "zebra finch migration", limit=10, order_by="graph", w4_enrich=False)
    assert [r["id"] for r in off] == [r["id"] for r in on_optout]
    for r_off, r_opt in zip(off, on_optout):  # noqa: B905
        # Full-row equality: no why-layer keys AND no boost in the opt-out
        # run — order and keys both match the flag-off baseline exactly.
        assert r_off == r_opt
    by_opt = {r["id"]: r["graph_ranking"] for r in on_optout}
    assert "w4_contested_boost" not in by_opt[hot]


def test_integration_golden_ordering_flag_off_equals_irrelevant_flag_on(ranked_sdk, monkeypatch):
    """R11 golden-ordering regression over the REAL path: an irrelevant
    conflict (the counter-claim shares no significant token with the query)
    must leave the ranking BYTE-IDENTICAL whether the W4 flag is off or on —
    flag-on never perturbs order without conflict-relevance.  Contestation
    is surfaced in both (epistemic honesty), scored in neither."""
    calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "weather fronts drive bird flocking aggregation")
    off = _run_fts_graph(ranked_sdk, flag_on=False)
    on = _run_fts_graph(ranked_sdk, flag_on=True, monkeypatch=monkeypatch)
    assert [r["id"] for r in off] == [r["id"] for r in on]
    for r_off, r_on in zip(off, on):  # noqa: B905
        assert r_off["graph_ranking"] == r_on["graph_ranking"]
        assert "w4_contested_boost" not in r_off["graph_ranking"]
    by_off = {r["id"]: r["graph_ranking"] for r in off}
    # Structural parity preserved: degree-equal twins share a graph_boost —
    # only variance distinguishes them, and variance alone never reorders.
    assert by_off[hot]["graph_boost"] == by_off[calm]["graph_boost"]
    assert by_off[hot]["contested"] is True
    assert by_off[calm]["contested"] is False
