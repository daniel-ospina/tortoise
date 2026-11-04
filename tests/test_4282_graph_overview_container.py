"""#4282 — `graph_overview`, the first container of the 27-tool surface.

The approved target (`docs/product/beta-sdk-surface.md` row 9) has ONE orientation
tool: *"What is in this memory graph and how it is shaped. Returns a named-section
envelope, not a flat dict."* `docs/product/canonical-mcp-tools.md` row 6 names what it
absorbs, and `tools/bridge_table.py`'s authored Phase-0.1 `DESTINATION` map says where
each absorbed name lands. This file is the gate on the container's own four properties.

WHAT THIS IS THE GATE ON

  1. **The container is advertised and its absorbed siblings are not.** The six names
     that retired in this batch leave `TOOL_REGISTRY` — a caller reaching them lands on
     the warning shim, which is #3883's contract, not a name vanishing.
  2. **Every pointer RESOLVES in one hop.** A retired name's replacement names the
     container and a section the container actually dispatches. Aliases are not
     transitive (GitHub's own `DeprecatedToolAliases` says so, and `RETIRED_USE_INSTEAD`
     is resolved one level deep), so a pointer at another retired name would dead-end —
     `tortoise_health` → `tortoise_overview` would have become exactly that when
     `tortoise_overview` retired. The seven pre-existing pointers were re-pointed for
     this reason and are asserted here too.
  3. **Each section routes to the door it absorbed** — asserted by substitution, so a
     regression names the arm that broke rather than "dispatch is wrong". Nothing is
     re-implemented: the narrow doors are still the readers.
  4. **The default summary did not move.** `tortoise_graph_overview()` with no `section`
     returns exactly the envelope `tortoise_overview()` returned before this change —
     the five argument-taking or newly-absorbed sections stay out of it. A container
     that grew its default payload would be a behaviour change nobody asked for.
  5. **A section that needs an argument refuses by name** rather than calling its door
     with `None` and letting the failure surface as an unrelated error.

NO DATABASE: these are routing tests, and routing is what a container changes. The
doors' own behaviour is covered by their own suites, which this change does not touch.
"""
from __future__ import annotations

import pytest

from tortoise import mcp_server as ms
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

CONTAINER = "tortoise_graph_overview"

#: The names this batch retired INTO the container — `canonical-mcp-tools.md` row 6,
#: filtered through the authored `DESTINATION` map (which sends `issue_insight` to
#: `search_knowledge` and the three `list_*` names to other destinations, so they are
#: NOT in this set).
RETIRED_INTO_CONTAINER = {
    "tortoise_overview",
    "tortoise_summarize_structure",
    "tortoise_validate_domain",
    "tortoise_audit",
    "tortoise_dream_health",
}

#: Absorbed, but its DOOR stays live this batch: `check_structure` is the one member
#: with a WRAP envelope (`-> list[dict]`), so it carries the #8482 failure repair that a
#: `-> Any` container cannot. The section and the door answer the same rows; retiring the
#: door before the container has a declared envelope would move the repair off the
#: advertised path. Pinned as a fact so the next lane cannot "tidy" it away silently.
SECTION_ONLY_MEMBERS = {"tortoise_check_structure": "structure_check"}

#: Pointers that predate this batch and had to be RE-POINTED, because they named the
#: now-retired `tortoise_overview` and a two-hop pointer resolves nowhere.
REPOINTED = {
    "tortoise_list_pointkinds": "pointkinds",
    "tortoise_list_tags": "tags",
    "tortoise_list_sources": "sources",
    "tortoise_taxonomy": "taxonomy",
    "tortoise_health": "health",
    "tortoise_status": "status",
    "tortoise_stale": "stale",
}


class TestDeclaration:
    def test_the_container_is_on_the_advertised_surface(self):
        assert CONTAINER in {t.name for t in TOOL_REGISTRY}

    def test_every_absorbed_name_is_retired_not_deleted(self):
        live = {t.name for t in TOOL_REGISTRY}
        retired = {t.name for t in RETIRED_TOOL_REGISTRY}
        for name in RETIRED_INTO_CONTAINER:
            assert name not in live, f"{name} is still advertised"
            assert name in retired, f"{name} is gone instead of retired (#3883)"

    def test_the_container_keeps_its_handler(self):
        """A custom-handler entry is served from `globals()[name]` in mcp_server."""
        entry = next(t for t in TOOL_REGISTRY if t.name == CONTAINER)
        assert entry.sdk_method == "", "the container dispatches; it binds no SDK method"
        assert callable(getattr(ms, CONTAINER, None))

    def test_container_row_is_a_read(self):
        """Row 9 is in the READ block of the approved table. Reads never mix writes."""
        entry = next(t for t in TOOL_REGISTRY if t.name == CONTAINER)
        assert entry.risk == "read"
        assert entry.writes is False


class TestEveryPointerResolvesInOneHop:
    @pytest.mark.parametrize("name", sorted(RETIRED_INTO_CONTAINER | set(REPOINTED)))
    def test_pointer_names_the_container(self, name):
        replacement = RETIRED_USE_INSTEAD[name]
        assert replacement.startswith(CONTAINER + "("), replacement

    @pytest.mark.parametrize("name", sorted(RETIRED_INTO_CONTAINER | set(REPOINTED)))
    def test_pointer_never_names_another_retired_name(self, name):
        """Aliases do not chain: a replacement that is itself retired is a dead end."""
        replacement = RETIRED_USE_INSTEAD[name]
        target = replacement.split("(", 1)[0]
        assert target not in RETIRED_USE_INSTEAD, (
            f"{name} points at {target}, which is itself retired — two hops resolve nowhere")

    @pytest.mark.parametrize(
        "name,section",
        sorted([(n, s) for n, s in REPOINTED.items()]
               + [(n, None) for n in RETIRED_INTO_CONTAINER]),
    )
    def test_pointer_section_is_one_the_container_dispatches(self, name, section):
        """The named section must be a real member, or the pointer names a call that fails."""
        replacement = RETIRED_USE_INSTEAD[name]
        assert "section=" in replacement
        if 'section="' not in replacement:
            # The bare form `tortoise_graph_overview(section=...)` — the retired container
            # itself, whose replacement is the same envelope with no section pinned. It
            # cannot name a section it does not choose, so the resolution test is that it
            # names the container (above) and that the container accepts the parameter.
            assert replacement == f"{CONTAINER}(section=...)", replacement
            return
        declared = replacement.split('section="', 1)[1].split('"', 1)[0]
        assert declared in ms._OVERVIEW_SECTIONS, (
            f"{name} points at section {declared!r}, which the container does not dispatch")


class TestSectionRouting:
    """Each section reaches the door it absorbed — by substitution, never by re-running it."""

    @pytest.fixture
    def spy(self, monkeypatch):
        calls = []

        def record(label):
            def _fn(*args, **kwargs):
                calls.append((label, args, kwargs))
                return {"door": label}
            return _fn

        for label in ("validate_domain", "audit", "dream_health", "issue_insight",
                      "check_structure", "summarize_structure", "taxonomy", "health",
                      "status", "stale"):
            monkeypatch.setattr(ms, f"tortoise_{label}", record(label), raising=False)
        return calls

    def test_validate_domain_needs_its_domain(self, spy):
        result = ms.tortoise_graph_overview(section="validate_domain", domain="product")
        assert result == {"door": "validate_domain"}
        assert spy == [("validate_domain", ("product",), {})]

    def test_validate_domain_without_a_domain_refuses_by_name(self, spy):
        result = ms.tortoise_graph_overview(section="validate_domain")
        assert "domain" in result["error"]
        assert spy == [], "a refusal must not reach the door with a missing argument"

    def test_audit_forwards_point_kinds(self, spy):
        ms.tortoise_graph_overview(section="audit", point_kinds=["statement"])
        assert spy == [("audit", (), {"point_kinds": ["statement"]})]

    def test_dream_health_reaches_its_door(self, spy):
        ms.tortoise_graph_overview(section="dream_health")
        assert spy == [("dream_health", (), {})]

    def test_structure_and_structure_check_keep_their_doors(self, spy):
        ms.tortoise_graph_overview(section="structure")
        ms.tortoise_graph_overview(section="structure_check")
        assert [c[0] for c in spy] == ["summarize_structure", "check_structure"]

    def test_unknown_section_lists_every_section(self):
        result = ms.tortoise_graph_overview(section="nope")
        assert "unknown section" in result["error"]
        for section in ms._OVERVIEW_SECTIONS:
            assert section in result["error"]


class TestDefaultSummaryIsUnchanged:
    def test_argument_taking_sections_stay_out_of_the_summary(self, monkeypatch):
        seen: list[str] = []

        def _fake_section(section, entity_id, days, limit, **kwargs):
            seen.append(section)
            return {}

        monkeypatch.setattr(ms, "_overview_section", _fake_section)
        ms.tortoise_graph_overview()
        for section in ("topics", "validate_domain", "audit", "dream_health"):
            assert section not in seen, f"{section} entered the default summary"

    def test_the_summary_still_carries_exactly_the_eleven_pre_existing_keys(self, monkeypatch):
        """The pin is a LITERAL, not a derivation.

        Deriving the expectation from `_OVERVIEW_SECTIONS` and `_OVERVIEW_SUMMARY_SKIP`
        would agree with any edit to those two constants — demonstrated: adding `health`
        and `status` to the skip set kept a derived version of this test green while the
        default envelope silently dropped to 9 keys. The eleven names below are what
        `tortoise_overview()` returned before #4282, typed out.
        """
        seen: list[str] = []

        def _fake_section(section, entity_id, days, limit, **kwargs):
            seen.append(section)
            return {"bounded": True}

        monkeypatch.setattr(ms, "_overview_section", _fake_section)
        result = ms.tortoise_graph_overview()
        expected = ["taxonomy", "structure", "structure_check", "pointkinds",
                    "tags", "sources", "namespaces", "graphs", "health",
                    "status", "stale"]
        assert seen == expected
        assert list(result) == expected
        assert ms._OVERVIEW_DEFAULT_SUMMARY_KEYS == tuple(expected)
