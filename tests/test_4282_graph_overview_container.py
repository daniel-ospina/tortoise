"""#4282 — `graph_overview`, the first container of the 27-tool surface.

The approved target (`docs/product/beta-sdk-surface.md` row 9) has ONE orientation
tool: *"What is in this memory graph and how it is shaped. Returns a named-section
envelope, not a flat dict."* `docs/product/canonical-mcp-tools.md` row 6 names what it
absorbs, and `tools/bridge_table.py`'s authored Phase-0.1 `DESTINATION` map says where
each absorbed name lands. FIVE of row 6's seven names retire into the container; the
other two are accounted for in `SECTION_ONLY_MEMBERS` and in the note below. This file
is the gate on the container's own four properties.

WHAT THIS IS THE GATE ON

  1. **The container is advertised and its absorbed siblings are not.** The five names
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
     the FOUR sections in `_OVERVIEW_SUMMARY_SKIP` stay out of it. A container that grew
     its default payload would be a behaviour change nobody asked for.
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

#: EMPTY since #11609 lane P11 — the reason the one member was here is GONE, and that is
#: the point rather than an omission.
#:
#: `tortoise_check_structure` stayed live while it did because it is the one member with a
#: WRAP envelope (`-> list[dict]`), so it carried the #8482 failure repair that a `-> Any`
#: container could not. #11609 P11 moved that repair onto the CONTAINER BOUNDARY
#: (`_row_section_envelope` in `mcp_server.py`, which ships `{"result": [...]}` for a list
#: arm and leaves a mapping untouched), which is exactly the condition this set existed to
#: wait for — so the door retires FORWARD into `graph_overview(section="structure_check")`
#: and the set is empty. ⛔ Re-adding a row here re-opens "the container has no envelope",
#: which is no longer true; the repair is pinned per arm by
#: `tests/test_11609_read_containers.py::TestTheContainersCarryTheEnvelope` and by
#: `tests/test_11609_structural_blockers.py::test_the_retired_door_still_declares_the_array_envelope`.
SECTION_ONLY_MEMBERS: dict[str, str] = {}

#: The member the set used to hold, kept so the transition itself is asserted rather than
#: described: it must be RETIRED (not deleted), its pointer must name the section, and the
#: container must now carry the envelope the door used to own.
RETIRED_ONCE_THE_CONTAINER_CARRIED_THE_ENVELOPE = {
    "tortoise_check_structure": "structure_check",
}

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


#: Every door `_overview_section` dispatches, in `tortoise_<name>` form. Listed here so the
#: dispatch can be MEASURED (by substitution) instead of read out of a declared tuple — see
#: `_dispatched_sections`. `raising=True` on the patches is deliberate: a renamed door must
#: fail this file loudly, not be created on the spot by a permissive spy.
OVERVIEW_DOORS = (
    "taxonomy", "summarize_structure", "check_structure", "list_pointkinds",
    "list_tags", "list_sources", "list_namespaces", "list_graphs", "list_topics",
    "health", "status", "stale", "validate_domain", "audit", "dream_health",
)


def _dispatched_sections(monkeypatch, candidates) -> set[str]:
    """The sections `_overview_section` ACTUALLY dispatches — measured, not declared.

    `_OVERVIEW_SECTIONS` is the tuple the ERROR MESSAGE iterates; the dispatch is the
    `if section == ...` chain in `tortoise/mcp_server.py`. The two can disagree, and when
    they do a section is advertised by the error text and dead-ends in the `unknown
    section` `_SafeError` — which is why asserting membership in the tuple is an assertion
    about the advertisement, not about the call (#10914 review P3).

    Each candidate is called once with EVERY door substituted and a full argument set, so a
    routed section reaches exactly one door and a section with no arm reaches none. NO
    DATABASE: the doors are the only thing that would touch one.
    """
    reached: list[str] = []

    def record(label):
        def _fn(*args, **kwargs):
            reached.append(label)
            return {"door": label}
        return _fn

    for door in OVERVIEW_DOORS:
        monkeypatch.setattr(ms, f"tortoise_{door}", record(door))

    dispatched: set[str] = set()
    for section in candidates:
        before = len(reached)
        result = ms._overview_section(
            section, entity_id="point-1", days=30, limit=50,
            domain="product", point_kinds=["statement"],
        )
        if len(reached) > before:
            dispatched.add(section)
            continue
        assert isinstance(result, dict) and "unknown section" in str(result.get("error")), (
            f"{section!r} reached no door and did not refuse by name either: {result!r} — "
            "an arm that neither dispatches nor refuses is a silent hole")
    return dispatched


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


class TestSectionOnlyMembers:
    """The set is EMPTY, and the member that left it left for the right reason.

    `tortoise_check_structure` was the one member absorbed as a SECTION whose door stayed
    LIVE: it carries the #8482 wrap-envelope repair (`-> list[dict]`) and the container
    returned `Any`, so retiring the door first would have moved the repair off the
    advertised path — silently, because the shim keeps answering.

    #11609 lane P11 satisfied that condition instead of bypassing it: every container now
    runs its result through `_row_section_envelope`, so the container builds the same
    `{"result": [...]}` envelope by hand and the door retires FORWARD. These tests assert
    the transition in both directions — the door is retired with a one-hop pointer, and the
    repair now lives at the container boundary, on the wire — so a later "tidy" that
    re-adds the door, or drops the boundary wrap, is loud.
    """

    def test_the_set_is_empty_now_that_the_container_carries_the_envelope(self):
        assert SECTION_ONLY_MEMBERS == {}, (
            "SECTION_ONLY_MEMBERS is non-empty: a door has been put back in the "
            "stay-live pen after #11609 P11 gave the containers the boundary envelope. "
            "If a NEW section-only member exists, say why in the comment — otherwise "
            "the row is re-opening a gap that no longer exists")

    @pytest.mark.parametrize(
        "door,section",
        sorted(RETIRED_ONCE_THE_CONTAINER_CARRIED_THE_ENVELOPE.items()))
    def test_the_former_member_retired_forward_not_deleted(self, door, section):
        live = {t.name for t in TOOL_REGISTRY}
        assert door not in live, (
            f"{door} is still advertised — #11609 P11 retired it forward once the "
            "container carried the envelope at the boundary")
        assert door not in {t.name for t in TOOL_REGISTRY}
        assert RETIRED_USE_INSTEAD[door] == (
            f'{CONTAINER}(section="{section}")'), (
            f"{door} is pointed at {RETIRED_USE_INSTEAD[door]!r}, which is not the "
            "one-hop section pointer a migrating caller needs")

    @pytest.mark.parametrize(
        "door,section",
        sorted(RETIRED_ONCE_THE_CONTAINER_CARRIED_THE_ENVELOPE.items()))
    def test_the_section_still_reaches_it(self, door, section, monkeypatch):
        called = []
        monkeypatch.setattr(ms, f"tortoise_{door.removeprefix('tortoise_')}",
                            lambda *a, **k: called.append((a, k)) or {"ok": True})
        ms.tortoise_graph_overview(section=section)
        assert called, f"section {section!r} no longer dispatches to {door}"

    @pytest.mark.parametrize(
        "door,section",
        sorted(RETIRED_ONCE_THE_CONTAINER_CARRIED_THE_ENVELOPE.items()))
    def test_the_envelope_repair_now_lives_at_the_container_boundary(
            self, door, section, monkeypatch):
        """The property the door was kept alive for, measured on the container.

        This is the assertion that makes the retirement safe: the retired door's own
        envelope was `{"result": [...]}`, and the container must now ship exactly that
        for a LIST section arm — not a bare text block (`#10085` measured
        `{"text": "[...]"}` with no `structuredContent`).
        """
        monkeypatch.setattr(
            ms, f"tortoise_{door.removeprefix('tortoise_')}",
            lambda *a, **k: [{"id": "p1"}])
        out = ms.tortoise_graph_overview(section=section)
        assert getattr(out, "structured_content", None) == {"result": [{"id": "p1"}]}, (
            f"section {section!r} no longer ships the envelope the retired door {door} "
            f"carried on the wire: {out!r}")
        assert (getattr(out, "meta", None) or {}).get("fastmcp") == {"wrap_result": True}, (
            "the boundary wrap must advertise itself the same way FastMCP's wrap "
            "annotation does, or a client that unwraps on `meta` sees a different shape")


class TestArgumentForwarding:
    """The container must FORWARD `days`/`limit`/`entity_id`, not re-default them.

    Nothing covered the container's own pass-through before: the sibling suite exercises
    `tortoise_overview`, so a swapped `days`/`limit` in the container's delegate would have
    red nothing.
    """

    @pytest.fixture
    def door(self, monkeypatch):
        seen = {}

        def _fake(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return {"ok": True}

        monkeypatch.setattr(ms, "tortoise_stale", _fake)
        monkeypatch.setattr(ms, "tortoise_list_topics", _fake)
        return seen

    def test_days_and_limit_reach_the_stale_door(self, door):
        ms.tortoise_graph_overview(section="stale", days=7, limit=9)
        assert door["kwargs"] == {"days": 7, "limit": 9}, door

    def test_entity_id_reaches_the_topics_door(self, door):
        ms.tortoise_graph_overview(section="topics", entity_id="point-42")
        assert door["args"] == ("point-42",), door

    def test_topics_without_an_entity_id_refuses_before_the_door(self, door):
        result = ms.tortoise_graph_overview(section="topics")
        assert "entity_id" in result["error"]
        assert door == {}, "a refusal must not reach the door"


class TestEveryPointerResolvesInOneHop:
    @pytest.mark.parametrize("name", sorted(RETIRED_INTO_CONTAINER | set(REPOINTED)))
    def test_pointer_names_the_container(self, name):
        replacement = RETIRED_USE_INSTEAD[name]
        assert replacement.startswith(CONTAINER + "("), replacement

    @pytest.mark.parametrize("name", sorted(RETIRED_USE_INSTEAD))
    def test_pointer_never_names_another_retired_name(self, name):
        """Aliases do not chain: a replacement that is itself retired is a dead end.

        Parameterized over the WHOLE `RETIRED_USE_INSTEAD` map, not over this batch's
        member set. The narrower parametrization is what let #12948 ship two dead ends:
        `tortoise_index_sessions` and `tortoise_ingest_corpus` were pointed at
        `tortoise_index_files`, which the SAME batch retired a few lines below them, and
        neither name was a member of this file's batch sets (`#12948` review F1). A
        one-hop rule asserted only over the members a lane happened to add is a rule that
        cannot see the pointer it breaks.
        """
        replacement = RETIRED_USE_INSTEAD[name]
        target = replacement.split("(", 1)[0]
        assert target not in RETIRED_USE_INSTEAD, (
            f"{name} points at {target}, which is itself retired — two hops resolve nowhere")

    @pytest.mark.parametrize("entry", RETIRED_TOOL_REGISTRY, ids=lambda t: t.name)
    def test_no_retired_description_offers_a_retired_destination(self, entry):
        """A retired entry's own `description` reaches the caller — so it cannot dead-end.

        `tortoise/mcp_server.py` answers a retired call with
        ``RETIRED — use {pointer}. {description}``, so the description is a POINTER, not
        prose: `tortoise_index_sessions`'s still opened with "DEPRECATED — use
        tortoise_index_files." — the retired name — directly after the shim had named the
        live container (`#12948` review F1). A prefix naming a name the same message has
        just retired sends the caller into a second hop that resolves nowhere.

        Scoped to the "use <name>" DIRECTIVE form on purpose: a live description that says
        it "absorbs" or "replaces" a retired name is correct and must keep doing so
        (`tortoise_index_sources_from_directory` names all three it absorbed).
        """
        for retired_name in RETIRED_USE_INSTEAD:
            assert f"use {retired_name}" not in entry.description, (
                f"{entry.name}'s description directs the caller to {retired_name}, "
                "which is itself retired — the message dead-ends one hop in")

    @pytest.mark.parametrize(
        "name,section",
        sorted([(n, s) for n, s in REPOINTED.items()]
               + [(n, None) for n in RETIRED_INTO_CONTAINER]),
    )
    def test_pointer_section_is_one_the_container_dispatches(self, name, section, monkeypatch):
        """The named section must be DISPATCHED, not merely listed.

        `_OVERVIEW_SECTIONS` is what the error message advertises; the dispatch is the
        `if section ==` chain. A tuple-membership assertion therefore stayed green for a
        section the container never routes, while the pointer dead-ended in `unknown
        section` — the assertion was weaker than the property its name states (#10914
        review P3). This measures the dispatch by substitution instead.
        """
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
        dispatched = _dispatched_sections(monkeypatch, [declared])
        assert declared in dispatched, (
            f"{name} points at section {declared!r}, which the container does not dispatch "
            f"(measured, not read off `_OVERVIEW_SECTIONS`)")

    def test_every_advertised_section_is_actually_dispatched(self, monkeypatch):
        """`_OVERVIEW_SECTIONS` ⊆ dispatched: the tuple and the chain cannot drift.

        The tuple is the ADVERTISEMENT (the error text and the default-summary loop); the
        chain is the DISPATCH. A member added to one and not the other is a section the
        container tells callers to use and then refuses.
        """
        advertised = list(ms._OVERVIEW_SECTIONS)
        dispatched = _dispatched_sections(monkeypatch, advertised)
        assert set(advertised) - dispatched == set(), (
            "advertised by `_OVERVIEW_SECTIONS` but no `if section ==` arm dispatches "
            f"them: {sorted(set(advertised) - dispatched)}")
        assert "definitely_not_a_section" not in dispatched, (
            "the probe routed a name that has no arm — the measurement is not measuring")


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

        # ONLY the doors this file asserts. `issue_insight` is not in this batch at all,
        # and patching a name with `raising=False` silently CREATES it if it ever goes
        # away — a spy that cannot fail is not coverage.
        for label in ("validate_domain", "audit", "dream_health",
                      "check_structure", "summarize_structure"):
            monkeypatch.setattr(ms, f"tortoise_{label}", record(label))
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

    def test_unknown_section_lists_every_section(self, monkeypatch):
        # The message is BUILT from `_OVERVIEW_SECTIONS`, so asserting "every listed name
        # appears in the message" compared that constant with itself and could never
        # disagree with it (#10914 review P3). Compare it with what the chain DISPATCHES.
        advertised = list(ms._OVERVIEW_SECTIONS)
        dispatched = _dispatched_sections(monkeypatch, advertised)
        result = ms.tortoise_graph_overview(section="nope")
        assert "unknown section" in result["error"]
        listed = {
            part.strip()
            for part in result["error"].split("Valid sections:", 1)[1].split(",")
            if part.strip()
        }
        assert listed == dispatched, (
            "the error message advertises sections the dispatch chain does not reach: "
            f"{sorted(listed - dispatched)}")


class TestDefaultSummaryIsUnchanged:
    def test_argument_taking_sections_stay_out_of_the_summary(self, monkeypatch):
        seen: list[str] = []

        def _fake_section(section, entity_id, days, limit, **kwargs):
            seen.append(section)
            return {}

        monkeypatch.setattr(ms, "_overview_section", _fake_section)
        ms.tortoise_graph_overview()
        assert len(ms._OVERVIEW_SUMMARY_SKIP) == 4, (
            "the skip set is FOUR sections; a fifth means one was added without this "
            "pin moving, and the default envelope is not 11 keys any more")
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
        assert tuple(expected) == ms._OVERVIEW_DEFAULT_SUMMARY_KEYS
