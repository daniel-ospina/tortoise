"""#4122 — the registry's single `risk` axis and the hints/permissions derived from it.

Owner ruling (2026-10-21): a tool is classified by the caller's PERCEIVED RISK,
not by whether its handler happens to write. `ToolDefinition` therefore carries
ONE declaration, `risk`, and everything else is derived::

    risk: read | maintenance | write | destructive | admin
       ├─ writes  (needs graphs:write)  ← write | destructive | admin
       ├─ readOnlyHint                  ← read
       └─ idempotentHint                ← maintenance (or an explicit `idempotent`)

These tests exist so the derivation cannot rot back into two independent fields:

* the hints a client SEES must agree with the tier, for every served entry;
* the permission the scope gate READS must agree with the tier;
* the three read-through tools (`get_confidence`, `compute_confidence`,
  `packs_list`) must stay callable with `graphs:read` AND stop claiming
  `readOnlyHint: true` — the hint is what clients auto-approve on, and a wrong
  one is the actual defect the issue named.

Each property is asserted twice: over the real served registry (so a drifted
entry fails), and against a deliberately-broken entry (so the assertion is not
a tautology that would keep passing if the property were deleted).
"""
from __future__ import annotations

import dataclasses

import pytest

from tortoise.tool_registry import (
    MAINTENANCE_TIER,
    RISK_TIERS,
    RETIRED_TOOL_REGISTRY,
    TOOL_REGISTRY,
    WRITE_RISK_TIERS,
    ToolDefinition,
    _ro,
    _rw,
    annotations_for_risk,
    get_write_tool_names,
)

#: The three tools the ruling is about. They refresh stored state, so they are
#: not pure reads; they keep a read permission, so they are not writes.
MAINTENANCE_TOOLS = (
    "tortoise_get_confidence",
    "tortoise_compute_confidence",
    "tortoise_packs_list",
)


def served() -> list[ToolDefinition]:
    """Every entry the server can resolve — live plus retired (#3883)."""
    return [*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY]


def read_hint_violations(entries) -> list[str]:
    """`readOnlyHint` must be True exactly for `risk == "read"`.

    `maintenance` is the state that used to be unrepresentable: a tool that
    refreshes stored state (`readOnlyHint: false`, correctly) yet must remain
    callable by a `graphs:read` key. Anything else that claims to be read-only
    is the #4122 defect; anything that writes and claims to be read-only is the
    #4474/#4170 defect.
    """
    out: list[str] = []
    for e in entries:
        if e.annotations is None:
            out.append(f"{e.name}: no annotations at all")
            continue
        if (e.annotations.readOnlyHint is True) != (e.risk == "read"):
            out.append(
                f"{e.name}: readOnlyHint={e.annotations.readOnlyHint} disagrees "
                f"with risk={e.risk!r} (#4122)")
    return out


def writes_violations(entries) -> list[str]:
    """`writes` (i.e. "needs graphs:write") must be True exactly for the writing
    tiers — the permission and the tier are one statement."""
    out: list[str] = []
    for e in entries:
        if bool(e.writes) != (e.risk in WRITE_RISK_TIERS):
            out.append(
                f"{e.name}: writes={e.writes} disagrees with risk={e.risk!r} "
                f"(#4122: writing tiers are {sorted(WRITE_RISK_TIERS)})")
    return out


class TestTheRiskAxis:
    def test_every_served_entry_declares_a_known_tier(self):
        entries = served()
        assert entries, "served registry is empty — the checks would be vacuous"
        unknown = {e.name: e.risk for e in entries if e.risk not in RISK_TIERS}
        assert unknown == {}, f"entries with an unknown risk tier: {unknown}"

    def test_the_tier_census_is_the_rulings(self):
        """The distribution is pinned, not just the total.

        A count is the only thing that notices a whole tier silently emptying
        (e.g. every `destructive` tool drifting back to `write` because someone
        found the hint inconvenient).
        """
        census: dict[str, int] = {tier: 0 for tier in RISK_TIERS}
        for e in served():
            census[e.risk] += 1
        assert census == {
            "read": 48,
            "maintenance": 3,
            "write": 32,
            "destructive": 12,
            "admin": 4,
        }, census
        assert sum(census.values()) == len(served())

    def test_the_maintenance_tier_is_exactly_the_ruling(self):
        """Pinned by name: permissioning a tool as read-while-it-writes is a
        deliberate act, never something a new entry may do by accident."""
        maintenance = {e.name for e in served() if e.risk == MAINTENANCE_TIER}
        assert maintenance == set(MAINTENANCE_TOOLS), sorted(maintenance)

    def test_a_typo_in_a_tier_fails_at_construction(self):
        """`risk="read-only"` must NOT fall back to a read — an under-classified
        writer served as a read is the #4170 failure mode."""
        with pytest.raises(ValueError, match="unknown risk"):
            ToolDefinition(name="tortoise_probe_typo", id="surface.probe.typo",
                           description="probe", risk="read-only",
                           http_policy=False, sdk_method="")


class TestHintsCannotDrift:
    def test_read_only_hint_follows_the_tier(self):
        assert read_hint_violations(served()) == []
        # … and the property can still fire: a read-tier entry carrying a writer
        # annotation is the #4474 defect.
        target = next(e for e in served() if e.risk == "read")
        rogue = dataclasses.replace(target, annotations=_rw())
        assert read_hint_violations([rogue]), "drift guard is vacuous"

    def test_writes_permission_follows_the_tier(self):
        assert writes_violations(served()) == []
        # `writes` is a DERIVED property, so it cannot disagree on a real entry;
        # the arm is proven against a duck-typed one, which is also the tripwire
        # if `writes` ever goes back to being a hand-set field.
        import types
        forged = types.SimpleNamespace(
            name="tortoise_probe_forged", risk=MAINTENANCE_TIER, writes=True,
            annotations=_rw())
        assert writes_violations([forged]), "drift guard is vacuous"

    def test_every_hint_is_reproduced_by_the_derivation(self):
        """No entry contributes a hand-made annotation: re-deriving from the
        entry's own tier must reproduce exactly what clients are served."""
        for e in served():
            derived = annotations_for_risk(e.risk, e.idempotent)
            assert e.annotations == derived, (
                f"{e.name}: served hints {e.annotations} are not the derivation "
                f"{derived} for risk={e.risk!r}")

    def test_the_three_tier_states_map_to_three_hint_states(self):
        """The table itself, so a change to it is a visible edit here too."""
        read = annotations_for_risk("read")
        maint = annotations_for_risk(MAINTENANCE_TIER)
        write = annotations_for_risk("write")
        assert (read.readOnlyHint, read.destructiveHint, read.idempotentHint) \
            == (True, False, False)
        assert (maint.readOnlyHint, maint.destructiveHint, maint.idempotentHint) \
            == (False, False, True)
        assert (write.readOnlyHint, write.destructiveHint, write.idempotentHint) \
            == (False, False, False)
        # A genuinely idempotent write keeps the retry-safety hint …
        assert annotations_for_risk("write", True).idempotentHint is True
        # … and the destructive/admin tiers are the ones that may overwrite.
        for tier in ("destructive", "admin"):
            assert annotations_for_risk(tier).destructiveHint is True
            assert annotations_for_risk(tier).readOnlyHint is False

    def test_write_names_are_exactly_the_writing_tiers(self):
        """`WRITE_TOOL_NAMES` (the permission set AND the read-velocity
        classification set) is the tier derivation, over the served set."""
        expected = frozenset(e.name for e in served() if e.risk in WRITE_RISK_TIERS)
        assert get_write_tool_names() == expected
        assert len(expected) == 48, sorted(expected)  # census, hard-coded


class TestTheReadThroughToolsRegressionPin:
    """The bug itself: a `graphs:read` key could call a tool whose handler writes,
    *and* the tool advertised `readOnlyHint: true` (which clients auto-approve
    on). The permission stays; the false hint goes."""

    def test_they_keep_read_scope(self):
        from tortoise.mcp_auth import _current_legacy_full_access, _current_scopes
        from tortoise.mcp_server import _enforce_mcp_tool_scope

        scopes_tok = _current_scopes.set(["graphs:read"])
        legacy_tok = _current_legacy_full_access.set(False)
        try:
            for name in MAINTENANCE_TOOLS:
                _enforce_mcp_tool_scope(name)  # must NOT raise
        finally:
            _current_scopes.reset(scopes_tok)
            _current_legacy_full_access.reset(legacy_tok)

    def test_they_do_not_claim_to_be_read_only(self):
        entries = {e.name: e for e in served()}
        for name in MAINTENANCE_TOOLS:
            entry = entries[name]
            assert entry.risk == MAINTENANCE_TIER, name
            assert entry.annotations.readOnlyHint is False, (
                f"{name}: readOnlyHint must be false — clients auto-approve on it "
                f"and this tool refreshes stored state (#4122)")
            # and it is NOT a write, so it never needs graphs:write
            assert entry.writes is False, name
            assert entry.annotations.idempotentHint is True, name

    def test_the_hint_the_client_sees_is_the_false_one(self):
        """The regression is about the SERVED hint, not the registry field — read
        it back off the registered tool, as a client would."""
        import asyncio

        from tortoise.mcp_server import mcp

        async def _tools():
            return {t.name: t for t in await mcp._list_tools()}

        tools = asyncio.run(_tools())
        for name in MAINTENANCE_TOOLS:
            hint = getattr(tools[name].annotations, "readOnlyHint",
                           getattr(tools[name].annotations, "read_only_hint", None))
            assert hint is not True, f"{name}: served readOnlyHint is {hint!r}"


class TestPureReadsStillOnlyNeedReadScope:
    def test_every_read_tier_tool_is_admitted_with_graphs_read(self):
        from tortoise.mcp_auth import _current_legacy_full_access, _current_scopes
        from tortoise.mcp_server import _enforce_mcp_tool_scope

        reads = [e.name for e in served() if e.risk == "read"]
        assert len(reads) == 48, sorted(reads)
        scopes_tok = _current_scopes.set(["graphs:read"])
        legacy_tok = _current_legacy_full_access.set(False)
        try:
            for name in reads:
                _enforce_mcp_tool_scope(name)  # must NOT raise
        finally:
            _current_scopes.reset(scopes_tok)
            _current_legacy_full_access.reset(legacy_tok)

    def test_a_read_tier_entry_carries_the_read_hint(self):
        read = next(e for e in served() if e.risk == "read")
        assert read.annotations.readOnlyHint is True
        assert read.writes is False
        # sanity: the two shorthands still describe the two pre-existing states
        assert _ro().readOnlyHint is True and _rw().readOnlyHint is False
