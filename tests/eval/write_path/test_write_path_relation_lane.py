"""#11237 — the S5 Job 1 relation WRITE lane (Object→Object edges).

WHAT THIS LANE IS
-----------------
`#10999` delivered the narrative's entity↔entity relations as **transport**:
``execute_embed`` validates them into ``result["relations"]`` and nothing
consumed them. `#11237` owns the remaining leg — the WRITE path — and this
module pins it end to end, at the layer that can actually lose an edge: the
**capture commit** (``sdk.capture_session`` → ``_extract_session_v2``).

⛔ TWO LEGS, AND THE DIFFERENCE MATTERS (the #9077 lesson)
---------------------------------------------------------
* ``test_relation_leg_writes_object_object_edges`` and its siblings
  **monkeypatch ``extract_session_v2``** with a hand-built emission. They grade
  the **WIRE** — resolution, write-path verdict, writer, idempotence — and say
  **nothing** about whether a real model states such a relation. That
  monkeypatch is the eval lane's DELIBERATE deterministic seam and must not be
  deleted: the product number comes from a ``real: true`` receipt, never from
  here.
* ``test_fold_lane_relations_reach_the_commit_leg`` runs the REAL
  ``execute_embed`` (no monkeypatch below the capture seam) and hands its own
  ``result["relations"]`` to the REAL commit — the fold + commit legs together.

⛔ #10000 (P0) is asserted, not assumed: the write path consumes the TYPED
relation (two ids, a verb, a closed-vocabulary meaning) and nothing else — no
transcript, no ``summary``, no ``_searchText``. A test here reds if the leg
starts carrying prose into FalkorDB.
"""
from __future__ import annotations

import contextlib
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.write_path import corpus, runner  # noqa: E402

pytestmark = pytest.mark.timeout(900)

SESSION = "wp01_quarry_debug"


def _emission(entities: list[dict], relations: list[dict],
              points: list[dict] | None = None,
              session_id: str = SESSION) -> dict:
    """A `extract_session_v2`-shaped result: the WIRE's own seam.

    Shaped exactly like the shipped function's return so
    ``sdk._extract_session_v2`` consumes it unchanged — the operator lane's
    ``_extractor`` idiom (#2552), reused rather than re-invented.
    """
    payload = {
        "session_id": session_id, "story_arc": "", "entities": entities,
        "points": points or [], "events": [], "operators": [],
        "supersessions": [], "client_commit_id": f"ccid-{session_id}",
    }
    return {
        "session_id": session_id, "story_arc": "", "embed_list": {},
        "search": {"mode": "embedded", "degraded": True},
        "payload": payload, "chain_notes": [], "link_before_create": [],
        "supersessions": [], "warnings": [], "minted_kinds": [],
        "stats": {}, "errors": [], "relations": relations,
    }


def _extractor(result: dict):
    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return result
    return _run


@pytest.fixture()
def _lane(monkeypatch, tmp_path):
    """A hermetic SDK — embedded graph, a unique namespace, no LLM."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    from tortoise.sdk import TortoiseSDK

    workdir = tmp_path / "graph"
    workdir.mkdir()
    sdk = TortoiseSDK(db_path=str(workdir / "lane.db"),
                      namespace=f"w11237lane_{uuid.uuid4().hex[:8]}")
    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    yield {"sdk": sdk, "workdir": workdir}
    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    with contextlib.suppress(Exception):
        sdk.close()


def _capture(lane, monkeypatch, result: dict,
             session_id: str = SESSION) -> dict:
    import tortoise.extractor_v2 as ev2

    monkeypatch.setattr(ev2, "extract_session_v2", _extractor(result))
    fixture = corpus.load_fixture(SESSION)
    conv = runner.parse_roundtrip(
        session_id, fixture["conversation"], fixture["harness"],
        workdir=lane["workdir"])
    cap = lane["sdk"].capture_session(
        conv, session_id=session_id, harness=fixture["harness"])
    assert cap.get("ok") is True, cap
    return cap


def _edges(sdk) -> set[tuple[str, str, str, str]]:
    """``{(edge_type, src_name, dst_name, label)}`` for every Object→Object edge."""
    rows = sdk._get_proj().g.query(
        "MATCH (a:Object)-[e]->(b:Object) "
        "WHERE type(e) IN ['hasPart','dependsOn','related','ownedBy',"
        "'managedBy','IMPL','NAND','references','wasDerivedFrom'] "
        "RETURN type(e), a.name, b.name, "
        "coalesce(e.label, '')").result_set
    return {(str(t), str(a), str(b), str(lbl)) for t, a, b, lbl in rows}


def _edge_counts(sdk) -> dict[str, int]:
    """``{edge_type: count}`` — a COUNT, because a duplicate is invisible in a set.

    Two edges of the same type between the same two nodes with the same label
    collapse into ONE tuple in :func:`_edges`, so idempotence — a claim about
    the NUMBER of edges in the graph — cannot be asserted on a set at all
    (#11237 review: the `MERGE`→`CREATE` mutant stayed green).
    """
    rows = sdk._get_proj().g.query(
        "MATCH (a:Object)-[e]->(b:Object) "
        "WHERE type(e) IN ['hasPart','dependsOn','related','ownedBy',"
        "'managedBy','IMPL','NAND','references','wasDerivedFrom'] "
        "RETURN type(e), count(*)").result_set
    return {str(t): int(c or 0) for t, c in rows}


_ENTITIES = [
    {"name": "retry storm", "kind": "occurrence", "lifecycle": "created"},
    {"name": "deadline slip", "kind": "occurrence", "lifecycle": "created"},
    {"name": "retry budget", "kind": "occurrence", "lifecycle": "created"},
]


# ⛔ The PURE-PLAN guards for this module live in
# `tests/test_relation_write_11237.py`, not here: `eval/**` is `on_demand` by an
# owner decision (#10194), so a change to `tortoise/relation_write.py` ALONE runs
# no file under `tests/eval/` — the module's own surface is `core`. What stays here
# is the graph-level lane (a REAL capture commits the edges), which is the
# product-number half and is deliberately on demand.

def test_relation_leg_writes_object_object_edges(_lane, monkeypatch):
    """A stated relation LANDS as an edge between the two resolved Objects.

    Pre-#11237 (measured on main at ``f76b74c3e``: ``git grep -n
    'result\\["relations"\\]' tortoise/`` finds no consumer outside the
    extractor) the two Objects are written and the assertion between them is
    lost. Three verb families are pinned at once because they take three
    DIFFERENT routes through the registry's verdict:

      * ``causes`` — a Layer-2 meaning whose mechanism is IMPL → the
        operator-less direct edge, the meaning riding ``label``;
      * ``hasPart`` — a structural predicate → ``create_edge``;
      * ``supports`` → IMPL direct edge labelled with the meaning.
    """
    result = _emission(_ENTITIES, [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry budget", "dst": "retry storm", "predicate": "hasPart"},
        {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"},
    ])
    cap = _capture(_lane, monkeypatch, result)
    written = cap["relation_write"]
    assert written["written"] == 2, written
    # `causes` and `supports` are TWO stated meanings over ONE pair, and the
    # DIRECT writer's identity is `(mechanism, src, dst)` — the stated verb is
    # deliberately not in it — so the second is a COLLAPSE, not a drop and not a
    # second edge. It is counted and named, so the three buckets reconcile with
    # the stated population (#11237 review).
    assert written["dropped"] == 0, written
    assert written["collapsed"] == 1, written
    assert written["written"] + written["dropped"] + written["collapsed"] \
        == written["considered"] == 3, written
    fold, = written["collapses"]
    assert (fold["src"], fold["dst"]) == ("retry storm", "deadline slip"), fold
    assert fold["predicate"] == "supports" and fold["into"] == "IMPL", fold

    edges = _edges(_lane["sdk"])
    assert ("IMPL", "retry storm", "deadline slip", "causes") in edges, edges
    assert ("hasPart", "retry budget", "retry storm", "") in edges, edges
    assert len([e for e in edges if e[0] == "IMPL"
                and e[1] == "retry storm" and e[2] == "deadline slip"]) == 1, edges


def test_relation_leg_is_idempotent_across_a_re_finalise(_lane, monkeypatch):
    """Re-finalising the same batch does not duplicate the edge (#11237 target).

    ⛔ The second commit must be a REAL one, and the earlier version of this test
    was not: `capture_session` short-circuits a re-capture of an existing
    `session_id` (`session_existed and not retry_failed_capture` →
    `mode: "replayed"`), so `_extract_session_v2` — and therefore this leg —
    never ran a second time. It asserted that a graph nobody had written to was
    unchanged, and the `MERGE`→`CREATE` mutant stayed GREEN (measured, #11237
    review). The second capture now takes a DISTINCT id and names the SAME
    entities, so the whole write path re-runs over the same two nodes.

    The assertion is a COUNT (:func:`_edge_counts`), not a set: two edges with
    the same type/label between the same pair are ONE tuple in a set.

    Non-vacuity: `MERGE`→`CREATE` in `create_direct_edge` (or dropping either
    writer's merge) makes the second capture write a second edge, and the count
    doubles.
    """
    result = _emission(_ENTITIES, [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry budget", "dst": "retry storm", "predicate": "hasPart"},
    ])
    _capture(_lane, monkeypatch, result)
    first = _edge_counts(_lane["sdk"])
    assert first == {"IMPL": 1, "hasPart": 1}, first
    second = _capture(_lane, monkeypatch,
                      _emission(_ENTITIES, result["relations"],
                                session_id="wp01_quarry_debug_again"),
                      session_id="wp01_quarry_debug_again")
    assert _edge_counts(_lane["sdk"]) == first, (
        "the second REAL capture duplicated an edge — both writers are supposed "
        "to MERGE on the edge")
    # The plan half is the other side: the same statement folds instead of
    # planning a second edge.
    report = second["relation_write"]
    assert report["written"] + report["dropped"] + report["collapsed"] \
        == report["considered"] == 2, report


def test_relation_leg_names_what_it_refused(_lane, monkeypatch):
    """A relation the registry binds to no path is DROPPED AND NAMED.

    ``transacts`` is in the emission's allowlist but neither in
    ``_VALID_EDGE_PREDICATES`` nor in ``SEMANTICS_MECHANISMS`` — so
    ``relation_write_paths()`` answers ``()`` and nothing may be invented in
    its place. An endpoint that resolves to NO entity this capture wrote, and
    a name this capture wrote TWICE under different kinds (``#11235``: Layer-1
    identity is ``(name, kind)``), are refused on the same rule — never
    guessed through.
    """
    ambiguous = [
        {"name": "quarry", "kind": "theme", "lifecycle": "created"},
        {"name": "quarry", "kind": "epic", "lifecycle": "created"},
    ]
    result = _emission(_ENTITIES + ambiguous, [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "transacts"},
        {"src": "ghost entity", "dst": "deadline slip", "predicate": "related"},
    ])
    cap = _capture(_lane, monkeypatch, result)
    report = cap["relation_write"]
    assert report["written"] == 0, report
    assert report["dropped"] == 2, report
    reasons = " | ".join(d["reason"] for d in report["drops"])
    assert "binds no declared write path" in reasons, reasons
    assert "names no entity this capture wrote" in reasons, reasons
    assert _edges(_lane["sdk"]) == set()


def test_relation_leg_carries_no_narrative_text(_lane, monkeypatch):
    """⛔ #10000 (P0): the edge carries the TYPED relation, never the story.

    The channel's record was already stripped to ``{src, dst, predicate,
    semantics}`` by ``_assemble_relations``; this asserts the write leg adds no
    prose either — every edge property is a short typed token, and no
    transcript/summary text reaches a property. (The entity NAMES are node
    identity, not narrative: they are what the edge's endpoints are.)
    """
    story = ("the retry storm causes the deadline slip because the retry "
             "budget was consumed by the failover storm again")
    result = _emission(_ENTITIES, [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
    ])
    result["summary"] = story
    result["embed_list"] = {"summary": story}
    _capture(_lane, monkeypatch, result)
    rows = _lane["sdk"]._get_proj().g.query(
        "MATCH (a:Object)-[e]->(b:Object) RETURN properties(e)").result_set
    assert rows
    for (props,) in rows:
        for key, value in (props or {}).items():
            assert key not in {"summary", "_searchText", "content",
                               "transcript", "raw"}, (key, props)
            if isinstance(value, str):
                assert story not in value, (key, value)
                assert value != story, (key, value)
                assert len(value) <= 64, (key, value)


def test_fold_lane_relations_reach_the_commit_leg(_lane, monkeypatch):
    """The REAL ``execute_embed`` output commits its own relations.

    No monkeypatch below the capture seam: ``execute_embed`` validates the
    emission's ``relations`` section (``_assemble_relations``) and the REAL
    commit writes the edges from ``result["relations"]`` — the two legs `#10999`
    and `#11237` split between them.
    """
    import tortoise.extractor_v2 as ev2

    embed = {
        "entities": _ENTITIES, "events": [], "points": [], "operators": [],
        "relations": [
            {"src": "retry storm", "dst": "deadline slip",
             "predicate": "causes"},
            {"src": "retry budget", "dst": "retry storm",
             "predicate": "related"},
        ],
        "chain_notes": [], "link_before_create": [],
    }
    folded = ev2.execute_embed(embed, {"points": []}, session_id=SESSION,
                               story_arc="", summary="")
    assert [r["predicate"] for r in folded["relations"]] == \
        ["causes", "related"], folded["relations"]
    cap = _capture(_lane, monkeypatch, folded)
    assert cap["relation_write"]["written"] == 2, \
        cap["relation_write"]
    edges = _edges(_lane["sdk"])
    assert ("IMPL", "retry storm", "deadline slip", "causes") in edges, edges
    assert ("related", "retry budget", "retry storm", "") in edges, edges
