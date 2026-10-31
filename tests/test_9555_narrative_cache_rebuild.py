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
3. the narrative is asserted to be ON the rebuilt `:Source` node, which is the
   half a count can never see (the seed carries the capture's `SourceCreated`
   record for exactly this — the old seed held no `:Source` record at all, so
   the rebuild never had to reproduce `story_arc` on anything).
"""
from __future__ import annotations

import json
import os
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
    means "wrote something"), and the narrative is on a rebuilt node
    (otherwise nothing the cache holds is the thing under test).

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
            assert any(props.get("story_arc") == NARRATIVE for props in prefixed), (
                "the pre-wipe cache does not carry the narrative on its "
                f"`:Source` node: {prefixed}")

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
            assert any(props.get("story_arc") == NARRATIVE
                       for _labels, props in after_nodes), (
                "the rebuilt cache holds no node carrying §9.1's narrative — a "
                "rebuild of the turns alone would pass a node count and lose the "
                "text")
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
    sdk = TortoiseSDK(graph_name=graph)
    try:
        ev = sdk.create_event("narrative-event-door", "AgentSession",
                              story_arc=NARRATIVE, project="kept-228")
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
