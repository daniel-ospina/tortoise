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

    (1) FAILS pre-fix at the first `is None`: the prose is on the node.
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
