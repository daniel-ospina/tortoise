"""#12184 — the ranker dispatches by ROW TYPE, and refuses labels it cannot score.

The defect this file pins: ``GraphRanker._fetch_signals`` had TWO branches —

    if entity_type == "event": return self._fetch_event_signals(ids)
    return self._fetch_point_signals(ids)          # ← everything else

so ``rerank(..., entity_type="object")`` (and ``"subject"``, ``"source"``,
``"document"``) silently fetched POINT signals for entity ids. Nothing raised,
nothing warned, and the output was plausible: a point's signal is its OWN
persisted belief and its OPERATOR edges, and an entity has neither, so every
entity row came back ``confidence: None`` / ``degree: 0`` and the "graph"
contribution was a constant. A constant is not a neutral default — it is a
claim that the graph does not distinguish these rows, made by a read that never
looked at the edges that could.

What is asserted here:
  * the dispatch is explicit for every label in ``VALID_ENTITY_TYPES``;
  * a label with no signal set REFUSES loudly, and the refusal is not swallowed
    into the defensive ``None`` (which a caller reads as "the read did not
    answer" and ranks anyway). ⛔ THE ARM THAT PROVES THAT IS
    ``test_an_unknown_label_raises_before_any_read``: it calls ``_fetch_signals``
    directly, so moving the dispatch back INSIDE the ``try`` (the regression the
    property names) reds it. ``test_rerank_lets_the_refusal_out…`` does NOT prove
    it — measured: with the dispatch restored inside the ``try`` that arm still
    passes, because ``rerank`` reaches ``signal_family_for`` a second time,
    unguarded, at the identity dimension (``ranking.py:1063``). It proves only
    that ``rerank`` never ANSWERS an unknown label with a ranked list;
  * an entity row's signals are its INBOUND CLAIMS (degree + mean belief), not
    its own belief and not its operator edges — measured on a real graph, with
    the point read on the same ids as an anti-inert control;
  * ``StateRanker`` — the other class with the same internal dispatch (audit
    point 4 on the issue) — routes through the same resolution and refuses too;
  * the identity term applies to entity rows only, and is reported.

MUST run against a live FalkorDB (Docker). Test-prefixed isolated graph.
"""
from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests import _live_utils
from tortoise.ranking import (
    ENTITY_SIGNAL_SPECS,
    SIGNAL_FAMILY_BY_ENTITY_TYPE,
    GraphRanker,
    StateRanker,
    UnsupportedEntitySignals,
    signal_family_for,
)
from tortoise.sdk import TortoiseSDK
from tortoise.security import VALID_ENTITY_TYPES

_GRAPH = f"tortoise_test_12184rk_{uuid.uuid4().hex[:8]}"
_DB_URI = _live_utils.docker_uri(_GRAPH)
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    _probe = TortoiseSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


@pytest.fixture(autouse=True)
def _isolated_db_uri():
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    yield
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


@pytest.fixture
def sdk():
    sdk = TortoiseSDK()
    try:
        yield sdk
    finally:
        with contextlib.suppress(Exception):
            sdk._get_proj().g.query(
                "MATCH (n) WHERE n.id IS NOT NULL OR n.url IS NOT NULL "
                "DETACH DELETE n")
        sdk.close()


class _NeverQueried:
    """A projection that fails the moment a signal read touches the graph.

    The refusal tests use it so a "refused" verdict cannot be produced by a
    query that happened to return nothing: if the fetch ran, this raises.
    """

    class g:
        @staticmethod
        def query(*_a, **_kw):
            raise AssertionError("the graph was queried for a refused label")


# ── 1. The dispatch is explicit, and total over the vocabulary ────────────

def test_every_label_in_the_vocabulary_has_a_declared_family():
    """No label in the read vocabulary may fall through to an implicit branch."""
    assert set(SIGNAL_FAMILY_BY_ENTITY_TYPE) == set(VALID_ENTITY_TYPES)


def test_the_family_map_sends_entities_to_the_entity_fetch_not_points():
    for et in ENTITY_SIGNAL_SPECS:
        assert signal_family_for(et) == "entity"
    # `operator` → point is a ROUTING DECLARATION (an operator IS a :Point),
    # not a fallback — pinned so a future edit cannot quietly widen it.
    assert signal_family_for("operator") == "point"
    assert signal_family_for("point") == "point"
    assert signal_family_for("event") == "event"


def test_the_dispatch_calls_the_fetch_that_belongs_to_the_label(monkeypatch):
    """Per-label: each family reaches ITS fetch, and only that one."""
    calls: list[tuple[str, str]] = []
    r = GraphRanker(projection=_NeverQueried())

    def _point(ids):
        calls.append(("point", ""))
        return {}

    def _event(ids):
        calls.append(("event", ""))
        return {}

    def _entity(ids, entity_type):
        calls.append(("entity", entity_type))
        return {}

    monkeypatch.setattr(r, "_fetch_point_signals", _point)
    monkeypatch.setattr(r, "_fetch_event_signals", _event)
    monkeypatch.setattr(r, "_fetch_entity_signals", _entity)

    expected = {
        "point": ("point", ""),
        "operator": ("point", ""),
        "event": ("event", ""),
        "object": ("entity", "object"),
        "subject": ("entity", "subject"),
        "source": ("entity", "source"),
        "document": ("entity", "document"),
    }
    for et, want in expected.items():
        calls.clear()
        r._fetch_signals(["x"], et)
        assert calls == [want], f"{et} dispatched to {calls}, not {want}"


# ── 2. A label with no signals REFUSES, loudly and unswallowed ────────────

def test_an_unknown_label_raises_before_any_read():
    r = GraphRanker(projection=_NeverQueried())
    with pytest.raises(UnsupportedEntitySignals):
        r._fetch_signals(["x"], "widget")


def test_the_refusal_is_a_value_error_for_existing_callers():
    assert issubclass(UnsupportedEntitySignals, ValueError)


def test_rerank_lets_the_refusal_out_instead_of_ranking_with_defaults():
    """The old shape returned a ranked list here. It must not.

    ⚠️ NOT the arm for "the refusal is not swallowed into the defensive ``None``".
    Measured by review: restore the dispatch inside ``_fetch_signals``' own ``try``
    and this arm STILL PASSES, because ``rerank`` calls ``signal_family_for`` a
    second time (unguarded) at the identity dimension. What it proves is the
    caller-visible contract: no ranked list for an unknown label. The
    "not swallowed" property is pinned by
    ``test_an_unknown_label_raises_before_any_read``, which reds in that state.
    """
    r = GraphRanker(projection=_NeverQueried())
    rows = [{"id": "a", "scores": {"rrf": 0.9}}, {"id": "b", "scores": {"rrf": 0.5}}]
    with pytest.raises(UnsupportedEntitySignals):
        r.rerank(rows, entity_type="widget")


def test_state_ranker_refuses_a_label_it_has_no_signal_set_for():
    r = StateRanker(projection=_NeverQueried())
    rows = [{"id": "a", "entity_type": "widget", "scores": {"rrf": 0.9}}]
    with pytest.raises(UnsupportedEntitySignals):
        r.rerank(rows)
    # `event` too: StateRanker has NO event signals, and silently ranking one
    # with point signals is the defect class, not a graceful degradation.
    with pytest.raises(UnsupportedEntitySignals):
        StateRanker(projection=_NeverQueried()).rerank(
            [{"id": "e", "entity_type": "event", "scores": {"rrf": 0.9}}])


# ── 3. What an entity row is actually measured by (live graph) ────────────

def _seed_object_with_claims(sdk, *, beliefs=(0.9, None, None)) -> tuple[str, str]:
    """An Object and three claims about it; only the first carries a belief."""
    obj = sdk.create_object("the deploy pipeline", objectKind="core:other")
    oid = obj["id"]
    pids = []
    for i, belief in enumerate(beliefs):
        pid = sdk.create_point("statement", f"claim {i} about the deploy pipeline",
                               status="live")["id"]
        if belief is not None:
            sdk.set_point_baseline(pid, 9.0 if belief > 0.5 else 1.0,
                                   1.0 if belief > 0.5 else 9.0)
        sdk.create_edge("aboutObject", pid, oid)
        pids.append(pid)
    return oid, pids


def test_entity_signals_are_the_inbound_claims_not_a_point_read(sdk):
    """The regression, measured: the point read answers nothing here.

    An Object has no persisted belief and no operator edges, so
    ``_fetch_point_signals`` reports ``confidence: None`` / ``degree: 0`` for
    it — that IS the pre-fix dispatch's answer, and it is indistinguishable
    from "the graph knows nothing". The entity read answers the truth: three
    claims point at it, one of them measured at 0.9.
    """
    oid, pids = _seed_object_with_claims(sdk)
    proj = sdk._get_proj()
    r = GraphRanker(proj)

    point_sig = r._fetch_point_signals([oid])[oid]
    assert point_sig["degree"] == 0, "an Object has no operator edges"
    assert point_sig["confidence"] is None, "an Object has no belief of its own"

    entity_sig = r._fetch_signals([oid], "object")[oid]
    assert entity_sig["degree"] == len(pids) == 3
    assert entity_sig["confidence"] == pytest.approx(0.9, abs=1e-6)
    assert entity_sig["is_entity"] is True
    assert entity_sig["ep_state"] == "baseline", (
        "a mean over a DECLARED prior is a prior, never a measurement")


def test_entity_confidence_is_absent_when_no_claim_carries_a_belief(sdk):
    """#11702 one level up: no claim belief is ABSENT, never a fabricated 0.5."""
    oid, _ = _seed_object_with_claims(sdk, beliefs=(None, None))
    sig = GraphRanker(sdk._get_proj())._fetch_signals([oid], "object")[oid]
    assert sig["confidence"] is None
    assert sig["ep_state"] == "absent"
    assert sig["has_prior"] is False
    # …and the boost keeps the structural weight rather than inventing belief.
    boost = GraphRanker(sdk._get_proj()).graph_boost({"id": oid}, sig)
    assert boost == pytest.approx(0.5 * (1.0 - 1.0 / (1.0 + 2)), abs=1e-4)


def test_a_subject_gets_the_same_entity_signal_set(sdk):
    subj = sdk.create_subject("epistemic-team")
    sid = subj["id"]
    pid = sdk.create_point("statement", "the epistemic team owns the ranking lane",
                           status="live")["id"]
    sdk.set_point_baseline(pid, 9.0, 1.0)
    sdk.create_edge("aboutSubject", pid, sid)
    sig = GraphRanker(sdk._get_proj())._fetch_signals([sid], "subject")[sid]
    assert sig["degree"] == 1
    assert sig["confidence"] == pytest.approx(0.9, abs=1e-6)
    assert sig["is_entity"] is True


def test_a_source_counts_the_claims_extracted_from_it(sdk):
    """A Source resolves by ``url``, and its claims are ``extractedFrom`` ones."""
    url = f"https://example.test/doc/{uuid.uuid4().hex[:8]}"
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (s:Source {url: $url, sourceKind: 'document'})",
        params={"url": url})
    for i in range(2):
        pid = sdk.create_point("statement", f"extracted claim {i}", status="live")["id"]
        sdk.set_point_baseline(pid, 3.0, 1.0)
        proj.g.query(
            "MATCH (p:Point {id: $pid}), (s:Source {url: $url}) "
            "CREATE (p)-[:extractedFrom]->(s)",
            params={"pid": pid, "url": url})
    sig = GraphRanker(proj)._fetch_signals([url], "source")[url]
    assert sig["degree"] == 2
    assert sig["confidence"] == pytest.approx(0.75, abs=1e-6)


def test_an_uninformative_entity_read_says_so(sdk):
    """A read that answers ``(0, None)`` for every row measured NOTHING.

    The field exists because "the read answered" and "the answer distinguished
    the rows" are different facts — the conflation is what let a constant graph
    term read as a graph verdict.
    """
    oid = sdk.create_object("an unreferenced object", objectKind="core:other")["id"]
    r = GraphRanker(sdk._get_proj())
    r.rerank([{"id": oid, "name": "an unreferenced object",
               "scores": {"rrf": 0.5}}], entity_type="object")
    assert r.signals_read is True, "the read DID answer"
    assert r.signals_informative is False, (
        "…and it answered that the graph has no structure for this row")

    # ⚠️ This block is the PER-BATCH check, and its comment said the opposite
    # until #12184's review: `oid` itself still carries no claims, and the
    # seeded Object is a DIFFERENT row that the pool does not contain — so the
    # honest label is "the field is per-batch, not per-row". The real positive
    # control is the block after it (`r3`), which ranks the seeded Object.
    _seed_object_with_claims(sdk, beliefs=(0.9,))
    r2 = GraphRanker(sdk._get_proj())
    r2.rerank([{"id": oid, "name": "an unreferenced object",
                "scores": {"rrf": 0.5}}], entity_type="object")
    assert r2.signals_informative is False, (
        "the field is per-BATCH: another row being informative must not make "
        "THIS read informative")
    seeded = [r for r in sdk._get_proj().g.query(
        "MATCH (n:Object) WHERE n.id <> $oid RETURN n.id LIMIT 1",
        params={"oid": oid}).result_set]
    r3 = GraphRanker(sdk._get_proj())
    r3.rerank([{"id": seeded[0][0], "scores": {"rrf": 0.5}}],
              entity_type="object")
    assert r3.signals_informative is True


# ── 4. The identity term applies to entity rows only ─────────────────────

def test_identity_floors_relevance_for_an_entity_row():
    """The query NAMES the row: no lexical score can be more true than that."""
    rows = [
        {"id": "long", "name": "the brief and status log", "scores": {"rrf": 0.9}},
        {"id": "exact", "name": "The Brief", "scores": {"rrf": 0.2}},
    ]
    out = GraphRanker(projection=None).rerank(rows, entity_type="object",
                                              query="the brief")
    assert [r["id"] for r in out] == ["exact", "long"]
    assert out[0]["graph_ranking"]["identity_match"] is True
    assert out[1]["graph_ranking"]["identity_match"] is False
    # The leg's own similarity is untouched and still reported — identity is an
    # ORDER DIMENSION, not a rewritten score.
    assert out[0]["graph_ranking"]["similarity"] == 0.0
    assert out[1]["graph_ranking"]["similarity"] == 1.0


def test_identity_is_not_applied_to_point_rows(monkeypatch):
    """A claim's surface is ``content``; ranking it by name equality is noise."""
    rows = [
        {"id": "long", "content": "the brief and status log", "scores": {"rrf": 0.9}},
        {"id": "exact", "content": "The Brief", "scores": {"rrf": 0.2}},
    ]
    out = GraphRanker(projection=None).rerank(rows, entity_type="point",
                                              query="the brief")
    assert [r["id"] for r in out] == ["long", "exact"]
    assert "identity_match" not in out[0]["graph_ranking"]


# ── 4b. The #1348 ablation reaches the entity arm too (review N3) ────────

def test_the_degree_ablation_reaches_entity_rows(monkeypatch):
    """``use_degree=False`` must neutralize an entity's DEGREE term as well.

    The ablation exists to answer one question — how much of the boost is
    belief? — and an entity's degree (its INBOUND CLAIM count) is a degree. The
    entity arm was added after the ablation and scored ``connectivity``
    unconditionally, so with the ablation ON a row with no belief still got a
    non-zero boost and the arm no longer isolated belief for the rows this
    change added. The two rows below differ ONLY in degree, which is what makes
    the assertion about the degree term and not about confidence.
    """
    two_claims = {"confidence": 0.8, "degree": 2, "is_entity": True}
    no_claims = {"confidence": 0.8, "degree": 0, "is_entity": True}

    on = GraphRanker(projection=None, use_degree=True)
    off = GraphRanker(projection=None, use_degree=False)
    # Ablation ON: the degree term moves the boost (2 claims > 0 claims).
    assert on.graph_boost({"id": "a"}, two_claims) > on.graph_boost(
        {"id": "b"}, no_claims)
    # Ablation OFF: degree is neutralized, so the two are equal — and the boost
    # is exactly the belief term, which is the whole point of the arm.
    assert off.graph_boost({"id": "a"}, two_claims) == off.graph_boost(
        {"id": "b"}, no_claims) == pytest.approx(0.8, abs=1e-4)
    # …and with no belief there is nothing left to weight, exactly as for points.
    assert off.graph_boost(
        {"id": "c"}, {"confidence": None, "degree": 3,
                       "is_entity": True}) == 0.0


# ── 5. The end-to-end read reaches the entity family ─────────────────────

def test_graph_order_over_objects_reports_the_entity_family(sdk):
    oid, _ = _seed_object_with_claims(sdk)
    rows = sdk.tortoise_fts_query("deploy pipeline", entity_type="object",
                                  order_by="graph", limit=5)
    hit = next((r for r in rows if r["id"] == oid), None)
    assert hit is not None, "fixture must return the seeded Object"
    assert hit["graph_ranking"]["signal_family"] == "entity"
    assert hit["graph_ranking"]["graph_boost"] > 0, (
        "three inbound claims must register as structure — the point read "
        "gave this row 0.0")


def test_confidence_order_over_objects_uses_the_mean_claim_belief(sdk):
    """Review N2 — a THIRD product path reaches the entity read, so pin it.

    ``order_by="confidence"`` sorts on
    ``GraphRanker._fetch_signals(..., entity_type)[id]["confidence"]``
    (``sdk.py``'s confidence sort). Before #12184 that read answered a POINT
    signal for an object id — the Object's own (absent) belief, i.e. ``None``
    for every Object — so every Object fell to the unmeasured tier
    (``_confidence_key`` → ``(1, 0.0)``) and kept RRF order. The entity read
    answers the MEAN CLAIM BELIEF, so an Object whose claims carry beliefs now
    sorts AHEAD of an Object with none. That is an improvement and it is a
    product-path change; it is pinned here rather than left undisclosed.

    ⚠️ NON-VACUITY (round-2 review F2). The first version of this test could
    pass on the pre-fix code: the belief-carrying Object also happened to win
    RRF (it was created first and its name WAS the query), so the pre-fix tie
    — both keys ``(1, 0.0)``, a stable sort preserving pool order — coincidentally
    reproduced the expected order. Two changes make the pin discriminate:

      1. the fixture is INVERTED — the belief-LESS Object carries the query
         verbatim and the belief-carrying one a longer name, so fused retrieval
         puts the wrong row first and only the belief key can flip it;
      2. the sort KEY is asserted as a VALUE on the same read the SDK consumes,
         which the pre-fix point read cannot answer.
    """
    name = f"conf-order-object-{uuid.uuid4().hex[:8]}"
    # ⚠️ inverted on purpose: `bare` gets the query VERBATIM so it wins fused
    # retrieval; `measured` sorts first only if its belief is read.
    bare = sdk.create_object(name, objectKind="core:other")["id"]
    measured = sdk.create_object(f"{name} measured", objectKind="core:other")["id"]
    for i in range(2):
        pid = sdk.create_point(
            "statement", f"a confident claim {i} about {name}", status="live")["id"]
        sdk.set_point_baseline(pid, 9.0, 1.0)      # mean 0.9
        sdk.create_edge("aboutObject", pid, measured)

    # The sort KEY itself, read through the same call the SDK's
    # ``order_by="confidence"`` branch makes. Asserted as a VALUE because an
    # order assertion alone can be satisfied by the pool order the tie keeps.
    read = GraphRanker(sdk._get_proj())._fetch_signals([measured, bare], "object")
    assert read[measured]["confidence"] == pytest.approx(0.9), (
        "an Object's confidence key must be the MEAN BELIEF OF ITS CLAIMS — if "
        "this is None, the read is still the pre-#12184 point read (an Object "
        "has no belief of its own)")
    assert read[bare]["confidence"] is None, (
        "the belief-less Object must answer ABSENT, not a fabricated neutral")

    rows = sdk.tortoise_fts_query(name, entity_type="object",
                                  order_by="confidence", limit=10)
    ids = [r["id"] for r in rows]
    assert measured in ids and bare in ids, (
        f"both seeded Objects must be in the window: {ids}")
    assert ids.index(measured) < ids.index(bare), (
        "the row whose CLAIMS carry a measured belief must sort first even "
        "though the belief-less row names the query verbatim — if it does "
        "not, `order_by='confidence'` is still reading the Object's own "
        "absent belief (the pre-#12184 point read)")


# ── 6. The identity surface the FIELD CONTRACT publishes ────────────────

def test_identity_match_fires_on_a_source_title(sdk):
    """Review round 3 — the recorded contract must describe the shipped code.

    ``graph_ranking.identity_match``'s recorded ``emitted_when`` said the term
    was "effectively inert" on ``source``/``document`` rows because "the FTS
    leg indexes ``_searchText``". Both halves of that sentence were wrong, and
    both are asserted here, because the field is emitted precisely so a
    consumer can account for an order identity produced:

      1. the surface the ranker COMPARES is ``content``, and the SDK's entity
         fetch fills ``content`` from ``n.title`` on the source/document
         branches — ``_searchText`` is a different column, the one the
         fulltext index is DECLARED on, not the one compared;
      2. so an exact TITLE query really does set ``identity_match`` true, and
         that row sorts AHEAD of every scored row: rare, but LIVE.

    Pinned because the claim is about a product path (``tortoise_search``
    with ``entity_type="source"``), not an internal: if the entity fetch
    starts filling ``content`` from ``_searchText``, or ``_identity_match``
    stops reading ``content``, this reddens and the contract has moved.
    """
    title = f"quarterly ranking review {uuid.uuid4().hex[:8]}"
    url = f"https://example.test/src/{uuid.uuid4().hex[:8]}"
    # ``documentKind`` NULL ⇒ this row IS a ``source`` (the discriminator
    # ``run_fts_query`` applies: document ⇒ documentKind NOT NULL, source ⇒
    # NULL). ``_searchText`` carries the title AND a topic tail on purpose: it
    # is the column the Source fulltext index is DECLARED on (so the row is
    # retrievable), and it is deliberately NOT equal to the title — the whole
    # point of the pin is that these are two different columns and the ranker
    # compares ``n.title`` (via ``content``), not the indexed text.
    sdk._get_proj().g.query(
        "CREATE (s:Source {url: $url, title: $title, sourceKind: 'web', "
        "_searchText: $st})",
        params={"url": url, "title": title,
                "st": f"{title}, supplementary topic"})

    rows = sdk.tortoise_fts_query(title, entity_type="source",
                                  order_by="graph", limit=10)
    hit = next((r for r in rows if r["id"] == url), None)
    assert hit is not None, (
        "the seeded Source must be retrieved by its own title, or the pin is "
        "vacuous")
    assert hit["content"] == title, (
        "the SDK's entity fetch fills `content` from n.title on the source "
        "branch; if this is `_searchText`, the field contract would be right "
        "that the FTS column is the compared surface")
    assert hit["graph_ranking"]["identity_match"] is True, (
        "an exact TITLE query must set identity_match on a source row — the "
        "recorded contract called the term inert on source/document rows")
