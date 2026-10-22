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
from pathlib import Path

import pytest

from tortoise.tool_registry import (
    MAINTENANCE_TIER,
    RETIRED_TOOL_REGISTRY,
    RISK_TIERS,
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

    def test_every_tier_is_enumerated(self):
        """Every tier has an enumerated census — no tier can quietly widen.

        `write`, `destructive`, `admin` and `maintenance` are pinned BY NAME (the
        three tests above/below); the reading tiers are pinned by COUNT here, so a
        new `read` tool is a deliberate act rather than a line that just appears.
        Written as explicit numbers, not as a re-derived `len(x) == len(x)`: the
        only thing this test may assert is a figure no code path can recompute.
        """
        reading = {e.name for e in served() if e.risk in ("read", MAINTENANCE_TIER)}
        assert len(reading) == 51, sorted(reading)   # 48 read + 3 maintenance
        assert len(served()) == 99, sorted(e.name for e in served())

    def test_the_destructive_set_is_pinned_by_name(self):
        """`destructive` means "removes or overwrites user-authored state".

        Pinned by name because the count alone let a review finding through: five
        rows that overwrite (supersede moves every edge off the old point,
        invalidate/promote_point write a terminal status, dream rewrites stored
        confidence graph-wide, backfill_v25 rewrites every node's schema) were
        first tiered `write`, which silently removed the client's
        `destructiveHint` confirmation for them.

        The identity-upsert family joined them for the same reason one step
        further out (#9572): `create_subject` / `create_object` always send a
        `subjectKind` / `objectKind` — `"other"` when the caller named no kind —
        and `create_entity` supplies none, which the fold reads as `"other"` too,
        so a bare re-create rewrites a user-authored discriminator. `main` served
        `destructiveHint: true` on all three, and a `write` tier would have taken
        that confirmation away on a call that clobbers.
        """
        # A tool belongs here whenever a REPEAT or a WRAPPER call reaches an
        # overwrite: `mitigate_operator` re-runs `update_point` +
        # `set_point_baseline` on its idempotent path, `index_sessions` is a thin
        # wrapper over the corpus re-index, and `mine_conversations` wraps the
        # same re-ingest before mining it.
        destructive = {e.name for e in served() if e.risk == "destructive"}
        assert destructive == {
            # the delete family — their own descriptions say "cannot be undone"
            "tortoise_delete", "tortoise_delete_point", "tortoise_delete_entity",
            # removal from default retrieval
            "tortoise_retract_point",
            # in-place overwrite of user-authored properties
            "tortoise_update", "tortoise_update_point", "tortoise_update_entity",
            "tortoise_annotate_operator", "tortoise_operator_action",
            "tortoise_set_point_baseline", "tortoise_assess_source",
            "tortoise_set_source_tier", "tortoise_mitigate_operator",
            # identity upsert that re-applies a LIBRARY DEFAULT over a
            # user-authored discriminator (#9572)
            "tortoise_create_subject", "tortoise_create_object",
            "tortoise_create_entity",
            # terminal status writes / replacement
            "tortoise_supersede", "tortoise_invalidate", "tortoise_promote_point",
            # whole-graph rewrites
            "tortoise_dream", "tortoise_backfill_v25",
            # bulk re-ingest that rewrites an existing corpus' stored rows
            "tortoise_index_files", "tortoise_ingest_corpus",
            "tortoise_index_sessions", "tortoise_mine_conversations",
        }, sorted(destructive)

    def test_the_admin_set_is_pinned_by_name(self):
        """`admin` = changes what the product DOES for the org/graph/install."""
        admin = {e.name for e in served() if e.risk == "admin"}
        assert admin == {
            "tortoise_org_create",            # org provisioning
            "tortoise_pack_install",          # installs code
            "tortoise_onboarding_session_recording",  # org-wide recording
            "tortoise_onboarding_github_connect",     # credential handshake
            "tortoise_graph_set_recording",   # per-graph recording override
            "tortoise_manage_sources",        # enable/disable sources
        }, sorted(admin)

    def test_the_write_set_is_additive_only(self):
        """The `write` tier is pinned by name too — it must contain nothing that
        REMOVES a row or rewrites a row the call did not name, because that is the
        distinction from `destructive` and what makes `destructiveHint: false` an
        honest claim for these rows.

        It may, however, apply the caller's own property to the row the call
        identifies (`create_source` sets `credibilityTier`). "Additive" must not
        be read as "cannot change anything" — but a call that stores a value the
        caller never supplied is NOT this tier, which is why the identity-upsert
        family (`create_subject` / `create_object` / `create_entity`, #9572) sits
        in `destructive` — see the tier axis in `tortoise/tool_registry.py`.

        `tortoise_get_source_reliability` is here deliberately: it ADDS a cache
        row but is a declared writer on main, so a `graphs:read` key cannot call
        it today — demoting it to `maintenance` would hand read keys a new tool,
        which is a surface change, not a classification.

        `tortoise_onboarding_github_index` is here deliberately too: it starts a
        background index of external GitHub content (new rows, no user-authored
        row rewritten), so it is additive rather than a corpus re-ingest.
        """
        writes = {e.name for e in served() if e.risk == "write"}
        assert writes == {
            "tortoise_approve_merge", "tortoise_checkpoint",
            "tortoise_create_document", "tortoise_create_edge",
            "tortoise_create_event", "tortoise_create_operator",
            "tortoise_create_point", "tortoise_create_source",
            "tortoise_diary_write",
            "tortoise_file_decision", "tortoise_file_human_approval",
            "tortoise_get_source_reliability",
            "tortoise_ingest",
            "tortoise_onboarding_demo_create",
            "tortoise_onboarding_github_index", "tortoise_onboarding_seed",
            "tortoise_session_capture",
        }, sorted(writes)

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

    def test_risk_is_required_and_the_removed_fields_are_not_passable(self):
        """The derivation is STRUCTURAL: `risk` has no default, and
        `annotations` / `writes` are properties, so neither the drift nor a
        fail-open omission can come back by edit.

        A default of `read` would fail OPEN — `readOnlyHint: true` (client
        auto-approve) plus `writes: false` (`graphs:read` reaches it) — so
        omitting the axis must be as loud as omitting `annotations` was before
        #4122. Carried over from #9573's cycle-1 P1 pin when the two
        implementations of the axis were merged (it pinned the same three
        constructions against its `field(init=False)` shape).
        """
        base = dict(name="tortoise_probe_bare", id="surface.probe.bare",
                    description="probe", http_policy=True, sdk_method="query")
        with pytest.raises(TypeError):
            ToolDefinition(**base)  # no `risk` at all — must not default to read
        with pytest.raises(TypeError):
            ToolDefinition(**base, risk="read", writes=True)  # removed field
        with pytest.raises(TypeError):
            ToolDefinition(**base, risk="read", annotations=_ro())  # removed field


class TestHintsCannotDrift:
    def test_read_only_hint_follows_the_tier(self):
        assert read_hint_violations(served()) == []
        # … and the property can still fire: a read-tier entry carrying a writer
        # annotation is the #4474 defect.
        target = next(e for e in served() if e.risk == "read")
        rogue = dataclasses.replace(target, annotations_override=_rw())
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

    def test_replace_re_derives_the_hints(self):
        """`dataclasses.replace(risk=...)` must not keep the OLD tier's hints.

        This is the drift the tier model exists to close and the only path to it
        left in the dataclass's own API: a stored `annotations` field would be
        copied verbatim by `replace`, so a hosted/deployment variant built that
        way would serve `readOnlyHint: true` on a write tool. `annotations` is a
        property for exactly this reason.
        """
        target = next(e for e in served() if e.risk == "read")
        for tier in RISK_TIERS:
            moved = dataclasses.replace(target, risk=tier)
            assert moved.annotations == annotations_for_risk(tier, moved.idempotent), (
                f"replace(risk={tier!r}) kept stale hints {moved.annotations}")
            assert moved.writes == (tier in WRITE_RISK_TIERS)

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
        # … and a `maintenance` row may still declare itself non-idempotent, which
        # is the only way `_RISK_HINTS` can be the single statement: the measured
        # case is `tortoise_compute_confidence` (call 2 starts from call 1's write).
        assert annotations_for_risk(MAINTENANCE_TIER, False).idempotentHint is False
        # A genuinely idempotent write keeps the retry-safety hint …
        assert annotations_for_risk("write", True).idempotentHint is True
        # … and the destructive/admin tiers are the ones that may overwrite.
        for tier in ("destructive", "admin"):
            assert annotations_for_risk(tier).destructiveHint is True
            assert annotations_for_risk(tier).readOnlyHint is False

    def test_no_registry_entry_overrides_its_derived_hints(self):
        """`annotations_override` exists ONLY so a probe can construct a
        disagreeing entry and prove the drift guards fire.

        Pinned because an override that AGREES with its tier is invisible to
        `test_every_hint_is_reproduced_by_the_derivation`, yet survives
        `dataclasses.replace(risk=...)` — bringing the stale-hint drift back
        through the one door the property design left open.
        """
        overridden = [e.name for e in served() if e.annotations_override is not None]
        assert overridden == [], overridden

    def test_the_idempotent_overrides_are_pinned_by_name(self):
        """`idempotent` is the ONE element a tier does not decide, so which
        entries override it must be a deliberate, visible act — otherwise a
        `destructive` tool could claim retry-safety by adding a keyword."""
        overrides = {(e.name, e.idempotent) for e in served()
                     if e.idempotent is not None}
        assert overrides == {
            # write tier, idempotent by operation (dedup on content/identity)
            ("tortoise_approve_merge", True),
            ("tortoise_checkpoint", True),
            ("tortoise_create_document", True),
            ("tortoise_create_operator", True),
            ("tortoise_create_point", True),
            ("tortoise_create_source", True),
            ("tortoise_ingest", True),
            ("tortoise_onboarding_demo_create", True),
            # destructive tier, idempotent by operation (same id → same row)
            ("tortoise_mitigate_operator", True),
            # maintenance tier, NOT idempotent: a repeat call re-stores a
            # re-derived score (#4122 measurement: 0.693315 -> 0.693320)
            ("tortoise_compute_confidence", False),
            ("tortoise_get_confidence", False),
        }, sorted(overrides)

    def test_write_names_are_exactly_the_writing_tiers(self):
        """`WRITE_TOOL_NAMES` (the permission set AND the read-velocity
        classification set) is the tier derivation, over the served set."""
        from tortoise.mcp_server import WRITE_TOOL_NAMES

        expected = frozenset(e.name for e in served() if e.risk in WRITE_RISK_TIERS)
        # NOTE: `WRITE_TOOL_NAMES` IS `get_write_tool_names()` and `expected` is the
        # same comprehension, so this chain is one assertion said three ways — it
        # catches a broken derivation, never an independent disagreement. The
        # INDEPENDENT pins are the literal count below, the gate's own refusals
        # (`tests/test_tool_registry.py::test_gate_refuses_read_scope_…`, which
        # calls `_enforce_mcp_tool_scope` per name) and the served-vs-registry
        # comparison in `TestTheReadThroughToolsRegressionPin`.
        assert get_write_tool_names() == expected == WRITE_TOOL_NAMES
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
        # idempotent by default — except the two that re-store a re-derived score on
        # a repeat call (both take the same lazy `dream(dirty_only=True)` path).
        for name in ("tortoise_get_confidence", "tortoise_compute_confidence"):
            assert entries[name].annotations.idempotentHint is False, name
        assert entries["tortoise_packs_list"].annotations.idempotentHint is True

    def test_the_hint_the_client_sees_is_the_false_one(self):
        """The regression is about the SERVED hint, not the registry value — read
        it back off the registered tool, as a client would — and check ALL THREE
        hints for EVERY served tool, not just the trio's `readOnlyHint`."""
        import asyncio

        from tortoise.mcp_server import mcp

        async def _tools():
            return {t.name: t for t in await mcp._list_tools()}

        tools = asyncio.run(_tools())
        assert tools, "served tool list is empty — the check would be vacuous"
        for name in MAINTENANCE_TOOLS:
            ann = tools[name].annotations
            assert ann is not None, f"{name}: no annotations on the served tool"
            assert ann.readOnlyHint is False, (
                f"{name}: served readOnlyHint is {ann.readOnlyHint!r} — clients "
                f"auto-approve on it and this tool refreshes stored state (#4122)")
        # The served surface must equal the derivation for every advertised tool.
        by_name = {e.name: e for e in served()}
        mismatches = []
        for name, tool in tools.items():
            entry = by_name.get(name)
            if entry is None:
                mismatches.append(f"{name}: served but absent from the registry")
                continue
            ann = tool.annotations
            for attr, expected in (
                ("readOnlyHint", entry.annotations.readOnlyHint),
                ("destructiveHint", entry.annotations.destructiveHint),
                ("idempotentHint", entry.annotations.idempotentHint),
            ):
                served_value = getattr(ann, attr, None)
                if served_value is not expected:
                    mismatches.append(
                        f"{name}: served {attr}={served_value!r}, tier says {expected!r}")
        assert mismatches == [], mismatches


#: #9572 — the family whose bare repeat call RE-APPLIES a library default over a
#: user-authored property: `create_subject` / `create_object` always send a
#: `subjectKind` / `objectKind` (`"other"` when the caller named none),
#: `create_entity` sends none and the fold's `ev.get(..., "other")` default takes
#: over, and `mine_conversations` wraps the corpus re-ingest (`ingest_corpus`,
#: which is `destructive` for exactly that reason).
#:
#: All four served `destructiveHint: true` on `main` (`_rw()`), so tiering them
#: `write` silently removed the client's confirmation at the moment the call got
#: MORE dangerous (#4122 review, MEDIUM). They keep `graphs:write` either way — the
#: tier only decides the hint — so this is not a permission change.
IDENTITY_UPSERT_OVERWRITERS = (
    "tortoise_create_subject",
    "tortoise_create_object",
    "tortoise_create_entity",
    "tortoise_mine_conversations",
)


class TestTheIdentityUpsertFamilyKeepsItsConfirmation:
    """A repeat call applies a DEFAULT the caller never supplied, so these may not
    claim to be additive (#9572 is the write, this is the HINT).

    Both halves fail on the pre-fix code: there the four are `risk="write"`, so
    `destructiveHint` is `False`.
    """

    def test_the_tier_is_destructive_and_the_permission_is_untouched(self):
        entries = {e.name: e for e in served()}
        for name in IDENTITY_UPSERT_OVERWRITERS:
            entry = entries[name]
            assert entry.risk == "destructive", (
                f"{name}: re-applying a library default over a user-authored "
                f"property is `destructive`, not additive (#9572)")
            assert entry.annotations.destructiveHint is True, (
                f"{name}: destructiveHint must stay true — `main` served it true "
                f"and a bare re-create can clobber a stored discriminator")
            assert entry.annotations.readOnlyHint is False, name
            # write and destructive BOTH derive `writes`, so nothing about key
            # scope moves: a `graphs:read` key was refused before and is refused now.
            assert entry.writes is True, name

    def test_the_served_hint_is_the_destructive_one(self):
        """Read it back off the registered tool, as a client would — the registry
        value agreeing with itself is not the property that matters."""
        import asyncio

        from tortoise.mcp_server import mcp

        async def _tools():
            return {t.name: t for t in await mcp._list_tools()}

        tools = asyncio.run(_tools())
        assert tools, "served tool list is empty — the check would be vacuous"
        for name in IDENTITY_UPSERT_OVERWRITERS:
            ann = tools[name].annotations
            assert ann is not None, f"{name}: no annotations on the served tool"
            assert ann.destructiveHint is True, (
                f"{name}: served destructiveHint is {ann.destructiveHint!r} — a "
                f"client gates its confirmation on this and a bare re-create "
                f"overwrites a user-authored discriminator (#9572)")
            assert ann.readOnlyHint is False, name


class TestReadTierCannotHideAnOutOfBoundaryWrite:
    """`events_poll` purges expired `:GraphEvent` rows from `event_store.py`, i.e.
    outside `sdk.py`, so `sdk_graph_mutators()` cannot see it. The carve-out is
    declared (`RETENTION_CARVEOUTS`) and this arm is what makes the declaration
    load-bearing: an undeclared read/maintenance tool that reaches one of those
    operations fails."""

    def test_the_declared_carveout_is_the_only_one(self):
        import tool_surface_capabilities as tsc

        declared = set(tsc.RETENTION_CARVEOUTS)
        assert declared == {"tortoise_events_poll"}, sorted(declared)
        assert tsc.write_classification_violations(tsc.served_registry()) == []

    def test_an_undeclared_read_tool_reaching_one_fails(self, monkeypatch):
        import tool_surface_capabilities as tsc

        # Pretend `query` were an out-of-boundary mutator: the read tools that
        # reach it are not declared, so the arm must fire for them.
        monkeypatch.setitem(
            tsc.OUT_OF_BOUNDARY_MUTATORS, "query",
            "probe: pretend a mutation lives outside sdk.py")
        violations = tsc.write_classification_violations(tsc.served_registry())
        assert any("out-of-boundary mutation" in v and "tortoise_query" in v
                   for v in violations), violations
        # …and a `maintenance` or writing tool is not what this arm is for.
        assert not any("out-of-boundary mutation" in v
                       and "tortoise_create_point" in v for v in violations)


class TestPureReadsStillOnlyNeedReadScope:
    def test_every_read_tier_tool_is_admitted_with_graphs_read(self):
        from tortoise.mcp_auth import _current_legacy_full_access, _current_scopes
        from tortoise.mcp_server import _enforce_mcp_tool_scope

        reads = [e.name for e in served() if e.risk == "read"]
        assert reads, "no read-tier tool — the check would be vacuous"
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


class TestThisGuardSuiteIsActuallySelected:
    """A drift guard that no CI leg SELECTS protects nothing — and it is worse
    than absent, because this module's docstring and the `OVERRIDES:` note in
    `docs/product/canonical-mcp-tools.md` cite it as *the* enforcement of the tier
    axis (#4122 review).

    `tools/ci_selection.py::integrity()` — the required `manifest-integrity` job —
    rglobs `tests/**/test_*.py` and reds on every file `classify_test_file()`
    cannot place in a surface; `select()` then builds EVERY leg FROM the manifest,
    so the same omission also means the file runs nowhere. This pins the three
    rows that make it both classified and selected, here in the file they
    protect, so a later manifest edit that drops one reds at the guard it would
    have disarmed — not only in a gate a lane can mistake for bookkeeping.
    """

    MANIFEST = Path(__file__).resolve().parents[1] / "config" / "ci-surfaces.yml"
    THIS = "test_tool_risk_tiers.py"

    def _manifest(self) -> dict:
        import yaml

        manifest = yaml.safe_load(self.MANIFEST.read_text())
        assert isinstance(manifest, dict), f"{self.MANIFEST} did not parse to a mapping"
        return manifest

    def test_a_surface_classifies_it(self):
        from tools.ci_selection import classify_test_file

        surface = classify_test_file(self.THIS, self._manifest())
        assert surface is not None, (
            f"{self.THIS} is registered in NO CI surface — `--integrity` reds the "
            f"required `manifest-integrity` job and no leg ever selects this file"
        )
        # `tortoise/tool_registry.py` is a SHARED_MODULE that expands to the full
        # matrix, so any surface runs these pins on the diff that matters; `core`
        # is where the sibling `test_tool_registry.py` lives, and `core` is the
        # selector's own fallback for an unmapped tests/ path.
        assert surface == "core"

    def test_tier1_also_carries_it_so_a_docs_only_pr_grades_it(self):
        # `docs/product/bridge-table.md` and `mcp-rename-table.md` are the visible
        # face of these pins (both regenerated by their own generators), so the
        # PR shape most likely to leave them stale must still run this file.
        assert self.THIS in set(self._manifest().get("tier1") or ()), (
            "without the `tier1` row a docs-only PR never grades the tier/hint "
            "pins, and the regenerated tables are exactly what such a PR edits"
        )

    def test_it_carries_a_durations_weight(self):
        durations = self._manifest().get("durations") or {}
        assert self.THIS in durations, (
            "an absent weight packs at the flat DEFAULT_FAST_WEIGHT, so the push "
            "split is duration-blind for this file (#3400)"
        )
        assert isinstance(durations[self.THIS], (int, float))
