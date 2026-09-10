"""#2165 Task 1 Step 4 — fixture shape + R16(b) + R9 geometry calibrations.

Asserts the synthetic graph substrate (tests/_assembly_graph.py) mirrors the
v2 lane faithfully and that the TWO geometry calibrations hold at DEFAULT
legacy caps (each pins a pre-registered Task-6/7 delta's A-half BEFORE the
later task's capture — Task 6 and Task 7 are forbidden from editing the
substrate, so a vacuous arm must be impossible to discover late):

* R16(b): flag-OFF legacy ask() at DEFAULT caps admits ≥1 of the
  out-of-subgraph gold (the canary compares couch vs dog bed — the gold on
  the THIRD object bookshelf is outside BOTH resolved subgraphs, so the
  assembled arm's B=0 is structurally reachable).
* R9: flag-OFF legacy ask() at DEFAULT caps admits FEWER than 2 of the two
  deep-rank golds (the 87-row pool ranks the golds ~56-57 — outside the
  default rerank depth, inside a widened fetch).

Docker lane only (live FalkorDB — dedicated per-test graph with fulltext,
deleted at teardown; the module probe verifies the FTS round-trip, not just
a bare ping). Dense/vector legs pinned OUT (_no_embedder) — the evidence
path is deterministic text, no model, no network.
"""
from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import tests._assembly_graph as ag
from tests._live_utils import live_uri
from tortoise.sdk import TortoiseSDK

# ── Live-FalkorDB + FTS availability ───────────────────────────────────────
_URI = live_uri()
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
_PROBE_GRAPH = f"{_URI}_probe"
try:
    os.environ["TORTOISE_DB_URI"] = _PROBE_GRAPH
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    # the module's ask-calibrations depend on the FULLTEXT surface (embedded
    # returns [] silently), so the availability gate must prove an FTS
    # round-trip, not just a bare ping: write a point, query it back.
    _probe.create_point(
        "statement", "probe zzqfulltext roundtrip token 7f3a9c", id="pProbe",
        session_id="sess-probe", is_episodic=True, status="draft")
    _hits = _probe.tortoise_fts_query(
        "zzqfulltext roundtrip token", entity_type="point", limit=3)
    if _hits and _hits[0].get("id") == "pProbe":
        FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    with contextlib.suppress(Exception):
        _probe._get_proj().db.select_graph(
            _PROBE_GRAPH.rsplit("/", 1)[-1]).delete()
    with contextlib.suppress(Exception):
        _probe.close()
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane — tier-2 embedded legs skip)")


def _fresh_uri() -> str:
    return f"{_URI}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


@pytest.fixture
def sdk(monkeypatch):
    # dedicated per-test graph — DELETED at teardown (shared docker server;
    # leftover per-test graphs accumulated past the server maxmemory and
    # locked the whole lane — never leave a fresh-URI graph behind)
    uri = _fresh_uri()
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    s = TortoiseSDK()
    try:
        yield s
    finally:
        name = uri.rsplit("/", 1)[-1]
        with contextlib.suppress(Exception):
            s._get_proj().db.select_graph(name).delete()
        s.close()


def _count(proj, cypher: str, **params) -> int:
    return proj.g.query(cypher, params=params).result_set[0][0]


def _row(proj, cypher: str, **params):
    return proj.g.query(cypher, params=params).result_set


def _legacy_ask(sdk, monkeypatch, question: str, *, flag_off: bool = True):
    """Deterministic flag-OFF legacy ask() with a FakeReader — hermetic:
    the assembly master flag is pinned OFF (never the ambient default), so
    the calibration cannot silently route through the Task-6 fired branch
    once it lands."""
    from tests.test_ask_sdk import _install_fake
    if flag_off:
        monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY",
                           raising=False)
    _install_fake(sdk, monkeypatch)
    return sdk.ask(question)


def test_base_graph_v2_lane_faithful(sdk):
    """Shape: Objects incl. the folded supersession chain with PINNED fold
    timestamp; aboutObject Points with SPARSE `when`; Events dated with ZERO
    aboutObject edges; createdAt = session date."""
    h = ag.build_base_graph(sdk)
    proj = sdk._get_proj()
    n_objs = _count(proj, "MATCH (o:Object) RETURN count(o)")
    assert n_objs == 3, f"expected 3 Objects, got {n_objs}"
    # supersession folded (live path) — couch superseded by sofa, pinned ts
    rows = _row(proj, "MATCH (o:Object {name:'couch'}) "
                      "RETURN o.status, o.supersededBy, o.supersededAt")
    assert rows and rows[0][0] == "superseded" and rows[0][1] == "sofa", rows
    assert rows[0][2] == "2026-09-01T00:00:00Z", \
        f"fold timestamp must be pinned (byte-golden state headers): {rows}"
    assert _row(proj, "MATCH (o:Object {name:'sofa'}) RETURN o.status"
               )[0][0] == "live"
    assert _row(proj, "MATCH (o:Object {name:'dog bed'}) RETURN o.status"
               )[0][0] == "live"
    # sparse `when`: exactly the two dated points carry it
    n_when = _count(proj, "MATCH (p:Point) WHERE p.when IS NOT NULL "
                          "RETURN count(p)")
    assert n_when == 2, f"sparse when expected 2 dated points, got {n_when}"
    # points carry NO eventId (v2-lane faithful — the eventId join is vacuous)
    n_ev = _count(proj, "MATCH (p:Point) WHERE p.eventId IS NOT NULL "
                        "RETURN count(p)")
    assert n_ev == 0, f"expected 0 points with eventId, got {n_ev}"
    assert _row(proj, "MATCH (p:Point {id:'pA-couch-bought'}) "
                      "RETURN p.createdAt")[0][0] == ag.SESSION_A_DATE
    n_edges = _count(proj, "MATCH (:Point)-[:aboutObject]->(:Object) "
                           "RETURN count(*)")
    assert n_edges >= 5, f"expected >=5 aboutObject edges, got {n_edges}"
    # EP stamped non-neutral on evidence points
    ep = _row(proj, "MATCH (p:Point {id:'pB-couch-sold'}) "
                    "RETURN p.ep_alpha")[0][0]
    assert ep and float(ep) > 1.0, f"EP must be non-neutral on evidence: {ep}"
    # Events: dated, ZERO aboutObject (base = v2-faithful pre-R8 shape)
    n_evt = _count(proj, "MATCH (e:Event) RETURN count(e)")
    assert n_evt == 2, f"expected 2 Events, got {n_evt}"
    n_ev_edges = _count(proj, "MATCH (e:Event)-[:aboutObject]->(:Object) "
                              "RETURN count(*)")
    assert n_ev_edges == 0, f"base Events must be edge-less, got {n_ev_edges}"
    assert _row(proj, "MATCH (e:Event {lme_event_id:'evA-couch'}) "
                      "RETURN e.startedAt")[0][0] == "2026-08-10"
    # ≥12 distractors WITHOUT any aboutObject edge (the noise-pool contract —
    # not a proxy over total points, which would pass if a distractor were
    # silently re-anchored to a subject)
    n_noise = _count(
        proj, "MATCH (p:Point) WHERE NOT (p)-[:aboutObject]->(:Object) "
              "RETURN count(p)")
    assert n_noise >= 12, f"expected >=12 distractor points, got {n_noise}"
    # every canary key the later tasks consume is present (unasserted keys rot
    # silently and break Tasks 2/6 by reference at a distance)
    assert {"current", "compare", "interval", "misfire", "ago"} <= set(
        h["questions"]), f"canary keys missing: {sorted(h['questions'])}"


def test_hosted_variant_event_about_edges(sdk):
    """Hosted-lane variant: Event-aboutObject edges exist (superset spine)."""
    ag.build_base_graph(sdk)
    ag.build_hosted_variant(sdk)
    proj = sdk._get_proj()
    n = _count(proj, "MATCH (e:Event)-[:aboutObject]->(:Object) "
                     "RETURN count(*)")
    assert n == 3, f"hosted variant: expected 3 Event-aboutObject edges, {n}"


def test_hub_graph_exceeds_slice_cap(sdk):
    """Hub fixture: an Object with >per-slice-cap aboutObject points (the
    Task 4 bounded-pre-fetch fixture must have rows_requested < total)."""
    name = ag.build_hub_graph(sdk, n_points=60)
    proj = sdk._get_proj()
    n = _count(proj, "MATCH (p:Point)-[:aboutObject]->(:Object {name:$n}) "
                     "RETURN count(p)", n=name)
    assert n == 60, f"hub fixture expected 60 anchored points, got {n}"


def test_deep_rank_substrate(sdk):
    """R9 substrate shape: 87 rows, both dated golds present + marked."""
    d = ag.build_deep_rank_substrate(sdk)
    proj = sdk._get_proj()
    n = _count(proj, "MATCH (:Point)-[:aboutObject]->(:Object "
                     "{name:'deep-subject'}) RETURN count(*)")
    assert n == d["pool_rows"] == 87, \
        f"R9 substrate must hold exactly its pool (87), got {n}"
    for g in (d["gold_a"], d["gold_b"]):
        row = _row(proj, "MATCH (p:Point {id:$id}) "
                         "RETURN p.has_answer, p.when", id=g)
        assert row and row[0][0] is True and row[0][1], \
            f"deep-rank gold {g} must be answer-marked AND dated"
        assert row[0][1] in ("2026-09-01", "2026-09-02"), \
            f"gold {g} must carry a VALID calendar date: {row}"


def test_supersession_chain_variants(sdk):
    """No-fabrication variants present: orphan successor, torn row, and a
    recall-excluded (archived) successor whose fold chain still exists."""
    ag.build_supersession_chain_variants(sdk)
    proj = sdk._get_proj()
    rows = _row(proj, "MATCH (o:Object {name:'orphan-src'}) "
                      "RETURN o.status, o.supersededBy, o.supersededAt")
    assert rows and rows[0][0] == "superseded" and rows[0][1] == \
        "successor-never-created" and rows[0][2], \
        f"orphan row must be superseded w/ name + ts: {rows}"
    # the orphan successor node must be ABSENT (no fabrication)
    n_orphan = _count(proj, "MATCH (o:Object "
                            "{name:'successor-never-created'}) RETURN count(o)")
    assert n_orphan == 0, \
        "orphan successor must NOT exist as a node"
    torn = _row(proj, "MATCH (o:Object {name:'torn-row'}) "
                      "RETURN o.status, o.supersededBy")
    assert torn and torn[0][0] == "superseded" and not torn[0][1], \
        f"torn row must be superseded w/ EMPTY successor: {torn}"
    # (c): excl-dst archived AND the fold itself happened (excl-src folded
    # with the successor name) — an unasserted fold would let the raw archive
    # query pass while the chain premise rots
    src = _row(proj, "MATCH (o:Object {name:'excl-src'}) "
                     "RETURN o.status, o.supersededBy")
    assert src and src[0][0] == "superseded" and src[0][1] == "excl-dst", \
        f"excl-src must be folded to excl-dst: {src}"
    dst = _row(proj, "MATCH (o:Object {name:'excl-dst'}) RETURN o.status")
    assert dst and dst[0][0] == "archived", \
        f"excl-dst must be archived (recall-excluded): {dst}"


def test_malformed_date_row(sdk):
    """Malformed-date row: garbage `when` with SENTINEL createdAt (no usable
    date → undated tier — the Task 6 single-outcome pin)."""
    ag.build_malformed_date_row(sdk)
    proj = sdk._get_proj()
    rows = _row(proj, "MATCH (p:Point {id:'pMalformed'}) "
                      "RETURN p.when, p.createdAt")
    assert rows and rows[0][0] == "not-a-real-date-2026" and \
        rows[0][1] == ag.UNDATED_SENTINEL, f"malformed row: {rows}"


def test_out_of_subgraph_gold_calibration(sdk, monkeypatch):
    """R16(b) calibration (Task 1 Step 4): flag-OFF legacy ask() at DEFAULT
    caps ADMITS ≥1 of the out-of-subgraph gold — guarantees the A≥1 half of
    the pre-registered A≥1/B=0 delta before Task 6 capture.

    Geometry (pinned): the canary compares couch vs dog bed — the gold lives
    on the THIRD object bookshelf (aboutObject-ANCHORED there only), so the
    both-halves fired branch walks couch + dog bed subgraphs and CANNOT
    admit it (B=0 structurally reachable), while the whole pool sits inside
    DEFAULT caps so legacy DOES admit it (A≥1)."""
    ag.build_base_graph(sdk)
    o = ag.build_out_of_subgraph_gold(sdk)
    proj = sdk._get_proj()
    res = _legacy_ask(sdk, monkeypatch, o["question"])
    ev = res.get("evidence", "")
    # gold-distinctive token (the gold's own quote word — not the question's
    # subject words, which any couch/dog-bed row would carry)
    assert "reading lamp" in ev, (
        f"legacy DEFAULT-caps must admit the out-of-subgraph gold "
        f"(A≥1 calibration): evidence={ev[:300]}")
    # geometry sanity: couch + dog bed resolve; the gold anchors ONLY
    # bookshelf (a third object OUTSIDE both compare subgraphs)
    gold_rows = _row(proj, "MATCH (p:Point {id:$id})-[r:aboutObject]->"
                           "(o:Object) RETURN collect(o.name)",
                     id=o["gold"])
    assert gold_rows and gold_rows[0][0] == ["bookshelf"], \
        f"gold must anchor ONLY bookshelf: {gold_rows}"
    assert o["question"].count("couch") and o["question"].count("dog bed"), \
        "canary must compare the two BASE subjects (couch/dog bed)"
    assert "bookshelf" not in o["question"], \
        "bookshelf must stay a THIRD (unresolved) object in the canary"
    for nm in ("couch", "dog bed"):
        assert _count(proj, "MATCH (o:Object {name:$n}) RETURN count(o)",
                      n=nm) == 1


def test_deep_rank_geometry_calibration(sdk, monkeypatch):
    """R9 geometry calibration (Task 1): the honest, non-vacuous pool-40
    mechanism, pinned NOW (Task 7 is forbidden from editing the substrate).

    Measured platform truth: this FalkorDB fulltext scores ties (0.0) — the
    result order is a deterministic internal order, not BM25 — and DEFAULT
    evidence keeps the first ~40 rows. On this exact content set pDeepG1
    (marker "fieldtrip") deterministically ranks INSIDE the default pool
    (an honest in-pool same-subject evidence row) while pDeepG2 (marker
    "almanac") ranks at 56/57 — OUTSIDE pool-40, INSIDE a widened 120
    fetch. The calibration pins the delta Task 7's A-widened arm depends
    on: DEFAULT admits G1 only; the widened arm must admit BOTH (G2 is the
    discriminating row). Fails loudly if the deep gold ever leaks into
    DEFAULT evidence (the pool stopped binding) or vanishes from widened
    reach (the widened arm would be vacuous in the other direction)."""
    d = ag.build_deep_rank_substrate(sdk)
    res = _legacy_ask(sdk, monkeypatch, d["question"])
    ev = res.get("evidence", "")
    # G1 (in-pool): its marker MUST appear in DEFAULT evidence
    assert "fieldtrip" in ev, (
        "R9 geometry broken: the in-pool gold pDeepG1 is NOT admitted at "
        "DEFAULT — the pool-40 rank contract changed. Evidence head: "
        f"{ev[:200]}")
    # G2 (deep): its marker MUST NOT appear — DEFAULT pool-40 binds below it
    assert "almanac" not in ev, (
        "R9 geometry broken: the deep gold pDeepG2 leaked into DEFAULT "
        "evidence — the pool does not bind below rank 56 (Task 7's "
        "A-widened arm would be vacuous). Evidence head: "
        f"{ev[:200]}")
    # widened reach: a 120-deep FTS fetch must return BOTH golds (Task 7's
    # widened arm admits both ONLY if both are retrievable at that depth)
    hits = sdk.tortoise_fts_query(d["question"], entity_type="point",
                                  limit=120)
    hit_ids = {h.get("id") for h in hits}
    assert {d["gold_a"], d["gold_b"]} <= hit_ids, (
        f"R9 widened reach broken: both golds must be retrievable at "
        f"limit=120 (got {sorted(hit_ids & {d['gold_a'], d['gold_b']})})")
