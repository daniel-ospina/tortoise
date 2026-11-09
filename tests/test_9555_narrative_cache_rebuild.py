"""#9555 / R7 — the narrative SURVIVES the derived cache being deleted (b).

WHY THIS IS A SEPARATE FILE FROM `test_9555_narrative_carrier.py`, and it is not
tidiness. (b) has two halves with two different arming requirements:

* the CARRIER + the READ PATH need a Postgres (`TEST_RECORD_DB_URI`) and are
  armed in CI's `test-record-postgres` job — whose leg gate FAILS the job on ANY
  skip (`.github/scripts/record-postgres-leg-gate.py`, #9551), because an
  unarmed job and an armed-but-skipping one are indistinguishable from outside;
* the CACHE SURVIVAL half needs a FalkorDB as well (`TORTOISE_DB_URI`), which
  that job does not have. Keeping it in the same file would therefore make the
  armed job red on a skip it cannot prevent — or, worse, push a reviewer to
  delete the skip and ship a leg that asserts against a cache that was never
  touched.

So: this file costs one skip wherever a FalkorDB is absent, and it is where the
claim "deleting the derived cache and rebuilding from the record alone produces
the same narrative" is measured against a REAL graph that is really destroyed.

⛔ WHAT THE ASSERTIONS ARE AIMED AT (PR #9862 review, P2). A first draft read the
record on the SAME Postgres connection on both sides of the wipe and compared the
two answers. Postgres state is identical either side of a FalkorDB wipe, so that
equality was structurally guaranteed: a rebuild that reproduced NONE of the
narrative — or wrote unrelated nodes — passed green. The leg now measures the
three windows that can actually fail:

1. the RECORD is read while the cache is EMPTY (right after the destroy
   assertion), which is the state §9.1's claim is about;
2. the REBUILT CACHE is compared to the PRE-WIPE cache node for node (labels +
   every property), so the rebuild's OUTPUT is checked, not its node count;
3. the narrative is asserted to be **ABSENT** from the rebuilt cache while the
   RECORD still serves it (#10394) — the half a count can never see. The seed
   carries the capture's `SourceCreated` record deliberately: the old seed held
   no `:Source` record at all, so a rebuild that projected one was never tried
   against the prose.

⛔ WHAT CHANGED AT #10394, and why the pin moved rather than being deleted.
This leg used to assert the OPPOSITE of window 3 — that a rebuilt node CARRIED
`story_arc` — which was true, and was the defect: the RECORD → CACHE restore is
one of the routes that put §9.1's prose back into the graph, so #10000's write
deletion was undone by the next restore. §9.1 says the narrative lives in Postgres and
NOT in the graph, so *"the narrative survives the cache being deleted"* is now
measured where it is true: through the RECORD READER (`record_text.
serve_from_record`, window 1 and the post-wipe read at the end), while the
rebuilt cache is asserted prose-free. A pin that regressed to "the node carries
it" would be re-declaring the defect, so window 3 is inverted, not dropped.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

import pytest
from test_9555_narrative_carrier import (
    NARRATIVE,
    SESSION,
    _ensure_schema,
    _record_db,
    _replay_db,
    _seed,
    _tenant,
)

from tortoise import record_text


def _cache_nodes(proj) -> list[tuple[list[str], dict]]:
    """Every node in the cache: its labels and EVERY property it carries.

    `properties(n)` rather than a hand-picked list, so a rebuild that drops or
    invents a property is visible — a projection that wrote only `id` would
    satisfy an assertion that looked at `id`.
    """
    rows = proj.g.query(
        "MATCH (n) RETURN labels(n) AS labels, properties(n) AS props"
    ).result_set
    return [(list(labels), dict(props)) for labels, props in rows]


def _fingerprint(nodes) -> tuple[str, ...]:
    """A comparable, order-independent form of a cache snapshot."""
    return tuple(sorted(
        f"{sorted(labels)} {json.dumps(props, sort_keys=True, default=str)}"
        for labels, props in nodes))


def test_the_public_capture_path_REBUILDS_THE_CACHE_FROM_THE_RECORD_and_the_narrative_survives():
    """(b), the whole sentence: delete the derived cache, rebuild from the
    record alone, and the narrative is the SAME one.

    The narrative lives in the record (§9.1), so the assertion has three parts
    and all three matter: the record serves the text while the cache is EMPTY
    (otherwise "survives" is a claim about a cache that never went away), the
    rebuilt cache is the pre-wipe cache node for node (otherwise "rebuild"
    means "wrote something"), and the narrative is ABSENT from the rebuilt cache
    while the record still serves it (§9.1 puts it in Postgres and out of the
    graph — asserting the opposite was the defect `#10394` closed).

    ⚠️ Needs a FalkorDB as well as the record's Postgres, so it skips unless BOTH
    are armed — the record half runs in CI's `test-record-postgres` job, the
    cache half on a lane with `TORTOISE_DB_URI`.
    """
    conn = _record_db()
    if not os.environ.get("TORTOISE_DB_URI"):
        conn.close()
        pytest.skip("TORTOISE_DB_URI not set — no cache to destroy and rebuild")
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id, with_source=True)
        before = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert before.served

        from tortoise.cache_projection import read_record_view
        from tortoise.projection import FalkorProjection
        from tortoise.record_restore import restore_from_record_view

        # The RECORD→CACHE reader runs on the app role too: since #9600 it
        # scopes the connection with the tenant GUC and REFUSES a role that
        # could bypass RLS, so the provisioning superuser cannot read a view.
        # The view is materialised in memory, so the transaction ends here and
        # the restore below is a pure function of what was read.
        view_conn = _replay_db()
        try:
            view = read_record_view(view_conn, org_id=org_id, graph_id=graph_id)
            view_conn.commit()
        finally:
            view_conn.close()

        # The cache graph is minted EMPTY, which is what makes the first
        # restore a pure record→cache write; it is then DESTROYED and rebuilt a
        # second time, so "the narrative survived the cache being deleted" is a
        # statement about a cache that really went away (`test_` is required by
        # the projection's own L2 destructive-op guard).
        cache_graph = f"test_9555_narr_{uuid.uuid4().hex[:8]}"
        proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                        graph_name=cache_graph)
        try:
            assert view, "the record holds no rows — nothing to rebuild FROM"
            first = restore_from_record_view(
                view, org_id=org_id, graph_id=graph_id, cache_graph=cache_graph,
                project=proj)
            assert first.nodes_written > 0, (
                "the rebuild wrote nothing — 'the narrative survived' would then "
                "be a statement about a cache that was never restored")

            # ── WHAT THE CACHE HOLDS, BEFORE THE WIPE ─────────────────────
            # The reference the rebuild must REPRODUCE. A count cannot express
            # this: two runs writing different nodes both report `> 0`.
            before_nodes = _cache_nodes(proj)
            assert before_nodes, "the first restore left an empty cache"
            prefixed = [props for labels, props in before_nodes
                        if "Source" in labels]
            assert prefixed, (
                "the record's `SourceCreated` record produced no `:Source` node "
                f"— the cache holds {[labels for labels, _ in before_nodes]}, so "
                "there is no node for the narrative to be reproduced ON")
            assert not any("story_arc" in props for props in prefixed), (
                "the record→cache projection put §9.1's narrative on a "
                f"`:Source` node: {prefixed}. §9.1/D1 keeps the prose in "
                "`public.session_narrative` and OUT of the graph, so the restore "
                "is a WRITE door for the key #10000 deleted three others of "
                "(#10394 door 2)")

            # ── DELETE THE DERIVED CACHE ──────────────────────────────────
            proj.g.query("MATCH (n) DETACH DELETE n")
            rows = proj.g.query("MATCH (n) RETURN count(n)")
            remaining = int(rows.result_set[0][0]) if rows.result_set else 0
            assert remaining == 0, (
                f"the cache still holds {remaining} node(s) — the rebuild below "
                "would be adding to a cache that was never destroyed")

            # ── (1) THE RECORD, READ FROM AN EMPTY CACHE ──────────────────
            # ⛔ This is the window the claim is about, and the pre-fix draft
            # passed it over: the service must answer from the RECORD while the
            # cache that used to hold the same text holds nothing.
            during = record_text.serve_from_record(
                conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
            assert during.served, (
                "the record serves NOTHING while the cache is empty — §9.1's "
                "'the narrative is in the record, not the cache' is false")
            assert during.session.narrative == NARRATIVE
            assert during.session.redactions == 4
            assert [t.body for t in during.session.turns] == [
                t.body for t in before.session.turns]

            # ── (2) REBUILD FROM THE RECORD ALONE ─────────────────────────
            second = restore_from_record_view(
                view, org_id=org_id, graph_id=graph_id, cache_graph=cache_graph,
                project=proj)
            assert second.nodes_written > 0, (
                "the rebuild from the record alone wrote nothing")

            # ⛔ THE REBUILD'S OUTPUT, not its node count: the cache after the
            # wipe must BE the cache before it, property for property.
            after_nodes = _cache_nodes(proj)
            assert _fingerprint(after_nodes) == _fingerprint(before_nodes), (
                "the rebuilt cache is not the pre-wipe cache:\n"
                f"  before: {_fingerprint(before_nodes)}\n"
                f"  after:  {_fingerprint(after_nodes)}")
            assert not any("story_arc" in props
                           for _labels, props in after_nodes), (
                "the rebuilt cache holds §9.1's narrative on a node — the "
                "restore re-materialised what #10000 stopped writing, so "
                "the next routine restore undid #10000's write deletion "
                "(#10394 door 2)")
        finally:
            proj.close()

        after = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert after.served
        assert after.session.narrative == before.session.narrative == NARRATIVE
        assert after.session.redactions == before.session.redactions == 4
        assert [t.body for t in after.session.turns] == [
            t.body for t in before.session.turns]
    finally:
        conn.close()


def test_the_document_fold_cannot_put_the_narrative_back_on_a_source():
    """#10389 review ISSUE 1, door 1 — the OPEN passthrough is now closed.

    ⚠️ IT LIVES IN THIS FILE, NOT IN `test_9555_narrative_carrier.py`, because it
    needs a GRAPH: the carrier file is executed by CI's `test-record-postgres`
    job, whose leg gate FAILS the job on ANY skip (#9551) and which exports no
    `TORTOISE_DB_URI` — a URI-gated test there would red a job over a cache it
    does not provision. This file is the one that is deliberately NOT in that
    job for exactly this reason (see its module docstring).

    `_upsert_document` passes NO `allow_keys` DELIBERATELY (#228 pins the
    document route as a caller passthrough), and `story_arc` was in none of
    `_SOURCE_HANDLED` / `_DOC_PASSTHROUGH_HANDLED` / `_DOC_RETIRED_KEYS` — so a
    `DocumentCreated` carrying it landed §9.1's prose on the `:Source` node
    verbatim. MEASURED before the fix: `create_document(url, kind,
    story_arc=<narrative>)` (the SDK door behind the MCP tool
    `tortoise_create_document`) left `s.story_arc` = the PROSE on the node.

    (1) FAILS pre-fix, at the FIRST assertion rather than at the graph probe: the
        deny-set membership check below reds before the fold runs, because
        `story_arc` was in no deny set at all. The prose-on-the-node probe is the
        second, independent arm — it is what reds if the entry is dropped again
        while the membership check is (wrongly) satisfied.
    (2) DOES NOT CLOSE #228: a genuinely arbitrary caller prop still lands, so
        this is a deny-list entry and not a blanket refusal. It also asserts the
        key stays DECLARED for reads — declared ⇒ readable, listed in the
        passthrough reserve ⇒ never written from a payload (#3998/D30).
    (3) REACHABLE: the fold the production writer calls, on a real graph.
    """
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("TORTOISE_DB_URI not set — the fold needs a graph to write")
    from tortoise.projection import FalkorProjection
    from tortoise.projection.entities import _EntityHandlers as _FP

    assert "story_arc" in _FP._DOC_PASSTHROUGH_HANDLED, (
        "the document passthrough admits `story_arc` again — a DocumentCreated "
        "carrying §9.1's narrative then writes the PROSE onto a :Source node "
        "(#10389 ISSUE 1 door 1)")
    assert "story_arc" in _FP._SOURCE_NODE_PROPS, (
        "`story_arc` left the READ declaration — legacy nodes that still carry "
        "it would stop reading back, which is the opposite defect")

    proj = FalkorProjection.from_uri(
        os.environ["TORTOISE_DB_URI"],
        graph_name=f"test_9555_narr_doc_{uuid.uuid4().hex[:8]}")
    try:
        url = "docs/narrative-door.md"
        proj._upsert_document({
            "id": url, "title": "narrative door", "document_kind": "note",
            "story_arc": NARRATIVE, "project": "kept-228",
            "suppress_embedding": True,
        })
        rows = proj.g.query(
            "MATCH (s:Source {url:$u}) RETURN s.story_arc, s.project",
            params={"u": url}).result_set
        assert rows, "the document fold minted no :Source — the probe proved nothing"
        arc, project = rows[0]
        assert arc is None, (
            "§9.1's narrative reached a `:Source` node through the DOCUMENT "
            "fold — D1 puts it in public.session_narrative only, and a "
            "record-sourced restore would then re-rent the RAM #10000 exists "
            f"to stop (#10389 ISSUE 1 door 1). Got {arc!r}")
        assert project == "kept-228", (
            "#228's arbitrary-prop passthrough was closed along with "
            "`story_arc` — the deny entry must be ONE key, not a blanket refusal")
    finally:
        proj.close()


def test_the_event_fold_and_its_replay_cannot_put_the_narrative_on_an_event():
    """#10389 review round 5 ISSUE 1 (P2) — the `:Event` half of the enumeration.

    ⚠️ SAME FILE AND SAME REASON as the document pin above: it needs a GRAPH, and
    the carrier file runs in CI's zero-skip `test-record-postgres` job (#9551).

    `create_event(...)` (the SDK door behind the MCP tool `tortoise_create_event`)
    emits `EventRecorded`, and `_upsert_event` is that record's replay fold — its
    extras ride `_persist_extra_props`, whose skip set is
    `_META_KEYS | _EVENT_HANDLED`. `story_arc` was in neither, and ONTOLOGY §4.4
    declares it on `:Source` only, so on `:Event` it was an UNDECLARED property
    on the open passthrough: the #3998/D30 shape one label over, and DURABLE —
    the same fold runs on replay, so a `rebuild_all` restores it. MEASURED
    before the entry: one call left `e.story_arc` = the PROSE on the
    AgentSession node, the very anchor this PR's own
    `tests/test_commit_endpoint.py::TestFourNodeChain` asserts is prose-free.

    (1) FAILS pre-fix, at the FIRST assertion (the skip-set membership) — the
        graph arms below are independent, so a dropped entry cannot be masked by
        a passing one.
    (2) DOES NOT CLOSE the open passthrough (#228): a genuinely arbitrary caller
        prop still lands, and the REPLAY arm proves it is the KEY that is denied
        rather than the record.
    (3) REPLAY IS THE SAME FOLD: the second arm replays a legacy `EventRecorded`
        carrying the key into a FRESH graph and reads nothing back, so the deny
        holds for a rebuild and not only for the live write.
    """
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("TORTOISE_DB_URI not set — the fold needs a graph to write")
    from tortoise.projection import FalkorProjection
    from tortoise.projection.entities import _EntityHandlers as _FP
    from tortoise.sdk import TortoiseSDK

    assert "story_arc" in _FP._EVENT_HANDLED, (
        "the EventRecorded fold admits `story_arc` again — `create_event(..., "
        "story_arc=…)` then writes §9.1's PROSE onto an `:Event` node and a "
        "rebuild restores it (#10389 ISSUE 1 door 1)")

    graph = f"test_9555_narr_event_{uuid.uuid4().hex[:8]}"
    # ⛔ THE DOOR NEEDS A HOME TO ACCEPT THE KEY (#10000, #10389 review cycle 2 P1).
    # `_refuse_unpaired_narrative` refuses a narrative with no session and an SDK
    # with no journal, because either shape stores the prose NOWHERE — so this
    # fixture must arm the pair to reach the FOLD this test is about. The pin is
    # unchanged: the key must not reach the node, and `project` must still land.
    _tmp = tempfile.mkdtemp(prefix="narr9555-")
    sdk = TortoiseSDK(graph_name=graph,
                      event_log_path=os.path.join(_tmp, "journal.jsonl"))
    try:
        ev = sdk.create_event("narrative-event-door", "AgentSession",
                              story_arc=NARRATIVE, project="kept-228",
                              sessionId="test-9555-event-fold-session")
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) RETURN e.story_arc, e.project",
            params={"e": ev["id"]}).result_set
        assert rows, "create_event minted no :Event — the probe proved nothing"
        arc, project = rows[0]
        assert arc is None, (
            "§9.1's narrative reached an `:Event` node through the SDK door "
            "behind `tortoise_create_event` — D1 puts it in "
            f"public.session_narrative only (#10000). Got {arc!r}")
        assert project == "kept-228", (
            "#228's arbitrary-prop passthrough was closed along with "
            "`story_arc` — the deny entry must be ONE key, not a blanket refusal")
    finally:
        sdk.close()

    proj = FalkorProjection.from_uri(
        os.environ["TORTOISE_DB_URI"],
        graph_name=f"test_9555_narr_event_replay_{uuid.uuid4().hex[:8]}")
    try:
        eid = f"ev-replay-door-{uuid.uuid4().hex[:8]}"
        # FLAT record shape (`inner = event.get("event", event)` in
        # `_upsert_event` — a nested `event` dict would carry ONLY its own keys
        # and the probe would prove nothing about the extras).
        proj.apply({"type": "EventRecorded", "id": eid, "eventId": eid,
                    "name": "legacy journal record",
                    "story_arc": NARRATIVE, "project": "kept-228"})
        rows = proj.g.query(
            "MATCH (e:Event {eventId:$e}) RETURN e.story_arc, e.project",
            params={"e": eid}).result_set
        assert rows, "the replay fold minted no :Event — the probe proved nothing"
        arc, project = rows[0]
        assert arc is None, (
            "a legacy `EventRecorded` carrying `story_arc` REPLAYS the prose "
            "onto the node, so `rebuild_all` re-rents the RAM #10000 exists to "
            f"stop (#10389 ISSUE 1 door 1). Got {arc!r}")
        assert project == "kept-228", (
            "the replay arm must deny the KEY, not every extra prop")
    finally:
        proj.close()


def test_the_tenant_route_cannot_put_the_narrative_back_on_an_event():
    """#10389 review round 5 ISSUE 1 (P2) — the SECOND `:Event` door.

    `_EVENT_HANDLED` closes the FOLD, and it cannot close this one: the generic
    tenant surface (`tortoise_update_entity`) writes caller props straight through
    `SET n += $props` on its non-Point label loop and never consults a label
    skip-set. MEASURED before the guard: `update_entity(<agentSession eventId>,
    story_arc=<narrative>)` was ACCEPTED, persisted live, journalled into an
    `EntityMutated` record and SURVIVED a `rebuild_all` — the `__runId` hole
    (#5196 round 3) one property over, on the anchor this PR declares prose-free.

    (1) FAILS pre-fix: the write is accepted and reads back, so both the
        `pytest.raises` and the read-back assertion red.
    (2) DOES NOT CLOSE the tenant surface: a caller prop it is FOR is still
        accepted through the same call, so this is one refused key and not a
        blanket refusal (the #4649 lesson: a broad refusal broke a live contract).
    (3) REACHABLE: the SDK method behind the MCP tool `tortoise_update_entity`,
        on its own graph.
    """
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("TORTOISE_DB_URI not set — the route needs a graph to write")
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(graph_name=f"test_9555_narr_evroute_{uuid.uuid4().hex[:8]}")
    try:
        ev = sdk.create_event("narrative-event-tenant", "AgentSession")
        eid = ev["id"]

        with pytest.raises(ValueError,
                           match="not a caller-settable property on an :Event"):
            sdk.update_entity(eid, story_arc=NARRATIVE)

        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) RETURN e.story_arc",
            params={"e": eid}).result_set
        assert rows, "the probe Event is missing — the refusal proved nothing"
        assert rows[0][0] is None, (
            "the S1 narrative reached an `:Event` node through the tenant route "
            "— §9.1/D1 places it in public.session_narrative only (#10000)")

        # ...and the surface still works for a prop it is FOR.
        sdk.update_entity(eid, eventStatus="completed")
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) RETURN e.eventStatus",
            params={"e": eid}).result_set
        assert rows and rows[0][0] == "completed", (
            "the refusal was too broad — `eventStatus` is an ordinary Event prop "
            "and must still land through this route")
    finally:
        sdk.close()


def test_the_event_journal_replay_drops_the_prose():
    """The `EntityMutated` → `:Event` half — the deny that survives a rebuild.

    The two doors the round-5 review named are refused at the WRITE
    (`_EVENT_HANDLED` for the `EventRecorded` fold, `_EVENT_SERVER_MANAGED_PROPS`
    for the tenant route), and one of them is refused at the REPLAY too because
    that fold is the replay fold. This is the third surface, and neither of those
    reaches it: `_fold_entity_mutation` applies an `EntityMutated` record's
    `state` verbatim for every label it has no deny arm for. The tenant route
    journalled `story_arc` that way BEFORE the guard existed, so without this arm
    a `rebuild_all` keeps restoring the prose — the "SURVIVED `rebuild_all`" half
    of the finding — while the live door is shut.

    It mirrors the `:Source` arm this PR already carries, whose set is read by the
    same replay code (and whose residual (a) had to stop saying "nodes already
    carrying the prose keep it" while a replay reclaimed it). Residual (a) now
    states it for BOTH anchors.

    Two arms so neither can pass vacuously: the prose is gone, AND the journalled
    op's own other key still replays — a deny that dropped the whole state map
    would silently lose a legitimate Event mutation.
    """
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("TORTOISE_DB_URI not set — the fold needs a graph to write")
    from tortoise.projection import FalkorProjection
    from tortoise.projection import entities as _E

    assert "story_arc" in _E._EVENT_SERVER_MANAGED_PROPS, (
        "`story_arc` left `_EVENT_SERVER_MANAGED_PROPS` — the tenant route "
        "accepts it on an `:Event` again AND the `EntityMutated` replay arm "
        "stops dropping it (#10389 ISSUE 1)")

    proj = FalkorProjection.from_uri(
        os.environ["TORTOISE_DB_URI"],
        graph_name=f"test_9555_narr_event_replay2_{uuid.uuid4().hex[:8]}")
    try:
        eid = f"ev-replay-mutated-{uuid.uuid4().hex[:8]}"
        proj._upsert_event({"id": eid, "eventId": eid,
                            "eventKind": "agentSession"})
        proj._fold_entity_mutation({
            "op": "restatus", "id": eid, "label": "Event",
            "state": {"story_arc": NARRATIVE, "status": "completed"},
        })
        rows = proj.g.query(
            "MATCH (e:Event {eventId:$e}) RETURN e.story_arc, e.status",
            params={"e": eid}).result_set
        assert rows, "the probe Event is missing — the refusal proved nothing"
        arc, status = rows[0]
        assert arc is None, (
            "the `EntityMutated` → `:Event` replay arm restored §9.1's prose on "
            f"the node, so a `rebuild_all` re-rents the RAM #10000 stops "
            f"(#10389 ISSUE 1). Got {arc!r}")
        assert status == "completed", (
            "the replay arm dropped the WHOLE state map instead of the one "
            "denied key — a journalled Event mutation must still replay its "
            "other properties")
    finally:
        proj.close()


def test_the_point_clear_removes_the_prose_and_keeps_the_node():
    """#11325 — the ONE shipped repair that clears the prose and keeps the entity.

    After `#11325` the reconciler's repair text says the not-projectable class has
    no route that RESTORES the value, and that on a `:Point` a targeted CLEAR
    does ship: `update_point(<id>, story_arc=None)` removes the property and
    KEEPS the node — ⛔ and ONLY where the record still holds the text for the
    session (`public.session_narrative.narrative`; NOT the point's own `props`
    tail — the clear's OWN record write overwrites it with a stored null, pinned
    by `test_the_point_props_tail_is_not_a_record_home_for_the_prose` below);
    where the narrative row does not carry it the node's copy is the only one no
    shipped route can put back on a production graph, so
    that clear
    deletes the prose. That is load-bearing operator guidance — it is the difference
    between "clear the stale copy" and "wipe the graph" — and it was asserted as
    measured at six prose sites while nothing in the suite exercised the door.
    `tests/test_10000_narrative_record_home.py` pins the REFUSAL of a non-empty
    narrative on the same call, which is a different claim.

    The seed goes through the JOURNAL REPLAY fold (`_upsert_point_props`), not
    through `create_point`: since `#10389` both the create and the update door
    refuse a NON-EMPTY narrative, so a legacy copy on a `:Point` is exactly what
    a replayed `PointAdded` (or a pre-guard write) leaves behind.

    Two arms so neither can pass vacuously:
      (1) the property is GONE while the node and its OTHER props are still there
          — a clear that deleted the node is `tortoise_delete`, not this door;
      (2) re-running the SAME creation fold puts the prose BACK: the clear is
          JOURNAL-bound (`#10429`'s replay half), so a journal replay — which
          routes its `PointAdded` through `_upsert_point_props` — re-materialises
          it, while a RECORD → CACHE rebuild replays the stored
          `props.story_arc = null` and keeps it clear. ⚠️ SCOPE: this arm runs the
          FOLD, not `rebuild_all`, so it pins that the creation fold still
          persists the key (a point deny-set lever would red it) and does NOT pin
          `rebuild_all`'s routing of that fold — that half is `#10429`'s, and §17.2
          says so at the citation.
    """
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("TORTOISE_DB_URI not set — the clear needs a graph to write")
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(graph_name=f"test_9555_pointclear_{uuid.uuid4().hex[:8]}")
    try:
        proj = sdk._get_proj()
        pid = f"pt-clear-{uuid.uuid4().hex[:8]}"
        content = "a legacy turn that carried the prose"
        creation = {"id": pid, "content": content, "is_episodic": True,
                    "story_arc": NARRATIVE}
        proj._upsert_point_props(creation)
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.story_arc, n.content",
            params={"id": pid}).result_set
        assert rows and rows[0][0] == NARRATIVE, (
            "the seed did not land the prose on the `:Point` — the clear below "
            f"would prove nothing. Got {rows!r}")

        sdk.update_point(pid, story_arc=None)

        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.story_arc, n.content",
            params={"id": pid}).result_set
        assert rows, (
            "`update_point(<id>, story_arc=None)` DELETED the node — the shipping "
            "repair is a CLEAR that keeps the entity (#11325); a node delete is "
            "`tortoise_delete` and journals the delete that removes its record row")
        arc, kept = rows[0]
        assert arc is None, (
            "the clear left the prose on the node — `SET n += $props` with a "
            f"`None` value is supposed to REMOVE the key. Got {arc!r}")
        assert kept == content, (
            "the clear took the node's OTHER properties with it; it must remove ONE "
            f"key. Got {kept!r}")

        # (2) JOURNAL-BOUND: the CREATION record still carries the prose, and its
        # fold is the journal replay fold — so replaying it re-materialises what
        # the clear removed. (A RECORD → CACHE rebuild replays the stored null
        # instead, and keeps it clear — `_props_from_row` + `node_statement`.)
        proj._upsert_point_props(creation)
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.story_arc",
            params={"id": pid}).result_set
        assert rows and rows[0][0] == NARRATIVE, (
            "the creation FOLD no longer re-materialises the prose from a record "
            "that carries it — section 17.2's JOURNAL-bound statement rests on this "
            "fold, so it and the `update_point` clear's scope must be re-stated "
            "(note this arm does NOT cover `rebuild_all`'s routing of the fold, "
            "which is `#10429`'s)")
    finally:
        sdk.close()


def test_the_point_props_tail_is_not_a_record_home_for_the_prose():
    """#11325 review cycle 3 — the clear CONSUMES the tail it is licensed by.

    The per-row test licensed a `:Point` clear "where the record still HOLDS the
    text" and counted the point's OWN `props` tail as one of the two homes. That
    second home is consumed by the very clear it licenses, so on a row whose only
    record home is the tail the "safe" clear destroys the last copy the record's
    KEYED VIEW holds:

      (1) `update_point(<id>, story_arc=None)` journals `PointRevised` carrying
          `story_arc: None` — the `None` is what makes the clear work at all;
      (2) `_split_properties("point", …)` finds no `story_arc` column on
          `public.point` and the name is not refused for `point`, so it lands in
          the tail — with NO null filter;
      (3) `_patch_statement` writes the tail as a RIGHT-HAND merge
          (`props = props || %s::jsonb`), so `{"story_arc": null}` OVERWRITES the
          prose that tail held.

    So the operator guidance names `public.session_narrative.narrative` and
    nothing else (§17.2, §9.1(a), `record_reconcile`, `record_serve`, the epic's
    cache-projection section and the changelog's cycle-3 stamp). ⛔ This is the
    pin that keeps those docs honest rather than restating them: a null filter
    added for `point` (making the tail survive) or a left-hand merge would make
    the tail a home again, and this test reds until the guidance says so.
    """
    import tortoise.record_projection as rp

    columns = rp.table_columns()
    refused = rp.refused_props_names()
    assert "story_arc" not in columns["point"], (
        "`public.point` now HAS a `story_arc` column — the tail argument below no "
        "longer holds, so re-derive the §17.2 guidance before removing this pin"
    )
    col_values, tail = rp._split_properties(
        "point", {"id": "pt-tail-home", "story_arc": None},
        columns=columns, refused=refused,
    )
    assert "story_arc" not in col_values, col_values
    assert "story_arc" in tail and tail["story_arc"] is None, (
        "a cleared `story_arc` no longer reaches the point's props tail — this "
        "pin's premise moved (a null filter was added?), so the §17.2 per-row "
        f"test may need the tail back as a home. Got tail={tail!r}"
    )

    stmt = rp._patch_statement(
        "org", "graph", rp.Node("point", "Point", "id"), "pt-tail-home",
        col_values, tail,
    )
    assert "props = props ||" in stmt.sql, (
        "the point patch is no longer a RIGHT-HAND props merge, so a stored prose "
        "tail would SURVIVE the clear and the tail would be a legitimate home "
        f"again — re-derive the §17.2 guidance. SQL: {stmt.sql!r}"
    )
    # …AND the null actually REACHES that merge: the right-handed form alone is not
    # enough, because a null-stripping serializer would emit `props || '{}'` and
    # leave the stored prose standing while the SQL above still reads right-handed.
    assert json.loads(stmt.params[1]) == {"story_arc": None}, (
        "the cleared `story_arc` no longer reaches the right-hand merge's parameter "
        "(a null-stripping serializer?), so the stored prose would SURVIVE the "
        f"clear and the tail would be a home again. Got {stmt.params!r}"
    )


# ── door 2: the RECORD → CACHE restore (#10394) ─────────────────────────────
#
# `#10389` closed one write door; this is the exit door it left open, and it is
# a PLACEMENT decision rather than a key-set entry, which is why it is its own
# issue. The chain is four hops and none of them is a bug on its own:
#
#   cache_projection.projected_columns("source")   ← keeps `story_arc`
#     → _props_from_row(...)                       ← emits it as a property
#       → node_from_row(...) → RecordNode.props    ← carries it
#         → node_statement(...) `SET n += $props`  ← WRITES the prose on a node
#
# so a restore from the record re-materialises exactly what #10000's write
# deletions removed. The record column is legitimately PRIMARY
# (`public.source.story_arc`, `public.event.story_arc`: §9.1 says the narrative
# lives in Postgres, and the record IS Postgres) — what §9.1/D1 forbids is the
# prose in the GRAPH. Authority in the record is therefore not projectability
# into the cache, and the projection had no vocabulary for that distinction.
#
# ⛔ The fix is at that seam — STOP THE WRITE — not a purge pass after it: a
# post-restore sweep would still rent the RAM for the duration and would have to
# be re-run on every restore, i.e. #10000's write deletion undone in a loop.

_NARRATIVE_DOOR_ROW = {
    # The RECORD row, not a graph node: every column a `:Source` row carries
    # that this probe needs to prove the column SET is still projected.
    "url": "docs/door-2-record-restore.md",
    "title": "door 2",
    "summary": "a summary (must still land)",
    "status": "active",
    "story_arc": NARRATIVE,
}


def test_the_record_restore_writes_no_story_arc_on_any_node():
    """#10394 — door 2: the record→cache RESTORE must not re-rent §9.1's prose.

    Three arms (the second split into 2b/2c), and the first two red on the
    PRE-FIX head — not on this tree, where all of them pass. A fourth point
    below is a SCOPE NOTE, not an arm: it names where the surviving-text half is
    measured (the Postgres-armed sibling leg), and it asserts nothing here:

    (1) the projection's COLUMN VOCABULARY excludes the key for BOTH record
        tables, so the deny is a placement statement rather than a per-table
        patch. This is the arm that reds before any graph is touched.
    (2) `node_from_row` — the seam `read_record_view` uses — drops `story_arc`
        from a row that CARRIES it, while the row's other columns still project.
        The input row really holds the prose (asserted), so the deny is
        non-vacuous: it is one key dropped, not an empty row.
    (3) a REAL restore into a graph that already holds a legacy node carrying
        the prose leaves `sum(size(n.story_arc))` at 0 over every node — this is
        the "restore → 0" acceptance, and it is the arm that would
        red if the deny were implemented in the reader only.
    (scope) `public.session_narrative` is untouched by this change: the record
        READER is not on this path at all (`record_text.serve_from_record` reads
        the narrative table), which is why the surviving-text half of the claim
        stays in the Postgres-armed leg above.

    ⚠️ Needs a FalkorDB only — the view is built by hand from RECORD-SHAPED
    rows, so this leg runs wherever a cache exists rather than only where a
    Postgres does (that half is the sibling Postgres+FalkorDB leg).
    """
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("TORTOISE_DB_URI not set — the restore needs a cache to write")
    from tortoise import cache_projection as cp
    from tortoise.projection import FalkorProjection
    from tortoise.record_projection import table_columns
    from tortoise.record_restore import restore_from_record_view

    # ── (1) THE VOCABULARY: the key is not projectable from EITHER table ──
    for table in ("source", "event"):
        assert "story_arc" in table_columns()[table], (
            f"`public.{table}.story_arc` left the RECORD — this issue's premise "
            "is that the record KEEPS the column and only the cache must not "
            "materialise it; if the column is gone, the placement row and this "
            "pin both need re-deciding rather than patching")
        assert "story_arc" not in cp.projected_columns(table), (
            f"`projected_columns({table!r})` still keeps `story_arc`, so the "
            f"RECORD → CACHE restore writes §9.1's PROSE onto a :{table} node "
            "again and every cache rebuild re-rents the RAM #10000 stops "
            "(#10394 door 2)")

    # ── (2) THE SEAM: a row that CARRIES the prose loses exactly that key ──
    source_row = dict(_NARRATIVE_DOOR_ROW)
    assert source_row["story_arc"] == NARRATIVE, (
        "the probe row does not carry the narrative — the deny below would be "
        "vacuous")
    node = cp.node_from_row("Source", source_row)
    assert "story_arc" not in node.props, (
        "`node_from_row` projected §9.1's narrative out of a record row — this "
        f"is the hop that feeds the restore's `SET n += $props` (#10394). "
        f"Got {node.props.get('story_arc')!r}")
    assert node.props.get("summary") == "a summary (must still land)", (
        "the deny dropped the whole row instead of the one column: a "
        "record-sourced cache must stay as rich as the record everywhere §9.1 "
        "does not forbid a column")

    event_row = {"event_id": "ev-door-2", "event_kind": "agentSession",
                 "summary": "an event summary", "story_arc": NARRATIVE}
    event_node = cp.node_from_row("Event", event_row)
    assert "story_arc" not in event_node.props, (
        "the `:Event` anchor of door 2 is still projected — `public.event."
        "story_arc` re-materialises the prose on the AgentSession node the "
        "commit path this PR's sibling asserts is prose-free (#10394)")
    assert event_node.props.get("summary") == "an event summary", (
        "the `:Event` deny dropped the row instead of the one column")

    # ── (2b) THE OPEN-SET `props` TAIL: the back door ────────────────────
    # `_props_from_row` copies the tail VERBATIM, so a legacy row that carried
    # the key there would reach the graph while the column door stayed shut.
    # Not reachable through today's writer (`record_projection` classifies
    # `story_arc` as a COLUMN) — which is exactly why it needs its own arm: a
    # door that is closed only because nobody has used it yet is a door with no
    # pin, and the neighbouring `_EPISODIC_SOURCE_PROSE_COLUMNS` note states the
    # same rule ("a guard that stops guarding without reddening is worse than no
    # guard").
    tailed = cp.node_from_row(
        "Source", {"url": "docs/door-2-tail.md",
                   "props": {"story_arc": NARRATIVE, "openSetProp": "kept"}})
    assert "story_arc" not in tailed.props, (
        "§9.1's narrative reached the node through the `props` TAIL — the tail "
        "is copied verbatim, so the not-projectable deny must filter it there "
        f"too (#10394). Got {tailed.props.get('story_arc')!r}")
    assert tailed.props.get("openSetProp") == "kept", (
        "the tail filter dropped the whole open set instead of the one denied "
        "key — `#228`'s passthrough must survive on this door")

    # ── (2c) BOTH SPELLINGS: a forbidden AMBIGUOUS column ────────────────
    # `CACHE_FORBIDDEN_COLUMNS` holds RECORD COLUMN names; the `props` tail is
    # keyed by GRAPH PROPERTY names. They coincide only for a single-spelled
    # column, and FIVE columns here are ambiguous — so the tail filter widens
    # through `placement_spellings`. ⛔ Without this arm the widen could be
    # reverted to a column-names-only comparison and NOTHING would red
    # (measured by review: the whole seam suite stayed green), which is exactly
    # the "deny that stops denying without reddening" the widen exists to stop.
    # `source.session_id` is a real ambiguous column in this projection
    # (`sessionId` / `session_id`), used here as a STAND-IN for a future entry.
    import tortoise.cache_projection as _cp

    original = _cp.CACHE_FORBIDDEN_COLUMNS
    spellings = _cp.placement_spellings().get("source", {}).get("session_id", ())
    assert len(spellings) > 1, (
        "`source.session_id` is no longer a multi-spelled column, so this arm's "
        f"stand-in proves nothing about ambiguity (spellings: {spellings})")
    try:
        _cp.CACHE_FORBIDDEN_COLUMNS = dict(original)
        _cp.CACHE_FORBIDDEN_COLUMNS["source"] = (
            frozenset(original.get("source", frozenset())) | {"session_id"})
        _cp.projected_columns.cache_clear()
        ambiguous = cp.node_from_row(
            "Source", {"url": "docs/door-2-ambiguous.md",
                       "props": {"sessionId": "S", "session_id": "S",
                                 "openSetProp": "kept"}})
        assert "sessionId" not in ambiguous.props and "session_id" not in ambiguous.props, (
            "the `props`-tail deny matched only the COLUMN spelling, so an "
            "AMBIGUOUS forbidden column walks through the tail while the column "
            f"door stays shut (#10394). Got {sorted(ambiguous.props)}")
        assert ambiguous.props.get("openSetProp") == "kept", (
            "the widen dropped an unrelated tail key — it must deny the exact "
            "spellings, not the tail")
    finally:
        _cp.CACHE_FORBIDDEN_COLUMNS = original
        _cp.projected_columns.cache_clear()

    # ── (3) A REAL RESTORE over a cache that still holds a legacy copy ─────
    from tortoise.cache_projection import RecordView

    view = RecordView(nodes=(node, event_node), relations=())
    graph = f"test_10394_restore_{uuid.uuid4().hex[:8]}"
    proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                    graph_name=graph)
    try:
        # A LEGACY state: a node that still carries the prose. No purge removes
        # it (`#10017`'s purge never matches a `:Source` — #11325); the restore runs
        # over it
        # (it wipes first), so a restore that re-materialises the key puts it
        # back and this leg reads non-zero.
        proj.g.query(
            "MERGE (s:Source {url:$u}) SET s.story_arc = $arc",
            params={"u": "legacy/pre-purge.md", "arc": NARRATIVE})
        # A legacy copy of the prose is what the restore must not re-create;
        # the pre-restore count is informational (the wipe removes the legacy
        # node either way, so it can never be the assertion). ⛔ NOT "#10017's
        # purge's state": that purge deletes legacy episodic turn `:Point`s and
        # never matches a `:Source` (#11325).

        out = restore_from_record_view(
            view, org_id="org-door-2", graph_id="g-door-2", cache_graph=graph,
            project=proj)
        assert out.nodes_written == 2, (
            f"the restore wrote {out.nodes_written} node(s), not the view's 2 — "
            "the probe below would then be reading a cache that was not "
            "restored")

        bytes_of_prose = proj.g.query(
            "MATCH (n) RETURN sum(size(n.story_arc))").result_set[0][0]
        assert not bytes_of_prose, (
            "the RESTORE re-materialised §9.1's narrative as a node property — "
            "`sum(size(n.story_arc))` = "
            f"{bytes_of_prose} B after a record→cache restore, so the next "
            "routine recovery re-rents the prose §9.1 removed (#10394 door 2; "
            "`#10017`'s purge is the legacy turn population and never matched "
            "a `:Source` — #11325)")

        held = proj.g.query(
            "MATCH (s:Source {url:$u}) RETURN s.summary",
            params={"u": _NARRATIVE_DOOR_ROW["url"]}).result_set
        assert held and held[0][0] == "a summary (must still land)", (
            "the restored `:Source` lost its other columns — the cache must "
            "stay as rich as the record everywhere §9.1 does not forbid it")
    finally:
        proj.close()


def test_the_reconciler_reads_a_pre_purge_cache_copy_as_graph_only_not_divergence():
    """Door 2's third consumer: `record-reconcile` must not call it a divergence.

    No graph and no Postgres — this is the partition the verb applies to a cache
    snapshot it already read, so it is a pure function of the two views plus the
    per-key placement set.

    ⛔ WHY THIS IS NOT COVERED BY `tests/test_reconciliation.py` OR
    `tests/test_record_reconcile_trigger.py`: both are green at HEAD *and* at the
    pre-fix head — they exercise the partition with §17.5's cache-owned scalars,
    which the record schema REFUSES, and never with a column the record HOLDS and
    the projection now declines. Measured: `story_arc` appears in neither file.
    Without this arm, a change that made `story_arc` a permanent
    `field_mismatches` on every cache that still carries a pre-purge copy would
    red nothing — the "signal indistinguishable from noise" this module's own
    docstring grades P1, since a healthy pair that still holds legacy prose would
    exit 3 forever.
    """
    from tortoise import cache_projection as cp
    from tortoise.record_reconcile import reconcile_record_and_cache

    key = cp.declared_key("Source", "docs/door-2.md")
    # The RECORD side as `read_record_view` builds it: through the same
    # projector, so `story_arc` is absent by construction (that is what makes
    # the pair disagree in one direction only).
    record_props = dict(cp.node_from_row(
        "Source", {"url": "docs/door-2.md", "title": "door 2"}).props)
    assert "story_arc" not in record_props, (
        "the record side of this probe carries the narrative, so the partition "
        "below would be comparing a record-held name — this arm would then "
        "prove nothing about the DECLINED case (#10394)")

    # The CACHE side: a node written before the purge, so it still carries the
    # prose alongside every property the projection still writes.
    cache_props = dict(record_props)
    cache_props["story_arc"] = NARRATIVE

    placed = {key: frozenset(cp.projected_properties("source")) | {"url"}}
    result = reconcile_record_and_cache({key: record_props}, {key: cache_props},
                                       placed=placed)

    assert result.report.field_mismatches == 0, (
        "`record-reconcile` reports §9.1's narrative as a FIELD MISMATCH against "
        "a record that is correct — every cache still holding a pre-purge copy "
        "then diverges forever, which is the noise floor this module refuses "
        f"(#10394). Got {result.report.field_mismatches}")
    assert dict(result.graph_only).get("story_arc", 0) >= 1, (
        "the pre-purge copy was not counted as `graph_only` either — it has "
        "then vanished from the report entirely, which is the other way to "
        f"lose it. Got {dict(result.graph_only)}")
