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
    "taxonomy": "_taxonomy",
    "structure": "summarize_structure",
    "structure_check": "check_structure",
    "pointkinds": "_list_pointkinds",
    "tags": "_list_tags",
    "sources": "_list_sources",
    "namespaces": "_list_namespaces",
    "status": "_status",
    "stale": "stale_points",
    "validate_domain": "_validate_domain",
    "audit": "audit",
    "dream_health": "dream_health_check",
    "topics": "_list_topics",
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


class TestOverviewSectionSets:
    """The container's set and the DOOR's set — one approved container, two tables.

    `tortoise_graph_overview` dispatches through `_overview_section`, NOT through
    `TortoiseSDK.graph_overview`, so the two value sets can disagree — and at
    `07f5d61bfa` they did: the SDK declared 13 sections and RAISED for `graphs`/
    `health`, while the MCP door served 15 (13 + those two), and NOTHING compared the
    two. The comments on both sides claimed the SDK tuple drove the door's tuple; it
    did not (#12951 fresh-context review, ISSUE 4).

    The difference is legitimate and is now DECLARED at
    `mcp_server._OVERVIEW_TRANSPORT_ONLY_SECTIONS` (with the reason each one cannot be
    an SDK section). What these cases add is the pin that the declared difference is
    the WHOLE difference, in both directions, and that the two no-`section` folds are
    as different as they in fact are.
    """

    def test_the_door_serves_the_container_s_sections_plus_exactly_the_declared_two(self):
        from tortoise import mcp_server as ms

        served = set(ms._OVERVIEW_SECTIONS)
        declared = set(TortoiseSDK.GRAPH_OVERVIEW_SECTIONS)
        assert declared <= served, (
            "the container dispatches sections this door cannot serve: "
            f"{sorted(declared - served)} — `tortoise_graph_overview(section=…)` is "
            "where the approved values are advertised")
        undeclared = served - declared - set(ms._OVERVIEW_TRANSPORT_ONLY_SECTIONS)
        assert not undeclared, (
            f"{sorted(undeclared)} are served by the MCP door but are neither "
            "container sections nor declared transport-only: a third dispatch table "
            "grew back")

    def test_the_transport_only_set_is_the_two_named_sections(self):
        """Pinned LITERALLY: a third transport-only section is a DECISION, not a diff."""
        from tortoise import mcp_server as ms

        assert set(ms._OVERVIEW_TRANSPORT_ONLY_SECTIONS) == {"graphs", "health"}

    def test_the_sdk_refuses_the_transport_only_sections_and_names_its_own_set(self):
        """The SDK's `Valid sections:` list must be the set it can actually dispatch.

        At the pre-pin head the MCP door served `graphs`/`health` while
        `sdk.graph_overview("health")` raised a message advertising 13 values — so a
        caller reading that message was told, by the container, that a section the
        transport serves does not exist.
        """
        from tortoise import mcp_server as ms

        sdk = TortoiseSDK.__new__(TortoiseSDK)
        for section in ms._OVERVIEW_TRANSPORT_ONLY_SECTIONS:
            with pytest.raises(ValueError) as exc:
                sdk.graph_overview(section, entity_id="point-1")
            message = str(exc.value)
            assert section in message
            valid = [s.strip() for s in message.split("Valid sections:")[1].split(",")]
            assert section not in valid, (
                f"the SDK advertised {section!r} as valid but cannot dispatch it")
            for approved in TortoiseSDK.GRAPH_OVERVIEW_SECTIONS:
                assert approved in valid, approved

    def test_the_two_no_section_folds_differ_exactly_as_documented(self):
        """Both docstrings claimed one set; the SDK and the transport fold differently.

        The SDK's combined read leaves out only its argument-requiring sections; the
        transport's default summary is the OLDER epic-#888 envelope, so it keeps
        `audit`/`dream_health` out and its two transport-only sections in. That is a
        presentation decision on each side — the defect was that each side said they
        were the same, so a caller could not know which envelope to expect.
        """
        from tortoise import mcp_server as ms

        sdk_fold = set(TortoiseSDK.GRAPH_OVERVIEW_SECTIONS) - set(
            TortoiseSDK.GRAPH_OVERVIEW_NEEDS_ARGUMENT)
        transport_fold = set(ms._OVERVIEW_DEFAULT_SUMMARY_KEYS)
        assert sdk_fold - transport_fold == {"audit", "dream_health"}
        assert transport_fold - sdk_fold == set(ms._OVERVIEW_TRANSPORT_ONLY_SECTIONS)


# ── row 3 — search_knowledge, mode= ───────────────────────────────────
SEARCH_KNOWLEDGE_DOORS = {
    "hybrid": "tortoise_fts_query",
    "tag": "query_points_by_tag",
    "sessions": "_search_sessions",
    "entry_points": "_suggest_entry_points",
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
        # It is ALSO asked not to record the population (#7983) — this stub has to
        # accept that kwarg, which is the same reach the pg arm performs.
        monkeypatch.setattr(
            TortoiseSDK, "query_points_by_tag",
            lambda self, tag, _record_reads=True: reached.append(
                ("query_points_by_tag", (), {}))
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


class TestSearchKnowledgeTagArm:
    """The `tag` arm's two dropped contracts (#12951 fresh-context review 2 and 3).

    The arm is four lines: read the tag, slice a page, return. It therefore
      * DECLARED `include_retracted=False` and never applied it — `query_points_by_tag`
        has no exclusion of its own, so every caller was served tombstones, while the
        pre-existing door for the same question filters them (Epic #888); and
      * read the tag through the accessor's DEFAULT `_record_reads=True`, recording the
        WHOLE population while serving one page — the direction `query_points_by_tag`'s
        own docstring says must not happen (#7983), since a tag of 400 points inflated
        the read fraction `f` by 400 for a 100-row answer.

    Both were invisible because the only tag-arm assertion in this file was *which
    door* it reached.
    """

    def _sdk(self, monkeypatch, rows):
        """A real instance: the accessor stubbed, the ledger WATCHED."""
        from tortoise import sdk as sdk_mod

        calls: list = []

        def fake(self, tag, _record_reads=True):
            calls.append((tag, _record_reads))
            return list(rows)

        monkeypatch.setattr(TortoiseSDK, "query_points_by_tag", fake)
        recorded: list = []
        # `kind=` (#13084) is keyword-only on the helper; the stub records the
        # rows exactly as before and ignores the call-kind it now also receives.
        monkeypatch.setattr(
            sdk_mod, "_ledger_record",
            lambda label, page, **kw: recorded.append((label, list(page))))
        return TortoiseSDK.__new__(TortoiseSDK), calls, recorded

    def test_retracted_points_are_excluded_by_default(self, monkeypatch):
        rows = [{"id": "live-1", "status": "live"},
                {"id": "dead-1", "status": "retracted"},
                {"id": "live-2"}]
        sdk, _, _ = self._sdk(monkeypatch, rows)
        out = sdk.search_knowledge(mode="tag", tag="pricing")
        assert [r["id"] for r in out["results"]] == ["live-1", "live-2"], (
            "a caller who omits include_retracted (the default) was served the tombstone")
        # the envelope describes the population the caller asked for, not the raw tag
        assert out["total"] == 2 and out["hasMore"] is False

    def test_include_retracted_returns_the_tombstones(self, monkeypatch):
        rows = [{"id": "live-1", "status": "live"},
                {"id": "dead-1", "status": "retracted"}]
        sdk, _, _ = self._sdk(monkeypatch, rows)
        out = sdk.search_knowledge(mode="tag", tag="pricing", include_retracted=True)
        assert [r["id"] for r in out["results"]] == ["live-1", "dead-1"], (
            "the flag must still surface tombstones — the fix is the default, not a filter")

    def test_the_filter_runs_before_the_page_so_the_total_is_honest(self, monkeypatch):
        rows = [{"id": "dead-1", "status": "retracted"},
                {"id": "live-1"}, {"id": "live-2"}, {"id": "live-3"}]
        sdk, _, _ = self._sdk(monkeypatch, rows)
        out = sdk.search_knowledge(mode="tag", tag="pricing", limit=2)
        assert [r["id"] for r in out["results"]] == ["live-1", "live-2"]
        assert out["total"] == 3 and out["hasMore"] is True

    def test_the_accessor_is_asked_not_to_record_the_population(self, monkeypatch):
        sdk, calls, _ = self._sdk(monkeypatch, [{"id": f"p{i}"} for i in range(5)])
        sdk.search_knowledge(mode="tag", tag="pricing", limit=2)
        assert calls == [("pricing", False)], (
            "the tag arm IS the paginated caller `query_points_by_tag`'s docstring "
            "warns about: recording the whole tag would bias `f` HIGH (#7983)")

    def test_the_ledger_records_the_served_page_not_the_tag(self, monkeypatch):
        rows = [{"id": f"p{i}"} for i in range(5)]
        sdk, _, recorded = self._sdk(monkeypatch, rows)
        sdk.search_knowledge(mode="tag", tag="pricing", offset=1, limit=2)
        assert recorded == [("Point", [{"id": "p1"}, {"id": "p2"}])], (
            "the ledger must see exactly the rows this call served — one page of two, "
            "never the five-point tag")

    def test_the_ledger_is_not_written_when_the_page_is_empty(self, monkeypatch):
        """A page past the end records NOTHING — an empty read is not a read of rows."""
        sdk, _, recorded = self._sdk(monkeypatch, [{"id": "p0"}])
        out = sdk.search_knowledge(mode="tag", tag="pricing", offset=9)
        assert out["results"] == [] and out["hasMore"] is False
        assert recorded == [("Point", [])]


# ── row 4 — list_knowledge, kind= ─────────────────────────────────────
LIST_KNOWLEDGE_DOORS = {
    "claim": "paginated_query",
    "source": "_list_sources",
    "source_reliability": "get_source_reliability",
    "namespace": "_list_namespaces",
    "topic": "_list_topics",
    "diary": "_diary_read",
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
             "calibrate_summary", "_provenance", "compute_confidence"},
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
                        {"get_confidence", "_provenance"}, reached)
        sdk.check_confidence(claim_id="point-1")
        assert [r[0] for r in reached] == ["get_confidence"]
        out = sdk.check_confidence(claim_id="point-1", include_provenance=True)
        assert [r[0] for r in reached] == ["get_confidence", "get_confidence",
                                           "_provenance"]
        assert set(out) == {"confidence", "provenance"}


# ── row 8 — explore_connections, dispatch by argument ─────────────────
class TestExploreConnections:
    def test_no_selector_refuses(self, monkeypatch):
        sdk = _bare_sdk(monkeypatch, {"_suggest_entry_points", "_expand_relationships"},
                        [])
        with pytest.raises(ValueError) as exc:
            sdk.explore_connections()
        assert "node_id" in str(exc.value) and "query" in str(exc.value)

    def test_include_content_takes_the_expand_door(self, monkeypatch):
        reached: list = []
        sdk = _bare_sdk(monkeypatch, {"_expand_relationships"}, reached)
        sdk.explore_connections("point-1", include_content=True)
        assert [r[0] for r in reached] == ["_expand_relationships"]

    def test_a_query_resolves_then_walks(self, monkeypatch):
        """`traverse` was DELETED with `explore_connections` as its destination and no
        implementation (#4282), so this arm is the only way back to the walk."""
        asked: list = []
        monkeypatch.setattr(
            TortoiseSDK, "_suggest_entry_points",
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
        monkeypatch.setattr(TortoiseSDK, "_suggest_entry_points",
                            lambda self, q, **kw: [])
        sdk = TortoiseSDK.__new__(TortoiseSDK)
        out = sdk.explore_connections(query="nothing at all")
        assert out["results"] == [] and "matched no node" in out["error"]


# ── row 10 — review_link_candidates, dispatch by argument ─────────────
REVIEW_LINK_DOORS = {
    "connections": "review_connections",
    "dedup": "_list_dedup_candidates",
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


# ── the six doors that stay live, and the envelope that is the reason ────────
#
# `tortoise/tool_registry.py`'s retirement block, this file's `TestApprovedNames`
# docstring and the PR body all point at `TestWrapEnvelopeDoorsStayLive` as the
# pin for the six doors this batch deliberately does NOT retire (#12951 review
# P2: the citation named a class that did not exist, so the load-bearing
# exception was pinned by nothing). This is that class.
#
# The exception is the #8482 failure repair: these six declare `-> list[dict]`,
# and the array schema is what carries a `_safe` failure payload. Their
# containers return `Any`, so retiring a door before the container declares an
# envelope would move the repair off the advertised path. The pin therefore has
# three halves — the door is LIVE, its declared envelope is a LIST, and its
# handler reaches an SDK door that RESOLVES — and it is asserted for every one
# of the six rather than for a representative.
WRAP_ENVELOPE_DOORS = (
    # tool name, the SDK door its handler reaches
    ("tortoise_search_sessions", "search_knowledge"),
    ("tortoise_suggest_entry_points", "search_knowledge"),
    # `list_batches` is the one member whose SDK method did NOT retire (it is the
    # still-public exact read that `list_knowledge(kind='batch')` delegates to), so
    # this door reaches the method itself rather than the container.
    ("tortoise_list_batches", "list_batches"),
    ("tortoise_list_namespaces", "graph_overview"),
    ("tortoise_diary_read", "list_knowledge"),
    ("tortoise_expand_relationships", "explore_connections"),
)


def _handler_sdk_reach(fn) -> set[str]:
    """Every `<sdk>.<method>` the handler hands to `_safe` (`_get_org_sdk().<m>`)."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    reached = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Call)):
            continue
        inner = node.value.func
        inner_name = getattr(inner, "id", getattr(inner, "attr", ""))
        if inner_name == "_get_org_sdk":
            reached.add(node.attr)
    return reached


class TestWrapEnvelopeDoorsStayLive:
    @pytest.mark.parametrize("tool,sdk_door", WRAP_ENVELOPE_DOORS)
    def test_the_door_is_live_and_keeps_its_list_envelope(self, tool, sdk_door):
        """Live in the registry, still `-> list[dict]`, and its reach RESOLVES."""
        import inspect

        from tortoise import mcp_server as ms

        live = {t.name: t for t in TOOL_REGISTRY}
        assert tool in live, (
            f"{tool} is one of the six doors this batch keeps live (#8482 repair). "
            "Retiring it before its container declares an envelope moves the "
            "repair off the advertised path")
        assert tool not in RETIRED_USE_INSTEAD, (
            f"{tool} was retired forward — that is the exact move the six-door "
            "exception forbids until the container declares `-> list[dict]`")
        assert tool not in {t.name for t in RETIRED_TOOL_REGISTRY}, tool

        fn = getattr(ms, tool)
        # `eval_str=True`: `mcp_server` defers annotations (`from __future__ import
        # annotations`), so the raw annotation is the STRING `'list[dict]'` and a
        # string comparison would pass on `'list[dict] | None'` too.
        annotation = inspect.signature(fn, eval_str=True).return_annotation
        assert annotation == list[dict], (
            f"{tool} declares {annotation!r}: the array schema is what carries the "
            "#8482 `_safe` failure payload, so a non-list envelope defeats the "
            "reason this door is still live")

        reached = _handler_sdk_reach(fn)
        assert sdk_door in reached, (
            f"{tool} no longer answers through `{sdk_door}` (reaches {sorted(reached)})"
            " — the door must keep answering through a door that resolves")
        assert hasattr(TortoiseSDK, sdk_door), (
            f"{tool} answers through `TortoiseSDK.{sdk_door}`, which does not exist "
            "— the live door would raise on its first call")

    def test_the_six_are_the_whole_exception(self):
        """No live door OUTSIDE these six both declares `-> list[dict]` and reaches a container.

        The parametrized case above asserts the six individually, which makes a
        `kept <= live_names` / `not retired_forward` restatement of it tautological
        (#12951 review round 2, P3). The property that is NOT implied is the one this
        exception is actually about: the envelope is kept for doors that answer through
        a read/query CONTAINER, so a seventh such door must be a deliberate decision
        recorded here, not an addition this file cannot see. Measured over the live
        registry: three live doors also declare `-> list[dict]` and reach NO container
        (`tortoise_calibrate_summary`, `tortoise_check_structure`, `tortoise_search`),
        so a container reach — not the envelope alone — is what makes a door a member.
        """
        import inspect

        from tortoise import mcp_server as ms

        live_names = {t.name for t in TOOL_REGISTRY}
        kept = {tool for tool, _ in WRAP_ENVELOPE_DOORS}
        assert kept <= live_names
        retired_forward = kept & set(RETIRED_USE_INSTEAD)
        assert not retired_forward, retired_forward

        offenders = {}
        for tool in sorted(live_names - kept):
            fn = getattr(ms, tool, None)
            if fn is None:  # a registered name with no handler is another test's finding
                continue
            annotation = inspect.signature(fn, eval_str=True).return_annotation
            if annotation != list[dict]:
                continue
            hit = _handler_sdk_reach(fn) & set(CONTAINERS)
            if hit:
                offenders[tool] = sorted(hit)
        assert not offenders, (
            f"{offenders} declare `-> list[dict]` AND answer through a container: the "
            "wrap-envelope exception is exactly these six doors, so a seventh carries "
            "the same #8482/#10914 decision and belongs in WRAP_ENVELOPE_DOORS")


class TestSuggestEntryPointsFallback:
    """The MCP door's ZERO-HIT arm — the one no test exercised (#12951 review P1).

    The door's shape is: try the hybrid read, and if it returns nothing fall back to
    entity resolution. The fallback used to call `sdk.suggest_entry_points`, which this
    PR re-privatised — and the call sits OUTSIDE `_safe`, so a live advertised read
    raised `AttributeError` instead of answering `[]` on every zero-hit query. Nothing
    caught it because the only tool-level test accepted a non-empty hybrid answer.
    """

    def test_the_zero_hit_fallback_answers_through_the_container(self, monkeypatch):
        from tortoise import mcp_auth
        from tortoise import mcp_server as ms

        reached = []
        sdk = _bare_sdk(monkeypatch, {"search_knowledge"}, reached)
        # the hybrid arm's own door, returning NOTHING — the fallback's trigger
        monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", lambda *a, **k: [])
        monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: sdk)
        # `_safe`'s fail-closed auth gate needs a transport mode; stdio is the
        # dev-mode lane these routing tests run on.
        token = mcp_auth._transport_mode.set("stdio")

        try:
            out = ms.tortoise_suggest_entry_points("nothing matches this", limit=3)
        finally:
            mcp_auth._transport_mode.reset(token)

        assert out == {"door": "search_knowledge"}, out
        # the recorder is patched onto the CLASS, so an unbound call arrives with
        # `self` first — the assertion is on the door, the query and the kwargs.
        assert len(reached) == 1, reached
        label, args, kwargs = reached[0]
        assert label == "search_knowledge", reached
        assert args[-1] == "nothing matches this", reached
        assert kwargs == {"mode": "entry_points", "kind": None, "limit": 3}, reached

    def test_an_empty_query_is_no_hits_and_touches_no_door(self, monkeypatch):
        """`[]` for BOTH halves — by two different routes, and only one is longstanding.

        `""` — the container REFUSES the empty string (it is a text-driven read), and
        this door's contract is "no hits", so the refusal is answered here rather than
        raised. This door answered `[]` before the guard as well.

        `"   "` — the container does NOT refuse a blank string. The old handler reached
        the hybrid read with the raw string, and `tortoise_fts_query("   ")` matches every
        row (measured: 5 rows at `limit=5`, 6 of 6 rows at `limit=50` on a 6-point graph),
        so a blank query used to answer with arbitrary entities. That is a false
        resolution answer, not an enumeration request, so it is a DELIBERATE CHANGE to
        answer `[]` (#12951 review rounds 1 and 3).
        """
        from tortoise import mcp_auth
        from tortoise import mcp_server as ms

        reached = []
        sdk = _bare_sdk(monkeypatch, {"search_knowledge"}, reached)
        monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: sdk)
        token = mcp_auth._transport_mode.set("stdio")
        try:
            assert ms.tortoise_suggest_entry_points("") == []
            assert ms.tortoise_suggest_entry_points("   ") == []
        finally:
            mcp_auth._transport_mode.reset(token)
        assert reached == []


class TestApprovedNames:
    @pytest.mark.parametrize("name", CONTAINERS)
    def test_the_name_is_the_owner_approved_one(self, name):
        """`tortoise/__all__.py` is the approved list and is NOT derivable from code.

        A container built under a name that is not on it would be an unapproved
        addition however sensible the name is.

        The declaration is loaded BY PATH, never as the dotted name: importing
        `tortoise.__all__` binds the MODULE on the package, and `from tortoise
        import *` then raises `TypeError: 'module' object is not iterable` for
        the rest of the process — which is the trap
        `test_sdk_surface_declaration.py::test_loading_the_declaration_by_path_does_not_shadow_star_import`
        pins (#12951 review P1: this test used the dotted form and red it).
        """
        from pathlib import Path

        from tools import sdk_surface_declaration as surface

        approved = surface.load_declaration(
            Path(__file__).resolve().parent.parent / "tortoise" / "__all__.py")

        assert name in approved["__all__"], (
            f"{name} is not one of the owner-approved 40 — building it under this name "
            "would be a surface change nobody approved")


class TestProvenanceDoorDoesNotPayTheCalibrationGate:
    """The RETIRED `tortoise_provenance` door answers the CHAIN on an uncalibrated graph.

    `TortoiseSDK.provenance` retired into `check_confidence`, whose claim arm pays the
    #344 / #7478 EP gate — so a door whose whole job is "who decided this?" raised
    `CalibrationError` on any graph with one uncalibrated evidence point, while the
    chain itself resolved fine (measured on this head; found by the test sweep, not
    by the review). The fix passes `require_calibration=False` because this door
    THROWS THE POSTERIOR AWAY and answers with the chain only.

    ⛔ This class exists because nothing observed the door's posture: `grep -rn
    'tortoise_provenance(' tests/` had no hits, so reverting the fix left the suite
    GREEN — the fix-round review's P2 (the same class of defect as the review's own
    "the citation named a pin that did not exist").
    """

    def _sdk_that_pays_the_gate(self, monkeypatch, chain, seen):
        """The CONTAINER's recorded contract: the claim arm raises unless opted out.

        Patched onto the CLASS (the same discipline as `_bare_sdk`) so the real
        `check_confidence` dispatch runs and the argument it forwards is what is
        measured — not a spy that would accept any kwargs.
        """
        from tortoise.exceptions import CalibrationError

        def get_confidence(self, claim_id, require_calibration=None):
            seen.append(require_calibration)
            if require_calibration is not False:
                # the #344 posture: fail-closed on an uncalibrated posterior
                raise CalibrationError(
                    "get_confidence: 1 uncalibrated evidence points")
            return {"mean": 0.5, "variance": 0.1}

        monkeypatch.setattr(TortoiseSDK, "get_confidence", get_confidence)
        monkeypatch.setattr(TortoiseSDK, "_provenance",
                            lambda self, pid: chain)
        return TortoiseSDK.__new__(TortoiseSDK)

    def test_the_door_answers_the_chain(self, monkeypatch):
        from tortoise import mcp_auth
        from tortoise import mcp_server as ms

        chain = {"authoredBy": ["operator-1"], "entity": "point-1"}
        seen: list = []
        sdk = self._sdk_that_pays_the_gate(monkeypatch, chain, seen)
        monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: sdk)
        token = mcp_auth._transport_mode.set("stdio")
        try:
            out = ms.tortoise_provenance("point-1")
        finally:
            mcp_auth._transport_mode.reset(token)

        assert not isinstance(out, ms._SafeError), (
            "the live door returned the #344 gate's CalibrationError instead of the "
            f"chain: {out!r} — this door discards the posterior and owes the chain")
        assert out == chain
        # the opt-out is the DOOR's argument, not an ambient default
        assert seen == [False], seen

    def test_a_caller_that_serves_the_posterior_still_pays_the_gate(self, monkeypatch):
        """The gate is not relaxed — only this one door opts out."""
        import inspect

        from tortoise.exceptions import CalibrationError

        seen: list = []
        sdk = self._sdk_that_pays_the_gate(monkeypatch, {}, seen)
        with pytest.raises(CalibrationError):
            sdk.check_confidence("point-1", include_provenance=True)
        # and the container's own default remains "the shared posture"
        assert (inspect.signature(TortoiseSDK.check_confidence)
                .parameters["require_calibration"].default is None), (
            "check_confidence's default posture changed — `None` means 'use the "
            "shared fail-closed default (#344)', and only the provenance door may "
            "opt out of it")



class TestProvenanceDoorIsNotAReadTier:
    """The RETIRED `tortoise_provenance` door WRITES, so it cannot claim `read`.

    `#12951` fresh-context review, ISSUE 1. The door answers through
    `check_confidence(claim_id, include_provenance=True)`, whose claim arm reaches
    `get_confidence` — and that read's lazy-consistency path DREAMS a dirty root
    (`dream(dirty_only=True, …)` runs `SET n.confidence`). It declared `risk="read"`,
    so its #3883 shim shipped `readOnlyHint: true` over a call that can write a
    `graphs:read` key's graph — the #4122 defect in its exact shape.

    Two things hid it, and this class closes both:

      * `require_calibration=False` was read as "no write". It removes the GATE, not
        the dream — the posture the calibration-pin class above measures, and the
        reason that pin did not catch this.
      * the reach lives inside the `_check_confidence_provenance` HELPER and the row's
        binding is blanked (`sdk_method=""`), so the #4122 ledger's op-keyed arm had
        nothing to key on. It is declared by TOOL NAME now
        (`tests/tool_surface_capabilities.NON_SDK_READ_BINDINGS`).
    """

    def _row(self):
        row = {t.name: t for t in RETIRED_TOOL_REGISTRY}.get("tortoise_provenance")
        assert row is not None, "tortoise_provenance left the retired registry"
        return row

    def test_the_tier_is_maintenance_because_the_door_reaches_a_write(self):
        row = self._row()
        assert row.risk == "maintenance", (
            f"tortoise_provenance is tiered {row.risk!r}: its claim arm reaches "
            "get_confidence, whose lazy dream WRITES n.confidence (#4122)")
        assert row.annotations is not None
        assert row.annotations.readOnlyHint is False, (
            "readOnlyHint is what a client auto-approves on — a maintenance-tier "
            "door must not claim it (#4122)")

    def test_the_4122_ledger_now_sees_the_door(self):
        """The declaration is by TOOL NAME, because the binding is blank.

        `declared_set_violations` is the arm that refuses a read-through writer bound
        to a non-`maintenance` HTTP tool. Before this declaration it could not see
        this door at all, so demoting the tier back to `read` reddened NOTHING.
        """
        from tests import tool_surface_capabilities as caps

        assert caps.NON_SDK_READ_BINDINGS.get("check_confidence") == \
            "tortoise_provenance"
        problems = caps.declared_set_violations(
            [*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY])
        named = [p for p in problems if "tortoise_provenance" in p]
        assert not named, named
