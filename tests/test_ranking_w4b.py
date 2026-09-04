"""W4-b contentiousness-as-scored-signal tests (issue #2102, epic #2080).

S7 (ranking.py scoring surface) + S8 (batch EP reads) over both rankers:

* contested = has_ep AND variance > CONTESTED_VARIANCE_THRESHOLD (STRICT —
  exactly-at is NOT contested, just-above IS; uncalibrated (no persisted
  alpha/beta) is NOT contested);
* the scored boost applies ONLY under the W4 flag (TORTOISE_W4_ENRICHMENT)
  AND a query AND a conflict RELEVANT to the query (significant-token
  overlap with the NAND-side counter-claim, resolved in ONE batch read —
  no N+1);
* flag-off / no-query / irrelevant-conflict runs are byte-identical to the
  pre-W4-b ranking (R11 golden-ordering regression);
* E2E-1 ranking-participation: the contested twin outranks its uncontested
  twin on a conflict-relevant query (pre-assertion: the contested twin's
  PERSISTED variance exceeds the threshold before the ordering asserts).
"""
from __future__ import annotations

import os
import tempfile

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
            "zebra finch migration", ["flocking zebra behavior is social"]
        ) is True
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
            return self._Rows(self.counter_claims)
        self.signal_query_count += 1
        return self._Rows(self.point_rows)


# ── Unit: batch relevance resolution (S8 N+1 guard) ────────────────────────

class TestBatchResolution:
    def test_single_batch_query_for_many_contested(self):
        fake = _FakeProjection(counter_claims=[
            ("p1", "the cache rollback is the safer default"),
            ("p2", "cache versioning adds nothing"),
            ("p3", "unrelated weather claim"),
        ])
        boosts = resolve_contested_relevance(fake, ["p1", "p2", "p3"], "cache rollback")
        # One query for N > 1 contested ids — never per-result.
        assert fake.nand_query_count == 1
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

def _wipe(sdk):
    sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")


@pytest.fixture()
def ranked_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_w4b_"), "test.db")
    sdk = TortoiseSDK(db_path)
    _wipe(sdk)
    yield sdk
    sdk.close()


def _seed_twin_conflict(sdk, counter_content: str) -> tuple[str, str, str]:
    """Twin claim points (identical content, different persisted EP
    variance) + a NAND operator from a counter-claim into the contested
    twin (the canonical conflict shape why.py resolves).

    The calm twin receives a PARITY NAND from an unrelated point so both
    twins have identical operator-connectivity: the only difference that
    can separate them is EP variance (and the W4-b boost under it)."""
    calm = sdk.create_point("statement", _TWIN_CONTENT)
    hot = sdk.create_point("statement", _TWIN_CONTENT)
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
        ranked_sdk, "flocking zebra behavior is social, not migratory")
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


def test_integration_conflict_relevant_query_outranks_twin(ranked_sdk, monkeypatch):
    calm, hot, _cc = _seed_twin_conflict(
        ranked_sdk, "flocking zebra behavior is social, not migratory")
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
    # Conflict-relevant query (the counter-claim shares "zebra"+"finch") =>
    # the contested twin OUTRANKS its uncontested twin.
    assert ids.index(hot) < ids.index(calm)
    by_id = {r["id"]: r for r in results}
    on_hot = by_id[hot]["graph_ranking"]
    on_calm = by_id[calm]["graph_ranking"]
    assert on_hot["w4_contested_boost"] == W4_CONTESTED_BOOST
    assert on_hot["final_score"] > on_calm["final_score"]
    # Causal delta: flag-on moves the contested twin UP by exactly the
    # boost effect (W4_CONTESTED_BOOST x graph_boost_weight = 0.0175 in
    # final-score space) and leaves the uncontested twin untouched.  If the
    # boost were inert, hot_final(on) == hot_off and the ordering asserts
    # above could pass on retrieval noise alone.
    assert on_calm["final_score"] == calm_off  # calm never boosted
    assert on_hot["final_score"] - hot_off == pytest.approx(
        W4_CONTESTED_BOOST * 0.35, abs=2e-3)


def _run_fts_graph(sdk, flag_on: bool, monkeypatch=None):
    if flag_on:
        assert monkeypatch is not None
        monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    else:
        os.environ.pop("TORTOISE_W4_ENRICHMENT", None)
    return sdk.tortoise_fts_query(
        "zebra finch migration", limit=10, order_by="graph")


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
