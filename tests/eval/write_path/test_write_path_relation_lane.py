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
              points: list[dict] | None = None, *,
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


def _capture(lane, monkeypatch, result: dict, *,
             session_id: str | None = None) -> dict:
    import tortoise.extractor_v2 as ev2

    monkeypatch.setattr(ev2, "extract_session_v2", _extractor(result))
    fixture = corpus.load_fixture(SESSION)
    conv = runner.parse_roundtrip(
        SESSION, fixture["conversation"], fixture["harness"],
        workdir=lane["workdir"])
    cap = lane["sdk"].capture_session(
        conv, session_id=session_id or SESSION, harness=fixture["harness"])
    assert cap.get("ok") is True, cap
    return cap


def _edges(sdk) -> set[tuple[str, str, str, str]]:
    """``{(edge_type, src_name, dst_name, label)}`` for every Object→Object edge."""
    return set(_edge_counts(sdk))


def _edge_counts(sdk) -> dict[tuple[str, str, str, str], int]:
    """``{(edge_type, src_name, dst_name, label): n}`` — ``n`` read from the graph.

    A COUNT, not a set. A duplicated edge keeps the same identity
    ``(type, src, dst, label)``, so it collapses into the same set element by
    construction — a set comparison can never observe a duplicate. That is
    exactly the hole the first revision of the idempotence test fell into
    (#11548 review, P1).
    """
    rows = sdk._get_proj().g.query(
        "MATCH (a:Object)-[e]->(b:Object) "
        "WHERE type(e) IN ['hasPart','dependsOn','related','ownedBy',"
        "'managedBy','IMPL','NAND','references','wasDerivedFrom'] "
        "RETURN type(e), a.name, b.name, "
        "coalesce(e.label, ''), count(*)").result_set
    counts: dict[tuple[str, str, str, str], int] = {}
    for t, a, b, lbl, n in rows:
        key = (str(t), str(a), str(b), str(lbl))
        counts[key] = counts.get(key, 0) + int(n or 0)
    return counts


def _stored_direction(sdk, src_name: str, dst_name: str,
                      rel: str) -> str | None:
    """The ``direction`` the GRAPH holds for one edge — a read, not a claim."""
    rows = sdk._get_proj().g.query(
        "MATCH (a {name:$a})-[e]->(b {name:$b}) WHERE type(e) = $t "
        "RETURN e.direction",
        params={"a": src_name, "b": dst_name, "t": rel}).result_set
    return str(rows[0][0]) if rows else None


def _edge_props_by_id(sdk, src_id: str, dst_id: str) -> list[dict]:
    """Every property bag on the edge(s) between two ids, whatever the labels.

    Labelled nowhere and filtered by ``id`` on both ends, so it reaches the
    Subject-targeted edges (``ownedBy``/``managedBy``) the Object→Object read
    cannot see — the population the ``#10000`` pin used to miss (#11548 P2).
    """
    rows = sdk._get_proj().g.query(
        "MATCH (a {id:$a})-[e]->(b {id:$b}) RETURN properties(e)",
        params={"a": src_id, "b": dst_id}).result_set
    return [props or {} for (props,) in rows]


_ENTITIES = [
    {"name": "retry storm", "kind": "occurrence", "lifecycle": "created"},
    {"name": "deadline slip", "kind": "occurrence", "lifecycle": "created"},
    {"name": "retry budget", "kind": "occurrence", "lifecycle": "created"},
]


# ⛔ The PURE-PLAN guards for this module live in
# `tests/test_relation_write_11237.py`, not here: `eval/**` is `on_demand` by an
# owner decision (#10194), and the on-demand subtraction runs AFTER surface
# selection — so a change to `tortoise/relation_write.py` ALONE (surface `core`)
# runs no file under `tests/eval/` at all, and an `eval/**` row on `core` is a row
# that can never select it. What stays here is the graph-level lane (a REAL
# capture commits the edges), which is the product-number half and is on demand.

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
    assert written["written"] >= 2, written

    edges = _edges(_lane["sdk"])
    # `causes` (IMPL, labelled) and `supports` collapse onto ONE IMPL edge
    # between the same two Objects: `create_direct_edge` is a bare-pattern MERGE
    # ("exactly one edge per (src, tgt, type)"), so on THIS arm the edge's
    # identity is the MECHANISM, not the stated verb. The fold is COUNTED and
    # NAMED on the receipt rather than silently dropped.
    assert ("IMPL", "retry storm", "deadline slip", "causes") in edges, edges
    assert ("hasPart", "retry budget", "retry storm", "") in edges, edges
    assert len([e for e in edges if e[0] == "IMPL"
                and e[1] == "retry storm" and e[2] == "deadline slip"]) == 1, edges
    assert written["folded"] == 1, written
    assert [f["folded"] for f in written["collapsed"]] == [["supports"]], written
    assert written["collapsed"][0]["kept"] == "causes", written
    # The fold reconciles the accounting: 3 stated = 2 written + 0 dropped + 1
    # folded, so the third meaning is counted, named and reconciled.
    assert (written["written"] + written["dropped"]
            + written["folded"]) == 3, written
    # ⛔ THE DIRECTION IS WRITTEN, NOT INHERITED (#11548 review, P2).
    # `create_direct_edge` defaults an ABSENT direction to "bidirectional" for
    # IMPL, so an unpassed direction would let EP propagate belief back along a
    # claim the story stated one way. `causes` is ruled `->`, so the graph holds
    # a one-way flow, and the receipt says what it holds.
    assert _stored_direction(_lane["sdk"], "retry storm", "deadline slip",
                             "IMPL") == "->", _edges(_lane["sdk"])
    from tortoise.live import direction_flows
    assert direction_flows("->") == (True, False)
    direct_rows = [row for row in written["edges"]
                   if row["write_path"] == "direct"]
    assert direct_rows and all(row["direction"] == "->"
                               for row in direct_rows), written["edges"]
    # The structural arm's writer records no direction at all — the receipt must
    # say None rather than invent one for it.
    structural_rows = [row for row in written["edges"]
                       if row["write_path"] == "structural"]
    assert structural_rows and all(row["direction"] is None
                                   for row in structural_rows), written["edges"]


def test_a_ruled_meaning_writes_the_direction_the_registry_rules(
        _lane, monkeypatch):
    """⛔ The direction comes from the MEANING's ruling, not one blanket value.

    A blanket one-way token close the "writer's IMPL default" hole and opened a
    worse one (#11548 review round 4, P1): ``mutuallySupportive`` and
    ``mutuallyExclusive`` are ruled ``<->`` in
    ``pack_registry.SEMANTICS_DIRECTIONS`` (ONTOLOGY §8 / #7852) and are
    reachable on this arm, so writing them one-way makes ``direction_flows``
    read ``(True, False)`` where the ruling says ``(True, True)`` — a regression
    against even the writer's own default for IMPL.

    FAILS IF ``_direction_for`` is replaced by a constant, or if it stops
    consulting the registry table.
    """
    from tortoise.live import direction_flows
    from tortoise.pack_registry import SEMANTICS_DIRECTIONS, SEMANTICS_MECHANISMS

    result = _emission(_ENTITIES, [
        {"src": "retry budget", "dst": "deadline slip",
         "predicate": "mutuallySupportive"},
        {"src": "retry budget", "dst": "retry storm",
         "predicate": "refutes"},
    ])
    cap = _capture(_lane, monkeypatch, result)
    written = cap["relation_write"]["edges"]
    assert len(written) == 2, written
    assert SEMANTICS_DIRECTIONS["mutuallySupportive"] == "<->"
    assert SEMANTICS_MECHANISMS["mutuallySupportive"] == "IMPL"
    stored = _stored_direction(
        _lane["sdk"], "retry budget", "deadline slip", "IMPL")
    assert stored == "<->", stored
    assert direction_flows(stored) == (True, True)
    # …and a one-way ruling is still one-way on the same run: `refutes` is a
    # NAND-typed meaning ruled `->`, so the mutual reading is not blanket.
    assert SEMANTICS_DIRECTIONS["refutes"] == "->"
    assert SEMANTICS_MECHANISMS["refutes"] == "NAND"
    assert _stored_direction(
        _lane["sdk"], "retry budget", "retry storm", "NAND") == "->"
    assert [row["direction"] for row in written] == ["<->", "->"], written


def test_relation_leg_is_idempotent_across_a_re_finalise(_lane, monkeypatch):
    """Re-finalising the same batch does not duplicate the edge (#11237 target).

    ⛔ The first revision of this test called ``_capture`` twice with the SAME
    ``session_id`` and compared the edge SET — which cannot fail, for two
    independent reasons (#11548 review, P1):

    1. a re-capture of an EXISTING, successfully captured session is the #1727
       REPLAY no-op (``sdk.py``: ``elif session_existed and not
       retry_failed_capture:`` → ``mode: "replayed"``), so
       ``_extract_session_v2`` — and therefore ``apply_relation_edges`` — never
       runs a second time; and
    2. the comparison was a SET of edge identities, and a duplicated edge has
       the SAME identity, so it collapses into the same element.

    Both holes are closed here: the second finalise is a REAL commit of the
    same batch — a distinct ``session_id``/``client_commit_id``, so the replay
    skip cannot answer for the write path, and the same entity NAMES, so the
    writers see the same edge identity — and the assertion is a graph COUNT.

    Revert-check (the mutant that must redden this): ``create_direct_edge``'s
    bare-pattern ``MERGE`` → ``CREATE``, or ``create_edge``'s ``MERGE`` →
    ``CREATE``. The second pass then leaves two edges per pair and this test
    fails on the count.
    """
    rels = [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry budget", "dst": "retry storm", "predicate": "hasPart"},
    ]
    _capture(_lane, monkeypatch, _emission(_ENTITIES, rels))
    first = _edge_counts(_lane["sdk"])
    assert first, "the first capture must have written an edge to re-finalise"
    assert all(count == 1 for count in first.values()), first

    second_session = f"{SESSION}_refinalise"
    again = _capture(_lane, monkeypatch,
                     _emission(_ENTITIES, rels, session_id=second_session),
                     session_id=second_session)
    # ⛔ ANTI-VACUITY: the second call must be a REAL finalise, not the #1727
    # replay no-op (which returns no `relation_write` block at all, so this
    # assertion is what makes the whole test non-vacuous).
    assert again["relation_write"]["written"] == 2, again["relation_write"]

    second = _edge_counts(_lane["sdk"])
    assert second == first, (
        "a re-finalise must MERGE onto the same edge, not add one: "
        f"before={first} after={second}")
    assert all(count == 1 for count in second.values()), second


def test_relation_leg_names_what_it_refused(_lane, monkeypatch):
    """A relation the registry binds to no path is DROPPED AND NAMED.

    ``transacts`` is in the emission's allowlist but neither in
    ``_VALID_EDGE_PREDICATES`` nor in ``SEMANTICS_MECHANISMS`` — so
    ``relation_write_paths()`` answers ``()`` and nothing may be invented in
    its place. An endpoint that resolves to NO entity this capture wrote is
    refused on the same rule — never guessed through.

    ⛔ The name written TWICE under different kinds (``#11235``: Layer-1
    identity is ``(name, kind)``) is NOT pinned here, and the entities below
    exist only as the collision a later relation WOULD hit: this lane cannot
    express it, because ``create_entity`` keys an Object by ``(name, label)``,
    so two kinds under one name collapse onto ONE node and the ambiguity never
    reaches the plan. The collision that DOES survive a capture is a name
    written to two different LABELS (an Object and a Subject), and the
    assertion for it is plan-level —
    ``tests/test_relation_write_11237.py::test_ambiguous_endpoint_is_refused_not_guessed``
    — which is also where a change to ``tortoise/relation_write.py`` alone is
    actually graded. (An earlier revision of this docstring claimed the
    coverage was here; it was not, and no assertion in this file reads
    ``#11235``.)
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
    transcript/summary text reaches a property.

    ⛔ THE POPULATION IS EVERY EDGE THE LEG WROTE, read by ID on BOTH ends — not
    ``(a:Object)-[e]->(b:Object)``. The structural arm writes ``ownedBy`` /
    ``managedBy`` / ``hasPart`` to **Subjects** as well as Objects, and a pin
    that only reads Object→Object edges left those outside the P0 gate
    (#11548 review, P2). A short-token guard is also applied to the label, so a
    sub-64-character prose fragment cannot pass it on the edges it does read.
    """
    story = ("the retry storm causes the deadline slip because the retry "
             "budget was consumed by the failover storm again")
    result = _emission([*_ENTITIES,
        # a SUBJECT: the structural arm writes ``ownedBy``/``managedBy`` to
        # Subjects, and those edges are outside any Object→Object read.
        {"name": "lumen auth", "kind": "team", "lifecycle": "created"},
    ], [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry budget", "dst": "retry storm", "predicate": "hasPart"},
        {"src": "retry storm", "dst": "lumen auth", "predicate": "ownedBy"},
    ])
    result["summary"] = story
    result["embed_list"] = {"summary": story}
    cap = _capture(_lane, monkeypatch, result)
    written = cap["relation_write"]["edges"]
    assert len(written) == 3, written
    assert any(row["predicate"] == "ownedBy" for row in written), written
    seen = 0
    for row in written:
        for props in _edge_props_by_id(
                _lane["sdk"], row["src_id"], row["dst_id"]):
            seen += 1
            for key, value in props.items():
                assert key not in {"summary", "_searchText", "content",
                                   "transcript", "raw"}, (key, props)
                if isinstance(value, str):
                    assert story not in value, (key, value)
                    assert value != story, (key, value)
                    assert len(value) <= 64, (key, value)
    assert seen == 3, seen


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
