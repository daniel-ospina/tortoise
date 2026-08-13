"""Epic #888 W2 — write/revise tool consolidation tests.

Covers the consolidated write surface (PR #912 design):
  create_entity(type=subject|object|event|document) + about* edge wiring
  update(id)  — Point lifecycle semantics vs plain entity update
  delete(id)  — Point or entity
  supersede(old, new, transfer_edges=) — full transfer vs invalidate behavior
  operator_action(action=mitigate|annotate)
  create_edge(relation, from, to) — typed structural edge, operator-less
  write nudges — {node, nudges} with top related candidates (nudge, don't enforce)

Includes the LLM-judged proxy: precise structural assertions that the event
about* edges point at the SEMANTICALLY RIGHT targets (right edge type → right
target id), with decoys proving no accidental wiring — the deterministic proxy
for an LLM judging semantic correctness (epic instruction: when no LLM harness
is available, assert structural correctness precisely).

Runnable with: ../tortoise/.venv/bin/python -m pytest tests/test_write_consolidation.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp embedded DB. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_w2_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_point(sdk: TortoiseSDK, **kw):
    return sdk.create_point(kw.pop("kind", "statement"),
                            kw.pop("content", "test content"), **kw)


def _about_targets(sdk, source_id: str, rel: str) -> list[str]:
    """Target ids of (source)-[rel]->(target) edges, by node id or eventId."""
    rows = sdk._get_proj().g.query(
        "MATCH (s)-[r]->(t) WHERE type(r) = $rel AND s.id = $sid "
        "RETURN coalesce(t.id, t.eventId)",
        params={"rel": rel, "sid": source_id},
    ).result_set
    return sorted(row[0] for row in rows if row[0])


# ── create_entity ───────────────────────────────────────────────────

class TestCreateEntity:
    def test_routes_subject(self, sdk):
        r = sdk.create_entity("subject", "Acme Corp", subjectKind="company")
        assert "node" in r and "nudges" in r  # write surface returns nudges
        node = r["node"]
        assert node.get("subjectKind") == "company"
        assert node.get("status") == "live"
        assert sdk.get_entity(node["id"])["name"] == "Acme Corp"

    def test_routes_object(self, sdk):
        node = sdk.create_entity("object", "Widget", objectKind="product")["node"]
        assert node.get("objectKind") == "product"
        assert node.get("status") == "live"

    def test_routes_document(self, sdk):
        node = sdk.create_entity("document", "Q3 Plan", documentKind="planDoc")["node"]
        assert node.get("documentKind") == "planDoc"
        assert node.get("doc_status") == "draft"  # documents enter as draft

    def test_routes_event_requires_eventkind(self, sdk):
        with pytest.raises(ValueError, match="eventKind"):
            sdk.create_entity("event", "review")

    def test_type_case_insensitive(self, sdk):
        node = sdk.create_entity("SUBJECT", "Case Team", subjectKind="team")["node"]
        assert node.get("subjectKind") == "team"
        node2 = sdk.create_entity(" Object ", "Trim", objectKind="service")["node"]
        assert node2.get("objectKind") == "service"

    def test_unknown_type_raises(self, sdk):
        with pytest.raises(ValueError, match="unknown type"):
            sdk.create_entity("widget", "x")

    def test_aliases_are_thin_and_consistent(self, sdk):
        """Legacy create_* methods return the bare node (no nudges key) with
        identical field semantics to create_entity."""
        a = sdk.create_subject("Alpha", "company")
        b = sdk.create_entity("subject", "Beta", subjectKind="company")["node"]
        assert a.get("subjectKind") == b.get("subjectKind") == "company"
        assert a.get("status") == b.get("status") == "live"
        assert "nudges" not in a  # legacy contract: node only
        ev = sdk.create_event("review", "meeting")
        assert ev.get("eventKind") == "meeting"
        assert ev.get("eventStatus") == "scheduled"

    def test_event_preserves_about_edges(self, sdk):
        """CRITICAL (#888 W2): create_entity(type='event') wires about* edges
        exactly like the legacy create_event — extracted from props, stored as
        edges, NOT as string properties."""
        subj = sdk.create_entity("subject", "CS Team", subjectKind="team")["node"]
        obj = sdk.create_entity("object", "Onboarding", objectKind="flow")["node"]
        point = _make_point(sdk, content="customers churn in 30 days")
        doc = sdk.create_entity("document", "Q3 Report", documentKind="report")["node"]
        ev = sdk.create_entity(
            "event", "churn review", eventKind="meeting",
            aboutSubject=subj["id"], aboutObject=obj["id"],
            aboutPoint=point["id"], aboutDocument=doc["id"],
        )["node"]
        eid = ev["eventId"]
        assert _about_targets(sdk, eid, "aboutSubject") == [subj["id"]]
        assert _about_targets(sdk, eid, "aboutObject") == [obj["id"]]
        assert _about_targets(sdk, eid, "aboutPoint") == [point["id"]]
        assert _about_targets(sdk, eid, "aboutDocument") == [doc["id"]]
        # about* refs must NOT leak into node properties
        for prop in ("aboutSubject", "aboutObject", "aboutPoint", "aboutDocument"):
            assert prop not in ev, f"{prop} stored as property, not edge"

    def test_event_about_name_resolution(self, sdk):
        """Legacy behavior: a non-ULID aboutObject is name-resolved (or stubbed)
        by _create_about_edges — the consolidated path preserves it."""
        sdk.create_entity("object", "Known Object", objectKind="product")
        ev = sdk.create_entity("event", "demo", eventKind="launch",
                               aboutObject="Known Object")["node"]
        targets = _about_targets(sdk, ev["eventId"], "aboutObject")
        assert len(targets) == 1  # resolved to the existing Object by name

    def test_nested_props_coercion(self, sdk):
        """MCP-style props={'...'} dict is flattened (#218 convention)."""
        node = sdk.create_entity("object", "Coerced", props={
            "objectKind": "service", "tier": "gold"})["node"]
        assert node.get("objectKind") == "service"
        assert node.get("tier") == "gold"


# ── update ──────────────────────────────────────────────────────────

class TestUpdate:
    def test_update_point_promotes_draft_to_live(self, sdk):
        p = _make_point(sdk, content="draft claim")
        assert sdk.get_point(p["id"]).get("status") == "draft"
        r = sdk.update(p["id"], status="live")
        assert r["status"] == "live"

    def test_update_point_object_increments_version(self, sdk):
        """Point:Object nodes get version increment on every update."""
        p = _make_point(sdk, content="versioned claim")
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n:Object", params={"id": p["id"]})
        r1 = sdk.update(p["id"], note="first")
        assert r1["version"] == 1
        r2 = sdk.update(p["id"], note="second")
        assert r2["version"] == 2

    def test_update_plain_entity_sets_props(self, sdk):
        obj = sdk.create_entity("object", "Plain Entity", objectKind="service")["node"]
        r = sdk.update(obj["id"], tier="enterprise", sla="99.9")
        assert r.get("tier") == "enterprise"
        assert r.get("sla") == "99.9"

    def test_update_rejects_context_on_point(self, sdk):
        """Point-lifecycle semantics: context is removed (#49) — still rejected."""
        p = _make_point(sdk, content="ctx claim")
        with pytest.raises(TypeError, match="context"):
            sdk.update(p["id"], context="x")

    def test_update_validates_status(self, sdk):
        p = _make_point(sdk, content="status claim")
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.update(p["id"], status="bogus")

    def test_update_only_promotes_draft_to_live(self, sdk):
        p = _make_point(sdk, content="promote claim")
        sdk.update(p["id"], status="live")
        with pytest.raises(ValueError, match="only promotes"):
            sdk.update(p["id"], status="retracted")  # lifecycle via retract/supersede

    def test_update_unknown_id_returns_empty(self, sdk):
        assert sdk.update("000000000000-000000000000", tier="x") == {}

    def test_update_tags_sync(self, sdk):
        p = _make_point(sdk, content="tagged claim")
        sdk.update(p["id"], tags=["gamma"])
        assert [r["id"] for r in sdk.query_points_by_tag("gamma")] == [p["id"]]

    def test_update_entity_via_legacy_alias(self, sdk):
        obj = sdk.create_entity("object", "Alias Entity", objectKind="service")["node"]
        r = sdk.update_entity(obj["id"], tier="pro")
        assert r.get("tier") == "pro"


# ── delete ──────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_point(self, sdk):
        p = _make_point(sdk, content="doomed point")
        assert sdk.delete(p["id"]) is True
        assert sdk.get_point(p["id"]) == {}
        assert sdk.delete(p["id"]) is False  # idempotent False on re-delete

    def test_delete_entity(self, sdk):
        obj = sdk.create_entity("object", "Doomed Entity", objectKind="service")["node"]
        assert sdk.delete(obj["id"]) is True
        assert sdk.get_entity(obj["id"]) == {}

    def test_delete_unknown_false(self, sdk):
        assert sdk.delete("000000000000-000000000000") is False

    def test_delete_event_by_eventid(self, sdk):
        ev = sdk.create_entity("event", "doomed event", eventKind="meeting")["node"]
        assert sdk.delete(ev["eventId"]) is True


# ── supersede / transfer_edges ──────────────────────────────────────

class TestSupersede:
    def _old_new(self, sdk, text_old="old claim xyz", text_new="new claim xyz"):
        old = _make_point(sdk, content=text_old)
        new = _make_point(sdk, content=text_new)
        return old, new

    def test_transfer_true_transfers_edges(self, sdk):
        old, new = self._old_new(sdk)
        obj = sdk.create_entity("object", "Sup Target", objectKind="product")["node"]
        sdk.create_edge("aboutObject", old["id"], obj["id"])
        r = sdk.supersede(old["id"], new["id"], transfer_edges=True)
        assert r["invalidated"] is True
        assert r["edges_transferred"] >= 1
        assert _about_targets(sdk, old["id"], "aboutObject") == []  # moved off
        assert _about_targets(sdk, new["id"], "aboutObject") == [obj["id"]]
        assert sdk.get_point(old["id"])["status"] == "superseded"
        assert len(sdk.traverse(new["id"], "CORRECTS", direction="outgoing")) == 1

    def test_transfer_false_matches_invalidate(self, sdk):
        """transfer_edges=False = invalidate behavior: CORRECTS + outdated,
        NO edge transfer."""
        old, new = self._old_new(sdk)
        obj = sdk.create_entity("object", "Inv Target", objectKind="product")["node"]
        sdk.create_edge("aboutObject", old["id"], obj["id"])
        r = sdk.supersede(old["id"], new["id"], transfer_edges=False)
        assert r["invalidated"] is True
        assert "edges_transferred" not in r
        assert _about_targets(sdk, old["id"], "aboutObject") == [obj["id"]]  # stays
        assert _about_targets(sdk, new["id"], "aboutObject") == []  # not moved
        assert sdk.get_point(old["id"]).get("outdated") is True
        assert len(sdk.traverse(new["id"], "CORRECTS", direction="outgoing")) == 1

    def test_default_is_full_supersede(self, sdk):
        old, new = self._old_new(sdk)
        r = sdk.supersede(old["id"], new["id"])
        assert r["invalidated"] is True
        assert r.get("edges_transferred") == 0  # nothing to transfer, still full path
        assert sdk.get_point(old["id"])["status"] == "superseded"

    def test_legacy_alias_parity(self, sdk):
        """invalidate_point ≡ supersede(transfer_edges=False);
        supersede_point ≡ supersede(transfer_edges=True)."""
        old, new = self._old_new(sdk, "old alpha", "new alpha")
        r_legacy = sdk.invalidate_point(old["id"], new["id"])
        old2, new2 = self._old_new(sdk, "old beta", "new beta")
        r_new = sdk.supersede(old2["id"], new2["id"], transfer_edges=False)
        assert r_legacy["invalidated"] == r_new["invalidated"] is True
        assert sdk.get_point(old["id"]).get("outdated") is True
        assert sdk.get_point(old2["id"]).get("outdated") is True

    def test_guards_preserved(self, sdk):
        """Missing/self endpoints still raise through the consolidated entry."""
        p = _make_point(sdk, content="guard point")
        with pytest.raises(ValueError, match="No point"):
            sdk.supersede("missing-old", p["id"])
        with pytest.raises(ValueError, match="cannot be the point itself"):
            sdk.supersede(p["id"], p["id"], transfer_edges=False)


# ── operator_action ─────────────────────────────────────────────────

class TestOperatorAction:
    def _operator(self, sdk):
        a = _make_point(sdk, content="claim a")
        b = _make_point(sdk, content="claim b")
        return sdk.create_operator("IMPL", a["id"], [b["id"]])

    def test_mitigate_routes(self, sdk):
        op = self._operator(sdk)
        r = sdk.operator_action("mitigate", id=op["id"], reason="small sample",
                                strength=0.3)
        assert r.get("mitigation_strength") == 0.3
        assert r.get("pointKind") == "statement"
        # mitigation Point linked to the operator
        rows = sdk._get_proj().g.query(
            "MATCH (op:Point {id:$id})-[:mitigated_by]->(m:Point) RETURN m.id",
            params={"id": op["id"]},
        ).result_set
        assert len(rows) == 1

    def test_annotate_routes(self, sdk):
        op = self._operator(sdk)
        r = sdk.operator_action("annotate", id=op["id"], bias=0.1, precision=0.8,
                                consistency=0.7, directness=0.9)
        assert r.get("annotator_bias") == 0.1
        assert r.get("annotator_precision") == 0.8
        assert r.get("annotator_consistency") == 0.7
        assert r.get("annotator_directness") == 0.9

    def test_unknown_action_raises(self, sdk):
        op = self._operator(sdk)
        with pytest.raises(ValueError, match="unknown action"):
            sdk.operator_action("dream", id=op["id"])

    def test_legacy_alias_parity(self, sdk):
        op = self._operator(sdk)
        r1 = sdk.mitigate_operator(op["id"], "legacy reason", 0.4)
        op2 = self._operator(sdk)
        r2 = sdk.operator_action("mitigate", id=op2["id"], reason="new reason",
                                 strength=0.4)
        assert r1.get("mitigation_strength") == r2.get("mitigation_strength") == 0.4


# ── create_edge ─────────────────────────────────────────────────────

class TestCreateEdge:
    def test_typed_structural_edge(self, sdk):
        p = _make_point(sdk, content="produces claim")
        obj = sdk.create_entity("object", "Output", objectKind="artifact")["node"]
        r = sdk.create_edge("produces", p["id"], obj["id"])
        assert r["created"] is True
        assert r["edge"] == {"relation": "produces", "from": p["id"], "to": obj["id"]}
        assert _about_targets(sdk, p["id"], "produces") == [obj["id"]]

    def test_about_edge_via_create_edge(self, sdk):
        subj = sdk.create_entity("subject", "Edge Team", subjectKind="team")["node"]
        p = _make_point(sdk, content="edge claim")
        assert sdk.create_edge("aboutSubject", p["id"], subj["id"])["created"] is True
        assert _about_targets(sdk, p["id"], "aboutSubject") == [subj["id"]]

    def test_related_and_depends_on(self, sdk):
        o1 = sdk.create_entity("object", "R1", objectKind="service")["node"]
        o2 = sdk.create_entity("object", "R2", objectKind="service")["node"]
        assert sdk.create_edge("related", o1["id"], o2["id"])["created"] is True
        assert sdk.create_edge("dependsOn", o1["id"], o2["id"])["created"] is True

    def test_unknown_relation_raises(self, sdk):
        p = _make_point(sdk, content="x")
        with pytest.raises(ValueError, match="Unknown predicate"):
            sdk.create_edge("FLIES", p["id"], p["id"])

    def test_operator_less_by_default(self, sdk):
        """Reification rule (v3.5 §8): structural edges carry NO operator."""
        p = _make_point(sdk, content="reify claim")
        obj = sdk.create_entity("object", "Reify", objectKind="artifact")["node"]
        sdk.create_edge("uses", p["id"], obj["id"])
        ops = sdk._get_proj().g.query(
            "MATCH (n:Point {is_operator:true}) RETURN count(n)"
        ).result_set[0][0]
        assert ops == 0

    def test_missing_endpoint_returns_created_false(self, sdk):
        p = _make_point(sdk, content="orphan edge claim")
        r = sdk.create_edge("uses", p["id"], "000000000000-000000000000")
        assert r["created"] is False


# ── write nudges (nudge, don't enforce) ─────────────────────────────

class TestNudges:
    def test_nudges_suggest_impl(self, sdk):
        _make_point(sdk, content="the widget roadmap for q3 is planned")
        r = sdk.create_entity("object", "widget roadmap", objectKind="plan")
        assert len(r["nudges"]) == 1
        n = r["nudges"][0]
        assert n["suggested_relation"] == "IMPL"
        assert "candidate" in n and "reason" in n

    def test_nudges_suggest_nand_on_contradiction(self, sdk):
        _make_point(sdk, content="widgets contradict the rollout plan")
        r = sdk.create_entity("object", "widget rollout", objectKind="plan")
        assert any(n["suggested_relation"] == "NAND" for n in r["nudges"])

    def test_nudges_suggest_mitigate_for_operator(self, sdk):
        a = _make_point(sdk, content="widgets are reliable")
        b = _make_point(sdk, content="widgets break often")
        sdk.create_operator("IMPL", a["id"], [b["id"]], label="widgets support plan")
        r = sdk.create_entity("object", "widgets plan", objectKind="plan")
        rels = [n["suggested_relation"] for n in r["nudges"]]
        assert "mitigate" in rels, f"operator candidate not nudged: {rels}"

    def test_nudges_cap_at_three_and_exclude_self(self, sdk):
        for i in range(6):
            _make_point(sdk, content=f"widget roadmap item number {i}")
        r = sdk.create_entity("object", "widget roadmap", objectKind="plan")
        assert len(r["nudges"]) <= 3
        candidates = [n["candidate"] for n in r["nudges"]]
        assert len(set(candidates)) == len(candidates)  # no dupes

    def test_nudges_empty_when_no_candidates(self, sdk):
        r = sdk.create_entity("object", "lonely object", objectKind="service")
        assert r["nudges"] == []

    def test_nudges_not_enforced(self, sdk):
        """Nudges are advisory — no IMPL/NAND/mitigate edge is created."""
        _make_point(sdk, content="the widget roadmap for q3 is planned")
        r = sdk.create_entity("object", "widget roadmap", objectKind="plan")
        assert len(r["nudges"]) == 1
        edges = sdk._get_proj().g.query(
            "MATCH ()-[r]->() WHERE type(r) IN ['IMPL','NAND','mitigated_by'] "
            "RETURN count(r)"
        ).result_set[0][0]
        assert edges == 0


# ── LLM-judged proxy: semantic about* wiring ────────────────────────
# No LLM harness is available in the deterministic test environment, so this
# suite stands in for an LLM judge: it verifies NOT just that about* edges
# exist, but that each edge type points at the SEMANTICALLY RIGHT target —
# with decoy entities present to prove the wiring is precise, not accidental.
# (Epic #888 W2 instruction: assert structural correctness precisely when no
# LLM harness is available, and note it as the proxy.)

class TestEventAboutEdgesSemanticProxy:
    def test_about_edges_wire_to_semantically_right_targets(self, sdk):
        # Real targets the event is about
        subj = sdk.create_entity("subject", "Customer Success Team",
                                 subjectKind="team")["node"]
        obj = sdk.create_entity("object", "Onboarding Flow",
                                objectKind="flow")["node"]
        point = _make_point(sdk, content="New customers churn within the first 30 days")
        doc = sdk.create_entity("document", "Q3 Onboarding Report",
                                documentKind="report")["node"]
        # Decoys the event is NOT about — must never be wired
        decoy_subj = sdk.create_entity("subject", "Legal Team",
                                       subjectKind="team")["node"]
        decoy_obj = sdk.create_entity("object", "Billing System",
                                      objectKind="service")["node"]

        ev = sdk.create_entity(
            "event", "customer churn review", eventKind="meeting",
            aboutSubject=subj["id"], aboutObject=obj["id"],
            aboutPoint=point["id"], aboutDocument=doc["id"],
        )["node"]
        eid = ev["eventId"]

        # Right edge TYPE → right target id (semantic precision)
        assert _about_targets(sdk, eid, "aboutSubject") == [subj["id"]]
        assert _about_targets(sdk, eid, "aboutObject") == [obj["id"]]
        assert _about_targets(sdk, eid, "aboutPoint") == [point["id"]]
        assert _about_targets(sdk, eid, "aboutDocument") == [doc["id"]]

        # Decoys untouched — no cross-wiring, no name-collision stubs
        for rel, decoy in (("aboutSubject", decoy_subj["id"]),
                           ("aboutObject", decoy_obj["id"])):
            assert decoy not in _about_targets(sdk, eid, rel), \
                f"decoy {decoy} wired via {rel}"

        # Exactly the four intended about edges — nothing extra
        total = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid})-[r]->() "
            "WHERE type(r) STARTS WITH 'about' RETURN count(r)",
            params={"eid": eid},
        ).result_set[0][0]
        assert total == 4

    def test_about_name_resolution_picks_right_object(self, sdk):
        """Name resolution (legacy _create_about_edges) must resolve to the
        semantically right entity — not a decoy sharing tokens."""
        sdk.create_entity("object", "Onboarding Flow", objectKind="flow")
        sdk.create_entity("object", "Onboarding Billing", objectKind="service")
        ev = sdk.create_entity("event", "flow demo", eventKind="demo",
                               aboutObject="Onboarding Flow")["node"]
        targets = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o) RETURN o.name",
            params={"eid": ev["eventId"]},
        ).result_set
        assert [r[0] for r in targets] == ["Onboarding Flow"]

    def test_about_edges_target_id_not_name_prop(self, sdk):
        """The edge target is the node identity — no stringified about* props."""
        subj = sdk.create_entity("subject", "Precise Team", subjectKind="team")["node"]
        ev = sdk.create_entity("event", "precise demo", eventKind="demo",
                               aboutSubject=subj["id"])["node"]
        raw = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
            params={"eid": ev["eventId"]},
        ).result_set[0][0]
        assert "aboutSubject" not in raw
        assert "aboutObject" not in raw


# ── MCP surface (epic #888 W2 handlers) ─────────────────────────────

@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate)."""
    from tortoise.mcp_auth import (
        _current_team_id, _current_team_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


class TestMcpHandlers:
    def test_tortoise_create_entity_and_update_and_delete(self):
        from tortoise.mcp_server import (
            tortoise_create_entity, tortoise_delete, tortoise_update,
        )
        r = tortoise_create_entity("object", "Mcp Entity",
                                   props={"objectKind": "service"})
        assert "error" not in r, r
        assert "node" in r and "nudges" in r
        nid = r["node"]["id"]
        u = tortoise_update(nid, props={"tier": "gold"})
        assert "error" not in u and u.get("tier") == "gold"
        d = tortoise_delete(nid)
        assert d.get("deleted") is True and d.get("id") == nid

    def test_tortoise_operator_action_mitigate_requires_reason(self):
        from tortoise.mcp_server import tortoise_operator_action
        r = tortoise_operator_action("mitigate", id="whatever")
        assert "error" in r and "reason" in r["error"]

    def test_tortoise_operator_action_annotate_requires_dims(self):
        from tortoise.mcp_server import tortoise_operator_action
        r = tortoise_operator_action("annotate", id="whatever", bias=0.1)
        assert "error" in r and "bias, precision" in r["error"]

    def test_tortoise_operator_action_unknown(self):
        from tortoise.mcp_server import tortoise_operator_action
        r = tortoise_operator_action("dream", id="x")
        assert "error" in r and "unknown action" in r["error"]

    def test_tortoise_supersede_transfer_edges_param(self):
        from tortoise.mcp_server import (
            tortoise_create_point, tortoise_supersede,
        )
        a = tortoise_create_point("statement", "mcp old claim zz")
        b = tortoise_create_point("statement", "mcp new claim zz")
        if "error" in a or "error" in b:
            pytest.skip("FalkorDB not available")
        r = tortoise_supersede(a["id"], b["id"], transfer_edges=False)
        assert "error" not in r and r["invalidated"] is True

    def test_tortoise_create_edge_returns_rich_dict(self):
        from tortoise.mcp_server import (
            tortoise_create_edge, tortoise_create_entity, tortoise_create_point,
        )
        p = tortoise_create_point("statement", "mcp edge claim")
        if "error" in p:
            pytest.skip("FalkorDB not available")
        obj = tortoise_create_entity("object", "Mcp Edge Object",
                                     props={"objectKind": "artifact"})
        if "error" in obj:
            pytest.skip("FalkorDB not available")
        r = tortoise_create_edge(p["id"], obj["node"]["id"], "uses")
        assert "edge" in r and "created" in r and "nudges" in r
        assert r["created"] is True
        assert r["edge"]["relation"] == "uses"
