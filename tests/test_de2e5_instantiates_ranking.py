"""DE2E-5 — produces/uses wiring + INSTANTIATES drift removal (#781).

Epic plan §7 DE2E-5:
- decision Event produces decision Point (file_decision, design #421)
- session Event uses artifact Object
- zero INSTANTIATES edges on both the ingest and mining paths
- aboutObject session appears in top-5 ranking results AND ranking score
  with aboutObject edges > score without them (observable baseline)
- Point-origin aboutObject does NOT inflate session boost (Event-anchored
  rewrite) — the negative variant
- security whitelist has no INSTANTIATES
"""
from __future__ import annotations

import os
import tempfile

import pytest

from tortoise.ranking import GraphRanker
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


SESSION_MD = """---
sessionId: s-de2e5-1
date: 2026-08-01
issues:
  - tortoise#123
---

## User
We decided to move the FalkorDB default port to 16379.

## Assistant
The redis config needs the new port. tortoise#123 tracks the migration.
"""


def _no_instantiates(sdk) -> bool:
    rows = sdk._get_proj().g.query(
        "MATCH ()-[r:INSTANTIATES]->() RETURN count(r)"
    ).result_set
    return rows[0][0] == 0


def _event_about_objects(sdk, event_id: str) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object) "
        "RETURN count(o)",
        params={"eid": event_id},
    ).result_set
    return rows[0][0]


class TestDe2e5:
    def test_ingest_path_about_object_and_no_instantiates(self, sdk, tmp_path):
        """A session ingested with issue frontmatter gets aboutObject edges to
        issue Objects — and zero INSTANTIATES anywhere."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "session.md").write_text(SESSION_MD)
        res = sdk.ingest_corpus(str(corpus), eventKind="AgentSession")
        assert res.get("ingested", 0) >= 1 or res.get("updated", 0) >= 1, res
        # The session Event exists with aboutObject edges to issue Objects.
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventKind:'AgentSession'})-[:aboutObject]->(o:Object) "
            "RETURN o.id, o.objectKind"
        ).result_set
        assert rows, "session Event must wire aboutObject to issue Objects"
        kinds = {r[1] for r in rows}
        assert "issue" in kinds, f"expected an issue-kind Object, got {kinds}"
        assert _no_instantiates(sdk), "INSTANTIATES must not exist on the ingest path"

    def test_decision_produces_point(self, sdk):
        """The humanApproval flow wires the Event → decision Point via
        produces (design #421) — the canonical produces writer."""
        proj = sdk._get_proj()
        claim = sdk.create_point("statement", "claim under approval", status="live")
        sdk.create_subject("alice")
        artifact = sdk.create_entity("document", "doc-1",
                                     documentKind="plan", objectKind="document")
        artifact_id = artifact["node"]["id"]
        res = sdk.file_human_approval(approver_id="alice",
                                      artifact_id=artifact_id,
                                      point_ids=[claim["id"]])
        rows = proj.g.query(
            "MATCH (e:Event)-[:produces]->(p:Point) "
            "RETURN e.eventId, p.id"
        ).result_set
        assert rows, "approval Event must produce the decision Point"

    def test_ranking_about_object_boost_beats_baseline(self, sdk):
        """An aboutObject-connected session ranks ABOVE an identical session
        without aboutObject edges (observable baseline)."""
        proj = sdk._get_proj()
        # Baseline session: no aboutObject.
        e1 = sdk.create_event("base session", "AgentSession",
                              startedAt="2026-08-01T00:00:00Z")
        # AboutObject session: wired to an issue Object.
        e2 = sdk.create_event("about session", "AgentSession",
                              startedAt="2026-08-01T00:00:00Z")
        oid = "issue_abc12345"
        proj.g.query(
            "MERGE (o:Object {id:$oid}) SET o.name='tortoise#123', "
            "o.objectKind='issue'",
            params={"oid": oid},
        )
        proj.create_about_edge(e2["eventId"], oid, "aboutObject")

        ranker = GraphRanker(proj)
        results = ranker.rerank(
            [
                {"id": e1["eventId"], "scores": {"rrf": 1.0}},
                {"id": e2["eventId"], "scores": {"rrf": 1.0}},
            ],
            entity_type="event",
        )
        by_id = {r["id"]: r["graph_ranking"] for r in results}
        boost_about = by_id[e2["eventId"]]["graph_boost"]
        boost_base = by_id[e1["eventId"]]["graph_boost"]
        assert boost_about > boost_base, (
            f"aboutObject session boost ({boost_about}) must exceed baseline "
            f"({boost_base})"
        )
        assert results[0]["id"] == e2["eventId"], (
            "aboutObject session must rank first on equal similarity"
        )

    def test_point_origin_about_object_does_not_inflate_session(self, sdk):
        """Negative variant: ONLY a Point-origin aboutObject edge — the
        session boost must stay at the baseline (Event-anchored rewrite)."""
        proj = sdk._get_proj()
        e = sdk.create_event("point-origin session", "AgentSession",
                             startedAt="2026-08-01T00:00:00Z")
        p = sdk.create_point("statement", "we decided on 16379", status="live")
        oid = "issue_xyz98765"
        proj.g.query(
            "MERGE (o:Object {id:$oid}) SET o.name='tortoise#999', "
            "o.objectKind='issue'",
            params={"oid": oid},
        )
        # POINT-origin aboutObject — the Event itself has no such edge.
        proj.create_about_edge(p["id"], oid, "aboutObject")

        ranker = GraphRanker(proj)
        results = ranker.rerank(
            [{"id": e["eventId"], "scores": {"rrf": 1.0}}],
            entity_type="event",
        )
        boost = results[0]["graph_ranking"]["graph_boost"]
        # Baseline for an event with zero aboutObject: 0.6·0 + 0.4·confidence.
        assert _event_about_objects(sdk, e["eventId"]) == 0
        expected = round(0.4 * 0.5, 4)  # no produced points → conf 0.5
        assert boost == expected, (
            f"Point-origin aboutObject must NOT inflate the session boost "
            f"(got {boost}, expected {expected})"
        )

    def test_security_whitelist_has_no_instantiates(self):
        """The security edge whitelist must not contain INSTANTIATES."""
        import tortoise.security as sec
        source = open(sec.__file__).read()
        assert "INSTANTIATES" not in source, (
            "security whitelist must be INSTANTIATES-free"
        )
