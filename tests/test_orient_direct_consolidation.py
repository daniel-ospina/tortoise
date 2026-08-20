"""Epic #888 W3 — orient/direct tool consolidation tests (PR #912 design).

Covers:
  overview(section=) — one tool consolidating the list_*/status/health/
      taxonomy/structure zoo; each section returns the same shape as the
      legacy tool it replaces; omitted section → compact combined summary.
  get(id, type=) — one tool consolidating get_point/get_entity/get_operator/
      get_events/get_session/get_governance; type omitted → auto-detect by id
      lookup; invalid section/type → clear errors.
  Regression — every legacy list_*/get_*/status/health/taxonomy tool still
      works and returns the same shape as the consolidated surface.

Runnable with: python -m pytest tests/test_orient_direct_consolidation.py -v
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
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_w3_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate)."""
    from tortoise.mcp_auth import (  # noqa: I001
        _current_team_id, _current_team_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


@pytest.fixture
def mcp_sdk(sdk):
    """Swap the module-level SDK so MCP tool wrappers hit the test DB."""
    import tortoise.mcp_server as mcp_mod
    orig_sdk = mcp_mod.sdk
    mcp_mod.sdk = sdk
    yield sdk
    mcp_mod.sdk = orig_sdk


def _seed_graph(sdk: TortoiseSDK) -> dict:
    """Deterministic seed: points, source, tag, entity, event, operator."""
    p1 = sdk.create_point("statement", "alpha claim", authoredBy="tester")
    p2 = sdk.create_point("decision", "beta decision", authoredBy="tester",
                          tags=["t1", "t2"])
    src = sdk.create_source("https://w3.example.com/doc", "document")
    sdk.create_point("statement", "from source", extractedFrom=src["url"])
    subj = sdk.create_subject("W3 Team", "team")
    sdk.create_object("W3 Widget", "product", ownedBy=subj["id"])
    ev = sdk.create_event("w3 review", "meeting")
    sess = sdk.create_event("w3 session", "AgentSession",
                            session_id="sess-1")
    op = sdk.create_operator("IMPL", p1["id"], [p2["id"]])
    return {"p1": p1, "p2": p2, "source": src, "subject": subj,
            "event": ev, "session": sess, "operator": op}


# ── overview: section parity with legacy tools ─────────────────────

class TestOverviewSections:
    def test_section_taxonomy_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_overview, tortoise_taxonomy,
        )
        _seed_graph(sdk)
        assert tortoise_overview(section="taxonomy") == tortoise_taxonomy()
        assert isinstance(tortoise_overview(section="taxonomy"), dict)

    def test_section_structure_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_overview, tortoise_summarize_structure,
        )
        _seed_graph(sdk)
        result = tortoise_overview(section="structure")
        assert result == tortoise_summarize_structure()
        assert set(result) >= {"gate0_jtbds", "gate1_use_cases", "total"}

    def test_section_structure_check_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_check_structure, tortoise_overview,
        )
        _seed_graph(sdk)
        assert tortoise_overview(section="structure_check") == tortoise_check_structure()
        assert isinstance(tortoise_overview(section="structure_check"), list)

    def test_section_pointkinds_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_list_pointkinds, tortoise_overview,
        )
        _seed_graph(sdk)
        result = tortoise_overview(section="pointkinds")
        assert result == tortoise_list_pointkinds()
        kinds = {r["kind"] for r in result}
        assert {"statement", "decision"} <= kinds

    def test_section_tags_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_list_tags, tortoise_overview
        _seed_graph(sdk)
        result = tortoise_overview(section="tags")
        assert result == tortoise_list_tags()
        names = {r["name"] for r in result}
        assert {"t1", "t2"} <= names

    def test_section_sources_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_list_sources, tortoise_overview
        _seed_graph(sdk)
        result = tortoise_overview(section="sources")
        assert result == tortoise_list_sources()
        urls = {r["url"] for r in result}
        assert "https://w3.example.com/doc" in urls

    def test_section_namespaces_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_list_namespaces, tortoise_overview,
        )
        result = tortoise_overview(section="namespaces")
        assert result == tortoise_list_namespaces()
        assert isinstance(result, list) and result

    def test_section_graphs_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_list_graphs, tortoise_overview
        assert tortoise_overview(section="graphs") == tortoise_list_graphs()

    def test_section_topics_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_list_topics, tortoise_overview
        seed = _seed_graph(sdk)
        pid = seed["p1"]["id"]
        assert tortoise_overview(section="topics", entity_id=pid) == \
            tortoise_list_topics(pid)

    def test_section_health_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_health, tortoise_overview
        result = tortoise_overview(section="health")
        assert result == tortoise_health()
        assert set(result) >= {"status", "falkordb", "graph_size"}

    def test_section_status_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview, tortoise_status
        result = tortoise_overview(section="status")
        assert result == tortoise_status()
        assert set(result) >= {"connected", "counts", "total_entities"}

    def test_section_stale_matches_legacy(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview, tortoise_stale
        _seed_graph(sdk)  # fresh points are not stale (createdAt = now)
        assert tortoise_overview(section="stale") == tortoise_stale()
        assert tortoise_overview(section="stale", days=1, limit=5) == \
            tortoise_stale(days=1, limit=5)

    def test_section_is_case_insensitive_and_stripped(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview, tortoise_taxonomy
        _seed_graph(sdk)
        assert tortoise_overview(section="  TAXONOMY ") == tortoise_taxonomy()

    def test_invalid_section_returns_clear_error(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview
        result = tortoise_overview(section="bogus")
        assert isinstance(result, dict) and "error" in result
        assert "bogus" in result["error"]
        assert "taxonomy" in result["error"]  # lists valid sections

    def test_topics_section_requires_entity_id(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview
        result = tortoise_overview(section="topics")
        assert isinstance(result, dict) and "error" in result
        assert "entity_id" in result["error"]


class TestOverviewDefaultSummary:
    def test_default_returns_combined_summary(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview
        _seed_graph(sdk)
        result = tortoise_overview()
        assert isinstance(result, dict)
        expected_keys = {"taxonomy", "structure", "structure_check",
                         "pointkinds", "tags", "sources", "namespaces",
                         "graphs", "health", "status", "stale"}
        assert expected_keys <= set(result)
        assert "topics" not in result  # requires entity_id — excluded
        assert isinstance(result["pointkinds"], list)
        assert isinstance(result["taxonomy"], dict)
        assert isinstance(result["status"], dict)
        assert result["taxonomy"]["Point"] >= 3

    def test_default_matches_individual_sections(self, sdk, mcp_sdk):
        """Combined summary values equal the single-section calls."""
        from tortoise.mcp_server import tortoise_overview
        _seed_graph(sdk)
        combined = tortoise_overview()

        def _stable(d):
            # uptime is time-varying (increases between calls) — strip it so
            # the comparison is deterministic; everything else is stable.
            if isinstance(d, dict) and "uptime" in d:
                d = {k: v for k, v in d.items() if k != "uptime"}
            return d

        for sec in ("taxonomy", "structure", "pointkinds", "tags", "sources",
                    "namespaces", "graphs", "health", "status", "stale"):
            assert _stable(combined[sec]) == _stable(tortoise_overview(section=sec)), sec


# ── get: type routing + auto-detect ────────────────────────────────

class TestGet:
    def test_type_point(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_point
        seed = _seed_graph(sdk)
        pid = seed["p1"]["id"]
        assert tortoise_get(pid, type="point") == tortoise_get_point(pid)
        assert tortoise_get(pid, type="point")["content"] == "alpha claim"

    def test_type_operator(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_operator
        seed = _seed_graph(sdk)
        op_id = seed["operator"]["id"]
        result = tortoise_get(op_id, type="operator")
        assert result == tortoise_get_operator(op_id)
        assert result.get("is_operator") is True

    def test_type_operator_non_operator_errors(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get
        seed = _seed_graph(sdk)
        result = tortoise_get(seed["p1"]["id"], type="operator")
        assert isinstance(result, dict) and "error" in result
        assert "not an operator" in result["error"]

    def test_type_entity(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_entity
        seed = _seed_graph(sdk)
        sid = seed["subject"]["id"]
        assert tortoise_get(sid, type="entity") == tortoise_get_entity(sid)
        assert tortoise_get(sid, type="entity")["name"] == "W3 Team"

    def test_type_event_resolves_via_entity(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_entity
        seed = _seed_graph(sdk)
        eid = seed["event"]["eventId"]
        result = tortoise_get(eid, type="event")
        assert result == tortoise_get_entity(eid)
        assert result.get("eventKind") == "meeting"

    def test_type_session(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_session
        _seed_graph(sdk)
        result = tortoise_get("sess-1", type="session")
        assert result == tortoise_get_session("sess-1")
        assert result.get("session_id") == "sess-1"

    def test_type_events_lists_recent(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_events
        seed = _seed_graph(sdk)
        assert tortoise_get(None, type="events") == tortoise_get_events()
        filtered = tortoise_get("meeting", type="events")
        assert filtered == tortoise_get_events(eventKind="meeting")
        assert any(e["eventId"] == seed["event"]["eventId"] for e in filtered)

    def test_type_governance(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_governance
        seed = _seed_graph(sdk)
        sid = seed["subject"]["id"]
        assert tortoise_get(sid, type="governance") == tortoise_get_governance(sid)
        names = {e.get("name") for e in tortoise_get(sid, type="governance")}
        assert "W3 Widget" in names

    def test_auto_detect_point(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_point
        seed = _seed_graph(sdk)
        pid = seed["p1"]["id"]
        assert tortoise_get(pid) == tortoise_get_point(pid)

    def test_auto_detect_entity(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_entity
        seed = _seed_graph(sdk)
        sid = seed["subject"]["id"]
        assert tortoise_get(sid) == tortoise_get_entity(sid)

    def test_auto_detect_operator(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_point
        seed = _seed_graph(sdk)
        op_id = seed["operator"]["id"]
        assert tortoise_get(op_id) == tortoise_get_point(op_id)
        assert tortoise_get(op_id).get("is_operator") is True

    def test_auto_detect_event(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get, tortoise_get_entity
        seed = _seed_graph(sdk)
        eid = seed["event"]["eventId"]
        assert tortoise_get(eid) == tortoise_get_entity(eid)

    def test_auto_detect_session_id(self, sdk, mcp_sdk):
        """session_id (not the eventId) resolves via the get_session fallback."""
        from tortoise.mcp_server import tortoise_get, tortoise_get_session
        _seed_graph(sdk)
        assert tortoise_get("sess-1") == tortoise_get_session("sess-1")

    def test_auto_detect_missing_returns_empty(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get
        assert tortoise_get("no-such-node") == {}

    def test_invalid_type_returns_clear_error(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get
        result = tortoise_get("whatever", type="bogus")
        assert isinstance(result, dict) and "error" in result
        assert "bogus" in result["error"]
        assert "point" in result["error"]  # lists valid types

    def test_missing_id_errors(self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_get
        assert "error" in tortoise_get(None)
        assert "error" in tortoise_get("")
        assert "error" in tortoise_get(None, type="point")
        # events type tolerates missing id (list surface)
        assert isinstance(tortoise_get(None, type="events"), list)


# ── Regression: legacy tools still work (thin aliases) ─────────────

class TestLegacyAliases:
    def test_list_zoo_still_works(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_list_graphs, tortoise_list_namespaces, tortoise_list_pointkinds,
            tortoise_list_sources, tortoise_list_tags, tortoise_list_topics,
        )
        seed = _seed_graph(sdk)
        assert isinstance(tortoise_list_pointkinds(), list)
        assert isinstance(tortoise_list_sources(), list)
        assert isinstance(tortoise_list_namespaces(), list)
        assert isinstance(tortoise_list_tags(), list)
        assert isinstance(tortoise_list_graphs(), list)
        assert isinstance(tortoise_list_topics(seed["p1"]["id"]), dict)

    def test_status_health_taxonomy_still_work(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_health, tortoise_status, tortoise_taxonomy,
        )
        assert isinstance(tortoise_status(), dict)
        assert isinstance(tortoise_health(), dict)
        assert isinstance(tortoise_taxonomy(), dict)

    def test_structure_tools_still_work(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_check_structure, tortoise_stale, tortoise_summarize_structure,
        )
        _seed_graph(sdk)
        assert isinstance(tortoise_check_structure(), list)
        assert isinstance(tortoise_summarize_structure(), dict)
        assert isinstance(tortoise_stale(), dict)

    def test_get_zoo_still_works(self, sdk, mcp_sdk):
        from tortoise.mcp_server import (  # noqa: I001
            tortoise_get_entity, tortoise_get_events, tortoise_get_governance,
            tortoise_get_operator, tortoise_get_point, tortoise_get_session,
        )
        seed = _seed_graph(sdk)
        assert tortoise_get_point(seed["p1"]["id"])["content"] == "alpha claim"
        assert tortoise_get_entity(seed["subject"]["id"])["name"] == "W3 Team"
        assert tortoise_get_operator(seed["operator"]["id"])["is_operator"] is True
        assert isinstance(tortoise_get_events(), list)
        assert tortoise_get_session("sess-1")["session_id"] == "sess-1"
        assert isinstance(tortoise_get_governance(seed["subject"]["id"]), list)

    def test_aliases_delegate_to_consolidated_surface(self, sdk, mcp_sdk):
        """Legacy tools return the same data as the consolidated tools."""
        import tortoise.mcp_server as mcp_mod
        _seed_graph(sdk)
        # overview sections
        assert mcp_mod.tortoise_taxonomy() == mcp_mod.tortoise_overview(section="taxonomy")
        assert mcp_mod.tortoise_status() == mcp_mod.tortoise_overview(section="status")
        assert mcp_mod.tortoise_list_tags() == mcp_mod.tortoise_overview(section="tags")
        # get types
        pid = sdk.create_point("statement", "alias-check")["id"]
        assert mcp_mod.tortoise_get_point(pid) == mcp_mod.tortoise_get(pid, type="point")
        assert mcp_mod.tortoise_get_point(pid) == mcp_mod.tortoise_get(pid)


# ── Registry: new tools are registered for both surfaces ────────────

class TestRegistry:
    def test_new_tools_registered_with_readonly_policy(self):
        from tortoise.tool_registry import TOOL_REGISTRY
        names = {t.name for t in TOOL_REGISTRY}
        assert "tortoise_overview" in names
        assert "tortoise_get" in names
        for t in TOOL_REGISTRY:
            if t.name in ("tortoise_overview", "tortoise_get"):
                assert t.http_policy is True
                assert t.annotations.readOnlyHint is True

    def test_handlers_exist_in_mcp_server_globals(self):
        """FastMCPAdapter registers registry entries by name in globals —
        missing handlers are skipped with a warning (silent surface loss)."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.tool_registry import TOOL_REGISTRY
        for t in TOOL_REGISTRY:
            if t.name in ("tortoise_overview", "tortoise_get"):
                assert t.name in mcp_mod.__dict__, t.name
                assert callable(mcp_mod.__dict__[t.name])

    def test_group_assignments(self):
        from tortoise.tool_registry import GROUP_BY_NAME
        assert GROUP_BY_NAME["tortoise_overview"] == "reasoning"
        assert GROUP_BY_NAME["tortoise_get"] == "graph"

    def test_http_allowed_derived(self):
        from tortoise.tool_registry import get_http_allowed
        allowed = get_http_allowed()
        assert "tortoise_overview" in allowed
        assert "tortoise_get" in allowed
