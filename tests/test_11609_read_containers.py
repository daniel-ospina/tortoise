"""#11609 Phase 2 — the READ/QUERY family's containers (#4282).

The approved surface has ONE read verb per question (`docs/product/beta-sdk-surface.md`
rows 3-10). Phase 0.1 recorded, for each of them, the discriminator it dispatches on
(`docs/product/bridge-table.md` Part A) and — in its Part C1 — that six of them have
**no `def` on `TortoiseSDK` at all**: *"These are not renames. They are new methods that
must be built in Phase 2."* This file gates the six that were built here:

    search_knowledge        mode=            (row 3)
    list_knowledge          kind=            (row 4)
    check_confidence        (by argument)   (row 5)
    graph_overview          section=         (row 6, C1 — the MCP door existed, no `def`)
    explore_connections     (by argument)   (row 8)
    review_link_candidates  (by argument)   (row 10)

WHAT THIS GATES, IN BOTH DIRECTIONS

  1. **Every discriminator value reaches its OWN door.** Measured by substitution, so a
     regression names the arm that broke instead of "dispatch is wrong".
  2. **An unknown value REFUSES by name**, listing the valid set. A closed enum that
     silently answers something else is worse than no enum (AIP-193: brief, actionable,
     no implementation detail).
  3. **The mapped MCP name answers through the #3883 shim** after it leaves the
     advertised surface — a client that still calls it gets the same rows plus a warning.
  4. **The declared value tuples are the DISPATCH, not prose**: each declared tuple is
     walked and every value must reach exactly one door. A value added to the tuple with
     no arm is a hole the advertisement would otherwise hide (`#10914 review P3`).

NO DATABASE: these are routing tests, and routing is what a container changes. Each
door's own behaviour is covered by its own suite, which this change does not touch.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)


def _bare_sdk(monkeypatch, doors, reached):
    """An SDK instance with EVERY door substituted by a recorder.

    Built with `__new__` so no connection is opened: the substituted doors are the only
    thing that would touch a database. `raising=True` on the patches is deliberate — a
    renamed door must fail this file loudly rather than be created on the spot by a
    permissive spy.
    """
    sdk = TortoiseSDK.__new__(TortoiseSDK)

    def record(label):
        def _fn(*args, **kwargs):
            reached.append((label, args, kwargs))
            return {"door": label}
        return _fn

    for door in doors:
        monkeypatch.setattr(TortoiseSDK, door, record(door))
    return sdk


# ── row 6 — graph_overview, section= ──────────────────────────────────
GRAPH_OVERVIEW_DOORS = {
    "taxonomy": "taxonomy",
    "structure": "summarize_structure",
    "structure_check": "check_structure",
    "pointkinds": "list_pointkinds",
    "tags": "list_tags",
    "sources": "list_sources",
    "namespaces": "list_namespaces",
    "status": "status",
    "stale": "stale_points",
    "validate_domain": "validate_domain",
    "audit": "audit",
    "dream_health": "dream_health_check",
    "topics": "list_topics",
}


class TestGraphOverview:
    def test_every_declared_section_has_an_arm(self, monkeypatch):
        """The declared tuple and the dispatch must agree, value by value."""
        assert set(GRAPH_OVERVIEW_DOORS) == set(TortoiseSDK.GRAPH_OVERVIEW_SECTIONS), (
            "the container's declared sections and this file's routing table disagree — "
            "one of them is stale, and a caller reads the declared one")

    @pytest.mark.parametrize("section,door", sorted(GRAPH_OVERVIEW_DOORS.items()))
    def test_section_reaches_its_door(self, section, door, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(GRAPH_OVERVIEW_DOORS.values()), reached)
        sdk.graph_overview(section, domain="product", entity_id="point-1")
        assert [r[0] for r in reached] == [door], (
            f"section {section!r} must reach exactly {door!r}, reached "
            f"{[r[0] for r in reached]}")

    def test_an_unknown_section_refuses_and_names_the_valid_set(self, monkeypatch):
        sdk = _bare_sdk(monkeypatch, set(GRAPH_OVERVIEW_DOORS.values()), [])
        with pytest.raises(ValueError) as exc:
            sdk.graph_overview("nonsense")
        message = str(exc.value)
        assert "nonsense" in message
        for section in TortoiseSDK.GRAPH_OVERVIEW_SECTIONS:
            assert section in message, (
                f"{section!r} is dispatchable but the refusal does not name it, so a "
                "caller cannot recover from the typo it just made")

    @pytest.mark.parametrize("section", ["topics", "validate_domain"])
    def test_a_section_that_needs_an_argument_refuses_by_name(self, section, monkeypatch):
        """Not a silent `None` into the door — the refusal names the argument."""
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(GRAPH_OVERVIEW_DOORS.values()), reached)
        with pytest.raises(ValueError) as exc:
            sdk.graph_overview(section)
        assert reached == [], f"{section!r} called a door with no argument"
        assert "requires" in str(exc.value)

    def test_the_combined_read_skips_the_argument_sections(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(GRAPH_OVERVIEW_DOORS.values()), reached)
        combined = sdk.graph_overview()
        assert set(combined) == set(TortoiseSDK.GRAPH_OVERVIEW_SECTIONS) - set(
            TortoiseSDK.GRAPH_OVERVIEW_NEEDS_ARGUMENT)
        for skipped in TortoiseSDK.GRAPH_OVERVIEW_NEEDS_ARGUMENT:
            assert skipped not in combined

    def test_stale_forwards_days_and_limit(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(GRAPH_OVERVIEW_DOORS.values()), reached)
        sdk.graph_overview("stale", days=7, limit=3)
        assert reached[0][2] == {"days": 7, "limit": 3}


# ── row 3 — search_knowledge, mode= ───────────────────────────────────
SEARCH_KNOWLEDGE_DOORS = {
    "hybrid": "tortoise_fts_query",
    "tag": "query_points_by_tag",
    "sessions": "search_sessions",
    "entry_points": "suggest_entry_points",
    "issue_insight": "issue_insight",
}


class TestSearchKnowledge:
    def test_every_declared_mode_has_an_arm(self):
        declared = set(TortoiseSDK.SEARCH_KNOWLEDGE_MODES)
        # `full_text` delegates to the OTHER container (both approved documents claim
        # this read — bridge-table §D4), so it is measured separately below rather than
        # through a door of its own.
        assert declared == set(SEARCH_KNOWLEDGE_DOORS) | {"full_text"}

    @pytest.mark.parametrize("mode,door", sorted(SEARCH_KNOWLEDGE_DOORS.items()))
    def test_mode_reaches_its_door(self, mode, door, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(SEARCH_KNOWLEDGE_DOORS.values()), reached)
        # The tag arm pages the rows its door returns, so that door answers with rows.
        monkeypatch.setattr(
            TortoiseSDK, "query_points_by_tag",
            lambda self, tag: reached.append(("query_points_by_tag", (), {}))
            or [{"id": "point-1"}])
        kwargs = {"query": "q", "tag": "t", "title": "a title"}
        sdk.search_knowledge(mode=mode, **kwargs)
        assert [r[0] for r in reached] == [door]

    def test_full_text_delegates_to_list_knowledge(self, monkeypatch):
        """ONE implementation: the exact claim list is `list_knowledge(kind='claim')`."""
        seen = {}

        def fake(self, kind="claim", **kwargs):
            seen["kind"] = kind
            seen.update(kwargs)
            return {"results": [], "total": 0, "hasMore": False}

        monkeypatch.setattr(TortoiseSDK, "list_knowledge", fake)
        sdk = TortoiseSDK.__new__(TortoiseSDK)
        out = sdk.search_knowledge(mode="full_text", kind="statement", offset=5,
                                   limit=2)
        assert seen["kind"] == "claim"
        assert seen["point_kind"] == "statement"
        assert seen["offset"] == 5 and seen["limit"] == 2
        assert out["hasMore"] is False

    def test_the_default_mode_is_hybrid(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(SEARCH_KNOWLEDGE_DOORS.values()), reached)
        sdk.search_knowledge(query="q")
        assert reached[0][0] == "tortoise_fts_query"

    def test_an_unknown_mode_refuses_and_names_the_valid_set(self, monkeypatch):
        sdk = _bare_sdk(monkeypatch, set(SEARCH_KNOWLEDGE_DOORS.values()), [])
        with pytest.raises(ValueError) as exc:
            sdk.search_knowledge(query="q", mode="semantic")
        message = str(exc.value)
        assert "semantic" in message
        for mode in TortoiseSDK.SEARCH_KNOWLEDGE_MODES:
            assert mode in message

    @pytest.mark.parametrize("mode,missing", [
        ("tag", "tag"), ("sessions", "query"), ("entry_points", "query"),
        ("issue_insight", "title"),
    ])
    def test_a_mode_without_its_argument_refuses_by_name(self, mode, missing, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(SEARCH_KNOWLEDGE_DOORS.values()), reached)
        with pytest.raises(ValueError) as exc:
            sdk.search_knowledge(mode=mode)
        assert reached == [], f"mode={mode!r} called a door with no argument"
        assert missing in str(exc.value)


# ── row 4 — list_knowledge, kind= ─────────────────────────────────────
LIST_KNOWLEDGE_DOORS = {
    "claim": "paginated_query",
    "source": "list_sources",
    "source_reliability": "get_source_reliability",
    "namespace": "list_namespaces",
    "topic": "list_topics",
    "diary": "diary_read",
}


class TestListKnowledge:
    def test_every_declared_kind_has_an_arm(self):
        assert set(TortoiseSDK.LIST_KNOWLEDGE_KINDS) == set(LIST_KNOWLEDGE_DOORS) | {
            "batch"}

    @pytest.mark.parametrize("kind,door", sorted(LIST_KNOWLEDGE_DOORS.items()))
    def test_kind_reaches_its_door(self, kind, door, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(LIST_KNOWLEDGE_DOORS.values()), reached)
        sdk.list_knowledge(kind, url="https://example.test/a", entity_id="e1",
                           agent="a1")
        assert [r[0] for r in reached] == [door]

    @pytest.mark.parametrize("kwargs,door", [
        ({"id": "batch-1"}, "list_batch"),
        ({}, "list_batches"),
    ])
    def test_batch_dispatches_on_the_id(self, kwargs, door, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, {"list_batch", "list_batches"}, reached)
        sdk.list_knowledge("batch", **kwargs)
        assert [r[0] for r in reached] == [door]

    def test_claim_forwards_paging_to_the_exact_read(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, {"paginated_query"}, reached)
        sdk.list_knowledge("claim", point_kind="statement", offset=20, limit=10)
        _label, args, kwargs = reached[0]
        assert kwargs["kind"] == "statement"
        assert kwargs["skip"] == 20 and kwargs["limit"] == 10
        assert args[0] is sdk and len(args) == 1, "only self is positional"


    def test_an_unknown_kind_refuses_and_names_the_valid_set(self, monkeypatch):
        sdk = _bare_sdk(monkeypatch, set(LIST_KNOWLEDGE_DOORS.values()), [])
        with pytest.raises(ValueError) as exc:
            sdk.list_knowledge("drafts")
        message = str(exc.value)
        assert "drafts" in message
        for kind in TortoiseSDK.LIST_KNOWLEDGE_KINDS:
            assert kind in message

    @pytest.mark.parametrize("kind,missing", [
        ("source_reliability", "url"), ("topic", "entity_id"), ("diary", "agent"),
    ])
    def test_a_kind_without_its_argument_refuses_by_name(self, kind, missing, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(LIST_KNOWLEDGE_DOORS.values()), reached)
        with pytest.raises(ValueError) as exc:
            sdk.list_knowledge(kind)
        assert reached == []
        assert missing in str(exc.value)


# ── row 5 — check_confidence, dispatch by argument ────────────────────
class TestCheckConfidence:
    @pytest.mark.parametrize("kwargs,door", [
        ({"claim_id": "point-1"}, "get_confidence"),
        ({"topic": "pricing"}, "belief_timeline"),
        ({"session": True}, "session_context"),
        ({"calibrate": True}, "calibrate_summary"),
        ({}, "calibrate_summary"),
    ])
    def test_selector_reaches_its_door(self, kwargs, door, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(
            monkeypatch,
            {"get_confidence", "belief_timeline", "session_context",
             "calibrate_summary", "provenance", "compute_confidence"},
            reached)
        sdk.check_confidence(**kwargs)
        assert [r[0] for r in reached] == [door]

    def test_it_never_recomputes(self, monkeypatch):
        """The container is a READ. `compute_confidence` writes, so it is not an arm."""
        reached: list = []
        sdk = _bare_sdk(
            monkeypatch,
            {"get_confidence", "belief_timeline", "session_context",
             "calibrate_summary", "compute_confidence"},
            reached)
        sdk.check_confidence(claim_id="point-1")
        sdk.check_confidence(topic="t")
        sdk.check_confidence(session=True)
        sdk.check_confidence()
        assert "compute_confidence" not in [r[0] for r in reached]

    def test_provenance_rides_the_claim_arm_only_when_asked(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch,
                        {"get_confidence", "provenance"}, reached)
        sdk.check_confidence(claim_id="point-1")
        assert [r[0] for r in reached] == ["get_confidence"]
        out = sdk.check_confidence(claim_id="point-1", include_provenance=True)
        assert [r[0] for r in reached] == ["get_confidence", "get_confidence",
                                           "provenance"]
        assert set(out) == {"confidence", "provenance"}


# ── row 8 — explore_connections, dispatch by argument ─────────────────
class TestExploreConnections:
    def test_no_selector_refuses(self, monkeypatch):
        sdk = _bare_sdk(monkeypatch, {"suggest_entry_points", "expand_relationships"},
                        [])
        with pytest.raises(ValueError) as exc:
            sdk.explore_connections()
        assert "node_id" in str(exc.value) and "query" in str(exc.value)

    def test_include_content_takes_the_expand_door(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, {"expand_relationships"}, reached)
        sdk.explore_connections("point-1", include_content=True)
        assert [r[0] for r in reached] == ["expand_relationships"]

    def test_a_query_resolves_then_walks(self, monkeypatch):
        """`traverse` was DELETED with `explore_connections` as its destination and no
        implementation (#4282), so this arm is the only way back to the walk."""
        asked: list = []
        monkeypatch.setattr(
            TortoiseSDK, "suggest_entry_points",
            lambda self, q, **kw: asked.append(q) or [{"id": "point-9"}])
        sdk = TortoiseSDK.__new__(TortoiseSDK)
        sdk._proj = SimpleNamespace(db="db", graph_name="g")

        import tortoise.navigation as nav
        walked: list = []
        monkeypatch.setattr(
            nav, "entityProfile",
            lambda db, graph, node_id, **kw: walked.append((node_id, kw)) or {"ok": True})
        out = sdk.explore_connections(query="pricing tiers", hops=3)
        assert asked == ["pricing tiers"]
        assert walked and walked[0][0] == "point-9"
        assert walked[0][1]["hops"] == 3
        assert out == {"ok": True}

    def test_a_query_matching_nothing_says_so(self, monkeypatch):
        monkeypatch.setattr(TortoiseSDK, "suggest_entry_points",
                            lambda self, q, **kw: [])
        sdk = TortoiseSDK.__new__(TortoiseSDK)
        out = sdk.explore_connections(query="nothing at all")
        assert out["results"] == [] and "matched no node" in out["error"]


# ── row 10 — review_link_candidates, dispatch by argument ─────────────
REVIEW_LINK_DOORS = {
    "connections": "review_connections",
    "dedup": "list_dedup_candidates",
    "cross_lens": "get_cross_lens_candidates",
}


class TestReviewLinkCandidates:
    def test_every_declared_kind_has_an_arm(self):
        assert set(TortoiseSDK.REVIEW_LINK_CANDIDATE_KINDS) == set(REVIEW_LINK_DOORS)

    @pytest.mark.parametrize("kind,door", sorted(REVIEW_LINK_DOORS.items()))
    def test_kind_reaches_its_door(self, kind, door, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, set(REVIEW_LINK_DOORS.values()), reached)
        sdk.review_link_candidates(kind)
        assert [r[0] for r in reached] == [door]

    def test_an_unknown_kind_refuses_and_names_the_valid_set(self, monkeypatch):
        sdk = _bare_sdk(monkeypatch, set(REVIEW_LINK_DOORS.values()), [])
        with pytest.raises(ValueError) as exc:
            sdk.review_link_candidates("nope")
        for kind in TortoiseSDK.REVIEW_LINK_CANDIDATE_KINDS:
            assert kind in str(exc.value)


# ── the containers are on the approved surface, and they ship with a retirement ──
CONTAINERS = (
    "search_knowledge",
    "list_knowledge",
    "check_confidence",
    "explore_connections",
    "graph_overview",
    "review_link_candidates",
)


class TestApprovedNames:
    @pytest.mark.parametrize("name", CONTAINERS)
    def test_the_name_is_the_owner_approved_one(self, name):
        """`tortoise/__all__.py` is the approved list and is NOT derivable from code.

        A container built under a name that is not on it would be an unapproved
        addition however sensible the name is.
        """
        from tortoise import __all__ as approved

        assert name in approved.__all__, (
            f"{name} is not one of the owner-approved 40 — building it under this name "
            "would be a surface change nobody approved")
