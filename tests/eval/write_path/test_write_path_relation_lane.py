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
