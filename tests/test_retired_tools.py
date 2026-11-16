"""#3883 — a retired MCP name must RESOLVE and WARN, never silently disappear.

#3836 option (b): a retired name keeps working and tells the caller it is
retired. These tests pin both halves of that contract on the real declaration:

* the name is OFF the advertised surface (it is a reduction), and
* the name still RESOLVES through the warning shim, and the warning reaches the
  caller in the answer (`content`) and in the result `_meta`.

They also pin the properties that make the retirement SAFE — the handler
function and the SDK method stay, and the shim's structured output is exactly
what the live tool produced.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp.tools.base import ToolResult

from tortoise import mcp_server as ms
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

RETIRED_BY_NAME = {t.name: t for t in RETIRED_TOOL_REGISTRY}


class TestDeclaration:
    def test_retired_names_are_not_in_the_live_registry(self):
        live = {t.name for t in TOOL_REGISTRY}
        retired = set(RETIRED_BY_NAME)
        assert retired == set(RETIRED_USE_INSTEAD)
        assert not (live & retired), f"live and retired overlap: {live & retired}"

    def test_every_retired_entry_names_a_replacement(self):
        for name, spec in RETIRED_BY_NAME.items():
            assert spec.retired_use_instead, f"{name} retired without a replacement"
            assert spec.retired_use_instead.startswith("tortoise_"), spec.retired_use_instead

    def test_retired_handler_functions_still_exist(self):
        """The capability is not lost: the consolidator calls these interns."""
        for spec in RETIRED_TOOL_REGISTRY:
            handler = getattr(ms, spec.name, None)
            assert callable(handler), f"handler {spec.name}() was removed"

    def test_public_sdk_surface_is_unchanged(self):
        """The SDK surface did not GROW: 110 public methods, as pinned today.

        This used to read *"Only MCP tool NAMES are retired — every public SDK method
        stays"*, which #4282 phase 2 made false: `events_poll`/`restore_point_at`
        (2.1) and the memory-graph four (2.3) are retired SDK methods, and phase 2.2
        (#10053) implemented three approved-but-absent names, so the count
        moves by design and this test is the pin on where it lands. A retirement or
        an approved name gaining a `def` is an OWNER-APPROVED row in
        `docs/product/beta-sdk-surface.md`, never this assertion. The 125 → 120 → 118 → 110
        move is #12901 wave 1's five off-surface retirements, then #11609 Phase 2's three
        waves — four retired and three delivered by lane B (119), seven retired and six
        delivered by lane C (118), then fourteen retired and six delivered by this branch's
        READ/QUERY containers (110) — all disjoint, so no wave drops another's moves.
        The 150 → 124 move is
        #4282's owner-ruled deletion of the 26 surplus methods nothing called
        (2026-10-27, PR #10187); the 124 → 125 move is an owner-approved
        **implementation** of an already-approved row — row 28,
        `get_organisation_account`, which `docs/product/beta-sdk-surface.md` records as
        "already **is** the name" — delivered, not added (the approved 40 did not
        grow) — with its manifest row in `config/surface-manifest.yml`. The CANONICAL
        docs say the same fact in their own words: `canonical-sdk-methods.md` calls it
        "an IMPLEMENTATION of an already-approved name, not an addition to the approved
        40", and `canonical-mcp-tools.md` records that "in none of these moves did the
        declared surface change". Note that `beta-sdk-surface.md` says of itself, in its
        own header: "This document is a PROPOSAL, and it is NOT the authority for the
        MCP target" — where it and `canonical-mcp-tools.md` differ, canonical governs.
        #11609 batch 2 then moved it 125 → 124 (SIX approved containers implemented, SEVEN
        live names retired), and the phase rule is that every batch ends BELOW where it
        started, which is what makes this pin move DOWN and never up.
        #12521 (`8e4e335b05`) gave it its `def`, approved by daniel-ospina on 2026-11-14
        and recorded at `#10015` comment `6863608333`; `docs/product/beta-sdk-surface.md`
        read *"40 methods against **125** today"* at that move (it now reads **111**, moved
        by #12901 wave 1 and then by #11609 Phase 2), and
        `config/surface-manifest.yml`'s `counts.sdk_public_methods` was `125`, so this
        assertion was then the one place still pinned to the pre-delivery number.

        THREE moves then took it DOWN, and the two #11609 waves are disjoint (wave 1's
        membership rule excluded any name a `tool_registry` entry bound, live or retired;
        the four doors #11609 Phase 2 re-keys were live MCP tools at that time):

        * #12901 (2026-11-15) 125 → 120 — five OFF-SURFACE methods retired (archived
          under `archive/sdk-methods/b2-sdk-retirement-wave-1/` and recorded in
          `tortoise/sdk_retired.py::REMOVED_SDK_METHODS`). The approved 40 did not move.
        * #11609 Phase 2 (2026-11-15) 120 → 112 — six READ/QUERY containers landed, and the
          fourteen read methods behind them went private (+6 −14). The approved 40 did not
          move.
        * #11609 Phase 2 (2026-11-16) 112 → 111 — FOUR public names retired into
          containers (`supersede` → `supersede_knowledge`, `update_entity` →
          `update_knowledge`, `file_decision` → `write_question`, `file_human_approval` →
          `record_decision`) and THREE added (`supersede_knowledge`, `write_question`,
          `record_decision`; `update_knowledge` already existed), a net -1.

        `high_water_counts.sdk_public_methods` is **111** — this PR (#12951) LOWERED it from
        the 150 it was pinned at on `main`, because a pin that keeps slack stops being a
        growth pin. Every move is the cheap direction, and the pin still refuses any
        UNAPPROVED growth above it.
        Re-derive it from `len(public)` at the head; never type a number ahead of the
        manifest.

        ⛔ This assertion is defence-in-depth, NOT the approval gate. What refuses an
        UNAPPROVED addition is `tools/surface-guard.py` **step 1** — an SDK method or
        tool that is not in the frozen baseline (`surface-guard.py:964`, "is not in the
        frozen baseline") — plus `tests/test_sdk_surface.py` (`DECLARED_COUNT` /
        declaration-vs-baseline). Measured: adding a stray public method to
        `TortoiseSDK` leaves `tests/test_sdk_retirement.py` green (14 passed; it is a
        FLOOR, so growth cannot red it), reds `test_sdk_surface.py` on FOUR rows
        (`test_the_declaration_describes_the_live_class_exactly` plus the three
        `test_the_clean_run_reports_all_three_views` arms) and reds THIS pin too —
        five failures across the two files, not five in `test_sdk_surface.py` alone.

        Two guard layers, and the path decides which you meet: WITHOUT a re-cut a
        stray method is refused by `tools/surface-guard.py` **step 1** ("is not in the
        frozen baseline", rc 1). WITH a re-cut, `cut` writes the new method's own
        baseline row, which GREENS step 1, and CARRIES `high_water_counts` — an EXCEEDS
        check (`live_n > high_water`) that is therefore silent in the band this surface
        sits in: measured, live 150 + re-cut → rc 0, live 151 → rc 1 ("public SDK
        methods live 151 > pin 150"). So the guard pair alone does NOT bound a small
        regenerate-and-push addition.

        Other instruments do red it, and this list is neither exhaustive nor an
        approval: the count is hand-typed in several test registers (`DECLARED_COUNT`,
        this assertion, `test_surface_manifest.py`'s live-vs-baseline check, the typed
        count in `docs/product/canonical-mcp-tools.md`); every rendered artifact
        (`docs/product/sdk-surface-baseline.md`, `mcp-sdk-surface.md`,
        `sdk-surface-declaration.md`, `config/sdk-surface.json`) must be REGENERATED
        with the new row; and `tools/sdk_rename_table.py --check` REFUSES an
        unmapped public method outright — it demands a canonical group and a
        disposition, so the surface cannot grow without someone saying what the method IS.

        Do not read this count as approval.
        """
        from tortoise.sdk import TortoiseSDK

        public = [
            n
            for n in dir(TortoiseSDK)
            if not n.startswith("_") and callable(getattr(TortoiseSDK, n))
        ]
        assert len(public) == 110, f"SDK surface changed: {len(public)} != 110"
        # 125 → 111 across TWO 2026-11 waves, all DOWN — the direction #4282 requires:
        #   #12901 wave 1  retired 5 off-surface methods with no production caller  (−5)
        #   #11609 Phase 2 landed 6 approved READ/QUERY containers (+6) and
        #                  re-privatised the 14 read methods they cover      (−14)
        #   #11609 Phase 2 retired 4 public names into the WRITE/MUTATE containers (−4)
        #                  and added the 3 that absorbed them                (+3)
        # 125 + 6 − 14 − 5 − 4 + 3 = 111. `high_water_counts` in
        # `config/surface-manifest.yml` is re-cut, never hand-edited, and the guard still
        # refuses growth above the baseline.

    def test_retired_sdk_bindings_that_resolve_are_still_public(self):
        from tortoise.sdk import TortoiseSDK

        for spec in RETIRED_TOOL_REGISTRY:
            declared = spec.sdk_method or ""
            if declared and hasattr(TortoiseSDK, declared):
                assert callable(getattr(TortoiseSDK, declared)), declared


class TestAdvertisedSurface:
    def test_retired_names_are_not_advertised(self):
        """Both enumerations the surface-guard reads must exclude retired names."""

        async def _check():
            retired = set(RETIRED_BY_NAME)
            listed = {t.name for t in await ms.mcp._list_tools()}
            advertised = {t.name for t in await ms.mcp.list_tools()}
            assert not (listed & retired), f"components: {listed & retired}"
            assert not (advertised & retired), f"tools/list: {advertised & retired}"
            assert len(listed) == len(TOOL_REGISTRY)

        asyncio.run(_check())

    def test_every_retired_name_resolves_with_a_retirement_marker(self):
        """A retired name must never be a bare 'tool not found' (#3883)."""

        async def _check():
            for spec in RETIRED_TOOL_REGISTRY:
                tool = await ms.mcp.get_tool(spec.name)
                assert tool is not None, f"{spec.name} does not resolve"
                marker = ((tool.meta or {}).get("tortoise") or {}).get("retired") or {}
                assert marker.get("retired") is True, f"{spec.name} has no marker"
                assert marker.get("use_instead") == spec.retired_use_instead
                # The resolved implementation is the SHIM, not the original handler.
                assert (getattr(tool.fn, "__doc__", "") or "").startswith("RETIRED")

        asyncio.run(_check())

    def test_retired_names_have_no_registered_component(self):
        components = {
            getattr(c, "name", None)
            for k, c in ms.mcp._local_provider._components.items()
            if k.startswith("tool:")
        }
        assert not (components & set(RETIRED_BY_NAME))


class TestWarning:
    def test_warning_is_appended_to_content_and_in_meta(self):
        spec = RETIRED_BY_NAME["tortoise_list_tags"]
        base = ToolResult(content={"ok": True}, structured_content={"ok": True})
        out = ms._warn_retired_result(base, spec)
        assert out is not base
        # The warning is the LAST block: the payload stays content[0] and the
        # structured content is byte-identical to the live tool's.
        assert out.content[-1].text == ms._retired_warning(spec)["message"]
        assert out.content[0].text == base.content[0].text
        assert "tortoise_list_tags" in out.content[-1].text
        # The replacement this pointer names is the CONTAINER, not the retired name it used
        # to name: `tortoise_overview` retired into `tortoise_graph_overview` in the same
        # #4282 batch, and a warning that pointed at a retired name would dead-end.
        assert "tortoise_graph_overview" in out.content[-1].text
        assert out.meta["tortoise"]["retired"]["use_instead"] == spec.retired_use_instead
        assert out.structured_content == {"ok": True}

    def test_shim_preserves_the_wrap_envelope_for_list_results(self):
        spec = RETIRED_BY_NAME["tortoise_list_tags"]

        def handler() -> list[dict]:
            return [{"a": 1}]

        shim = ms.build_retired_tools([spec], {spec.name: handler})[spec.name]
        result = asyncio.run(shim.run({}))
        assert result.structured_content == {"result": [{"a": 1}]}
        assert result.content[0].text.startswith("["), "payload must stay content[0]"
        assert result.content[-1].text.startswith("RETIRED TOOL")
        assert len(result.content) > 1, "the warning block is missing"

    def test_shim_preserves_dict_structured_content(self):
        spec = RETIRED_BY_NAME["tortoise_health"]

        def handler() -> dict:
            return {"status": "ok"}

        shim = ms.build_retired_tools([spec], {spec.name: handler})[spec.name]
        result = asyncio.run(shim.run({}))
        assert result.structured_content == {"status": "ok"}
        assert result.content[-1].text.startswith("RETIRED TOOL")
        assert result.content[0].text != result.content[-1].text

    def test_each_shim_calls_its_own_handler(self):
        """Regression: a loop-local `def` once captured the LAST handler, so every
        retired name answered with the last one's payload."""
        s1 = RETIRED_BY_NAME["tortoise_list_tags"]
        s2 = RETIRED_BY_NAME["tortoise_health"]
        calls: list[str] = []

        def h1():
            calls.append("h1")
            return {"who": "one"}

        def h2():
            calls.append("h2")
            return {"who": "two"}

        shims = ms.build_retired_tools([s1, s2], {s1.name: h1, s2.name: h2})
        r1 = asyncio.run(shims[s1.name].run({}))
        r2 = asyncio.run(shims[s2.name].run({}))
        assert calls == ["h1", "h2"]
        assert r1.structured_content == {"who": "one"}
        assert r2.structured_content == {"who": "two"}

    def test_transform_prefers_a_live_tool_over_a_shim(self):
        """If a name is live again, the live tool wins — the shim is a fallback."""
        s1 = RETIRED_BY_NAME["tortoise_list_tags"]

        async def _check():
            live = await ms.mcp.get_tool("tortoise_query")
            marker = ((live.meta or {}).get("tortoise") or {}).get("retired")
            assert marker is None, "a live tool was served as retired"

            transform = ms._RetiredToolTransform({s1.name: "sentinel-shim"})

            async def _none(name, *, version=None):
                return None

            assert await transform.get_tool(s1.name, _none) == "sentinel-shim"
            assert await transform.get_tool("tortoise_query", _none) is None

            kept = [type("T", (), {"name": n}) for n in ("tortoise_query", s1.name)]
            filtered = {t.name for t in await transform.list_tools(kept)}
            assert filtered == {"tortoise_query"}

        asyncio.run(_check())


class TestScopeGate:
    """#4170 x #3883: a retired name still resolves through the scope gate.

    A retired name is still SERVED (through the warning shim), so the gate must
    be able to read its permission. While the lookup covered live tools only, a
    scoped caller reached the gate and was told "Unknown tool - denied" - the
    silent disappearance #3883 exists to prevent, for the keys least able to
    debug it.
    """

    def test_every_retired_name_resolves_for_the_gate(self):
        from tortoise.tool_registry import get_tool_by_name

        by_name = get_tool_by_name()
        missing = {s.name for s in RETIRED_TOOL_REGISTRY} - set(by_name)
        assert not missing, f"retired names unresolvable by the gate: {missing}"

    def test_retired_reader_passes_a_read_scope(self):
        spec = next(s for s in RETIRED_TOOL_REGISTRY if not s.writes)
        token = ms._current_scopes.set(["graphs:read"])
        try:
            ms._enforce_mcp_tool_scope(spec.name)  # must not raise
        finally:
            ms._current_scopes.reset(token)

    def test_retired_name_without_a_data_scope_is_gated_not_unknown(self):
        spec = next(s for s in RETIRED_TOOL_REGISTRY if not s.writes)
        token = ms._current_scopes.set([])
        try:
            with pytest.raises(ms.AuthorizationError) as excinfo:
                ms._enforce_mcp_tool_scope(spec.name)
        finally:
            ms._current_scopes.reset(token)
        assert "Unknown tool" not in str(excinfo.value), (
            "a retired name must be gated by its own permission, not denied as unknown")
