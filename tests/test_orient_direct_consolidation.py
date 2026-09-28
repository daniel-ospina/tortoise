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

import json
import os
import shutil
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
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate)."""
    from tortoise.mcp_auth import (  # noqa: I001
        _current_org_id, _current_org_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


@pytest.fixture
def mcp_sdk(sdk):
    """Swap the module-level SDK so MCP tool wrappers hit the test DB."""
    import tortoise.mcp_server as mcp_mod
    orig_sdk = mcp_mod.sdk
    mcp_mod.sdk = sdk
    yield sdk
    mcp_mod.sdk = orig_sdk


def _strip_timing(d: dict) -> dict:
    """Strip timing-sensitive fields (uptime, db.latency_ms) before a
    cross-call equality — #1517: metrics() measures wall-clock at call time,
    so two calls land ms apart and byte-equality flakes under load."""
    if not isinstance(d, dict):
        return d
    out = {k: v for k, v in d.items() if k not in ("uptime", "latency_ms")}
    if isinstance(out.get("db"), dict):
        out["db"] = {k: v for k, v in out["db"].items()
                     if k != "latency_ms"}
    return out


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
        # #1517: db.latency_ms is a wall-clock measurement taken at call time
        # — the two calls land ms apart, so strip it before comparing (the
        # contract is the health shape + ok/error semantics, not the exact ms).
        assert _strip_timing(result) == _strip_timing(tortoise_health())
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
        assert isinstance(result["taxonomy"], dict)
        assert isinstance(result["status"], dict)
        assert result["taxonomy"]["Point"] >= 3
        # #3510 — the four DATA-PROPORTIONAL sections (row per graph row, not
        # per graph-shape token) stay present but are summarised to bounded
        # counts; their full arrays are opt-in behind the matching section=
        # (asserted byte-identical to the legacy tools in TestOverviewSections).
        for sec in ("sources", "tags", "pointkinds", "structure_check"):
            assert isinstance(result[sec], dict), sec
            assert not isinstance(result[sec], list), sec

    def test_default_matches_individual_sections(self, sdk, mcp_sdk):
        """Combined summary values equal the single-section calls."""
        from tortoise.mcp_server import tortoise_overview
        _seed_graph(sdk)
        combined = tortoise_overview()

        def _stable(d):
            # uptime + db.latency_ms are time-varying (increase/measured
            # between calls) — strip them so the comparison is deterministic;
            # everything else is stable. #1517: latency_ms is a wall-clock
            # probe at call time, so byte-equality on it flakes under load.
            if isinstance(d, dict):
                d = {k: v for k, v in d.items()
                     if k not in ("uptime", "latency_ms")}
                if isinstance(d.get("db"), dict):
                    d["db"] = {k: v for k, v in d["db"].items()
                                if k != "latency_ms"}
            return d

        # Byte-equality holds for every section whose size is bounded by the
        # graph's SHAPE. The DATA-PROPORTIONAL sections are deliberately NOT
        # in the loop: their explicit-section call IS the unbounded rows that
        # #3510 removes from the no-arg payload, so asserting equality with it
        # would re-baseline the very shape being fixed. Their summaries are
        # asserted instead, and the rows stay reachable via section=.
        for sec in ("taxonomy", "structure", "namespaces", "graphs",
                    "health", "status", "stale"):
            assert _stable(combined[sec]) == _stable(tortoise_overview(section=sec)), sec
        # `sources` — see TestOverviewSourcesSummary.
        assert combined["sources"] == {"total": 1, "with_points": 1,
                                       "by_kind": {"document": 1}}
        assert len(tortoise_overview(section="sources")) == 1
        # `tags`, `pointkinds`, `structure_check` — see
        # TestOverviewBoundedSections.
        assert combined["tags"] == {"total": 2,
                                    "by_name": {"t1": 1, "t2": 1}}
        # `pointkinds` folds in POINTS (magnitude), so `total` is the summed
        # Point count, not the number of kinds in the section row list.
        assert combined["pointkinds"]["total"] == sum(
            r["count"] for r in tortoise_overview(section="pointkinds"))
        assert combined["structure_check"]["total"] == \
            len(tortoise_overview(section="structure_check"))


class TestOverviewSourcesSummary:
    """#3510 — the no-arg default must not volunteer the unbounded rows."""

    def test_default_sources_is_a_counts_summary_not_the_rows(
            self, sdk, mcp_sdk):
        from tortoise.mcp_server import tortoise_overview
        _seed_graph(sdk)
        summary = tortoise_overview()["sources"]
        assert isinstance(summary, dict), "the raw rows array is unbounded"
        assert set(summary) == {"total", "with_points", "by_kind"}
        assert summary["total"] == 1
        assert summary["with_points"] == 1
        assert summary["by_kind"] == {"document": 1}
        # The bound that matters is the PAYLOAD, not the field names: the
        # summary is counts, so its size does not track the registry size
        # (asserted at 1,000 rows in the next test).
        assert len(json.dumps(summary)) < 200

    def test_default_sources_payload_does_not_grow_with_the_registry(
            self, sdk, mcp_sdk, monkeypatch):
        """Boundedness, not just presence: 100x the sources, ~same bytes.

        Before #3510 the no-arg payload carried every row (~117 bytes each,
        measured), so this difference was ~115 kB and the assertion failed.
        """
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_overview

        def _rows_for(n):
            def _rows():
                return [{"url": f"https://example.com/{i}",
                         "sourceKind": "github_pr" if i % 2 else "document",
                         "points": 1 if i < 5 else 0}
                        for i in range(n)]
            return _rows

        sizes: dict[int, int] = {}
        for n in (10, 1000):
            monkeypatch.setattr(mcp_mod, "tortoise_list_sources",
                                _rows_for(n))
            default = tortoise_overview()
            sizes[n] = len(json.dumps(default["sources"]))
            assert default["sources"]["total"] == n
            assert default["sources"]["with_points"] == 5
            # the explicit opt-in is UNCHANGED — all rows, still unbounded
            assert len(tortoise_overview(section="sources")) == n
        assert sizes[10] < 200, sizes
        assert sizes[1000] - sizes[10] < 64, sizes

    def test_sources_summary_folds_a_huge_kind_vocabulary(
            self, sdk, mcp_sdk, monkeypatch):
        """`by_kind` is bounded too — a caller can register any kind string."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import _OVERVIEW_SUMMARY_TOP, tortoise_overview
        monkeypatch.setattr(mcp_mod, "tortoise_list_sources", lambda: [
            {"url": f"https://example.com/{i}", "sourceKind": f"kind-{i}",
             "points": 0} for i in range(500)])
        summary = tortoise_overview()["sources"]
        assert summary["total"] == 500
        # #3510 P2: the remainder is a SIBLING field, never a key in the map
        assert len(summary["by_kind"]) == _OVERVIEW_SUMMARY_TOP
        assert summary["other"] == 500 - _OVERVIEW_SUMMARY_TOP
        assert sum(summary["by_kind"].values()) + summary["other"] == 500
        assert len(json.dumps(summary)) < 900

    def test_sources_summary_passes_through_an_error_envelope(
            self, sdk, mcp_sdk, monkeypatch):
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_overview
        monkeypatch.setattr(mcp_mod, "tortoise_list_sources",
                            lambda: {"error": "boom"})
        assert tortoise_overview()["sources"] == {"error": "boom"}


class TestOverviewBoundedSections:
    """#3510 — EVERY data-proportional section is bounded, not just sources.

    A section is data-proportional when it yields one row per graph row rather
    than per graph-shape token (a fixed vocabulary). The four are `sources`,
    `tags`, `pointkinds` and `structure_check`; each folds to counts here and
    keeps its full array behind its own section= (parity with the legacy tool
    is asserted in TestOverviewSections).
    """

    # (section, mcp_server tool it delegates to, row group field, summary key)
    _BOUNDED = (
        ("tags", "tortoise_list_tags", "name", "by_name", "count"),
        ("pointkinds", "tortoise_list_pointkinds", "kind", "by_kind",
         "count"),
    )

    @pytest.mark.parametrize("section,tool,group_field,out_field,count_field",
                             _BOUNDED)
    def test_payload_does_not_grow_with_the_row_count(
            self, sdk, mcp_sdk, monkeypatch, section, tool, group_field,
            out_field, count_field):
        """10 → 50,000 rows: the summary stays O(top-N), not O(rows).

        Before #3510 the no-arg payload carried every row, so the difference
        between 10 and 50,000 rows was hundreds of kB and this failed.
        """
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import _OVERVIEW_SUMMARY_TOP, tortoise_overview

        def _rows_for(n):
            def _rows():
                return [{group_field: f"{group_field}-{i:06d}",
                         count_field: 1, "pack": ""} for i in range(n)]
            return _rows

        sizes: dict[int, int] = {}
        for n in (10, 1_000, 50_000):
            monkeypatch.setattr(mcp_mod, tool, _rows_for(n))
            default = tortoise_overview()
            summary = default[section]
            sizes[n] = len(json.dumps(summary))
            # each row's magnitude is 1 here, so the magnitude-unit total is n
            assert summary["total"] == n
            # a magnitude unit carries no row-count field beside the sum
            assert "with_points" not in summary
            # the fold is a count in the section's own unit (a partition),
            # never a list of rows
            assert isinstance(summary[out_field], dict)
            assert all(isinstance(v, int) for v in summary[out_field].values())
            # the group map is capped at top-N whatever n is; the remainder is
            # the sibling `other`, in the SAME unit
            if n > _OVERVIEW_SUMMARY_TOP:
                assert len(summary[out_field]) == _OVERVIEW_SUMMARY_TOP
                assert summary["other"] == n - _OVERVIEW_SUMMARY_TOP
            else:
                assert len(summary[out_field]) == n
                assert "other" not in summary
            assert sum(summary[out_field].values()) + summary.get("other", 0) == n
            # the explicit opt-in is UNCHANGED — all rows, still unbounded
            assert len(tortoise_overview(section=section)) == n
        # 50,000 rows is still ONE top-N fold, not 50,000 rows: the whole
        # summary is a couple hundred bytes, and past the top-N only the
        # digits of total/with_points/other move (4 bytes here).
        assert sizes[50_000] < 512, sizes
        assert sizes[50_000] - sizes[1_000] < 64, sizes

    @pytest.mark.parametrize("section,tool,group_field,out_field,count_field",
                             _BOUNDED)
    def test_fold_is_bounded_when_the_group_vocabulary_is_unbounded(
            self, sdk, mcp_sdk, monkeypatch, section, tool, group_field,
            out_field, count_field):
        """A caller can invent any tag name / kind string, so the group key is
        unbounded too — the top-N fold is what bounds it."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import _OVERVIEW_SUMMARY_TOP, tortoise_overview
        monkeypatch.setattr(mcp_mod, tool, lambda: [
            {group_field: f"{group_field}-{i}", count_field: 1, "pack": ""}
            for i in range(500)])
        summary = tortoise_overview()[section]
        assert summary["total"] == 500
        assert len(summary[out_field]) == _OVERVIEW_SUMMARY_TOP
        assert summary["other"] == 500 - _OVERVIEW_SUMMARY_TOP
        assert sum(summary[out_field].values()) + summary["other"] == 500

    def test_tags_rank_the_top_n_by_usage(self, sdk, mcp_sdk, monkeypatch):
        """A tag name is its own group, so the fold must rank by the tag's
        point count — an alphabetical sample would be useless orientation."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import _OVERVIEW_SUMMARY_TOP, tortoise_overview
        rows = [{"name": f"popular-{i:02d}", "count": 100 - i}
                for i in range(_OVERVIEW_SUMMARY_TOP)]
        rows += [{"name": f"rare-{i}", "count": 1} for i in range(30)]
        monkeypatch.setattr(mcp_mod, "tortoise_list_tags", lambda: rows)
        summary = tortoise_overview()["tags"]
        # magnitude unit: each popular tag keeps its own count (100..81), and
        # `total` sums those magnitudes — it is not a count of tag names
        assert summary["total"] == sum(range(81, 101)) + 30
        assert "rare-0" not in summary["by_name"]
        assert summary["by_name"]["popular-00"] == 100
        assert summary["other"] == 30
        assert sum(summary["by_name"].values()) + summary["other"] == \
            summary["total"]

    def test_tags_summary_reports_the_tag_magnitude_not_its_row_count(
            self, sdk, mcp_sdk, monkeypatch):
        """#3510 P1: a group is ONE tag row, so a row count would be a literal
        1 for every group. The fold must report the tag's count of tagged
        Points and keep `total` in that same unit."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_overview
        monkeypatch.setattr(mcp_mod, "tortoise_list_tags", lambda: [
            {"name": "hot", "count": 50}, {"name": "cold", "count": 30}])
        summary = tortoise_overview()["tags"]
        assert summary["by_name"] == {"hot": 50, "cold": 30}
        assert summary["total"] == 80
        assert sum(summary["by_name"].values()) == summary["total"]

    def test_pointkinds_summary_reports_point_counts_not_row_counts(
            self, sdk, mcp_sdk, monkeypatch):
        """#3510 P1: `pointkinds` rows are one per kind, so a row count is 1 for
        every group; the summary must carry each kind's Point count."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_overview
        monkeypatch.setattr(mcp_mod, "tortoise_list_pointkinds", lambda: [
            {"kind": "statement", "count": 1234, "pack": ""},
            {"kind": "decision", "count": 7, "pack": ""}])
        summary = tortoise_overview()["pointkinds"]
        assert summary["by_kind"] == {"statement": 1234, "decision": 7}
        assert summary["total"] == 1241
        assert sum(summary["by_kind"].values()) == summary["total"]

    def test_overview_tag_magnitude_agrees_with_list_tags(self, sdk, mcp_sdk):
        """On a live graph, overview()["tags"] must AGREE with list_tags()' own
        magnitude — not report 1 per tag."""
        from tortoise.mcp_server import tortoise_list_tags, tortoise_overview
        for i in range(5):
            sdk.create_point("statement", f"tagged {i}", tags=["hot"])
        sdk.create_point("statement", "untagged")
        legacy = {r["name"]: r["count"] for r in tortoise_list_tags()}
        summary = tortoise_overview()["tags"]
        assert legacy["hot"] == 5
        assert summary["by_name"]["hot"] == legacy["hot"]
        assert summary["total"] == sum(legacy.values())

    def test_real_group_named_other_does_not_collide_with_the_remainder(
            self, sdk, mcp_sdk, monkeypatch):
        """#3510 P2: `sourceKind` is free-form, so a real kind named "other"
        must stay a group of its own, never merged with the folded remainder."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import (
            _OVERVIEW_SUMMARY_OTHER,
            _OVERVIEW_SUMMARY_TOP,
            tortoise_overview,
        )
        rows = [{"url": f"https://example.com/o{i}", "sourceKind": "other",
                 "points": 0} for i in range(3)]
        distinct = _OVERVIEW_SUMMARY_TOP + 5
        rows += [{"url": f"https://example.com/{i}", "sourceKind": f"k{i}",
                  "points": 0} for i in range(distinct)]
        monkeypatch.setattr(mcp_mod, "tortoise_list_sources", lambda: rows)
        summary = tortoise_overview()["sources"]
        # the 3 real rows are their own group — NOT inflated by the remainder
        assert summary["by_kind"]["other"] == 3
        # ranking keeps "other" (3 rows) plus 19 one-row kinds; the rest fold
        assert summary[_OVERVIEW_SUMMARY_OTHER] == \
            distinct - (_OVERVIEW_SUMMARY_TOP - 1)
        assert summary["total"] == len(rows)
        assert sum(summary["by_kind"].values()) + summary["other"] == \
            summary["total"]

    def test_structure_check_summary_is_counts_by_rule(
            self, sdk, mcp_sdk, monkeypatch):
        """One violation per broken Point — the no-arg summary reports
        {total, by_rule} and the rows stay behind section='structure_check'."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_overview
        rows = ([{"type": "orphaned_draft", "id": f"p{i}", "message": "m"}
                 for i in range(400)]
                + [{"type": "orphan_use_case", "id": "u1", "message": "m"}])
        monkeypatch.setattr(mcp_mod, "tortoise_check_structure", lambda: rows)
        summary = tortoise_overview()["structure_check"]
        assert set(summary) == {"total", "by_rule"}
        assert isinstance(summary["by_rule"], dict)  # a count, never a list
        assert summary["total"] == 401
        assert summary["by_rule"] == {"orphaned_draft": 400,
                                      "orphan_use_case": 1}
        assert sum(summary["by_rule"].values()) == 401
        assert len(tortoise_overview(section="structure_check")) == 401
        assert len(json.dumps(summary)) < 200

    @pytest.mark.parametrize("section,tool", [
        ("tags", "tortoise_list_tags"),
        ("pointkinds", "tortoise_list_pointkinds"),
        ("structure_check", "tortoise_check_structure"),
    ])
    def test_summary_passes_through_an_error_envelope(
            self, sdk, mcp_sdk, monkeypatch, section, tool):
        import tortoise.mcp_server as mcp_mod
        from tortoise.mcp_server import tortoise_overview
        monkeypatch.setattr(mcp_mod, tool, lambda: {"error": "boom"})
        assert tortoise_overview()[section] == {"error": "boom"}


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
        # `tortoise_get` retires into `tortoise_get_entity` (owner decision,
        # docs/product/canonical-mcp-tools.md @ approval_pr 4120), so the live
        # W3 fetch tool is `get_entity`.
        assert "tortoise_get_entity" in names
        for t in TOOL_REGISTRY:
            if t.name in ("tortoise_overview", "tortoise_get_entity"):
                assert t.http_policy is True
                assert t.annotations.readOnlyHint is True

    def test_handlers_exist_in_mcp_server_globals(self):
        """FastMCPAdapter registers registry entries by name in globals —
        missing handlers are skipped with a warning (silent surface loss)."""
        import tortoise.mcp_server as mcp_mod
        from tortoise.tool_registry import TOOL_REGISTRY
        for t in TOOL_REGISTRY:
            if t.name in ("tortoise_overview", "tortoise_get_entity"):
                assert t.name in mcp_mod.__dict__, t.name
                assert callable(mcp_mod.__dict__[t.name])

    def test_group_assignments(self):
        from tortoise.tool_registry import GROUP_BY_NAME
        assert GROUP_BY_NAME["tortoise_overview"] == "reasoning"
        assert GROUP_BY_NAME["tortoise_get_entity"] == "graph"

    def test_http_allowed_derived(self):
        from tortoise.tool_registry import get_http_allowed
        allowed = get_http_allowed()
        assert "tortoise_overview" in allowed
        assert "tortoise_get_entity" in allowed
